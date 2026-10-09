---
name: freecad-modeling
description: Model 3D parts in FreeCAD via MCP — primitives, booleans, sketches, dress-up, export. Use when the user asks to create, modify, or export CAD geometry in FreeCAD.
version: 1.0.0
metadata:
  hermes:
    tags: [freecad, cad, 3d-modeling, mcp]
    category: engineering
    related_skills: []
---

# FreeCAD Modeling via MCP

Drive FreeCAD through the `mcp_freecad_*` tools. The FreeCAD GUI must be
running with the **Hermes MCP** workbench bridge started (TCP port 9876).

## Prerequisites

1. FreeCAD is running (GUI mode)
2. Hermes MCP workbench is active and **Start Bridge** was clicked
3. `mcp_freecad_ping` returns `true`

If ping fails, tell the user to check:
- FreeCAD is open
- Hermes MCP workbench → Start Bridge
- Port 9876 is not blocked

## Workflow

### 1. Start a document

```
mcp_freecad_new_document(name="MyPart")
```

### 2. Build geometry

Use primitives for simple shapes, `run_python` for complex geometry:

**Primitives** (fast, one call each):
- `mcp_freecad_create_box(l, w, h, x, y, z)` → returns object name
- `mcp_freecad_create_cylinder(r, h, x, y, z)` → axis along Z by default
- `mcp_freecad_create_sphere(r, x, y, z)`
- `mcp_freecad_create_cone(r1, r2, h, x, y, z)`
- `mcp_freecad_create_torus(r1, r2, x, y, z)`

**Complex geometry** (write FreeCAD Python, execute in FreeCAD context):
- `mcp_freecad_run_python(code="...")` — full FreeCAD/Part API access
- Use `Part.makeBox()`, `Part.makeCylinder()`, `Part.makeSphere()`, etc.
- Boolean ops: `shape.cut(other)`, `shape.fuse(other)`, `shape.common(other)`
- Always call `doc.recompute()` after adding objects

### 3. Boolean operations

| Operation | Tool |
|-----------|------|
| Union (add) | `mcp_freecad_fuse_objects(obj1, obj2)` |
| Subtract (cut) | `mcp_freecad_cut_objects(base, tool)` |
| Intersect | `mcp_freecad_common_objects(obj1, obj2)` |

### 4. Transform

- `mcp_freecad_move_object(obj, dx, dy, dz)` — translate
- `mcp_freecad_rotate_object(obj, angle, axis, center)` — rotate (degrees)
- `mcp_freecad_scale_object(obj, sx, sy, sz)` — scale
- `mcp_freecad_mirror_object(obj, plane)` — mirror

### 5. Dress-up

- `mcp_freecad_fillet_object(obj, radius)` — fillet all edges
- `mcp_freecad_fillet_object(obj, radius, edges=[0,2,5])` — specific edges (0-indexed)
- `mcp_freecad_chamfer_object(obj, size)` — chamfer all edges

### 6. Sketch (for PartDesign)

- `mcp_freecad_create_sketch(obj, plane)` — new sketch on a face
- `mcp_freecad_add_sketch_line(sketch, x1, y1, x2, y2)`
- `mcp_freecad_add_sketch_circle(sketch, cx, cy, r)`
- `mcp_freecad_pad_sketch(sketch, depth)` — extrude to solid

### 7. Export

- `mcp_freecad_export_stl(obj, path)` — for 3D printing
- `mcp_freecad_export_step(obj, path)` — for CAD exchange
- `mcp_freecad_export_3mf(obj, path)` — for 3D printing (preferred over STL)

### 8. View

- `mcp_freecad_fit_all()` — fit viewport
- `mcp_freecad_set_view("iso"|"front"|"top"|"right"|"rear"|"bottom"|"left")`
- `mcp_freecad_screenshot(path)` — capture viewport as PNG

## Design Rules (3D Printing)

When modeling for FDM printing (user has Prusa CORE One HF):

- **Minimum wall thickness**: 1.2mm structural, 0.8mm non-critical
- **Clearance**: +0.2mm for sliding fits, -0.1mm for press fits
- **Fillet over sharp corners**: reduces stress concentration, prints better
- **Draft angles**: 2-3° on vertical walls for easier removal
- **Overhangs**: <45° from horizontal is self-supporting; above needs supports
- **Hole sizes**: add 0.2-0.4mm to nominal for clean drilling
- **Orientation**: strongest axis = Z (layer adhesion is weakest)

## Material Reference (user's printer)

| Material | Nozzle | Bed | Fan | Notes |
|----------|--------|-----|-----|-------|
| PLA | 210-220°C | 60°C | 100% | Prototypes, decorative |
| PETG | 230-250°C | 80°C | 50-70% | Functional, outdoor |
| ABS-CF | 260°C | 110°C | 0% | FPV frames, high temp |
| TPU-95A | 225°C | 55°C | 100% | Flexible, bumpers |

## Common Patterns

### Bracket with holes
```python
import Part, FreeCAD
doc = FreeCAD.activeDocument()
base = Part.makeBox(50, 30, 5)
holes = [Part.makeCylinder(1.5, 7, FreeCAD.Vector(x, y, -1))
         for x, y in [(5,5), (45,5), (5,25), (45,25)]]
result = base
for h in holes:
    result = result.cut(h)
obj = doc.addObject("Part::Feature", "Bracket")
obj.Shape = result
doc.recompute()
```

### Hex bolt
```python
import Part, FreeCAD, math
doc = FreeCAD.activeDocument()
# Hex head (across flats = 6mm → circumscribed radius)
af = 6.0
r_head = af / math.sqrt(3) * 2  # ≈ 6.93mm
head = Part.makeCylinder(r_head, 5.0)
# Shaft (M6 = 6mm diameter)
shaft = Part.makeCylinder(3.0, 20.0, FreeCAD.Vector(0, 0, 5))
bolt = head.fuse(shaft)
obj = doc.addObject("Part::Feature", "Bolt")
obj.Shape = bolt
doc.recompute()
```

## Error Handling

- If a tool returns an error, check `mcp_freecad_ping` first
- Complex geometry may time out — increase timeout in config
- Fillet/chamfer can fail on short edges — catch and report
- Always `doc.recompute()` after adding/modifying objects
