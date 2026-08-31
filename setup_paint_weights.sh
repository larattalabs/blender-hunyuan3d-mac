#!/usr/bin/env bash
# Assemble the weights root that `hy3d paint` expects.
#
# The two HF bundles ship flat (unet/, vae/, dinov2/, realesrgan/), but the Swift loader wants
#   <root>/hunyuan3d-paint-v2-0/{unet,vae}/   <- RGB 2.0 (also the VAE the PBR path reuses)
#   <root>/hunyuan3d-paintpbr-v2-1/unet/      <- PBR 2.1
#   <root>/dinov2-giant/ and <root>/realesrgan/
# so this stitches them together with symlinks — no bytes are copied.
set -euo pipefail

MLX_DIR="${MLX_DIR:-$HOME/AI/hunyuan3d-mlx}"
W="$MLX_DIR/weights"
ROOT="$W/paint"

[ -d "$W/paint-small" ] || { echo "missing $W/paint-small — hf download zimengxiong/hunyuan3d-mlx-paint-small --local-dir $W/paint-small" >&2; exit 1; }

mkdir -p "$ROOT"
ln -sfn "$W/paint-small" "$ROOT/hunyuan3d-paint-v2-0"          # unet + vae (RGB 2.0)
if [ -d "$W/paint-large" ]; then
  ln -sfn "$W/paint-large" "$ROOT/hunyuan3d-paintpbr-v2-1"     # unet (PBR 2.1)
  ln -sfn "$W/paint-large/dinov2" "$ROOT/dinov2-giant"         # PBR image conditioning
  ln -sfn "$W/paint-large/realesrgan" "$ROOT/realesrgan"
else
  echo "note: no paint-large — RGB paint only, no PBR" >&2
  ln -sfn "$W/paint-small/realesrgan" "$ROOT/realesrgan"
fi

echo "assembled $ROOT:"
ls -l "$ROOT" | sed 's/^/  /'
