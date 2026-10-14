"""Normalize phone photos (HEIC/JPG/PNG) or a video into a scene's images/ folder.

Usage:
  python pipeline/prepare_images.py --input ../data/photos_raw --scene ../data/office
  python pipeline/prepare_images.py --input clip.mov --scene ../data/office --fps 2
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}


def sharpness(rgb: np.ndarray) -> float:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_video(video: Path, fps: float, out_dir: Path) -> list[Path]:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps}", "-q:v", "2",
         str(out_dir / "f_%04d.jpg")],
        check=True,
    )
    return sorted(out_dir.glob("f_*.jpg"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="photo folder or a single video file")
    ap.add_argument("--scene", required=True, help="scene folder, e.g. ../data/office")
    ap.add_argument("--long-side", type=int, default=1024)
    ap.add_argument("--fps", type=float, default=2.0, help="frame rate when input is a video")
    ap.add_argument("--max-images", type=int, default=60)
    args = ap.parse_args()

    src = Path(args.input)
    scene = Path(args.scene)
    out = scene / "images"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    tmp = None
    if src.is_file() and src.suffix.lower() in VIDEO_EXTS:
        tmp = Path(tempfile.mkdtemp())
        files = extract_video(src, args.fps, tmp)
    else:
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not files:
        raise SystemExit(f"no images found in {src}")

    records = []
    for f in files:
        img = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
        w, h = img.size
        s = args.long_side / max(w, h)
        if s < 1:
            img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
        records.append({"source": f.name, "sourceSize": [w, h], "img": img,
                        "sharpness": sharpness(np.asarray(img))})

    # Too many frames: keep evenly spaced ones (photos are assumed to be in walking order).
    if len(records) > args.max_images:
        idx = np.linspace(0, len(records) - 1, args.max_images).round().astype(int)
        records = [records[i] for i in idx]

    sharp = np.array([r["sharpness"] for r in records])
    blur_cut = np.median(sharp) * 0.35
    manifest = []
    for i, r in enumerate(records):
        name = f"{i:04d}.jpg"
        r["img"].save(out / name, quality=95)
        manifest.append({"file": name, "source": r["source"], "sourceSize": r["sourceSize"],
                         "size": list(r["img"].size), "sharpness": round(r["sharpness"], 1),
                         "likelyBlurry": bool(r["sharpness"] < blur_cut)})

    (scene / "frames_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    if tmp:
        shutil.rmtree(tmp)

    sizes = {tuple(m["size"]) for m in manifest}
    print(f"{len(manifest)} images -> {out}  sizes={sizes}")
    blurry = [m["source"] for m in manifest if m["likelyBlurry"]]
    if blurry:
        print(f"possibly blurry ({len(blurry)}): {blurry}")
    if len(sizes) > 1:
        print("warning: mixed orientations/sizes; VGGT works best with one orientation")


if __name__ == "__main__":
    main()
