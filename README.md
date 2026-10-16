# Just Standup (Move at Work)

Turn a familiar office into a personalized five-minute workout route. You record a short video of your workspace
on your phone, and the app returns three safe exercise spots: which object to use, which exercise to do there, the
best frame showing that spot, and the real ExRx.net instructions.

- Product and UI: [Technical design (中文)](docs/TECHNICAL_DESIGN.md), [approved UI — 01_UI.html](docs/reference/01_UI.html)
  (download it and open it in a browser), and the earlier SVG drafts in `outputs/`.
- Archived 3D / human–object-interaction experiments: [docs/HOI_EXPERIMENTS.md](docs/HOI_EXPERIMENTS.md)
  (results are in `outputs/archive_3d/`).

---

## 1. Sponsor integrations

| Sponsor | What it does here | Where |
|---|---|---|
| **SurrealDB** | Exercise knowledge graph + app database (uploads, scene objects, recommendations) | `exrx_db/`, `webapp/db.py` |
| **Daytona** | Hosts the web server; runs every upload's planning in its own throwaway, linked sandbox | `deploy/`, `webapp/server.py`, `standup_agent/daytona_runner.py`, `standup_agent/worker.py` |
| **Arize AX** | OpenTelemetry / OpenInference tracing of every LLM call, safety guardrail and sandbox step | `standup_agent/tracing.py` |
| **AWS** | Amazon Bedrock serves the vision-language planner (Claude); IAM credentials mint short-lived Bedrock tokens per sandbox | `standup_agent/reasoner.py` (`BedrockVLM`), `webapp/server.py` (`bedrock_token`) |

### SurrealDB: the exercise graph and the app's state

**ExRx graph (`exrx_db/`).** This is a personal-use copy of the ExRx.net library, rebuilt from Internet Archive
snapshots (see `exrx_db/README.md`; the content is not redistributed and lives in `data/`, which is gitignored).

- `schema.surql` defines the nodes `exercise`, `muscle`, `joint` and `equipment`, plus these relation edges:
  `exercise->works{role}->muscle`, `->moves{kind,action}->joint`, `->uses->equipment` and `->variation_of->exercise`.
- Indexes: BM25 full-text on `name` / `search_text`, plus an **HNSW vector index** (384-d cosine, `bge-small-en-v1.5`
  embeddings) on `exercise.embedding`.
- `api.surql` holds server-side SurrealQL functions (`fn::search` does hybrid BM25 + vector search with reciprocal-rank
  fusion; there are also `fn::similar`, `fn::muscle_exercises`, and others). It also exposes them as **`DEFINE API`
  HTTP endpoints** gated by an `x-api-key` middleware.
- `query.py` provides the same queries as LLM tool specs. `mcp_server.py` serves them over **MCP**, so Claude Code or
  any agent can query the graph (a read-only `reader` user that can never write).

**App tables (`webapp/db.py`).** These sit next to the ExRx graph in the same database:

```
affordance ─suits→ exercise           built by db.seed(): full-text search over exercise.search_text,
                                      limited to bodyweight/stretch exercises
upload ─contains→ scene_object ─affords→ affordance
upload ─recommends→ exercise          Claude's 3 picks, pointing at the real ExRx records
demo_video:<exercise id>              a playable YouTube demo we picked (ExRx's own videos only play on exrx.net)
```

- `seed()` only keeps bodyweight/stretch exercises that **have a demo video**. It also drops anything unsuitable for an
  office (`EXCLUDE`: inverted moves, jumps, gym-only equipment), and `seat` never maps to step-ups.
- Demo videos are curated in `webapp/demo_videos.csv`. Run `python -m webapp.demo_videos export` to list the
  candidate exercises, fill in `youtube_url` (and optionally `start_s`), then run `... load` to write them to
  `demo_video`; `... status` shows coverage. The app embeds our pick first, then a YouTube video ExRx itself used,
  and otherwise links out to the ExRx page.

- `export_catalog()` reads `affordance -suits-> exercise` and produces the candidate list sent to the planner, so the
  planner can only choose exercises that exist in the graph.
- `save_plan()` writes the result back as graph edges. `recommendations()` reads it back with one graph traversal,
  including "other options": `->contains->scene_object->affords->affordance->suits->exercise`.
- Locally, SurrealDB 3.x runs with RocksDB (`exrx_db/serve.sh`). When deployed, it runs either inside the server sandbox
  or on **SurrealDB Cloud** (set `SURREAL_URL=wss://...`).

### Daytona: hosting and per-upload isolation

`python deploy/deploy_daytona.py up` deploys the whole thing:

1. It builds the **`standup-planner` snapshot** from a declarative Daytona image (`deploy/planner_image.py`: Python
   3.12 + anthropic/arize/opencv). The image contains no planner code; its entrypoint (`deploy/planner_boot.py`)
   downloads the code bundle from the server at boot, so code changes need no image rebuild.
