"""Phantom backend: subject-consistent (identity-reference) video generation with Phantom-Wan-14B.

    <env python> standup_agent/backends/phantom/run.py --job job.json

Phantom has no frame conditioning; the scene can only be suggested through an extra reference image.
Options (job["options"][key] or env var):
  model: Phantom-Wan-14B only
  scene_ref (PHANTOM_SCENE_REF): "none" | "frame" (default: add the scene frame as an extra reference)
  segment   (PHANTOM_SEGMENT): "1" (default) cut the person out of identity photos (BiRefNet, white bg)
  max_refs  : max identity photos (default 2; Phantom supports <= 4 refs in total)
  steps     (PHANTOM_STEPS): default 40
  resident_blocks (PHANTOM_RESIDENT): 14B only, number of DiT blocks kept on GPU (rest streamed from
              pinned CPU memory every forward); default 8 of 40 (121 frames at 480p need ~10 GB activations).
  size      : "480p" (default, 480x832 portrait) | "720p" (14B only)
"""
import gc
import os as _os
_os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from _common import (REPO, contact_sheet, fit_pad, main_wrapper, opt, portrait_size, round_4n1,  # noqa: E402
                     segment_subjects, write_meta, write_video)

sys.path.insert(0, os.path.join(REPO, "third_party", "Phantom"))
CK = os.path.join(REPO, "checkpoints", "phantom")


def enable_block_swap(model, device, n_resident):
    """Keep the first n_resident transformer blocks on GPU; stream the others from pinned CPU memory.
    Patches model.to / model.cpu (instance attributes) so Phantom's generate() cannot move it wholesale."""
    import torch
    blocks = list(model.blocks)
    for name, mod in model.named_children():
        if name != "blocks":
            mod.to(device)
    for name, p in model.named_parameters(recurse=False):
        p.data = p.data.to(device)
    for name, b in model.named_buffers(recurse=False):
        b.data = b.data.to(device)
    swapped = []
    for i, blk in enumerate(blocks):
        if i < n_resident:
            blk.to(device)
            continue
        params = list(blk.parameters()) + list(blk.buffers())
        cpu = [p.data.pin_memory() for p in params]
        for p, c in zip(params, cpu):
            p.data = c

        def pre(mod, args, params=params, cpu=cpu):
            for p, c in zip(params, cpu):
                p.data = c.to(device, non_blocking=True)

        def post(mod, args, out, params=params, cpu=cpu):
            for p, c in zip(params, cpu):
                p.data = c
            return out

        blk.register_forward_pre_hook(pre)
        blk.register_forward_hook(post)
        swapped.append(blk)

    def to_noop(*a, **k):
        return model

    def cpu_all(*a, **k):
        for i, blk in enumerate(blocks):
            if i < n_resident:
                blk.cpu()
        for name, mod in model.named_children():
            if name != "blocks":
                mod.cpu()
        torch.cuda.empty_cache()
        return model

    model.to = to_noop
    model.cpu = cpu_all
    return len(swapped)


