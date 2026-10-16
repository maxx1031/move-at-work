"""Just Standup workout agent: scene video + user photos -> 3 exercise suggestions -> 3 generated workout videos.

python -m standup_agent.run --video data/404711060.mp4 --photos data/user_photos --out outputs/agent/404711060 \
    --routes compose_i2v [--plan_only]

1. Sample frames, describe the user's appearance from their photos (Qwen3-VL).
2. List objects in the scene and what each safely affords (seat / table_edge / wall / rail / step / floor_open).
3. Choose 3 exercises from catalog.json (ExRx.net-based) that fit those objects; pick the best frame per exercise.
4. For each suggestion and each route (backend), write a job (see BACKENDS.md) and run it in that backend's env.

Tracing: export ARIZE_SPACE_ID and ARIZE_API_KEY to send traces to Arize AX (see tracing.py): every VLM call, every
safety-rule override (GUARDRAIL spans) and every backend job, one trace per scene video.
"""
import argparse
import json
import os
import queue
import shutil
import sys
import subprocess
import threading
import time
from pathlib import Path

from . import reasoner as R
from . import tracing as T

REPO = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).with_name("backends.json")
# Artifacts, extra people and camera motion only: listing motions here ("jumping, kicking, walking away...") also
# damped the exercise motion itself (A/B test: clips froze in the start pose).
NEGATIVE = ("other people, second person, crowd, bystanders, camera movement, panning, zooming, scene change, cut, "
            "acrobatics, flipping, floating, levitating, distorted body, extra limbs, extra fingers, deformed hands, "
            "person merging with furniture, blurry, low quality, text, watermark")

_PRONOUNS = {"she": ("she", "her", "She"), "he": ("he", "his", "He"), "they": ("they", "their", "They")}


def _gender(text, pronoun):
    """Catalog constraints are written with she/her; adapt to the user's pronoun."""
    subj, poss, cap = _PRONOUNS.get(pronoun, _PRONOUNS["they"])
    for a, b in ((" she ", f" {subj} "), ("She ", f"{cap} "), (" her ", f" {poss} "), ("Her ", f"{poss.title()} ")):
        text = text.replace(a, b)
    return text


def build_prompts(sugg: R.Suggestion, identity: dict, scene_desc: str, fps=24):
    """Video prompt: who -> start pose -> movement with reps -> what stays still -> empty scene -> locked camera.
    Explicit constraints matter most for small movements (calf raises), which models otherwise over-animate."""
    ex, obj = sugg.exercise, sugg.object["name"]
    appearance, pronoun = identity["appearance"], identity.get("pronoun", "they")
    subj, _, cap = _PRONOUNS.get(pronoun, _PRONOUNS["they"])
    start = ex["start_pose"].format(object=obj)
    motion = ex["motion"].format(object=obj)
    constraints = _gender(" " + ex.get("constraints", "").format(object=obj), pronoun).strip()
    pose_checks = [q.format(object=obj) for q in ex.get("pose_checks", [])]
    motion_checks = [q.format(object=obj) for q in ex.get("motion_checks", [])]
    a = appearance[0].upper() + appearance[1:]
    # Motion first and active, one short stability clause (A/B-tested: long "stays still / only X moves" wording
    # froze Wan2.2 in the start pose; this version gave real repetitions with good form).
    brief = constraints.split(";")[0].strip().rstrip(".")
    prompt = (f"{a} {start}. {cap} does {ex.get('reps', 'slow repetitions')} of {ex['name'].split(' (')[0].lower()} "
              f"using the {obj}: {motion}. {brief[:1].upper() + brief[1:] + '. ' if brief else ''}{scene_desc} Static camera, {subj} is the "
              f"only person, natural lighting, photorealistic.")
    return dict(start_pose_instruction=f"{appearance} {start}", prompt=prompt, negative_prompt=NEGATIVE,
                motion=motion, constraints=constraints, pose_checks=pose_checks, orientation=ex.get("orientation"),
                motion_checks=motion_checks, rom=ex.get("rom", {}), unilateral=ex.get("unilateral", False),
                start_angles=ex.get("start_angles", {}), min_reps=ex.get("min_reps", 2),
                num_frames=int(ex.get("duration_s", 5) * fps) + 1, fps=fps)


