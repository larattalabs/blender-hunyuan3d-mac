# Trees: use a procedural generator, not this pipeline

Measured 2026-08-31, Blender 5.2 on an M5 Max. Short version: for trees and foliage, free Blender
add-ons beat both image-to-3D pipelines by roughly **three orders of magnitude in time** and produce
better structure. Use Hunyuan3D for props; use a tree generator for trees.

| | time per tree | structure | wind/LOD data |
|---|---|---|---|
| **Modular Tree** (add-on) | **0.2–0.6 s** | real branch hierarchy, instanced leaves | Pivot Painter export, LOD + billboard sockets |
| **Sapling Tree Gen** (add-on) | ~1 s | curve-based branches, leaf planes | none built in |
| Hunyuan3D (this repo) | ~7 min | solid blob canopy, needles painted on | none |
| TRELLIS.2 | 12 min – 2h 46m | individual leaves (oak), but 300k non-manifold edges | none |

The AI pipelines cannot produce what game vegetation needs — alpha-card foliage, LOD chains,
hierarchical wind pivots — because those are structural properties of how the asset is *authored*,
not shape that can be inferred from a photo.

## What to install

All free, GPL, on the Blender extensions platform. Pure-Python except Modular Tree, which ships a
macOS-arm64 native build:

```bash
blender --command extension install sapling_tree_gen --enable
blender --command extension install modular_tree --enable
blender --command extension install easy_tree --enable
blender --command extension install space_colonization_tree_generator --enable
```

Blender needs *Allow Online Access* enabled in preferences first, or `extension sync` refuses.

**Modular Tree** is the pick: species presets (`OAK`, `PINE`, `WILLOW`, `RANDOM`), sub-second
generation, `mtree.export_pivot_painter` for Unreal wind data. **Space Colonization** grows a tree
into a volume — it needs a mesh with enough faces as the active object (a UV sphere works) and
errors out otherwise.

## Two of them are broken on Blender 5.2 — and how to fix them

Modular Tree and Easy Tree both fail with:

```
TypeError: bpy_struct[key] = val: id properties not supported for this type
```

They set geometry-node modifier inputs the Blender 4.x way, `mod[socket_id] = value`. Blender 5.x
moved those values:

```python
# Blender 4.x
mod[socket_id] = value
# Blender 5.x
mod.properties.inputs[socket_id]["value"] = value
```

`patches/blender5-gn-modifier-sockets.md` carries a compatibility shim and the sed-able rewrite
(33 call sites in Easy Tree, 12 in Modular Tree). Both generate correctly afterwards. Modular Tree's
source even carries a comment claiming it was updated for "Blender 5.0+" — it got the socket
*identifier* change but not the *value* change.

**These patches live in the extensions folder and are overwritten on add-on update.** Worth
reporting upstream rather than maintaining locally.

## Caveat on the triangle counts

Modular Tree's leaves are **instanced**, so baking them out is heavy: exporting with `export_apply`
gave 4.3M triangles for the oak, 7.0M for the pine, 12.7M for the willow. Those are authoring
figures, not shipping ones — real use means LODs or billboards through the leaf node group's own
sockets. `gameify.sh` in this repo is the wrong tool for instanced foliage; it targets single dense
meshes.
