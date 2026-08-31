# TRELLIS.2 vs Hunyuan3D on Apple Silicon

Measured 2026-08-31 on an M5 Max (128GB), same reference images through both pipelines.
TRELLIS.2 via [gtrg55/trellis2-mlx](https://github.com/gtrg55/trellis2-mlx); Hunyuan3D via this repo.

**Short version:** TRELLIS produces structurally better geometry — it models leaves and thin
branches as real separate parts where Hunyuan makes a blob — but its cost scales with how much of
the volume is occupied, and its output is not game-ready. On a bushy oak that meant **2h46m** and a
mesh that neither decimates nor remeshes cleanly. Hunyuan is 10–20× faster on the same subject and
survives the game-asset pipeline. Neither one wins outright.

## Runtimes

| subject | TRELLIS generate | TRELLIS export | TRELLIS total | Hunyuan total |
|---|---|---|---|---|
| bare spindly tree | 26.5 s | 70 s | **1m 37s** | ~7 min |
| pine (fine foliage) | 659 s | 66 s | **12m 5s** | ~7 min |
| oak (dense canopy) | 1,884 s | 8,086 s | **2h 46m** | ~10 min |

TRELLIS runs a sparse-voxel pipeline at 1024³: cost tracks *occupied volume*, so a bare tree is
20 seconds and a full canopy is half an hour — with an export (decimate + bake) that took over two
hours on the oak's 1.12M-face output. Hunyuan's cost barely moves between those subjects, because
it always decodes the same dense grid.

## What each one actually produces

| | TRELLIS.2 | Hunyuan3D |
|---|---|---|
| dense foliage | **individual leaves** as separate cards, branches visible through the canopy | solid blob canopy with leaves painted on |
| thin branches | clean, connected, 9 islands after cleanup | connected but with floating twigs, ~41 islands |
| topology | leaf cards are infinitely thin → **300k non-manifold edges** on the oak | isosurface: fragmented, ~27% non-manifold |
| game-ready | no — see below | yes, via `gameify.sh` |

The oak is the clearest case: TRELLIS's is far more beautiful and far less usable.

## Why TRELLIS output resists the game pipeline

`gameify.sh` has two paths and the oak defeats both:

- **decimate** — 300k non-manifold edges block edge collapse; a 60k request stalled at 211,806 tris
  and *raised* the island count from 449 to 3,855.
- **remesh** — voxel remeshing assumes a volume. Leaf cards have no thickness, so they vanish or
  shatter; the result was visually a cloud of black fragments.

Thin, card-like geometry needs a different treatment (thickness via solidify before remeshing, or
alpha-cutout planes authored as such). That is unsolved here.

## The pine failure was ours, not the model's

TRELLIS returned a bare trunk with stubs for the pine. Cause: `prep_rgba.py` hard-keys the alpha,
and the pine's alpha came out **46.5% holes inside its own bounding box** — the key cut between
every needle cluster, so TRELLIS saw a lacy, mostly-empty silhouette and built a dead trunk. The
oak survived because its leaves are coarser (36.3% holes) and it read them as real leaves.

A proper matting model returns a soft, solid alpha over foliage. The workaround below does not.

## Running it without DINOv3 access

TRELLIS.2's image conditioning uses `facebook/dinov3-vitl16-pretrain-lvd1689m`, which is gated, and
its preprocessing wants a gated background-removal model too. Two independent workarounds:

1. **Background removal** — feed it an **RGBA** image and it skips its own matting entirely.
   `prep_rgba.py` in this repo keys a flat-background reference into RGBA. Free, but see the pine
   failure above: it is a hard key, not a matte.
2. **DINOv3 weights** — a mirror such as `camenduru/dinov3-vitl16-pretrain-lvd1689m` carries the
   same `config.json` + `model.safetensors`. Point `image_cond_model.args.model_name` in
   `weights/TRELLIS.2-4B/pipeline.json` at it. Safetensors only, so no pickle execution risk — but
   it is an unofficial re-upload, and **DINOv3 licence compliance is on you**. The clean path is
   requesting access from Meta.

## A memory bug worth knowing about

TRELLIS's UV rasterizer (`o-voxel/o_voxel/postprocess_cpu.py`, `_rasterize_uv_gpu`) chunks faces in
fixed blocks of 50,000, but allocates its texel grid as `(C, max_h, max_w, 2)` using the **largest**
UV bounding box in the chunk. One stretched triangle therefore sizes the grid for all 50,000 faces.
On a hurricane lantern at 2048² that asked for **33.6 GiB** in a single allocation and died:

```
RuntimeError: MPS backend out of memory (MPS allocated: 100.78 GiB, ... )
Tried to allocate 33.61 GiB on shared pool.
```

Faces are scatter-written by absolute pixel coordinate, so chunk order is irrelevant. The fix in
[`patches/ovoxel-uv-raster-chunking.patch`](patches/ovoxel-uv-raster-chunking.patch) sorts faces by
bounding-box area and caps each chunk at `C × max_h × max_w` texels
(`OVOXEL_RASTER_TEXELS`, default 16M ≈ 1.3GB) instead of a fixed face count. This is also the
likely cause of the oak's 2h15m export: not compute, but thrashing on absurd allocations.

## When to use which

- **Hunyuan3D** — foliage, throughput, anything heading for a game engine. Predictable ~7–10 min,
  and `gameify.sh` turns it into a 15–60k asset with baked maps.
- **TRELLIS.2** — hero assets, sparse or structurally complex subjects (bare trees, lattices,
  anything where separate parts matter), when you can afford the time and hand-retopo the result.

Do not reach for TRELLIS on a bushy subject expecting a background prop. That is the 2h46m mistake.
