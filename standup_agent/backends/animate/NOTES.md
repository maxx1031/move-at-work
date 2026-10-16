# animate backend (Wan2.2-Animate-14B, replacement mode)

Optional refinement pass: takes a generated exercise video (`job.driving_video`, e.g. the compose_i2v `video.mp4`)
and `job.identity_photos[0]`, and re-renders the person with the user's identity while keeping the motion and
background. Env: `wan22` (same as compose_i2v, `envs/wan22.yml`).

## Run

```bash
# job = compose_i2v job + "driving_video": "/abs/path/video.mp4" and a new out_dir
CUDA_VISIBLE_DEVICES=0 ~/miniconda3/envs/wan22/bin/python standup_agent/backends/animate/run.py \
    --job work/agent_test/job_animate.json            # optional: --steps 20 --w_len 4 --h_len 8
```

Stages (subprocesses):
1. `preprocess.py` -> official `wan/modules/animate/preprocess/preprocess_data.py --replace_flag
   --resolution_area 720 1280 (or 1280 720) --fps <job fps> --iterations 3 --k 7 --w_len 4 --h_len 8`
   (YOLOv10m det + ViTPose-H wholebody (onnxruntime-gpu), face crops, SAM2-L person mask, masked background video).
   Writes `out_dir/process_results/{src_pose,src_face,src_bg,src_mask}.mp4, src_ref.png`.
2. `wan_animate.py` -> `wan.WanAnimate(...).generate(replace_flag=True, refert_num=1, clip_len=77, 20 dpm++ steps,
   guide 1.0)` with the relighting LoRA (`--use_relighting_lora` equivalent).

## Weights

`checkpoints/wan22/Wan2.2-Animate-14B/` (`hf download Wan-AI/Wan2.2-Animate-14B`, ~60 GB incl. `process_checkpoint/`
with det/pose2d/sam2 and `relighting_lora.ckpt`).

## Deviations from the official recipe (no submodule edits)

- **DiT offloading.** Official code loads the 14B DiT straight onto the GPU (`from_pretrained(device_map=cuda)`, ~35 GB
  bf16) -> cannot fit 24 GB. `wan_animate.py` monkeypatches `WanAnimateModel.from_pretrained` to load on CPU and
  attaches diffusers group offloading (block-level, 1 block/group, CUDA-stream prefetch). The repo's sampling loop
  never moves the DiT, so the hooks stream blocks in. T5 is moved to GPU only for encoding (`offload_model=True`).
- **SAM2 connected components.** The pinned SAM2 commit always builds `sam2._C`; its connected-components kernel
  segfaults inside the preprocessing process (works standalone). `preprocess.py` swaps
  `sam2.utils.misc.get_connected_components` for a scipy CPU equivalent and then runs the official script via runpy.
- **SAM2 install** must be editable (`pip install -e git+...@0e78a11#egg=SAM-2`), otherwise `sam2_configs/*.yaml`
  are missing and hydra fails with "Cannot find primary config 'sam2_hiera_l.yaml'".
- **Mask granularity** defaults to `--w_len 4 --h_len 8` instead of the README's `1 1`: with 1x1 the mask is the
  person's bounding rectangle, which also covers (and regenerates) the chair the person interacts with.
- `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` is set by run.py (first attempt OOMed at 21.3 GB in the FFN
  with 3.6 GB reserved-but-unallocated).
- Output is saved at the job fps (official generate.py saves at 30 fps).

## Measured (test job, 121 frames 704x1280 @24fps, RTX 4090, GPU shared with desktop)

- Preprocessing: ~1-2 min.
- Model load ~85 s (resident 2.7 GB after load). Sampling **~51.8 s/step**; 121 frames = 2 clips of 77 (refert 1)
  x 20 steps = **2164 s (~36 min)**. Peak torch alloc **21.7 GB** (nvidia-smi ~24.1 GB, right at the limit;
  it OOMed once without expandable_segments).
- End-to-end through run.py was not completed: stage 2 was run by hand on run.py's preprocessing output
  (`outputs/agent/test/animate/video_manual_stage2.mp4`). The run.py wiring (preprocess -> wan_animate -> ffmpeg ->
  meta.json) is in place but has not been run as a single job.
- Quality: motion and background follow the driving video closely; the person stays recognizable (cap, glasses, brown
  T-shirt, leggings). Because the compose_i2v driving video already shows the user, the output looks almost the same
  as the input, so for this pipeline it is mostly a slow no-op. It would matter more when the driving video shows a
  different person.
