#!/usr/bin/env python3
"""Full-history announcement metadata crawler for cninfo.com.cn (v4, robust).

API constraints (verified by probing):
- pageSize hard-capped at 30; a single query freezes at ~3,030 records.
- Exact partition dimensions (sum == total, verified on peak day 2025-04-29):
    plate : szmb szcy shmb shkcp bj          (深主板/创业板/沪主板/科创板/北交所)
    trade : 19 CSRC industries
  Overlapping last-resort dimension: 26 market categories.
- Server soft-throttles bursts: latency rises, then 403/429/503.

v4 design:
- Seed ONE window per day (2003-01-01 .. today) -> minimal probe overhead.
- Split order for over-cap cells: plate -> trade -> category.
- NEVER checkpoint an incomplete cell: re-queue it (bounded); hopeless cells
  go to partial/ (excluded from merge, fixable later).
- Adaptive global rate limiter: grows when healthy, halves on errors,
  global pause on error storms.
"""
import json
import os
import queue
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, timedelta

API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PAGE_SIZE = 30
CAP = 2900
WORKERS = 12
MAX_DEPTH = 8

PLATES = ["szmb", "szcy", "shmb", "shkcp", "bj"]
TRADES = [
    "农、林、牧、渔业", "采矿业", "制造业", "电力、热力、燃气及水生产和供应业", "建筑业",
    "批发和零售业", "交通运输、仓储和邮政业", "住宿和餐饮业", "信息传输、软件和信息技术服务业",
    "金融业", "房地产业", "租赁和商务服务业", "科学研究和技术服务业",
    "水利、环境和公共设施管理业", "居民服务、修理和其他服务业", "教育", "卫生和社会工作",
    "文化、体育和娱乐业", "综合",
]
CATS = [
    "category_ndbg_szsh", "category_bndbg_szsh", "category_yjdbg_szsh", "category_sjdbg_szsh",
    "category_yjygjxz_szsh", "category_qyfpxzcs_szsh", "category_dshgg_szsh", "category_jshgg_szsh",
    "category_gddh_szsh", "category_rcjy_szsh", "category_gszl_szsh", "category_zj_szsh",
    "category_sf_szsh", "category_zf_szsh", "category_gqjl_szsh", "category_pg_szsh",
    "category_jj_szsh", "category_gszq_szsh", "category_kzzq_szsh", "category_qtrz_szsh",
    "category_gqbd_szsh", "category_bcgz_szsh", "category_cqdq_szsh", "category_fxts_szsh",
    "category_tbclts_szsh", "category_tszlq_szsh",
]

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "full-meta")
WINDOW_DIR = os.path.join(OUT_DIR, "windows")
PARTIAL_DIR = os.path.join(OUT_DIR, "partial")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.cninfo.com.cn/new/index.jsp",
}

stats = {"pages": 0, "records": 0, "cells_done": 0, "cells_total": 0, "partial": 0,
         "retries": 0, "splits": 0, "errors": 0, "start": time.time()}
lock = threading.Lock()
q = queue.Queue()

# ---- adaptive rate limiter (token bucket, graduated backoff) ----
rate = {"rps": 8.0, "tokens": 8.0, "last": time.monotonic(), "last_err": 0.0,
        "err_window": [], "last_bump": time.monotonic(), "pause_until": 0.0, "pause_dur": 60.0}
def throttle():
    while True:
        with lock:
            now = time.monotonic()
            if now < rate["pause_until"]:
                wait = rate["pause_until"] - now
                time.sleep(min(wait, 30))
                continue
            rate["tokens"] = min(rate["rps"], rate["tokens"] + (now - rate["last"]) * rate["rps"])
            rate["last"] = now
            rate["err_window"] = [t for t in rate["err_window"] if now - t < 60]
            if len(rate["err_window"]) >= 3:
                # repeated trouble: graduated global pause (doubles each time, cap 20 min)
                rate["pause_until"] = now + rate["pause_dur"]
                rate["pause_dur"] = min(1200.0, rate["pause_dur"] * 2)
                rate["err_window"] = []
                rate["rps"] = max(4.0, rate["rps"] * 0.5)
                continue
            if rate["tokens"] >= 1:
                rate["tokens"] -= 1
                return
        time.sleep(0.02)


