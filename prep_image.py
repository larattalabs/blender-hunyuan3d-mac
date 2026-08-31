#!/usr/bin/env python
"""Prepare a reference image for Hunyuan3D: matte to white, crop tight, pad square.

    <comfy venv python> prep_image.py in.png out.png

Hunyuan3D's official pipeline removes the background before conditioning; ComfyUI's
native nodes do not. Skipping it produces spectacular failures — a flat-grey-background
mushroom came back as floating sheets, and at higher steps as a solid cube. The same
image matted onto white and cropped came back perfect.

What this does, in order:
  * alpha channel present  -> composite it over white
  * else flat background   -> corner-sampled key colour, everything close to it turns white
  * else (busy photo)      -> left alone; a real matting model would be needed
  * crop to the subject, pad to a square with ~8% margin, resize to 768.

Needs PIL + numpy, which is why it runs under ComfyUI's interpreter rather than the bridge's.
"""
import sys

import numpy as np
from PIL import Image

TARGET = 768
MARGIN = 1.16          # square side relative to the subject's longest edge
KEY_TOLERANCE = 30     # per-pixel |RGB - background| sum below this counts as background
FLAT_BG_STD = 12.0     # corner colour spread above this means "not a flat background"


def foreground_mask(rgb):
    corners = np.concatenate([
        rgb[:12, :12].reshape(-1, 3), rgb[:12, -12:].reshape(-1, 3),
        rgb[-12:, :12].reshape(-1, 3), rgb[-12:, -12:].reshape(-1, 3),
    ])
    if corners.std(axis=0).max() > FLAT_BG_STD:
        return None  # busy background: don't guess
    key = np.median(corners, axis=0)
    return np.abs(rgb - key).sum(axis=2) > KEY_TOLERANCE


def main(src, dst):
    im = Image.open(src)
    im = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")

    if im.mode == "RGBA":
        arr = np.asarray(im).astype(int)
        alpha = arr[..., 3] / 255.0
        rgb = arr[..., :3] * alpha[..., None] + 255 * (1 - alpha[..., None])
        mask = alpha > 0.5
    else:
        rgb = np.asarray(im).astype(int)
        mask = foreground_mask(rgb)
        if mask is not None:
            rgb = np.where(mask[..., None], rgb, 255)

    flat = Image.fromarray(rgb.astype(np.uint8), "RGB")

    if mask is not None and mask.any() and mask.mean() < 0.95:
        ys, xs = np.where(mask)
        flat = flat.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        note = f"matted+cropped to {flat.size}"
    else:
        note = "left as-is (no flat background found)"

    side = int(max(flat.size) * MARGIN)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(flat, ((side - flat.size[0]) // 2, (side - flat.size[1]) // 2))
    canvas.resize((TARGET, TARGET), Image.LANCZOS).save(dst)
    print(f"prep: {note} -> {TARGET}x{TARGET}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
