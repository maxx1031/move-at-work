"""SurrealDB access for the web app, against the online (SurrealDB Cloud) ExRx database.

The scraped ExRx graph (exercise, equipment, muscle, joint, uses, works, moves, variation_of) is only read. The app
adds its own tables next to it:

    affordance ─suits→ exercise          which ExRx exercises a kind of spot supports (found by full-text search
                                          over exercise.search_text, limited to bodyweight / stretch exercises)
    upload ─contains→ scene_object ─affords→ affordance
    upload ─recommends→ exercise          Claude's 3 picks, pointing at the real ExRx records

Settings: SURREAL_URL, SURREAL_USER, SURREAL_PASS, SURREAL_NS, SURREAL_DB (the ExRx database).
"""
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from surrealdb import RecordID, Surreal

CATALOG = Path(__file__).resolve().parents[1] / "standup_agent" / "catalog.json"  # affordance definitions
# Short phrases: SurrealDB's @@ full-text match requires every term, so each phrase is searched on its own.
SEARCH = {
    "seat": ["bench dip", "chair", "seated", "step-up", "box squat", "sit"],
    "table_edge": ["incline push-up", "table", "desk", "counter"],
    "wall": ["wall"],
    "rail": ["rail", "heel elevated", "leg swing", "assisted squat"],
    "step": ["step", "calf raise"],
    "floor_open": ["lunge", "squat", "march", "standing", "jumping jack"],
}
BODYWEIGHT = ["Body Weight", "Bodyweight", "Stretch", "Self-assisted", "Isometric"]
PER_AFFORDANCE = 12
APP_TABLES = ["affordance", "upload", "scene_object"]
APP_EDGES = ["suits", "contains", "affords", "recommends"]


@contextmanager
def connect():
    """Signs in as a root user, or, if that is refused, as a namespace or database user."""
    env = os.environ
    ns, name = env.get("SURREAL_NS", "exrx"), env.get("SURREAL_DB", "exrx")
    user = {"username": env.get("SURREAL_USER", "root"), "password": env.get("SURREAL_PASS", "root")}
    with Surreal(env.get("SURREAL_URL", "ws://127.0.0.1:8000/rpc")) as db:
        for scope in ({}, {"namespace": ns}, {"namespace": ns, "database": name}):
            try:
                db.signin({**user, **scope})
                break
            except Exception as e:
                err = e
        else:
            raise err
        db.use(ns, name)
        yield db


def _one(result):
    """select() of one record id returns a dict or a one-element list depending on the SDK version."""
    return (result[0] if result else None) if isinstance(result, list) else result


def _plain(v):
    """RecordIDs -> their key, recursively (for JSON responses)."""
    if isinstance(v, RecordID):
        return v.id
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def seed():
    """Create the app's tables and (re)build the affordance -suits-> exercise edges from full-text search.
    Only app tables are written; the ExRx tables are read."""
    affs = json.loads(CATALOG.read_text())["affordances"]
    with connect() as db:
        for t in APP_TABLES:
            db.query(f"DEFINE TABLE IF NOT EXISTS {t} SCHEMALESS;")
        for e in APP_EDGES:
            db.query(f"DEFINE TABLE IF NOT EXISTS {e} TYPE RELATION SCHEMALESS;")
        db.query("DELETE suits;")
        for name, desc in affs.items():
            a = RecordID("affordance", name)
            db.upsert(a, {"name": name, "description": desc, "search": SEARCH.get(name, [])})
            hits = {}
            for phrase in SEARCH.get(name, []):
                rows = db.query(
                    "SELECT id, search::score(1) AS score FROM exercise WHERE search_text @1@ $q "
                    "AND ->uses->equipment.name ANYINSIDE $bw AND section != 'Plyometrics' "
                    "ORDER BY score DESC LIMIT $n;", {"q": phrase, "bw": BODYWEIGHT, "n": PER_AFFORDANCE})
                for r in rows:
                    key = r["id"].id
                    if key not in hits or r["score"] > hits[key][0]:
                        hits[key] = (r["score"], phrase)
            for key, (score, phrase) in sorted(hits.items(), key=lambda kv: -kv[1][0])[:PER_AFFORDANCE]:
                db.query("RELATE $a->suits->$e SET score = $s, phrase = $p;",
                         {"a": a, "e": RecordID("exercise", key), "s": score, "p": phrase})


