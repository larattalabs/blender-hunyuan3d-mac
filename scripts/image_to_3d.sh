#!/usr/bin/env bash
# Turn a reference image into a GLB mesh via the local Hunyuan3D endpoint.
#
#   ./image_to_3d.sh reference.png [out.glb] [octree] [steps] [rgb|pbr|none]
#
# The 5th argument picks texturing: rgb (default, ~72s), pbr (~115s, albedo + roughness),
# or none for bare geometry. Shape itself is ~11s.
# Starts nothing itself — run serve.sh first (Blender's autostart normally has).
set -euo pipefail

IMG="${1:?usage: image_to_3d.sh reference.png [out.glb] [octree] [steps]}"
OUT="${2:-model.glb}"
OCTREE="${3:-256}"
STEPS="${4:-30}"
PAINT="${5:-rgb}"
case "$PAINT" in rgb|pbr|none) ;; *) echo "5th arg must be rgb, pbr or none" >&2; exit 1;; esac
URL="${HY3D_URL:-http://localhost:8081}"

[ -f "$IMG" ] || { echo "no such image: $IMG" >&2; exit 1; }
curl -sf -m 5 -o /dev/null "$URL/health" || { echo "endpoint down at $URL — run serve.sh" >&2; exit 1; }

REQ="$(mktemp -t hy3dreq)"
trap 'rm -f "$REQ"' EXIT
python3 - "$IMG" "$OCTREE" "$STEPS" "$PAINT" > "$REQ" <<'PY'
import base64, json, sys
img, octree, steps, paint = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
json.dump({"image": base64.b64encode(open(img, "rb").read()).decode(),
           "octree_resolution": octree, "num_inference_steps": steps,
           "guidance_scale": 5.5, "texture": paint != "none",
           "paint_model": "pbr" if paint == "pbr" else "rgb"}, sys.stdout)
PY

CODE=$(curl -sS -m 3600 -o "$OUT" -w "%{http_code}" -H "Content-Type: application/json" \
  --data-binary @"$REQ" "$URL/generate")
if [ "$CODE" != "200" ]; then
  echo "generation failed (HTTP $CODE):" >&2; cat "$OUT" >&2; echo >&2; rm -f "$OUT"; exit 1
fi
echo "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
