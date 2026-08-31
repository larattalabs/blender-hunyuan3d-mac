"""Turn a generated GLB into a game-ready asset: clean topology + baked maps.

    blender -b --python gameify.py -- in.glb out.glb [--tris 15000] [--tex 2048]
                                      [--mode decimate|remesh] [--voxel auto|0.006]
                                      [--no-normal] [--no-ao] [--keep-source]

Two paths:
  decimate (default) — weld the UV-seam splits, then decimate to budget. Keeps the original
      texture and UVs, hits the budget exactly, and preserves thin structures. Use this.
  remesh            — voxel remesh, then re-bake albedo/AO/normal onto fresh UVs. Uniform
      topology and a watertight result, at the cost of re-projected textures and rounded-off
      thin features. Reach for it when the welded mesh still will not decimate.

Generated meshes are raw isosurfaces — a lantern came out at 1,006,732 tris in 47,870
disconnected islands with 27% of its edges non-manifold. Decimating that directly shreds the
UVs and stalls well above any game budget (a 15k request stopped at 20,579). So instead:

  voxel remesh  ->  triangulate + decimate to budget  ->  smart UV unwrap
                ->  bake albedo (+ tangent-space normal) from the original dense mesh

The dense mesh is the bake source, not the deliverable: its detail survives in the normal map.
That is also why generating at low quality to "get a smaller model" is the wrong move — it
throws away the detail you would have baked.

Caveat: voxel remeshing rounds off genuinely thin features. Hero assets still want hand retopo.

Two bake details that cost an afternoon, both verified on Blender 5.2:
  * type='DIFFUSE' with pass_filter={'COLOR'} bakes BLACK from a glTF-imported image-texture
    material (a flat colour on the same source bakes fine). Rewiring the source's base colour
    into an Emission shader and baking EMIT transfers it exactly — and is the standard route.
  * The bake destination must be ONE image-texture node whose `.image` is swapped between
    passes. Creating a second node for the second pass made object.bake return CANCELLED with
    no report at all.
"""

import os
import sys
import time

import bmesh
import bpy
import numpy as np


def parse_args(argv):
    a = argv[argv.index("--") + 1:] if "--" in argv else argv
    if len(a) < 2:
        raise SystemExit(__doc__)
    opts = {"src": a[0], "dst": a[1], "tris": 15000, "tex": 2048, "mode": "decimate",
            "voxel": "auto", "normal": True, "ao": True, "keep_source": False}
    i = 2
    while i < len(a):
        k = a[i]
        if k == "--tris": i += 1; opts["tris"] = int(a[i])
        elif k == "--tex": i += 1; opts["tex"] = int(a[i])
        elif k == "--mode": i += 1; opts["mode"] = a[i]
        elif k == "--voxel": i += 1; opts["voxel"] = a[i]
        elif k == "--no-normal": opts["normal"] = False
        elif k == "--no-ao": opts["ao"] = False
        elif k == "--keep-source": opts["keep_source"] = True
        else: raise SystemExit(f"unknown option {k}")
        i += 1
    return opts


def log(msg):
    print(f"[gameify] {msg}", flush=True)


def mesh_stats(ob):
    """(tris, islands, non-manifold edges) — the numbers that decide if a mesh is usable."""
    bm = bmesh.new(); bm.from_mesh(ob.data)
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    seen = set(); islands = 0
    for f in bm.faces:
        if f.index in seen:
            continue
        islands += 1; stack = [f]; seen.add(f.index)
        while stack:
            c = stack.pop()
            for e in c.edges:
                for lf in e.link_faces:
                    if lf.index not in seen:
                        seen.add(lf.index); stack.append(lf)
    n = len(bm.faces); bm.free()
    return n, islands, nm


def emissive_albedo(source):
    """Make each source material emit its base colour, so an EMIT bake transfers it exactly."""
    for mat in source.data.materials:
        if not mat:
            continue
        nt = mat.node_tree
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if out is None or bsdf is None:
            continue
        emit = nt.nodes.new("ShaderNodeEmission")
        base = bsdf.inputs["Base Color"]
        if base.links:
            nt.links.new(base.links[0].from_socket, emit.inputs["Color"])
        else:
            emit.inputs["Color"].default_value = base.default_value
        nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])


