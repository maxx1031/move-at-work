"""Quality judge (local Qwen3-VL): pick the best composed start frame, and check a generated video.

Runs as a subprocess in the `standup` env, so its GPU memory is freed before the video models run.

  python -m standup_agent.judge frames --job job.json --cands a.png b.png ... --out choice.json
  python -m standup_agent.judge video  --job job.json --video video.mp4 --out check.json

Both write JSON with per-criterion scores (0-10) and an overall decision.
"""
import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import reasoner as R
from . import tracing as T

CHECK_Q = """Look at this image of a person. Answer each question strictly with true or false:
{questions}
Also: is the person's body anatomically natural (normal number of arms/legs/fingers, undistorted face and hands)?
Return JSON: {{"answers": [true/false for each numbered question], "anatomy_ok": true/false}}"""

VIDEO_Q = """These frames (in time order) come from an AI-generated exercise video that should show:
exercise: {exercise}
motion: {motion}
constraints: {constraints}

Score from 0 to 10 (10 = perfect):
- exercise: the person actually performs this exercise with roughly correct form and range of motion (0 if a
  different movement, acrobatics, floating, flipping, or no clear repetition)
- constraints: the stated constraints are respected (what must stay still stays still)
- single_person: one person only, throughout (0 if anyone else appears at any time)
- scene_stable: background and camera viewpoint stay the same throughout (0 if the camera pans or the place changes)
- anatomy: natural body throughout (0 for extra/missing limbs, broken joints, melting)
Return JSON: {{"exercise": n, "constraints": n, "single_person": n, "scene_stable": n, "anatomy": n,
"issue": "biggest problem in a few words"}}"""

KEY_Q = """Answer strictly about this single image: is it true that {q}?
Return JSON: {{"yes": true/false}}"""

# A frame/video must reach these minimums on every criterion to pass.
VIDEO_MIN = dict(exercise=6, constraints=5, single_person=8, scene_stable=7, anatomy=6)
FRAME_W = dict(identity=2.0, pose=2.0, contact=1.5, scene=1.0, single_person=1.5, anatomy=1.0)


def _thumb(im, side=640):
    im = im.copy()
    im.thumbnail((side, side))
    return im


_CLIP = {}


def identity_similarity(crop, photos):
    """Mean CLIP image-embedding cosine similarity between the person crop and the user's photos (appearance /
    outfit match; the 8B VLM was unreliable at 'same person?' judgements)."""
    import torch
    from transformers import CLIPModel, CLIPProcessor
    if not _CLIP:
        _CLIP["m"] = CLIPModel.from_pretrained("openai/clip-vit-large-patch14").to("cuda").eval()
        _CLIP["p"] = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")
    m, p = _CLIP["m"], _CLIP["p"]
    ims = [crop] + [person_crop(Image.open(x).convert("RGB")) for x in photos]
    with torch.no_grad():
        f = m.get_image_features(**p(images=ims, return_tensors="pt").to("cuda"))
    f = torch.nn.functional.normalize(f, dim=-1)
    return round(float((f[1:] @ f[0]).mean()), 3)


SIDE_Q = """Answer about the person in this image (left/right always mean the left/right side of the IMAGE):
1. Which side of the image is the person facing (where the face and chest point)?
2. {second}
Return JSON: {{"facing": "left|right|camera|away", "other": "left|right|neither"}}"""


def orientation_ok(vlm, im, job):
    """Exercise-specific front/back check from two image-relative left/right answers (an 8B VLM answers these
    reliably, unlike 'is the person facing the staircase?'). None if the exercise has no orientation rule."""
    rule, obj = job.get("orientation"), job.get("object", "object")
    # Only for raised-foot exercises: "which side is the object on" is ambiguous for objects on both sides of the
    # person (e.g. stair handrails); the pose checklist (hands on the object, back against the wall) covers those.
    if rule not in ("foot_front", "foot_back"):
        return None, {}
    second = (f"Which side of the image is the raised foot (the foot resting on the {obj}) on, relative to the "
              f"person's body?" if rule.startswith("foot") else
              f"Which side of the person is the {obj} on, in the image?")
    try:
        a, _ = vlm.ask_json([("Image:", _thumb(im))], SIDE_Q.format(second=second), max_new_tokens=40)
    except ValueError:
        return False, {}
    facing, other = str(a.get("facing", "")).lower(), str(a.get("other", "")).lower()
    if facing not in ("left", "right") or other not in ("left", "right"):
        return False, a
    same = facing == other
    return (same if rule in ("foot_front", "face_object") else not same), a


