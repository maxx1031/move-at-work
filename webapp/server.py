"""Just Standup test web app: record your workspace on your phone, get 3 workout spots back.

uvicorn webapp.server:app --host 0.0.0.0 --port 8080        (from the repo root; SurrealDB must be running)

POST /api/uploads            phone video -> upload id (planning runs in the background)
GET  /api/uploads/{id}       status, then the 3 recommendations read back from SurrealDB
/internal/jobs/{id}/...      what the per-upload planner sandbox fetches / posts (per-job token)

STANDUP_RUNNER=daytona (default when SERVER_SANDBOX_ID is set): each upload is planned in its own linked Daytona
sandbox started from the PLANNER_SNAPSHOT snapshot. STANDUP_RUNNER=local runs the same worker as a local process.
Credentials (DAYTONA_API_KEY, AWS_*, ARIZE_*) are read from the environment or from app/.env on every job, so they
can be refreshed without restarting.
"""
import datetime
import io
import json
import os
import secrets as pysecrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from standup_agent import tracing as T
from webapp import db

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("STANDUP_DATA", ROOT / "data_web"))
CONFIG = ROOT / "config.json"  # written by deploy: server sandbox id + public URL
BUNDLE_FILES = ["standup_agent/__init__.py", "standup_agent/reasoner.py", "standup_agent/run.py",
                "standup_agent/tracing.py", "standup_agent/worker.py", "standup_agent/catalog.json"]
JOB_TIMEOUT = 12 * 60

app = FastAPI(title="Just Standup")
JOBS = {}  # upload id -> {"token", "event", "dir"}


def settings():
    """Env plus app/.env (re-read each time so credentials can be refreshed by redeploying the file)."""
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    if CONFIG.exists():
        env.update({k.upper(): str(v) for k, v in json.loads(CONFIG.read_text()).items()})
    return env


@app.on_event("startup")
def startup():
    DATA.mkdir(parents=True, exist_ok=True)
    # SurrealDB settings may come from app/.env (deploy writes them there); db.connect() reads os.environ.
    os.environ.update({k: v for k, v in settings().items() if k.startswith("SURREAL_")})
    for attempt in range(20):  # SurrealDB may still be starting
        try:
            db.seed()
            return
        except Exception as e:
            print(f"waiting for SurrealDB ({e})", flush=True)
            time.sleep(1)
    raise RuntimeError("SurrealDB unreachable")


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).with_name("index.html")).read_text()


@app.get("/healthz")
def healthz():
    return {"ok": True, "exercises": len(db.export_catalog()["exercises"])}


@app.get("/qr", response_class=HTMLResponse)
def qr_page(request: Request):
    url = settings().get("PUBLIC_URL") or str(request.base_url)
    return f"""<html><body style="font-family:sans-serif;text-align:center;margin-top:40px">
    <h1>Just Standup</h1><p>Scan to try it on your phone</p><img src="/qr.png" width="360"><p>{url}</p></body></html>"""


@app.get("/qr.png")
def qr_png(request: Request):
    import qrcode
    buf = io.BytesIO()
    qrcode.make(settings().get("PUBLIC_URL") or str(request.base_url)).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ------------------------------------------------------------------ public API

@app.post("/api/uploads")
async def create_upload(video: UploadFile = File(...)):
    upload_id = uuid.uuid4().hex[:10]
    d = DATA / upload_id
    d.mkdir(parents=True)
    raw = d / ("raw" + (Path(video.filename or "").suffix.lower() or ".mp4"))
    with raw.open("wb") as f:
        shutil.copyfileobj(video.file, f)
    db.create_upload(upload_id, raw.name)
    JOBS[upload_id] = {"token": pysecrets.token_urlsafe(24), "event": threading.Event(), "dir": d}
    threading.Thread(target=run_job, args=(upload_id,), daemon=True).start()
    return {"id": upload_id}


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    upload, recs, options = db.recommendations(upload_id)
    if not upload:
        raise HTTPException(404)
    upload = {k: v for k, v in upload.items() if k in ("status", "stage", "error", "scene", "setting", "timings")}
    return {"id": upload_id, **upload, "recommendations": recs, "other_options": options}


