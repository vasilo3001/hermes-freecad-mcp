#!/usr/bin/env python3
"""
Hermes FreeCAD MCP — MCP Server (MCP SDK 2.x)
==============================================
mcp_server.py — MCP server that bridges Hermes Agent to FreeCAD.

Supports two transports:
  1. stdio  — for local Hermes (per-session, spawned by Hermes)
  2. HTTP   — for persistent deployment on homeserver (Portainer stack)

HTTP mode (recommended for multi-agent / Tailscale):
  python3 mcp_server.py --http --port 8000
  # Hermes config:
  #   mcp_servers:
  #     freecad:
  #       url: "http://192.168.1.184:3111/mcp"

stdio mode (local, single agent):
  python3 mcp_server.py
  # Hermes config:
  #   mcp_servers:
  #     freecad:
  #       command: "python3"
  #       args: ["/path/to/mcp_server.py"]

Environment variables:
  FREECAD_HOST  — FreeCAD bridge host (default: 127.0.0.1, use host.docker.internal in Docker)
  FREECAD_PORT  — FreeCAD bridge port (default: 9876)
"""

import json
import socket
import sys
import os
import argparse

# ── MCP SDK ──────────────────────────────────────────
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    print("ERROR: mcp package not installed (v2.x). Run: pip install 'mcp>=2.0.0'", file=sys.stderr)
    sys.exit(1)

# ── Configuration ────────────────────────────────────
FREECAD_HOST = os.environ.get("FREECAD_HOST", "127.0.0.1")
FREECAD_PORT = int(os.environ.get("FREECAD_PORT", "9876"))
SERVER_NAME = "freecad"
SERVER_VERSION = "1.0.0"


# ── TCP Client ───────────────────────────────────────
class FreeCADClient:
    """TCP client that sends commands to the FreeCAD addon."""

    def __init__(self, host=FREECAD_HOST, port=FREECAD_PORT):
        self.host = host
        self.port = port
        self._id_counter = 0

    def _next_id(self):
        self._id_counter += 1
        return self._id_counter

    def send_command(self, tool: str, args: dict = None, timeout: float = 30.0) -> dict:
        """Send a command to FreeCAD and return the result."""
        if args is None:
            args = {}

        req_id = self._next_id()
        request = json.dumps({"id": req_id, "tool": tool, "args": args}) + "\n"

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((self.host, self.port))
            sock.sendall(request.encode("utf-8"))

            # Read response (newline-delimited)
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
            sock.close()

            if not buffer:
                return {"error": "Empty response from FreeCAD"}

            response = json.loads(buffer.decode("utf-8").strip())
            return response

        except ConnectionRefusedError:
            return {
                "error": (
                    f"Cannot connect to FreeCAD at {self.host}:{self.port}. "
                    "Is FreeCAD running with the Hermes MCP workbench active "
                    "and the bridge started?"
                )
            }
        except socket.timeout:
            return {"error": f"Timeout waiting for FreeCAD response ({timeout}s)"}
        except Exception as e:
            return {"error": f"TCP error: {type(e).__name__}: {e}"}


# ── MCP Server ───────────────────────────────────────
server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)
client = FreeCADClient()


def _forward(tool: str, args: dict, timeout: float = 60.0) -> str:
    """Forward a tool call to FreeCAD and return JSON result."""
    if tool == "ping":
        timeout = 5.0
    elif tool == "run_python":
        timeout = 120.0

    response = client.send_command(tool, args, timeout=timeout)

    if "error" in response:
        text = json.dumps({"error": response["error"]})
        if "traceback" in response:
            text += "\n\nTraceback:\n" + response["traceback"]
        return text

    result = response.get("result", response)
    return json.dumps(result, indent=2)


# ── Tool Definitions ─────────────────────────────────
# Each @server.tool() registers a tool with MCPServer.
# The function name becomes the tool name (prefixed with freecad_ below).


# ── Connection ──

@server.tool()
def freecad_ping() -> str:
    """Check if the FreeCAD bridge is running and responsive."""
    return _forward("ping", {})


