#!/usr/bin/env python3
"""Fetch announcements from cninfo.com.cn (hisAnnouncement/query API).

NOTE: The API's pageNum-based pagination is unstable when many announcements
share the same timestamp (live inserts shift OFFSET windows). Querying a fixed
past date range (seDate) is stable, so we partition by date range.
"""
import json
import time
import urllib.request
import urllib.parse

API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PAGE_SIZE = 30
TOTAL_PAGES = 20          # 20 * 30 = 600 announcements
SE_DATE = "2026-10-20~2026-10-21"  # stable past window (no live inserts)
OUT = "announcements.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.cninfo.com.cn/new/index.jsp",
}

def fetch_page(page_num: int) -> dict:
    body = urllib.parse.urlencode({
        "pageNum": page_num,
        "pageSize": PAGE_SIZE,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": SE_DATE,
        "sortName": "announcementTime",
        "sortType": "desc",
        "isHLtitle": "true",
    }).encode()
    req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    all_items = []
    total = None
    for page in range(1, TOTAL_PAGES + 1):
        for attempt in range(3):
            try:
                data = fetch_page(page)
                break
            except Exception as e:
                print(f"page {page} attempt {attempt+1} failed: {e}", flush=True)
                time.sleep(3)
        else:
            print(f"page {page} gave up", flush=True)
            continue
        total = data.get("totalRecordNum", total)
        items = data.get("announcements") or []
        all_items.extend(items)
        if page % 5 == 0 or page == 1:
            print(f"page {page}/{TOTAL_PAGES}: got {len(items)} items (cumulative {len(all_items)})", flush=True)
        if not items:
            break
        time.sleep(1.2)  # politeness

    # normalize + dedup by announcementId (safety net)
    records, seen = [], set()
    for it in all_items:
        aid = it.get("announcementId")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        records.append({
            "announcementId": aid,
            "secCode": it.get("secCode"),
            "secName": it.get("secName"),
            "title": it.get("announcementTitle"),
            "time": it.get("announcementTime"),
            "timeStr": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime((it.get("announcementTime") or 0) / 1000)) if it.get("announcementTime") else None,
            "adjunctUrl": it.get("adjunctUrl"),
            "adjunctSizeKB": it.get("adjunctSize"),
            "adjunctType": it.get("adjunctType"),
            "pdfUrl": "https://static.cninfo.com.cn/" + it["adjunctUrl"] if it.get("adjunctUrl") else None,
        })

    result = {
        "source": "https://www.cninfo.com.cn/new/index.jsp",
        "api": API,
        "dateRange": SE_DATE,
        "totalAvailableInRange": total,
        "fetchedCount": len(records),
        "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "announcements": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"DONE: {len(records)} unique announcements saved to {OUT} (range total: {total})", flush=True)

if __name__ == "__main__":
    main()
