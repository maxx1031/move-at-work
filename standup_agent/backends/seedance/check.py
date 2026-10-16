"""Check fal.ai access for the Seedance endpoints (key valid, endpoint exists) without generating a video.

    ~/miniconda3/envs/standup/bin/python standup_agent/backends/seedance/check.py

Uses FAL_KEY from the environment or .env (never printed). Submits an intentionally invalid request (no image) to
each endpoint: a validation error (422) means the key works and the endpoint exists; 401/403 means a key problem;
404 means the endpoint id is wrong.
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import ENDPOINTS, load_key  # noqa: E402

load_key()
h = {"Authorization": f"Key {os.environ['FAL_KEY']}", "Content-Type": "application/json"}
for alias, ep in ENDPOINTS.items():
    r = requests.post(f"https://fal.run/{ep}", headers=h, json={"prompt": "access check"}, timeout=60)
    verdict = {422: "OK (key works, endpoint exists; validation error expected)", 401: "KEY REJECTED",
               403: "NO ACCESS", 404: "ENDPOINT NOT FOUND"}.get(r.status_code, r.text[:200])
    print(f"{alias:9s} {ep:45s} {r.status_code}  {verdict}")
