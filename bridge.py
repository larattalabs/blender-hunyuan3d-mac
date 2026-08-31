#!/usr/bin/env python3
"""Hunyuan3D local API shim for the BlenderMCP addon.

Speaks the contract that blender_mcp_addon.create_hunyuan_job_local_site expects:

    POST /generate  {text?, image(base64|path)?, octree_resolution, num_inference_steps,
                     guidance_scale, texture}
    -> 200 with raw GLB bytes, or non-200 with a plain-text explanation.

Backed by ComfyUI's native Hunyuan3D v2 nodes (image -> shape). Shape only:
texture generation needs Tencent's CUDA rasterizer, which does not exist on Apple Silicon.

Stdlib only, so any python3 can run it.
"""

import atexit
import base64
import io
import json
import os
import random
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
# When Blender autostarts us we do NOT want ComfyUI resident too: boot it on the first
# real request instead. serve.sh sets these; HY3D_AUTOSTART_COMFY=0 disables the behaviour.
AUTOSTART_COMFY = os.environ.get("HY3D_AUTOSTART_COMFY", "1") != "0"
COMFY_DIR = os.path.expanduser(os.environ.get("COMFY_DIR", "~/ComfyUI-Installs/ComfyUI/ComfyUI"))
COMFY_ALT_PORT = os.environ.get("COMFY_ALT_PORT", "8189")
COMFY_BOOT_TIMEOUT = int(os.environ.get("HY3D_COMFY_BOOT_TIMEOUT", "300"))
# Only used by the optional ComfyUI fallback; the default is where Comfy Desktop keeps it.
COMFY_MODEL_PATHS = os.path.expanduser(os.environ.get(
    "COMFY_EXTRA_MODEL_PATHS", "~/Library/Application Support/Comfy Desktop/shared_model_paths.yaml"))
COMFY_INPUT_DIR = os.path.expanduser(os.environ.get("COMFY_INPUT_DIR", "~/ComfyUI-Shared/input"))
COMFY_OUTPUT_DIR = os.path.expanduser(os.environ.get("COMFY_OUTPUT_DIR", "~/ComfyUI-Shared/output"))
# Hunyuan3D expects a background-free, tightly-cropped subject. ComfyUI's nodes don't do that
# step, and skipping it produces garbage meshes, so we do it here for every request.
PREPROCESS = os.environ.get("HY3D_PREPROCESS", "1") != "0"
# Texture generation: Hunyuan3D-Paint ported to MLX (github.com/ZimengXiong/Hunyuan3D-Swift).
# Shape still comes from ComfyUI; `hy3d paint` textures the mesh afterwards.
PAINT_BIN = os.path.expanduser(os.environ.get(
    "HY3D_PAINT_BIN", "~/AI/hunyuan3d-mlx/.build/release/hy3d"))
PAINT_WEIGHTS = os.path.expanduser(os.environ.get(
    "HY3D_PAINT_WEIGHTS", "~/AI/hunyuan3d-mlx/weights/paint"))
PAINT_MODEL = os.environ.get("HY3D_PAINT_MODEL", "rgb")   # rgb | pbr
PAINT_TIMEOUT = int(os.environ.get("HY3D_PAINT_TIMEOUT", "1800"))
# Shape backend: the same MLX binary does shape in ~11s vs ~180s through ComfyUI, so it is the
# default when its weights are present; ComfyUI stays as the fallback.
SHAPE_BACKEND = os.environ.get("HY3D_SHAPE_BACKEND", "auto")   # auto | mlx | comfy
SHAPE_WEIGHTS = os.path.expanduser(os.environ.get(
    "HY3D_SHAPE_WEIGHTS", "~/AI/hunyuan3d-mlx/weights/shape-small"))
SHAPE_WEIGHTS_LARGE = os.path.expanduser(os.environ.get(
    "HY3D_SHAPE_WEIGHTS_LARGE", "~/AI/hunyuan3d-mlx/weights/shape-large"))