def export_catalog():
    """The candidate exercises, read from the graph (affordance -suits-> exercise), in the planner's catalog.json
    form. An exercise that suits several kinds of spot is listed under its best-scoring one."""
    with connect() as db:
        affs = db.query("SELECT name, description FROM affordance ORDER BY name;")
        rows = db.query("SELECT score, in.name AS affordance, out.{id, name, section, muscle_groups, target_muscles, "
                        "instructions, comments, classification, source_url} AS ex FROM suits ORDER BY score DESC;")
    exercises, seen = [], set()
    for r in rows:
        e = r["ex"]
        key = e["id"].id
        if key in seen:
            continue
        seen.add(key)
        ins = e.get("instructions") or {}
        cls = e.get("classification") or {}
        groups = e.get("muscle_groups") or []
        exercises.append({
            "id": key, "name": e["name"], "affordance": r["affordance"],
            "exrx_category": f"{groups[0] if groups else e.get('section', '')}",
            "exrx_list": e.get("source_url", ""),
            "level": ", ".join(f"{k}: {v}" for k, v in cls.items()) or "bodyweight",
            "target": ", ".join(e.get("target_muscles") or groups) or e.get("section", ""),
            # Prompt fields; braces escaped because the planner .format()s them with the object name.
            "start_pose": "uses the {object}. " + _esc(ins.get("preparation", "")),
            "motion": _esc(ins.get("execution", "")),
            "safety": _esc((e.get("comments") or "Move slowly and stop if anything hurts.")[:240]),
        })
    return {"about": "Candidates from the ExRx graph in SurrealDB (affordance -suits-> exercise).",
            "affordances": {a["name"]: a["description"] for a in affs}, "exercises": exercises}


def _esc(text):
    return re.sub(r"\s+", " ", str(text)).replace("{", "{{").replace("}", "}}").strip()


def create_upload(upload_id, video_name):
    with connect() as db:
        db.query("CREATE $u CONTENT {video: $v, status: 'queued', stage: 'uploaded', created_at: time::now()};",
                 {"u": RecordID("upload", upload_id), "v": video_name})


def set_status(upload_id, status, stage, **fields):
    with connect() as db:
        db.query("UPDATE $u MERGE $f;", {"u": RecordID("upload", upload_id),
                                         "f": {"status": status, "stage": stage, **fields}})


def save_plan(upload_id, plan):
    """Write the scene's objects and the recommendations as graph edges."""
    u = RecordID("upload", upload_id)
    scene = plan["scene"]
    with connect() as db:
        db.query("UPDATE $u MERGE $f;", {"u": u, "f": {"scene": scene.get("scene"), "setting": scene.get("setting"),
                                                        "guardrail_overrides": scene.get("guardrail_overrides", [])}})
        for k, o in enumerate(scene.get("objects", [])):
            obj = RecordID("scene_object", f"{upload_id}_{k}")
            db.upsert(obj, {"name": o["name"], "description": o.get("description", ""),
                            "concerns": o.get("concerns", ""), "upload": u})
            db.query("RELATE $u->contains->$o;", {"u": u, "o": obj})
            for a in o.get("affordances", []):
                db.query("RELATE $o->affords->$a;", {"o": obj, "a": RecordID("affordance", a)})
        for s in plan["suggestions"]:
            db.query("RELATE $u->recommends->$e CONTENT $c;", {
                "u": u, "e": RecordID("exercise", s["exercise_id"]),
                "c": {"rank": s["rank"], "object": s["object"], "why": s["why"], "safety": s["safety"],
                      "side": s.get("side"), "frame_index": s.get("frame_index"),
                      "frame": f"/api/uploads/{upload_id}/frame/{s['rank']}.jpg"}})


def video_embed(video):
    """An iframe URL that plays outside exrx.net, or None. Only YouTube qualifies: every sampled ExRx Vimeo
    video is domain-restricted to exrx.net (player answers 403 elsewhere)."""
    if not video or video.get("provider") != "youtube":
        return None
    m = re.search(r"(?:v=|/embed/)([\w-]{11})", video.get("url", "") + " " + video.get("embed_url", ""))
    return f"https://www.youtube-nocookie.com/embed/{m.group(1)}?rel=0&modestbranding=1" if m else None


def recommendations(upload_id):
    """The upload's 3 recommendations joined with their ExRx records (name, instructions, media, source page), plus
    every other exercise the scene's objects could support: contains -> scene_object -affords-> affordance -suits->."""
    u = RecordID("upload", upload_id)
    with connect() as db:
        recs = db.query("SELECT rank, object, why, safety, side, frame, out.{id, name, section, muscle_groups, "
                        "target_muscles, instructions, media, source_url} AS exercise FROM recommends "
                        "WHERE in = $u ORDER BY rank;", {"u": u})
        options = db.query("SELECT VALUE array::distinct(array::flatten(->contains->scene_object->affords->affordance"
                           "->suits->exercise.name)) FROM ONLY $u;", {"u": u})
        upload = _one(db.select(u))
    recs = _plain(recs)
    for r in recs:
        ex = r["exercise"]
        media = ex.pop("media", None) or []
        video = next((m for m in media if m.get("kind") == "video"), None)
        ex["video_embed"] = video_embed(video)
        # ExRx's Vimeo player and GIFs refuse to load on other sites (domain-restricted), so those
        # exercises link out to their ExRx page, where ExRx's own player plays them.
        ex["video_link"] = ex.get("source_url") if video and not ex["video_embed"] else None
    picked = {r["exercise"]["name"] for r in recs}
    return upload, recs, [o for o in (options or []) if o not in picked]
