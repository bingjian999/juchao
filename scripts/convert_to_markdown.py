#!/usr/bin/env python3
"""Convert announcement PDFs to Markdown via the local PageSift server (localhost:3002)."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # parent of scripts/
MD_DIR = os.path.join(DATA, "markdown")
PAGESIFT = "http://localhost:3002/v1/scrape"
API_KEY = "sk-test"

def scrape_markdown(url: str, retries: int = 4):
    body = json.dumps({"url": url, "formats": ["markdown"], "timeout": 120000}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                PAGESIFT, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {API_KEY}"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") and data.get("data", {}).get("markdown"):
                return data["data"]["markdown"], None
            return None, f"api error: {str(data)[:200]}"
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(5 * (attempt + 1))  # rate limited / busy, back off
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            if attempt == retries - 1:
                return None, f"{type(e).__name__}: {e}"
            time.sleep(3)
    return None, "retries exhausted"

def convert(rec):
    aid = rec["announcementId"]
    date = (rec.get("timeStr") or "unknown")[:10]
    name = f"{date}_{rec.get('secCode') or 'NA'}_{aid}.md"
    path = os.path.join(MD_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return (aid, name, True, "cached")
    md, err = scrape_markdown(rec["pdfUrl"])
    if md is None:
        return (aid, name, False, err)
    header = (f"# {rec['title']}\n\n"
              f"- 股票代码: {rec.get('secCode')}\n"
              f"- 股票简称: {rec.get('secName')}\n"
              f"- 公告时间: {rec.get('timeStr')}\n"
              f"- 原文链接: {rec['pdfUrl']}\n\n---\n\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + md)
    return (aid, name, True, None)

def main():
    os.makedirs(MD_DIR, exist_ok=True)
    with open(os.path.join(DATA, "announcements.json"), encoding="utf-8") as f:
        records = json.load(f)["announcements"]
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(convert, r): r for r in records}
        for i, fut in enumerate(as_completed(futs), 1):
            aid, name, success, err = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
                print(f"FAILED {aid}: {err}", flush=True)
            if i % 25 == 0:
                print(f"progress: {i}/{len(records)} (ok={ok}, fail={fail})", flush=True)
    print(f"DONE: ok={ok}, fail={fail}, markdown dir: {MD_DIR}", flush=True)

if __name__ == "__main__":
    main()