def _call(cmd, log_path, gpu):
    """Run a step as a subprocess; it gets the current span as STANDUP_TRACEPARENT (the judge's spans join it)."""
    with open(log_path, "a") as log:
        log.write("\n$ " + " ".join(map(str, cmd)) + "\n")
        log.flush()
        return subprocess.call([str(c) for c in cmd], cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu), "PIP_CONFIG_FILE": "/dev/null",
                                    "STANDUP_TRACEPARENT": T.inject().get("traceparent", "")})


def compose_start_frame(sugg_jobs, registry, gpu, n_cands, rounds=2):
    """Best-of-N start frame: Qwen-Image-Edit composes n candidates, the judge checks each (pose checklist,
    anatomy, one person, scene unchanged, identity). If none passes, another batch with new seeds is composed."""
    j0 = sugg_jobs[0]
    # Worker threads start with an empty trace context: join the plan's trace through the job's stored context.
    with T.span("compose_start_frame", "CHAIN", input={"suggestion": Path(j0["out_dir"]).parent.name},
                parent=T.extract(j0.get("trace_context")), metadata={"gpu": gpu, "n_cands": n_cands}) as s:
        start = _compose_start_frame(j0, registry, gpu, n_cands, rounds)
        T.set_output(s, {"start_frame": start})
        if start is None:
            T.fail(s, "no start frame candidate was composed")
    return start


def _compose_start_frame(j0, registry, gpu, n_cands, rounds):
    d = Path(j0["out_dir"]).parent / "start"
    d.mkdir(parents=True, exist_ok=True)
    best = d / "start_frame.png"
    if best.exists():
        return str(best)
    portrait = j0["height"] >= j0["width"]
    W, H = (704, 1280) if portrait else (1280, 704)
    py = os.path.expanduser(registry["compose_i2v"]["python"])
    jf = d / "job.json"
    jf.write_text(json.dumps(j0, indent=2))
    cands, choice = [], None
    for rnd in range(rounds):
        seed = int(j0["seed"]) + 100 * rnd
        first = d / f"cand_r{rnd}.png"
        instr = ["--instruction", j0["start_pose_instruction"]]
        if j0.get("orientation") in ("foot_front", "foot_back"):
            # Image-edit models ignore "facing the staircase" but follow image-relative directions: compose both
            # directions (alternating candidates) and let the judge's orientation check pick the right one.
            instr = ["--instruction", j0["start_pose_instruction"] + " The person is turned toward the LEFT side of "
                     "the image, shown in side view.", "--alt_instruction", j0["start_pose_instruction"] +
                     " The person is turned toward the RIGHT side of the image, shown in side view."]
        with T.span("compose_candidates", "CHAIN", input={"round": rnd, "seed": seed, "n": n_cands}) as c:
            rc = _call([py, REPO / "standup_agent/backends/compose_i2v/compose.py", "--scene", j0["scene_frame"],
                        "--identity", *j0["identity_photos"], *instr, "--out", first, "--width", W, "--height", H,
                        "--seed", seed, "--extra_seeds", *[seed + k for k in range(1, n_cands)]], d / "log.txt", gpu)
            new = [str(x) for x in [first] + [d / f"cand_r{rnd}_s{seed + k}.png" for k in range(1, n_cands)]
                   if Path(x).exists()]
            T.set_output(c, {"rc": rc, "candidates": new})
            if not new:
                T.fail(c, f"compose produced no candidates (exit {rc})")
        cands += new
        if not cands:
            continue
        if _call([sys.executable, "-m", "standup_agent.judge", "frames", "--job", jf, "--cands", *cands,
                  "--out", d / "choice.json"], d / "log.txt", gpu) == 0:
            choice = json.loads((d / "choice.json").read_text())
            if choice["best_passed"]:
                break
    if not cands:
        return None
    shutil.copy(choice["best"] if choice else cands[0], best)
    return str(best)


