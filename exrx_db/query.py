"""Read-only query API over the ExRx SurrealDB graph, shaped as tools for (V)LMs.

Use directly from Python:
    from exrx_db.query import ExRx
    ex = ExRx()
    ex.search_exercises("hip hinge with dumbbells", muscle="gluteus")

or hand `TOOL_SPECS` (OpenAI/Qwen-style function schemas) to a function-calling VLM and route its
calls through `ExRx.call(name, arguments)`. `mcp_server.py` exposes the same tools over MCP.
"""
import json
import os
from functools import cached_property
from pathlib import Path

from surrealdb import RecordID, Surreal

_DOTENV = Path(__file__).resolve().parents[1] / ".env"


def env(name, default):
    """os.environ, then the repo .env; empty values count as unset."""
    value = os.environ.get(name)
    if not value and _DOTENV.exists():
        for line in _DOTENV.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == name:
                value = v.strip().strip("'\"")
    return value or default

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

class ExRx:
    def __init__(self, url=None, user=None, password=None):
        self.db = Surreal(url or env("EXRX_DB_URL", "ws://127.0.0.1:8000/rpc"))
        # Database-level VIEWER user created by load.py: tools can read, never write.
        self.db.signin({
            "namespace": "exrx", "database": "exrx",
            "username": user or "reader",
            "password": password or env("EXRX_READER_PASS", "reader"),
        })
        self.db.use("exrx", "exrx")

    @cached_property
    def _model(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBED_MODEL)

    def _embed(self, text):
        return [float(x) for x in self._model.encode(QUERY_PREFIX + text, normalize_embeddings=True)]

    def _q(self, sql, **params):
        return self.db.query(sql, params)

    # ---- tools (thin wrappers over the server-side fn::* in api.surql) -------------
    def search_exercises(self, query, section=None, equipment=None, muscle=None, role=None, limit=10):
        """Hybrid search (BM25 full-text + vector similarity, fused with reciprocal rank)."""
        return self._q("RETURN fn::search($q, $v, $section, $equipment, $muscle, $role, $limit)",
                       q=query, v=self._embed(query), section=section, equipment=equipment,
                       muscle=muscle, role=role, limit=int(limit))

    def get_exercise(self, exercise_id):
        """Full card: classification, instructions, muscles by role, joint actions, media, variations."""
        return self._q("RETURN fn::card($e)", e=RecordID("exercise", exercise_id))

    def exercises_for_muscle(self, muscle, role=None, limit=50):
        """Exercises working a muscle (substring match on muscle name), optionally by role."""
        return self._q("RETURN fn::muscle_exercises($m, $role, $limit)",
                       m=muscle, role=role, limit=int(limit))

    def exercises_for_joint_action(self, joint, action=None, kind=None, limit=50):
        """Exercises by articulation, e.g. joint='Hip', action='Extension', kind='dynamic'."""
        return self._q("RETURN fn::joint_exercises($j, $a, $k, $limit)",
                       j=joint, a=action, k=kind, limit=int(limit))

    def similar_exercises(self, exercise_id, limit=10):
        """Nearest neighbours by embedding (excluding the exercise itself)."""
        return self._q("RETURN fn::similar($e, $limit)",
                       e=RecordID("exercise", exercise_id), limit=int(limit))

    def list_values(self, field):
        """Valid filter values: sections | equipment | muscles | joints | roles | actions."""
        return self._q("RETURN fn::values($f)", f=field)

    def run_surrealql(self, query):
        """Arbitrary read-only SurrealQL (the connection's VIEWER role rejects writes)."""
        return self._q(query)

    def call(self, name, arguments):
        """Dispatch a tool call from a function-calling model; returns a JSON string."""
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        if name not in TOOL_NAMES:
            raise ValueError(f"unknown tool {name}")
        return json.dumps(getattr(self, name)(**arguments), ensure_ascii=False, default=str)


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(required)}}}


_S = {"type": "string"}
TOOL_SPECS = [
    _fn("search_exercises",
        "Find exercises by free-text description (movement, goal, posture seen in an image...). "
        "Optional filters narrow results. Returns ids to pass to get_exercise.",
        {"query": _S, "section": {**_S, "enum": ["WeightExercises", "Stretches", "Plyometrics", "Aerobic"]},
         "equipment": {**_S, "description": "e.g. Cable, Barbell, Dumbbell, Body Weight"},
         "muscle": {**_S, "description": "muscle name substring, e.g. 'gluteus', 'latissimus'"},
         "role": {**_S, "description": "target | synergists | stabilizers | dynamic_stabilizers | ..."},
         "limit": {"type": "integer", "default": 10}}, ["query"]),
    _fn("get_exercise", "Full details for one exercise id.", {"exercise_id": _S}, ["exercise_id"]),
    _fn("exercises_for_muscle", "Exercises that work a muscle, optionally only in a given role.",
        {"muscle": _S, "role": _S, "limit": {"type": "integer", "default": 50}}, ["muscle"]),
    _fn("exercises_for_joint_action", "Exercises by joint action, e.g. Hip Extension (dynamic).",
        {"joint": _S, "action": _S, "kind": {**_S, "enum": ["dynamic", "static"]},
         "limit": {"type": "integer", "default": 50}}, ["joint"]),
    _fn("similar_exercises", "Exercises most similar to a given exercise id.",
        {"exercise_id": _S, "limit": {"type": "integer", "default": 10}}, ["exercise_id"]),
    _fn("list_values", "List valid values for filters.",
        {"field": {**_S, "enum": ["sections", "equipment", "muscles", "joints", "roles", "actions"]}},
        ["field"]),
    _fn("run_surrealql",
        "Read-only SurrealQL. Tables: exercise, muscle, joint, equipment; edges "
        "exercise->works{role,label}->muscle, exercise->moves{kind,action}->joint, "
        "exercise->uses->equipment, exercise->variation_of->exercise.",
        {"query": _S}, ["query"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}
