#!/usr/bin/env python3
"""Crop a generated image into WeChat Official Account cover sizes.

WeChat shows two crops of an article cover:
  * 2.35:1 banner on the article card (recommended 900x383);
  * 1:1 thumbnail when the article is forwarded or shown in a list (recommended 500x500).

The script finds the content's bounding box (pixels that differ from the background colour,
sampled at the image corners) and centres both crops on it, so a model that did not place the
subject exactly in the middle still yields usable covers. It fails loudly when the content does
not fit a crop instead of silently cutting it off.

Usage:
  make_cover.py generated.png out_dir [--name wechat-cover] [--pad 0.08]
Writes <out_dir>/<name>.png (900x383) and <out_dir>/<name>-1x1.png (500x500) and prints a
JSON report.
"""
import argparse
import json
import pathlib
import sys

from PIL import Image, ImageChops

BANNER = (900, 383)
SQUARE = (500, 500)


def content_bbox(im, tolerance=24):
    """Bounding box of everything that differs from the corner background colour."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    bg = tuple(sorted(c[i] for c in corners)[1] for i in range(3))  # robust-ish median
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    mask = diff.point(lambda v: 255 if v > tolerance else 0)
    return mask.getbbox(), bg


def fit_crop(img_w, img_h, ratio, bbox, pad):
    """Largest-needed crop of the given aspect ratio centred on bbox (with padding)."""
    x0, y0, x1, y1 = bbox
    bw, bh = (x1 - x0) * (1 + 2 * pad), (y1 - y0) * (1 + 2 * pad)
    cw = max(bw, bh * ratio)
    ch = cw / ratio
    # never upscale beyond the source: shrink to fit the image, keeping the ratio
    if cw > img_w:
        cw, ch = img_w, img_w / ratio
    if ch > img_h:
        ch, cw = img_h, img_h * ratio
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = min(max(cx - cw / 2, 0), img_w - cw)
    top = min(max(cy - ch / 2, 0), img_h - ch)
    box = tuple(round(v) for v in (left, top, left + cw, top + ch))
    fits = box[0] <= x0 and box[1] <= y0 and box[2] >= x1 and box[3] >= y1
    return box, fits


def make(src, out_dir, name="wechat-cover", pad=0.15):
    im = Image.open(src).convert("RGB")
    bbox, bg = content_bbox(im)
    if bbox is None:
        raise SystemExit("image looks blank: no content differs from the background")
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"source": str(src), "source_size": im.size, "background": bg, "content_bbox": bbox,
              "outputs": [], "warnings": []}
    for suffix, size in (("", BANNER), ("-1x1", SQUARE)):
        box, fits = fit_crop(im.width, im.height, size[0] / size[1], bbox, pad)
        path = out_dir / f"{name}{suffix}.png"
        im.crop(box).resize(size, Image.LANCZOS).save(path)
        report["outputs"].append({"path": str(path), "size": size, "crop_box": box, "content_fits": fits})
        if not fits:
            report["warnings"].append(
                f"{path.name}: content does not fit a {size[0]}:{size[1]} crop; regenerate the image "
                "with the subject more compact and centred")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="generated cover image (any size, ideally wide such as 1536x1024)")
    ap.add_argument("out_dir", help="directory for the cropped covers")
    ap.add_argument("--name", default="wechat-cover", help="output base name (default wechat-cover)")
    ap.add_argument("--pad", type=float, default=0.15, help="padding around content, fraction of its size")
    args = ap.parse_args(argv)
    report = make(args.src, args.out_dir, args.name, args.pad)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 1 if report["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())
