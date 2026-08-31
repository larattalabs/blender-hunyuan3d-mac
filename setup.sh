#!/usr/bin/env bash
# One-shot setup for the MLX shape + paint backend (github.com/ZimengXiong/Hunyuan3D-Swift).
#
# Builds the Swift package, plants the Metal library it needs, and assembles the paint weights
# root. Safe to re-run.
set -euo pipefail

MLX_DIR="${MLX_DIR:-$HOME/AI/hunyuan3d-mlx}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$MLX_DIR/.build/release/hy3d"

[ -d "$MLX_DIR" ] || git clone https://github.com/ZimengXiong/Hunyuan3D-Swift.git "$MLX_DIR"

echo "== building hy3d (a few minutes the first time)"
( cd "$MLX_DIR" && swift build -c release )

# SwiftPM never builds MLX's Metal kernels, so the binary aborts with "Failed to load the default
# metallib" unless mlx.metallib is colocated with it. Borrow the one from any Python mlx install —
# keep the versions close (mlx-swift pins MLX 0.31.x).
if [ ! -f "$MLX_DIR/.build/release/mlx.metallib" ]; then
  echo "== planting mlx.metallib"
  LIB="$(find "$HOME/AI" -path "*/site-packages/mlx/lib/mlx.metallib" -print -quit 2>/dev/null || true)"
  [ -n "$LIB" ] || { echo "no mlx.metallib found under ~/AI — pip install mlx somewhere, or copy one in" >&2; exit 1; }
  cp "$LIB" "$MLX_DIR/.build/release/mlx.metallib"
  echo "   from $LIB"
fi

echo "== weights"
for r in shape-small paint-small paint-large; do
  if [ -d "$MLX_DIR/weights/$r" ]; then
    echo "   have $r ($(du -sh "$MLX_DIR/weights/$r" | cut -f1))"
  else
    echo "   MISSING $r -> hf download zimengxiong/hunyuan3d-mlx-$r --local-dir $MLX_DIR/weights/$r"
  fi
done
[ -d "$MLX_DIR/weights/paint-small" ] && "$HERE/setup_paint_weights.sh"

echo "== smoke test"
"$BIN" help > /dev/null && echo "   hy3d runs"