@app.get("/api/uploads/{upload_id}/frame/{rank}.jpg")
def get_frame(upload_id: str, rank: int):
    hits = sorted((DATA / upload_id / "out").glob(f"sugg{rank}_*/scene_frame.jpg"))
    if not hits:
        raise HTTPException(404)
    return FileResponse(hits[0], media_type="image/jpeg")


# ------------------------------------------------------------------ planner sandbox <-> server

def _job(upload_id, request):
    job = JOBS.get(upload_id)
    if not job or request.headers.get("x-job-token") != job["token"]:
        raise HTTPException(403)
    return job


@app.get("/internal/jobs/{upload_id}/bundle.tar.gz")
def job_bundle(upload_id: str, request: Request):
    _job(upload_id, request)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in BUNDLE_FILES:
            tar.add(ROOT / f, arcname=f)
    return Response(buf.getvalue(), media_type="application/gzip")


@app.get("/internal/jobs/{upload_id}/job")
def job_spec(upload_id: str, request: Request):
    _job(upload_id, request)
    return {"video_name": "video.mp4", "catalog": db.export_catalog(),
            "vlm": settings().get("STANDUP_VLM", "bedrock"), "n": 3, "frames": 16}


@app.get("/internal/jobs/{upload_id}/video")
def job_video(upload_id: str, request: Request):
    return FileResponse(_job(upload_id, request)["dir"] / "video.mp4", media_type="video/mp4")


@app.post("/internal/jobs/{upload_id}/result")
async def job_result(upload_id: str, request: Request):
    job = _job(upload_id, request)
    out = job["dir"] / "out"
    tarfile.open(fileobj=io.BytesIO(await request.body())).extractall(out, filter="data")
    job["event"].set()
    return {"ok": True}


# ------------------------------------------------------------------ job runner

def run_job(upload_id):
    job, env = JOBS[upload_id], settings()
    d, t0, timings = job["dir"], time.time(), {}
    os.environ[T.SESSION_ENV] = f"upload-{upload_id}"
    with T.span("handle_upload", "CHAIN", input={"upload_id": upload_id}) as root:
        try:
            db.set_status(upload_id, "running", "preparing video")
            with T.span("transcode", "TOOL"):
                prepare_video(next(d.glob("raw.*")), d / "video.mp4")
            timings["transcode_s"] = round(time.time() - t0, 1)

            runner = env.get("STANDUP_RUNNER") or ("daytona" if env.get("SERVER_SANDBOX_ID") else "local")
            worker_env = planner_env(env, upload_id, job["token"])
            db.set_status(upload_id, "running", "starting a private sandbox")
            t = time.time()
            with T.span(f"planner.{runner}", "AGENT", metadata={"runner": runner}) as s:
                worker_env["STANDUP_TRACEPARENT"] = T.inject().get("traceparent", "")
                stop = start_daytona(env, worker_env, upload_id, s) if runner == "daytona" \
                    else start_local(worker_env, d)
                db.set_status(upload_id, "running", "Claude is checking your space")
                try:
                    if not job["event"].wait(JOB_TIMEOUT):
                        raise TimeoutError("planner did not report back in time")
                finally:
                    stop()
            timings["plan_s"] = round(time.time() - t, 1)

            w = json.loads((d / "out" / "worker.json").read_text())
            if not (d / "out" / "plan.json").exists():
                raise RuntimeError(f"planner failed (exit {w['exit_code']}): {w['log'][-800:]}")
            db.set_status(upload_id, "running", "saving to SurrealDB")
            db.save_plan(upload_id, json.loads((d / "out" / "plan.json").read_text()))
            timings["total_s"] = round(time.time() - t0, 1)
            db.set_status(upload_id, "done", "done", timings=timings)
            T.set_output(root, timings)
        except Exception as e:
            T.fail(root, str(e))
            db.set_status(upload_id, "error", "error", error=f"{type(e).__name__}: {e}"[:1000])
        finally:
            JOBS[upload_id]["event"].set()
            T.flush()


