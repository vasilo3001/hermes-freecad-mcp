"""
Smoke test: verify the MCP bridge is working
=============================================
Run this in the FreeCAD Python console after starting the bridge.
Or let Hermes execute it via mcp_freecad_run_python.

Tests:
  1. Create a box
  2. Create a cylinder
  3. Cut cylinder from box
  4. Verify volume
  5. Export STL
  6. Clean up
"""

import FreeCAD
import Part
import os
import tempfile

print("=" * 50)
print("  Hermes FreeCAD MCP — Smoke Test")
print("=" * 50)

# ── 1. New document ─────────────────────────────────
doc = FreeCAD.newDocument("SmokeTest")
print("[1] Document created: %s" % doc.Name)

# ── 2. Create box ───────────────────────────────────
box = Part.makeBox(50, 30, 10)
box_obj = doc.addObject("Part::Feature", "TestBox")
box_obj.Shape = box
doc.recompute()
print("[2] Box created: 50x30x10mm, volume=%.1f mm³" % box_obj.Shape.Volume)

# ── 3. Create cylinder ──────────────────────────────
cyl = Part.makeCylinder(5, 12, FreeCAD.Vector(25, 15, -1))
cyl_obj = doc.addObject("Part::Feature", "TestCyl")
cyl_obj.Shape = cyl
doc.recompute()
print("[3] Cylinder created: r=5mm h=12mm, volume=%.1f mm³" % cyl_obj.Shape.Volume)

# ── 4. Cut ──────────────────────────────────────────
cut_shape = box_obj.Shape.cut(cyl_obj.Shape)
cut_obj = doc.addObject("Part::Feature", "TestCut")
cut_obj.Shape = cut_shape
doc.recompute()
print("[4] Cut result: volume=%.1f mm³" % cut_obj.Shape.Volume)

expected = box_obj.Shape.Volume - cyl_obj.Shape.Volume
# Volume won't be exactly box - cyl because the cylinder extends beyond
# the box (starts at z=-1, ends at z=11, box is z=0..10)
# The cut removes the intersection, which is a 10mm-tall cylinder
intersection_vol = Part.makeCylinder(5, 10).Volume
expected = box_obj.Shape.Volume - intersection_vol
actual = cut_obj.Shape.Volume
diff = abs(actual - expected)
if diff < 1.0:
    print("    ✓ Volume check passed (diff=%.3f mm³)" % diff)
else:
    print("    ✗ Volume check FAILED (expected=%.1f, got=%.1f)" % (expected, actual))

# ── 5. Export STL ───────────────────────────────────
stl_path = os.path.join(tempfile.gettempdir(), "smoke_test.stl")
import Mesh, MeshPart
mesh = MeshPart.meshFromShape(Shape=cut_obj.Shape, LinearDeflection=0.1)
mesh.write(stl_path)
print("[5] STL exported: %s (%d facets)" % (stl_path, mesh.CountFacets))

# ── 6. Cleanup ──────────────────────────────────────
doc.removeObject("TestCut")
doc.removeObject("TestCyl")
doc.removeObject("TestBox")
doc.recompute()
FreeCAD.closeDocument(doc.Name)
print("[6] Cleanup done")

print("=" * 50)
print("  Smoke test complete")
print("=" * 50)