SHAPE_TIMEOUT = int(os.environ.get("HY3D_SHAPE_TIMEOUT", "900"))

# Quality presets. "high" is the default: the larger shape model plus higher paint render and
# texture resolution, which is the difference between merged blobs and separated parts.
# Measured on a lantern: high ~6.5min, fast ~95s. Set HY3D_QUALITY=fast while iterating.
# Individual knobs below override whichever preset is active.
QUALITY = os.environ.get("HY3D_QUALITY", "high").lower()
PRESETS = {
    "fast": {"shape": "small", "res": 512, "paint_steps": 15, "tex": 2048},
    "high": {"shape": "large", "res": 768, "paint_steps": 25, "tex": 4096},
}
HERE = os.path.dirname(os.path.abspath(__file__))
CKPT = os.environ.get("HY3D_CKPT", "hunyuan3d-dit-v2_fp16.safetensors")
BIND_HOST = os.environ.get("HY3D_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("HY3D_PORT", "8081"))
# Generation on MPS is minutes, not seconds.
JOB_TIMEOUT = int(os.environ.get("HY3D_TIMEOUT", "1800"))

CLIENT_ID = str(uuid.uuid4())

_comfy_proc = None
_comfy_lock = threading.Lock()


def log(msg):
    print(f"[hy3d-bridge] {msg}", flush=True)


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


def _comfy_alive(url, timeout=3):
    try:
        with urllib.request.urlopen(f"{url}/system_stats", timeout=timeout):
            return True
    except Exception:
        return False


def ensure_comfy():
    """Point COMFY_URL at a live ComfyUI, starting a headless one if allowed."""
    global COMFY_URL, _comfy_proc
    if _comfy_alive(COMFY_URL):
        return
    with _comfy_lock:
        if _comfy_alive(COMFY_URL):
            return
        for port in ("8188", COMFY_ALT_PORT):
            url = f"http://127.0.0.1:{port}"
            if _port_open(port) and _comfy_alive(url):
                COMFY_URL = url
                log(f"using ComfyUI already running on :{port}")
                return
        if not AUTOSTART_COMFY:
            raise RuntimeError(f"No ComfyUI reachable at {COMFY_URL} and autostart is off")

        logpath = os.path.join(HERE, "logs", "comfy-headless.log")
        os.makedirs(os.path.dirname(logpath), exist_ok=True)
        cmd = [
            os.path.join(COMFY_DIR, ".venv", "bin", "python"), "main.py",
            "--listen", "127.0.0.1", "--port", str(COMFY_ALT_PORT), "--disable-auto-launch",
            "--extra-model-paths-config", COMFY_MODEL_PATHS,
            "--input-directory", COMFY_INPUT_DIR,
            "--output-directory", COMFY_OUTPUT_DIR,
        ]
        log(f"starting headless ComfyUI on :{COMFY_ALT_PORT} (log: {logpath})")
        with open(logpath, "ab") as fh:
            _comfy_proc = subprocess.Popen(cmd, cwd=COMFY_DIR, stdout=fh, stderr=fh,
                                           stdin=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{COMFY_ALT_PORT}"
        deadline = time.time() + COMFY_BOOT_TIMEOUT
        while time.time() < deadline:
            if _comfy_proc.poll() is not None:
                raise RuntimeError(f"ComfyUI exited on startup; see {logpath}")
            if _comfy_alive(url):
                COMFY_URL = url
                log("headless ComfyUI ready")
                return
            time.sleep(2)
        raise RuntimeError(f"ComfyUI did not come up within {COMFY_BOOT_TIMEOUT}s; see {logpath}")


@atexit.register
def _stop_comfy():
    if _comfy_proc is not None and _comfy_proc.poll() is None:
        log("stopping the ComfyUI we started")
        _comfy_proc.terminate()


def comfy_get(path, timeout=30):
    with urllib.request.urlopen(f"{COMFY_URL}{path}", timeout=timeout) as r:
        return r.read()


def comfy_post_json(path, payload, timeout=60):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{COMFY_URL}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # ComfyUI puts the useful validation detail in the body, not the status line.
        raise RuntimeError(f"ComfyUI {path} -> HTTP {e.code}: {e.read().decode(errors='replace')[:1200]}") from None


def comfy_upload_image(name, raw):
    """POST /upload/image as multipart/form-data."""
    boundary = "----hy3dbridge" + uuid.uuid4().hex
    body = io.BytesIO()

    def w(s):
        body.write(s.encode() if isinstance(s, str) else s)

    w(f"--{boundary}\r\n")
    w(f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n')
    w("Content-Type: application/octet-stream\r\n\r\n")
    w(raw)
    w(f"\r\n--{boundary}\r\n")
    w('Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n')
    w(f"--{boundary}--\r\n")

    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image",
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    subfolder = out.get("subfolder") or ""
    return f"{subfolder}/{out['name']}" if subfolder else out["name"]


def build_prompt(image_ref, octree_resolution, steps, cfg, seed):
    return {
        "1": {"class_type": "ImageOnlyCheckpointLoader", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image_ref}},
        "3": {
            "class_type": "CLIPVisionEncode",
            "inputs": {"clip_vision": ["1", 1], "image": ["2", 0], "crop": "none"},
        },
        "4": {"class_type": "Hunyuan3Dv2Conditioning", "inputs": {"clip_vision_output": ["3", 0]}},
        "5": {"class_type": "EmptyLatentHunyuan3Dv2", "inputs": {"resolution": 3072, "batch_size": 1}},
        "6": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 1.0}},
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["6", 0],
                "positive": ["4", 0],
                "negative": ["4", 1],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecodeHunyuan3D",
            "inputs": {
                "samples": ["7", 0],
                "vae": ["1", 2],
                "num_chunks": 8000,
                "octree_resolution": octree_resolution,
            },
        },
        "9": {
            "class_type": "VoxelToMesh",
            "inputs": {"voxel": ["8", 0], "algorithm": "surface net", "threshold": 0.6},
        },
        "10": {
            "class_type": "SaveGLB",
            "inputs": {"mesh": ["9", 0], "filename_prefix": "mesh/hy3d_blender"},
        },
    }