@server.tool()
def freecad_get_status() -> str:
    """Get FreeCAD addon status (running, connections, last command)."""
    response = client.send_command("ping", {}, timeout=5.0)
    if "error" in response:
        return json.dumps({"status": "OFFLINE", "detail": response["error"]})
    return json.dumps({"status": "ONLINE", "host": FREECAD_HOST, "port": FREECAD_PORT})


# ── Document ──

@server.tool()
def freecad_get_document_info() -> str:
    """Get info about the active FreeCAD document (name, objects, labels)."""
    return _forward("get_document_info", {})


@server.tool()
def freecad_new_document(name: str = "Document") -> str:
    """Create a new FreeCAD document."""
    return _forward("new_document", {"name": name})


@server.tool()
def freecad_save_document(path: str = "") -> str:
    """Save the active FreeCAD document."""
    args = {}
    if path:
        args["path"] = path
    return _forward("save_document", args)


@server.tool()
def freecad_open_document(path: str) -> str:
    """Open an existing FreeCAD document (.FCStd)."""
    return _forward("open_document", {"path": path})


@server.tool()
def freecad_list_objects() -> str:
    """List all objects in the active document with their types and bounding boxes."""
    return _forward("list_objects", {})


@server.tool()
def freecad_get_object_info(name: str) -> str:
    """Get detailed info about a specific object (bbox, volume, faces, edges)."""
    return _forward("get_object_info", {"name": name})


@server.tool()
def freecad_delete_object(name: str) -> str:
    """Delete an object from the document."""
    return _forward("delete_object", {"name": name})


@server.tool()
def freecad_undo() -> str:
    """Undo the last operation."""
    return _forward("undo", {})


@server.tool()
def freecad_redo() -> str:
    """Redo the last undone operation."""
    return _forward("redo", {})


# ── Part Primitives ──

@server.tool()
def freecad_create_box(
    length: float = 10, width: float = 10, height: float = 10,
    x: float = 0, y: float = 0, z: float = 0, name: str = "",
) -> str:
    """Create a box (cuboid) in the active document. All dimensions in mm."""
    args = {"length": length, "width": width, "height": height, "x": x, "y": y, "z": z}
    if name:
        args["name"] = name
    return _forward("create_box", args)


@server.tool()
def freecad_create_cylinder(
    radius: float = 5, height: float = 10,
    x: float = 0, y: float = 0, z: float = 0, name: str = "",
) -> str:
    """Create a cylinder in the active document. Dimensions in mm."""
    args = {"radius": radius, "height": height, "x": x, "y": y, "z": z}
    if name:
        args["name"] = name
    return _forward("create_cylinder", args)


@server.tool()
def freecad_create_sphere(
    radius: float = 5,
    x: float = 0, y: float = 0, z: float = 0, name: str = "",
) -> str:
    """Create a sphere in the active document. Dimensions in mm."""
    args = {"radius": radius, "x": x, "y": y, "z": z}
    if name:
        args["name"] = name
    return _forward("create_sphere", args)


@server.tool()
def freecad_create_cone(
    radius1: float = 5, radius2: float = 0, height: float = 10,
    x: float = 0, y: float = 0, z: float = 0, name: str = "",
) -> str:
    """Create a cone in the active document. radius1=bottom, radius2=top (0 for point). Dimensions in mm."""
    args = {"radius1": radius1, "radius2": radius2, "height": height, "x": x, "y": y, "z": z}
    if name:
        args["name"] = name
    return _forward("create_cone", args)


@server.tool()
def freecad_create_torus(
    radius1: float = 10, radius2: float = 3,
    x: float = 0, y: float = 0, z: float = 0, name: str = "",
) -> str:
    """Create a torus (donut) in the active document. radius1=major, radius2=minor. Dimensions in mm."""
    args = {"radius1": radius1, "radius2": radius2, "x": x, "y": y, "z": z}
    if name:
        args["name"] = name
    return _forward("create_torus", args)


# ── Boolean Operations ──

@server.tool()
def freecad_fuse_objects(objects: list[str], name: str = "") -> str:
    """Fuse (union) two or more objects together into one solid."""
    args = {"objects": objects}
    if name:
        args["name"] = name
    return _forward("fuse_objects", args)


