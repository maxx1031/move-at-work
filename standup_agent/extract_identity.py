"""Make "user photos" from the user's own workout videos: person crops from a varied set of frames.

python -m standup_agent.extract_identity --videos data/653704674.mp4 data/1.mp4 --out data/user_photos --n 4

For each video, frames are sampled, the (single, largest) person is detected, and frames are scored by person
size and sharpness. Picks are spread over time and across videos so the identity set shows different angles.
"""
import argparse
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from .reasoner import PersonDetector, sharpness


@torch.no_grad()
def person_box(det: PersonDetector, im: Image.Image, score=0.8):
    x = det.tf(im).to(det.device)
    o = det.model([x])[0]
    keep = (o["labels"] == 1) & (o["scores"] > score)
    if not keep.any():
        return None
    b = o["boxes"][keep]
    areas = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return b[int(areas.argmax())].cpu().numpy()


def candidates(video, det, n_samples=40):
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for i in np.linspace(0, total - 1, min(n_samples, total)).round().astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if not ok:
            continue
        im = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        b = person_box(det, im)
        if b is None:
            continue
        x0, y0, x1, y1 = b
        if (y1 - y0) / im.height < 0.3:  # person too small to carry identity
            continue
        pad = 0.08 * (y1 - y0)
        crop = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
        out.append(dict(video=str(video), frame=int(i), t=i / max(total - 1, 1), crop=crop,
                        score=np.log1p(sharpness(crop)) * (y1 - y0) / im.height))
    cap.release()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="+", required=True)
    ap.add_argument("--out", default="data/user_photos")
    ap.add_argument("--n", type=int, default=4, help="photos to keep in total")
    a = ap.parse_args()
    det = PersonDetector()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("user_*.jpg"):
        old.unlink()
    per_video = [candidates(v, det) for v in a.videos]
    # round-robin over videos; within a video take the best-scoring frame from each time segment
    k_per = [a.n // len(a.videos) + (1 if i < a.n % len(a.videos) else 0) for i in range(len(a.videos))]
    picks = []
    for cands, k in zip(per_video, k_per):
        for seg in range(k):
            lo, hi = seg / k, (seg + 1) / k
            pool = [c for c in cands if lo <= c["t"] <= hi] or cands
            if pool:
                picks.append(max(pool, key=lambda c: c["score"]))
    for j, c in enumerate(picks):
        p = out / f"user_{j}.jpg"
        c["crop"].save(p, quality=95)
        print(f"{p}: {Path(c['video']).name} frame {c['frame']} ({c['crop'].width}x{c['crop'].height})")


if __name__ == "__main__":
    main()