def main():
    o = parse_args(sys.argv)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=o["src"])

    meshes = [x for x in bpy.context.scene.objects if x.type == "MESH"]
    if not meshes:
        raise SystemExit("no mesh in the input file")
    source = meshes[0]
    if len(meshes) > 1:                    # generated files are single-mesh; join defensively
        bpy.ops.object.select_all(action="DESELECT")
        for m in meshes:
            m.select_set(True)
        bpy.context.view_layer.objects.active = source
        bpy.ops.object.join()
    source.name = "bake_source"
    size = max(source.dimensions)
    # size/400, not /200: at /200 a 2m spindly tree remeshed into 373 disconnected fragments
    # because its branches were thinner than a voxel; /400 brought that to 27 with no cost on
    # chunky assets (a lantern stayed at 1 island either way, +0.3s of remesh).
    voxel = size / 400.0 if o["voxel"] == "auto" else float(o["voxel"])

    n, isl, nm = mesh_stats(source)
    # hy3d paint unwraps with xatlas and exports the mesh split along every UV seam, so a painted
    # asset reports tens of thousands of "islands" that are not holes at all (the bare tree: 46
    # islands as generated, 5,425 after painting, same face count). Weld them back so the stats
    # mean something and the bake source is coherent.
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-5)
    bpy.ops.object.mode_set(mode="OBJECT")
    n_w, isl_w, nm_w = mesh_stats(source)
    log(f"source: {n} tris, {isl} islands, {nm} non-manifold edges, {size:.3f} units tall")
    log(f"welded UV seams: {isl_w} real islands, {nm_w} non-manifold edges")

    if o["mode"] not in ("decimate", "remesh"):
        raise SystemExit("--mode must be 'decimate' or 'remesh'")

    if o["mode"] == "decimate":
        # Welding the UV-seam splits is what makes plain decimation work: before it, a 15k
        # request on the lantern stalled at 20,579 tris and shredded the texture; after it,
        # decimation lands exactly on budget, keeps the UV layer, and the original (4096²)
        # texture comes along untouched — no re-bake, no re-projection loss.
        target = source
        target.name = "gameready"
        m = target.modifiers.new("tri", "TRIANGULATE")
        bpy.ops.object.modifier_apply(modifier=m.name)
        cur = len(target.data.polygons)
        if cur > o["tris"]:
            m = target.modifiers.new("dec", "DECIMATE")
            m.ratio = o["tris"] / cur
            bpy.ops.object.modifier_apply(modifier=m.name)
        n2, isl2, nm2 = mesh_stats(target)
        log(f"target: {n2} tris, {isl2} islands, {nm2} non-manifold edges (original texture kept)")
        if n2 > o["tris"] * 1.25:
            log(f"WARNING: could not reach {o['tris']} tris — try --mode remesh")
        for mtl in target.data.materials:
            if not mtl:
                continue
            b = next((x for x in mtl.node_tree.nodes if x.type == "BSDF_PRINCIPLED"), None)
            if b and not b.inputs["Metallic"].links:
                b.inputs["Metallic"].default_value = 0.0   # glTF defaults metallicFactor to 1
            for nd in mtl.node_tree.nodes:      # a 4096² paint texture is 25MB of GLB on its own
                if nd.type == "TEX_IMAGE" and nd.image and max(nd.image.size) > o["tex"]:
                    was = tuple(nd.image.size)
                    nd.image.scale(o["tex"], o["tex"])
                    nd.image.pack()
                    log(f"resized {nd.image.name} {was} -> {o['tex']}x{o['tex']}")
        bpy.ops.object.select_all(action="DESELECT")
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        bpy.ops.export_scene.gltf(filepath=o["dst"], export_format="GLB", use_selection=True)
        log(f"wrote {o['dst']} ({os.path.getsize(o['dst'])/1e6:.1f} MB)")
        print(f"GAMEIFY_RESULT tris={n2} islands={isl2} nonmanifold={nm2} src_tris={n}")
        return

    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.duplicate()
    target = bpy.context.view_layer.objects.active
    target.name = "gameready"

    t = time.time()
    m = target.modifiers.new("remesh", "REMESH")
    m.mode = "VOXEL"; m.voxel_size = voxel; m.adaptivity = 0.0
    bpy.ops.object.modifier_apply(modifier=m.name)
    log(f"remeshed at voxel {voxel:.4f} -> {len(target.data.polygons)} faces in {time.time()-t:.1f}s")

    m = target.modifiers.new("tri", "TRIANGULATE")      # so the decimate ratio means tris
    bpy.ops.object.modifier_apply(modifier=m.name)
    cur = len(target.data.polygons)
    if cur > o["tris"]:
        m = target.modifiers.new("dec", "DECIMATE")
        m.ratio = o["tris"] / cur
        bpy.ops.object.modifier_apply(modifier=m.name)

    # Drop confetti the remesh leaves behind — pieces this small are never a real part.
    bm = bmesh.new(); bm.from_mesh(target.data)
    seen = set(); kill = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack = [f]; comp = []; seen.add(f.index)
        while stack:
            c = stack.pop(); comp.append(c)
            for e in c.edges:
                for lf in e.link_faces:
                    if lf.index not in seen:
                        seen.add(lf.index); stack.append(lf)
        if len(comp) < 8:
            kill.extend(comp)
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        bm.to_mesh(target.data); target.data.update()
        log(f"dropped {len(kill)} faces of sub-8-face confetti")
    bm.free()

    n2, isl2, nm2 = mesh_stats(target)
    log(f"target: {n2} tris, {isl2} islands, {nm2} non-manifold edges")

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15192, island_margin=0.005)   # 66 degrees
    bpy.ops.object.mode_set(mode="OBJECT")

    # Carry the source's surface response over before we drop its material. A fresh Blender
    # material defaults to roughness 0.5, which makes a smooth low-poly hull mirror the sky and
    # read pale blue next to the dense original — the maps are fine, the shading is not.
    src_bsdf = next((x for m in source.data.materials if m
                     for x in m.node_tree.nodes if x.type == "BSDF_PRINCIPLED"), None)
    roughness = float(src_bsdf.inputs["Roughness"].default_value) if src_bsdf else 1.0
    # hy3d's GLB writer omits pbrMetallicRoughness factors, and glTF's spec default for
    # metallicFactor is 1.0 — so every painted asset imports as pure metal, mirroring the sky.
    # These are painted props: force dielectric unless a real metallic texture came along.
    metallic = 0.0
    if src_bsdf and src_bsdf.inputs["Metallic"].links:
        metallic = float(src_bsdf.inputs["Metallic"].default_value)

    target.data.materials.clear()
    mat = bpy.data.materials.new("gameready")
    mat.use_nodes = True
    target.data.materials.append(mat)
    albedo = bpy.data.images.new("albedo", o["tex"], o["tex"])
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = albedo
    mat.node_tree.nodes.active = node          # the one, reused, bake destination
    node.select = True

    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.samples = 1       # a colour/normal transfer needs no sampling
    emissive_albedo(source)

    def bake(bake_type, image):
        node.image = image
        mat.node_tree.nodes.active = node
        bpy.ops.object.select_all(action="DESELECT")
        source.select_set(True); target.select_set(True)
        bpy.context.view_layer.objects.active = target      # active object receives the bake
        t0 = time.time()
        r = bpy.ops.object.bake(type=bake_type, use_clear=True, use_selected_to_active=True,
                                cage_extrusion=size * 0.02, max_ray_distance=size * 0.05)
        buf = np.empty(o["tex"] * o["tex"] * 4, dtype=np.float32)
        image.pixels.foreach_get(buf)
        px = buf.reshape(-1, 4)[:, :3]
        log(f"baked {bake_type.lower()} {o['tex']}x{o['tex']} in {time.time()-t0:.1f}s "
            f"(mean {px.mean():.3f} std {px.std():.3f})")
        if "CANCELLED" in r or px.std() < 1e-4:
            log(f"WARNING: the {bake_type.lower()} bake produced nothing ({r})")

    bake("EMIT", albedo)

    if o["ao"]:
        # The source self-shadows through its own detail; a smooth low-poly hull cannot, which is
        # why a faithful albedo still renders washed out. Bake the occlusion off the dense mesh and
        # multiply it in, so one texture carries the shape's own shading.
        ao = bpy.data.images.new("ao", o["tex"], o["tex"], alpha=False, is_data=True)
        bpy.context.scene.cycles.samples = 32          # AO is the one pass that needs samples
        bake("AO", ao)
        bpy.context.scene.cycles.samples = 1
        a_buf = np.empty(o["tex"] * o["tex"] * 4, dtype=np.float32)
        o_buf = np.empty(o["tex"] * o["tex"] * 4, dtype=np.float32)
        albedo.pixels.foreach_get(a_buf); ao.pixels.foreach_get(o_buf)
        a = a_buf.reshape(-1, 4); occ = o_buf.reshape(-1, 4)
        a[:, :3] *= np.clip(occ[:, :3], 0.0, 1.0)      # straight multiply, no strength fudge
        albedo.pixels.foreach_set(a.reshape(-1))
        albedo.update()
        bpy.data.images.remove(ao)
        node.image = albedo          # the shared bake node still pointed at the AO image
        log(f"multiplied AO into the albedo (mean now {a[:, :3].mean():.3f})")

    bsdf = next(x for x in mat.node_tree.nodes if x.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    log(f"material: roughness {roughness:.2f}, metallic {metallic:.2f} (copied from the source)")
    mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])

    if o["normal"]:
        nrm = bpy.data.images.new("normal", o["tex"], o["tex"], alpha=False, is_data=True)
        bake("NORMAL", nrm)
        nnode = mat.node_tree.nodes.new("ShaderNodeTexImage")
        nnode.image = nrm
        nmap = mat.node_tree.nodes.new("ShaderNodeNormalMap")
        mat.node_tree.links.new(nnode.outputs["Color"], nmap.inputs["Color"])
        mat.node_tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        node.image = albedo                    # the shared bake node still held the normal map
        mat.node_tree.nodes.active = node

    for img in bpy.data.images:
        if img.name in ("albedo", "normal"):
            img.pack()

    if not o["keep_source"]:
        bpy.data.objects.remove(source, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.export_scene.gltf(filepath=o["dst"], export_format="GLB", use_selection=True)
    log(f"wrote {o['dst']} ({os.path.getsize(o['dst'])/1e6:.1f} MB)")
    print(f"GAMEIFY_RESULT tris={n2} islands={isl2} nonmanifold={nm2} src_tris={n}")


if __name__ == "__main__":
    main()
