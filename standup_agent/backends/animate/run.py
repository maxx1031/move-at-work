"""animate backend: Wan2.2-Animate-14B in REPLACEMENT mode.

    <wan22 python> standup_agent/backends/animate/run.py --job job.json

Job fields used (see standup_agent/BACKENDS.md): driving_video (required, extra field: path to an exercise video,
e.g. the compose_i2v output), identity_photos[0] (reference character), prompt, fps, seed, out_dir.
Output keeps the driving video's motion and background, with the person replaced by the user's identity.
Stages (each a subprocess in the same `wan22` env):
  1. official preprocessing (YOLOv10 det + ViTPose-H wholebody pose, face crops, SAM2 person mask, bg video)
  2. Wan2.2-Animate-14B replacement inference with relighting LoRA (wan_animate.py: DiT group offloading)
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "../../.."))
WAN = os.path.join(ROOT, "third_party/Wan2.2")
CKPT = os.path.join(ROOT, "checkpoints/wan22/Wan2.2-Animate-14B")
PY = sys.executable
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("compose_i2v_run", os.path.join(HERE, "../compose_i2v/run.py"))
_ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ci)
VramMonitor, run_stage = _ci.VramMonitor, _ci.run_stage


def main():
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--steps", type=int, default=20)
    # mask grid subdivisions (official default 1x1 = person bbox rectangle; finer keeps more background, e.g. chair)
    ap.add_argument("--w_len", type=int, default=4)
    ap.add_argument("--h_len", type=int, default=8)
    a = ap.parse_args()
    job = json.load(open(a.job))
    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "log.txt")
    mon = VramMonitor()
    mon.start()
    t0 = time.time()
    try:
        drv = job.get("driving_video")
        if not drv or not os.path.exists(drv):
            raise ValueError(f"job.driving_video missing or not found: {drv}")
        ref = job["identity_photos"][0]
        fps = int(job.get("fps", 24))
        seed = int(job.get("seed", 42))
        from PIL import Image
        import decord
        vr = decord.VideoReader(drv)
        h, w = vr[0].shape[:2]
        # Model is trained at 1280x720-class area; keep the driving video's orientation.
        area = [720, 1280] if h >= w else [1280, 720]
        prep = os.path.join(out_dir, "process_results")
        tp = time.time()
        run_stage([PY, os.path.join(HERE, "preprocess.py"),
                   "--ckpt_path", os.path.join(CKPT, "process_checkpoint"), "--video_path", drv,
                   "--refer_path", ref, "--save_path", prep, "--resolution_area", *map(str, area),
                   "--fps", str(fps), "--iterations", "3", "--k", "7", "--w_len", str(a.w_len), "--h_len", str(a.h_len),
                   "--replace_flag"], log_path)
        t_prep = time.time() - tp
        tw = time.time()
        raw = os.path.join(out_dir, "wan_raw.mp4")
        run_stage([PY, os.path.join(HERE, "wan_animate.py"), "--src_root_path", prep, "--out", raw,
                   "--prompt", job.get("prompt", "") or "", "--seed", str(seed), "--fps", str(fps),
                   "--steps", str(a.steps)], log_path)
        t_wan = time.time() - tw
        video = os.path.join(out_dir, "video.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(fps), video])
        if r.returncode == 0:
            os.remove(raw)
        else:
            os.replace(raw, video)
        # first frame for reference
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-frames:v", "1",
                        os.path.join(out_dir, "first_frame.png")])
        vr2 = decord.VideoReader(video)
        mon.stop = True
        meta = {
            "backend": "animate",
            "model_ids": ["Wan-AI/Wan2.2-Animate-14B (+relighting LoRA, process_checkpoint)"],
            "resolution": [vr2[0].shape[1], vr2[0].shape[0]],
            "num_frames": len(vr2),
            "fps": fps,
            "seed": seed,
            "runtime_s": round(time.time() - t0, 1),
            "runtime_preprocess_s": round(t_prep, 1),
            "runtime_wan_s": round(t_wan, 1),
            "peak_vram_gb": round(mon.peak_mb / 1024, 2),
            "notes": f"replacement mode, driving_video={drv}, reference={ref}, steps={a.steps}, "
                     f"mask grid w_len={a.w_len} h_len={a.h_len}, DiT block-level group offload",
        }
        json.dump(meta, open(os.path.join(out_dir, "meta.json"), "w"), indent=2)
        print(json.dumps(meta, indent=2))
    except Exception as e:
        mon.stop = True
        with open(os.path.join(out_dir, "error.txt"), "w") as f:
            f.write(f"{e}\n\n{traceback.format_exc()}")
        print("[animate] FAILED:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
