"""Stage 2 of compose_i2v: Wan2.2-TI2V-5B image-to-video using the official repo (third_party/Wan2.2).

Equivalent to `generate.py --task ti2v-5B --size 704*1280 --offload_model True --convert_model_dtype --t5_cpu`
but importable so we can pass a negative prompt and an exact frame count.
"""
import argparse
import os
import sys
import time

import torch
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
WAN = os.path.join(ROOT, "third_party/Wan2.2")
sys.path.insert(0, WAN)

import wan  # noqa: E402
from wan.configs import WAN_CONFIGS  # noqa: E402
from wan.utils.utils import save_video  # noqa: E402


def tiled_decode(vae, zs, tile=48, overlap=16):
    """Spatially tiled replacement for Wan2_2_VAE.decode (fp32), splitting the longer latent side into
    overlapping tiles blended linearly. Full-frame decode of 121x1280x704 peaks at ~22 GB, which OOMs on a
    24 GB card next to the desktop; two tiles peak at ~13 GB."""
    outs = []
    for z in zs:
        z = z.unsqueeze(0)
        _, _, _, h, w = z.shape
        axis = 3 if h >= w else 4
        n = z.shape[axis]
        if n <= tile:
            outs.append(vae.model.decode(z, vae.scale).float().clamp_(-1, 1).squeeze(0))
            continue
        starts, s0 = [], 0
        while True:
            starts.append(min(s0, n - tile))
            if s0 + tile >= n:
                break
            s0 += tile - overlap
        up = 16  # Wan2.2 VAE spatial upsampling factor
        acc = None
        wsum = None
        for st in starts:
            zt = z.narrow(axis, st, tile)
            with torch.no_grad():
                xt = vae.model.decode(zt, vae.scale).float().cpu()
            L = tile * up
            ramp = torch.ones(L)
            ov = overlap * up
            if st > 0:
                ramp[:ov] = torch.linspace(0, 1, ov + 2)[1:-1]
            if st + tile < n:
                ramp[-ov:] = torch.linspace(1, 0, ov + 2)[1:-1]
            shape = [1] * xt.dim()
            shape[axis] = L
            ramp = ramp.view(shape)
            if acc is None:
                full = list(xt.shape)
                full[axis] = n * up
                acc = torch.zeros(full)
                wsum = torch.zeros([1] * axis + [n * up] + [1] * (xt.dim() - axis - 1))
            acc.narrow(axis, st * up, L).add_(xt * ramp)
            wsum.narrow(axis, st * up, L).add_(ramp)
            del xt
            torch.cuda.empty_cache()
        outs.append((acc / wsum).clamp_(-1, 1).squeeze(0))
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--negative_prompt", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frame_num", type=int, default=121)
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--guide_scale", type=float, default=5.0)
    ap.add_argument("--shift", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--ckpt_dir", default=os.path.join(ROOT, "checkpoints/wan22/Wan2.2-TI2V-5B"))
    args = ap.parse_args()

    cfg = WAN_CONFIGS["ti2v-5B"]
    n_prompt = cfg.sample_neg_prompt + (", " + args.negative_prompt if args.negative_prompt else "")
    t0 = time.time()
    pipe = wan.WanTI2V(config=cfg, checkpoint_dir=args.ckpt_dir, device_id=0, rank=0,
                       t5_cpu=True, convert_model_dtype=True)
    pipe.vae.decode = lambda zs: tiled_decode(pipe.vae, zs)
    print(f"[wan] loaded in {time.time() - t0:.1f}s", flush=True)
    img = Image.open(args.image).convert("RGB")
    t1 = time.time()
    video = pipe.generate(args.prompt, img=img, size=(args.width, args.height),
                          max_area=args.width * args.height, frame_num=args.frame_num,
                          shift=args.shift, sample_solver="unipc", sampling_steps=args.steps,
                          guide_scale=args.guide_scale, n_prompt=n_prompt, seed=args.seed,
                          offload_model=True)
    print(f"[wan] sampled in {time.time() - t1:.1f}s; video {tuple(video.shape)}; "
          f"peak alloc {torch.cuda.max_memory_allocated() / 2**30:.1f} GB", flush=True)
    save_video(tensor=video[None], save_file=args.out, fps=args.fps, nrow=1, normalize=True,
               value_range=(-1, 1))


if __name__ == "__main__":
    main()