def build(n_resident):
    """Re-implementation of Phantom_Wan_S2V.__init__ that loads the DiT on CPU in bf16 (the original
    loads the fp32 14B shards straight onto the GPU, which cannot fit 24 GB)."""
    import json
    from functools import partial  # noqa
    import torch
    from safetensors.torch import load_file
    import phantom_wan
    from phantom_wan.configs import WAN_CONFIGS
    from phantom_wan.modules.model import WanModel
    from phantom_wan.modules.t5 import T5EncoderModel
    from phantom_wan.modules.vae import WanVAE

    cfg = WAN_CONFIGS["s2v-14B"]
    ckpt_dir = os.path.join(os.path.dirname(CK), "wan21_shared")  # Wan2.1 T5 encoder / VAE / tokenizer
    device = torch.device("cuda:0")
    s = object.__new__(phantom_wan.Phantom_Wan_S2V)
    s.device, s.config, s.rank, s.t5_cpu = device, cfg, 0, True
    s.num_train_timesteps, s.param_dtype = cfg.num_train_timesteps, cfg.param_dtype
    s.text_encoder = T5EncoderModel(text_len=cfg.text_len, dtype=cfg.t5_dtype, device=torch.device("cpu"),
                                    checkpoint_path=os.path.join(ckpt_dir, cfg.t5_checkpoint),
                                    tokenizer_path=os.path.join(ckpt_dir, cfg.t5_tokenizer), shard_fn=None)
    s.vae_stride, s.patch_size = cfg.vae_stride, cfg.patch_size
    s.vae = WanVAE(vae_pth=os.path.join(ckpt_dir, cfg.vae_checkpoint), device=device)
    with torch.device("meta"):
        m = WanModel(dim=cfg.dim, ffn_dim=cfg.ffn_dim, freq_dim=cfg.freq_dim, num_heads=cfg.num_heads,
                     num_layers=cfg.num_layers, window_size=cfg.window_size, qk_norm=cfg.qk_norm,
                     cross_attn_norm=cfg.cross_attn_norm, eps=cfg.eps)
    md = os.path.join(CK, "Phantom-Wan-Models")
    idx = json.load(open(os.path.join(md, "Phantom_Wan_14B.safetensors.index.json")))
    state = {}
    for f in sorted(set(idx["weight_map"].values())):
        sd = load_file(os.path.join(md, f))
        state.update(sd)  # fp32; Linear weights cast to bf16 after loading
        del sd
    ids = ["bytedance-research/Phantom (Phantom_Wan_14B)", "Wan2.1 T5 umt5-xxl encoder + VAE (checkpoints/wan21_shared)"]
    missing, unexpected = m.load_state_dict(state, strict=False, assign=True)
    # non-persistent buffers (rope freqs) are not in the state dict -> rebuild them on CPU
    if any(p.is_meta for p in list(m.parameters())):
        raise RuntimeError(f"missing weights: {[k for k, p in m.named_parameters() if p.is_meta][:5]}")
    if hasattr(m, "freqs") and m.freqs.is_meta:
        from phantom_wan.modules.model import rope_params
        d = cfg.dim // cfg.num_heads
        m.freqs = torch.cat([rope_params(1024, d - 4 * (d // 6)), rope_params(1024, 2 * (d // 6)),
                             rope_params(1024, 2 * (d // 6))], dim=1)
    del state
    gc.collect()
    m.eval().requires_grad_(False)
    s.model = m
    s.sp_size = 1
    s.sample_neg_prompt = cfg.sample_neg_prompt
    # only Linear weights in bf16 (memory); norms/modulation stay fp32 like the original fp32 model under
    # autocast, otherwise q/k reach rope_apply as bf16 and view_as_complex fails
    for mod in m.modules():
        if isinstance(mod, torch.nn.Linear):
            mod.to(torch.bfloat16)
    n_sw = enable_block_swap(m, device, n_resident)
    print(f"[phantom] block swap: {n_sw}/{len(m.blocks)} blocks streamed from CPU", flush=True)
    return s, ids


def run(job, t0):
    import numpy as np
    import torch
    from PIL import Image

    out = job["out_dir"]
    scene_ref = str(opt(job, "scene_ref", "frame", "PHANTOM_SCENE_REF"))
    size = opt(job, "size", "480p")
    W, H = portrait_size(job, 1280, 720) if size == "720p" else portrait_size(job)
    gen_fps, max_f = 24, 121  # Phantom-Wan-14B is trained on 121 frames @ 24 fps
    N = round_4n1(round(job["num_frames"] * gen_fps / job["fps"]) + 2, hi=max_f)
    steps = int(opt(job, "steps", 40, "PHANTOM_STEPS"))
    n_res = int(opt(job, "resident_blocks", 8, "PHANTOM_RESIDENT"))

    ids = list(job["identity_photos"])[: int(opt(job, "max_refs", 2))]
    if str(opt(job, "segment", "1", "PHANTOM_SEGMENT")) == "1":
        ids = segment_subjects(ids, out)
    refs = [fit_pad(Image.open(p), W, H) for p in ids]
    if scene_ref == "frame":
        refs.append(fit_pad(Image.open(job["scene_frame"]), W, H))
    refs = refs[:4]

    torch.cuda.reset_peak_memory_stats()
    pipe, model_ids = build(n_resident=n_res)
    neg = job["negative_prompt"] or ""
    t1 = time.time()
    with torch.no_grad():
        video = pipe.generate(job["prompt"], refs, size=(W, H), frame_num=N, shift=5.0, sample_solver="unipc",
                              sampling_steps=steps, guide_scale_img=5.0, guide_scale_text=7.5, n_prompt=neg,
                              seed=int(job["seed"]), offload_model=True)
    gen_s = time.time() - t1
    v = ((video.clamp(-1, 1) + 1) * 127.5).permute(1, 2, 3, 0).byte().cpu().numpy()  # C,T,H,W -> T,H,W,C
    frames = [Image.fromarray(f) for f in v]
    write_video(frames, os.path.join(out, "video.mp4"), gen_fps, job["fps"])
    contact_sheet(frames, os.path.join(out, "contact_sheet.jpg"))
    for i, r in enumerate(refs):
        r.save(os.path.join(out, f"ref_input_{i}.png"))
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    write_meta(job, backend="phantom", model_ids=model_ids, resolution=[W, H], num_frames=len(frames),
               runtime_s=round(time.time() - t0, 1), peak_vram_gb=round(peak, 2),
               notes=f"model=Phantom-Wan-14B scene_ref={scene_ref} refs={len(refs)} steps={steps} gen_fps={gen_fps} "
                     f"resampled to {job['fps']} fps; sampling {gen_s:.0f}s; block swap resident={n_res}/40")


if __name__ == "__main__":
    main_wrapper(run)