def generate_video(job, registry, gpu, retries):
    """Run one video backend from the start frame; judge the video; retry with a new seed if it fails."""
    with T.span("generate_video", "CHAIN", input={"job_id": job["job_id"], "exercise": job.get("exercise")},
                parent=T.extract(job.get("trace_context")), metadata={"route": job["route"], "gpu": gpu}) as s:
        r = _generate_video(job, registry, gpu, retries)
        T.set_output(s, {"passed": r["passed"], "video": r["video"], "attempts": len(r["attempts"]),
                         "scores": (r["check"] or {}).get("scores"), "issue": (r["check"] or {}).get("issue")})
        if not r["video"]:
            T.fail(s, "no attempt produced a video")
    return r


def _generate_video(job, registry, gpu, retries):
    out = Path(job["out_dir"])
    py = os.path.expanduser(registry[job["route"]]["python"])
    attempts = []
    for k in range(retries + 1):
        d = out / f"attempt{k}"
        d.mkdir(parents=True, exist_ok=True)
        jk = dict(job, out_dir=str(d), seed=int(job["seed"]) + 1000 * k)
        (d / "job.json").write_text(json.dumps(jk, indent=2))
        t0 = time.time()
        with T.span(f"backend.{job['route']}", "CHAIN", input={"attempt": k, "seed": jk["seed"]}) as b:
            rc = _call([py, REPO / f"standup_agent/backends/{job['route']}/run.py", "--job", d / "job.json"],
                       d / "log.txt", gpu)
            T.set_output(b, {"rc": rc, "seconds": round(time.time() - t0, 1)})
            if rc != 0:
                T.fail(b, (d / "error.txt").read_text()[-2000:] if (d / "error.txt").exists() else f"exit {rc}")
        att = dict(attempt=k, rc=rc, seconds=round(time.time() - t0, 1), video=None, check=None)
        if rc == 0 and (d / "video.mp4").exists():
            att["video"] = str(d / "video.mp4")
            if _call([sys.executable, "-m", "standup_agent.judge", "video", "--job", d / "job.json",
                      "--video", d / "video.mp4", "--out", d / "check.json"], d / "log.txt", gpu) == 0:
                att["check"] = json.loads((d / "check.json").read_text())
        attempts.append(att)
        if att["check"] and att["check"]["passed"]:
            break
    ok = [a for a in attempts if a["video"]]
    # Passed attempts first (a tie on VLM scores once picked a failed attempt over the passing one), then scores.
    best = max(ok, key=lambda a: (bool(a["check"] and a["check"]["passed"]),
                                  sum(v or 0 for v in (a["check"] or {}).get("scores", {}).values())), default=None)
    if best:
        shutil.copy(best["video"], out / "video.mp4")
    return dict(attempts=attempts, video=str(out / "video.mp4") if best else None,
                passed=bool(best and best["check"] and best["check"]["passed"]),
                check=best["check"] if best else None)


