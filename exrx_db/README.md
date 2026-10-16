# exrx_db — personal ExRx exercise graph in SurrealDB

Private, personal-use copy of the ExRx.net exercise library (use approved by ExRx for personal use),
built from Internet Archive snapshots. Page content stays in `data/exrx/` (gitignored) and in a local
SurrealDB bound to `127.0.0.1`; do not publish or redistribute it.

## Pipeline

```bash
conda activate exrx                     # python 3.12: surrealdb, sentence-transformers, bs4, mcp
python -I exrx_db/download.py           # Wayback snapshots -> data/exrx/pages (resumable, ~2.5 s/page)
python -I exrx_db/parse.py              # -> data/exrx/parsed/{exercises,muscles,listings}.jsonl
exrx_db/serve.sh &                      # SurrealDB 3.x, rocksdb:data/exrx/surreal.db on 127.0.0.1:8000
python -I exrx_db/load.py               # schema + graph + bge-small embeddings (idempotent)
```

`download.py` expects `data/exrx/cdx_all.json`, the Wayback CDX listing for `exrx.net` HTML pages:

```bash
curl -sS "https://web.archive.org/cdx/search/cdx?url=exrx.net/&matchType=domain&output=json&fl=original,timestamp,mimetype&filter=statuscode:200&filter=mimetype:text/html&collapse=urlkey" -o data/exrx/cdx_all.json
```

Credentials (`.env`): `EXRX_DB_USER` / `EXRX_DB_PASS` (root, used by `serve.sh` and `load.py`) and
`EXRX_READER_PASS` (database-level `VIEWER` user `reader`, used by all query tools).

## Graph

| Node | Key example | Notes |
|---|---|---|
| `exercise` | `exercise:weightexercises_latissimusdorsi_cbfrontpulldown` | name, section, utility/mechanics/force, equipment[], body_regions[], instructions{}, media[] (GIF, Vimeo), embedding |
| `muscle` | `muscle:latissimusdorsi` | name, aliases, heads, movement, attachments |
| `joint` | `joint:hip` | |
| `equipment` | `equipment:cable` | |

Edges: `exercise->works{role,label}->muscle` (role: target, synergists, stabilizers, …),
`exercise->moves{kind,action}->joint`, `exercise->uses->equipment`,
`exercise->variation_of->exercise`.

Indexes: BM25 full-text on `name` and `search_text`, HNSW (384-d cosine) on `embedding`.

## Querying from VLMs

Seven read-only tools: `search_exercises` (hybrid BM25 + vector with section/equipment/muscle/role
filters), `get_exercise`, `exercises_for_muscle`, `exercises_for_joint_action`, `similar_exercises`,
`list_values`, `run_surrealql`.

**Function calling (Qwen-VL, GPT-4o, etc.):**

```python
from exrx_db.query import ExRx, TOOL_SPECS
ex = ExRx()
# pass TOOL_SPECS as `tools=`; for each tool call the model makes:
result_json = ex.call(call.function.name, call.function.arguments)
```

**MCP (Claude Code / Desktop, any MCP client):**

```bash
claude mcp add exrx -- conda run -n exrx python -I exrx_db/mcp_server.py
python exrx_db/mcp_server.py --http 127.0.0.1:8765   # streamable HTTP for other agents
```

**Raw SurrealQL examples:**

```surql
-- cable exercises whose target is the lats
SELECT name FROM exercise
WHERE equipment CONTAINS 'Cable' AND ->works[WHERE role = 'target']->muscle.name CONTAINS 'Latissimus Dorsi';

-- everything that dynamically extends the hip
SELECT name FROM exercise WHERE ->moves[WHERE kind = 'dynamic' AND action = 'Extension']->(joint WHERE name = 'Hip');

-- full-text
SELECT name, search::score(1) AS s FROM exercise WHERE search_text @1@ 'kettlebell swing' ORDER BY s DESC LIMIT 5;

-- exercise card
RETURN fn::card(exercise:plyometrics_boxjump);
```
