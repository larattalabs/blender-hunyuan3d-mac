#!/usr/bin/env bash
# Render a GLB to a PNG so you can look at what was generated.
#   ./preview.sh model.glb [preview.png]
set -euo pipefail
GLB="${1:?usage: preview.sh model.glb [out.png]}"
OUT="${2:-preview.png}"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$GLB" in /*) ;; *) GLB="$PWD/$GLB";; esac
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT";; esac
"$BLENDER" -b --python "$HERE/preview.py" -- "$GLB" "$OUT" 2>&1 | grep -E "IMPORTED|MATERIAL|RENDERED|Error|Traceback"
