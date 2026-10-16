"""Load parsed ExRx JSONL (data/exrx/parsed) into SurrealDB as a graph, with embeddings.

Requires a running server (see exrx_db/serve.sh). Idempotent: re-running upserts everything.
"""
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from surrealdb import RecordID, Surreal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exrx_db.query import env  # noqa: E402

HERE = Path(__file__).resolve().parent
PARSED = HERE.parent / "data" / "exrx" / "parsed"
URL = env("EXRX_DB_URL", "ws://127.0.0.1:8000/rpc")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-d, matches the HNSW index in schema.surql

REGIONS = {
    "NeckWt": "Neck", "ShouldWt": "Shoulders", "ArmWt": "Upper Arms", "ForeArmWt": "Forearms",
    "BackWt": "Back", "ChestWt": "Chest", "WaistWt": "Waist", "HipsWt": "Hips",
    "ThighWt": "Thighs", "CalfWt": "Calves",
}


def ensure_secret(name):
    """Return a secret from the environment/.env, generating and saving one to .env if unset."""
    value = env(name, None)
    if not value:
        import secrets
        value = secrets.token_urlsafe(32)
        dotenv = HERE.parent / ".env"
        with dotenv.open("a") as f:
            f.write(f"\n{name}={value}\n")
        os.environ[name] = value
        print(f"generated {name} -> .env", flush=True)
    return value


def key(path):
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def short_key(path, prefix):
    """/Muscles/BicepsBrachii -> bicepsbrachii (record ids stay readable)."""
    return key(path.split(prefix, 1)[1]) if prefix in path else key(path)


def read(name):
    return [json.loads(l) for l in (PARSED / f"{name}.jsonl").open()]


def embed_text(ex, targets, others, equipment):
    ins = ex.get("instructions") or {}
    parts = [
        ex["name"], ex["section"],
        " ".join(f"{k}: {v}" for k, v in (ex.get("classification") or {}).items()),
        "targets " + ", ".join(targets) if targets else "",
        "also works " + ", ".join(others) if others else "",
        "equipment " + ", ".join(equipment) if equipment else "",
        " ".join(f"{a['kind'] or ''} {a['joint']} {a['action']}" for a in ex.get("articulations", [])),
        ins.get("preparation", ""), ins.get("execution", ""),
    ]
    return ". ".join(p for p in parts if p)[:2000]