def run_jobs(jobs, registry, gpus, n_cands=4, retries=1):
    """Per suggestion (one GPU): best-of-N start frame, then every video route from it (judged, with retry)."""
    by_sugg = {}
    for j in jobs:
        by_sugg.setdefault(str(Path(j["out_dir"]).parent), []).append(j)
    q = queue.Queue()
    for sj in by_sugg.values():
        q.put(sj)
    results = {}

    def worker(gpu):
        while True:
            try:
                sj = q.get_nowait()
            except queue.Empty:
                return
            t0 = time.time()
            start = compose_start_frame(sj, registry, gpu, n_cands)
            print(f"[gpu {gpu}] {Path(sj[0]['out_dir']).parent.name}: start frame "
                  f"{'ok' if start else 'FAILED (using scene frame)'} ({time.time() - t0:.0f}s)", flush=True)
            for job in sj:
                job = dict(job, first_frame=start)
                r = generate_video(job, registry, gpu, retries)
                r["out_root"] = str(Path(job["out_dir"]).parents[1])
                r["start_frame"] = start
                results[job["job_id"]] = r
                c = r["check"] or {}
                print(f"[gpu {gpu}] {job['job_id']}: {'PASS' if r['passed'] else 'fail'} "
                      f"scores={c.get('scores')} issue={c.get('issue', '')!r} attempts={len(r['attempts'])}",
                      flush=True)

    threads = [threading.Thread(target=worker, args=(g,)) for g in gpus]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="a scene video, or a folder of them (batch)")
    ap.add_argument("--photos", default="", help="dir or comma list of the user's photos (optional for --plan_only)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--routes", default="compose_i2v",
                    help="comma list of video backends (see backends.json); all start from the judged start frame")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--frames", type=int, default=16, help="frames sampled for the VLM")
    ap.add_argument("--vlm", default="bedrock",
                    help="'bedrock' / 'bedrock:<model id>' (Claude on Amazon Bedrock) or a local Qwen3-VL checkpoint path")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--plan_only", action="store_true", help="only reason + write plans (runs in-process)")
    ap.add_argument("--skip_plan", action="store_true", help="reuse existing plan.json files; only generate")
    ap.add_argument("--candidates", type=int, default=4, help="composed start-frame candidates per suggestion")
    ap.add_argument("--only_failed", action="store_true",
                    help="with --skip_plan: only regenerate suggestions without a passing rejudge.json")
    ap.add_argument("--retries", type=int, default=1, help="regenerations when the video judge fails a clip")
    a = ap.parse_args()

    out_root = Path(a.out).resolve()
    # Groups this run's traces in Arize; the planning child process inherits it.
    os.environ.setdefault(T.SESSION_ENV, f"{out_root.name}-{time.strftime('%Y%m%d-%H%M%S')}")
    p = Path(a.photos)
    photos = [] if not a.photos else \
        sorted(str(x.resolve()) for x in p.iterdir() if x.suffix.lower() in (".jpg", ".jpeg", ".png")) \
        if p.is_dir() else [str(Path(x).resolve()) for x in a.photos.split(",")]
    catalog = R.load_catalog()
    routes = [r for r in a.routes.split(",") if r]
    v = Path(a.video)
    videos = sorted(x for x in v.iterdir() if x.suffix.lower() in (".mp4", ".mov", ".m4v")) if v.is_dir() else [v]

    plan_dirs = [out_root / x.stem if v.is_dir() else out_root for x in videos]
    if a.plan_only:
        # ---- reasoning for every video (VLM loaded once)
        vlm = R.make_vlm(a.vlm, device=f"cuda:{a.gpus.split(',')[0]}")
        identity = R.describe_identity(vlm, photos) if photos else {"appearance": "a person", "pronoun": "they"}
        for video, out in zip(videos, plan_dirs):
            try:
                plan_video(vlm, video, photos, identity, out, catalog, a.n, a.frames, routes, a.seed)
            except Exception as e:  # one bad video must not stop a batch
                out.mkdir(parents=True, exist_ok=True)
                (out / "error.txt").write_text(f"{type(e).__name__}: {e}")
                print(f"[plan] {video.name}: FAILED {e}", flush=True)
        return

    # ---- plan in a child process: when it exits, all VLM / detector GPU memory is released for the generators
    if not a.skip_plan:
        cmd = [sys.executable, "-m", "standup_agent.run", "--video", a.video, "--photos", a.photos, "--out", a.out,
               "--routes", a.routes, "--n", str(a.n), "--frames", str(a.frames), "--vlm", a.vlm, "--gpus", a.gpus,
               "--seed", str(a.seed), "--plan_only"]
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "")} \
            if "CUDA_VISIBLE_DEVICES" in os.environ else dict(os.environ)
        if subprocess.call(cmd, cwd=str(REPO), env=env) != 0:
            raise SystemExit("planning failed")
    if not routes:
        return

    # ---- generation, from the plans on disk
    registry = json.loads(REGISTRY.read_text())
    missing = [r for r in routes if r not in registry]
    if missing:
        raise SystemExit(f"backends not registered in {REGISTRY}: {missing}")
    jobs = [j for out in plan_dirs if (out / "plan.json").exists()
            for j in jobs_from_plan(out, routes, photos, a.seed, a.only_failed)]
    results = run_jobs(jobs, registry, [g for g in a.gpus.split(",") if g], a.candidates, a.retries)
    for out in {Path(j["out_dir"]).parents[1] for j in jobs}:
        res = {k: r for k, r in results.items() if r.get("out_root") == str(out)}
        (out / "results.json").write_text(json.dumps(res, indent=2))
        d = json.loads((out / "plan.json").read_text())
        write_plan_md(out, d["scene"], d["identity"], d["suggestions"], res)


