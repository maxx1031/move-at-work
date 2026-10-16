"""Shared helpers for video-generation backends (see standup_agent/BACKENDS.md)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

import numpy as np
from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_job(path):
    with open(path) as f:
        job = json.load(f)
    job.setdefault("negative_prompt", "")
    job.setdefault("num_frames", 81)
    job.setdefault("fps", 24)
    job.setdefault("width", 704)
    job.setdefault("height", 1280)
    job.setdefault("seed", 42)
    job.setdefault("first_frame", None)
    job.setdefault("options", {})  # optional, backend-specific overrides
    os.makedirs(job["out_dir"], exist_ok=True)
    return job


def opt(job, key, default=None, env=None):
    """Option lookup: job['options'][key] > env var > default."""
    if key in job.get("options", {}):
        return job["options"][key]
    if env and os.environ.get(env) not in (None, ""):
        return os.environ[env]
    return default


def round_4n1(n, lo=5, hi=81):
    n = max(lo, min(hi, int(n)))
    return (n - 1) // 4 * 4 + 1


def portrait_size(job, long_side=832, short_side=480):
    """Pick (W, H) at the model's native area with the job's orientation."""
    if job["height"] >= job["width"]:
        return short_side, long_side
    return long_side, short_side


def fit_cover(img, W, H):
    """Resize+center-crop a PIL image to exactly W x H."""
    img = img.convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    l = (img.width - W) // 2
    t = (img.height - H) // 2
    return img.crop((l, t, l + W, t + H))


def fit_pad(img, W, H, color=(255, 255, 255)):
    """Resize (keep aspect) and pad onto a W x H canvas (VACE-style white canvas)."""
    img = img.convert("RGB")
    s = min(W / img.width, H / img.height)
    img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    c = Image.new("RGB", (W, H), color)
    c.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return c


def write_video(frames, path, fps_in, fps_out):
    """frames: list of PIL images or HxWx3 uint8 arrays. Writes H.264 mp4 at fps_out
    (time-preserving resample of a clip that was generated at fps_in)."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    arr = [np.asarray(f.convert("RGB") if isinstance(f, Image.Image) else f, dtype=np.uint8) for f in frames]
    h, w = arr[0].shape[:2]
    cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(fps_in), "-i", "-", "-vf", f"fps={fps_out}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "18", "-movflags", "+faststart", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for a in arr:
        p.stdin.write(a.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg failed")


def contact_sheet(frames, path, n=6, height=360):
    idx = np.linspace(0, len(frames) - 1, n).round().astype(int)
    ims = []
    for i in idx:
        f = frames[i]
        f = f if isinstance(f, Image.Image) else Image.fromarray(f)
        ims.append(f.resize((round(f.width * height / f.height), height)))
    W = sum(i.width for i in ims)
    c = Image.new("RGB", (W, height))
    x = 0
    for im in ims:
        c.paste(im, (x, 0))
        x += im.width
    c.save(path)


def write_meta(job, **kw):
    meta = {"backend": None, "model_ids": [], "resolution": None, "num_frames": None, "fps": job["fps"],
            "seed": job["seed"], "runtime_s": None, "peak_vram_gb": None, "notes": ""}
    meta.update(kw)
    with open(os.path.join(job["out_dir"], "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


def main_wrapper(fn):
    """Run fn(job) and implement the exit-code / error.txt contract."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    a = ap.parse_args()
    job = load_job(a.job)
    t0 = time.time()
    try:
        fn(job, t0)
    except BaseException as e:  # noqa
        with open(os.path.join(job["out_dir"], "error.txt"), "w") as f:
            f.write(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        traceback.print_exc()
        sys.exit(1)


def segment_subjects(paths, out_dir, birefnet_dir=None, device="cuda"):
    """Cut the subject out of each reference photo with BiRefNet and paste it on white,
    cropped to its bbox (+margin). Returns list of saved png paths. Frees the model after."""
    import torch
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation
    birefnet_dir = birefnet_dir or os.path.join(REPO, "checkpoints", "vace", "BiRefNet")
    model = AutoModelForImageSegmentation.from_pretrained(birefnet_dir, trust_remote_code=True).to(device).eval().half()
    tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    outs = []
    for i, p in enumerate(paths):
        img = Image.open(p).convert("RGB")
        with torch.no_grad():
            pred = model(tf(img).unsqueeze(0).to(device).half())[-1].sigmoid().float().cpu()[0, 0]
        m = Image.fromarray((pred.numpy() * 255).astype(np.uint8)).resize(img.size, Image.BILINEAR)
        ma = np.asarray(m).astype(np.float32)[..., None] / 255.0
        comp = (np.asarray(img).astype(np.float32) * ma + 255.0 * (1 - ma)).astype(np.uint8)
        ys, xs = np.where(ma[..., 0] > 0.5)
        comp = Image.fromarray(comp)
        if len(xs):
            mx = int(0.05 * img.width)
            my = int(0.05 * img.height)
            comp = comp.crop((max(0, xs.min() - mx), max(0, ys.min() - my),
                              min(img.width, xs.max() + mx), min(img.height, ys.max() + my)))
        op = os.path.join(out_dir, f"ref_seg_{i}.png")
        comp.save(op)
        outs.append(op)
    del model
    torch.cuda.empty_cache()
    return outs
