---
name: blender-3d-gen
description: Generate textured 3D assets locally for Blender with Hunyuan3D — text prompt → reference image (Krea 2 Turbo) → mesh + texture (Hunyuan3D MLX) → imported into the open Blender scene, ~2 minutes end to end. Use when asked to make/generate/add a 3D model, object, prop, or asset for a Blender scene, when driving BlenderMCP's Hunyuan3D tools, or when a scene needs an object that isn't worth modelling by hand. Covers reference-image prompting, mesh and texture parameters, previewing the result, and what the local pipeline can't do (text-conditioned generation).
---

# Local 3D asset generation for Blender

Everything runs on this machine. No API keys, no cloud.

```
prompt ──Krea 2 Turbo (~25s)──▶ reference.png ──hy3d shape (~11s)──▶ mesh ──hy3d paint (~72s)──▶ textured ──▶ Blender
```

All MLX/Metal, all local. **~7 minutes** from prompt to a textured asset at the default `high`
quality; add `"quality": "fast"` to a request (or `HY3D_QUALITY=fast`) for a ~2 minute draft while
you iterate on the reference image.

Tools live in the blender-hunyuan3d-mac checkout, `$HY3D_DIR` below (see its README for the service itself).

Paths below use `$HY3D_DIR` for the blender-hunyuan3d-mac checkout and `$HY3D_MLX` for the MLX
engine (default `~/AI/hunyuan3d-mlx`). Set them, or substitute your own locations.

## Before anything

```bash
curl -s localhost:8081/health
```

Check `"shape_backend": "mlx"` and `"paint": {"available": true}`. The `ok:false` line refers only
to ComfyUI, which is a fallback and normally not running — ignore it. A connection refused means the
endpoint itself is down: Blender starts it at launch, otherwise run
`$HY3D_DIR/serve.sh`.

## The loop

```bash
cd "$HY3D_DIR"

./scripts/make_reference.sh "a weathered wooden treasure chest with iron bands" /tmp/ref.png 3
./scripts/image_to_3d.sh /tmp/ref.png /tmp/chest.glb 256 30 rgb   # textured, ~95s (5th arg: rgb|pbr|none)
./scripts/preview.sh /tmp/chest.glb /tmp/chest.png              # then Read the PNG and actually look at it
```

**Look at both images before handing anything over.** The reference image decides the mesh — if the
mesh is wrong, re-roll the *image* (new seed or a clearer prompt), not the mesh parameters. Reading
`/tmp/ref.png` before spending three minutes on geometry is the cheapest check available.

Then import into the user's open scene (BlenderMCP addon socket, port 9876):

```python
import json, socket
s = socket.create_connection(("127.0.0.1", 9876), timeout=1800)
s.sendall(json.dumps({"type": "execute_code", "params": {"code":
    "import bpy; bpy.ops.import_scene.gltf(filepath='/tmp/chest.glb')"}}).encode())
# (the import + cleanup block below is what to send in practice)
print(json.loads(s.recv(65536).decode()))
```

For a **textured** asset, either tick "Generate Texture" in the panel (autostart leaves it on) or
POST with `"texture": true` — the bridge runs `hy3d paint` after the shape stage. To texture a mesh
you already have:

```bash
$HY3D_MLX/.build/release/hy3d paint mesh.glb ref.png -o textured.glb \
  --weights $HY3D_MLX/weights/paint --model rgb   # or pbr: albedo + metallic-roughness
```

Or let the addon do the whole generate-and-import itself — `create_hunyuan_job` with
`{"image": "/tmp/ref.png"}`, or the `generate_hunyuan3d_model` MCP tool if BlenderMCP tools are
available in this session. Same result either way, and **Blender's UI freezes while it runs** (~15s untextured, ~90s textured) —
the addon's socket handler blocks. Say so up front rather than letting the user think it hung.

## Writing the reference image prompt

The model reconstructs what it can see. It cannot invent an occluded back, and it will happily
model a background or a second object into the mesh.

**Always include** (the `make_reference.sh` style suffix does this for you): single object,
centered, entire object visible with margin, three-quarter view slightly above, plain flat
light-grey background, even diffuse lighting, no cast shadows, product-photo framing.

**Spend the prompt on the object**: material, era, wear, silhouette, distinctive parts
("iron bands", "riveted corners", "domed lid"). Silhouette and large forms survive into the mesh;
fine surface pattern does not.

**Avoid** — these produce bad meshes, not just bad images:
- scenes, groundplanes, or props next to the object ("a chest *in a cave*") — they become geometry
- cropped or edge-touching subjects — the mesh gets sliced off
- top-down, straight-on, or extreme perspective — a 3/4 view carries the most shape information
- glass, chrome, mirrors, and volumetrics — nothing coherent to reconstruct
- hair-thin parts (wires, antennae, spokes) — they vanish or web over at 256 octree
- dramatic side lighting or heavy shadow — read as shape and dents the geometry

