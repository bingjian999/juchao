#!/usr/bin/env python3
"""Watchdog: wait for the cninfo API rate-limit ban to lift, then launch the full crawl."""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import os

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CRAWLER = os.path.join(HERE, "fetch_full_meta.py")
PY = sys.executable
LOG = os.path.join(os.path.dirname(HERE), "full-meta", "watchdog.log")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def probe():
    body = urllib.parse.urlencode({
        "pageNum": 1, "pageSize": 30, "column": "szse", "tabName": "fulltext",
        "isHLtitle": "true", "sortName": "announcementTime", "sortType": "desc",
        "seDate": "2024-06-03~2024-06-03", "plate": "szmb",
    }).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/index.jsp",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status == 200 and "announcements" in data
    except urllib.error.HTTPError as e:
        log(f"probe HTTP {e.code}")
        return False
    except Exception as e:
        log(f"probe error {type(e).__name__}")
        return False


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log("watchdog started; waiting for API unban (probe every 120s, need 3 consecutive OK)")
    ok_streak = 0
    waited = 0
    while waited < 90 * 60:  # up to 90 minutes
        if probe():
            ok_streak += 1
            log(f"probe OK (streak {ok_streak}/3)")
            if ok_streak >= 3:
                log("API recovered - launching full crawl")
                subprocess.run([PY, CRAWLER])
                return
        else:
            ok_streak = 0
        time.sleep(120)
        waited += 120
    log("watchdog gave up after 90 minutes")


if __name__ == "__main__":
    main()
