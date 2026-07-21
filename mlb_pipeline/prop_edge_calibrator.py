"""Prop edge calibrator — nightly job.

Reads graded PRIME/STRONG props from a rolling 30-day window, buckets by
(tier, prop_type, direction), and categorizes each bucket as KEEP (>=60%
hit rate on n>=5), KILL (<45% on n>=5), or NEUTRAL. Writes results to
prop_edge_calibration so the prop scorer can apply filter/downgrade rules
before writing tiers.

Discovered 2026-07-05: STRONG tier headline hit rate (43% 5-day) hides
that STRONG bb_under is 74% while STRONG er_over is 41%. Bucket-level
filtering delivers +10.7pp lift on 33-day backtest.

CLI:
    python prop_edge_calibrator.py                # produce today's calibration
    python prop_edge_calibrator.py --dry-run      # print only, no write
    python prop_edge_calibrator.py --window 60    # different window
"""
import os
import sys
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
           "Prefer": "resolution=merge-duplicates,return=minimal"}

MIN_SAMPLE = 5              # ignore buckets with fewer graded picks than this
KEEP_THRESHOLD = 60.0       # bucket hit rate >= this → KEEP tier as published
KILL_THRESHOLD = 45.0       # bucket hit rate < this → downgrade to SKIP


def _conviction_band(conv):
    """Bucket a raw conviction score into named bands.

    2026-07-21 addition per project_ha_under_conviction_band_720:
    HA_UNDER 85+ hits 66.7%, 75-84 hits 45.8% — same PRIME badge, opposite EV.
    Same pattern likely holds across prop types; sub-band tracking exposes it.
    """
    if conv is None:
        return None
    try:
        c = float(conv)
    except (TypeError, ValueError):
        return None
    if c >= 85: return '85+'
    if c >= 75: return '75-84'
    if c >= 65: return '65-74'
    return '<65'


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def fetch_graded_props(start_date, end_date):
    """Pull graded PRIME/STRONG props with book lines in the window."""
    rows, off, page = [], 0, 1000
    while True:
        r = requests.get(
            f"{SB}/rest/v1/mlb_pipeline_props"
            f"?game_date=gte.{start_date}&game_date=lte.{end_date}"
            f"&tier=in.(PRIME,STRONG)"
            f"&book_line=not.is.null&result=not.is.null"
            f"&select=game_date,tier,prop_type,direction,result,conviction"
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


def bucket_by_direction(rows):
    """Return dict[(tier, prop_type, direction, conviction_band)] -> [wins, losses].

    2026-07-21 upgrade: keyed on conviction_band in addition to tier.
    Also writes 'ALL' band rows for backward compat.
    """
    buckets = defaultdict(lambda: [0, 0])
    for p in rows:
        r = p.get("result") or ""
        won = "Win" in r
        lost = "Loss" in r
        if not (won or lost):
            continue
        # Sub-band bucket
        band = _conviction_band(p.get("conviction"))
        if band:
            k = (p["tier"], p["prop_type"], p["direction"], band)
            if won: buckets[k][0] += 1
            else:   buckets[k][1] += 1
        # Aggregate 'ALL' bucket (backward compat)
        k_all = (p["tier"], p["prop_type"], p["direction"], 'ALL')
        if won: buckets[k_all][0] += 1
        else:   buckets[k_all][1] += 1
    return buckets


def categorize(hit_rate, sample_size):
    """Category rules. NEUTRAL if below sample threshold OR mid-range."""
    if sample_size < MIN_SAMPLE:
        return "NEUTRAL"
    if hit_rate >= KEEP_THRESHOLD:
        return "KEEP"
    if hit_rate < KILL_THRESHOLD:
        return "KILL"
    return "NEUTRAL"


def build_calibration(buckets, computed_at, window_days):
    rows = []
    for (tier, prop_type, direction, band), (w, l) in buckets.items():
        n = w + l
        pct = 100.0 * w / n if n else 0.0
        rows.append({
            "tier": tier,
            "prop_type": prop_type,
            "direction": direction,
            "conviction_band": band,
            "hit_rate": round(pct, 1),
            "sample_size": n,
            "category": categorize(pct, n),
            "computed_at": computed_at.isoformat(),
            "window_days": window_days,
        })
    return rows


def write_calibration(rows):
    if not rows:
        print("  Nothing to write.")
        return
    r = requests.post(
        f"{SB}/rest/v1/prop_edge_calibration",
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️  Write failed {r.status_code}: {r.text[:200]}")
    else:
        print(f"  ✅ Wrote {len(rows)} bucket rows for {rows[0]['computed_at']}")


def print_summary(rows):
    kept = [r for r in rows if r["category"] == "KEEP"]
    killed = [r for r in rows if r["category"] == "KILL"]
    neutral = [r for r in rows if r["category"] == "NEUTRAL"]
    def _fmt(r):
        return (f"    {r['tier']:>6} {r['prop_type']:>10} {r['direction']:>5} "
                f"[{r.get('conviction_band', 'ALL'):>5}]: {r['hit_rate']}% (n={r['sample_size']})")
    print(f"\n  KEEP ({len(kept)}):")
    for r in sorted(kept, key=lambda x: -x["hit_rate"]):
        print(_fmt(r))
    print(f"\n  KILL ({len(killed)}):")
    for r in sorted(killed, key=lambda x: x["hit_rate"]):
        print(_fmt(r))
    print(f"\n  NEUTRAL ({len(neutral)}):")
    for r in sorted(neutral, key=lambda x: -x["sample_size"])[:20]:
        print(_fmt(r))


def run(window_days, dry_run):
    today = _today_et()
    start = today - timedelta(days=window_days)
    label = " [DRY-RUN]" if dry_run else ""
    print(f"=== Prop edge calibrator {today}{label} ===")
    print(f"  Window: {start} → {today} ({window_days}d)")

    graded = fetch_graded_props(start, today)
    print(f"  Graded props in window: {len(graded)}")
    if not graded:
        print("  No data — abort.")
        return

    buckets = bucket_by_direction(graded)
    rows = build_calibration(buckets, today, window_days)
    print(f"  Bucket count: {len(rows)}")

    print_summary(rows)

    if dry_run:
        print("\n  DRY-RUN — no writes performed.")
        return
    write_calibration(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=30,
                    help="Rolling window days (default 30)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print only, do not write to DB")
    args = ap.parse_args()
    run(window_days=args.window, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
