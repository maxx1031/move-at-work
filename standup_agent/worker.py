"""Runs inside a per-upload planner sandbox, which is linked to the web server sandbox on a private network.

The sandbox boots from the `standup-planner` snapshot, whose entrypoint (deploy/planner_boot.py) downloads this code
from the server and runs `python -m standup_agent.worker`. The worker:
1. fetches the job (settings + the exercise library exported from SurrealDB) and the user's video from the server,
2. plans with Claude on Bedrock (`standup_agent.run --plan_only`, with the safety guardrails),
3. posts the plan, the chosen frames and its log back to the server as one tar.gz.
Env: STANDUP_JOB_URL, STANDUP_JOB_TOKEN (+ BEDROCK_API_KEY, AWS_REGION and the optional Arize variables).
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

URL, TOKEN = os.environ["STANDUP_JOB_URL"], os.environ["STANDUP_JOB_TOKEN"]


def call(path, data=None, content_type=None):
    headers = {"X-Job-Token": TOKEN, **({"Content-Type": content_type} if content_type else {})}
    with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, headers=headers), timeout=300) as r:
        return r.read()


def main():
    job = json.loads(call("/job"))
    video = Path("in") / job["video_name"]
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(call("/video"))
    Path("standup_agent/catalog.json").write_text(json.dumps(job["catalog"]))  # the library, from SurrealDB
    cmd = [sys.executable, "-m", "standup_agent.run", "--video", str(video), "--out", "out", "--vlm", job["vlm"],
           "--n", str(job["n"]), "--frames", str(job["frames"]), "--plan_only"]
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = Path("out")
    out.mkdir(exist_ok=True)
    (out / "worker.json").write_text(json.dumps({"exit_code": p.returncode, "seconds": round(time.time() - t, 1),
                                                 "log": (p.stdout + p.stderr)[-6000:]}))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(out, arcname=".")
    call("/result", buf.getvalue(), "application/gzip")


if __name__ == "__main__":
    main()
