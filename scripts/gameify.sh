#!/usr/bin/env bash
# Make a generated GLB game-ready: clean topology + baked albedo/normal maps.
#   ./gameify.sh in.glb out.glb [tris] [tex]
set -euo pipefail
IN="${1:?usage: gameify.sh in.glb out.glb [tris] [tex]}"
OUT="${2:?usage: gameify.sh in.glb out.glb [tris] [tex]}"
TRIS="${3:-15000}"
TEX="${4:-2048}"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$IN" in /*) ;; *) IN="$PWD/$IN";; esac
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT";; esac
"$BLENDER" -b --python "$HERE/gameify.py" -- "$IN" "$OUT" --tris "$TRIS" --tex "$TEX" 2>&1 \
  | grep -E "^\[gameify\]|GAMEIFY_RESULT|Error|Traceback"
