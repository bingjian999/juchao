#!/usr/bin/env python3
"""Content harvester: tail the metadata crawl's checkpoint files and convert
new announcements to Markdown via the local PageSift server (parallel work;
static.cninfo.com.cn is not rate-limited).

- Tails full-meta/windows/*.jsonl for new records.
- Skips announcements already converted (existing 600 + its own progress).
- Converts via PageSift /v1/scrape -> markdown/<date>_<code>_<id>.md
- Polite: 2 workers, ~0.5s per announcement.
- Cap: MAX_NEW announcements (default 4000), then exits.
"""
import glob
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.dirname(HERE)
WINDOW_DIR = os.path.join(DATA, "full-meta", "windows")
MD_DIR = os.path.join(DATA, "markdown")
STATE = os.path.join(DATA, "full-meta", "harvester_state.json")
PAGESIFT = "http://localhost:3002/v1/scrape"
API_KEY = "sk-test"
MAX_NEW = int(os.environ.get("HARVEST_MAX", "4000"))
WORKERS = 2

state_lock = threading.Lock()
state = {"done": set(), "queued": set(), "converted": 0, "failed": 0}


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        state["done"] = set(d.get("done", []))
        state["converted"] = d.get("converted", 0)
        state["failed"] = d.get("failed", 0)
    # existing markdown files count as done
    for fp in glob.glob(os.path.join(MD_DIR, "*.md")):
        base = os.path.basename(fp)[:-3]
        parts = base.split("_")
        if parts:
            state["done"].add(parts[-1])


def save_state():
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(state["done"]), "converted": state["converted"],
                   "failed": state["failed"]}, f)


def scrape_markdown(url, retries=4):
    body = json.dumps({"url": url, "formats": ["markdown"], "timeout": 120000}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(PAGESIFT, data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {API_KEY}"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") and data.get("data", {}).get("markdown"):
                return data["data"]["markdown"]
            return None  # empty markdown (scanned PDF etc.)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(3)
    return None


def convert(rec):
    aid = rec["announcementId"]
    date = (rec.get("timeStr") or "unknown")[:10]
    name = f"{date}_{rec.get('secCode') or 'NA'}_{aid}.md"
    path = os.path.join(MD_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with state_lock:
            state["done"].add(aid)
        return
    md = scrape_markdown(rec["pdfUrl"])
    if md is None:
        with state_lock:
            state["failed"] += 1
            state["done"].add(aid)
        return
    header = (f"# {rec['title']}\n\n"
              f"- 股票代码: {rec.get('secCode')}\n"
              f"- 股票简称: {rec.get('secName')}\n"
              f"- 公告时间: {rec.get('timeStr')}\n"
              f"- 原文链接: {rec['pdfUrl']}\n\n---\n\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + md)
    with state_lock:
        state["done"].add(aid)
        state["converted"] += 1


def worker(jobs):
    while True:
        with state_lock:
            if not jobs:
                return
            rec = jobs.pop(0)
        try:
            convert(rec)
        except Exception as e:
            print(f"convert error {rec.get('announcementId')}: {e}", flush=True)
        time.sleep(0.4)


def main():
    os.makedirs(MD_DIR, exist_ok=True)
    load_state()
    print(f"harvester start: already done={len(state['done'])}, cap={MAX_NEW}", flush=True)
    offsets = {}  # file -> byte offset
    jobs = []
    last_log = time.time()
    while True:
        # scan checkpoint files for new lines
        new_recs = []
        for fp in sorted(glob.glob(os.path.join(WINDOW_DIR, "*.jsonl"))):
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            off = offsets.get(fp, 0)
            if size <= off:
                continue
            try:
                with open(fp, encoding="utf-8") as f:
                    f.seek(off)
                    chunk = f.read()
                    offsets[fp] = f.tell()
            except OSError:
                continue
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                aid = rec.get("announcementId")
                if not aid or aid in state["done"] or aid in state["queued"]:
                    continue
                if not rec.get("pdfUrl"):
                    continue
                state["queued"].add(aid)
                new_recs.append(rec)

        with state_lock:
            already = len(state["done"])
        room = MAX_NEW - (state["converted"] + state["failed"])
        if new_recs and room > 0:
            jobs.extend(new_recs[:room])
        if jobs:
            threads = [threading.Thread(target=worker, args=(jobs,), daemon=True) for _ in range(WORKERS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            save_state()
        if time.time() - last_log > 60:
            print(f"harvester: converted={state['converted']} failed={state['failed']} "
                  f"done_total={len(state['done'])} (cap {MAX_NEW})", flush=True)
            save_state()
            last_log = time.time()
        if state["converted"] + state["failed"] >= MAX_NEW:
            print(f"harvester reached cap: converted={state['converted']} failed={state['failed']}", flush=True)
            save_state()
            return
        time.sleep(20)


if __name__ == "__main__":
    main()
