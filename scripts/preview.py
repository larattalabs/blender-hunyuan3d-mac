"""Headless turntable-ish preview of a GLB, so an agent can SEE the mesh it made.

    /Applications/Blender.app/Contents/MacOS/Blender -b --python preview.py -- model.glb preview.png

Prints IMPORTED with vertex counts, writes a 640x640 EEVEE render on a flat background.
"""
import bpy, sys, math, mathutils
glb, out = sys.argv[-2], sys.argv[-1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
objs=[o for o in bpy.context.scene.objects if o.type=='MESH']
print("IMPORTED:", [(o.name, len(o.data.vertices)) for o in objs])
# frame the mesh
bb=[o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
lo=mathutils.Vector((min(v.x for v in bb),min(v.y for v in bb),min(v.z for v in bb)))
hi=mathutils.Vector((max(v.x for v in bb),max(v.y for v in bb),max(v.z for v in bb)))
ctr=(lo+hi)/2; size=max((hi-lo))
cam_data=bpy.data.cameras.new("cam"); cam=bpy.data.objects.new("cam",cam_data)
bpy.context.scene.collection.objects.link(cam); bpy.context.scene.camera=cam
cam.location = ctr + mathutils.Vector((1.6,-2.0,1.3))*size
cam.rotation_euler = (mathutils.Vector(ctr)-cam.location).to_track_quat('-Z','Y').to_euler()
light=bpy.data.objects.new("l", bpy.data.lights.new("l", type='SUN')); light.data.energy=4
bpy.context.scene.collection.objects.link(light); light.rotation_euler=(math.radians(50),0,math.radians(40))
# Neutral studio grey, not a sky: a blue sky tints every asset (and mirrors off smooth surfaces),
# which makes it useless for judging whether a texture is faithful. Grey still gives metal and
# roughness something to reflect.
w=bpy.data.worlds.new("w"); w.use_nodes=True
nt=w.node_tree; bg=nt.nodes["Background"]
bg.inputs[0].default_value=(0.55,0.55,0.55,1); bg.inputs[1].default_value=1.0
bpy.context.scene.world=w

# Report what the material actually carries — the quick way to tell RGB from PBR output.
for o in objs:
    for m in o.data.materials:
        if not m.use_nodes: continue
        maps=[]
        for n in m.node_tree.nodes:
            if n.type=="TEX_IMAGE" and n.image:
                links=[l.to_socket.name for l in m.node_tree.links if l.from_node is n]
                maps.append(f"{n.image.name}{tuple(n.image.size)}->{'/'.join(links) or '?'}")
        print("MATERIAL:", m.name, "|", "; ".join(maps) or "no image maps")
sc=bpy.context.scene; sc.render.engine='BLENDER_EEVEE'
sc.render.resolution_x=640; sc.render.resolution_y=640; sc.render.filepath=out
sc.eevee.taa_render_samples=16
bpy.ops.render.render(write_still=True)
print("RENDERED", out)