@server.tool()
def freecad_cut_objects(base: str, tools: list[str], name: str = "") -> str:
    """Cut (subtract) tool objects from a base object."""
    args = {"base": base, "tools": tools}
    if name:
        args["name"] = name
    return _forward("cut_objects", args)


@server.tool()
def freecad_common_objects(objects: list[str], name: str = "") -> str:
    """Find the common (intersection) volume of two or more objects."""
    args = {"objects": objects}
    if name:
        args["name"] = name
    return _forward("common_objects", args)


# ── Transformations ──

@server.tool()
def freecad_move_object(
    object_name: str,
    x: float = 0, y: float = 0, z: float = 0,
) -> str:
    """Move (translate) an object. Displacements in mm."""
    return _forward("move_object", {"object_name": object_name, "x": x, "y": y, "z": z})


@server.tool()
def freecad_rotate_object(
    object_name: str,
    axis: str = "z",
    angle: float = 90,
    center: list[float] = None,
) -> str:
    """Rotate an object around an axis. axis: x|y|z, angle in degrees."""
    args = {"object_name": object_name, "axis": axis, "angle": angle}
    if center:
        args["center"] = center
    return _forward("rotate_object", args)


@server.tool()
def freecad_scale_object(
    object_name: str,
    factor: float = 1.0,
    center: list[float] = None,
) -> str:
    """Scale an object by a factor (1.0 = no change)."""
    args = {"object_name": object_name, "factor": factor}
    if center:
        args["center"] = center
    return _forward("scale_object", args)


@server.tool()
def freecad_mirror_object(
    object_name: str,
    plane: str = "YZ",
    name: str = "",
) -> str:
    """Create a mirror copy of an object across a plane. plane: XY|XZ|YZ."""
    args = {"object_name": object_name, "plane": plane}
    if name:
        args["name"] = name
    return _forward("mirror_object", args)


@server.tool()
def freecad_copy_object(
    object_name: str,
    name: str = "",
    x: float = 0, y: float = 0, z: float = 0,
) -> str:
    """Create a copy of an object, optionally offset."""
    args = {"object_name": object_name, "x": x, "y": y, "z": z}
    if name:
        args["name"] = name
    return _forward("copy_object", args)


# ── Dress-up ──

@server.tool()
def freecad_fillet_object(
    object_name: str,
    radius: float = 1.0,
    edges: list[int] = None,
    name: str = "",
) -> str:
    """Apply a fillet to edges of an object. If no edges specified, fillets all edges."""
    args = {"object_name": object_name, "radius": radius}
    if edges:
        args["edges"] = edges
    if name:
        args["name"] = name
    return _forward("fillet_object", args)


@server.tool()
def freecad_chamfer_object(
    object_name: str,
    distance: float = 1.0,
    edges: list[int] = None,
    name: str = "",
) -> str:
    """Apply a chamfer to edges of an object. If no edges specified, chamfers all edges."""
    args = {"object_name": object_name, "distance": distance}
    if edges:
        args["edges"] = edges
    if name:
        args["name"] = name
    return _forward("chamfer_object", args)


# ── Sketching ──

@server.tool()
def freecad_create_sketch(name: str = "", plane: str = "XY") -> str:
    """Create a new sketch object on a plane. plane: XY|XZ|YZ."""
    args = {"plane": plane}
    if name:
        args["name"] = name
    return _forward("create_sketch", args)


@server.tool()
def freecad_add_sketch_line(
    sketch_name: str,
    x1: float = 0, y1: float = 0,
    x2: float = 10, y2: float = 0,
) -> str:
    """Add a line to a sketch. Coordinates in mm."""
    return _forward("add_sketch_line", {
        "sketch_name": sketch_name,
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
    })


@server.tool()
def freecad_add_sketch_circle(
    sketch_name: str,
    cx: float = 0, cy: float = 0,
    radius: float = 5,
) -> str:
    """Add a circle to a sketch. Coordinates and radius in mm."""
    return _forward("add_sketch_circle", {
        "sketch_name": sketch_name,
        "cx": cx, "cy": cy, "radius": radius,
    })