def jobs_from_plan(out, routes, photos, seed, only_failed=False):
    """Generation jobs (see BACKENDS.md) for every suggestion in out/plan.json and every route."""
    from PIL import Image
    d = json.loads((out / "plan.json").read_text())
    catalog = {x["id"]: x for x in R.load_catalog()["exercises"]}
    jobs = []
    for e in d["suggestions"]:
        # Exercise rules always come from the CURRENT catalog (plans saved earlier may predate new checks).
        ex = catalog.get(e["exercise_id"], {})
        fmt = lambda qs: [q.format(object=e["object"]) for q in qs]
        e = dict(e, rom=ex.get("rom", e.get("rom", {})), unilateral=ex.get("unilateral", e.get("unilateral", False)),
                 start_angles=ex.get("start_angles", e.get("start_angles", {})),
                 pose_checks=fmt(ex["pose_checks"]) if "pose_checks" in ex else e.get("pose_checks", []),
                 motion_checks=fmt(ex["motion_checks"]) if "motion_checks" in ex else e.get("motion_checks", []),
                 orientation=ex.get("orientation", e.get("orientation")), min_reps=ex.get("min_reps", 2))
        if only_failed:
            rj = Path(e["scene_frame"]).parent
            if any(json.loads(f.read_text()).get("passed") for f in rj.glob("*/rejudge.json")):
                continue
        frame = Path(e["scene_frame"])
        W, H = Image.open(frame).size
        for r in routes:
            jobs.append(dict(job_id=f"{out.name}/sugg{e['rank']}_{r}", route=r, scene_frame=str(frame),
                             exercise=e["exercise"], object=e["object"], motion=e.get("motion", ""),
                             constraints=e.get("constraints", ""), pose_checks=e.get("pose_checks", []),
                             orientation=e.get("orientation"), motion_checks=e.get("motion_checks", []),
                             rom=e.get("rom", {}), unilateral=e.get("unilateral", False),
                             start_angles=e.get("start_angles", {}), min_reps=e.get("min_reps", 2),
                             identity_photos=photos, first_frame=None,
                             start_pose_instruction=e["start_pose_instruction"], prompt=e["prompt"],
                             negative_prompt=e["negative_prompt"], num_frames=e["num_frames"], fps=e["fps"],
                             width=W, height=H, seed=seed, out_dir=str(frame.parent / r),
                             trace_context=d.get("trace_context")))
    return jobs


def plan_video(vlm, video, photos, identity, out, catalog, n, n_frames, routes, seed):
    """Reason about one scene video; write plan.json / plan.md and scene frames.
    A re-plan replaces the previous plan, so stale suggestion folders are removed first."""
    # A caller in another process (e.g. the Daytona orchestrator) can pass its span as STANDUP_TRACEPARENT.
    parent = T.extract({"traceparent": os.environ["STANDUP_TRACEPARENT"]}) if os.environ.get("STANDUP_TRACEPARENT") else None
    with T.span("plan_video", "AGENT", input={"video": str(video), "identity": identity}, parent=parent,
                metadata={"video": Path(video).name, "n": n, "frames": n_frames, "routes": routes,
                          "vlm": getattr(vlm, "name", None)}) as s:
        plan = _plan_video(vlm, video, identity, out, catalog, n, n_frames, routes, T.inject())
        T.set_output(s, [{k: e[k] for k in ("exercise_id", "object", "why", "safety", "frame_index",
                                             "people_in_frame", "object_verified")} for e in plan])