def person_crop(im, pad=0.08):
    """Crop to the largest detected person (the user is small in a full scene frame)."""
    from .extract_identity import person_box
    b = person_box(R.person_detector(), im, score=0.7)
    if b is None:
        return im
    x0, y0, x1, y1 = b
    p = pad * (y1 - y0)
    return im.crop((max(0, x0 - p), max(0, y0 - p), min(im.width, x1 + p), min(im.height, y1 + p)))


SOFT_CHECK_WORDS = ("plank line", "straight diagonal", "straight line", "upright")


def face_visible(im, min_score=12.0):
    """Keypoint check that the face is turned to the camera enough to carry identity: the nose and at least one
    eye must be confidently detected. Calibrated: a back-to-camera frame scored nose 9.5, side and three-quarter
    views 14-20, the user photo 14 (the model still guesses a nose from behind, at lower confidence)."""
    import torch
    if not _KP:
        from torchvision.models.detection import KeypointRCNN_ResNet50_FPN_Weights, keypointrcnn_resnet50_fpn
        w = KeypointRCNN_ResNet50_FPN_Weights.DEFAULT
        _KP["m"], _KP["tf"] = keypointrcnn_resnet50_fpn(weights=w).to("cuda").eval(), w.transforms()
    with torch.no_grad():
        o = _KP["m"]([_KP["tf"](im).to("cuda")])[0]
    if not len(o["scores"]) or o["scores"][0] < 0.7:
        return False, None
    v = o["keypoints_scores"][0].cpu().numpy()  # 0 nose, 1 left eye, 2 right eye
    return bool(v[0] > min_score and max(v[1], v[2]) > min_score), [round(float(x), 1) for x in v[:3]]


def start_angles_ok(im, spec):
    """Start frame must be the movement's true start (e.g. push-ups: arms straight; squats: standing tall).
    A push-up start frame with elbows already bent produced static clips on every seed. Uses the best-visible
    side's joint angle from the keypoint model. Returns (ok, measured)."""
    if not spec:
        return True, {}
    import torch
    face_visible(im)  # ensures the keypoint model is loaded
    with torch.no_grad():
        o = _KP["m"]([_KP["tf"](im).to("cuda")])[0]
    if not len(o["scores"]) or o["scores"][0] < 0.7:
        return False, {}
    k = o["keypoints"][0, :, :2].cpu().numpy()
    v = o["keypoints_scores"][0].cpu().numpy()

    def ang(a, b, c):
        v1, v2 = k[a] - k[b], k[c] - k[b]
        return float(np.degrees(np.arccos(np.clip(v1 @ v2 / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6), -1, 1))))
    tri = dict(elbow=((5, 7, 9), (6, 8, 10)), knee=((11, 13, 15), (12, 14, 16)))
    meas, ok = {}, True
    for key, lo in spec.items():
        j = key[:-4]
        sides = tri[j]
        best = max(sides, key=lambda t: v[list(t)].min())
        meas[j] = round(ang(*best), 1)
        ok &= meas[j] >= lo
    return ok, meas


