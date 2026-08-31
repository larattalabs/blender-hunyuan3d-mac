#!/usr/bin/env bash
# Generate a shape several times and keep the best-connected result.
#
#   ./best_shape.sh reference.png out.glb [tries] [octree] [steps]
#
# Seed is by far the strongest lever on whether thin structures come out connected: the same
# spindly tree, same settings, ran 17 / 25 / 33 / 33 / 56 islands across five seeds. Nothing else
# came close (octree 384->512 moved it 46->36; the marching-cubes isolevel barely mattered).
# Shape is ~30s, so trying a few and keeping the least fragmented is cheap.
set -euo pipefail

IMG="${1:?usage: best_shape.sh reference.png out.glb [tries] [octree] [steps]}"
OUT="${2:?usage: best_shape.sh reference.png out.glb [tries] [octree] [steps]}"
TRIES="${3:-3}"; OCTREE="${4:-512}"; STEPS="${5:-40}"
MLX_DIR="${MLX_DIR:-$HOME/AI/hunyuan3d-mlx}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
WEIGHTS="${HY3D_SHAPE_WEIGHTS:-$MLX_DIR/weights/shape-large}"
COMFY_PY="${COMFY_PY:-$HOME/ComfyUI-Installs/ComfyUI/ComfyUI/.venv/bin/python}"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
prep="$work/prep.png"
"$COMFY_PY" "$HERE/../prep_image.py" "$IMG" "$prep" >/dev/null || cp "$IMG" "$prep"

best=""; best_n=999999
for i in $(seq 1 "$TRIES"); do
  seed=$((i * 7919 % 100000))
  "$MLX_DIR/.build/release/hy3d" shape "$prep" -o "$work/try$i.glb" --weights "$WEIGHTS" \
      --steps "$STEPS" --octree "$OCTREE" --seed "$seed" >/dev/null 2>&1
  n=$("$BLENDER" -b --python-expr "
import bpy,bmesh,sys
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath='$work/try$i.glb')
o=[x for x in bpy.context.scene.objects if x.type=='MESH'][0]
bpy.context.view_layer.objects.active=o; o.select_set(True)
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=1e-5); bpy.ops.object.mode_set(mode='OBJECT')
bm=bmesh.new(); bm.from_mesh(o.data); seen=set(); n=0
for f in bm.faces:
    if f.index in seen: continue
    n+=1; st=[f]; seen.add(f.index)
    while st:
        c=st.pop()
        for e in c.edges:
            for lf in e.link_faces:
                if lf.index not in seen: seen.add(lf.index); st.append(lf)
print('ISLANDCOUNT', n)
" 2>/dev/null | grep ISLANDCOUNT | awk '{print $2}')
  echo "  seed $seed -> ${n:-?} islands"
  if [ -n "${n:-}" ] && [ "$n" -lt "$best_n" ]; then best_n=$n; best="$work/try$i.glb"; fi
done

[ -n "$best" ] || { echo "no successful generation" >&2; exit 1; }
cp "$best" "$OUT"
echo "kept the $best_n-island result -> $OUT"
