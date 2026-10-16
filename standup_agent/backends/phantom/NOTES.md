# phantom backend

Phantom-Wan-14B is a subject-consistent video model: the identity photos are passed as reference
images. Phantom has no frame conditioning, so the scene can only be suggested by adding the scene frame
as one more reference image (`scene_ref=frame`, the default).

## Run

```
CUDA_VISIBLE_DEVICES=1 ~/miniconda3/envs/wan21/bin/python standup_agent/backends/phantom/run.py --job job.json
```

Optional `job["options"]` keys (or env vars):

- `model` (`PHANTOM_MODEL`): only `14B` is accepted. Anything else writes `error.txt` and exits 1.
- `scene_ref`: `frame` (default) or `none`.
- `segment`, `max_refs`, `steps` (default 40), `resident_blocks` (default 8), `size`.

## Code, weights, env

- Code: `third_party/Phantom` @ bd84b60 (submodule, unmodified).
  - The clone is a sparse, blob-filtered checkout: `assets/` and `examples/ref_results/` were left out
    because GitHub served this repo at about 50 KB/s.
  - `run.py` builds `Phantom_Wan_S2V` itself (`object.__new__`) instead of calling its `__init__`. The
    original `__init__` loads the fp32 14B shards (57 GB) straight onto the GPU.
  - Instead, `run.py` loads the shards on CPU and casts only the `nn.Linear` weights to bf16. Norms and
    modulation stay fp32; this is required, because with bf16 norms `rope_apply` fails in
    `view_as_complex`.
  - It then turns on block swapping:
    - 8 of the 40 DiT blocks stay on the GPU.
    - The other 32 sit in pinned CPU memory and are copied to the GPU by forward hooks for each forward
      pass.
    - `model.to` and `model.cpu` are patched so `generate()` cannot move the whole model.
  - T5 runs on the CPU.
  - `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- Weights:
  - `checkpoints/phantom/Phantom-Wan-Models/`: `hf download bytedance-research/Phantom`.
    files.
- Env: `wan21`, shared with vace; see `../vace/NOTES.md` and `envs/wan21.yml`.
- Person cutout: BiRefNet from `checkpoints/vace/BiRefNet`.

## Settings

- Resolution: 480x832 portrait. The model accepts portrait sizes even though `generate.py` only lists
  landscape sizes.
- Frames: 121 at 24 fps (5 s), the 14B training setting. 40 UniPC steps, shift 5.
- Guidance: image scale 5.0, text scale 7.5. That is three DiT passes per step.

## Results (test job, seed 42, RTX 4090)

All test outputs are in `outputs/agent/test/phantom/<job>/`.

| job | refs | runtime | peak VRAM | scene kept | identity | split squat, foot on chair |
|---|---|---|---|---|---|---|
| ph14_sceneref | 2 identity cutouts + scene frame | **56.8 min** (78.7 s/step) | 16.6 GB | **no**: generic office with a big window; only a white folding chair carried over | good (cap, brown tee, leggings) | **no**: holds a static lunge with the rear foot on the floor; almost no motion |

**Verdict:** Phantom keeps identity well, but it cannot anchor the user's scene and did not follow the
exercise. Use the vace backend (Wan2.2-VACE-Fun-A14B, scene as frame 0) for this product.
