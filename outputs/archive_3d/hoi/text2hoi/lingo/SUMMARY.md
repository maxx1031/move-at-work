# LINGO: text + static scene → motion, on the video folding chair

**Result: only "walk → sit down" works, and only in LINGO's own phrasing. P1, P3 and P4 are not generated.** The chair is part of the static scene grid, so it never moves.

## Setup
- **Code and weights:** `third_party/lingo-release` (no source edits); env `lingo` (torch 1.13.1+cu117, `envs/lingo.yml`); checkpoints in `checkpoints/lingo/`, data in `data/lingo/dataset/` (one 3.25 GB Drive zip).
- **Scene:** `baselines/lingo-release/make_scene.py` voxelizes `work/video_chair/chair.glb` into LINGO's 2 cm occupancy grid (`data/lingo/dataset/Scene_vis/chair_only.npy`). LINGO is Y-up, metres, like our frame, so no axis conversion was needed.
- **Output:** 30 fps male SMPL-X, refit to SMPL-X neutral for our renderer (2–3 mm error). LINGO bodies sit about 5 cm below the floor, as in its training data, so each sequence is lifted to stand on our floor.
- **Placements:** start positions, goals and hand goals are in `inputs/jobs.json`. Seed 0; sampling 12–27 s per job.

## Two structural limits
1. **Vocabulary:** about 640 training phrases, with nothing like a foot on furniture, a step-up, a lunge/split squat or a stretch.
2. **Scene input is gated by keywords:** the code only gives the network the scene occupancy when the text contains one of a few hard-coded keywords (`walk`, `sit down`, `lie down`, `kick`, …). P1, P3 and P4 contain none, so for those prompts the model never "sees" the chair.

## Per prompt

| Run | What the body does | Foot–chair contact |
|---|---|---|
| P1 (verbatim split squat) | Stands with its back to the chair, shifts weight; no split squat | feet ≥ 0.58 m away |
| `squat down` | Deep two-foot squat in front of the chair | none on the chair |
| `kick chair with left foot` | Stands still; no kick | feet 0.38 m away |
| P2 (verbatim) | Walks ~1.3 m to the chair, then stands; never sits ("sits down" ≠ keyword `sit down`) | right foot touches a chair leg at floor level |
| **`walk` → `sit down on chair`** | **Walks up and sits naturally (only good result)** | feet stay on floor |
| P3 | Bends and reaches the right **hand** to the seat, straightens; no foot lift | feet ≥ 0.28 m away |
| `kick chair with right foot` | Stands still; no kick | — |
| `crawl on to chair` | Both hands on the seat, then stands back up | left foot touches the chair base |
| P4 | Stops after 1.4 s, just standing | — |

- No foot rises more than 6 cm in any run, and no foot ever lands on the seat (0.47 m).
- **Penetration:** the measured value stays at about 1.2 cm or less, but this misses passing through the thin seat. In the walk→sit run the pelvis ends 14 cm below the seat top (0.34 m vs 0.48 m), so the body sinks into the seat, matching an offset present in LINGO's own data.

## Files
- **Here:** per run, `<job>.npz` (SMPL-X neutral, our world frame), `.mp4`, `.png` (4-frame sheet), `_metrics.json`. LINGO inputs are in `inputs/`; raw output and logs in `raw/`.
- **`baselines/lingo-release/`:** `make_scene.py`, `make_inputs.py`, `run_sample.py`, `export_npz.py`, `contact_sheet.py`, `run.sh`, `NOTES.md`.
