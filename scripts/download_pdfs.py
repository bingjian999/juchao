#!/usr/bin/env python3
"""Download announcement PDFs from static.cninfo.com.cn."""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # parent of scripts/
PDF_DIR = os.path.join(DATA, "pdf")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "https://www.cninfo.com.cn/new/index.jsp"}

def download(rec):
    aid = rec["announcementId"]
    url = rec["pdfUrl"]
    if not url:
        return (aid, "no-url", False)
    # filename: <date>_<secCode>_<announcementId>.pdf
    date = (rec.get("timeStr") or "unknown")[:10]
    name = f"{date}_{rec.get('secCode') or 'NA'}_{aid}.pdf"
    path = os.path.join(PDF_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return (aid, name, True)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 100 or not data.startswith(b"%PDF"):
                return (aid, name, False)
            with open(path, "wb") as f:
                f.write(data)
            return (aid, name, True)
        except Exception as e:
            if attempt == 2:
                return (aid, f"{type(e).__name__}: {e}", False)
            time.sleep(2)

def main():
    os.makedirs(PDF_DIR, exist_ok=True)
    with open(os.path.join(DATA, "announcements.json"), encoding="utf-8") as f:
        records = json.load(f)["announcements"]
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(download, r): r for r in records}
        for i, fut in enumerate(as_completed(futs), 1):
            aid, info, success = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
                print(f"FAILED {aid}: {info}", flush=True)
            if i % 50 == 0:
                print(f"progress: {i}/{len(records)} (ok={ok}, fail={fail})", flush=True)
    print(f"DONE: ok={ok}, fail={fail}, pdf dir: {PDF_DIR}", flush=True)

if __name__ == "__main__":
    main()
