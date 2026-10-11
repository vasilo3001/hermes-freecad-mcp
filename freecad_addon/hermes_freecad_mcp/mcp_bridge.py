"""
Hermes FreeCAD MCP — Bridge Server
===================================
mcp_bridge.py — TCP server that runs inside FreeCAD's Python interpreter.

Receives JSON commands from the MCP server (via localhost TCP) and
executes them in the FreeCAD context. All geometry operations happen
here, so the user sees everything live in the FreeCAD 3D viewport.

Thread safety: FreeCAD's Python API is NOT thread-safe. All FreeCAD
calls are marshalled to the main (GUI) thread via a queue.Queue + QTimer
polling pattern (inspired by neka-nat/freecad-mcp and
spkane/freecad-addon-robust-mcp-server).

Protocol: newline-delimited JSON over TCP.
  Request:  {"id": 1, "tool": "create_box", "args": {...}}\n
  Response: {"id": 1, "result": {...}}\n
"""

import json
import os
import queue
import socket
import threading
import traceback
import time
import itertools
import atexit
import contextlib

import FreeCAD
import Part
from PySide import QtCore

MCP_BRIDGE_PORT = 9876
HOST = "0.0.0.0"  # Listen on all interfaces (allows remote MCP server)
QUEUE_POLL_INTERVAL_MS = 50  # How often the QTimer polls the queue
_req_counter = itertools.count(1)

# Global registry of active servers for cleanup on Python exit
_active_servers = []
_atexit_registered = False


def _cleanup_all_servers():
    """Clean up all active MCP servers before Python exits.

    This is registered with atexit to ensure QTimer objects are properly
    destroyed BEFORE Python's garbage collector runs during Py_FinalizeEx.
    """
    for server in list(_active_servers):
        with contextlib.suppress(Exception):
            server._cleanup_for_exit()


class ExecutionRequest:
    """Represents a code execution request."""

    def __init__(self, func, args, kwargs, req_id, timeout=60.0):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.req_id = req_id
        self.timeout = timeout
        self.result = None
        self.completed = threading.Event()


