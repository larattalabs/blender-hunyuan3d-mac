#!/usr/bin/env python3
"""Point the running Blender's BlenderMCP addon at the local Hunyuan3D endpoint.

Talks straight to the addon's socket server (127.0.0.1:9876) and sets the scene
properties the Hunyuan3D panel reads. Blender must be open with the addon's
server started. Scene properties live in the .blend, so re-run after opening a
different file (or set BLENDERMCP_HUNYUAN3D_API_URL in Blender's environment).
"""
import json
import os
import socket
import sys

HOST, PORT = "127.0.0.1", 9876
API_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8081"
TEXTURE = os.environ.get("BLENDERMCP_HUNYUAN3D_TEXTURE", "1") != "0"

CODE = f"""
import bpy
s = bpy.context.scene
s.blendermcp_use_hunyuan3d = True
s.blendermcp_hunyuan3d_mode = 'LOCAL_API'
s.blendermcp_hunyuan3d_api_url = {API_URL!r}
s.blendermcp_hunyuan3d_texture = {TEXTURE!r}
print('hunyuan3d ->', s.blendermcp_hunyuan3d_mode, s.blendermcp_hunyuan3d_api_url,
      'texture', s.blendermcp_hunyuan3d_texture)
"""


def send(cmd, params=None):
    s = socket.create_connection((HOST, PORT), timeout=1800)
    try:
        s.sendall(json.dumps({"type": cmd, "params": params or {}}).encode())
        chunks = b""
        s.settimeout(1800)
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            chunks += chunk
            try:
                return json.loads(chunks.decode())
            except json.JSONDecodeError:
                continue
        return json.loads(chunks.decode())
    finally:
        s.close()


if __name__ == "__main__":
    print(json.dumps(send("execute_code", {"code": CODE}), indent=2))
    print(json.dumps(send("get_hunyuan3d_status"), indent=2))
