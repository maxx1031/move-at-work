"""Download archived ExRx exercise pages from the Wayback Machine (personal use only).

Reads the CDX listing produced by `select_targets` and fetches the raw original HTML
(`id_` snapshots, no Wayback toolbar) politely: one request every DELAY seconds,
honest User-Agent, resumable. Output: data/exrx/pages/<path>.html + manifest.jsonl.
"""
import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1] / "data" / "exrx"
PAGES = ROOT / "pages"
MANIFEST = ROOT / "manifest.jsonl"
UA = "move-at-work personal archive (ozgurk2@illinois.edu)"
DELAY = 2.5
SECTIONS = {"WeightExercises", "Stretches", "Plyometrics", "Aerobic", "Muscles", "Lists"}


def select_targets():
    """Latest modern-format (no .html) capture per page in the exercise-related sections."""
    rows = json.load(open(ROOT / "cdx_all.json"))[1:]
    best = {}
    for original, ts, _mime in rows:
        path = re.sub(r"^https?://(www\.)?exrx\.net(:\d+)?", "", original, flags=re.I)
        path = path.split("?")[0].split("#")[0].rstrip("/")
        parts = path.strip("/").split("/")
        if parts[0] not in SECTIONS or path.lower().endswith(".html"):
            continue
        if re.search(r"\.(js|css|gif|jpe?g|png|mp4|pdf)$", path, re.I):
            continue
        key = path.lower()
        if key not in best or ts > best[key][1]:
            best[key] = (path, ts)
    return sorted(best.values())


class RateLimiter:
    """Shared across workers: spaces request starts by `interval`; a 429 pauses everyone and slows down."""

    def __init__(self, interval):
        self.interval = interval
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        time.sleep(max(0.0, start - now))

    def throttled(self):
        with self.lock:
            self.interval = min(self.interval * 2, 10.0)
            self.next_at = time.monotonic() + 60
            print(f"429: pausing 60 s, interval now {self.interval:.1f} s", flush=True)


def fetch(session, limiter, path, ts):
    url = f"https://web.archive.org/web/{ts}id_/https://exrx.net{path}"
    for attempt in range(5):
        limiter.wait()
        try:
            r = session.get(url, timeout=60)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code == 200:
                return r.text, r.url
            err = f"HTTP {r.status_code}"
            if r.status_code == 404:
                break
            if r.status_code == 429:
                limiter.throttled()
                continue
        time.sleep(DELAY * 4 * (attempt + 1))
    raise RuntimeError(err)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--interval", type=float, default=DELAY, help="min seconds between request starts")
    args = ap.parse_args()

    targets = select_targets()
    done = set()
    if MANIFEST.exists():
        done = {json.loads(l)["path"] for l in MANIFEST.open() if json.loads(l).get("ok")}
    todo = [t for t in targets if t[0] not in done]
    print(f"{len(targets)} targets, {len(done)} done, {len(todo)} to fetch, "
          f"{args.workers} workers, {args.interval} s interval", flush=True)

    limiter = RateLimiter(args.interval)
    local = threading.local()
    lock = threading.Lock()
    count = [0]

    def work(item, mf):
        path, ts = item
        if not hasattr(local, "session"):
            local.session = requests.Session()
            local.session.headers["User-Agent"] = UA
        rec = {"path": path, "timestamp": ts}
        try:
            html, final_url = fetch(local.session, limiter, path, ts)
            out = PAGES / (path.strip("/") + ".html")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            rec.update(file=str(out.relative_to(ROOT)), wayback_url=final_url, ok=True)
        except RuntimeError as e:
            rec.update(ok=False, error=str(e))
        with lock:
            mf.write(json.dumps(rec) + "\n")
            mf.flush()
            count[0] += 1
            if count[0] % 50 == 0:
                print(f"{count[0]}/{len(todo)}", flush=True)

    with MANIFEST.open("a") as mf, ThreadPoolExecutor(args.workers) as pool:
        for item in todo:
            pool.submit(work, item, mf)
    print("done", flush=True)


if __name__ == "__main__":
    sys.exit(main())