@server.tool()
def freecad_add_sketch_arc(
    sketch_name: str,
    x1: float, y1: float,
    x2: float, y2: float,
    xm: float, ym: float,
) -> str:
    """Add an arc to a sketch. (x1,y1)=start, (x2,y2)=end, (xm,ym)=midpoint."""
    return _forward("add_sketch_arc", {
        "sketch_name": sketch_name,
        "x1": x1, "y1": y1, "x2": x2, "y2": y2, "xm": xm, "ym": ym,
    })


@server.tool()
def freecad_add_constraint(
    sketch_name: str,
    ctype: str,
    p1: list[int] = None,
    p2: list[int] = None,
    value: float = None,
    geo1: int = None,
    geo2: int = None,
) -> str:
    """Add a constraint to a sketch.

    ctype: coincident, point_on_line, distance, distance_x, distance_y,
           horizontal, vertical, parallel, perpendicular, tangent, equal,
           symmetric, angle, radius, diameter, fixed, block

    Point-based (coincident, point_on_line, distance): p1=[geoId,posId], p2=[geoId,posId]
    Line-based (horizontal, vertical, fixed, block): geo1
    Two-line (parallel, perpendicular, tangent, equal, angle): geo1, geo2
    Radius/Diameter: geo1 + value
    Symmetric: p1, p2 + geo1 (axis)

    posId: 1=start point, 2=end point, 3=center (for circles/arcs)
    Returns constraint count and DoF (0 = fully constrained / green).
    """
    args = {"sketch_name": sketch_name, "ctype": ctype}
    if p1 is not None:
        args["p1"] = p1
    if p2 is not None:
        args["p2"] = p2
    if value is not None:
        args["value"] = value
    if geo1 is not None:
        args["geo1"] = geo1
    if geo2 is not None:
        args["geo2"] = geo2
    return _forward("add_constraint", args)


@server.tool()
def freecad_get_sketch_info(sketch_name: str) -> str:
    """Get detailed info about a sketch: geometry types, constraints, DoF, fully-constrained status."""
    return _forward("get_sketch_info", {"sketch_name": sketch_name})


@server.tool()
def freecad_solve_sketch(sketch_name: str) -> str:
    """Force the solver to run on a sketch and report the result (DoF, constraint status)."""
    return _forward("solve_sketch", {"sketch_name": sketch_name})


@server.tool()
def freecad_pad_sketch(
    sketch_name: str,
    length: float = 10,
    name: str = "",
) -> str:
    """Pad (extrude) a sketch into a 3D solid. Length in mm."""
    args = {"sketch_name": sketch_name, "length": length}
    if name:
        args["name"] = name
    return _forward("pad_sketch", args)


# ── PartDesign ──

@server.tool()
def freecad_create_body(name: str = "") -> str:
    """Create a new PartDesign Body (container for feature-based modeling)."""
    args = {}
    if name:
        args["name"] = name
    return _forward("create_body", args)


@server.tool()
def freecad_pocket_sketch(
    sketch_name: str,
    length: float = 10,
    pocket_type: str = "Length",
    name: str = "",
) -> str:
    """Create a PartDesign Pocket (cut extrusion) from a sketch in a Body.
    pocket_type: Length|ThroughAll|UpToFace|UpToVertex|TwoLengths"""
    args = {"sketch_name": sketch_name, "length": length, "pocket_type": pocket_type}
    if name:
        args["name"] = name
    return _forward("pocket_sketch", args)


@server.tool()
def freecad_revolution_sketch(
    sketch_name: str,
    angle: float = 360.0,
    axis: str = "Base_Y",
    name: str = "",
) -> str:
    """Create a PartDesign Revolution (revolve) from a sketch in a Body.
    axis: Base_X|Base_Y|Base_Z|Sketch_H|Sketch_V"""
    args = {"sketch_name": sketch_name, "angle": angle, "axis": axis}
    if name:
        args["name"] = name
    return _forward("revolution_sketch", args)


@server.tool()
def freecad_groove_sketch(
    sketch_name: str,
    angle: float = 360.0,
    axis: str = "Base_Y",
    name: str = "",
) -> str:
    """Create a PartDesign Groove (subtractive revolution) from a sketch in a Body.
    axis: Base_X|Base_Y|Base_Z|Sketch_H|Sketch_V"""
    args = {"sketch_name": sketch_name, "angle": angle, "axis": axis}
    if name:
        args["name"] = name
    return _forward("groove_sketch", args)


