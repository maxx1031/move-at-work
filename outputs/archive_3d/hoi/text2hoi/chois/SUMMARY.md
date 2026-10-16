# CHOIS: text → HOI on the video folding chair

**Result: CHOIS does none of the 4 prompts.** In every run it grabs the chair from a bent-over start pose, then lifts, tilts or carries it. No foot is ever lifted toward the seat (0.47 m). Changing the text barely changes the behaviour; the object waypoints and the model's manipulation prior dominate.

## Setup
- **Chair:** `work/video_chair/chair.glb` converted with `baselines/chois_release/prepare_unseen_object.py --up y` (Y→Z up, floor z=0, centred, 256³ SDF).
- **Start conditions:** OMOMO sequence `sub17_woodchair_000` supplies the initial pose (`--input_first_human_pose`, hands already on the chair), the body shape and the XY waypoints. The prompt replaces the text.
- **Model:** official `model-10.pt` with guidance; 120 frames @ 30 fps; 1 sample per prompt.
- **Variants:**
  - `default`: OMOMO waypoints.
  - `static`: all XY waypoints set to the start position; rotation stays free.
- **npz:** SMPL-X (`trans`, `root_orient`, `pose_body` 21×3, zero hands, 16 `betas`, `gender`), `verts`, `joints24`, `obj_rot_mat`, `obj_com_pos`. Object verts = R·rest + com, with rest = `data/chois_release/custom_objects/obj/video_chair.ply`. Frame is Z up.

## Metrics
Translation and rotation are measured relative to frame 0. Foot distance is from SMPL-X foot vertices to 30k points on the chair surface (unsigned). "Sole" is the lowest foot vertex.

| run | chair transl. (m) | rot. (°) | tilt (°) | L/R min dist (m) | frames <3 cm (L/R) | max sole height (m) | sampling / total (s) |
|---|---|---|---|---|---|---|---|
| P1_default | 1.78 | 180 | 42 | .001/.003 | 7/9 | 0.08 | 5.9/61 |
| P1_static | 0.70 | 140 | 87 | .049/.001 | 0/13 | 0.04 | 6.3/58 |
| P2_default | 1.69 | 132 | 60 | .006/.002 | 5/7 | 0.09 | 4.2/50 |
| P2_static | 0.14 | 104 | 11 | .001/.001 | 11/17 | 0.05 | 6.4/65 |
| P3_default | 1.68 | 130 | 75 | .093/.001 | 0/7 | 0.07 | 5.1/55 |
| P3_static | 0.31 | 123 | 8 | .008/.002 | 4/16 | 0.05 | 5.3/67 |
| P4_default | 1.64 | 48 | 10 | .001/.001 | 16/28 | 0.12 | 5.9/63 |
| P4_static | 0.12 | 92 | 4 | .001/.000 | 10/25 | 0.07 | 4.6/56 |

Near-contacts all happen with the sole at most 7 cm above the floor: the foot is next to or between the chair legs, never on the seat. Most of the total time is init, which loads all 28k OMOMO training windows.

## Per prompt
- **P1 (split squat):** fails.
  - `default`: lifts the chair overhead, walks about 2 m, sets it down, crouches.
  - `static`: lifts it to chest height and tilts it 87°, sets it down, bends over it.
- **P2 (walk to chair, sit):** fails.
  - `default`: carries the chair.
  - `static`: tips the chair, bends deeply with hands on the seat (body centroid down to 0.52 m), stands up. Never sits.
- **P3 (step up, right foot):** fails. It carries or turns the chair and stands next to it; the right sole never goes above 3 cm.
- **P4 (heel on seat, stretch):** fails. It hunches or crouches over the seat with its hands on it (loosely "leans forward"); no heel on the seat.

## Caveats
- Used the SMPL-X copies bundled with the CHOIS data.
- One start sequence and one sample per prompt.
- CHOIS's feet-on-floor guidance actively prevents a foot on the seat. Running without guidance or without the fixed start pose was not tried.

**Reproduce:** `bash baselines/text2hoi/run_chois_text2hoi.sh; bash baselines/text2hoi/render_all.sh chois`
