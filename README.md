# Hermes FreeCAD MCP

Control FreeCAD from **Hermes Agent** via the Model Context Protocol (MCP).

The Hermes Agent drives your FreeCAD GUI in real time — it creates geometry,
runs booleans, sketches, exports STL/STEP/3MF, and you watch everything happen
live in the FreeCAD 3D viewport.

```
┌──────────────────────────────────────────────────────────────┐
│  Hermes Agent (desktop app / CLI)                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Built-in MCP Client (reads ~/.hermes/config.yaml)     │  │
│  └───────────────────┬────────────────────────────────────┘  │
│                      │ stdio (JSON-RPC 2.0)                  │
│  ┌───────────────────▼────────────────────────────────────┐  │
│  │  mcp_server.py  (MCP Server — Python)                  │  │
│  │  • Registers 35 tools for Hermes                       │  │
│  │  • Forwards commands to FreeCAD addon                  │  │
│  └───────────────────┬────────────────────────────────────┘  │
│                      │ TCP localhost:9876 (newline JSON)     │
│  ┌───────────────────▼────────────────────────────────────┐  │
│  │  FreeCAD Addon  (runs inside FreeCAD GUI)              │  │
│  │  • TCP server on 127.0.0.1:9876                        │  │
│  │  • Executes Python in FreeCAD's own interpreter        │  │
│  │  • Part, PartDesign, Sketcher, Mesh, TechDraw          │  │
│  │  • User sees everything live in the 3D viewport        │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| **FreeCAD** | 1.0+ | `apt install freecad` (Ubuntu/Debian) or download from [freecad.org](https://www.freecad.org/downloads.html) |
| **Python** | 3.10+ | FreeCAD bundles its own Python; the MCP server uses your system Python |
| **mcp** | 1.0+ | `pip install mcp` — MCP Python SDK |
| **Hermes Agent** | latest | With native MCP client support |

## Installation

### 1. Install FreeCAD

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install freecad

# Or download from https://www.freecad.org/downloads.html
```

### 2. Install the FreeCAD Addon

Copy the addon folder into your FreeCAD Mod directory:

```bash
# Linux
mkdir -p ~/.local/share/FreeCAD/Mod/
cp -r freecad_addon/hermes_freecad_mcp ~/.local/share/FreeCAD/Mod/

# macOS
mkdir -p ~/Library/"Application Support"/FreeCAD/Mod/
cp -r freecad_addon/hermes_freecad_mcp ~/Library/"Application Support"/FreeCAD/Mod/
```

### 3. Install the MCP Server dependency

```bash
pip install mcp
```

### 4. Configure Hermes Agent

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  freecad:
    command: "python3"
    args: ["/absolute/path/to/mcp_server.py"]
    timeout: 120
```

Replace `/absolute/path/to/mcp_server.py` with the actual path.

### 5. Start FreeCAD and Activate the Bridge

1. Launch FreeCAD
2. Switch to the **Hermes MCP** workbench (workbench dropdown, top-right)
3. Click **Start Bridge** in the toolbar
4. You should see in the FreeCAD console:
   ```
   [HermesFreeCADMCP] Bridge started on port 9876. Hermes Agent can now connect.
   ```

### 6. Restart Hermes Agent

Restart Hermes so it picks up the new MCP server. On startup it will:
- Connect to `mcp_server.py` via stdio
- Discover all 35 FreeCAD tools
- Register them as `mcp_freecad_*` tools

## Usage

Just talk to Hermes naturally:

```
You:  Create a box 50mm x 30mm x 5mm at the origin
Hermes: [calls mcp_freecad_create_box]
        → Box created: 50×30×5mm at (0,0,0)

You:  Add a 4mm hole in the center of the top face
Hermes: [creates cylinder, cuts from box]
        → Hole drilled at center

You:  Fillet all edges with 1mm radius
Hermes: [calls mcp_freecad_fillet_object]
        → All edges filleted

You:  Export to STL for printing
Hermes: [calls mcp_freecad_export_stl]
        → Saved to ~/Documents/bracket.stl (12,450 facets)
```

For complex geometry, Hermes can write and execute arbitrary Python:

```
You:  Model a 50mm hex bolt with M6 thread, 20mm long
Hermes: [writes FreeCAD Python script, calls mcp_freecad_run_python]
        → Hex bolt created with 6mm across-flats, 20mm length
