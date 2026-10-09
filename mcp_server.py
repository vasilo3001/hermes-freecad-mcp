#!/usr/bin/env python3
"""
Hermes FreeCAD MCP — MCP Server
================================
mcp_server.py — MCP server that Hermes Agent connects to via stdio.

This server:
  1. Registers ~35 MCP tools for the Hermes Agent
  2. Forwards tool calls to the FreeCAD addon via TCP (localhost:9876)
  3. Returns results back to Hermes

Run this with:  python3 mcp_server.py
Hermes config:
  mcp_servers:
    freecad:
      command: "python3"
      args: ["/path/to/mcp_server.py"]
"""

import asyncio
import json
import socket
import sys
import os

# ── MCP SDK ──────────────────────────────────────────
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions
    import mcp.types as types
except ImportError:
    print("ERROR: mcp package not installed. Run: pip install mcp", file=sys.stderr)
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
server = Server(SERVER_NAME)
client = FreeCADClient()


def _tool(name, description, properties, required=None):
    """Helper to build an MCP Tool definition."""
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return types.Tool(name=name, description=description, inputSchema=schema)


# ── Tool Definitions ─────────────────────────────────
# These are the tools that Hermes Agent will see and can call.

def get_all_tools():
    """Return the full list of MCP tools."""
    return [
        # ── Connection ──
        _tool(
            "freecad_ping",
            "Check if the FreeCAD bridge is running and responsive.",
            {},
        ),
        _tool(
            "freecad_get_status",
            "Get FreeCAD addon status (running, connections, last command).",
            {},
        ),

        # ── Document ──
        _tool(
            "freecad_get_document_info",
            "Get info about the active FreeCAD document (name, objects, labels).",
            {},
        ),
        _tool(
            "freecad_new_document",
            "Create a new FreeCAD document.",
            {"name": {"type": "string", "description": "Document name", "default": "Document"}},
        ),
        _tool(
            "freecad_save_document",
            "Save the active FreeCAD document.",
            {"path": {"type": "string", "description": "File path to save to (optional, uses current path if omitted)"}},
        ),
        _tool(
            "freecad_open_document",
            "Open an existing FreeCAD document (.FCStd).",
            {"path": {"type": "string", "description": "Path to the .FCStd file"}},
            required=["path"],
        ),
        _tool(
            "freecad_list_objects",
            "List all objects in the active document with their types and bounding boxes.",
            {},
        ),
        _tool(
            "freecad_get_object_info",
            "Get detailed info about a specific object (bbox, volume, faces, edges).",
            {"name": {"type": "string", "description": "Object name"}},
            required=["name"],
        ),
        _tool(
            "freecad_delete_object",
            "Delete an object from the document.",
            {"name": {"type": "string", "description": "Object name to delete"}},
            required=["name"],
        ),
        _tool(
            "freecad_undo",
            "Undo the last operation.",
            {},
        ),
        _tool(
            "freecad_redo",
            "Redo the last undone operation.",
            {},
        ),

        # ── Part Primitives ──
        _tool(
            "freecad_create_box",
            "Create a box (cuboid) in the active document.",
            {
                "length": {"type": "number", "description": "Length in X (mm)", "default": 10},
                "width": {"type": "number", "description": "Width in Y (mm)", "default": 10},
                "height": {"type": "number", "description": "Height in Z (mm)", "default": 10},
                "x": {"type": "number", "description": "X position", "default": 0},
                "y": {"type": "number", "description": "Y position", "default": 0},
                "z": {"type": "number", "description": "Z position", "default": 0},
                "name": {"type": "string", "description": "Object name (optional)"},
            },
        ),
        _tool(
            "freecad_create_cylinder",
            "Create a cylinder in the active document.",
            {
                "radius": {"type": "number", "description": "Radius in mm", "default": 5},
                "height": {"type": "number", "description": "Height in mm", "default": 10},
                "x": {"type": "number", "description": "X position", "default": 0},
                "y": {"type": "number", "description": "Y position", "default": 0},
                "z": {"type": "number", "description": "Z position", "default": 0},
                "name": {"type": "string", "description": "Object name (optional)"},
            },
        ),
        _tool(
            "freecad_create_sphere",
            "Create a sphere in the active document.",
            {
                "radius": {"type": "number", "description": "Radius in mm", "default": 5},
                "x": {"type": "number", "description": "X position", "default": 0},
                "y": {"type": "number", "description": "Y position", "default": 0},
                "z": {"type": "number", "description": "Z position", "default": 0},
                "name": {"type": "string", "description": "Object name (optional)"},
            },
        ),
        _tool(
            "freecad_create_cone",
            "Create a cone in the active document.",
            {
                "radius1": {"type": "number", "description": "Bottom radius in mm", "default": 5},
                "radius2": {"type": "number", "description": "Top radius in mm (0 for point)", "default": 0},
                "height": {"type": "number", "description": "Height in mm", "default": 10},
                "x": {"type": "number", "description": "X position", "default": 0},
                "y": {"type": "number", "description": "Y position", "default": 0},
                "z": {"type": "number", "description": "Z position", "default": 0},
                "name": {"type": "string", "description": "Object name (optional)"},
            },
        ),
        _tool(
            "freecad_create_torus",
            "Create a torus (donut) in the active document.",
            {
                "radius1": {"type": "number", "description": "Major radius (center to tube center) in mm", "default": 10},
                "radius2": {"type": "number", "description": "Minor radius (tube thickness) in mm", "default": 3},
                "x": {"type": "number", "description": "X position", "default": 0},
                "y": {"type": "number", "description": "Y position", "default": 0},
                "z": {"type": "number", "description": "Z position", "default": 0},
                "name": {"type": "string", "description": "Object name (optional)"},
            },
        ),

        # ── Boolean Operations ──
        _tool(
            "freecad_fuse_objects",
            "Fuse (union) two or more objects together into one solid.",
            {
                "objects": {"type": "array", "items": {"type": "string"}, "description": "List of object names to fuse"},
                "name": {"type": "string", "description": "Name for the result (optional)"},
            },
            required=["objects"],
        ),
        _tool(
            "freecad_cut_objects",
            "Cut (subtract) tool objects from a base object.",
            {
                "base": {"type": "string", "description": "Base object name"},
                "tools": {"type": "array", "items": {"type": "string"}, "description": "Objects to subtract"},
                "name": {"type": "string", "description": "Name for the result (optional)"},
            },
            required=["base", "tools"],
        ),
        _tool(
            "freecad_common_objects",
            "Find the common (intersection) volume of two or more objects.",
            {
                "objects": {"type": "array", "items": {"type": "string"}, "description": "List of object names"},
                "name": {"type": "string", "description": "Name for the result (optional)"},
            },
            required=["objects"],
        ),

        # ── Transformations ──
        _tool(
            "freecad_move_object",
            "Move (translate) an object.",
            {
                "object_name": {"type": "string", "description": "Object to move"},
                "x": {"type": "number", "description": "X displacement in mm", "default": 0},
                "y": {"type": "number", "description": "Y displacement in mm", "default": 0},
                "z": {"type": "number", "description": "Z displacement in mm", "default": 0},
            },
            required=["object_name"],
        ),
        _tool(
            "freecad_rotate_object",
            "Rotate an object around an axis.",
            {
                "object_name": {"type": "string", "description": "Object to rotate"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "Rotation axis", "default": "z"},
                "angle": {"type": "number", "description": "Angle in degrees", "default": 90},
                "center": {"type": "array", "items": {"type": "number"}, "description": "Rotation center [x,y,z] (default: origin)"},
            },
            required=["object_name"],
        ),
        _tool(
            "freecad_scale_object",
            "Scale an object by a factor.",
            {
                "object_name": {"type": "string", "description": "Object to scale"},
                "factor": {"type": "number", "description": "Scale factor (1.0 = no change)", "default": 1.0},
                "center": {"type": "array", "items": {"type": "number"}, "description": "Scale center [x,y,z] (default: origin)"},
            },
            required=["object_name"],
        ),
        _tool(
            "freecad_mirror_object",
            "Create a mirror copy of an object across a plane.",
            {
                "object_name": {"type": "string", "description": "Object to mirror"},
                "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"], "description": "Mirror plane", "default": "YZ"},
                "name": {"type": "string", "description": "Name for the mirror copy (optional)"},
            },
            required=["object_name"],
        ),
        _tool(
            "freecad_copy_object",
            "Create a copy of an object, optionally offset.",
            {
                "object_name": {"type": "string", "description": "Object to copy"},
                "name": {"type": "string", "description": "Name for the copy (optional)"},
                "x": {"type": "number", "description": "X offset", "default": 0},
                "y": {"type": "number", "description": "Y offset", "default": 0},
                "z": {"type": "number", "description": "Z offset", "default": 0},
            },
            required=["object_name"],
        ),

        # ── Dress-up ──
        _tool(
            "freecad_fillet_object",
            "Apply a fillet to edges of an object. If no edges specified, fillets all edges.",
            {
                "object_name": {"type": "string", "description": "Object to fillet"},
                "radius": {"type": "number", "description": "Fillet radius in mm", "default": 1.0},
                "edges": {"type": "array", "items": {"type": "integer"}, "description": "Edge indices to fillet (optional, all if omitted)"},
                "name": {"type": "string", "description": "Name for the result (optional)"},
            },
            required=["object_name"],
        ),
        _tool(
            "freecad_chamfer_object",
            "Apply a chamfer to edges of an object. If no edges specified, chamfers all edges.",
            {
                "object_name": {"type": "string", "description": "Object to chamfer"},
                "distance": {"type": "number", "description": "Chamfer distance in mm", "default": 1.0},
                "edges": {"type": "array", "items": {"type": "integer"}, "description": "Edge indices to chamfer (optional, all if omitted)"},
                "name": {"type": "string", "description": "Name for the result (optional)"},
            },
            required=["object_name"],
        ),

        # ── Sketching ──
        _tool(
            "freecad_create_sketch",
            "Create a new sketch object.",
            {
                "name": {"type": "string", "description": "Sketch name (optional)"},
                "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"], "description": "Sketch plane", "default": "XY"},
            },
        ),
        _tool(
            "freecad_add_sketch_line",
            "Add a line to a sketch.",
            {
                "sketch_name": {"type": "string", "description": "Sketch object name"},
                "x1": {"type": "number", "description": "Start X", "default": 0},
                "y1": {"type": "number", "description": "Start Y", "default": 0},
                "x2": {"type": "number", "description": "End X", "default": 10},
                "y2": {"type": "number", "description": "End Y", "default": 0},
            },
            required=["sketch_name"],
        ),
        _tool(
            "freecad_add_sketch_circle",
            "Add a circle to a sketch.",
            {
                "sketch_name": {"type": "string", "description": "Sketch object name"},
                "cx": {"type": "number", "description": "Center X", "default": 0},
                "cy": {"type": "number", "description": "Center Y", "default": 0},
                "radius": {"type": "number", "description": "Radius in mm", "default": 5},
            },
            required=["sketch_name"],
        ),
        _tool(
            "freecad_pad_sketch",
            "Pad (extrude) a sketch into a 3D solid.",
            {
                "sketch_name": {"type": "string", "description": "Sketch object name"},
                "length": {"type": "number", "description": "Extrusion length in mm", "default": 10},
                "name": {"type": "string", "description": "Name for the result (optional)"},
            },
            required=["sketch_name"],
        ),

        # ── Export ──
        _tool(
            "freecad_export_stl",
            "Export objects to an STL file (for 3D printing).",
            {
                "path": {"type": "string", "description": "Output file path"},
                "objects": {"type": "array", "items": {"type": "string"}, "description": "Object names (optional, all if omitted)"},
                "tolerance": {"type": "number", "description": "Mesh linear deflection in mm", "default": 0.1},
            },
            required=["path"],
        ),
        _tool(
            "freecad_export_step",
            "Export objects to a STEP file (for CAD exchange).",
            {
                "path": {"type": "string", "description": "Output file path"},
                "objects": {"type": "array", "items": {"type": "string"}, "description": "Object names (optional, all if omitted)"},
            },
            required=["path"],
        ),
        _tool(
            "freecad_export_3mf",
            "Export objects to a 3MF file (for 3D printing).",
            {
                "path": {"type": "string", "description": "Output file path"},
                "objects": {"type": "array", "items": {"type": "string"}, "description": "Object names (optional, all if omitted)"},
            },
            required=["path"],
        ),

        # ── View ──
        _tool(
            "freecad_fit_all",
            "Fit all objects in the FreeCAD viewport.",
            {},
        ),
        _tool(
            "freecad_set_view",
            "Set the viewport to a standard view direction.",
            {
                "view": {
                    "type": "string",
                    "enum": ["isometric", "front", "top", "right", "rear", "bottom", "left", "axono"],
                    "description": "View direction",
                    "default": "isometric",
                },
            },
        ),
        _tool(
            "freecad_screenshot",
            "Capture a screenshot of the FreeCAD viewport and save to a PNG file.",
            {
                "path": {"type": "string", "description": "Output PNG path (optional, uses temp dir if omitted)"},
                "width": {"type": "integer", "description": "Image width in pixels", "default": 1920},
                "height": {"type": "integer", "description": "Image height in pixels", "default": 1080},
            },
        ),

        # ── Advanced ──
        _tool(
            "freecad_run_python",
            "Execute arbitrary Python code in the FreeCAD context. This is the most powerful tool — "
            "use it for complex geometry, parametric models, or anything the specific tools don't cover. "
            "The code has access to FreeCAD, Part, Sketcher, Mesh, MeshPart, os, json, math. "
            "Set a variable named 'result' to return a value.",
            {
                "code": {"type": "string", "description": "Python code to execute in FreeCAD context"},
            },
            required=["code"],
        ),
        _tool(
            "freecad_get_selection",
            "Get the current selection in the FreeCAD GUI (objects the user has selected).",
            {},
        ),
    ]


