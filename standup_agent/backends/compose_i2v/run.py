"""compose_i2v backend: Qwen-Image-Edit-2509 composes the user into the scene frame, Wan2.2-TI2V-5B animates it.

    <wan22 python> standup_agent/backends/compose_i2v/run.py --job job.json

See standup_agent/BACKENDS.md for the job/output contract and NOTES.md in this folder for details.
Each stage runs in its own subprocess (same `wan22` env) so GPU memory is fully released between them.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# Wan2.2-TI2V-5B native portrait / landscape sizes (W, H).
WAN_SIZES = [(704, 1280), (1280, 704)]


class VramMonitor(threading.Thread):
    """Polls nvidia-smi for the visible GPU (covers child processes)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak_mb = 0
        self.stop = False
        vis = os.environ.get("CUDA_VISIBLE_DEVICES", "0").split(",")[0].strip() or "0"
        self.gpu = vis

    def run(self):
        while not self.stop:
            try:
                out = subprocess.run(["nvidia-smi", "-i", self.gpu, "--query-gpu=memory.used",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                     timeout=10).stdout.strip()
                self.peak_mb = max(self.peak_mb, int(out.splitlines()[0]))
            except Exception:
                pass
            time.sleep(1.0)


def run_stage(cmd, log_path):
    print("[compose_i2v] $", " ".join(cmd[:3]), "...", flush=True)
    with open(log_path, "a") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        with open(log_path) as f:
            tail = f.read()[-3000:]
        raise RuntimeError(f"stage failed (exit {p.returncode}): {cmd[1]}\n{tail}")


def fit_frame(src, dst, W, H):
    """Resize + center-crop an image to exactly W x H."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    im.crop((x, y, x + W, y + H)).save(dst)


def main():
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--wan_steps", type=int, default=50)
    ap.add_argument("--compose_steps", type=int, default=None)
    a = ap.parse_args()
    job = json.load(open(a.job))
    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "log.txt")
    mon = VramMonitor()
    mon.start()
    t0 = time.time()
    try:
        from PIL import Image
        sw, sh = Image.open(job["scene_frame"]).size
        W, H = WAN_SIZES[0] if sh >= sw else WAN_SIZES[1]
        seed = int(job.get("seed", 42))
        fps = int(job.get("fps", 24))
        nf = int(job.get("num_frames", 121))
        nf = max(5, (nf - 1) // 4 * 4 + 1)  # Wan needs 4n+1
        first = os.path.join(out_dir, "first_frame.png")
        notes = []
        t_compose = 0.0
        if job.get("first_frame"):
            fit_frame(job["first_frame"], first, W, H)
            notes.append("first_frame given; composition skipped")
        else:
            tc = time.time()
            cmd = [PY, os.path.join(HERE, "compose.py"), "--scene", job["scene_frame"],
                   "--identity", *job["identity_photos"], "--instruction", job["start_pose_instruction"],
                   "--out", first, "--width", str(W), "--height", str(H), "--seed", str(seed)]
            if a.compose_steps:
                cmd += ["--steps", str(a.compose_steps)]
            run_stage(cmd, log_path)
            fit_frame(first, first, W, H)
            t_compose = time.time() - tc
        tw = time.time()
        raw = os.path.join(out_dir, "wan_raw.mp4")
        run_stage([PY, os.path.join(HERE, "wan_ti2v.py"), "--image", first, "--prompt", job["prompt"],
                   "--negative_prompt", job.get("negative_prompt", "") or "", "--out", raw,
                   "--frame_num", str(nf), "--width", str(W), "--height", str(H), "--seed", str(seed),
                   "--fps", str(fps), "--steps", str(a.wan_steps)], log_path)
        t_wan = time.time() - tw
        # Re-encode to H.264 yuv420p so it plays everywhere.
        video = os.path.join(out_dir, "video.mp4")
        ff = shutil.which("ffmpeg") or "ffmpeg"
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", raw, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-crf", "18", "-r", str(fps), video])
        if r.returncode == 0:
            os.remove(raw)
        else:
            os.replace(raw, video)
        mon.stop = True
        meta = {
            "backend": "compose_i2v",
            "model_ids": (["Qwen/Qwen-Image-Edit-2509", "lightx2v/Qwen-Image-Lightning (Edit-2509 8-step)"]
                          if not job.get("first_frame") else []) + ["Wan-AI/Wan2.2-TI2V-5B"],
            "resolution": [W, H],
            "num_frames": nf,
            "fps": fps,
            "seed": seed,
            "runtime_s": round(time.time() - t0, 1),
            "runtime_compose_s": round(t_compose, 1),
            "runtime_wan_s": round(t_wan, 1),
            "peak_vram_gb": round(mon.peak_mb / 1024, 2),
            "notes": "; ".join(notes + [f"wan steps={a.wan_steps}, offload_model+convert_model_dtype+t5_cpu"]),
        }
        json.dump(meta, open(os.path.join(out_dir, "meta.json"), "w"), indent=2)
        print(json.dumps(meta, indent=2))
    except Exception as e:
        mon.stop = True
        with open(os.path.join(out_dir, "error.txt"), "w") as f:
            f.write(f"{e}\n\n{traceback.format_exc()}")
        print("[compose_i2v] FAILED:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
