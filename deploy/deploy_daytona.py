"""Deploy the Just Standup test app to Daytona, fully in the cloud.

python deploy/deploy_daytona.py up        # build the planner snapshot if needed, create/update the server, print a QR
python deploy/deploy_daytona.py down      # delete the server sandbox (and any leftover planner sandboxes)

* Server sandbox (long-running, public preview URL): FastAPI web app + SurrealDB (rocksdb, on the sandbox disk).
* Planner snapshot, built from deploy/planner_image.py (Python + Claude/Arize/OpenCV deps; its entrypoint fetches the
  planner code and one job from the server over the link network). Each upload gets a fresh linked, ephemeral sandbox
  from it. Linked sandboxes need a snapshot, and this one is taken from a builder sandbox, which needs only the
  sandbox permission (creating snapshots directly needs write:snapshots).

Reads DAYTONA_API_KEY, AWS_* (Bedrock access) and optional ARIZE_* from the environment, and copies them to the
server's app/.env. Rerun `up` with fresh AWS credentials to refresh them without recreating anything.
"""
import argparse
import hashlib
import os
import shlex
import sys
import time
import urllib.request
from pathlib import Path

from daytona import CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams, Daytona, FileUpload, SessionExecuteRequest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from planner_image import BOOT, PLANNER_DEPS, planner_image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_FILES = ["webapp/__init__.py", "webapp/server.py", "webapp/db.py", "webapp/index.html",
             "standup_agent/__init__.py", "standup_agent/reasoner.py", "standup_agent/run.py",
             "standup_agent/tracing.py", "standup_agent/worker.py", "standup_agent/catalog.json"]
SERVER_DEPS = ["fastapi", "uvicorn[standard]", "python-multipart", "surrealdb>=2", "daytona>=0.224.1", "boto3",
               "aws-bedrock-token-generator", "arize-otel>=0.14", "qrcode[pil]", "numpy"]
SURREAL_VERSION = "v3.3.0"
ENV_KEYS = ["DAYTONA_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION",
            "AWS_DEFAULT_REGION", "ARIZE_SPACE_ID", "ARIZE_API_KEY", "ARIZE_PROJECT_NAME", "STANDUP_VLM",
            "SURREAL_URL", "SURREAL_USER", "SURREAL_PASS", "SURREAL_NS", "SURREAL_DB"]
LABELS = {"app": "just-standup", "role": "server"}


def planner_snapshot(daytona):
    """Named by a hash of the deps + boot script, so a change builds a new snapshot instead of reusing a stale one."""
    name = "standup-planner-" + hashlib.sha256((" ".join(PLANNER_DEPS) + BOOT.read_text()).encode()).hexdigest()[:8]
    try:
        state = str(daytona.snapshot.get(name).state).lower()
        if state.endswith("inactive"):
            daytona.snapshot.activate(name)
        if not state.endswith(("error", "failed")):
            print(f"planner snapshot {name}: ready")
            return name
    except Exception:
        pass
    print(f"building planner snapshot {name} (image build + snapshot, ~2 min) ...")
    builder = daytona.create(CreateSandboxFromImageParams(image=planner_image(),
                                                          labels={"app": "just-standup", "role": "builder"}), timeout=0)
    try:
        builder.stop(timeout=120)
        builder.create_snapshot(name)
    finally:
        builder.delete()
    return name


def find_server(daytona):
    return next((s for s in daytona.list() if (s.labels or {}).get("role") == "server"
                 and (s.labels or {}).get("app") == "just-standup"), None)


def sh(sandbox, cmd, timeout=600):
    r = sandbox.process.exec(cmd, timeout=timeout)
    if r.exit_code != 0:
        raise SystemExit(f"$ {cmd}\n{r.result[-3000:]}")
    return r.result


