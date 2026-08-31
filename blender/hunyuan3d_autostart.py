"""Start the local Hunyuan3D endpoint when Blender opens, and keep BlenderMCP pointed at it.

Blender imports every module in scripts/startup at launch. This one:
  1. starts this checkout's serve.sh if nothing is listening on :8081
     (bridge only — ComfyUI boots lazily on the first generation request), and
  2. wires the BlenderMCP addon's Hunyuan3D panel to it for every .blend that is opened,
     since those settings are scene properties and do not travel between files.

Nothing here blocks startup; failures are logged to the console and otherwise ignored.
Set BLENDERMCP_HUNYUAN3D_AUTOSTART=0 in Blender's environment to disable.
"""

import atexit
import os
import socket
import subprocess

import bpy
from bpy.app.handlers import persistent

# Resolve the checkout from this file's real location (works when symlinked into
# Blender's scripts/startup, which is the recommended install), env var wins.
SERVICE_DIR = os.environ.get("HY3D_SERVICE_DIR") or os.path.dirname(
    os.path.dirname(os.path.realpath(__file__)))
SERVE_SH = os.path.join(SERVICE_DIR, "serve.sh")
API_URL = os.environ.get("BLENDERMCP_HUNYUAN3D_API_URL", "http://localhost:8081")
PORT = int(API_URL.rsplit(":", 1)[-1].split("/")[0] or 8081)
ENABLED = os.environ.get("BLENDERMCP_HUNYUAN3D_AUTOSTART", "1") != "0"
# Texture generation works locally now (MLX port of Hunyuan3D-Paint), so it is on by default.
# It costs ~70-90s on top of ~11s of shape; set this to 0 for shape-only speed.
TEXTURE = os.environ.get("BLENDERMCP_HUNYUAN3D_TEXTURE", "1") != "0"
# Panel defaults, matched to the bridge's default "high" quality preset: octree 384 and 40 shape
# steps. On MLX that is ~45s of shape, and it resolves detail the 256/20 defaults merge together.
OCTREE = int(os.environ.get("BLENDERMCP_HUNYUAN3D_OCTREE", "384"))
STEPS = int(os.environ.get("BLENDERMCP_HUNYUAN3D_STEPS", "40"))
GUIDANCE = float(os.environ.get("BLENDERMCP_HUNYUAN3D_GUIDANCE", "5.5"))

_proc = None


def _listening(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _start():
    global _proc
    if not ENABLED or not os.path.isfile(SERVE_SH):
        return
    if _listening(PORT):
        print(f"[hunyuan3d] endpoint already up on :{PORT}")
        return
    logdir = os.path.join(SERVICE_DIR, "logs")
    os.makedirs(logdir, exist_ok=True)
    try:
        with open(os.path.join(logdir, "autostart.log"), "ab") as fh:
            _proc = subprocess.Popen(
                [SERVE_SH],
                cwd=SERVICE_DIR,
                stdout=fh,
                stderr=fh,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        print(f"[hunyuan3d] started {SERVE_SH} (pid {_proc.pid})")
    except Exception as e:  # never let this break Blender's startup
        print(f"[hunyuan3d] could not start the endpoint: {e}")


@atexit.register
def _stop():
    # Only stop what we started; a manually-run serve.sh keeps going.
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()


def _configure(scene):
    if not hasattr(scene, "blendermcp_use_hunyuan3d"):
        return  # BlenderMCP addon not enabled in this session
    scene.blendermcp_use_hunyuan3d = True
    scene.blendermcp_hunyuan3d_mode = "LOCAL_API"
    scene.blendermcp_hunyuan3d_api_url = API_URL
    scene.blendermcp_hunyuan3d_texture = TEXTURE
    scene.blendermcp_hunyuan3d_octree_resolution = OCTREE
    scene.blendermcp_hunyuan3d_num_inference_steps = STEPS
    scene.blendermcp_hunyuan3d_guidance_scale = GUIDANCE


@persistent
def _on_load(_dummy):
    try:
        _configure(bpy.context.scene)
    except Exception as e:
        print(f"[hunyuan3d] could not configure the panel: {e}")


def _configure_current():
    # Runs once after startup, when scenes and addon properties exist.
    for scene in bpy.data.scenes:
        try:
            _configure(scene)
        except Exception as e:
            print(f"[hunyuan3d] could not configure {scene.name}: {e}")
    return None


def register():
    if not ENABLED:
        return
    _start()
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_configure_current, first_interval=2.0)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_configure_current):
        bpy.app.timers.unregister(_configure_current)
    _stop()
