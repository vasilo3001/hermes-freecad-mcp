# Hermes FreeCAD MCP

Control FreeCAD from **Hermes Agent** via the Model Context Protocol (MCP).

The Hermes Agent drives your FreeCAD GUI in real time — it creates geometry,
runs booleans, sketches with constraints, exports STL/STEP/3MF, and you watch
everything happen live in the FreeCAD 3D viewport.

## Architecture

Two deployment modes:

### Mode 1: Local (stdio)

Hermes spawns `mcp_server.py` per-session via stdio:

```
Hermes Agent
  │ stdio (JSON-RPC 2.0)
  ▼
mcp_server.py  (MCP Server — Python)
  │ TCP 127.0.0.1:9876 (newline JSON)
  ▼
FreeCAD Addon  (runs inside FreeCAD GUI)
```

### Mode 2: Homeserver (HTTP) — recommended for multi-agent

Persistent MCP server in Docker on your homeserver, reachable by multiple
Hermes agents over LAN or Tailscale:

```
Hermes Agent (any machine on network)
  │ HTTP (streamable, JSON-RPC 2.0)
  ▼
mcp_server.py --http  (Docker container on homeserver)
  │ TCP host.docker.internal:9876
  ▼
FreeCAD Addon  (runs inside FreeCAD GUI on homeserver)
```

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| **FreeCAD** | 1.0+ | `apt install freecad` or [freecad.org](https://www.freecad.org/downloads.html) |
| **Python** | 3.10+ | MCP server uses system Python; FreeCAD bundles its own |
| **mcp** | >=2.0.0 | `pip install 'mcp>=2.0.0'` — MCP Python SDK v2 |
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

# Flatpak (e.g. Ubuntu with Flatpak FreeCAD)
mkdir -p ~/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod/
cp -r freecad_addon/hermes_freecad_mcp ~/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod/

# macOS
mkdir -p ~/Library/"Application Support"/FreeCAD/Mod/
cp -r freecad_addon/hermes_freecad_mcp ~/Library/"Application Support"/FreeCAD/Mod/
```

### 3. Install the MCP Server dependency

```bash
pip install 'mcp>=2.0.0'
```

### 4. Configure Hermes Agent

**Option A: Local stdio** (simplest, same machine as FreeCAD)

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  freecad:
    command: "python3"
    args: ["/absolute/path/to/mcp_server.py"]
    timeout: 120
```

**Option B: Homeserver HTTP** (multi-agent, persistent)

Deploy the Portainer stack (`docker-compose.yml`) on your homeserver, then
add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  freecad:
    url: "http://192.168.1.184:3111/mcp"
    timeout: 120
```

Or via Tailscale (from other machines on the network):

```yaml
mcp_servers:
  freecad:
    url: "http://<tailscale-ip>:3111/mcp"
    timeout: 120
```

**Portainer stack** (`docker-compose.yml`):

```yaml
services:
  freecadmcp:
    image: freecad-mcp:latest
    container_name: freecadmcp
    restart: unless-stopped
    ports:
      - "3111:8000"
    environment:
      - FREECAD_HOST=host.docker.internal
      - FREECAD_PORT=9876
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - homeserver_homeserver-net
networks:
  homeserver_homeserver-net:
    external: true
```

Build the image first:

```bash
docker build -t freecad-mcp .
```

### 5. Start FreeCAD and Activate the Bridge

1. Launch FreeCAD
2. Switch to the **Hermes MCP** workbench (workbench dropdown, top-right)
3. The bridge **auto-starts** on workbench activation. You should see in the FreeCAD console:
   ```
   [HermesFreeCADMCP] Bridge started on port 9876. Hermes Agent can now connect.
   ```

### 6. Restart Hermes Agent

Restart Hermes so it picks up the new MCP server. On startup it will:
- Connect to `mcp_server.py` (stdio or HTTP)
- Discover all 42 FreeCAD tools
- Register them as `mcp_freecad_*` tools

## Usage

Just talk to Hermes naturally:

```
You:  Create a box 50mm x 30mm x 5mm at the origin
Hermes: [calls freecad_create_box]
        → Box created: 50×30×5mm at (0,0,0)

You:  Add a 4mm hole in the center of the top face
Hermes: [creates cylinder, cuts from box]
        → Hole drilled at center

You:  Fillet all edges with 1mm radius
Hermes: [calls freecad_fillet_object]
        → All edges filleted

You:  Export to STL for printing
Hermes: [calls freecad_export_stl]
        → Saved to ~/Documents/bracket.stl (12,450 facets)
```

### Sketching with Constraints

```
You:  Create a sketch on the XY plane
Hermes: [calls freecad_create_sketch]

You:  Add a line from (0,0) to (100,0)
Hermes: [calls freecad_add_sketch_line]

You:  Constrain it to be horizontal and 100mm long
Hermes: [calls freecad_add_constraint x2]
        → Constraint count: 2, DoF: 2 (under-constrained)

You:  Lock the start point to the origin
Hermes: [calls freecad_add_constraint coincident]
        → Constraint count: 3, DoF: 0 (FULLY CONSTRAINED — green)
```

For complex geometry, Hermes can write and execute arbitrary Python:

```
You:  Model a 50mm hex bolt with M6 thread, 20mm long
Hermes: [writes FreeCAD Python script, calls freecad_run_python]
        → Hex bolt created with 6mm across-flats, 20mm length
```

## Available Tools (42)

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
| `freecad_create_sketch` | New sketch on a plane |
| `freecad_add_sketch_line` | Add line to sketch |
| `freecad_add_sketch_circle` | Add circle to sketch |
| `freecad_add_sketch_arc` | Add arc to sketch |
| `freecad_add_constraint` | Add constraint (coincident, distance, horizontal, etc.) |
| `freecad_get_sketch_info` | Geometry, constraints, DoF, fully-constrained status |
| `freecad_solve_sketch` | Force solver run, report DoF |
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
├── mcp_server.py                ← MCP server (stdio + HTTP transports)
├── freecad_addon/
│   └── hermes_freecad_mcp/      ← FreeCAD addon (copy to Mod dir)
│       ├── Init.py              ← Addon entry point
│       ├── InitGui.py           ← Workbench (bridge auto-starts on activation)
│       └── mcp_bridge.py        ← TCP server + thread-safe command dispatcher
├── examples/
│   └── drone_mount_bracket.py   ← Example: FPV drone camera mount
├── skills/
│   └── freecad-modeling/
│       └── SKILL.md             ← Hermes skill (workflow guide)
├── test_bridge.py               ← Direct TCP test (run against live bridge)
├── install.sh                   ← One-command installer
├── Dockerfile                   ← Docker image (HTTP mode)
├── docker-compose.yml           ← Portainer stack for homeserver deployment
├── .dockerignore
├── plugin.json                  ← Hermes Portable Agent Plugin manifest
├── mcp.json                     ← MCP server config (HTTP URL)
└── requirements.txt             ← Python dependencies
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Cannot connect to FreeCAD" | Is FreeCAD running? Is the bridge started? Check FreeCAD console. |
| "mcp package not installed" | `pip install 'mcp>=2.0.0'` |
| Tools not appearing in Hermes | Restart Hermes after config change. Check config syntax. |
| FreeCAD addon not showing | Check Mod directory exists. Restart FreeCAD. |
| Port 9876 already in use | Change `MCP_BRIDGE_PORT` in `mcp_bridge.py` and `FREECAD_PORT` env var. |
| Commands time out | Complex geometry can take time. Increase `timeout` in config. |
| HTTP mode: connection refused | Check Docker container is running: `docker ps`. Check port mapping. |
| HTTP mode: can't reach FreeCAD | Check `FREECAD_HOST` env var. On Linux, `extra_hosts` must include `host.docker.internal:host-gateway`. |

## Roadmap

- [x] Docker containerized MCP server (HTTP streamable)
- [x] Sketcher constraint solving (coincident, distance, horizontal, vertical, etc.)
- [ ] PartDesign workbench integration (pads, pockets, holes with proper constraints)
- [ ] TechDraw automated drawings
- [ ] FEM analysis hooks
- [ ] Multi-document support
- [ ] Windows support (addon Mod path + install script)

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