Human/character figures work but land in the uncanny valley; hard-surface props, furniture,
containers, tools, food, plants and stylised game assets are where this shines.

## The endpoint cleans the image for you

Every request is matted onto white, cropped to the subject and padded square before it reaches the
model (`prep_image.py`) — Hunyuan3D needs that and ComfyUI's nodes don't do it. Consequences worth
knowing:

- **A flat background in the reference is not cosmetic, it is what makes the matte work.** Keep it
  in the prompt.
- **A busy photo background is left alone** (there's no matting model installed) and will be
  reconstructed as geometry. Cut the subject out first, or re-shoot the prompt on a plain background.
- **Sheets, blobs or a solid block instead of the object** = the subject wasn't separated from the
  background. That was the pre-preprocessing failure mode; if it reappears, look at the input image
  first, not at `steps`.

## Parameters that matter

| knob | default | when to change |
|---|---|---|
| `octree` | 256 | 384–512 for small detail (buttons, teeth, thin rims); costs time and vertices |
| `steps` | 30 | 20 for a quick look; 50 when the shape comes out mushy. Raising it does **not** fix a bad input image — it made one failure worse |
| `texture` | on | ~72s extra. Turn off when iterating on shape, back on for the keeper |
| `paint_model` | `rgb` | `pbr` gives a de-lit albedo + a real roughness map at 4096² (~115s vs ~72s). See below |
| `guidance_scale` | 5.5 | 4–8 is the useful band; higher tracks the image harder |
| `--seed` (reference) | 0 | the fastest lever by far — re-roll the image before touching anything else |

Meshes land at ~100–400k vertices (textured runs are denser). For scene use, add a Decimate modifier (~0.2 ratio) unless the
user wants the raw density.

## RGB vs PBR paint (measured 2026-08-30)

`rgb` bakes lighting into the colour — it looks great straight out, and slightly "painted on".
`pbr` returns albedo + a metallic-roughness map, so the asset relights properly in your scene.

**The catch: the metallic channel comes back ~0.** Two subjects, both with obvious metal parts:
a treasure chest (metallic max 0.28, mean 0.02) and a steel moka pot (max 0.48, mean 0.00). The
roughness channel is genuinely informative (moka mean 0.10 — correctly glossy), and the channel
packing is per glTF spec, so this is the model being conservative, not a broken map. A metal asset
therefore renders as a chalky dielectric until you set Metallic yourself:

```python
b = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
for l in [l for l in mat.node_tree.links if l.to_socket == b.inputs["Metallic"]]:
    mat.node_tree.links.remove(l)
b.inputs["Metallic"].default_value = 1.0     # then let the roughness map do the work
```

Rule of thumb: **`rgb` for wood/stone/fabric/organic props** (it just works), **`pbr` when the asset
must relight or is metal** — and expect to set Metallic by hand for the metal case.

## After import — the cleanups worth doing unasked

Generated meshes arrive as `Mesh_0`, roughly unit-sized, origin at the world center, no material:

```python
import bpy
before = set(bpy.context.scene.objects)
bpy.ops.import_scene.gltf(filepath="/tmp/chest.glb")
new = [o for o in bpy.context.scene.objects if o not in before]   # never hardcode "Mesh_0":
o = next(o for o in new if o.type == "MESH")                      # repeat imports get .001, .002…
o.name = "treasure_chest"
bpy.ops.object.select_all(action='DESELECT'); o.select_set(True)
bpy.context.view_layer.objects.active = o
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')   # origin to the object, not the world
bpy.ops.object.shade_smooth_by_angle()               # keeps hard edges, smooths the rest

# Scale to a real-world height and sit it on the floor. Generated meshes arrive ~1-2 units tall
# with no meaningful scale, so this is the difference between a prop and a monolith.
TARGET_HEIGHT = 0.35                                 # metres, along Z
f = TARGET_HEIGHT / o.dimensions.z                   # dimensions already include current scale,
o.scale = tuple(s * f for s in o.scale)              # so multiply rather than assign (f, f, f)
bpy.context.view_layer.update()
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)   # bake scale back to 1
o.location.z -= min((o.matrix_world @ v.co).z for v in o.data.vertices)      # drop onto z=0
bpy.context.view_layer.update()
print(o.name, len(o.data.vertices), tuple(round(d, 3) for d in o.dimensions))
```

**Pick the height deliberately** — it is the one number the model cannot give you, and everything
downstream (physics, DOF, lighting falloff) reads wrong if it's off. Ask the user when it matters;
otherwise use a sane real-world value: a mug ~0.10, a hurricane lantern ~0.35, a chair ~0.90, a
door ~2.0, a tree ~5+. If the scene has existing objects, match against those instead of guessing.

Skip the floor-drop line for something that hangs, mounts or floats.

## Refining a result

When something comes back disappointing, the levers in order of payoff (all measured on the same
lantern reference):

| lever | how | cost | what it buys |
|---|---|---|---|
| **bigger shape model** | on by default (`"shape_model": "small"` to disable) | 12s → 45s | the big one. Separated wire cage, defined rivets, crisper collar — soft blobs become parts |
| **octree 384** | panel slider or `octree` | +5s | modest sharpening; more vertices |
| **tuned paint** | on by default (`"quality": "fast"` to drop it) | 95s → ~6.5min | burner slots read as holes, less blotching |
| **draft mode** | `"quality": "fast"` | ~95 s | drops both — use it while iterating, not for the keeper |
| **re-roll the reference** | new `--seed` | 25s | still the cheapest fix when the shape is simply wrong |

**Where the texture detail goes.** The paint pipeline renders six views and weights them
`front 1.0, back 0.5, left/right 0.1, top/bottom 0.05` (`Pipeline.swift`). So:

- **Generate from the angle the asset will be seen from** — the reference view gets 10× the weight
  of the sides and 20× the top. A prop seen mainly from its left should be generated from its left.
- Sides come out softer than the back, and **top/bottom faces are nearly unpainted** — expect a
  washed-out lid or base. Don't put a generated asset where the camera looks down on it.

**Artifacts that are the model, not your settings:**

- **Transparent things become opaque.** Glass, chrome and liquids get painted as pale solids with
  highlights baked in — the lantern's chimney is a white blob. Fix in Blender: select those faces
  and assign a real glass material; the geometry is usually right.
- **The back is invented**, symmetrically, from the front. Plausible, not accurate.
- **RGB paint bakes lighting in.** Use `pbr` when it must relight (see above).
- **Hair-thin parts merge** into their surroundings at any octree.

## Making it a game asset

What comes out is a raw isosurface of several hundred thousand tris. It also *reports* tens of
thousands of disconnected islands — but that is hy3d's paint stage splitting the mesh along UV
seams, not holes (a bare tree: 46 islands before painting, 5,425 after, same face count). Welding
the seams first is what makes everything else work:

```bash
"$HY3D_DIR/scripts/gameify.sh" model.glb model_game.glb 15000 2048
```

Default path: weld seams → decimate to budget → force metallic 0 → resize texture. Keeps the
original texture and UVs, hits budget exactly, and preserves thin structures. Measured: lantern
1,006,732 → 15,000 tris (6 islands, 8 MB); oak 1,980,060 → 15,000 (68 islands, 9.9 MB); a spindly
bare tree keeps every branch. `--mode remesh` re-bakes onto fresh uniform topology instead — use it
only if a welded mesh still will not decimate, since it rounds off thin features.

- **Generate at `high`, then gameify.** The dense mesh is the bake source; its detail survives in
  the normal map. Generating at `fast` to "get a smaller model" throws that away and still leaves
  broken topology.
- **Thin structures** survive the default path but not `--mode remesh`. A few detached twigs come
  from the shape stage itself (46 islands at octree 384, 36 at 512) — raise the octree if branch
  connectivity matters, or prompt for thicker limbs.
- Budgets: 5–15k tris with a 2k texture is a reasonable background prop.

## Limits — state these plainly, don't work around them silently

- **No text→3D at the endpoint.** The model is image-conditioned; a text-only request returns an
  error by design. Always make the image first.
- **Everything arrives dielectric now.** glTF defaults `metallicFactor` to 1.0 and hy3d omits it, so
  assets used to import as pure metal (a green pine rendering sky-blue). The bridge patches this on
  the way out; if you invoke `hy3d` directly, set Metallic to 0 yourself.
- **Texture works** (MLX port of Hunyuan3D-Paint, set up 2026-08-30) — no CUDA needed. The imported
  object arrives with a material and a baked texture image. Textures are convincing on hard-surface
  props; on smooth featureless forms the paint model bakes some shading smudges into the albedo.
- **One at a time.** Paint peaks near 38GB. Don't fan out parallel generations.
- **The ComfyUI fallback** (`HY3D_SHAPE_BACKEND=comfy`) is ~180s and shape-only. You should not need
  it; if `health` reports `shape_backend: comfy`, the MLX weights or `mlx.metallib` are missing —
  see the checkout's README.

## When this is the wrong tool

Parametric or precise geometry (a 40mm bracket with two 5mm holes, a staircase, a room) should be
scripted in `bpy` — it is faster, exact, and editable. Use generation for organic or detailed props
where hand-modelling is the expensive part. Also check `get_polyhaven_status` / Sketchfab in the
BlenderMCP panel first: an existing library asset is instant and textured.