```

## Available Tools (35)

### Connection
| Tool | Description |
|------|-------------|
| `freecad_ping` | Check if FreeCAD bridge is running |
| `freecad_get_status` | Get addon status |

### Document
| Tool | Description |
|------|-------------|
| `freecad_get_document_info` | Active document info |
| `freecad_new_document` | Create new document |
| `freecad_save_document` | Save document |
| `freecad_open_document` | Open .FCStd file |
| `freecad_list_objects` | List all objects |
| `freecad_get_object_info` | Object details (bbox, volume, faces) |
| `freecad_delete_object` | Delete object |
| `freecad_undo` / `freecad_redo` | Undo/redo |

### Part Primitives
| Tool | Description |
|------|-------------|
| `freecad_create_box` | Box (cuboid) |
| `freecad_create_cylinder` | Cylinder |
| `freecad_create_sphere` | Sphere |
| `freecad_create_cone` | Cone |
| `freecad_create_torus` | Torus (donut) |

### Boolean Operations
| Tool | Description |
|------|-------------|
| `freecad_fuse_objects` | Union |
| `freecad_cut_objects` | Subtract |
| `freecad_common_objects` | Intersection |

### Transformations
| Tool | Description |
|------|-------------|
| `freecad_move_object` | Translate |
| `freecad_rotate_object` | Rotate around axis |
| `freecad_scale_object` | Scale |
| `freecad_mirror_object` | Mirror across plane |
| `freecad_copy_object` | Copy with offset |

### Dress-up
| Tool | Description |
|------|-------------|
| `freecad_fillet_object` | Fillet edges |
| `freecad_chamfer_object` | Chamfer edges |

### Sketching
| Tool | Description |
|------|-------------|
| `freecad_create_sketch` | New sketch |
| `freecad_add_sketch_line` | Add line to sketch |
| `freecad_add_sketch_circle` | Add circle to sketch |
| `freecad_pad_sketch` | Extrude sketch to solid |

### Export
| Tool | Description |
|------|-------------|
| `freecad_export_stl` | Export STL (3D printing) |
| `freecad_export_step` | Export STEP (CAD exchange) |
| `freecad_export_3mf` | Export 3MF (3D printing) |

### View
| Tool | Description |
|------|-------------|
| `freecad_fit_all` | Fit viewport |
| `freecad_set_view` | Set view direction (iso, front, top, etc.) |
| `freecad_screenshot` | Capture viewport as PNG |

### Advanced
| Tool | Description |
|------|-------------|
| `freecad_run_python` | Execute arbitrary Python in FreeCAD context |
| `freecad_get_selection` | Get current GUI selection |

## Project Structure

```
freecadMCP vibecoding/
├── README.md                    ← This file
├── mcp_server.py                ← MCP server (Hermes → FreeCAD bridge)
├── freecad_addon/
│   └── hermes_freecad_mcp/      ← FreeCAD addon (copy to Mod dir)
│       ├── Init.py              ← Addon entry point
│       ├── InitGui.py           ← Workbench + toolbar commands
│       └── mcp_bridge.py        ← TCP server + command dispatcher
├── examples/
│   ├── drone_mount_bracket.py   ← Example: FPV drone camera mount
│   └── test_basic.py            ← Smoke test
├── install.sh                   ← One-command installer (see below)
└── requirements.txt             ← Python dependencies
```

### What `install.sh` does

```
1. Detects OS (Linux / macOS)
2. Finds FreeCAD:
   • System install (freecad in PATH)
   • Flatpak (flatpak list | grep freecad)
   • Data dir (~/.local/share/FreeCAD)
3. Checks Python 3 + installs 'mcp' pip package if missing
4. Copies freecad_addon/hermes_freecad_mcp/ → FreeCAD Mod directory:
   • Linux:   ~/.local/share/FreeCAD/Mod/
   • macOS:   ~/Library/Application Support/FreeCAD/Mod/
5. Prints the Hermes config.yaml snippet to add
```

It does NOT modify `config.yaml` automatically — it prints the snippet
so you can add it yourself (or paste it). This keeps the installer
non-destructive.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Cannot connect to FreeCAD" | Is FreeCAD running? Is the bridge started? Check FreeCAD console. |
| "mcp package not installed" | `pip install mcp` |
| Tools not appearing in Hermes | Restart Hermes after config change. Check `~/.hermes/config.yaml` syntax. |
| FreeCAD addon not showing | Check `~/.local/share/FreeCAD/Mod/hermes_freecad_mcp/` exists. Restart FreeCAD. |
| Port 9876 already in use | Change `MCP_BRIDGE_PORT` in `mcp_bridge.py` and `FREECAD_PORT` env var. |
| Commands time out | Complex geometry can take time. Increase `timeout` in config.yaml. |

## Roadmap

- [ ] Docker containerized FreeCAD (headless with VNC)
- [ ] PartDesign workbench integration (pads, pockets, holes with proper constraints)
- [ ] Sketcher constraint solving
- [ ] TechDraw automated drawings
- [ ] FEM analysis hooks
- [ ] Multi-document support
- [ ] Windows support (addon Mod path + install script)
- [ ] WebSocket transport (for remote FreeCAD over LAN)

## Credits

- Architecture inspired by [contextform/freecad-mcp](https://github.com/contextform/freecad-mcp)
- MCP protocol by [Model Context Protocol](https://modelcontextprotocol.io/)
- FreeCAD by the [FreeCAD community](https://www.freecad.org/)

## License

**MIT** — see [LICENSE](LICENSE).

This project is Free and Open Source Software (FOSS). The entire codebase —
FreeCAD addon, MCP server, examples, installer — is licensed under the
MIT License. You are free to use, modify, and share this software.
The only requirement is that the copyright notice is preserved.
