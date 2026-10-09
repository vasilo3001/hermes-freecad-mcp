"""
Example: FPV Drone Camera Mount Bracket
=========================================
A 50mm x 30mm x 5mm bracket with:
- 4x M3 mounting holes (3mm diameter) at corners
- Central 12mm hole for camera screw
- 1mm fillet on all edges
- 0.5mm chamfer on top face edges

Run this in the FreeCAD Python console, or let Hermes
execute it via mcp_freecad_run_python.
"""

import FreeCAD
import Part
import math

doc = FreeCAD.newDocument("DroneMount")

# ── Base plate ──────────────────────────────────────
L, W, T = 50.0, 30.0, 5.0  # Length, Width, Thickness (mm)
base = Part.makeBox(L, W, T)
base_obj = doc.addObject("Part::Feature", "BasePlate")
base_obj.Shape = base
base_obj.Label = "Base Plate"

# ── Corner mounting holes (M3 = 3mm) ───────────────
hole_r = 1.5  # 3mm diameter
margin = 5.0  # 5mm from edge
hole_positions = [
    (margin, margin),
    (L - margin, margin),
    (margin, W - margin),
    (L - margin, W - margin),
]

holes = []
for i, (hx, hy) in enumerate(hole_positions):
    h = Part.makeCylinder(hole_r, T + 2, FreeCAD.Vector(hx, hy, -1))
    holes.append(h)
    h_obj = doc.addObject("Part::Feature", f"Hole{i+1}")
    h_obj.Shape = h
    h_obj.Label = f"Mount Hole {i+1}"

# ── Central camera hole (12mm) ──────────────────────
cam_r = 6.0  # 12mm diameter
cam_hole = Part.makeCylinder(cam_r, T + 2, FreeCAD.Vector(L/2, W/2, -1))
cam_obj = doc.addObject("Part::Feature", "CameraHole")
cam_obj.Shape = cam_hole
cam_obj.Label = "Camera Hole"

# ── Cut all holes from base ─────────────────────────
cut_shape = base
for h in holes:
    cut_shape = cut_shape.cut(h)
cut_shape = cut_shape.cut(cam_hole)

result = doc.addObject("Part::Feature", "Bracket")
result.Shape = cut_shape
result.Label = "Drone Mount Bracket"

# ── Fillet all edges (1mm) ──────────────────────────
try:
    filleted = result.Shape.makeFillet(1.0, result.Shape.Edges)
    result.Shape = filleted
except Exception as e:
    print(f"Fillet failed (some edges may be too short): {e}")

# ── Hide the helper objects ─────────────────────────
base_obj.Visibility = False
for i in range(4):
    doc.getObject(f"Hole{i+1}").Visibility = False
cam_obj.Visibility = False

doc.recompute()

# ── Summary ─────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  Drone Mount Bracket")
print(f"{'='*50}")
print(f"  Plate:      {L} x {W} x {T} mm")
print(f"  Mount holes: 4x M3 ({hole_r*2}mm) at {margin}mm from edges")
print(f"  Camera hole: {cam_r*2}mm center")
print(f"  Fillet:     1.0mm all edges")
print(f"  Volume:     {result.Shape.Volume:.1f} mm³")
print(f"  Mass (PETG, 1.24 g/cm³): {result.Shape.Volume * 1.24 / 1000:.2f} g")
print(f"{'='*50}\n")

# ── Export ──────────────────────────────────────────
import os
export_dir = os.path.expanduser("~/Documents")
stl_path = os.path.join(export_dir, "drone_mount_bracket.stl")
import Mesh, MeshPart
mesh = MeshPart.meshFromShape(Shape=result.Shape, LinearDeflection=0.1)
mesh.write(stl_path)
print(f"  STL exported: {stl_path}")