def run_job(prompt):
    res = comfy_post_json("/prompt", {"prompt": prompt, "client_id": CLIENT_ID})
    if "prompt_id" not in res:
        raise RuntimeError(f"ComfyUI rejected the job: {json.dumps(res)[:600]}")
    prompt_id = res["prompt_id"]
    log(f"queued prompt {prompt_id}")

    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        hist = json.loads(comfy_get(f"/history/{prompt_id}") or b"{}")
        entry = hist.get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error" or (
            status.get("completed") is False and status.get("status_str") == "error"
        ):
            raise RuntimeError(f"ComfyUI job failed: {json.dumps(status)[:800]}")
        if not status.get("completed"):
            continue
        for node_out in entry.get("outputs", {}).values():
            for f in node_out.get("3d", []) or []:
                q = urllib.parse.urlencode(
                    {
                        "filename": f["filename"],
                        "subfolder": f.get("subfolder", ""),
                        "type": f.get("type", "output"),
                    }
                )
                log(f"done: {f.get('subfolder','')}/{f['filename']}")
                return comfy_get(f"/view?{q}", timeout=300)
        raise RuntimeError("Job completed but produced no GLB output")
    raise RuntimeError(f"Timed out after {JOB_TIMEOUT}s waiting for ComfyUI")


def preprocess(raw):
    """Matte to white + crop square via prep_image.py (needs PIL/numpy -> ComfyUI's python)."""
    py = os.path.join(COMFY_DIR, ".venv", "bin", "python")
    script = os.path.join(HERE, "prep_image.py")
    if not (os.path.exists(py) and os.path.exists(script)):
        log("preprocess skipped: prep_image.py or ComfyUI's python missing")
        return raw
    tmpdir = tempfile.mkdtemp(prefix="hy3dprep")
    src, dst = os.path.join(tmpdir, "in.png"), os.path.join(tmpdir, "out.png")
    try:
        with open(src, "wb") as fh:
            fh.write(raw)
        out = subprocess.run([py, script, src, dst], capture_output=True, timeout=120)
        if out.returncode != 0 or not os.path.exists(dst):
            log(f"preprocess failed, using the image as supplied: {out.stderr.decode()[:300]}")
            return raw
        log(out.stdout.decode().strip() or "prep: ok")
        with open(dst, "rb") as fh:
            return fh.read()
    except Exception as e:
        log(f"preprocess error, using the image as supplied: {e}")
        return raw
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def mlx_runtime_ok():
    """hy3d needs mlx.metallib sitting next to the binary (SwiftPM doesn't build one)."""
    return os.path.exists(PAINT_BIN) and os.path.exists(
        os.path.join(os.path.dirname(PAINT_BIN), "mlx.metallib"))


