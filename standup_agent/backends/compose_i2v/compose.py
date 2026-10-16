"""Stage 1 of compose_i2v: put the user (identity photos) into the scene frame with Qwen-Image-Edit-2509.

Usage: python compose.py --scene S --identity A.jpg [B.jpg] --instruction "..." --out first_frame.png
         [--width 704 --height 1280 --seed 42 --steps 8 --no_lightning]

Memory strategy (24 GB 4090): bf16 weights, Lightning-8step LoRA fused into the transformer, then diffusers
group offloading (block-level, CUDA streams) for the 20B transformer and the Qwen2.5-VL text encoder.
"""
import argparse
import math
import os
import time

import torch
from PIL import Image, ImageOps

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
CKPT = os.path.join(ROOT, "checkpoints/compose_i2v")
BASE = os.path.join(CKPT, "Qwen-Image-Edit-2509")
LIGHTNING = os.path.join(CKPT, "Qwen-Image-Lightning/Qwen-Image-Edit-2509",
                         "Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors")

NEG = ("extra people, duplicate person, extra limbs, deformed hands, distorted face, blurry, cartoon, "
       "painting, floating person, wrong scale, changed background")


def build_prompt(instruction: str, n_id: int) -> str:
    ids = "image 2" if n_id == 1 else "images 2" + "".join(f" and {i + 2}" for i in range(1, n_id))
    return (f"Place the person from {ids} into the scene of image 1. "
            f"The person is shown from the side or at a three-quarter angle, with the face visible to the camera. "
            f"{instruction} "
            f"Keep the background, the furniture and the camera viewpoint of image 1 exactly unchanged. "
            f"Keep the person's face, hair, headwear, glasses or other accessories and clothes identical to {ids}. "
            f"The person from {ids} is the only person in the image. "
            f"Full body visible, physically plausible contact with the floor and furniture, photorealistic, "
            f"correct scale, perspective, shadows and lighting.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--identity", nargs="+", required=True)
    ap.add_argument("--instruction", required=True)
    ap.add_argument("--alt_instruction", default=None,
                    help="used for every second candidate (e.g. the opposite facing direction)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--extra_seeds", type=int, nargs="*", default=[],
                    help="also save candidates <out>_s<seed>.png (debugging / candidate selection)")
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--no_lightning", action="store_true")
    ap.add_argument("--max_identity", type=int, default=2)
    args = ap.parse_args()

    from diffusers import FlowMatchEulerDiscreteScheduler, QwenImageEditPlusPipeline

    t0 = time.time()
    dtype = torch.bfloat16
    use_lightning = not args.no_lightning and os.path.exists(LIGHTNING)
    kw = {}
    if use_lightning:
        kw["scheduler"] = FlowMatchEulerDiscreteScheduler.from_config({
            "base_image_seq_len": 256, "base_shift": math.log(3), "invert_sigmas": False,
            "max_image_seq_len": 8192, "max_shift": math.log(3), "num_train_timesteps": 1000,
            "shift": 1.0, "shift_terminal": None, "stochastic_sampling": False,
            "time_shift_type": "exponential", "use_beta_sigmas": False, "use_dynamic_shifting": True,
            "use_exponential_sigmas": False, "use_karras_sigmas": False})
    pipe = QwenImageEditPlusPipeline.from_pretrained(BASE, torch_dtype=dtype, **kw)
    if use_lightning:
        pipe.load_lora_weights(LIGHTNING)
        pipe.fuse_lora()
        pipe.unload_lora_weights()
    dev = torch.device("cuda")
    pipe.transformer.enable_group_offload(onload_device=dev, offload_device=torch.device("cpu"),
                                          offload_type="block_level", num_blocks_per_group=1,
                                          use_stream=True)
    from diffusers.hooks import apply_group_offloading
    apply_group_offloading(pipe.text_encoder, onload_device=dev, offload_device=torch.device("cpu"),
                           offload_type="leaf_level", use_stream=True)
    pipe.vae.to(dev)
    print(f"[compose] loaded in {time.time() - t0:.1f}s (lightning={use_lightning})", flush=True)

    scene = ImageOps.exif_transpose(Image.open(args.scene)).convert("RGB")
    ids = [ImageOps.exif_transpose(Image.open(p)).convert("RGB") for p in args.identity[:args.max_identity]]
    prompts = [build_prompt(args.instruction, len(ids))]
    if args.alt_instruction:
        prompts.append(build_prompt(args.alt_instruction, len(ids)))
    for k, pr in enumerate(prompts):
        print(f"[compose] prompt {k}:", pr, flush=True)

    steps = args.steps or (8 if use_lightning else 40)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    stem, ext = os.path.splitext(args.out)
    for i, seed in enumerate([args.seed] + list(args.extra_seeds)):
        t1 = time.time()
        out = pipe(image=[scene] + ids, prompt=prompts[i % len(prompts)],
                   negative_prompt=NEG if not use_lightning else None,
                   true_cfg_scale=1.0 if use_lightning else 4.0,
                   num_inference_steps=steps, height=args.height, width=args.width,
                   generator=torch.Generator(device="cpu").manual_seed(seed)).images[0]
        print(f"[compose] seed {seed}: {steps} steps in {time.time() - t1:.1f}s; "
              f"peak alloc {torch.cuda.max_memory_allocated() / 2**30:.1f} GB", flush=True)
        out.save(args.out if i == 0 else f"{stem}_s{seed}{ext}")

if __name__ == "__main__":
    main()
