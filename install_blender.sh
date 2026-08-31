#!/usr/bin/env bash
# Symlink the autostart script into Blender's startup folder, so Blender launches the
# endpoint and wires the BlenderMCP panel by itself. Re-run after a Blender major upgrade.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT="$HOME/Library/Application Support/Blender"
[ -d "$SUPPORT" ] || { echo "no Blender support dir at $SUPPORT" >&2; exit 1; }

# Newest installed Blender version, unless one is named explicitly.
VERSION="${BLENDER_VERSION:-$(ls "$SUPPORT" | grep -E '^[0-9]+\.[0-9]+$' | sort -V | tail -1)}"
[ -n "$VERSION" ] || { echo "could not find a Blender version under $SUPPORT" >&2; exit 1; }

STARTUP="$SUPPORT/$VERSION/scripts/startup"
mkdir -p "$STARTUP"
ln -sfn "$HERE/blender/hunyuan3d_autostart.py" "$STARTUP/hunyuan3d_autostart.py"

echo "installed -> $STARTUP/hunyuan3d_autostart.py"
echo "restart Blender; the endpoint starts with it and the Hunyuan3D panel points at :8081"