def main():
    exercises, muscles, listings = read("exercises"), read("muscles"), read("listings")
    ex_by_path = {e["path"]: e for e in exercises}

    # --- placements from list pages: equipment / region / group / parent variation
    place = defaultdict(lambda: {"equipment": set(), "regions": set(), "groups": set(), "parents": set()})
    for r in listings:
        p = place[r["exercise_path"]]
        list_name = r["list_path"].rsplit("/", 1)[-1]
        if "/ExList/" in r["list_path"]:
            if r["equipment"]:
                p["equipment"].add(r["equipment"])
            p["regions"].add(REGIONS.get(list_name, list_name))
        elif r["equipment"]:
            p["groups"].add(r["equipment"])  # e.g. plyometric intensity / power-exercise category
        if r["group"]:
            p["groups"].add(r["group"])
        if r["parent_path"] and r["parent_path"] != r["exercise_path"]:
            p["parents"].add(r["parent_path"])

    # --- muscles: real pages + stubs for muscles referenced only from exercises
    muscle_rows = {}
    for m in muscles:
        aliases = [a.strip() for a in re.split(r"[;,]", m.get("other_names", "")) if a.strip()]
        muscle_rows[m["path"]] = {
            "id": RecordID("muscle", short_key(m["path"], "/Muscles/")),
            "name": m["name"], "aliases": aliases[:20], "source_url": m["source_url"],
            **{k: m[k] for k in ("heads", "movement", "attachments", "comments") if k in m},
        }
    for e in exercises:
        for mu in e["muscles"]:
            path = mu["muscle_path"] or "/Muscles/" + re.sub(r"\W+", "", mu["name"])
            if path not in muscle_rows:
                muscle_rows[path] = {"id": RecordID("muscle", short_key(path, "/Muscles/")),
                                     "name": mu["name"], "aliases": [], "stub": True}

    joints, equipment = {}, {}
    ex_rows, works, moves, uses, variations = [], [], [], [], []
    texts = []
    for e in exercises:
        eid = RecordID("exercise", key(e["path"]))
        p = place[e["path"]]
        targets = [m["name"] for m in e["muscles"] if m["role"] == "target"]
        others = [m["name"] for m in e["muscles"] if m["role"] != "target"]
        cls = e.get("classification") or {}
        eq = sorted(p["equipment"])
        ex_rows.append({
            "id": eid, "name": e["name"], "section": e["section"], "folder": e["folder"],
            "utility": cls.get("utility"), "mechanics": cls.get("mechanics"), "force": cls.get("force"),
            "classification": cls, "equipment": eq,
            "body_regions": sorted(p["regions"]), "muscle_groups": sorted(p["groups"]),
            "target_muscles": targets, "instructions": e.get("instructions") or {},
            "comments": e.get("comments"), "media": e.get("media", []),
            "source_url": e["source_url"], "archive_timestamp": e["archive_timestamp"],
        })
        texts.append(embed_text(e, targets, others, eq))
        ex_rows[-1]["search_text"] = texts[-1]
        for mu in e["muscles"]:
            path = mu["muscle_path"] or "/Muscles/" + re.sub(r"\W+", "", mu["name"])
            works.append({"in": eid, "out": muscle_rows[path]["id"], "role": mu["role"],
                          "label": mu["name"]})
        for a in e.get("articulations", []):
            jk = short_key(a["joint_path"], "/Articulations/")
            joints[jk] = {"id": RecordID("joint", jk), "name": a["joint_path"].rsplit("/", 1)[-1]}
            moves.append({"in": eid, "out": joints[jk]["id"], "kind": a["kind"], "action": a["action"]})
        for name in eq:
            qk = key(name)
            equipment[qk] = {"id": RecordID("equipment", qk), "name": name}
            uses.append({"in": eid, "out": equipment[qk]["id"]})
        for parent in p["parents"]:
            if parent in ex_by_path:
                variations.append({"in": eid, "out": RecordID("exercise", key(parent))})

    print(f"embedding {len(texts)} exercises with {EMBED_MODEL} ...", flush=True)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBED_MODEL)
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    for row, v in zip(ex_rows, vecs):
        row["embedding"] = [float(x) for x in v]

    with Surreal(URL) as db:
        db.signin({"username": env("EXRX_DB_USER", "root"), "password": env("EXRX_DB_PASS", "root")})
        db.use("exrx", "exrx")
        db.query((HERE / "schema.surql").read_text())
        # Read-only account used by query.py / mcp_server.py.
        # PASSWORD only accepts a literal; json.dumps gives a safely escaped double-quoted string.
        reader_pass = json.dumps(ensure_secret("EXRX_READER_PASS"))
        db.query(f"DEFINE USER OVERWRITE reader ON DATABASE PASSWORD {reader_pass} ROLES VIEWER;")
        # HTTP endpoints (api.surql) are gated on the x-api-key header matching $api_key.
        db.query("DEFINE PARAM OVERWRITE $api_key VALUE $k;", {"k": ensure_secret("EXRX_API_KEY")})
        db.query((HERE / "api.surql").read_text())
        db.query("DELETE works; DELETE moves; DELETE uses; DELETE variation_of;")

        def upsert(table, rows, size=200):
            for i in range(0, len(rows), size):
                db.query(f"FOR $r IN $rows {{ UPSERT $r.id CONTENT $r; }};", {"rows": rows[i:i + size]})
            print(f"{table}: {len(rows)}", flush=True)

        def relate(table, rows, size=500):
            for i in range(0, len(rows), size):
                db.query(f"INSERT RELATION INTO {table} $rows;", {"rows": rows[i:i + size]})
            print(f"{table}: {len(rows)}", flush=True)

        upsert("muscle", list(muscle_rows.values()))
        upsert("joint", list(joints.values()))
        upsert("equipment", list(equipment.values()))
        upsert("exercise", ex_rows, size=100)
        relate("works", works)
        relate("moves", moves)
        relate("uses", uses)
        relate("variation_of", variations)


if __name__ == "__main__":
    main()