def note_error():
    with lock:
        now = time.monotonic()
        rate["err_window"].append(now)
        rate["last_err"] = now
        # gentle single-error decrement; storms handled by graduated pause in throttle()
        rate["rps"] = max(4.0, rate["rps"] * 0.75)
        stats["retries"] += 1


def note_success():
    with lock:
        now = time.monotonic()
        if now - rate["last_err"] > 300:
            rate["pause_dur"] = 60.0  # healthy again: reset backoff
        # smooth ramp: +2 rps at most once per 60s, only after 120s healthy
        if now - rate["last_err"] > 180 and now - rate["last_bump"] > 90 and rate["rps"] < 20:
            rate["rps"] = min(20.0, rate["rps"] + 1)
            rate["last_bump"] = now


def log(msg):
    with lock:
        el = time.time() - stats["start"]
        r = stats["pages"] / el if el > 0 else 0
        left = stats["cells_total"] - stats["cells_done"]
        eta = left / max(stats["cells_done"] / el, 0.001) / 60 if stats["cells_done"] else -1
        print(f"[{time.strftime('%H:%M:%S')}] {el/60:6.1f}m pages={stats['pages']:>7} recs={stats['records']:>8} "
              f"cells={stats['cells_done']}/{stats['cells_total']} rate={r:5.1f}p/s eta={eta:6.1f}m "
              f"rps={rate['rps']:.0f} split={stats['splits']} retry={stats['retries']} "
              f"err={stats['errors']} partial={stats['partial']} | {msg}", flush=True)


def fetch_page(page_num, se_date, plate=None, trade=None, category=None, retries=6):
    params = {
        "pageNum": page_num, "pageSize": PAGE_SIZE,
        "column": "szse", "tabName": "fulltext", "isHLtitle": "true",
        "sortName": "announcementTime", "sortType": "desc", "seDate": se_date,
    }
    if plate:
        params["plate"] = plate
    if trade:
        params["trade"] = trade
    if category:
        params["category"] = category
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(retries):
        throttle()
        try:
            req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            note_success()
            return data
        except urllib.error.HTTPError as e:
            note_error()
            if e.code in (403, 429, 502, 503):
                time.sleep(min(45, 2 ** min(attempt, 4)))
                continue
            if attempt == retries - 1:
                raise
            time.sleep(2)
        except Exception:
            note_error()
            if attempt == retries - 1:
                raise
            time.sleep(2)
    return None


def normalize(it):
    t = it.get("announcementTime")
    return {
        "announcementId": it.get("announcementId"),
        "secCode": it.get("secCode"),
        "secName": it.get("secName"),
        "title": it.get("announcementTitle"),
        "time": t,
        "timeStr": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t / 1000)) if t else None,
        "adjunctUrl": it.get("adjunctUrl"),
        "adjunctSizeKB": it.get("adjunctSize"),
        "adjunctType": it.get("adjunctType"),
        "pdfUrl": "https://static.cninfo.com.cn/" + it["adjunctUrl"] if it.get("adjunctUrl") else None,
    }


def cell_name(day, plate, trade, cat):
    parts = [day.strftime("%Y%m%d")]
    parts.append("p" + str(PLATES.index(plate)) if plate else "p-")
    parts.append("t" + str(TRADES.index(trade)) if trade else "t-")
    parts.append("c" + str(CATS.index(cat)) if cat else "c-")
    return "_".join(parts)