def judge_frames(vlm, job, cands, drift_max=40.0, identity_min=0.6):
    """Judge each composed start-frame candidate on its own (more reliable for an 8B VLM than ranking them all at
    once): exercise-specific yes/no pose checks, anatomy, identity vs the user photos, plus code checks for extra
    people (detector) and scene changes (background vs the original frame). Best = passes everything, then
    highest identity; otherwise the one with the most checks passed."""
    scene = Image.open(job["scene_frame"]).convert("RGB")
    ids = [("User photo:", _thumb(Image.open(p).convert("RGB"), 384)) for p in job["identity_photos"][:2]]
    questions = job.get("pose_checks") or []
    rows = []
    for c in cands:
        im = Image.open(c).convert("RGB")
        meas = measure_video([(0, scene.resize(im.size)), (1, im)])
        people = meas["people_per_frame"][1]
        drift = meas["max_bg_drift"]
        qtxt = "\n".join(f"{k + 1}. {q}" for k, q in enumerate(questions)) or "1. Is a person visible?"
        try:
            chk, _ = vlm.ask_json([("Image:", _thumb(im))], CHECK_Q.format(questions=qtxt), max_new_tokens=120)
            answers = [bool(x) for x in chk.get("answers", [])][:max(len(questions), 1)]
            anatomy = bool(chk.get("anatomy_ok", False))
        except ValueError:
            answers, anatomy = [], False
        identity = identity_similarity(person_crop(im), job["identity_photos"])
        orient, orient_raw = orientation_ok(vlm, im, job)
        if orient is not None:
            answers = answers + [orient]
        n_q = max(len(questions), 1) + (orient is not None)
        # The left/right orientation rule must hold; of the yes/no pose questions one miss is tolerated when there
        # are 2+ (the 8B VLM gives occasional false "no"s, e.g. "straight plank line" on good push-up frames).
        q_ans = answers[:len(answers) - (orient is not None)]
        # Only body-SHAPE questions are soft (the VLM gives false "no"s on "straight plank line"); contact and
        # position questions ("both feet flat on the floor?") must all pass: tolerating any one miss let a
        # split-squat pose through as a chair-squat start.
        soft = [any(w in q.lower() for w in SOFT_CHECK_WORDS) for q in questions]
        hard_ok = all(a for a, sft in zip(q_ans, soft) if not sft)
        soft_misses = sum(1 for a, sft in zip(q_ans, soft) if sft and not a)
        pose_ok = (len(answers) == n_q and (orient is not False) and bool(q_ans) and hard_ok and soft_misses <= 1)
        face_ok, face_scores = face_visible(im)
        start_ok, start_meas = start_angles_ok(im, job.get("start_angles") or {})
        face_ok = face_ok and start_ok  # both are objective keypoint checks on the candidate
        ok = pose_ok and anatomy and people == 1 and drift <= drift_max and identity >= identity_min and face_ok
        rows.append(dict(candidate=str(c), passed=ok, pose_answers=answers, orientation=orient_raw,
                         anatomy_ok=anatomy, face_visible=face_ok, face_scores=face_scores, start_angles=start_meas,
                         start_angles_ok=start_ok, people=people,
                         bg_drift=drift, identity=identity,
                         rank=(ok, sum(answers) + anatomy + face_ok + (people == 1) + (drift <= drift_max), identity)))
    best = max(rows, key=lambda r: r["rank"])
    for r in rows:
        r.pop("rank")
    return dict(best=best["candidate"], best_passed=best["passed"], questions=questions, candidates=rows)


def sample_video(path, n=8):
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for i in np.linspace(0, total - 1, n).round().astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            out.append((int(i), Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))))
    cap.release()
    return out


def measure_video(frames, det=None, people_score=0.85, people_min_h=0.12, drift_side=(72, 128)):
    """Objective checks the VLM misses: people per frame (detector) and background drift vs frame 0
    (mean abs grey difference outside person boxes). Calibrated on known-good/bad clips: a camera pan onto a
    different place gave ~68, stable clips 17-41; a bystander appears as >=2 people in most frames."""
    import torch
    from torchvision.ops import nms
    det = det or R.person_detector()
    counts, greys, masks = [], [], []
    for _, im in frames:
        x = det.tf(im).to(det.device)
        with torch.no_grad():
            o = det.model([x])[0]
        k = (o["labels"] == 1) & (o["scores"] > people_score)
        b = o["boxes"][k]
        b = b[nms(b, o["scores"][k], 0.3)].cpu().numpy() if len(b) else b.cpu().numpy()
        b = [bb for bb in b if (bb[3] - bb[1]) / im.height > people_min_h]
        counts.append(len(b))
        w, h = drift_side
        m = np.ones((h, w), bool)
        for x0, y0, x1, y1 in b:
            m[int(y0 / im.height * h):int(y1 / im.height * h) + 1, int(x0 / im.width * w):int(x1 / im.width * w) + 1] = False
        masks.append(m)
        greys.append(np.asarray(im.convert("L").resize((w, h)), float))
    drift = [float(np.abs(g - greys[0])[m & masks[0]].mean()) for g, m in zip(greys[1:], masks[1:])]
    multi = sum(c >= 2 for c in counts) / max(len(counts), 1)
    return dict(people_per_frame=counts, multi_person_frac=round(multi, 2), max_bg_drift=round(max(drift), 1))


