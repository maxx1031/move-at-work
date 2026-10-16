"""VACE backend: identity reference images + scene-anchored video generation.

Uses DiffSynth-Studio (third_party/DiffSynth-Studio) to run Wan2.2-VACE-Fun-A14B (default) or Wan2.1-VACE-14B
with layer-wise CPU offload so that 14B models fit a 24 GB GPU.

    <env python> standup_agent/backends/vace/run.py --job job.json

Options (job["options"][key] or env var):
  model   (VACE_MODEL)  : "2.2-fun" (default, Wan2.2-VACE-Fun-A14B) | "14B" (Wan2.1-VACE-14B). 14B-class only.
  mode    (VACE_MODE)   : how the scene is anchored
      "auto"     : "ff" (best in tests: job.first_frame, else the scene frame, as frame 0)
      "ff"       : first frame (job.first_frame or scene frame) fixed, remaining frames generated
      "inpaint"  : scene frame held static as video; only a person box (mask_box) is generated
      "bgref"    : scene frame added as an extra reference image (no frame anchoring)
      "ref"      : identity references only (no scene anchoring)
  mask_box  : [x0,y0,x1,y1] normalised box regenerated in "inpaint" mode (default [0.0,0.25,1.0,1.0])
  segment   (VACE_SEGMENT): "1" (default) cut person out of identity photos with BiRefNet (white bg)
  max_refs  : max identity photos used (default 2)
  steps (VACE_STEPS) : sampling steps (default 40)
  size      : "480p" (default, 480x832 portrait) | "720p" (720x1280; 14B only, slow)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from _common import (REPO, contact_sheet, fit_cover, fit_pad, main_wrapper, opt, portrait_size,  # noqa: E402
                     round_4n1, segment_subjects, write_meta, write_video)

sys.path.insert(0, os.path.join(REPO, "third_party", "DiffSynth-Studio"))

CK = os.path.join(REPO, "checkpoints", "vace")
GEN_FPS = 16  # Wan native


def build_pipe(model):
    import torch
    from diffsynth.pipelines.wan_video import ModelConfig, WanVideoPipeline
    vram = dict(offload_dtype=torch.bfloat16, offload_device="cpu", onload_dtype=torch.bfloat16,
                onload_device="cpu", preparing_dtype=torch.bfloat16, preparing_device="cuda",
                computation_dtype=torch.bfloat16, computation_device="cuda")
    if model == "14B":
        d = os.path.join(CK, "Wan2.1-VACE-14B")
        dit = [sorted(os.path.join(d, f) for f in os.listdir(d) if f.startswith("diffusion_pytorch_model") and f.endswith(".safetensors"))]
        ids = ["Wan-AI/Wan2.1-VACE-14B"]
    elif model == "2.2-fun":
        d = os.path.join(CK, "Wan2.2-VACE-Fun-A14B")
        dit = [os.path.join(d, "high_noise_model", "diffusion_pytorch_model.safetensors"),
               os.path.join(d, "low_noise_model", "diffusion_pytorch_model.safetensors")]
        ids = ["alibaba-pai/Wan2.2-VACE-Fun-A14B"]
    else:
        raise ValueError(f"unknown model {model}")
    cfgs = [ModelConfig(path=p, **vram) for p in dit]
    cfgs += [ModelConfig(path=os.path.join(d, "models_t5_umt5-xxl-enc-bf16.pth"), **vram),
             ModelConfig(path=os.path.join(d, "Wan2.1_VAE.pth"), **vram)]
    tok = ModelConfig(path=os.path.join(CK, "Wan2.1-VACE-14B", "google", "umt5-xxl"))
    kw = {}
    if vram:
        kw["vram_limit"] = torch.cuda.mem_get_info("cuda")[1] / (1024 ** 3) - 4
    pipe = WanVideoPipeline.from_pretrained(torch_dtype=torch.bfloat16, device="cuda", model_configs=cfgs,
                                            tokenizer_config=tok, redirect_common_files=False, **kw)
    return pipe, ids


def run(job, t0):
    import numpy as np
    import torch
    from PIL import Image

    out = job["out_dir"]
    model = str(opt(job, "model", "2.2-fun", "VACE_MODEL"))
    if model not in ("14B", "2.2-fun"):
        raise ValueError(f"model {model!r} refused: only 14B-class models are allowed ('2.2-fun' or '14B')")
    mode = str(opt(job, "mode", "auto", "VACE_MODE"))
    if mode == "auto":
        mode = "ff"
    size = opt(job, "size", "480p")
    W, H = portrait_size(job, 1280, 720) if size == "720p" else portrait_size(job)
    N = round_4n1(round(job["num_frames"] * GEN_FPS / job["fps"]) + 2)  # same duration at 16 fps, capped at 81
    steps = int(opt(job, "steps", 40, "VACE_STEPS"))
    seed = int(job["seed"])

    # --- references (identity) ---
    ids = list(job["identity_photos"])[: int(opt(job, "max_refs", 2))]
    if str(opt(job, "segment", "1", "VACE_SEGMENT")) == "1":
        ids = segment_subjects(ids, out)
    refs = [fit_pad(Image.open(p), W, H) for p in ids]

    scene = fit_cover(Image.open(job["scene_frame"]), W, H)
    gray = Image.new("RGB", (W, H), (128, 128, 128))
    black = Image.new("L", (W, H), 0).convert("RGB")
    white = Image.new("L", (W, H), 255).convert("RGB")
    vace_video = vace_mask = None
    first = None
    if mode == "ff":
        first = fit_cover(Image.open(job["first_frame"] or job["scene_frame"]), W, H)
        vace_video = [first] + [gray] * (N - 1)
        vace_mask = [black] + [white] * (N - 1)
    elif mode == "inpaint":
        x0, y0, x1, y1 = opt(job, "mask_box", [0.0, 0.25, 1.0, 1.0])
        m = np.zeros((H, W, 3), np.uint8)
        m[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)] = 255
        mi = Image.fromarray(m)
        # masked (to-be-generated) pixels are set to neutral gray, as in VACE's inpainting preprocessors
        sv = np.where(m > 0, 128, np.asarray(scene)).astype(np.uint8)
        vace_video = [Image.fromarray(sv)] * N
        vace_mask = [mi] * N
    elif mode == "bgref":
        refs = refs + [scene]
    elif mode != "ref":
        raise ValueError(f"unknown mode {mode}")

    torch.cuda.reset_peak_memory_stats()
    pipe, model_ids = build_pipe(model)
    neg = job["negative_prompt"] or ("bright tones, overexposed, static, blurred details, subtitles, style, artwork, "
                                     "painting, still picture, worst quality, low quality, JPEG artifacts, ugly, "
                                     "deformed, extra fingers, poorly drawn hands, poorly drawn face, malformed limbs, "
                                     "fused fingers, three legs, many people in background, walking backwards")
    t1 = time.time()
    video = pipe(prompt=job["prompt"], negative_prompt=neg, vace_video=vace_video, vace_video_mask=vace_mask,
                 vace_reference_image=refs, height=H, width=W, num_frames=N, seed=seed, tiled=True,
                 num_inference_steps=steps, cfg_scale=5.0, sigma_shift=5.0)
    gen_s = time.time() - t1
    frames = [f if isinstance(f, Image.Image) else Image.fromarray(np.asarray(f)) for f in video]
    write_video(frames, os.path.join(out, "video.mp4"), GEN_FPS, job["fps"])
    contact_sheet(frames, os.path.join(out, "contact_sheet.jpg"))
    if first is not None:
        first.save(os.path.join(out, "first_frame.png"))
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    write_meta(job, backend="vace", model_ids=model_ids, resolution=[W, H], num_frames=len(frames),
               runtime_s=round(time.time() - t0, 1), peak_vram_gb=round(peak, 2),
               notes=f"mode={mode} steps={steps} refs={len(refs)} gen_fps={GEN_FPS} resampled to {job['fps']} fps; "
                     f"sampling {gen_s:.0f}s")


if __name__ == "__main__":
    main_wrapper(run)