# ── MCP Handlers ─────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return get_all_tools()


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Handle a tool call from the Hermes Agent."""
    # Strip the 'freecad_' prefix to get the FreeCAD tool name
    fc_tool = name
    if fc_tool.startswith("freecad_"):
        fc_tool = fc_tool[len("freecad_"):]

    # Special case: ping doesn't need FreeCAD
    if fc_tool == "ping":
        response = client.send_command("ping", {}, timeout=5.0)
        if "error" in response:
            return [types.TextContent(type="text", text=json.dumps(response))]
        return [types.TextContent(type="text", text=json.dumps(response.get("result", response)))]

    # Special case: get_status
    if fc_tool == "get_status":
        response = client.send_command("ping", {}, timeout=5.0)
        if "error" in response:
            return [types.TextContent(type="text", text=json.dumps({
                "status": "OFFLINE",
                "detail": response["error"],
            }))]
        return [types.TextContent(type="text", text=json.dumps({
            "status": "ONLINE",
            "host": FREECAD_HOST,
            "port": FREECAD_PORT,
        }))]

    # Forward to FreeCAD
    timeout = 60.0
    if fc_tool in ("run_python",):
        timeout = 120.0  # Long-running scripts

    response = client.send_command(fc_tool, arguments, timeout=timeout)

    # Format the response
    if "error" in response:
        text = json.dumps({"error": response["error"]})
        if "traceback" in response:
            text += "\n\nTraceback:\n" + response["traceback"]
    else:
        result = response.get("result", response)
        text = json.dumps(result, indent=2)

    return [types.TextContent(type="text", text=text)]


# ── Main ─────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
