# HOI-Diff: text → HOI without BEHAVE (approximation)

**Result: no prompt is generated as asked.** The only text-consistent behaviour is a sit/recline for P2 at full 1000 sampling steps, and even then the chair is tilted ~25° and doesn't support the body. No run puts a foot on the seat: the highest sole is 0.32 m, and the seat is at 0.47 m.

## What can't be run as intended
- **Normalisation:** BEHAVE-derived `dataset/behave_t2m/Mean_local.npy` / `Std_local.npy` are missing, so normalisation is approximated.
  - Human part (263 dims): HumanML3D evaluator stats (`t2m/.../Comp_v6_KLD01/meta`), with the feat_bias=25 groups (0:4, 259:263) undone.
  - Object part (6 dims): mean 0, std 0.6 — a guess.
- **Object template:** the chairwood template / `sample_points.npy` is also missing, so a true "built-in chairwood" run is impossible.

## What was run
- **Object:** 1024 points sampled on our chair (`work/video_chair/chair.glb`), centred at their mean, Y up. The model predicts the object pose, so the chair's start pose isn't ours.
- **Variants:**
  - `ourchair`: verbatim prompt.
  - `chairwood`: "chair"→"chairwood" in the text, same geometry.
- **Settings:** each variant at the official 5 steps (`skip_timesteps=995`) and at full 1000 steps (`_full1000`). 120 frames @ 20 fps; CFG 2.5; contact guidance on.
- **Human mesh:** SMPL neutral fitted with the repo's SMPLify to the 22 joints (error 10–16 mm).
- **npz:** `global_orient`, `body_pose`, `betas`, `transl`, `verts`, `obj_rot_mat`, `obj_trans`, `obj_centre_in_mesh`. Frame is Y up; object = R·(mesh − centre) + t.
- **Runtime:** batch of 8 takes 3.4 s at 5 steps and 64 s at 1000 steps, plus 7 s model load. SMPLify wasn't timed.

## Metrics
Tilt is the chair's up-axis angle from vertical. Distance is from foot vertices to the chair surface.

| run | chair transl. (m) | rot. (°) | tilt at frame 0 (°) | chair lowest point at frame 0 (m) | L/R min dist (m) | body-centroid height (m) |
|---|---|---|---|---|---|---|
| P1 ourchair / chairwood | .78/.75 | 16/23 | 7/23 | .57/.48 | .42/.37 | 1.09 (flat) |
| P2 | .20/.17 | 7/7 | 19/21 | .39/.38 | .41/.39 | 1.09 (flat) |
| P3 | .19/.16 | 6/4 | 21/23 | .37/.32 | .33/.36 | 1.05–1.09 |
| P4 | .12/.11 | 9/8 | 20/20 | .38/.33 | .32/.32 | 1.07 |
| P1 full1000 | .96/.55 | 7/15 | 25/22 | −.01/−.13 | .11/.06 | .74–.86 / .54–.85 |
| P2 full1000 | .39/1.06 | 27/13 | 25/23 | .10/.13 | .21/.26 | .58–.82 / .73–.88 |
| P3 full1000 | .95/1.14 | 10/18 | 16/21 | .37/.51 | .28/.37 | ~1.0 |
| P4 full1000 | .43/.42 | 20/17 | 26/28 | .07/.11 | .21/.16 | .58–.83 |

No run has any frame with a foot within 3 cm of the chair. The highest sole is 0.32 m (P2_chairwood_full1000, legs stretched forward while reclining).

## Per prompt (from the frames)
- **5-step, all prompts:** the person stands still with arms down. The chair hovers 0.3–0.6 m off the floor, tilted ~20°, partly overlapping the body. The text has no visible effect.
- **P1 full1000:** fails.
  - `ourchair`: bends forward over the chair with hands on the seat, small steps.
  - `chairwood`: sits or crouches, then folds forward into a collapsed crouch.
  - No foot on the seat.
- **P2 full1000:** sits, which is the only text-consistent behaviour (partial success). The pose is a recline, but the chair is tilted ~25° and offset, so the pelvis isn't on the seat; one frame has a body-collapse artifact.
- **P3 full1000:** fails. Stands or walks beside the chair; the right foot stays at most 6 cm above the floor.
- **P4 full1000:** fails. Almost identical to P2 (sit / recline), with no heel on the seat.

## Caveat
Normalisation stats and the object frame are approximated, so these results are only indicative of what the model would do with real BEHAVE data. The official 5-step sampler gives near-static output under these stats.

**Scripts:** `baselines/text2hoi/`. **Raw outputs:** `work/text2hoi/`.
