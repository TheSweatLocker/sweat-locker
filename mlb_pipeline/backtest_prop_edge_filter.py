"""Weekly backtest — validate the current allow-list against a rolling holdout.

Trains an allow-list on the training window (default 30d), applies it
to the following test window (default 7d), and compares raw vs filtered
hit rate + volume. Writes to prop_edge_backtest_history so we can track
calibration drift week over week.

Companion to prop_edge_calibrator.py. Same bucketing rules.

CLI:
    python backtest_prop_edge_filter.py               # train 30, test 7 ending today
    python backtest_prop_edge_filter.py --train 60 --test 14
    python backtest_prop_edge_filter.py --dry-run
"""
import os
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json",
           "Prefer": "return=minimal"}

MIN_SAMPLE = 5
KEEP_THRESHOLD = 60.0


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def fetch_props(start_date, end_date):
    rows, off, page = [], 0, 1000
    while True:
        r = requests.get(
            f"{SB}/rest/v1/mlb_pipeline_props"
            f"?game_date=gte.{start_date}&game_date=lte.{end_date}"
            f"&tier=in.(PRIME,STRONG)"
            f"&book_line=not.is.null&result=not.is.null"
            f"&select=game_date,tier,prop_type,direction,result"
            f"&limit={page}&offset={off}",
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < page:
            break
        off += page
    return rows


def train_keep_buckets(props):
    buckets = defaultdict(lambda: [0, 0])
    for p in props:
        k = (p["tier"], p["prop_type"], p["direction"])
        r = p.get("result") or ""
        if "Win" in r:
            buckets[k][0] += 1
        elif "Loss" in r:
            buckets[k][1] += 1
    keep = set()
    for k, (w, l) in buckets.items():
        n = w + l
        if n < MIN_SAMPLE:
            continue
        if 100.0 * w / n >= KEEP_THRESHOLD:
            keep.add(k)
    return keep, buckets


def evaluate(test_props, keep_buckets):
    raw_w = raw_l = filt_w = filt_l = 0
    for p in test_props:
        r = p.get("result") or ""
        won = "Win" in r
        lost = "Loss" in r
        if won:
            raw_w += 1
        elif lost:
            raw_l += 1
        k = (p["tier"], p["prop_type"], p["direction"])
        if k in keep_buckets:
            if won:
                filt_w += 1
            elif lost:
                filt_l += 1
    return raw_w, raw_l, filt_w, filt_l


def write_history(row):
    r = requests.post(
        f"{SB}/rest/v1/prop_edge_backtest_history",
        headers=H_WRITE, json=[row], timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️  Write failed {r.status_code}: {r.text[:200]}")


def run(train_window, test_window, dry_run):
    today = _today_et()
    test_end = today
    test_start = test_end - timedelta(days=test_window)
    train_end = test_start - timedelta(days=1)
    train_start = train_end - timedelta(days=train_window - 1)

    label = " [DRY-RUN]" if dry_run else ""
    print(f"=== Backtest{label} ===")
    print(f"  Train: {train_start} → {train_end} ({train_window}d)")
    print(f"  Test:  {test_start} → {test_end} ({test_window}d)")

    train_props = fetch_props(train_start, train_end)
    test_props = fetch_props(test_start, test_end)
    print(f"  Train n={len(train_props)}  Test n={len(test_props)}")
    if not train_props or not test_props:
        print("  Insufficient data — abort.")
        return

    keep_buckets, train_bkts = train_keep_buckets(train_props)
    print(f"  Keep buckets: {len(keep_buckets)}")
    for k in sorted(keep_buckets):
        w, l = train_bkts[k]
        print(f"    KEEP {k[0]:>6} {k[1]:>10} {k[2]:>5}: "
              f"{100.0*w/(w+l):.0f}% (n={w+l})")

    raw_w, raw_l, filt_w, filt_l = evaluate(test_props, keep_buckets)
    raw_n = raw_w + raw_l
    filt_n = filt_w + filt_l
    raw_pct = 100.0 * raw_w / max(1, raw_n)
    filt_pct = 100.0 * filt_w / max(1, filt_n)
    delta = filt_pct - raw_pct

    print(f"\n  RAW baseline:  {raw_w}-{raw_l} ({raw_pct:.1f}%) n={raw_n}")
    print(f"  FILTERED:      {filt_w}-{filt_l} ({filt_pct:.1f}%) n={filt_n}")
    print(f"  Delta:         {delta:+.1f}pp   picks removed: {raw_n - filt_n}")

    if dry_run:
        print("\n  DRY-RUN — no write to prop_edge_backtest_history.")
        return

    write_history({
        "training_window_days": train_window,
        "test_window_days": test_window,
        "test_start": test_start.isoformat(),
        "test_end": test_end.isoformat(),
        "raw_hits": raw_w,
        "raw_losses": raw_l,
        "raw_hit_rate": round(raw_pct, 1),
        "filtered_hits": filt_w,
        "filtered_losses": filt_l,
        "filtered_hit_rate": round(filt_pct, 1),
        "keep_buckets_count": len(keep_buckets),
        "delta_pp": round(delta, 1),
    })
    print("  ✅ Wrote row to prop_edge_backtest_history")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=30,
                    help="Training window days (default 30)")
    ap.add_argument("--test", type=int, default=7,
                    help="Test/holdout window days (default 7)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print only, do not write to DB")
    args = ap.parse_args()
    run(train_window=args.train, test_window=args.test, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
