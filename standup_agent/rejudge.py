"""Re-judge every generated clip of a run with the current judge and catalog thresholds; print a summary table.

python -m standup_agent.rejudge --root outputs/agent/e2e [--route compose_i2v]

Use after the judge or the catalog's range-of-motion / key-moment specs changed while a run was in progress.
Writes <route>/rejudge.json next to each clip and <root>/REJUDGE.md.
"""
import argparse
import json
from pathlib import Path

from . import judge as Jg
from . import reasoner as R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--route", default="compose_i2v")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    catalog = {e["id"]: e for e in R.load_catalog()["exercises"]}
    vlm = R.VLM()
    rows = []
    for plan_path in sorted(root.glob("*/plan.json")):
        plan = json.loads(plan_path.read_text())
        for e in plan["suggestions"]:
            d = Path(e["scene_frame"]).parent / a.route
            attempts = sorted(d.glob("attempt*/video.mp4"))
            if not attempts:
                rows.append((plan_path.parent.name, e["exercise_id"], None, "no video"))
                continue
            ex, obj = catalog[e["exercise_id"]], e["object"]
            best = None
            for v in attempts:
                job = json.loads((v.parent / "job.json").read_text())
                job["rom"] = ex.get("rom", {})
                job["unilateral"] = ex.get("unilateral", False)
                job["min_reps"] = ex.get("min_reps", 2)
                job["motion_checks"] = [q.format(object=obj) for q in ex.get("motion_checks", [])]
                r = Jg.judge_video(vlm, job, str(v))
                r["video"] = str(v)
                if best is None or (r["passed"], sum(x or 0 for x in r["scores"].values())) > \
                        (best["passed"], sum(x or 0 for x in best["scores"].values())):
                    best = r
            (d / "rejudge.json").write_text(json.dumps(best, indent=2))
            rows.append((plan_path.parent.name, e["exercise_id"], best, ""))
            print(f"{plan_path.parent.name}/{e['exercise_id']}: {'PASS' if best['passed'] else 'fail'} "
                  f"{best['range_of_motion']} {best['issue'][:80]}", flush=True)
    L = ["# Re-judged results\n", "| Scene | Exercise | Verdict | Range of motion | Issue | Best clip |",
         "|---|---|---|---|---|---|"]
    for scene, ex, r, note in rows:
        if r is None:
            L.append(f"| {scene} | {ex} | no video | | {note} | |")
        else:
            L.append(f"| {scene} | {ex} | {'PASS' if r['passed'] else 'fail'} | {r.get('range_of_motion')} | "
                     f"{r['issue'][:90]} | `{Path(r['video']).relative_to(root)}` |")
    n_pass = sum(1 for _, _, r, _ in rows if r and r["passed"])
    L.append(f"\n**{n_pass} of {len(rows)} suggestions have a passing clip.**")
    (root / "REJUDGE.md").write_text("\n".join(L))
    print(f"{n_pass}/{len(rows)} pass; table in {root / 'REJUDGE.md'}")


if __name__ == "__main__":
    main()