def up(recreate=False):
    daytona = Daytona()
    snapshot = planner_snapshot(daytona)
    server = find_server(daytona)
    if server is not None and recreate:  # network rules are fixed when a sandbox is created
        print(f"deleting old server sandbox {server.id}")
        server.delete(timeout=120, wait=True)
        server = None
    if server is None:
        server = daytona.create(CreateSandboxFromSnapshotParams(
            snapshot="daytona-medium", public=True, auto_stop_interval=0, labels=LABELS), timeout=180)
        print(f"created server sandbox {server.id}")
    else:
        if str(server.state).lower().endswith(("stopped", "archived")):
            server.start(timeout=180)
        print(f"updating server sandbox {server.id}")
    url = server.get_preview_link(8080).url

    env = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
    env["PLANNER_SNAPSHOT"] = snapshot
    dotenv = "".join(f"{k}={shlex.quote(v)}\n" for k, v in env.items())
    config = f'{{"server_sandbox_id": "{server.id}", "public_url": "{url}"}}'
    server.fs.upload_files([FileUpload(source=str(ROOT / f), destination=f"app/{f}") for f in APP_FILES]
                           + [FileUpload(source=dotenv.encode(), destination="app/.env"),
                              FileUpload(source=config.encode(), destination="app/config.json")])
    sh(server, "chmod 600 ~/app/.env")

    print("installing server dependencies ...")
    sh(server, "pip install -q " + " ".join(shlex.quote(d) for d in SERVER_DEPS), timeout=900)
    sh(server, f"test -x ~/bin/surreal || (mkdir -p ~/bin && curl -sL https://github.com/surrealdb/surrealdb/releases/"
               f"download/{SURREAL_VERSION}/surreal-{SURREAL_VERSION}.linux-amd64.tgz | tar -xz -C ~/bin)")

    sessions = {s.session_id for s in server.process.list_sessions()}
    cloud_db = os.environ.get("SURREAL_URL", "").startswith("wss://")  # e.g. SurrealDB Cloud
    if cloud_db:
        print(f"using SurrealDB at {os.environ['SURREAL_URL']}")
    elif "surreal" not in sessions:
        server.process.create_session("surreal")
        server.process.execute_session_command("surreal", SessionExecuteRequest(run_async=True, command=(
            "mkdir -p ~/data && ~/bin/surreal start --bind 127.0.0.1:8000 -u root -p root "
            "rocksdb:$HOME/data/surreal.db > ~/surreal.log 2>&1")))
        time.sleep(3)
    server.process.exec("pkill -f 'uvicorn webapp.server' || true", timeout=30)
    if "web" in sessions:
        server.process.delete_session("web")
    server.process.create_session("web")
    server.process.execute_session_command("web", SessionExecuteRequest(run_async=True, command=(
        "cd ~/app && STANDUP_DATA=$HOME/data/uploads python -m uvicorn webapp.server:app "
        "--host 0.0.0.0 --port 8080 > ~/web.log 2>&1")))

    for attempt in range(40):
        try:
            req = urllib.request.Request(url + "/healthz", headers={"X-Daytona-Skip-Preview-Warning": "true"})
            print("health:", urllib.request.urlopen(req, timeout=10).read().decode())
            break
        except Exception:
            time.sleep(3)
    else:
        print(server.process.exec("tail -40 ~/web.log; tail -10 ~/surreal.log", timeout=30).result)
        raise SystemExit("server did not come up")

    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    png = ROOT / "deploy" / "qr.png"
    qr.make_image().save(png)
    print(f"\nOpen on your phone: {url}\nQR image: {png}   (or show {url}/qr on a laptop screen)")


def down():
    daytona = Daytona()
    for s in daytona.list():
        if (s.labels or {}).get("app") == "just-standup":
            print(f"deleting {s.id} ({(s.labels or {}).get('role')})")
            s.delete()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["up", "down"])
    ap.add_argument("--recreate", action="store_true", help="replace the server sandbox (new URL)")
    a = ap.parse_args()
    if not os.environ.get("DAYTONA_API_KEY"):
        sys.exit("source ~/.daytona.env (and ~/.aws.env, ~/.arize.env, ~/.surreal.env) first")
    up(a.recreate) if a.action == "up" else down()