2. It creates or updates a long-running **server sandbox** (FastAPI + SurrealDB), prints its public preview URL and
   a QR code to open on your phone, and copies credentials into the sandbox's `app/.env`.
3. For **each upload**, the server starts a fresh **ephemeral sandbox linked to itself** (`linked_sandbox=`,
   `ttl_minutes=20`). The sandbox pulls its job, the video and the code over the private link network using a
   per-job token, plans, posts back one tar.gz, and is then deleted. The user's office video never shares a process
   with anyone else's.

`standup_agent/daytona_runner.py` is the standalone version, for testing from a laptop without the web app. It
stores the short-lived Bedrock token and the Arize key as **Daytona secrets restricted to their hosts**
(`bedrock-runtime.<region>.amazonaws.com`, `otlp.arize.com`). Code inside the sandbox can call Claude and send traces
but never sees either value. If the API key lacks the secrets permission, it falls back to env vars and logs a
warning.

`python deploy/deploy_daytona.py down` deletes the server sandbox and any leftover planner sandboxes.

### Arize AX: tracing

`standup_agent/tracing.py` turns tracing on whenever `ARIZE_SPACE_ID` and `ARIZE_API_KEY` are set; otherwise every
span is a no-op. Each upload produces **one trace** that spans processes and sandboxes. The server injects a W3C
`traceparent` into the sandbox (`STANDUP_TRACEPARENT`), so the planner's spans join the server's trace. All traces
from a run share a `session.id`.

- `LLM` spans: every Claude call, with prompt, output, token counts and (optionally) the input frames as images.
  Set `STANDUP_TRACE_IMAGES=0` to leave the images out.
- Claude runs with adaptive thinking (`display: "summarized"`); the summary is recorded on the LLM span as a
  *reasoning* part before the answer, so Arize shows why the model picked each spot.
- `EVALUATOR` spans: `judge.frames` / `judge.video` verdicts (scores, issue, range of motion). They join the
  `compose_start_frame` / `generate_video` / `backend.<route>` spans of the video path via `STANDUP_TRACEPARENT`.
- Spans that finish without error are marked `OK` (not `UNSET`), so failures are easy to filter.
- `GUARDRAIL` spans: `safety_guardrails` and `plan_validation`, with a `guardrail.override` event every time a safety
  rule overrides the model.
- `CHAIN` / `TOOL` / `AGENT` spans: transcoding, sandbox create/upload/plan/download/delete, and backend jobs.
- The project defaults to `just-standup` (`ARIZE_PROJECT_NAME`). Set `STANDUP_TRACE_CONSOLE=1` to also print spans.

### AWS: Amazon Bedrock for the planner

The planner model (Claude) runs on **Amazon Bedrock** (`bedrock-runtime`). `BedrockVLM` in `reasoner.py` uses
`anthropic.AnthropicBedrock`:

- Default model: `global.anthropic.claude-opus-5-5`. A refusal-fallback middleware retries on
  `global.anthropic.claude-opus-4-8`.
- Effort comes from `STANDUP_VLM_EFFORT` (default `medium`).
- It authenticates with a **short-lived Bedrock bearer token** (`BEDROCK_API_KEY`, 30 min). The server mints one per
  sandbox from its AWS IAM credentials, using `aws-bedrock-token-generator` + botocore. Long-lived AWS keys never enter a planner sandbox.
- `--vlm <path>` switches to a local Qwen3-VL checkpoint, which has the same `ask()` / `ask_json()` interface.

---

## 2. Technical pipeline

```
phone ──video──▶ FastAPI server (Daytona server sandbox)
                   │ ffmpeg → upright 720p H.264, ≤60 s
                   │ SurrealDB: create upload, export candidate catalog
                   ▼
                 linked ephemeral planner sandbox (Daytona)
                   │ standup_agent.worker → standup_agent.run --plan_only
                   │   1. sample frames from the video
                   │   2. Claude: list objects + what each safely affords
                   │   3. safety guardrails (rule-based, overrides Claude)
                   │   4. Claude: choose 3 exercises from the catalog
                   │   5. plan validation guardrail
                   │   6. pick the best/sharpest frame for each spot
                   ▼ tar.gz (plan.json, plan.md, frames)
                 server: save_plan → SurrealDB graph edges
                   ▼
phone ◀── GET /api/uploads/{id}: 3 spots + frames + ExRx instructions/media
```

All of this is traced in Arize as one trace.

### Planning (`standup_agent/reasoner.py`, `standup_agent/run.py`)

1. **Frames.** `read_frames` samples N frames uniformly (`--frames`, 16 in the app).
2. **Scene analysis.** Claude lists the visible objects and assigns each one *affordances* from a fixed vocabulary:
   `seat`, `table_edge`, `wall`, `rail`, `step`, `floor_open`.
3. **Safety guardrails** (`apply_safety_rules`). Deterministic rules remove affordances the model must not use, for
   example rolling or unstable objects, and drop objects that have no safe affordance. Every override is recorded in
   `plan.json` (`guardrail_overrides`) and traced.