class MCPBridgeServer:
    """TCP server that accepts commands from the MCP bridge.

    Uses a queue.Queue + QTimer pattern for thread-safe execution of
    FreeCAD API calls on the main GUI thread.
    """

    def __init__(self, port=MCP_BRIDGE_PORT):
        self.port = port
        self.host = HOST
        self.server_socket = None
        self.running = False
        self.thread = None
        self.connection_count = 0
        self.last_command_time = None
        self.command_log = []

        # Queue-based execution for thread safety
        self._request_queue = queue.Queue()
        self._timer = None
        self._headless = False

        # Register for cleanup on Python exit
        _active_servers.append(self)
        global _atexit_registered
        if not _atexit_registered:
            atexit.register(_cleanup_all_servers)
            _atexit_registered = True

    @property
    def is_running(self):
        return self.running

    def start(self):
        """Start the TCP server and queue processor."""
        if self.running:
            return

        self.running = True

        # Start queue processor (QTimer in GUI mode, thread in headless)
        self._start_queue_processor()

        # Start TCP server
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)

        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

        FreeCAD.Console.PrintMessage(
            "[HermesFreeCADMCP] Bridge started on port %d\n" % self.port
        )

    def stop(self):
        """Stop the TCP server."""
        self.running = False

        # Stop queue processor timer
        if self._timer:
            with contextlib.suppress(Exception):
                self._timer.stop()
                self._timer.timeout.disconnect()
                self._timer.deleteLater()
            self._timer = None

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        FreeCAD.Console.PrintMessage("[HermesFreeCADMCP] Bridge stopped\n")

    def _cleanup_for_exit(self):
        """Clean up server resources during Python exit (atexit handler).

        Uses shiboken.delete() for immediate destruction of QTimer objects
        to prevent GC crashes during Py_FinalizeEx.
        """
        self.running = False

        # Get shiboken delete function
        shiboken_delete = None
        with contextlib.suppress(ImportError):
            import shiboken6
            shiboken_delete = shiboken6.delete
        if shiboken_delete is None:
            with contextlib.suppress(ImportError):
                import shiboken2
                shiboken_delete = shiboken2.delete

        # Stop queue processor timer
        if self._timer:
            timer = self._timer
            self._timer = None
            with contextlib.suppress(Exception):
                timer.stop()
            with contextlib.suppress(Exception):
                timer.timeout.disconnect()
            if shiboken_delete is not None:
                with contextlib.suppress(Exception):
                    shiboken_delete(timer)

        # Close socket
        if self.server_socket:
            with contextlib.suppress(Exception):
                self.server_socket.close()
            self.server_socket = None

    def get_status(self):
        """Return a status string."""
        if not self.running:
            return "STOPPED"
        return (
            "RUNNING on port %d | connections: %d | "
            "last command: %s"
            % (
                self.port,
                self.connection_count,
                time.ctime(self.last_command_time) if self.last_command_time else "never",
            )
        )

    # ──────────────────────────────────────────────
    # Queue-based Thread Safety
    # ──────────────────────────────────────────────

    def _start_queue_processor(self):
        """Start the queue processor on the main GUI thread.

        In GUI mode, creates a QTimer that fires every QUEUE_POLL_INTERVAL_MS
        to process the request queue on the main thread.
        In headless mode, a daemon background thread polls the queue instead.
        """
        if FreeCAD.GuiUp:
            try:
                timer = QtCore.QTimer()
                timer.timeout.connect(self._process_queue)
                timer.start(QUEUE_POLL_INTERVAL_MS)
                self._timer = timer
                return
            except Exception:
                pass

        # Headless mode: use a background thread
        self._headless = True

        def _queue_loop():
            while self.running:
                self._process_queue()
                time.sleep(QUEUE_POLL_INTERVAL_MS / 1000.0)

        self._queue_thread = threading.Thread(
            target=_queue_loop, daemon=True, name="MCP-QueueProcessor"
        )
        self._queue_thread.start()

    def _process_queue(self):
        """Process pending execution requests on the main thread."""
        while not self._request_queue.empty():
            try:
                request = self._request_queue.get_nowait()
                result = self._execute_sync(request)
                request.result = result
                request.completed.set()
            except queue.Empty:
                break
            except Exception as e:
                FreeCAD.Console.PrintError(
                    "[HermesFreeCADMCP] Queue error: %s\n" % str(e)
                )

    def _execute_sync(self, request):
        """Execute a function call synchronously (main thread only)."""
        try:
            result = request.func(*request.args, **request.kwargs)
            return {"result": result}
        except Exception as e:
            return {
                "error": "%s: %s" % (type(e).__name__, str(e)),
                "traceback": traceback.format_exc(),
            }

    def _execute_on_main(self, func, args, kwargs, timeout=60.0):
        """Execute a function on the main thread via the queue system.

        Args:
            func: Callable to execute on the main thread.
            args: Positional arguments.
            kwargs: Keyword arguments.
            timeout: Execution timeout in seconds.

        Returns:
            The result of the function call.

        Raises:
            TimeoutError: If execution exceeds the timeout.
            RuntimeError: If the function raised an exception.
        """
        req_id = next(_req_counter)
        request = ExecutionRequest(func, args, kwargs, req_id, timeout)
        self._request_queue.put(request)

        if request.completed.wait(timeout=timeout):
            if request.result is None:
                raise RuntimeError("No result returned")
            if "error" in request.result:
                raise RuntimeError(request.result["error"])
            return request.result.get("result", request.result)
        else:
            raise TimeoutError(
                "FreeCAD command timed out after %.0fs" % timeout
            )

    # ──────────────────────────────────────────────
    # TCP Server
    # ──────────────────────────────────────────────

    def _accept_loop(self):
        """Accept incoming connections and spawn handler threads."""
        while self.running:
            try:
                client, addr = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            self.connection_count += 1
            conn_id = self.connection_count
            FreeCAD.Console.PrintMessage(
                "[HermesFreeCADMCP] Connection %d from %s:%d\n"
                % (conn_id, addr[0], addr[1])
            )

            handler = threading.Thread(
                target=self._handle_client,
                args=(client, conn_id),
                daemon=True,
            )
            handler.start()

    def _handle_client(self, client, conn_id):
        """Handle a single client connection (newline-delimited JSON)."""
        buffer = b""
        try:
            while self.running:
                try:
                    data = client.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not data:
                    break

                buffer += data

                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        request = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        response = {
                            "id": None,
                            "error": "Invalid JSON: %s" % str(e),
                        }
                        self._send_response(client, response)
                        continue

                    req_id = request.get("id")
                    tool = request.get("tool", "")
                    args = request.get("args", {})

                    self.last_command_time = time.time()
                    self.command_log.append({
                        "time": time.ctime(),
                        "tool": tool,
                        "args": args,
                    })
                    if len(self.command_log) > 50:
                        self.command_log.pop(0)

                    FreeCAD.Console.PrintMessage(
                        "[HermesFreeCADMCP] Executing: %s(%s)\n"
                        % (tool, json.dumps(args)[:200])
                    )

                    # Dispatch via main thread
                    try:
                        result = self._dispatch_main(tool, args)
                        response = {"id": req_id, "result": result}
                    except Exception as e:
                        response = {
                            "id": req_id,
                            "error": "%s: %s" % (type(e).__name__, str(e)),
                            "traceback": traceback.format_exc(),
                        }

                    self._send_response(client, response)

        finally:
            client.close()
            FreeCAD.Console.PrintMessage(
                "[HermesFreeCADMCP] Connection %d closed\n" % conn_id
            )

    def _send_response(self, client, response):
        """Send a JSON response to the client."""
        data = json.dumps(response) + "\n"
        client.sendall(data.encode("utf-8"))

    def _dispatch_main(self, tool, args):
        """Route a tool call, executing on the main thread."""
        handlers = {
            "get_document_info": self._get_document_info,
            "new_document": self._new_document,
            "save_document": self._save_document,
            "open_document": self._open_document,
            "list_objects": self._list_objects,
            "get_object_info": self._get_object_info,
            "delete_object": self._delete_object,
            "undo": self._undo,
            "redo": self._redo,
            "create_box": self._create_box,
            "create_cylinder": self._create_cylinder,
            "create_sphere": self._create_sphere,
            "create_cone": self._create_cone,
            "create_torus": self._create_torus,
            "create_wedge": self._create_wedge,
            "create_ellipse": self._create_ellipse,
            "fuse_objects": self._fuse_objects,
            "cut_objects": self._cut_objects,
            "common_objects": self._common_objects,
            "move_object": self._move_object,
            "rotate_object": self._rotate_object,
            "scale_object": self._scale_object,
            "mirror_object": self._mirror_object,
            "copy_object": self._copy_object,
            "fillet_object": self._fillet_object,
            "chamfer_object": self._chamfer_object,
            "create_sketch": self._create_sketch,
            "add_sketch_line": self._add_sketch_line,
            "add_sketch_circle": self._add_sketch_circle,
            "add_sketch_arc": self._add_sketch_arc,
            "add_constraint": self._add_constraint,
            "get_sketch_info": self._get_sketch_info,
            "solve_sketch": self._solve_sketch,
            "pad_sketch": self._pad_sketch,
            "extrude_sketch": self._extrude_sketch,
            # PartDesign tools
            "create_body": self._create_body,
            "pocket_sketch": self._pocket_sketch,
            "revolution_sketch": self._revolution_sketch,
            "groove_sketch": self._groove_sketch,
            "partdesign_fillet": self._partdesign_fillet,
            "partdesign_chamfer": self._partdesign_chamfer,
            # Export
            "export_stl": self._export_stl,
            "export_step": self._export_step,
            "export_3mf": self._export_3mf,
            "export_svg": self._export_svg,
            # View
            "fit_all": self._fit_all,
            "set_view": self._set_view,
            "screenshot": self._screenshot,
            # Misc
            "run_python": self._run_python,
            "get_selection": self._get_selection,
            "ping": self._ping,
        }

        if tool not in handlers:
            raise ValueError(
                "Unknown tool: '%s'. Available: %s"
                % (tool, ", ".join(sorted(handlers.keys())))
            )

        handler = handlers[tool]
        timeout = 120.0 if tool == "run_python" else 60.0

        # Execute on main thread via queue
        return self._execute_on_main(handler, (), args, timeout=timeout)

    # ──────────────────────────────────────────────
    # Handlers (all execute on main thread)
    # ──────────────────────────────────────────────

    def _get_document_info(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return {"error": "No active document"}
        return {
            "name": doc.Name,
            "objects": len(doc.Objects),
            "object_names": [o.Name for o in doc.Objects],
            "label": doc.Label,
        }

    def _new_document(self, name="Document"):
        doc = FreeCAD.newDocument(name)
        return {"name": doc.Name, "objects": 0}

    def _save_document(self, path=None):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return {"error": "No active document"}
        if path:
            doc.saveAs(path)
            return {"saved": path}
        doc.save()
        return {"saved": doc.FileName}

    def _open_document(self, path):
        doc = FreeCAD.openDocument(path)
        return {"name": doc.Name, "objects": len(doc.Objects)}

    def _list_objects(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return {"error": "No active document"}
        objects = []
        for o in doc.Objects:
            info = {
                "name": o.Name,
                "label": o.Label,
                "type": o.TypeId,
            }
            if hasattr(o, "Shape") and o.Shape:
                bbox = o.Shape.BoundBox
                info["bbox"] = {
                    "x": [bbox.XMin, bbox.XMax],
                    "y": [bbox.YMin, bbox.YMax],
                    "z": [bbox.ZMin, bbox.ZMax],
                }
                info["volume"] = o.Shape.Volume
            objects.append(info)
        return {"objects": objects}

    def _get_object_info(self, name):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return {"error": "No active document"}
        obj = doc.getObject(name)
        if not obj:
            return {"error": "Object not found: %s" % name}
        info = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
        }
        if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
            shape = obj.Shape
            bbox = shape.BoundBox
            info["bbox"] = {
                "x": [bbox.XMin, bbox.XMax],
                "y": [bbox.YMin, bbox.YMax],
                "z": [bbox.ZMin, bbox.ZMax],
            }
            info["volume"] = shape.Volume
            info["area"] = shape.Area
            info["faces"] = len(shape.Faces)
            info["edges"] = len(shape.Edges)
            info["vertices"] = len(shape.Vertexes)
        return info

    def _delete_object(self, name):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return {"error": "No active document"}
        obj = doc.getObject(name)
        if not obj:
            return {"error": "Object not found: %s" % name}
        doc.removeObject(name)
        doc.recompute()
        return {"deleted": name}

    def _undo(self):
        import FreeCADGui as Gui
        Gui.SendMsgToActiveView("ViewFit")
        FreeCAD.ActiveDocument.undo()
        return {"undone": True}

    def _redo(self):
        FreeCAD.ActiveDocument.redo()
        return {"redone": True}

    def _create_box(self, length=10, width=10, height=10, x=0, y=0, z=0, name=None):
        doc = self._ensure_doc()
        box = Part.makeBox(length, width, height)
        box.translate(FreeCAD.Vector(x, y, z))
        obj = doc.addObject("Part::Feature", name or "Box")
        obj.Shape = box
        obj.Label = name or "Box %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _create_cylinder(self, radius=5, height=10, x=0, y=0, z=0, name=None):
        doc = self._ensure_doc()
        cyl = Part.makeCylinder(radius, height)
        cyl.translate(FreeCAD.Vector(x, y, z))
        obj = doc.addObject("Part::Feature", name or "Cylinder")
        obj.Shape = cyl
        obj.Label = name or "Cylinder %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _create_sphere(self, radius=5, x=0, y=0, z=0, name=None):
        doc = self._ensure_doc()
        sph = Part.makeSphere(radius)
        sph.translate(FreeCAD.Vector(x, y, z))
        obj = doc.addObject("Part::Feature", name or "Sphere")
        obj.Shape = sph
        obj.Label = name or "Sphere %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _create_cone(self, radius1=5, radius2=0, height=10, x=0, y=0, z=0, name=None):
        doc = self._ensure_doc()
        cone = Part.makeCone(radius1, radius2, height)
        cone.translate(FreeCAD.Vector(x, y, z))
        obj = doc.addObject("Part::Feature", name or "Cone")
        obj.Shape = cone
        obj.Label = name or "Cone %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _create_torus(self, radius1=10, radius2=3, x=0, y=0, z=0, name=None):
        doc = self._ensure_doc()
        torus = Part.makeTorus(radius1, radius2)
        torus.translate(FreeCAD.Vector(x, y, z))
        obj = doc.addObject("Part::Feature", name or "Torus")
        obj.Shape = torus
        obj.Label = name or "Torus %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _create_wedge(self, xmin=0, ymin=0, zmin=0, x2min=0, x2max=10,
                      xmax=10, ymax=10, zmax=10, name=None):
        doc = self._ensure_doc()
        wedge = Part.makeWedge(xmin, ymin, zmin, x2min, x2max, xmax, ymax, zmax)
        obj = doc.addObject("Part::Feature", name or "Wedge")
        obj.Shape = wedge
        obj.Label = name or "Wedge %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _create_ellipse(self, major=10, minor=5, x=0, y=0, z=0, name=None):
        doc = self._ensure_doc()
        ellipse = Part.Ellipse(FreeCAD.Vector(x, y, z), major, minor)
        wire = Part.Wire(ellipse.toShape())
        obj = doc.addObject("Part::Feature", name or "Ellipse")
        obj.Shape = wire
        obj.Label = name or "Ellipse %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(obj)

    def _fuse_objects(self, objects, name=None):
        doc = self._ensure_doc()
        shapes = []
        for name in objects:
            obj = doc.getObject(name)
            if not obj:
                raise ValueError("Object not found: %s" % name)
            shapes.append(obj.Shape)
        fused = shapes[0]
        for s in shapes[1:]:
            fused = fused.fuse(s)
        result = doc.addObject("Part::Feature", name or "Fusion")
        result.Shape = fused
        result.Label = name or "Fusion %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(result)

    def _cut_objects(self, base, tools, name=None):
        doc = self._ensure_doc()
        base_obj = doc.getObject(base)
        if not base_obj:
            raise ValueError("Base object not found: %s" % base)
        result_shape = base_obj.Shape
        for tool_name in tools:
            tool_obj = doc.getObject(tool_name)
            if not tool_obj:
                raise ValueError("Tool object not found: %s" % tool_name)
            result_shape = result_shape.cut(tool_obj.Shape)
        result = doc.addObject("Part::Feature", name or "Cut")
        result.Shape = result_shape
        result.Label = name or "Cut %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(result)

    def _common_objects(self, objects, name=None):
        doc = self._ensure_doc()
        shapes = []
        for name in objects:
            obj = doc.getObject(name)
            if not obj:
                raise ValueError("Object not found: %s" % name)
            shapes.append(obj.Shape)
        result_shape = shapes[0]
        for s in shapes[1:]:
            result_shape = result_shape.common(s)
        result = doc.addObject("Part::Feature", name or "Common")
        result.Shape = result_shape
        result.Label = name or "Common %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(result)

    def _move_object(self, object_name, x=0, y=0, z=0):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        obj.Shape.translate(FreeCAD.Vector(x, y, z))
        doc.recompute()
        return self._object_result(obj)

    def _rotate_object(self, object_name, axis="z", angle=90, center=None):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        axis_map = {"x": FreeCAD.Vector(1, 0, 0), "y": FreeCAD.Vector(0, 1, 0), "z": FreeCAD.Vector(0, 0, 1)}
        if axis not in axis_map:
            raise ValueError("Axis must be x, y, or z")
        if center is None:
            center = [0, 0, 0]
        center_vec = FreeCAD.Vector(*center)
        obj.Shape.rotate(center_vec, axis_map[axis], angle)
        doc.recompute()
        return self._object_result(obj)

    def _scale_object(self, object_name, factor=1.0, center=None):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        if center is None:
            center = [0, 0, 0]
        center_vec = FreeCAD.Vector(*center)
        m = FreeCAD.Matrix()
        m.scale(factor, factor, factor)
        obj.Shape = obj.Shape.transformGeometry(m)
        doc.recompute()
        return self._object_result(obj)

    def _mirror_object(self, object_name, plane="YZ", name=None):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        plane_map = {
            "XY": (FreeCAD.Vector(0, 0, 1), FreeCAD.Vector(0, 0, 0)),
            "XZ": (FreeCAD.Vector(0, 1, 0), FreeCAD.Vector(0, 0, 0)),
            "YZ": (FreeCAD.Vector(1, 0, 0), FreeCAD.Vector(0, 0, 0)),
        }
        if plane not in plane_map:
            raise ValueError("Plane must be XY, XZ, or YZ")
        normal, point = plane_map[plane]
        mirrored = obj.Shape.mirror(point, normal)
        result = doc.addObject("Part::Feature", name or "Mirror")
        result.Shape = mirrored
        result.Label = name or "Mirror of %s" % object_name
        doc.recompute()
        return self._object_result(result)

    def _copy_object(self, object_name, name=None, x=0, y=0, z=0):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        copy = doc.addObject("Part::Feature", name or "Copy")
        copy.Shape = obj.Shape.copy()
        copy.Shape.translate(FreeCAD.Vector(x, y, z))
        copy.Label = name or "Copy of %s" % object_name
        doc.recompute()
        return self._object_result(copy)

    def _fillet_object(self, object_name, radius=1.0, edges=None, name=None):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        shape = obj.Shape
        if edges is None:
            edge_list = shape.Edges
        else:
            edge_list = [shape.Edges[i] for i in edges]
        filleted = shape.makeFillet(radius, edge_list)
        result = doc.addObject("Part::Feature", name or "Fillet")
        result.Shape = filleted
        result.Label = name or "Fillet %s" % object_name
        doc.recompute()
        return self._object_result(result)

    def _chamfer_object(self, object_name, distance=1.0, edges=None, name=None):
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)
        shape = obj.Shape
        if edges is None:
            edge_list = shape.Edges
        else:
            edge_list = [shape.Edges[i] for i in edges]
        chamfered = shape.makeChamfer(distance, edge_list)
        result = doc.addObject("Part::Feature", name or "Chamfer")
        result.Shape = chamfered
        result.Label = name or "Chamfer %s" % object_name
        doc.recompute()
        return self._object_result(result)

    def _create_sketch(self, name=None, plane="XY", body_name=None):
        """Create a new sketch, optionally attached to a PartDesign Body."""
        doc = self._ensure_doc()
        sketch_name = name or "Sketch"

        if body_name:
            body = doc.getObject(body_name)
            if not body:
                raise ValueError("Body not found: %s" % body_name)
            sketch = body.newObject("Sketcher::SketchObject", sketch_name)
            # Attach to body origin plane
            if plane in ("XY_Plane", "XZ_Plane", "YZ_Plane"):
                plane_obj = body.Origin.getObject(plane)
                if plane_obj:
                    sketch.AttachmentSupport = [(plane_obj, "")]
                    sketch.MapMode = "FlatFace"
            elif plane.startswith("Face"):
                sketch.AttachmentSupport = [(body, plane)]
                sketch.MapMode = "FlatFace"
        else:
            sketch = doc.addObject("Sketcher::SketchObject", sketch_name)
            if plane == "XY":
                sketch.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, 0), FreeCAD.Rotation(0, 0, 0, 1)
                )
            elif plane == "XZ":
                sketch.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, 0),
                    FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90),
                )
            elif plane == "YZ":
                sketch.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, 0),
                    FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90),
                )

        sketch.Label = name or "Sketch %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(sketch)

    def _add_sketch_line(self, sketch_name, x1=0, y1=0, x2=10, y2=0):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        idx = sketch.addGeometry(
            Part.LineSegment(
                FreeCAD.Vector(x1, y1, 0),
                FreeCAD.Vector(x2, y2, 0),
            ),
            False,
        )
        doc.recompute()
        return {"added": "line", "index": idx, "sketch": sketch_name}

    def _add_sketch_circle(self, sketch_name, cx=0, cy=0, radius=5):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        idx = sketch.addGeometry(
            Part.Circle(
                FreeCAD.Vector(cx, cy, 0),
                FreeCAD.Vector(0, 0, 1),
                radius,
            ),
            False,
        )
        doc.recompute()
        return {"added": "circle", "index": idx, "sketch": sketch_name}

    def _add_sketch_arc(self, sketch_name, x1, y1, x2, y2, xm, ym):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        idx = sketch.addGeometry(
            Part.Arc(
                FreeCAD.Vector(x1, y1, 0),
                FreeCAD.Vector(xm, ym, 0),
                FreeCAD.Vector(x2, y2, 0),
            ),
            False,
        )
        doc.recompute()
        return {"added": "arc", "index": idx, "sketch": sketch_name}

    # ── Constraint types (name → Sketcher.Constraint enum) ──
    _CONSTRAINT_MAP = {
        "coincident": "Coincident",
        "point_on_line": "PointOnLine",
        "distance": "Distance",
        "distance_x": "DistanceX",
        "distance_y": "DistanceY",
        "horizontal": "Horizontal",
        "vertical": "Vertical",
        "parallel": "Parallel",
        "perpendicular": "Perpendicular",
        "tangent": "Tangent",
        "equal": "Equal",
        "symmetric": "Symmetric",
        "angle": "Angle",
        "radius": "Radius",
        "diameter": "Diameter",
        "fixed": "Fixed",
        "block": "Block",
    }

    def _add_constraint(self, sketch_name, ctype, p1=None, p2=None,
                        value=None, geo1=None, geo2=None,
                        pos1=None, pos2=None):
        """Add a constraint to a sketch.

        Args:
            sketch_name: Name of the sketch object
            ctype: Constraint type (coincident, distance, horizontal, etc.)
            p1, p2: Point refs as [geoId, posId] for coincident/point_on_line
            geo1, geo2: Geometry IDs for line-based constraints
            pos1, pos2: Point positions on geo1/geo2 (1=start, 2=end, 3=center)
            value: Numeric value (distance, angle in degrees, radius, etc.)
        """
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)

        import Sketcher

        type_name = self._CONSTRAINT_MAP.get(ctype.lower())
        if not type_name:
            raise ValueError(
                "Unknown constraint type: '%s'. Available: %s"
                % (ctype, ", ".join(sorted(self._CONSTRAINT_MAP.keys())))
            )

        geo_count = sketch.GeometryCount

        if ctype.lower() in ("coincident", "point_on_line"):
            if not p1 or not p2:
                raise ValueError(
                    "%s requires p1 and p2 as [geoId, posId] pairs" % ctype
                )
            g1, pos1v = p1
            g2, pos2v = p2
            if g1 >= geo_count or g2 >= geo_count:
                raise ValueError(
                    "Geometry ID out of range (0-%d)" % (geo_count - 1)
                )
            c = Sketcher.Constraint(
                type_name, int(g1), int(pos1v), int(g2), int(pos2v)
            )

        elif ctype.lower() in ("distance", "distance_x", "distance_y"):
            if value is None:
                raise ValueError("%s requires a value" % ctype)
            if not p1:
                raise ValueError("%s requires p1 as [geoId, posId]" % ctype)
            g1, pos1v = p1
            if p2:
                g2, pos2v = p2
                c = Sketcher.Constraint(
                    type_name, int(g1), int(pos1v), int(g2), int(pos2v), float(value)
                )
            else:
                c = Sketcher.Constraint(
                    type_name, int(g1), int(pos1v), float(value)
                )

        elif ctype.lower() in ("radius", "diameter"):
            if value is None:
                raise ValueError("%s requires a value" % ctype)
            if geo1 is None:
                raise ValueError("%s requires geo1" % ctype)
            if geo1 >= geo_count:
                raise ValueError("Geometry ID out of range (0-%d)" % (geo_count - 1))
            c = Sketcher.Constraint(type_name, int(geo1), float(value))

        elif ctype.lower() == "angle":
            if value is None:
                raise ValueError("angle requires a value (degrees)")
            if geo1 is None or geo2 is None:
                raise ValueError("angle requires geo1 and geo2")
            c = Sketcher.Constraint(type_name, int(geo1), int(geo2), float(value))

        elif ctype.lower() in ("horizontal", "vertical", "fixed", "block"):
            if geo1 is None:
                raise ValueError("%s requires geo1" % ctype)
            if geo1 >= geo_count:
                raise ValueError("Geometry ID out of range (0-%d)" % (geo_count - 1))
            c = Sketcher.Constraint(type_name, int(geo1))

        elif ctype.lower() in ("parallel", "perpendicular", "tangent", "equal"):
            if geo1 is None or geo2 is None:
                raise ValueError("%s requires geo1 and geo2" % ctype)
            if geo1 >= geo_count or geo2 >= geo_count:
                raise ValueError("Geometry ID out of range (0-%d)" % (geo_count - 1))
            c = Sketcher.Constraint(type_name, int(geo1), int(geo2))

        elif ctype.lower() == "symmetric":
            if not p1 or not p2 or geo1 is None:
                raise ValueError(
                    "symmetric requires p1, p2 as [geoId,posId] and geo1 (axis)"
                )
            g1, pos1v = p1
            g2, pos2v = p2
            c = Sketcher.Constraint(
                type_name, int(g1), int(pos1v), int(g2), int(pos2v), int(geo1)
            )

        else:
            raise ValueError("Unhandled constraint type: %s" % ctype)

        sketch.addConstraint(c)
        doc.recompute()

        # Report constraint status with proper DoF calculation
        info = self._sketch_status(sketch)
        info["constraint_added"] = ctype
        info["constraint_count"] = len(sketch.Constraints)
        return info

    def _sketch_status(self, sketch):
        """Return DoF and constraint status for a sketch.

        Uses sketch.solve() which in FreeCAD 1.x returns the number of
        remaining degrees of freedom (0 = fully constrained).
        Falls back to manual calculation if solve() doesn't return DoF.
        """
        # Try the solver first — in FreeCAD 1.x, solve() returns DoF count
        solve_result = sketch.solve()

        # Determine DoF from solver result
        if isinstance(solve_result, int) and solve_result >= 0:
            dof = solve_result
        else:
            # Fallback: manual calculation
            dof = self._calculate_dof_manual(sketch)

        fully_constrained = (dof == 0)
        return {
            "geometry_count": sketch.GeometryCount,
            "constraint_count": len(sketch.Constraints),
            "dof": dof,
            "fully_constrained": fully_constrained,
            "status": "FULLY CONSTRAINED (green)" if fully_constrained
                      else "UNDER-CONSTRAINED (blue, %d DoF remaining)" % dof,
        }

    def _calculate_dof_manual(self, sketch):
        """Fallback manual DoF calculation when solve() doesn't return DoF.

        Each geometry type has a known number of DoF:
        - Line: 4 (2 points × 2 coords)
        - Circle: 3 (center x,y + radius)
        - Arc: 5 (center x,y + radius + start angle + end angle)
        - Ellipse: 7
        - Spline: variable

        Each constraint removes 1-2 DoF depending on type.
        """
        import Sketcher

        total_dof = 0
        for i in range(sketch.GeometryCount):
            try:
                g = sketch.getGeometry(i)
                gtype = type(g).__name__
                if gtype == "LineSegment":
                    total_dof += 4
                elif gtype == "Circle":
                    total_dof += 3
                elif gtype in ("ArcOfCircle", "Arc"):
                    total_dof += 5
                elif gtype == "Ellipse":
                    total_dof += 7
                else:
                    total_dof += 4
            except Exception:
                total_dof += 4

        # Subtract DoF removed by constraints
        # Constraint type → DoF removed
        _DOF_REMOVED = {
            "Coincident": 2,
            "Block": 4,
            "Distance": 1,
            "DistanceX": 1,
            "DistanceY": 1,
            "Horizontal": 1,
            "Vertical": 1,
            "Parallel": 1,
            "Perpendicular": 1,
            "PointOnLine": 1,
            "Tangent": 1,
            "Equal": 1,
            "Symmetric": 2,
            "Angle": 1,
            "Radius": 1,
            "Diameter": 1,
            "Fixed": 4,
        }

        removed = 0
        for c in sketch.Constraints:
            # Get constraint type name
            try:
                # In FreeCAD 1.x, constraint.Type is an integer enum
                type_val = c.Type
                # Try to get the name from the enum
                ctype_name = None
                try:
                    # Find the name that matches the integer value
                    for name in dir(Sketcher.Constraint.Type):
                        if not name.startswith("_"):
                            try:
                                if Sketcher.Constraint.Type[name] == type_val:
                                    ctype_name = name
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass
                if ctype_name is None:
                    ctype_name = "Unknown"
                removed += _DOF_REMOVED.get(ctype_name, 1)
            except Exception:
                removed += 1

        return max(0, total_dof - removed)

    def _get_sketch_info(self, sketch_name):
        """Get detailed info about a sketch: geometry, constraints, DoF."""
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)

        import Sketcher

        geo_types = []
        for i in range(sketch.GeometryCount):
            try:
                g = sketch.getGeometry(i)
                geo_types.append(type(g).__name__)
            except Exception:
                geo_types.append("unknown")

        constraints = []
        for c in sketch.Constraints:
            ctype = type(c).__name__
            constraints.append({
                "type": ctype,
                "value": getattr(c, "Value", None),
            })

        status = self._sketch_status(sketch)
        status["geometry_types"] = geo_types
        status["constraints"] = constraints
        return status

    def _solve_sketch(self, sketch_name):
        """Force the solver to run and report the result."""
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        doc.recompute()
        return self._sketch_status(sketch)

    def _pad_sketch(self, sketch_name, length=10, name=None):
        """Create a PartDesign Pad from a sketch (if in a Body) or Part extrusion."""
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)

        # Check if sketch is in a PartDesign Body
        body = self._find_body(sketch)
        if body:
            pad = body.newObject("PartDesign::Pad", name or "Pad")
            pad.Profile = sketch
            pad.Length = length
            doc.recompute()
            return self._object_result(pad)
        else:
            # Fallback: Part extrusion
            wire = sketch.Shape.Wires
            if not wire:
                raise ValueError("Sketch has no wires to extrude")
            face = Part.Face(wire[0])
            extruded = face.extrude(FreeCAD.Vector(0, 0, length))
            result = doc.addObject("Part::Feature", name or "Pad")
            result.Shape = extruded
            result.Label = name or "Pad %s" % sketch_name
            doc.recompute()
            return self._object_result(result)

    def _extrude_sketch(self, sketch_name, length=10, direction=None, name=None):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        wire = sketch.Shape.Wires
        if not wire:
            raise ValueError("Sketch has no wires to extrude")
        face = Part.Face(wire[0])
        if direction is None:
            direction = [0, 0, 1]
        dir_vec = FreeCAD.Vector(*direction)
        dir_vec.normalize()
        extruded = face.extrude(dir_vec * length)
        result = doc.addObject("Part::Feature", name or "Extrude")
        result.Shape = extruded
        result.Label = name or "Extrude %s" % sketch_name
        doc.recompute()
        return self._object_result(result)

    # ──────────────────────────────────────────────
    # PartDesign Tools
    # ──────────────────────────────────────────────

    def _create_body(self, name=None):
        """Create a new PartDesign Body."""
        doc = self._ensure_doc()
        body = doc.addObject("PartDesign::Body", name or "Body")
        body.Label = name or "Body %d" % (len(doc.Objects))
        doc.recompute()
        return {
            "name": body.Name,
            "label": body.Label,
            "type": body.TypeId,
        }

    def _pocket_sketch(self, sketch_name, length=10, pocket_type="Length", name=None):
        """Create a PartDesign Pocket (cut extrusion) from a sketch."""
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)

        body = self._find_body(sketch)
        if not body:
            raise ValueError("Sketch must be inside a PartDesign Body for Pocket")

        pocket = body.newObject("PartDesign::Pocket", name or "Pocket")
        pocket.Profile = sketch
        pocket.Length = length
        pocket.Type = pocket_type
        doc.recompute()
        return self._object_result(pocket)

    def _revolution_sketch(self, sketch_name, angle=360.0, axis="Base_Y", name=None):
        """Create a PartDesign Revolution from a sketch."""
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)

        body = self._find_body(sketch)
        if not body:
            raise ValueError("Sketch must be inside a PartDesign Body for Revolution")

        rev = body.newObject("PartDesign::Revolution", name or "Revolution")
        rev.Profile = sketch
        rev.Angle = angle

        # Set axis reference
        if axis.startswith("Base_"):
            axis_ref = axis.replace("Base_", "")
            axis_obj = body.Origin.getObject("%s_Axis" % axis_ref)
            if axis_obj:
                rev.ReferenceAxis = (axis_obj, [""])
        elif axis == "Sketch_V":
            rev.ReferenceAxis = (sketch, ["V_Axis"])
        elif axis == "Sketch_H":
            rev.ReferenceAxis = (sketch, ["H_Axis"])

        doc.recompute()
        return self._object_result(rev)

    def _groove_sketch(self, sketch_name, angle=360.0, axis="Base_Y", name=None):
        """Create a PartDesign Groove (subtractive revolution) from a sketch."""
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)

        body = self._find_body(sketch)
        if not body:
            raise ValueError("Sketch must be inside a PartDesign Body for Groove")

        groove = body.newObject("PartDesign::Groove", name or "Groove")
        groove.Profile = sketch
        groove.Angle = angle

        if axis.startswith("Base_"):
            axis_ref = axis.replace("Base_", "")
            axis_obj = body.Origin.getObject("%s_Axis" % axis_ref)
            if axis_obj:
                groove.ReferenceAxis = (axis_obj, [""])

        doc.recompute()
        return self._object_result(groove)

    def _partdesign_fillet(self, object_name, radius=1.0, edges=None, name=None):
        """Add a PartDesign Fillet to an object in a Body."""
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)

        body = self._find_body(obj)
        if not body:
            raise ValueError("Object must be inside a PartDesign Body")

        fillet = body.newObject("PartDesign::Fillet", name or "Fillet")
        if edges:
            fillet.Base = (obj, edges)
        else:
            fillet.Base = (obj, ["Edge%d" % (i + 1) for i in range(len(obj.Shape.Edges))])
        fillet.Radius = radius
        doc.recompute()
        return self._object_result(fillet)

    def _partdesign_chamfer(self, object_name, size=1.0, edges=None, name=None):
        """Add a PartDesign Chamfer to an object in a Body."""
        doc = self._ensure_doc()
        obj = doc.getObject(object_name)
        if not obj:
            raise ValueError("Object not found: %s" % object_name)

        body = self._find_body(obj)
        if not body:
            raise ValueError("Object must be inside a PartDesign Body")

        chamfer = body.newObject("PartDesign::Chamfer", name or "Chamfer")
        if edges:
            chamfer.Base = (obj, edges)
        else:
            chamfer.Base = (obj, ["Edge%d" % (i + 1) for i in range(len(obj.Shape.Edges))])
        chamfer.Size = size
        doc.recompute()
        return self._object_result(chamfer)

    def _find_body(self, obj):
        """Find the PartDesign Body containing the given object."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            return None
        for o in doc.Objects:
            if o.TypeId == "PartDesign::Body":
                if hasattr(o, "Group") and obj in o.Group:
                    return o
        return None

    # ──────────────────────────────────────────────
    # Export
    # ──────────────────────────────────────────────

    def _export_stl(self, path, objects=None, tolerance=0.1):
        doc = self._ensure_doc()
        if objects:
            to_export = []
            for name in objects:
                obj = doc.getObject(name)
                if not obj:
                    raise ValueError("Object not found: %s" % name)
                to_export.append(obj)
        else:
            to_export = doc.Objects
        import Mesh
        import MeshPart
        mesh = Mesh.Mesh()
        for obj in to_export:
            if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                m = MeshPart.meshFromShape(
                    Shape=obj.Shape,
                    LinearDeflection=tolerance,
                    AngularDeflection=0.5,
                )
                mesh.addMesh(m)
        mesh.write(path)
        return {"exported": path, "format": "STL", "facets": mesh.CountFacets}

    def _export_step(self, path, objects=None):
        doc = self._ensure_doc()
        if objects:
            to_export = []
            for name in objects:
                obj = doc.getObject(name)
                if not obj:
                    raise ValueError("Object not found: %s" % name)
                to_export.append(obj)
        else:
            to_export = doc.Objects
        Part.export(to_export, path)
        return {"exported": path, "format": "STEP"}

    def _export_3mf(self, path, objects=None):
        doc = self._ensure_doc()
        if objects:
            to_export = []
            for name in objects:
                obj = doc.getObject(name)
                if not obj:
                    raise ValueError("Object not found: %s" % name)
                to_export.append(obj)
        else:
            to_export = doc.Objects
        import Mesh
        import MeshPart
        mesh = Mesh.Mesh()
        for obj in to_export:
            if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                m = MeshPart.meshFromShape(Shape=obj.Shape, LinearDeflection=0.1)
                mesh.addMesh(m)
        mesh.write(path)
        return {"exported": path, "format": "3MF"}

    def _export_svg(self, path, objects=None, direction=None):
        doc = self._ensure_doc()
        if direction is None:
            direction = [0, 0, 1]
        import TechDraw
        page = doc.addObject("TechDraw::DrawPage", "SvgPage")
        template = doc.addObject("TechDraw::DrawSVGTemplate", "SvgTemplate")
        import os
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        template.Template = os.path.join(template_dir, "A3_Landscape_blank.svg")
        page.Template = template
        view = doc.addObject("TechDraw::DrawViewPart", "SvgView")
        page.addView(view)
        if objects:
            view.Source = [doc.getObject(o) for o in objects if doc.getObject(o)]
        else:
            view.Source = doc.Objects
        view.Direction = FreeCAD.Vector(*direction)
        doc.recompute()
        import TechDrawGui
        TechDrawGui.exportPageAsSvg(page, path)
        return {"exported": path, "format": "SVG"}

    # ──────────────────────────────────────────────
    # View
    # ──────────────────────────────────────────────

    def _fit_all(self):
        import FreeCADGui as Gui
        Gui.SendMsgToActiveView("ViewFit")
        return {"fitted": True}

    def _set_view(self, view="isometric"):
        import FreeCADGui as Gui
        v = Gui.activeDocument().activeView()
        if view == "isometric":
            v.viewIsometric()
        elif view == "front":
            v.viewFront()
        elif view == "top":
            v.viewTop()
        elif view == "right":
            v.viewRight()
        elif view == "rear":
            v.viewRear()
        elif view == "bottom":
            v.viewBottom()
        elif view == "left":
            v.viewLeft()
        elif view == "axono":
            v.viewAxonometric()
        return {"view": view}

    def _screenshot(self, path=None, width=1920, height=1080):
        import FreeCADGui as Gui
        view = Gui.activeDocument().activeView()
        if path is None:
            import tempfile
            path = os.path.join(tempfile.gettempdir(), "freecad_screenshot.png")
        view.saveImage(path, width, height, "White")
        return {"screenshot": path, "width": width, "height": height}

    # ──────────────────────────────────────────────
    # Misc
    # ──────────────────────────────────────────────

    def _run_python(self, code, timeout=30):
        namespace = {
            "FreeCAD": FreeCAD,
            "Part": Part,
            "sketcher": __import__("Sketcher"),
            "Mesh": __import__("Mesh"),
            "MeshPart": __import__("MeshPart"),
            "os": os,
            "json": json,
            "math": __import__("math"),
        }
        try:
            exec(code, namespace)
            result = namespace.get("result", None)
            return {
                "executed": True,
                "result": result,
                "objects": [o.Name for o in FreeCAD.ActiveDocument.Objects]
                if FreeCAD.ActiveDocument else [],
            }
        except Exception as e:
            return {
                "executed": False,
                "error": "%s: %s" % (type(e).__name__, str(e)),
                "traceback": traceback.format_exc(),
            }

    def _get_selection(self):
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelection()
        return {
            "selected": [s.Name for s in sel],
            "count": len(sel),
        }

    def _ping(self):
        return {"pong": True, "timestamp": time.ctime()}

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _ensure_doc(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            doc = FreeCAD.newDocument("Document")
        return doc

    def _object_result(self, obj):
        result = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
        }
        if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
            bbox = obj.Shape.BoundBox
            result["bbox"] = {
                "x": [bbox.XMin, bbox.XMax],
                "y": [bbox.YMin, bbox.YMax],
                "z": [bbox.ZMin, bbox.ZMax],
            }
            result["volume"] = obj.Shape.Volume
            result["faces"] = len(obj.Shape.Faces)
            result["edges"] = len(obj.Shape.Edges)
        return result
