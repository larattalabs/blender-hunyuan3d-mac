"""Cut the subject out of a flat-background reference into RGBA.

    <python with PIL+numpy> prep_rgba.py in.png out.png

Same keying as prep_image.py, but writing alpha instead of compositing onto white. Useful for
pipelines that do their own cropping and skip background removal when given an alpha channel
(TRELLIS.2 does exactly that), which avoids depending on a gated background-removal model.
"""
import sys

import numpy as np
from PIL import Image

KEY_TOLERANCE = 30
FLAT_BG_STD = 12.0


def main(src, dst):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(int)
    corners = np.concatenate([a[:12, :12].reshape(-1, 3), a[:12, -12:].reshape(-1, 3),
                              a[-12:, :12].reshape(-1, 3), a[-12:, -12:].reshape(-1, 3)])
    if corners.std(axis=0).max() > FLAT_BG_STD:
        raise SystemExit("background is not flat enough to key — cut the subject out by hand")
    key = np.median(corners, axis=0)
    fg = (np.abs(a - key).sum(axis=2) > KEY_TOLERANCE).astype(np.uint8) * 255
    out = np.dstack([a.astype(np.uint8), fg])
    Image.fromarray(out, "RGBA").save(dst)
    print(f"prep_rgba: {fg.mean()/255:.1%} of pixels kept as foreground -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
