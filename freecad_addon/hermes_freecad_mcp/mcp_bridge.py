"""
Hermes FreeCAD MCP — Bridge Server
===================================
mcp_bridge.py — TCP server that runs inside FreeCAD's Python interpreter.

Receives JSON commands from the MCP server (via localhost TCP) and
executes them in the FreeCAD context. All geometry operations happen
here, so the user sees everything live in the FreeCAD 3D viewport.

Thread safety: FreeCAD's Python API is NOT thread-safe. All FreeCAD
calls are marshalled to the main (GUI) thread via Qt's event loop.

Protocol: newline-delimited JSON over TCP.
  Request:  {"id": 1, "tool": "create_box", "args": {...}}\n
  Response: {"id": 1, "result": {...}}\n
"""

import json
import socket
import threading
import traceback
import time
import itertools

import FreeCAD
import Part
from PySide import QtCore

MCP_BRIDGE_PORT = 9876
HOST = "127.0.0.1"
_req_counter = itertools.count(1)


class _MainThreadDispatcher(QtCore.QObject):
    """QObject that lives on the main thread and executes FreeCAD calls."""

    def __init__(self):
        super().__init__()
        self._results = {}
        self._lock = threading.Lock()
        self._pending = {}

    def execute_on_main(self, func, args, kwargs, req_id):
        """Schedule func to run on the main thread, store result by req_id."""
        with self._lock:
            self._pending[req_id] = (func, args, kwargs)

        # Use QMetaObject to safely invoke on main thread
        QtCore.QMetaObject.invokeMethod(
            self,
            "_execute_pending",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG("int", req_id),
        )

    def _execute_pending(self, req_id):
        """Slot: execute the pending function on the main thread."""
        with self._lock:
            if req_id not in self._pending:
                return
            func, args, kwargs = self._pending.pop(req_id)

        try:
            result = func(*args, **kwargs)
            with self._lock:
                self._results[req_id] = {"result": result}
        except Exception as e:
            with self._lock:
                self._results[req_id] = {
                    "error": "%s: %s" % (type(e).__name__, str(e)),
                    "traceback": traceback.format_exc(),
                }

    # Qt slot decorator
    _execute_pending = QtCore.Slot("int")(_execute_pending)

    def get_result(self, req_id, timeout=60.0):
        """Wait for the result of a scheduled execution."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if req_id in self._results:
                    return self._results.pop(req_id)
            time.sleep(0.01)
        raise TimeoutError("FreeCAD command timed out after %.0fs" % timeout)


class MCPBridgeServer:
    """TCP server that accepts commands from the MCP bridge."""

    def __init__(self, port=MCP_BRIDGE_PORT):
        self.port = port
        self.host = HOST
        self.server_socket = None
        self.running = False
        self.thread = None
        self.connection_count = 0
        self.last_command_time = None
        self.command_log = []
        self._dispatcher = _MainThreadDispatcher()

    @property
    def is_running(self):
        return self.running

    def start(self):
        """Start the TCP server in a daemon thread."""
        if self.running:
            return

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)

        self.running = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the TCP server."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
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
            "pad_sketch": self._pad_sketch,
            "extrude_sketch": self._extrude_sketch,
            "export_stl": self._export_stl,
            "export_step": self._export_step,
            "export_3mf": self._export_3mf,
            "export_svg": self._export_svg,
            "fit_all": self._fit_all,
            "set_view": self._set_view,
            "screenshot": self._screenshot,
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

        # Execute on main thread
        req_id = next(_req_counter)
        self._dispatcher.execute_on_main(handler, (), args, req_id)
        result = self._dispatcher.get_result(req_id, timeout=timeout)

        if "error" in result:
            raise RuntimeError(result["error"])

        return result.get("result", result)

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
        if hasattr(obj, "Shape") and obj.Shape:
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

    def _create_sketch(self, name=None, plane="XY"):
        doc = self._ensure_doc()
        sketch = doc.addObject("Sketcher::SketchObject", name or "Sketch")
        sketch.Label = name or "Sketch %d" % (len(doc.Objects))
        doc.recompute()
        return self._object_result(sketch)

    def _add_sketch_line(self, sketch_name, x1=0, y1=0, x2=10, y2=0):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        import Sketcher
        sketch.addGeometry(
            Part.LineSegment(
                FreeCAD.Vector(x1, y1, 0),
                FreeCAD.Vector(x2, y2, 0),
            ),
            False,
        )
        doc.recompute()
        return {"added": "line", "sketch": sketch_name}

    def _add_sketch_circle(self, sketch_name, cx=0, cy=0, radius=5):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        import Sketcher
        sketch.addGeometry(
            Part.Circle(
                FreeCAD.Vector(cx, cy, 0),
                FreeCAD.Vector(0, 0, 1),
                radius,
            ),
            False,
        )
        doc.recompute()
        return {"added": "circle", "sketch": sketch_name}

    def _add_sketch_arc(self, sketch_name, x1, y1, x2, y2, xm, ym):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
        import Sketcher
        sketch.addGeometry(
            Part.Arc(
                FreeCAD.Vector(x1, y1, 0),
                FreeCAD.Vector(xm, ym, 0),
                FreeCAD.Vector(x2, y2, 0),
            ),
            False,
        )
        doc.recompute()
        return {"added": "arc", "sketch": sketch_name}

    def _pad_sketch(self, sketch_name, length=10, name=None):
        doc = self._ensure_doc()
        sketch = doc.getObject(sketch_name)
        if not sketch:
            raise ValueError("Sketch not found: %s" % sketch_name)
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
            if hasattr(obj, "Shape") and obj.Shape:
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
            if hasattr(obj, "Shape") and obj.Shape:
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
        if hasattr(obj, "Shape") and obj.Shape:
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
