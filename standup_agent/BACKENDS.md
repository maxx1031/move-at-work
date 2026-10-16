# Video-generation backend contract

The agent (`standup_agent/`) writes one **job file** per suggested exercise and runs each backend as a
subprocess in that backend's own conda env:

```
<env python> standup_agent/backends/<name>/run.py --job <job.json>
```

## job.json (input)

```json
{
  "job_id": "sugg1_compose_i2v",
  "scene_frame": "/abs/path/scene_frame.jpg",          // chosen video frame: the background / setting
  "identity_photos": ["/abs/path/u1.jpg", "..."],       // user's own photos (1-5); the person must look like this
  "first_frame": null,                                   // optional pre-composed start frame (person already in scene)
  "start_pose_instruction": "The person stands about one stride in front of the folding chair, facing away from it, ...",
  "prompt": "A woman does a Bulgarian split squat: her left foot rests on the seat of the folding chair behind her ...",
  "negative_prompt": "extra limbs, deformed hands, ...",
  "num_frames": 121,            // backends round to their valid lengths (e.g. 4n+1)
  "fps": 24,
  "width": 704, "height": 1280, // target; backends may use their native size and resize
  "seed": 42,
  "out_dir": "/abs/path/outputs/agent/<video>/sugg1/compose_i2v"
}
```

## Outputs (written to `out_dir`)

- `video.mp4` (required). The final clip, H.264, `fps` from the job.
- `first_frame.png` (if the backend composed or used one).
- `meta.json` (required): `{"backend", "model_ids", "resolution", "num_frames", "fps", "seed", "runtime_s",
  "peak_vram_gb", "notes"}`.
- Exit code 0 on success. On failure: non-zero exit and an `error.txt` with the reason.

## Rules for backends

- Load models from `checkpoints/<backend>/` (gitignored). Code goes in git submodules under `third_party/`. Each
  backend has its own env, exported to `envs/<env>.yml`.
- The GPU is chosen by `CUDA_VISIBLE_DEVICES`. Each backend must fit a single 24 GB RTX 4090, using offloading if needed.
- Never edit submodule sources. Put wrappers in `standup_agent/backends/<name>/`.