def paint_available():
    return mlx_runtime_ok() and os.path.isdir(PAINT_WEIGHTS)


def preset(name=None):
    return PRESETS.get((name or QUALITY), PRESETS["fast"])


def shape_weights_for(which):
    """'large' falls back to small if the large weights were never downloaded."""
    if which == "large" and os.path.isdir(SHAPE_WEIGHTS_LARGE):
        return SHAPE_WEIGHTS_LARGE
    return SHAPE_WEIGHTS


def mlx_shape_available():
    return mlx_runtime_ok() and os.path.isdir(SHAPE_WEIGHTS)


def shape_backend():
    if SHAPE_BACKEND == "mlx":
        return "mlx"
    if SHAPE_BACKEND == "comfy":
        return "comfy"
    return "mlx" if mlx_shape_available() else "comfy"


def shape_mlx(image_bytes, octree, steps, guidance, weights=None):
    """hy3d shape — MLX, ~11s, no ComfyUI involved."""
    tmpdir = tempfile.mkdtemp(prefix="hy3dshape")
    img, out = os.path.join(tmpdir, "ref.png"), os.path.join(tmpdir, "mesh.glb")
    try:
        with open(img, "wb") as fh:
            fh.write(image_bytes)
        w = weights or SHAPE_WEIGHTS
        cmd = [PAINT_BIN, "shape", img, "-o", out, "--weights", w,
               "--steps", str(steps), "--octree", str(octree), "--guidance", str(guidance)]
        log(f"shape (mlx, {os.path.basename(w)}): octree={octree} steps={steps} guidance={guidance}")
        r = subprocess.run(cmd, capture_output=True, timeout=SHAPE_TIMEOUT)
        if r.returncode != 0 or not os.path.exists(out):
            tail = (r.stderr or r.stdout).decode(errors="replace")[-800:]
            raise RuntimeError(f"hy3d shape failed: {tail}")
        log((r.stdout.decode(errors="replace").strip().splitlines() or ["done"])[-1])
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def free_comfy_memory():
    """Paint peaks near 38GB; don't hold ComfyUI's models at the same time."""
    try:
        comfy_post_json("/free", {"unload_models": True, "free_memory": True}, timeout=30)
    except Exception as e:
        log(f"could not ask ComfyUI to free memory (continuing): {e}")


