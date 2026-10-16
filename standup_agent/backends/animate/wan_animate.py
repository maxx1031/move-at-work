"""Wan2.2-Animate-14B inference (official repo, third_party/Wan2.2) adapted to a single 24 GB GPU.

Equivalent to `generate.py --task animate-14B --replace_flag --use_relighting_lora --offload_model True
--convert_model_dtype`, except that the official code loads the 14B DiT straight onto the GPU
(`device_map=cuda`, ~35 GB bf16). Here, without editing the submodule, we
  * load the DiT on CPU (monkeypatched `from_pretrained`, drops `device_map`),
  * attach diffusers group offloading (block-level, CUDA-stream prefetch) to it, so only ~1-2 transformer
    blocks live on the GPU at a time. The repo's sampling loop never moves the DiT, so the hooks handle it.
"""
import argparse
import os
import sys
import time

import torch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(ROOT, "third_party/Wan2.2"))

import wan  # noqa: E402
import wan.animate as wa  # noqa: E402
from wan.configs import WAN_CONFIGS  # noqa: E402
from wan.utils.utils import save_video  # noqa: E402

_orig_fp = wa.WanAnimateModel.from_pretrained.__func__


@classmethod
def _from_pretrained_cpu(cls, *a, **k):
    k.pop("device_map", None)
    return _orig_fp(cls, *a, **k)


wa.WanAnimateModel.from_pretrained = _from_pretrained_cpu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src_root_path", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--clip_len", type=int, default=77)
    ap.add_argument("--refert_num", type=int, default=1)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--no_relighting_lora", action="store_true")
    ap.add_argument("--ckpt_dir", default=os.path.join(ROOT, "checkpoints/wan22/Wan2.2-Animate-14B"))
    args = ap.parse_args()

    cfg = WAN_CONFIGS["animate-14B"]
    t0 = time.time()
    pipe = wan.WanAnimate(config=cfg, checkpoint_dir=args.ckpt_dir, device_id=0, rank=0,
                          t5_cpu=False, convert_model_dtype=True,
                          use_relighting_lora=not args.no_relighting_lora)
    pipe.noise_model.enable_group_offload(onload_device=torch.device("cuda"),
                                          offload_device=torch.device("cpu"),
                                          offload_type="block_level",
                                          num_blocks_per_group=1, use_stream=True)
    print(f"[animate] loaded in {time.time() - t0:.1f}s; resident "
          f"{torch.cuda.memory_allocated() / 2**30:.1f} GB", flush=True)
    t1 = time.time()
    video = pipe.generate(src_root_path=args.src_root_path, replace_flag=True, refert_num=args.refert_num,
                          clip_len=args.clip_len, shift=cfg.sample_shift, sample_solver="dpm++",
                          sampling_steps=args.steps, guide_scale=cfg.sample_guide_scale,
                          input_prompt=args.prompt, seed=args.seed, offload_model=True)
    print(f"[animate] sampled in {time.time() - t1:.1f}s; video {tuple(video.shape)}; "
          f"peak alloc {torch.cuda.max_memory_allocated() / 2**30:.1f} GB", flush=True)
    save_video(tensor=video[None], save_file=args.out, fps=args.fps, nrow=1, normalize=True,
               value_range=(-1, 1))


if __name__ == "__main__":
    main()
