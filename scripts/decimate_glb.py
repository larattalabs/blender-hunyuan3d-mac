"""Decimate a GLB to a face budget, in place of the original file.

    blender -b --python decimate_glb.py -- in.glb out.glb 1500000

Used by the bridge to cap shape output before painting. hy3d paint unwraps with xatlas and bakes
at 4096²; a 4-million-face canopy (what octree 512 produces for a bushy tree) kills it, while the
paint result is no better than from a 1.2M-face version — the texture is what carries the detail.
"""

import sys

import bpy

src, dst, budget = sys.argv[-3], sys.argv[-2], int(sys.argv[-1])

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh in the input file")

ob = meshes[0]
bpy.ops.object.select_all(action="DESELECT")
for m in meshes:
    m.select_set(True)
bpy.context.view_layer.objects.active = ob
if len(meshes) > 1:
    bpy.ops.object.join()

before = len(ob.data.polygons)
if before > budget:
    m = ob.modifiers.new("dec", "DECIMATE")
    m.ratio = budget / before
    bpy.ops.object.modifier_apply(modifier=m.name)

print(f"DECIMATE_RESULT before={before} after={len(ob.data.polygons)}")
bpy.ops.object.select_all(action="DESELECT")
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True)
