#!/usr/bin/env bash
# Start the local Hunyuan3D endpoint for BlenderMCP (http://localhost:8081/generate).
#
# Runs the bridge in the foreground; ComfyUI is started lazily by the bridge on the first
# generation request (pass --eager to boot it up front instead). Ctrl-C stops both.
# Shape-only (no texture) — see README.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI-Installs/ComfyUI/ComfyUI}"
COMFY_PY="${COMFY_PY:-$COMFY_DIR/.venv/bin/python}"
COMFY_PORT="${COMFY_PORT:-8188}"          # Comfy Desktop's port; reused if already up
COMFY_FALLBACK_PORT="${COMFY_FALLBACK_PORT:-8189}"
CKPT_DIR="${CKPT_DIR:-$HOME/ComfyUI-Shared/models/checkpoints}"
# Comfy Desktop's model/input/output wiring; a CLI-launched ComfyUI does not pick this up on its own.
MODEL_PATHS="${MODEL_PATHS:-$HOME/Library/Application Support/Comfy Desktop/shared_model_paths.yaml}"
SHARED_INPUT="${SHARED_INPUT:-$HOME/ComfyUI-Shared/input}"
SHARED_OUTPUT="${SHARED_OUTPUT:-$HOME/ComfyUI-Shared/output}"
CKPT_NAME="${HY3D_CKPT:-hunyuan3d-dit-v2_fp16.safetensors}"
LOG="$HERE/logs/comfy-headless.log"

if [ ! -e "$CKPT_DIR/$CKPT_NAME" ]; then
  echo "missing checkpoint: $CKPT_DIR/$CKPT_NAME" >&2
  echo "download it with:  hf download Comfy-Org/hunyuan3D_2.0_repackaged split_files/$CKPT_NAME" >&2
  exit 1
fi

EAGER=0
[ "${1:-}" = "--eager" ] && EAGER=1

up() { curl -sf -m 3 -o /dev/null "http://127.0.0.1:$1/system_stats"; }

COMFY_PID=""
if [ "$EAGER" = "0" ]; then
  echo "bridge only; ComfyUI will start on the first generation request"
  export COMFY_URL="http://127.0.0.1:$COMFY_PORT"
  exec python3 "$HERE/bridge.py"
fi
if up "$COMFY_PORT"; then
  echo "using ComfyUI already running on :$COMFY_PORT"
  URL="http://127.0.0.1:$COMFY_PORT"
elif up "$COMFY_FALLBACK_PORT"; then
  echo "using ComfyUI already running on :$COMFY_FALLBACK_PORT"
  URL="http://127.0.0.1:$COMFY_FALLBACK_PORT"
else
  echo "starting headless ComfyUI on :$COMFY_FALLBACK_PORT (log: $LOG)"
  mkdir -p "$HERE/logs"
  ( cd "$COMFY_DIR" && exec "$COMFY_PY" main.py --listen 127.0.0.1 \
      --port "$COMFY_FALLBACK_PORT" --disable-auto-launch \
      --extra-model-paths-config "$MODEL_PATHS" \
      --input-directory "$SHARED_INPUT" \
      --output-directory "$SHARED_OUTPUT" ) >"$LOG" 2>&1 &
  COMFY_PID=$!
  trap '[ -n "$COMFY_PID" ] && kill "$COMFY_PID" 2>/dev/null || true' EXIT INT TERM
  for _ in $(seq 1 120); do
    up "$COMFY_FALLBACK_PORT" && break
    kill -0 "$COMFY_PID" 2>/dev/null || { echo "ComfyUI died on startup; see $LOG" >&2; exit 1; }
    sleep 2
  done
  up "$COMFY_FALLBACK_PORT" || { echo "ComfyUI did not come up; see $LOG" >&2; exit 1; }
  URL="http://127.0.0.1:$COMFY_FALLBACK_PORT"
fi

export COMFY_URL="$URL"
# Not exec: keep this shell alive so the EXIT trap still stops the headless ComfyUI.
python3 "$HERE/bridge.py" &
BRIDGE_PID=$!
trap '[ -n "$COMFY_PID" ] && kill "$COMFY_PID" 2>/dev/null; kill "$BRIDGE_PID" 2>/dev/null || true' EXIT INT TERM
wait "$BRIDGE_PID"
