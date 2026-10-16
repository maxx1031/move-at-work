"""Plan one user upload inside a throwaway Daytona sandbox: the office video and photos never touch our server.

python -m standup_agent.daytona_runner --video data/404711060.mp4 --photos data/user_photos --out outputs/agent/x

1. Mint a short-lived Bedrock token and store it (and the Arize key) as Daytona secrets limited to their hosts:
   code in the sandbox can call Claude / send traces, but never sees either value.
2. Create an ephemeral sandbox with a hard TTL, upload the planner code, the video and the photos.
3. Run `standup_agent.run --plan_only` there (Claude on Bedrock + the safety guardrails).
4. Download only the plan (plan.json, plan.md, chosen scene frames), then delete the sandbox.

Needs DAYTONA_API_KEY and AWS credentials with Bedrock access; Arize tracing is on when ARIZE_SPACE_ID/ARIZE_API_KEY
are set. Each step is a span; the planner's spans inside the sandbox join the same trace.
"""
import argparse
import datetime
import json
import os
import shlex
import time
import uuid
from pathlib import Path

from . import tracing as T

PKG = Path(__file__).resolve().parent
CODE = ["__init__.py", "reasoner.py", "run.py", "tracing.py", "catalog.json"]  # all the planner needs
DEPS = ["anthropic[bedrock]>=1.15", "arize-otel>=0.14"]
BEDROCK_SECRET, ARIZE_SECRET = "standup-bedrock-token", "standup-arize-api-key"
IMAGE_EXT = (".jpg", ".jpeg", ".png")


def ensure_secret(daytona, name, value, hosts):
    """Create or update an organization secret that is only substituted for requests to `hosts`."""
    from daytona import CreateSecretParams, UpdateSecretParams
    existing = next((s for s in daytona.secret.list().items if s.name == name), None)
    if existing:
        daytona.secret.update(existing.id, UpdateSecretParams(value=value, hosts=hosts))
    else:
        daytona.secret.create(CreateSecretParams(name=name, value=value, hosts=hosts,
                                                 description="Just Standup planner (managed by daytona_runner)"))


def bedrock_token(region, minutes):
    from aws_bedrock_token_generator import provide_token
    return provide_token(region=region, expiry=datetime.timedelta(minutes=minutes))