def _plan_video(vlm, video, identity, out, catalog, n, n_frames, routes, trace_context):
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sugg*_*"):
        if old.is_dir():
            shutil.rmtree(old)
    frames, fps, total = R.read_frames(video, n_frames)
    scene = R.analyze_scene(vlm, frames, catalog)
    picked = R.plan(vlm, frames, scene, catalog, n)
    picked, rejected = picked if isinstance(picked, tuple) else (picked, [])
    scene["rejected_suggestions"] = rejected
    suggestions = [R.choose_frame(vlm, frames, s) for s in picked]
    plan = []
    for k, s in enumerate(suggestions, 1):
        idx, im = R.best_frame(video, s, [i for i, _ in frames], vlm=vlm)
        d = out / f"sugg{k}_{s.exercise['id']}"
        d.mkdir(parents=True, exist_ok=True)
        frame_path = d / "scene_frame.jpg"
        im.save(frame_path, quality=95)
        pr = build_prompts(s, identity, scene["scene"])
        entry = dict(rank=k, exercise_id=s.exercise["id"], exercise=s.exercise["name"],
                     exrx_category=s.exercise["exrx_category"], exrx_list=s.exercise["exrx_list"],
                     object=s.object["name"], object_description=s.object.get("description", ""),
                     why=s.why, safety=s.safety, side=s.side, frame_index=idx, frame_time_s=round(idx / fps, 2),
                     people_in_frame=s.people_visible, object_verified=s.object_verified,
                     scene_frame=str(frame_path), **pr)
        plan.append(entry)
    (out / "plan.json").write_text(json.dumps(dict(video=str(Path(video).resolve()), identity=identity, scene=scene,
                                                   suggestions=plan, routes=routes, trace_context=trace_context),
                                              indent=2))
    write_plan_md(out, scene, identity, plan)
    print(f"[plan] {Path(video).name}: " + "; ".join(f"{e['exercise_id']} @ {e['object']}" for e in plan), flush=True)
    return plan


def write_plan_md(out, scene, identity, plan, results=None):
    L = [f"# Workout plan\n", f"**Scene:** {scene['scene']}  ", f"**User:** {identity['appearance']}\n",
         "## Objects found\n", "| Object | Affords | Concerns |", "|---|---|---|"]
    L += [f"| {o['name']} | {', '.join(o['affordances'])} | {o.get('concerns') or '-'} |" for o in scene["objects"]]
    L.append("\n## Suggestions\n")
    for e in plan:
        L += [f"### {e['rank']}. {e['exercise']} on the {e['object']}",
              f"- Why: {e['why']}", f"- Safety: {e['safety']}",
              f"- ExRx: {e['exrx_category']}, see {e['exrx_list']}",
              f"- Frame: {e['frame_index']} (t = {e['frame_time_s']} s), `{Path(e['scene_frame']).relative_to(out)}`"
              + (" **WARNING: people visible in this frame; film the empty scene for best results**"
                 if e.get("people_in_frame") else "")
              + ("" if e.get("object_verified", True) else " **WARNING: the object could not be confirmed in any frame**"),
              f"- Prompt: {e['prompt']}"]
        if results:
            for jid, r in results.items():
                if jid.split("/")[-1].startswith(f"sugg{e['rank']}_"):
                    v = Path(r["video"]).relative_to(out) if r["video"] else "FAILED (see attempt*/log.txt)"
                    c = r.get("check") or {}
                    verdict = "passed" if r.get("passed") else f"did not pass ({c.get('issue', 'no check')})"
                    L.append(f"- Video ({jid.split('/')[-1].split('_', 1)[1]}): `{v}`, judge {verdict}, "
                             f"scores {c.get('scores')}, {len(r.get('attempts', []))} attempt(s)")
        L.append("")
    (out / "plan.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
