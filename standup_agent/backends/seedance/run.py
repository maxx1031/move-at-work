"""Seedance backend via fal.ai (US-available; BytePlus ModelArk is not offered in the US).

    <standup python> standup_agent/backends/seedance/run.py --job job.json

Hosted model, so no local GPU is used. The agent's judged start frame (the user already composed into the scene)
goes in as the image-to-video start frame, which carries the identity; the agent's prompt describes the exercise.

Config:
  FAL_KEY (required; environment or the repo's .env, never logged)
  SEEDANCE_ENDPOINT or job["options"]["endpoint"], one of the ENDPOINTS aliases below or a full fal endpoint id
      default "2.5-us"; use "2.0-fast" for cheaper / quicker iterations
  job["options"]: resolution ("720p"), duration (seconds, 4-30; default from the job), generate_audio (false),
      end_user_id (fal's optional end-customer id)
Seedance 2.5 image-to-video has no seed input on fal; every call is a fresh sample (fine for retries).
"""
import json
import os
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from _common import REPO, contact_sheet, main_wrapper, opt, write_meta  # noqa: E402

ENDPOINTS = {
    "2.5": "bytedance/seedance-2.5/image-to-video",
    "2.5-us": "bytedance/seedance-2.5/us/image-to-video",
    "2.0-fast": "bytedance/seedance-2.0/fast/image-to-video",
}
DEFAULT = "2.5-us"


def load_key():
    if os.environ.get("FAL_KEY"):
        return
    env = os.path.join(REPO, ".env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line.startswith("FAL_KEY="):
                os.environ["FAL_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY is not set (environment or .env in the repo root)")


def run(job, t0):
    import fal_client
    load_key()
    out = job["out_dir"]
    alias = opt(job, "endpoint", os.environ.get("SEEDANCE_ENDPOINT", DEFAULT))
    endpoint = ENDPOINTS.get(alias, alias)
    first = job.get("first_frame") or job["scene_frame"]

    t1 = time.time()
    image_url = fal_client.upload_file(first)  # fal storage URL for the start frame
    args = dict(prompt=job["prompt"], image_url=image_url,
                resolution=opt(job, "resolution", "720p"),
                duration=int(opt(job, "duration", max(4, round(job["num_frames"] / job["fps"])))),
                generate_audio=bool(opt(job, "generate_audio", False)))
    if opt(job, "end_user_id"):
        args["end_user_id"] = opt(job, "end_user_id")
    logs = []
    try:
        res = fal_client.subscribe(endpoint, arguments=args, with_logs=True,
                                   on_queue_update=lambda u: logs.append(str(getattr(u, "logs", u))[:300]))
    except Exception as e:
        # duration may be an enum of strings on some endpoints: retry once with a string
        if "duration" in str(e).lower():
            args["duration"] = str(args["duration"])
            res = fal_client.subscribe(endpoint, arguments=args, with_logs=True)
        else:
            raise
    gen_s = time.time() - t1
    with open(os.path.join(out, "fal_result.json"), "w") as f:
        json.dump({"endpoint": endpoint, "arguments": {k: v for k, v in args.items() if k != "image_url"},
                   "result": res, "queue_log_tail": logs[-5:]}, f, indent=2, default=str)

    url = (res.get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"no video url in fal result: {json.dumps(res, default=str)[:500]}")
    import requests
    v = requests.get(url, timeout=300)
    v.raise_for_status()
    path = os.path.join(out, "video.mp4")
    with open(path, "wb") as f:
        f.write(v.content)

    import cv2
    cap = cv2.VideoCapture(path)
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = []
    for i in range(0, max(n, 1), max(1, n // 6)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if ok:
            frames.append(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)))
    cap.release()
    if frames:
        contact_sheet(frames, os.path.join(out, "contact_sheet.jpg"))
    Image.open(first).save(os.path.join(out, "first_frame.png"))
    write_meta(job, backend="seedance", model_ids=[endpoint], resolution=[w, h], num_frames=n, fps=round(fps, 2),
               runtime_s=round(time.time() - t0, 1), peak_vram_gb=0.0,
               notes=f"fal.ai {endpoint}; generation {gen_s:.0f}s; seed {res.get('seed')}")


if __name__ == "__main__":
    main_wrapper(run)
