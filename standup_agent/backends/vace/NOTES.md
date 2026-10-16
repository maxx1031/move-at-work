# vace backend

Identity photos are passed as VACE reference images. The scene is anchored as frame 0 of the VACE
source video (`mode=ff`). The model runs through DiffSynth-Studio with layer-wise CPU offload, so a 14B
model fits on one 24 GB RTX 4090.

## Run

```
CUDA_VISIBLE_DEVICES=1 ~/miniconda3/envs/wan21/bin/python standup_agent/backends/vace/run.py --job job.json
```

Optional `job["options"]` keys (or env vars):

- `model` (`VACE_MODEL`): `2.2-fun` (default) or `14B`.
- `mode` (`VACE_MODE`): `auto` (= `ff`), `ff`, `inpaint`, `bgref` or `ref`.
- `mask_box`: used by `inpaint` mode.
- `segment`, `max_refs`, `steps`, `size`.


## Code, weights, env

- Code: `third_party/DiffSynth-Studio` @ 4a2d8af (submodule, unmodified).
- Reference only: `third_party/VACE` @ 48eb44f (VACE task formats). Its own inference needs the
  Wan2.1 `wan` package and has no offloading, so it is not used.
- Weights: `checkpoints/vace/`.
  - `Wan2.2-VACE-Fun-A14B` (alibaba-pai; high- and low-noise experts in bf16, 2x34.7 GB).
  - `Wan2.1-VACE-14B`.
  - `BiRefNet` (ZhengPeng7/BiRefNet), which cuts the person out of the identity photos onto white,
    as VACE and Phantom expect.

Download commands:

```
hf download alibaba-pai/Wan2.2-VACE-Fun-A14B --local-dir checkpoints/vace/Wan2.2-VACE-Fun-A14B --exclude 'models_t5*' --exclude Wan2.1_VAE.pth
hf download Wan-AI/Wan2.1-VACE-14B --local-dir checkpoints/vace/Wan2.1-VACE-14B --exclude 'models_t5*'
hf download ZhengPeng7/BiRefNet --local-dir checkpoints/vace/BiRefNet
```

Env: `wan21`, exported to `envs/wan21.yml` and shared with the phantom backend.

- Built as a clone of `trellis2`: python 3.10, torch 2.6.0+cu124, flash-attn 2.7.3.
- Added on top: diffusers 0.31, modelscope, peft, decord, easydict, ftfy, dashscope.
- Install command: `PIP_CONFIG_FILE=/dev/null pip install -c constraints(torch pins) ...`
- DiffSynth-Studio is imported from the submodule through `sys.path`, not pip-installed.

## Deviations

- **Resolution:** generates 480x832 portrait, which fits the model's native 480p area. `size=720p`
  is possible but about 2.5x slower.
- **Frames and fps:** generates 81 frames at 16 fps (5 s). ffmpeg then resamples the clip to the job's
  fps (24) while keeping the same duration.
- **Sampling:** 40 steps, cfg 5, shift 5.
- **VRAM:** with `vram_limit` = GPU total minus 4 GB, peak VRAM is about 19.2 GB.

## Results (test job, prompt from the task, seed 42, RTX 4090)

All test outputs are in `outputs/agent/test/vace/<job>/`.

| job | model / mode | runtime (5 s clip) | peak VRAM | scene kept | identity | split squat, foot on chair |
|---|---|---|---|---|---|---|
| vace22_firstframe | **Wan2.2-VACE-Fun-A14B**, frame 0 = compose_i2v `first_frame.png` | 24.4 min | 19.2 GB | yes, continuous (no jump) | yes | **yes, ~3 smooth reps, rear foot on the chair seat throughout** (best result) |
| vace22_ff | **Wan2.2-VACE-Fun-A14B**, scene = frame 0 | 25.3 min (36 s/step) | 19.2 GB | yes | yes (brown tee, cap, leggings) | **yes, ~3 clear down/up reps** |
| vace14_ff | Wan2.1-VACE-14B, scene = frame 0 | 25.3 min (37 s/step) | 19.2 GB | yes | yes | yes, ~1-2 reps; mostly holds the bottom |

### What to watch for

- **Camera jump when the empty scene is frame 0.** The person has to appear, so the model makes a
  small camera jump or zoom between frame 0 and frame 1. After that, the background stays consistent
  with the scene frame.
- **Fix: pass a pre-composed `job.first_frame`** (person already in the scene, e.g. from
  `compose_i2v`). This removed the jump in `vace22_firstframe`, the best clip of all the runs.
- **Caveat on identity:** the identity photos were taken in this same atrium while doing this same
  exercise, which makes the test easier.