def run_upload(video, photos, out, vlm="bedrock", n=3, frames=16, region="us-west-2", ttl=20, keep=False,
               timeout=900):
    from daytona import (CreateSandboxFromSnapshotParams, Daytona, FileUpload, SessionExecuteRequest)
    video, out = Path(video).resolve(), Path(out).resolve()
    photos = sorted(p for p in Path(photos).iterdir() if p.suffix.lower() in IMAGE_EXT) if Path(photos).is_dir() \
        else [Path(p) for p in photos.split(",")]
    upload_id = uuid.uuid4().hex[:8]
    os.environ.setdefault(T.SESSION_ENV, f"upload-{upload_id}")
    daytona = Daytona()
    timings, sandbox = {}, None

    with T.span("handle_upload", "CHAIN", input={"video": video.name, "photos": len(photos), "vlm": vlm},
                metadata={"upload_id": upload_id, "ttl_minutes": ttl}) as root:
        t0 = time.time()
        bedrock_host = f"bedrock-runtime.{region}.amazonaws.com"
        secrets, env = {}, {"AWS_REGION": region, "STANDUP_PERSON_DETECTOR": "0",
                            T.SESSION_ENV: os.environ[T.SESSION_ENV],
                            "DAYTONA_SANDBOX_OTEL_SERVICE_NAME": "just-standup-planner"}
        creds = {}  # sandbox env var -> (secret name, value, allowed host)
        if vlm.startswith("bedrock"):
            creds["BEDROCK_API_KEY"] = (BEDROCK_SECRET, bedrock_token(region, ttl + 10), bedrock_host)
        if T.enabled():
            creds["ARIZE_API_KEY"] = (ARIZE_SECRET, os.environ["ARIZE_API_KEY"], "otlp.arize.com")
            env.update(ARIZE_SPACE_ID=os.environ["ARIZE_SPACE_ID"], ARIZE_PROJECT_NAME=T.PROJECT,
                       STANDUP_ARIZE_TRANSPORT="http")
        with T.span("secrets.refresh", "CHAIN", input={k: v[2] for k, v in creds.items()}) as s:
            try:
                for var, (name, value, host) in creds.items():
                    ensure_secret(daytona, name, value, [host])
                    secrets[var] = name
            except Exception as e:
                if getattr(e, "status", None) != 403:
                    raise
                # The Daytona API key lacks the "manage secrets" permission: fall back to plain env vars, so the
                # values are readable inside the sandbox (the Bedrock token still expires with the sandbox).
                print(f"[{upload_id}] WARNING: Daytona key cannot manage secrets; passing credentials as env vars",
                      flush=True)
                T.event("secrets.fallback_to_env", reason="403 from Daytona secrets API")
                s.set_attribute("secrets.mode", "env")
                secrets = {}
                env.update({var: value for var, (_, value, _) in creds.items()})
        timings["secrets_s"] = round(time.time() - t0, 1)

        try:
            t = time.time()
            with T.span("sandbox.create", "CHAIN") as s:
                sandbox = daytona.create(CreateSandboxFromSnapshotParams(
                    ephemeral=True, ttl_minutes=ttl, env_vars=env, secrets=secrets,
                    labels={"app": "just-standup", "upload": upload_id}))
                s.set_attribute("daytona.sandbox_id", sandbox.id)
            root.set_attribute("daytona.sandbox_id", sandbox.id)
            timings["create_s"] = round(time.time() - t, 1)
            print(f"[{upload_id}] sandbox {sandbox.id} ({timings['create_s']} s)", flush=True)

            t = time.time()
            with T.span("sandbox.upload", "CHAIN", input={"files": len(CODE) + 1 + len(photos)}):
                files = [FileUpload(source=str(PKG / f), destination=f"app/standup_agent/{f}") for f in CODE]
                files.append(FileUpload(source=str(video), destination=f"in/{video.name}"))
                files += [FileUpload(source=str(p), destination=f"in/photos/{p.name}") for p in photos]
                sandbox.fs.upload_files(files)
            timings["upload_s"] = round(time.time() - t, 1)

            t = time.time()
            with T.span("sandbox.install", "CHAIN", input=DEPS) as s:
                r = sandbox.process.exec("pip install -q " + " ".join(shlex.quote(d) for d in DEPS), timeout=300)
                s.set_attribute("exit_code", r.exit_code)
                if r.exit_code != 0:
                    raise RuntimeError(f"pip install failed:\n{r.result[-2000:]}")
            timings["install_s"] = round(time.time() - t, 1)

            t = time.time()
            with T.span("sandbox.plan", "CHAIN", input={"vlm": vlm, "n": n, "frames": frames}) as s:
                cmd = (f"cd ~/app && STANDUP_TRACEPARENT={shlex.quote(T.inject().get('traceparent', ''))} "
                       f"python -m standup_agent.run --video ~/in/{shlex.quote(video.name)} --photos ~/in/photos "
                       f"--out ~/out --vlm {shlex.quote(vlm)} --n {n} --frames {frames} --plan_only")
                sandbox.process.create_session("plan")
                c = sandbox.process.execute_session_command("plan", SessionExecuteRequest(command=cmd, run_async=True))
                while (exit_code := sandbox.process.get_session_command("plan", c.cmd_id).exit_code) is None:
                    if time.time() - t > timeout:
                        raise TimeoutError(f"planning did not finish in {timeout} s")
                    time.sleep(3)  # polling is toolbox traffic, which also keeps the sandbox from auto-stopping
                logs = sandbox.process.get_session_command_logs("plan", c.cmd_id)
                log_text = (logs.stdout or "") + (logs.stderr or "")
                s.set_attribute("exit_code", exit_code)
                T.set_output(s, log_text[-4000:])
            timings["plan_s"] = round(time.time() - t, 1)
            print(log_text.strip()[-1500:], flush=True)

            t = time.time()
            with T.span("sandbox.download", "CHAIN") as s:
                r = sandbox.process.exec("cd ~/out 2>/dev/null && pwd && find . -type f", timeout=30)
                remote_out, *listing = r.result.split() if r.exit_code == 0 else [""]
                out.mkdir(parents=True, exist_ok=True)
                for rel in listing:
                    data = sandbox.fs.download_file(f"out/{rel[2:]}")
                    if rel.endswith((".json", ".md")):  # point the plan at the local copies of the frames
                        data = data.decode().replace(remote_out, str(out)).encode()
                    (out / rel[2:]).parent.mkdir(parents=True, exist_ok=True)
                    (out / rel[2:]).write_bytes(data)
                T.set_output(s, listing)
            timings["download_s"] = round(time.time() - t, 1)
            if exit_code != 0 or not (out / "plan.json").exists():
                T.fail(root, f"planning failed (exit {exit_code})")
        finally:
            if sandbox is not None and not keep:
                with T.span("sandbox.delete", "CHAIN"):
                    sandbox.delete()
        timings["total_s"] = round(time.time() - t0, 1)
        result = {"upload_id": upload_id, "sandbox_id": sandbox.id if sandbox else None, "exit_code": exit_code,
                  "timings": timings, "out": str(out), "files": [r[2:] for r in listing]}
        (out / "daytona_run.json").write_text(json.dumps(result, indent=2))
        T.set_output(root, result)
    T.flush()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--photos", required=True, help="dir or comma list of the user's photos")
    ap.add_argument("--out", required=True, help="local folder for the downloaded plan")
    ap.add_argument("--vlm", default="bedrock", help="'bedrock' or 'bedrock:<model id>'")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    ap.add_argument("--ttl", type=int, default=20, help="minutes before Daytona destroys the sandbox regardless")
    ap.add_argument("--keep", action="store_true", help="do not delete the sandbox (debugging; TTL still applies)")
    a = ap.parse_args()
    r = run_upload(a.video, a.photos, a.out, a.vlm, a.n, a.frames, a.region, a.ttl, a.keep)
    print(json.dumps(r["timings"]), "->", r["out"])


if __name__ == "__main__":
    main()
