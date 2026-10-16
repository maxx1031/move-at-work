"""Entrypoint of the `standup-planner` Daytona snapshot: fetch the planner code from the job server over the link
network, then run the worker. Without STANDUP_JOB_URL (e.g. a sandbox started by hand) it does nothing."""
import io
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

url, token = os.environ.get("STANDUP_JOB_URL"), os.environ.get("STANDUP_JOB_TOKEN")
if not url:
    sys.exit(0)
app = Path.home() / "app"
app.mkdir(parents=True, exist_ok=True)
for attempt in range(30):  # the link network can take a moment after boot
    try:
        req = urllib.request.Request(url + "/bundle.tar.gz", headers={"X-Job-Token": token})
        with urllib.request.urlopen(req, timeout=30) as r:
            tarfile.open(fileobj=io.BytesIO(r.read())).extractall(app, filter="data")
        break
    except OSError as e:
        print(f"bundle fetch failed ({e}); retrying", flush=True)
        time.sleep(2)
else:
    sys.exit("could not fetch the planner bundle")
sys.exit(subprocess.call([sys.executable, "-m", "standup_agent.worker"], cwd=app))
