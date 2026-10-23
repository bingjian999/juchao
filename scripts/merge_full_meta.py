#!/usr/bin/env python3
"""Merge full-meta window checkpoints into the complete dataset.

- Global dedup by announcementId (category filters overlap slightly).
- Outputs:
    full-meta/announcements_full.jsonl        (complete, ~all fields)
    full-meta/csv/announcements_<year>.csv    (compact index, one per year)
    full-meta/merge_stats.json
"""
import csv
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "full-meta")
WINDOW_DIR = os.path.join(OUT_DIR, "windows")
CSV_DIR = os.path.join(OUT_DIR, "csv")


def main():
    t0 = time.time()
    seen = {}
    dup = 0
    files = sorted(glob.glob(os.path.join(WINDOW_DIR, "*.jsonl")))
    print(f"merging {len(files)} window files ...", flush=True)
    for i, fp in enumerate(files):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                aid = rec.get("announcementId")
                if not aid:
                    continue
                if aid in seen:
                    dup += 1
                    # keep the richer record (with pdfUrl)
                    if not seen[aid].get("pdfUrl") and rec.get("pdfUrl"):
                        seen[aid] = rec
                else:
                    seen[aid] = rec
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(files)} files, unique={len(seen)} ({time.time()-t0:.0f}s)", flush=True)

    records = sorted(seen.values(), key=lambda r: (r.get("time") or 0))
    print(f"merged: {len(records)} unique announcements (duplicates collapsed: {dup})", flush=True)

    # full JSONL
    full_path = os.path.join(OUT_DIR, "announcements_full.jsonl")
    with open(full_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {full_path} ({os.path.getsize(full_path)/1048576:.1f} MB)", flush=True)

    # compact CSV per year
    os.makedirs(CSV_DIR, exist_ok=True)
    by_year = {}
    for r in records:
        y = (r.get("timeStr") or "unknown")[:4]
        by_year.setdefault(y, []).append(r)
    for y in sorted(by_year):
        path = os.path.join(CSV_DIR, f"announcements_{y}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["announcement_id", "date", "time", "sec_code", "sec_name",
                        "title", "size_kb", "pdf_url"])
            for r in by_year[y]:
                w.writerow([r.get("announcementId"), (r.get("timeStr") or "")[:10],
                            r.get("timeStr"), r.get("secCode"), r.get("secName"),
                            r.get("title"), r.get("adjunctSizeKB"), r.get("pdfUrl")])
        print(f"  {os.path.basename(path)}: {len(by_year[y])} rows", flush=True)

    stats = {
        "uniqueAnnouncements": len(records),
        "duplicatesCollapsed": dup,
        "windowFiles": len(files),
        "byYear": {y: len(v) for y, v in sorted(by_year.items())},
        "mergedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(OUT_DIR, "merge_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print("merge_stats:", json.dumps(stats["byYear"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