_KP = {}


def count_reps(x, frac=0.35):
    """Repetitions in a joint-angle series: excursions away from the resting level and back, with hysteresis at
    `frac` of the 10-90 percentile range (a single lift-and-hold counts 0 completed reps, jitter counts none)."""
    x = np.asarray(x, float)
    if len(x) < 4:
        return 0
    x = np.convolve(np.pad(x, 1, mode="edge"), np.ones(3) / 3, mode="valid")  # light smoothing, no edge dips
    lo, hi = np.percentile(x, 10), np.percentile(x, 90)
    if hi - lo < 1e-6:
        return 0
    rest = x[0]
    away_is_high = abs(hi - rest) > abs(lo - rest)
    far = hi - frac * (hi - lo) if away_is_high else lo + frac * (hi - lo)
    near = lo + frac * (hi - lo) if away_is_high else hi - frac * (hi - lo)
    state, n = "rest", 0
    for v in x:
        is_far = v >= far if away_is_high else v <= far
        is_near = v <= near if away_is_high else v >= near
        if state == "rest" and is_far:
            state = "away"
        elif state == "away" and is_near:
            state, n = "rest", n + 1
    return n


def range_of_motion(path, n=24, unilateral=False):
    """Objective motion measurement with COCO keypoints (torchvision Keypoint R-CNN): 10th-90th percentile range of
    the elbow / knee / hip angles and of the hip height (normalised by body height) over n frames. Calibrated by eye:
    real incline push-ups elbow ~100 vs static 8; real step-up knee ~117 + hip rise ~0.9 vs toe tap 37 / 0.03."""
    import torch
    from torchvision.models.detection import KeypointRCNN_ResNet50_FPN_Weights, keypointrcnn_resnet50_fpn
    if not _KP:
        w = KeypointRCNN_ResNet50_FPN_Weights.DEFAULT
        _KP["m"], _KP["tf"] = keypointrcnn_resnet50_fpn(weights=w).to("cuda").eval(), w.transforms()

    def ang(a, b, c):
        v1, v2 = a - b, c - b
        return float(np.degrees(np.arccos(np.clip(v1 @ v2 / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6), -1, 1))))

    rows = []
    for _, im in sample_video(path, n):
        with torch.no_grad():
            o = _KP["m"]([_KP["tf"](im).to("cuda")])[0]
        if not len(o["scores"]) or o["scores"][0] < 0.7:
            continue
        k = o["keypoints"][0, :, :2].cpu().numpy()  # 5/6 sho, 7/8 elb, 9/10 wri, 11/12 hip, 13/14 knee, 15/16 ank
        vis = o["keypoints_scores"][0].cpu().numpy()  # per-keypoint confidence (occluded far-side joints are low)
        body_h = max(k[15, 1], k[16, 1]) - min(k[5, 1], k[6, 1])
        rows.append(dict(elbow=[ang(k[5], k[7], k[9]), ang(k[6], k[8], k[10])],
                         knee=[ang(k[11], k[13], k[15]), ang(k[12], k[14], k[16])],
                         hip_angle=[ang(k[5], k[11], k[13]), ang(k[6], k[12], k[14])],
                         vis=dict(elbow=[vis[[5, 7, 9]].min(), vis[[6, 8, 10]].min()],
                                  knee=[vis[[11, 13, 15]].min(), vis[[12, 14, 16]].min()],
                                  hip_angle=[vis[[5, 11, 13]].min(), vis[[6, 12, 14]].min()]),
                         hip_y=(k[11, 1] + k[12, 1]) / 2, body_h=body_h, ankles=k[[15, 16]].copy()))
    if len(rows) < n // 3:
        return {"frames_with_person": len(rows)}
    rng = lambda a: float(np.percentile(a, 90) - np.percentile(a, 10))
    # Per joint, measure the side the model sees best (mean keypoint confidence over the clip): in side view the
    # far arm/leg is occluded and its guessed angle jitters (a static wall push-up read 31.7 deg via the far arm).
    # One-sided exercises (hip extension, leg swing, marching, step-up) move one limb that may be the far one:
    # take the side that moves more there.
    out, reps = {}, {}
    for j in ("elbow", "knee", "hip_angle"):
        side = int(np.argmax([np.mean([r["vis"][j][s] for r in rows]) for s in (0, 1)]))
        per_side = [rng([r[j][s] for r in rows]) for s in (0, 1)]
        s_used = int(np.argmax(per_side)) if unilateral else side
        out[j] = round(per_side[s_used], 1)
        reps[j] = count_reps([r[j][s_used] for r in rows])
    out["reps"] = reps
    bh = max(np.median([r["body_h"] for r in rows]), 1)
    out["hip_rise"] = round(rng([r["hip_y"] for r in rows]) / bh, 3)
    # Directional: how far the hips go ABOVE their starting height (image y grows downward). Crouching lowers the
    # hips and counted as "hip_rise"; a real step-up raises them by roughly the step height.
    hy = np.array([r["hip_y"] for r in rows])
    out["hip_up"] = round(float((np.median(hy[:3]) - np.percentile(hy, 5)) / bh), 3)
    # Feet planted: max drift of each ankle from its start position (body heights). Squats/push-ups keep both feet
    # in place; lunges/step-ups move one (a lunge clip passed as a chair squat on knee range alone).
    a = np.array([r["ankles"] for r in rows])  # (T, 2, 2)
    start = np.median(a[:3], axis=0)
    out["foot_drift"] = round(float(np.percentile(np.linalg.norm(a - start, axis=-1), 90, axis=0).max() / bh), 3)
    out["frames_with_person"] = len(rows)
    return out


