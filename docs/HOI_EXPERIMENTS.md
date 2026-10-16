# Human–object interaction experiments (Just Standup)

> **Archived (2026-10-15).** The 3D motion pipeline code (`pipeline/`, `baselines/`, the HOI/reconstruction submodules) has been removed from the repo. Its result videos and per-model summaries now live in `outputs/archive_3d/` (former `outputs/hoi/` paths below map to `outputs/archive_3d/hoi/`).

Date: 2026-10-14. Target exercise: **Bulgarian split squat** (rear foot on a chair seat, single-leg squat), with the chair staying still. Plus a control prompt (sit) and two other foot-on-chair prompts.

## 1. Can existing HOI models generate it from text + the chair?

All runs use the SAM 3D folding chair reconstructed from `data/404711060.mp4` (`work/video_chair/chair.glb`; seat 0.47 m).

Prompts:
- **P1:** split squat.
- **P2:** walk to the chair and sit.
- **P3:** step up with the right foot.
- **P4:** heel on the seat, hamstring stretch.

| Model | Object input | P1 split squat | P2 sit | P3 step up | P4 heel stretch | Chair stays still? |
|---|---|---|---|---|---|---|
| CHOIS | chair mesh (BPS) + waypoints | ❌ lifts/tilts chair | ❌ bends over it | ❌ carries it | ❌ hunches over seat | ❌ carried ≤1.8 m, rotated ≤180° |
| HOI-Diff* | chair point cloud | ❌ bends/crouches | ⚠️ sits, chair tilted ~25° | ❌ stands beside | ❌ sits | ❌ tilted, floating |
| LINGO | static scene grid | ❌ stands near chair | ✅ walks + sits (only with its own phrasing) | ❌ reaches with hand | ❌ stands | ✅ (static by construction) |

\*HOI-Diff ran without the BEHAVE dataset (license-gated): normalisation statistics were approximated, so its results are indicative only.

**No model ever put a foot on the seat** (highest sole: CHOIS 12 cm, HOI-Diff 32 cm, LINGO 6 cm). Why:
- **CHOIS** was trained on object manipulation only (carry/push/pull), and its guidance keeps both feet on the floor.
- **HOI-Diff** only knows BEHAVE's interactions (it learned to sit).
- **LINGO** has a ~640-phrase vocabulary with no foot-on-furniture actions. Its code also only passes the scene to the network for a few hard-coded keywords.

Per-run videos, contact sheets, metrics and summaries are in `outputs/hoi/text2hoi/{chois,hoidiff,lingo}/`.

### Scene-based baselines (no text input)

All three run on a custom chair (setup notes in `baselines/<repo>/NOTES.md`):
- **TRUMANS:** walks to the chair and sits.
- **DIMOS:** sits, from a hand-placed goal pose (its goal generator needs PROX).
- **SceneDiffuser:** places bodies anywhere in the scene, with no goal input.

None can be asked for a foot-on-seat exercise.

## 2. What works: motion prior + affordance-constrained optimisation

`pipeline/` (env `standup`):
1. **Object:** SAM 2 masks → VGGT cameras and points + MoGe-2 metric scale → floor plane → SAM 3D Objects mesh aligned to the measured points (`video_recon.py`, `align_mesh.py`).
2. **Affordance:** seat surface, front edge and forward direction from the mesh (`affordance.py`).
3. **Motion source**, either:
   - **(a) generated:** Motion-X reverse-lunge seed, optimised so the rear foot rests on the seat, the front foot is planted, the knee follows a down/up profile, and there is no penetration (`optimize_bss.py`). Passes all 6 plausibility checks.
   - **(b) recovered from a video of a person:** GVHMR, refined against the reconstructed object with 2D keypoints and contact constraints (`refine_recovered.py`).
4. **Render** into the person-removed video (EffectErase) with the reconstructed object (`composite_video.py`).

Final videos are in `outputs/hoi/final/`:
- `effecterase_body_chair.mp4`: Bulgarian split squat.
- `v0_effecterase_body.mp4`: hamstring stretch (railing reconstruction too poor; body only).
- `v1_effecterase_body_table.mp4`: single-leg squat next to the table.

## 3. Open issues
- **Metric scale** comes from single-image depth (±15%). The seat height in `work/human_video` is *assumed* to be 0.45 m; a tape measurement would fix it.
- **Thin objects:** the railing could not be fitted with a generated mesh. A parametric railing (tube along the top bar plus posts) fitted to the masks would be needed.
- **Licenses:**
  - EffectErase: CC BY-NC.
  - ProPainter: S-Lab non-commercial.
  - SMPL-X, the HOI datasets and BEHAVE: research licenses.
  - These need replacing or licensing before any commercial use.
