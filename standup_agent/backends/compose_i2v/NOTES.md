# compose_i2v backend

Two stages, both in the `wan22` conda env (`envs/wan22.yml`), each run as its own subprocess by `run.py` so GPU
memory is fully released between them:

1. **Compose** (`compose.py`): Qwen-Image-Edit-2509 (diffusers `QwenImageEditPlusPipeline`) with inputs
   `[scene_frame, identity_photos[0], identity_photos[1]]` (max 2 identity photos; Qwen-Edit-2509 handles up to 3
   images). Prompt template in `build_prompt()`; it wraps `job.start_pose_instruction`. Output at the Wan size
   (704x1280 portrait / 1280x704 landscape, chosen from the scene frame's orientation). Skipped if `job.first_frame`.
2. **Animate** (`wan_ti2v.py`): Wan2.2-TI2V-5B image-to-video through the official repo (`third_party/Wan2.2`, imported, not
   edited), equivalent to `generate.py --task ti2v-5B --size 704*1280 --offload_model True --convert_model_dtype
   --t5_cpu`, plus the job negative prompt appended to Wan's default one. 50 UniPC steps, CFG 5, shift 5, 4n+1 frames.
   Raw output is re-encoded to H.264/yuv420p (`video.mp4`).

## Run

```bash
CUDA_VISIBLE_DEVICES=0 ~/miniconda3/envs/wan22/bin/python standup_agent/backends/compose_i2v/run.py \
    --job work/agent_test/job_compose_i2v.json        # optional: --wan_steps 50 --compose_steps 8
# compose stage alone (with extra candidate seeds -> <out>_s<seed>.png):
CUDA_VISIBLE_DEVICES=0 ~/miniconda3/envs/wan22/bin/python standup_agent/backends/compose_i2v/compose.py \
    --scene scene.jpg --identity u0.jpg u1.jpg --instruction "..." --out ff.png --extra_seeds 1 2 3
```

## Weights (checkpoints/, gitignored)

- `checkpoints/compose_i2v/Qwen-Image-Edit-2509/` - `hf download Qwen/Qwen-Image-Edit-2509` (bf16, ~54 GB)
- `checkpoints/compose_i2v/Qwen-Image-Lightning/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors`
  - `hf download lightx2v/Qwen-Image-Lightning --include "Qwen-Image-Edit-2509/*"`
- `checkpoints/wan22/Wan2.2-TI2V-5B/` - `hf download Wan-AI/Wan2.2-TI2V-5B`

## Versions

Python 3.10, torch 2.6.0+cu124 (driver 550), flash-attn 2.7.4.post1 (prebuilt wheel cu12torch2.6cxx11abiFALSE),
diffusers 0.41.0, transformers 5.20.0, accelerate 1.15, peft 0.21. Wan2.2 commit 1ea34ff.
Install: `PIP_CONFIG_FILE=/dev/null pip install -c constraints.txt ...` with torch pinned in constraints so pip never
upgrades torch.

## Memory / speed strategy (RTX 4090 24 GB)

- Qwen-Image-Edit-2509 (20B transformer + Qwen2.5-VL-7B encoder) in **bf16**, Lightning 8-step LoRA fused into the
  transformer (true_cfg 1.0), then **diffusers group offloading**: transformer block-level (1 block/group, CUDA-stream
  prefetch), text encoder leaf-level with streams, VAE on GPU. Peak torch alloc **3.8 GB**; 8 steps at 704x1280 take
  **~28-31 s**; pipeline load ~60-90 s (from page cache). Needs ~60 GB of host RAM.
  `--no_lightning` gives the 40-step CFG-4 path (not used by default).
- Wan2.2-TI2V-5B: official offloading flags. DiT ~9.3 s/step at 704x1280x121 (50 steps ~8 min).
  **Deviation:** the stock `Wan2_2_VAE.decode` for 121x1280x704 peaks at ~22 GB (OOMs on a 24 GB card that also
  drives the desktop). `wan_ti2v.py` replaces `pipe.vae.decode` with a spatially tiled decode (2 tiles of 48 latent
  rows, 16-row linear-blend overlap, fp32): peak 14.8 GB, mean abs diff vs full decode 2e-4. `run.py` also sets
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- `meta.json` `peak_vram_gb` is nvidia-smi peak of the whole GPU (includes ~0.9 GB of desktop processes).

## Alternatives considered

- diffusers `WanImageToVideoPipeline` (Wan2.2-TI2V-5B-Diffusers) / DiffSynth-Studio were not needed: the official repo
  runs reliably on one 4090 once the VAE decode is tiled, so no extra weights/deps were added.
- transformers is NOT capped at <=4.51.3 (Wan2.2 requirements.txt): diffusers 0.41 (needed for
  QwenImageEditPlusPipeline) requires huggingface_hub>=1.32 which transformers 4.51 rejects. Wan's TI2V/Animate code
  paths work with transformers 5.20 (umt5 tokenizer only).

## Prompting lessons (compose stage)

- "facing away from the chair" alone makes Qwen render the person from behind (no face, foot not on chair). The template
  therefore inserts "The person is shown from the side or at a three-quarter angle, with the face visible to the
  camera." before the instruction; the agent's `start_pose_instruction` should describe orientation relative to the
  camera/frame (e.g. "side view, facing the right edge of the frame").
- Don't say "feet on the floor" in the template (conflicts with foot-on-chair poses).