@server.tool()
def freecad_partdesign_fillet(
    object_name: str,
    radius: float = 1.0,
    edges: list[str] = None,
    name: str = "",
) -> str:
    """Add a PartDesign Fillet to an object in a Body.
    edges: list of edge names (e.g. ["Edge1", "Edge3"]), or None for all edges."""
    args = {"object_name": object_name, "radius": radius}
    if edges:
        args["edges"] = edges
    if name:
        args["name"] = name
    return _forward("partdesign_fillet", args)


@server.tool()
def freecad_partdesign_chamfer(
    object_name: str,
    size: float = 1.0,
    edges: list[str] = None,
    name: str = "",
) -> str:
    """Add a PartDesign Chamfer to an object in a Body.
    edges: list of edge names (e.g. ["Edge1", "Edge3"]), or None for all edges."""
    args = {"object_name": object_name, "size": size}
    if edges:
        args["edges"] = edges
    if name:
        args["name"] = name
    return _forward("partdesign_chamfer", args)


# ── Export ──

@server.tool()
def freecad_export_stl(
    path: str,
    objects: list[str] = None,
    tolerance: float = 0.1,
) -> str:
    """Export objects to an STL file (for 3D printing)."""
    args = {"path": path, "tolerance": tolerance}
    if objects:
        args["objects"] = objects
    return _forward("export_stl", args)


@server.tool()
def freecad_export_step(
    path: str,
    objects: list[str] = None,
) -> str:
    """Export objects to a STEP file (for CAD exchange)."""
    args = {"path": path}
    if objects:
        args["objects"] = objects
    return _forward("export_step", args)


@server.tool()
def freecad_export_3mf(
    path: str,
    objects: list[str] = None,
) -> str:
    """Export objects to a 3MF file (for 3D printing)."""
    args = {"path": path}
    if objects:
        args["objects"] = objects
    return _forward("export_3mf", args)


# ── View ──

@server.tool()
def freecad_fit_all() -> str:
    """Fit all objects in the FreeCAD viewport."""
    return _forward("fit_all", {})


@server.tool()
def freecad_set_view(view: str = "isometric") -> str:
    """Set the viewport to a standard view direction.
    view: isometric|front|top|right|rear|bottom|left|axono"""
    return _forward("set_view", {"view": view})


@server.tool()
def freecad_screenshot(
    path: str = "",
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Capture a screenshot of the FreeCAD viewport and save to a PNG file."""
    args = {"width": width, "height": height}
    if path:
        args["path"] = path
    return _forward("screenshot", args)


# ── Advanced ──

@server.tool()
def freecad_run_python(code: str) -> str:
    """Execute arbitrary Python code in the FreeCAD context. This is the most powerful tool —
    use it for complex geometry, parametric models, or anything the specific tools don't cover.
    The code has access to FreeCAD, Part, Sketcher, Mesh, MeshPart, os, json, math.
    Set a variable named 'result' to return a value."""
    return _forward("run_python", {"code": code}, timeout=120.0)


@server.tool()
def freecad_get_selection() -> str:
    """Get the current selection in the FreeCAD GUI (objects the user has selected)."""
    return _forward("get_selection", {})


# ── Main ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FreeCAD MCP Server")
    parser.add_argument("--http", action="store_true", help="Run in HTTP (streamable) mode")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    args = parser.parse_args()

    if args.http:
        print(f"FreeCAD MCP Server — HTTP mode on {args.host}:{args.port}/mcp", file=sys.stderr)
        print(f"  FreeCAD bridge: {FREECAD_HOST}:{FREECAD_PORT}", file=sys.stderr)
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            stateless_http=True,  # No session state — safe for multi-client
        )
    else:
        print(f"FreeCAD MCP Server — stdio mode", file=sys.stderr)
        print(f"  FreeCAD bridge: {FREECAD_HOST}:{FREECAD_PORT}", file=sys.stderr)
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