def judge_video(vlm, job, video, drift_max=50.0, multi_max=0.25):
    frames = sample_video(video, 12)
    meas = measure_video(frames)
    frames = frames[::max(1, len(frames) // 8)][:8]
    imgs = [(f"Frame {k + 1}:", _thumb(im, 480)) for k, (_, im) in enumerate(frames)]
    obj, raw = vlm.ask_json(imgs, VIDEO_Q.format(exercise=job.get("exercise", ""), motion=job.get("motion", ""),
                                                 constraints=job.get("constraints", "")), max_new_tokens=300)
    scores = {k: obj.get(k) for k in VIDEO_MIN}
    issue = obj.get("issue", "")
    # Key-moment checks: the defining position of the exercise must appear in at least one frame (the holistic
    # "exercise" score passed a toe tap as a step-up). Asked per frame, so it's a concrete visual question.
    seen = {}
    for q in job.get("motion_checks", []):
        hits = []
        for k, (_, im) in enumerate(frames):
            try:
                a, _ = vlm.ask_json([("Image:", _thumb(im, 512))], KEY_Q.format(q=q), max_new_tokens=20)
                hits.append(bool(a.get("yes", False)))
            except ValueError:
                hits.append(False)
        seen[q] = sum(hits)
        if not any(hits):
            issue = f"key moment never seen: '{q}'; " + issue
    if meas["multi_person_frac"] >= multi_max:
        scores["single_person"] = 0
        issue = f"extra person in {int(meas['multi_person_frac'] * 100)}% of frames; " + issue
    if meas["max_bg_drift"] > drift_max:
        scores["scene_stable"] = 0
        issue = f"background drifts (camera/scene change, {meas['max_bg_drift']}); " + issue
    # Pass/fail: objective checks first. With a range-of-motion spec, motion is judged by keypoints (the VLM's
    # holistic exercise/constraints scores were wrong both ways: a toe tap passed as a step-up, real push-ups
    # failed); they stay as information. Without a spec (holds, stretches) the VLM's exercise score decides.
    rom_spec = job.get("rom") or {}
    rom = range_of_motion(video, unilateral=bool(job.get("unilateral"))) if rom_spec else {}
    rom_fail = [f"{j} {rom.get(j)} < {m}" for j, m in rom_spec.items()
                if not j.endswith("_max") and not (rom.get(j, 0) >= m)]
    rom_fail += [f"{j[:-4]} {rom.get(j[:-4])} > {m} (feet should stay planted)" for j, m in rom_spec.items()
                 if j.endswith("_max") and rom.get(j[:-4]) is not None and rom[j[:-4]] > m]
    # Repetition-based exercises must show at least 2 completed repetitions of a measured joint (ROM alone passed a
    # single lift-and-hold hip extension and a few shuffling kicks as a leg swing). hip_rise has no rep count.
    min_reps = job.get("min_reps", 2)
    angle_joints = [j for j in rom_spec if j in ("elbow", "knee", "hip_angle")]  # *_max limits have no reps
    if rom and angle_joints and min_reps and max((rom.get("reps") or {}).get(j, 0) for j in angle_joints) < min_reps:
        rom_fail.append(f"reps {max((rom.get('reps') or {}).get(j, 0) for j in angle_joints)} < {min_reps}")
    fails = [k for k in ("single_person", "scene_stable", "anatomy")
             if not isinstance(scores[k], (int, float)) or scores[k] < VIDEO_MIN[k]]
    if rom_spec:
        if rom_fail:
            fails.append("range_of_motion")
            issue = f"not enough movement ({', '.join(rom_fail)}); " + issue
        else:
            # Borderline motion (< 1.5x the minimum) that the VLM also rejects outright is not trusted; large,
            # clear motion passes on measurement alone (the VLM rated real push-ups with a 100 deg elbow range 3/10).
            borderline = any(rom.get(j, 0) < 1.5 * m for j, m in rom_spec.items() if not j.endswith("_max"))
            vlm_ex = scores.get("exercise")
            if borderline and isinstance(vlm_ex, (int, float)) and vlm_ex <= 2:
                fails.append("borderline_motion_vlm_disagrees")
                issue = "borderline range of motion and the VLM does not recognise the exercise; " + issue
    # Without a movement measure (no angle / hip_up spec, e.g. calf raises and holds with only a feet-planted
    # limit) the VLM's exercise score still decides whether the exercise is recognisable.
    movement_measured = any(j in ("elbow", "knee", "hip_angle", "hip_up", "hip_rise") for j in rom_spec)
    if not movement_measured and (not isinstance(scores["exercise"], (int, float))
                                  or scores["exercise"] < VIDEO_MIN["exercise"]):
        fails.append("exercise")
    return dict(scores=scores, passed=not fails, failed=fails, issue=issue, measurements=meas, key_moments=seen,
                range_of_motion=rom, rom_spec=rom_spec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["frames", "video"])
    ap.add_argument("--job", required=True)
    ap.add_argument("--cands", nargs="*")
    ap.add_argument("--video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--vlm", default=str(R.DEFAULT_VLM))
    a = ap.parse_args()
    job = json.loads(Path(a.job).read_text())
    vlm = R.VLM(a.vlm)
    # run.py passes its current span (compose_start_frame / generate_video) so the verdict joins that trace.
    tp = os.environ.get("STANDUP_TRACEPARENT")
    with T.span(f"judge.{a.what}", "EVALUATOR", input={"job_id": job.get("job_id"), "video": a.video,
                                                       "candidates": a.cands},
                parent=T.extract({"traceparent": tp}) if tp else None,
                metadata={"exercise": job.get("exercise"), "route": job.get("route")}) as s:
        res = judge_frames(vlm, job, a.cands) if a.what == "frames" else judge_video(vlm, job, a.video)
        T.set_output(s, {k: res.get(k) for k in ("passed", "failed", "scores", "issue", "range_of_motion")}
                     if a.what == "video" else {k: res.get(k) for k in ("best", "best_passed")})
    T.flush()
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps(res if a.what == "video" else {"best": res["best"], "passed": res["best_passed"]}, indent=None))


if __name__ == "__main__":
    main()