def paint_mesh(glb_bytes, image_bytes, model=None, res=None, steps=None, tex=None):
    """Texture a GLB with hy3d paint. Returns the textured GLB bytes."""
    model = (model or PAINT_MODEL).lower()
    if model not in ("rgb", "pbr"):
        raise RuntimeError(f"paint model must be 'rgb' or 'pbr', got {model!r}")
    tmpdir = tempfile.mkdtemp(prefix="hy3dpaint")
    mesh = os.path.join(tmpdir, "mesh.glb")
    img = os.path.join(tmpdir, "ref.png")
    out = os.path.join(tmpdir, "textured.glb")
    try:
        with open(mesh, "wb") as fh:
            fh.write(glb_bytes)
        with open(img, "wb") as fh:
            fh.write(image_bytes)
        if _comfy_alive(COMFY_URL, timeout=2):
            free_comfy_memory()
        cmd = [PAINT_BIN, "paint", mesh, img, "-o", out,
               "--weights", PAINT_WEIGHTS, "--model", model,
               "--res", str(res), "--steps", str(steps), "--tex", str(tex)]
        log(f"painting ({model}, res={res} steps={steps} tex={tex}) — this takes several minutes")
        r = subprocess.run(cmd, capture_output=True, timeout=PAINT_TIMEOUT)
        if r.returncode != 0 or not os.path.exists(out):
            tail = (r.stderr or r.stdout).decode(errors="replace")[-800:]
            raise RuntimeError(f"hy3d paint failed: {tail}")
        log(f"painted: {os.path.getsize(out)} bytes")
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        # hy3d paint --model pbr also drops <out>.albedo.png / .mr.png / .views.png next to the
        # output, so the directory is not empty: remove the tree, not the three files we named.
        shutil.rmtree(tmpdir, ignore_errors=True)


def demetalise_glb(glb):
    """Set metallicFactor to 0 on materials that have no metallic-roughness texture.

    hy3d's GLB writer omits the pbrMetallicRoughness factors, and glTF's spec default for
    metallicFactor is 1.0 — so every painted asset arrives as pure metal and mirrors whatever
    environment it is dropped into (a green pine renders sky-blue). PBR output carries a real
    MR texture and is left alone.
    """
    try:
        if glb[:4] != b"glTF":
            return glb
        length = int.from_bytes(glb[8:12], "little")
        off, chunks = 12, []
        while off < length:
            clen = int.from_bytes(glb[off:off + 4], "little")
            ctype = glb[off + 4:off + 8]
            chunks.append([ctype, bytearray(glb[off + 8:off + 8 + clen])])
            off += 8 + clen
        for i, (ctype, data) in enumerate(chunks):
            if ctype != b"JSON":
                continue
            doc = json.loads(data.decode("utf-8"))
            touched = 0
            for mat in doc.get("materials", []):
                pbr = mat.setdefault("pbrMetallicRoughness", {})
                if "metallicRoughnessTexture" in pbr:
                    continue
                if pbr.get("metallicFactor") != 0:
                    pbr["metallicFactor"] = 0
                    touched += 1
            if not touched:
                return glb
            out = json.dumps(doc, separators=(",", ":")).encode("utf-8")
            out += b" " * (-len(out) % 4)
            chunks[i][1] = bytearray(out)
            log(f"patched {touched} material(s) to metallicFactor 0")
        body = b"".join(len(d).to_bytes(4, "little") + t + bytes(d) for t, d in chunks)
        return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body
    except Exception as e:
        log(f"could not patch the GLB materials, returning it unchanged: {e}")
        return glb