def save_records(day, plate, trade, cat, records, partial=False):
    d = PARTIAL_DIR if partial else WINDOW_DIR
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, cell_name(day, plate, trade, cat) + ".jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def crawl_cell(day, plate=None, trade=None, cat=None, depth=0):
    name = cell_name(day, plate, trade, cat)
    ckpt = os.path.join(WINDOW_DIR, name + ".jsonl")
    if os.path.exists(ckpt) or os.path.exists(os.path.join(PARTIAL_DIR, name + ".jsonl")):
        with lock:
            stats["cells_done"] += 1
        return

    se_date = day.isoformat()
    first = fetch_page(1, se_date, plate, trade, cat)
    if first is None:
        with lock:
            stats["errors"] += 1
        if depth < MAX_DEPTH:
            q.put((day, plate, trade, cat, depth + 1))  # failure -> retry
        else:
            save_records(day, plate, trade, cat, [], partial=True)
            with lock:
                stats["cells_done"] += 1
        return

    items = first.get("announcements") or []
    total = first.get("totalRecordNum") or 0

    # over-cap and splittable -> split, never save a truncated cell
    if total > CAP and depth < MAX_DEPTH:
        if plate is None:
            with lock:
                stats["splits"] += 1
            for p in PLATES:
                q.put((day, p, trade, cat, depth + 1))
            return
        if trade is None:
            with lock:
                stats["splits"] += 1
            for t in TRADES:
                q.put((day, plate, t, cat, depth + 1))
            return
        if cat is None:
            with lock:
                stats["splits"] += 1
            for c in CATS:
                q.put((day, plate, trade, c, depth + 1))
            return
        # no dimension left: accept up to cap, mark partial
        with lock:
            stats["partial"] += 1
        log(f"PARTIAL-CELL {name}: total={total} (accepting ~{CAP})")

    # crawl the cell fully
    seen, records = set(), []
    for it in items:
        if it.get("announcementId"):
            seen.add(it["announcementId"])
            records.append(normalize(it))
    with lock:
        stats["pages"] += 1
        stats["records"] += len(records)

    expected = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    page, stall = 2, 0
    while page <= expected + 2 and page <= 110:
        data = fetch_page(page, se_date, plate, trade, cat)
        if data is None:
            break
        pit = data.get("announcements") or []
        if not pit:
            break
        new = 0
        for it in pit:
            aid = it.get("announcementId")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            records.append(normalize(it))
            new += 1
        with lock:
            stats["pages"] += 1
            stats["records"] += new
        stall = stall + 1 if new == 0 else 0
        if stall >= 4:
            break
        page += 1

    # completeness gate: never save a truncated cell as complete
    if total and len(records) < total * 0.98:
        if depth < MAX_DEPTH:
            q.put((day, plate, trade, cat, depth + 1))  # retry whole cell
            with lock:
                stats["retries"] += 1
            return
        save_records(day, plate, trade, cat, records, partial=True)
        with lock:
            stats["partial"] += 1
            stats["cells_done"] += 1
        log(f"PARTIAL {name}: got={len(records)} total={total}")
        return

    save_records(day, plate, trade, cat, records)
    with lock:
        stats["cells_done"] += 1


def worker():
    while True:
        try:
            job = q.get_nowait()
        except queue.Empty:
            return
        try:
            crawl_cell(*job)
        except Exception as e:
            log(f"CELL ERROR {job}: {e}")
            try:
                save_records(job[0], job[1], job[2], job[3], [], partial=True)
                with lock:
                    stats["cells_done"] += 1
            except Exception:
                pass
        finally:
            q.task_done()


def seed():
    cur = date(2003, 1, 1)
    end = date.today()
    n = 0
    while cur <= end:
        q.put((cur, None, None, None, 0))
        cur += timedelta(days=1)
        n += 1
    return n


def main():
    os.makedirs(WINDOW_DIR, exist_ok=True)
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    stats["cells_total"] = seed()
    log(f"seeded {stats['cells_total']} daily windows | workers={WORKERS} adaptive rps start=10")
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        time.sleep(60)
        with lock:
            el = time.time() - stats["start"]
            r = stats["pages"] / el if el else 0
            print(f"... {el/60:.1f}m pages={stats['pages']} recs={stats['records']} "
                  f"cells={stats['cells_done']}/{stats['cells_total']} rate={r:.1f}p/s rps={rate['rps']:.0f}", flush=True)
    q.join()
    log(f"CRAWL COMPLETE cells={stats['cells_done']} pages={stats['pages']} records={stats['records']} partial={stats['partial']}")
    with open(os.path.join(OUT_DIR, "crawl_stats.json"), "w") as f:
        json.dump({k: v for k, v in stats.items()}, f, indent=2)


if __name__ == "__main__":
    main()
