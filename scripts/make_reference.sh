#!/usr/bin/env bash
# Generate a Hunyuan3D-friendly reference image with Krea 2 Turbo (MLX, local).
#
#   ./make_reference.sh "a weathered wooden treasure chest with iron bands" [out.png] [seed]
#
# Appends the framing/lighting terms that make an image usable as image-to-3D input.
# ~25s at 768x768 on this machine. Override the styling with HY3D_STYLE.
set -euo pipefail

PROMPT="${1:?usage: make_reference.sh \"prompt\" [out.png] [seed]}"
OUT="${2:-reference.png}"
SEED="${3:-0}"
SIZE="${HY3D_REF_SIZE:-768}"
KREA="${KREA_DIR:-$HOME/AI/krea2-mlx}"
STYLE="${HY3D_STYLE:-single object, centered, entire object visible with margin, three-quarter view slightly above, plain flat light-grey background, even diffuse studio lighting, no cast shadows, no props, sharp focus, product photo}"

[ -x "$KREA/.venv/bin/python" ] || { echo "krea2-mlx venv not found at $KREA/.venv" >&2; exit 1; }

case "$OUT" in /*) ABS="$OUT";; *) ABS="$PWD/$OUT";; esac
cd "$KREA"
exec ./.venv/bin/python generate.py "$PROMPT, $STYLE" \
  --width "$SIZE" --height "$SIZE" --steps "${HY3D_REF_STEPS:-8}" --seed "$SEED" --out "$ABS"