4. **Exercise choice** (`plan`). Claude picks 3 exercises from **3 different categories**, so they train different body
   regions, choosing only from the catalog (exported from SurrealDB). Each pick is tied to a specific object.
   `_validate_plan` rejects picks that reference unknown exercises or objects, or that break the affordance rules.
5. **Frame choice.** For each suggestion, it picks the frame that shows the object best, combining a sharpness score,
   a person detector (avoids frames with people in them) and a Claude check.
6. Outputs: `plan.json`, `plan.md`, and `sugg<k>_<exercise>/scene_frame.jpg`.

### Optional: generated demo videos (offline research, GPU)

Without `--plan_only`, `standup_agent.run` continues for each suggestion. It composes the user (from their photos)
into the chosen frame, judges candidate start frames, generates a workout clip, and judges it, retrying on failure.
The judge (`standup_agent/judge.py`) combines VLM scores with measured checks: person count, background drift,
range of motion and rep counting. Backends run as subprocesses in their own conda envs under one job contract,
documented in [`standup_agent/BACKENDS.md`](standup_agent/BACKENDS.md). There is a per-backend `NOTES.md` under
`standup_agent/backends/`. Their model code is in the `third_party/` git submodules, and weights go in
`checkpoints/` (gitignored). This path is **not** used by the web app.

---

## 3. Running it

### Environments

```bash
git submodule update --init --recursive     # only needed for the video-generation backends
conda env create -f envs/standup.yml        # planner + web app (torch, Qwen3-VL, fastapi, arize-otel, ...)
pip install fastapi "uvicorn[standard]" python-multipart "surrealdb>=2" "daytona>=0.224.1" boto3 \
    aws-bedrock-token-generator "anthropic[bedrock]>=1.15" "arize-otel>=0.14" "qrcode[pil]"  # sponsor SDKs not all pinned in the yml
# envs/wan21.yml, envs/wan22.yml            # video-generation backends only
cp .env.example .env                        # fill in; .env is gitignored
```

`standup_agent/backends.json` maps each backend to its env's Python. It currently contains absolute paths from the
original machine, so edit it for yours.

### Credentials (environment or `.env`)

| Variable | For |
|---|---|
| `DAYTONA_API_KEY` | deploying, per-upload sandboxes |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, (`AWS_SESSION_TOKEN`), `AWS_REGION` | minting Bedrock tokens (needs Bedrock access to Claude) |
| `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, (`ARIZE_PROJECT_NAME`) | tracing (optional) |
| `SURREAL_URL`, `SURREAL_USER`, `SURREAL_PASS`, `SURREAL_NS`, `SURREAL_DB` | web app database (defaults: local `ws://127.0.0.1:8000/rpc`, ns/db `exrx`) |
| `EXRX_DB_USER`, `EXRX_DB_PASS`, `EXRX_READER_PASS`, `EXRX_API_KEY` | building / querying the local ExRx graph |

### Build the exercise graph (once)

See `exrx_db/README.md`: `download.py` → `parse.py` → `serve.sh` → `load.py`. Then seed the app tables:

```bash
python -c "from webapp import db; db.seed()"
```

### Run the web app

```bash
# local, planner as a local process (no Daytona needed)
STANDUP_RUNNER=local uvicorn webapp.server:app --host 0.0.0.0 --port 8080

# full cloud deploy on Daytona (prints URL + QR code)
python deploy/deploy_daytona.py up
python deploy/deploy_daytona.py down
```

### Run the planner directly

```bash
# locally, Claude on Bedrock
python -m standup_agent.run --video data/<scene>.mp4 --photos data/user_photos --out outputs/agent/<scene> \
    --vlm bedrock --plan_only

# inside a throwaway Daytona sandbox
python -m standup_agent.daytona_runner --video data/<scene>.mp4 --photos data/user_photos --out outputs/agent/x
```

---

## 4. Repo map

| Path | What |
|---|---|
| `webapp/` | FastAPI server (`server.py`), SurrealDB layer (`db.py`), phone UI (`index.html`) |
| `deploy/` | Daytona deploy script, planner snapshot image and boot entrypoint, demo QR code |
| `standup_agent/` | planner (`reasoner.py`, `run.py`), sandbox worker / runner, tracing, judge, video backends |
| `standup_agent/catalog.json` | affordance vocabulary + fallback exercise catalog (the app replaces it with the SurrealDB export) |
| `exrx_db/` | ExRx scraper/parser, SurrealDB schema, SurrealQL API, Python query tools, MCP server |
| `envs/` | conda env exports |
| `third_party/` | git submodules: Wan2.2, VACE, Phantom, DiffSynth-Studio (video backends only) |
| `.claude/skills/` | SurrealDB agent skills for Claude Code (pinned in `skills-lock.json`) |
| `outputs/archive_3d/` | summaries/frames from the archived 3D/HOI experiments (videos and meshes gitignored) |

Not in git: `data/` (videos, photos, ExRx content), `checkpoints/`, `body_models/`, `work/`, `.env`.