def resolve_image(image):
    """The addon sends base64; accept a local path or http(s) URL too."""
    if re.match(r"^https?://", image, re.IGNORECASE):
        with urllib.request.urlopen(image, timeout=60) as r:
            return r.read()
    if os.path.isfile(image):
        with open(image, "rb") as fh:
            return fh.read()
    return base64.b64decode(image)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        log(fmt % a)

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/health", "/"):
            return self._send(404, "not found")
        comfy_ok = _comfy_alive(COMFY_URL, timeout=5)
        detail = "" if comfy_ok else "not running (starts on the first generate request)"
        self._send(
            200,
            json.dumps(
                {
                    "ok": comfy_ok,
                    "comfy_url": COMFY_URL,
                    "checkpoint": CKPT,
                    "shape_backend": shape_backend(),
                    "quality": {"preset": QUALITY, **preset(),
                                "shape_large_available": os.path.isdir(SHAPE_WEIGHTS_LARGE)},
                    "mode": "shape + MLX texture" if paint_available() else "shape only (paint not installed)",
                    "paint": {"available": paint_available(), "binary": PAINT_BIN,
                              "weights": PAINT_WEIGHTS, "model": PAINT_MODEL},
                    "comfy_autostart": AUTOSTART_COMFY,
                    "preprocess": PREPROCESS,
                    "comfy_error": detail,
                },
                indent=2,
            ),
            "application/json",
        )

    def do_POST(self):
        if self.path.split("?")[0] != "/generate":
            return self._send(404, "not found")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, f"Bad request body: {e}")

        image = data.get("image")
        text = data.get("text")
        if not image:
            msg = (
                "This local Hunyuan3D endpoint is image-to-3D only; no text prompt was accepted"
                f" (text was: {text!r}). Generate or pick a reference image first, then pass it"
                " as the image argument."
                if text
                else "No image supplied. This endpoint requires an image (base64, file path, or URL)."
            )
            return self._send(400, msg)

        want_texture = bool(data.get("texture"))
        if want_texture and not paint_available():
            return self._send(
                400,
                "Texture generation is not set up. It needs the MLX port of Hunyuan3D-Paint:\n"
                f"  binary:  {PAINT_BIN}\n  weights: {PAINT_WEIGHTS}\n"
                "Run ./setup.sh in the checkout (see README), or uncheck 'Generate Texture'.",
            )

        octree = int(data.get("octree_resolution") or 256)
        steps = int(data.get("num_inference_steps") or 20)
        cfg = float(data.get("guidance_scale") or 5.5)

        try:
            raw = resolve_image(image)
        except Exception as e:
            return self._send(400, f"Could not read the supplied image: {e}")

        if PREPROCESS and data.get("preprocess", True):
            raw = preprocess(raw)

        q = preset(data.get("quality"))
        backend = shape_backend()
        try:
            if backend == "mlx":
                glb = shape_mlx(raw, octree, steps, cfg,
                                weights=shape_weights_for(data.get("shape_model", q["shape"])))
            else:
                ensure_comfy()
                name = comfy_upload_image(f"hy3d_blender_{uuid.uuid4().hex[:8]}.png", raw)
                log(f"generate: image={name} octree={octree} steps={steps} cfg={cfg}")
                glb = run_job(build_prompt(name, octree, steps, cfg, random.randint(0, 2**31 - 1)))
        except urllib.error.URLError as e:
            return self._send(502, f"Cannot reach ComfyUI at {COMFY_URL}: {e}")
        except Exception as e:
            return self._send(500, f"Generation failed: {e}")

        if not glb:
            return self._send(500, "Generation produced an empty GLB")
        glb = demetalise_glb(glb)

        if want_texture:
            try:
                glb = paint_mesh(
                    glb, raw, data.get("paint_model"),
                    res=int(data.get("paint_res", os.environ.get("HY3D_PAINT_RES", q["res"]))),
                    steps=int(data.get("paint_steps", os.environ.get("HY3D_PAINT_STEPS", q["paint_steps"]))),
                    tex=int(data.get("paint_tex", os.environ.get("HY3D_PAINT_TEX", q["tex"]))))
            except Exception as e:
                return self._send(500, f"Shape succeeded but texturing failed: {e}")

        self._send(200, glb, "model/gltf-binary")


def main():
    # SIGTERM would skip atexit, orphaning a ComfyUI we started.
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log(f"comfy={COMFY_URL} ckpt={CKPT} autostart_comfy={AUTOSTART_COMFY}")
    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    log(f"listening on http://{BIND_HOST}:{BIND_PORT}  (POST /generate, GET /health)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("bye")


if __name__ == "__main__":
    main()