def prepare_video(src, dst):
    """Phones record HEVC .mov with rotation metadata; give the planner a small upright H.264 MP4."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", "60", "-vf", "scale=-2:'min(720,ih)'",
           "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", str(dst)]
    if subprocess.run(cmd).returncode != 0 or not dst.exists():
        shutil.copy(src, dst)


def planner_env(env, upload_id, token):
    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-west-2"
    port = env.get("PORT", "8080")
    host = env.get("SERVER_SANDBOX_ID", "127.0.0.1")  # linked sandboxes reach their parent by sandbox ID
    w = {"STANDUP_JOB_URL": f"http://{host}:{port}/internal/jobs/{upload_id}", "STANDUP_JOB_TOKEN": token,
         "AWS_REGION": region, "STANDUP_PERSON_DETECTOR": "0", T.SESSION_ENV: f"upload-{upload_id}",
         "DAYTONA_SANDBOX_OTEL_SERVICE_NAME": "just-standup-planner"}
    if env.get("STANDUP_VLM", "bedrock").startswith("bedrock"):
        w["BEDROCK_API_KEY"] = bedrock_token(env, region)
    if env.get("ARIZE_SPACE_ID") and env.get("ARIZE_API_KEY"):
        w.update(ARIZE_SPACE_ID=env["ARIZE_SPACE_ID"], ARIZE_API_KEY=env["ARIZE_API_KEY"],
                 ARIZE_PROJECT_NAME=T.PROJECT, STANDUP_ARIZE_TRANSPORT="http")
    return w


def bedrock_token(env, region, minutes=30):
    """A short-lived Bedrock bearer token minted from the server's AWS credentials, for one planner sandbox."""
    import botocore.session
    from aws_bedrock_token_generator import provide_token
    s = botocore.session.Session()
    s.set_credentials(env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"], env.get("AWS_SESSION_TOKEN"))
    return provide_token(region=region, aws_credentials_provider=_Static(s.get_credentials()),
                         expiry=datetime.timedelta(minutes=minutes))


class _Static:
    """Credential provider wrapper for provide_token (it calls .load())."""
    def __init__(self, creds):
        self.creds = creds

    def load(self):
        return self.creds


def start_daytona(env, worker_env, upload_id, span):
    """A fresh, ephemeral sandbox linked to this server: its entrypoint pulls the job from us and posts the result."""
    from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig
    daytona = Daytona(DaytonaConfig(api_key=env["DAYTONA_API_KEY"]))
    sb = daytona.create(CreateSandboxFromSnapshotParams(
        snapshot=env["PLANNER_SNAPSHOT"], linked_sandbox=env["SERVER_SANDBOX_ID"],
        ephemeral=True, ttl_minutes=20, env_vars=worker_env,
        labels={"app": "just-standup", "role": "planner", "upload": upload_id}), timeout=300)
    span.set_attribute("daytona.sandbox_id", sb.id)
    db.set_status(upload_id, "running", "starting a private sandbox", sandbox_id=sb.id)
    return lambda: daytona.delete(sb)


def start_local(worker_env, d):
    """Same worker, as a local process (for testing the app without Daytona)."""
    work = d / "worker"
    for f in BUNDLE_FILES:
        (work / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, work / f)
    p = subprocess.Popen([sys.executable, "-m", "standup_agent.worker"], cwd=work, env={**os.environ, **worker_env},
                         stdout=open(d / "worker.log", "w"), stderr=subprocess.STDOUT)
    return lambda: p.poll() is None and p.terminate()
