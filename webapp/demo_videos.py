"""Manage playable demo videos (YouTube) for the exercises the app can recommend.

ExRx's own videos are domain-locked to exrx.net, so the app embeds a YouTube video we pick per exercise instead.

    python -m webapp.demo_videos export   # write/refresh webapp/demo_videos.csv: one row per candidate exercise
    python -m webapp.demo_videos load     # store every filled-in youtube_url in the `demo_video` table
    python -m webapp.demo_videos status   # how many candidates have a playable video

CSV columns: exercise_id, name, affordance, exrx_page, youtube_url, start_s, note. Fill youtube_url (any YouTube
URL form or the bare 11-character id) and optionally start_s (seconds into the video). Rows you already filled
are kept on re-export. Clearing youtube_url and running `load` removes that exercise's video.

Uses SURREAL_URL / SURREAL_USER / SURREAL_PASS like the server; falls back to EXRX_DB_URL / EXRX_DB_USER /
EXRX_DB_PASS from .env.
"""
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = Path(__file__).with_name("demo_videos.csv")
FIELDS = ["exercise_id", "name", "affordance", "exrx_page", "youtube_url", "start_s", "note"]


def _env_fallback():
    sys.path.insert(0, str(ROOT))
    from exrx_db.query import env
    for surreal, exrx in (("SURREAL_URL", "EXRX_DB_URL"), ("SURREAL_USER", "EXRX_DB_USER"),
                          ("SURREAL_PASS", "EXRX_DB_PASS")):
        if not os.environ.get(surreal) and env(exrx, None):
            os.environ[surreal] = env(exrx, None)


def _read_csv():
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(newline="") as f:
        return {row["exercise_id"]: row for row in csv.DictReader(f)}


def export():
    from webapp import db
    existing = _read_csv()
    with db.connect() as conn:
        rows = conn.query("SELECT record::id(out) AS id, out.name AS name, in.name AS affordance, "
                          "out.source_url AS page, score FROM suits ORDER BY affordance, score DESC;")
        stored = {record_key(d["id"]): d for d in conn.query("SELECT id, youtube_id, start, note FROM demo_video;") or []}
    out, seen = [], set()
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        prev, demo = existing.get(r["id"], {}), stored.get(r["id"], {})
        out.append({
            "exercise_id": r["id"], "name": r["name"], "affordance": r["affordance"], "exrx_page": r["page"],
            "youtube_url": prev.get("youtube_url") or (f"https://youtu.be/{demo['youtube_id']}" if demo else ""),
            "start_s": prev.get("start_s") or (demo.get("start") or ""),
            "note": prev.get("note") or demo.get("note") or "",
        })
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    filled = sum(1 for r in out if r["youtube_url"])
    print(f"{CSV_PATH.relative_to(ROOT)}: {len(out)} candidate exercises, {filled} with a video")


def load():
    from webapp import db
    rows = _read_csv()
    if not rows:
        sys.exit(f"{CSV_PATH} not found or empty; run `export` first")
    stored = errors = removed = 0
    with db.connect() as conn:
        have = {record_key(d) for d in conn.query("SELECT VALUE id FROM demo_video;") or []}
    for key, row in rows.items():
        url = (row.get("youtube_url") or "").strip()
        if not url and key not in have:
            continue
        try:
            db.set_demo_video(key, url, row.get("start_s") or None, row.get("note", ""))
            if url:
                stored += 1
            else:
                removed += 1
        except ValueError as e:
            errors += 1
            print(f"skip {key}: {e}")
    print(f"stored {stored}, removed {removed}, invalid {errors}")


def status():
    from webapp import db
    with db.connect() as conn:
        cands = conn.query("RETURN array::distinct((SELECT VALUE out FROM suits));")
        n_c = len(cands or [])
        n_v = len(conn.query("SELECT VALUE id FROM demo_video WHERE exercise INSIDE $c;", {"c": cands}) or [])
    print(f"{n_v}/{n_c} candidate exercises have a playable demo video")


def record_key(rid):
    return rid.id if hasattr(rid, "id") else str(rid).split(":", 1)[-1]


if __name__ == "__main__":
    _env_fallback()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"export": export, "load": load, "status": status}.get(cmd, lambda: sys.exit(__doc__))()
