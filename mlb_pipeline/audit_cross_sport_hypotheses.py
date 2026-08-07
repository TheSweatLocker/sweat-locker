"""Audit cross-sport pattern hypotheses (2026-08-06).

Reports how MLB-proven fade patterns are performing after they were
hypothesized against a new sport (NFL, NCAAF, NCAAB, NBA, UFC).

Reads signals column from each sport's *_pipeline_props table, filters
to resolved rows tagged with 'cross_sport_hypothesis_...', groups by
(sport, pattern_name, prop_type_family) and prints:
  - n graded
  - hit rate (props with result=W after the flip)
  - status: HOLDING (>=55%), NEUTRAL (45-55%), FAILING (<45%)

FAILING patterns should be reviewed — likely means the structural
book-mispricing story doesn't transfer to that sport, and the
hypothesis should be retired (or replaced with a per-sport rule).

Once a bucket accumulates 30+ graded rows, prop_tier_calibration's
_refresh_from_live_data will auto-write a sport-specific FADE_COMBOS
entry which overrides the hypothesis path anyway — this audit surfaces
whether the hypothesis was directionally correct in the interim.

Run: python audit_cross_sport_hypotheses.py
Optional: --days N (default 90, look back window)
          --min-n M (default 5, minimum n to report)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().split("\n"):
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

# Multi-sport props table registry — extend as sports come online
SPORT_TABLES = {
    'NFL':   'nfl_pipeline_props',
    'NCAAF': 'ncaaf_pipeline_props',
    'NCAAB': 'ncaab_pipeline_props',
    'NBA':   'nba_pipeline_props',
    'UFC':   'ufc_pipeline_props',
}

# Match tags emitted by cross_sport_pattern_hypothesis()
# Format: cross_sport_hypothesis_<pattern_name>_<sport>_<pt>_<tier>_<dir>_<pct>pct_...
_PATTERN_RE = re.compile(
    r'cross_sport_hypothesis_(?P<pattern>[a-z_]+?)_'
    r'(?P<sport>NFL|NCAAF|NCAAB|NBA|UFC)_'
    r'(?P<pt>[a-z_]+?)_'
    r'(?P<tier>PRIME|STRONG|SKIP)_(?P<dir>over|under)_'
    r'(?P<pct>\d+)pct'
)


def fetch_resolved(table: str, days: int) -> list:
    """Pull rows with resolved_at within window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_rows = []
    offset = 0
    while True:
        r = requests.get(
            f"{SB}/rest/v1/{table}",
            headers={**H_READ, "Range": f"{offset}-{offset+999}"},
            params={
                "select": "prop_type,tier,direction,result,signals,resolved_at",
                "resolved_at": f"gte.{cutoff}",
                "result": "not.is.null",
                "order": "resolved_at.desc",
            },
            timeout=30,
        )
        if r.status_code not in (200, 206): return all_rows
        batch = r.json()
        if not batch: break
        all_rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return all_rows


def extract_tag(signals) -> str | None:
    """signals may be dict, list, or str — pull the first cross_sport tag."""
    if signals is None: return None
    if isinstance(signals, str):
        try: signals = json.loads(signals)
        except Exception: return signals if 'cross_sport_hypothesis' in signals else None
    if isinstance(signals, dict):
        for v in signals.values():
            if isinstance(v, str) and 'cross_sport_hypothesis' in v: return v
        # nested check for common signal wrappers
        tc = signals.get('_tier_calibration') or signals.get('calibration_reason')
        if isinstance(tc, str) and 'cross_sport_hypothesis' in tc: return tc
    if isinstance(signals, list):
        for item in signals:
            if isinstance(item, str) and 'cross_sport_hypothesis' in item: return item
    return None


def classify_result(result) -> str | None:
    """Return 'W' / 'L' / None for push/void/other."""
    if result is None: return None
    r = str(result).upper().strip()
    if r in ('W', 'WIN'): return 'W'
    if r in ('L', 'LOSS', 'LOST'): return 'L'
    return None  # push / void / cash-half / unknown


def status(hit_pct: float, n: int) -> str:
    if n < 5: return 'THIN'
    if hit_pct >= 55.0: return 'HOLDING'
    if hit_pct < 45.0:  return 'FAILING'
    return 'NEUTRAL'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--min-n', type=int, default=5)
    args = ap.parse_args()

    # (sport, pattern, prop_type_family) → [W count, N total]
    buckets = defaultdict(lambda: [0, 0])
    per_sport_totals = defaultdict(lambda: [0, 0])

    for sport, table in SPORT_TABLES.items():
        r = requests.head(f"{SB}/rest/v1/{table}", headers=H_READ, timeout=10)
        if r.status_code >= 400:
            print(f"  {sport:6s} {table:30s} not available (skipping)")
            continue
        rows = fetch_resolved(table, args.days)
        tagged = 0
        for row in rows:
            tag = extract_tag(row.get('signals'))
            if not tag: continue
            m = _PATTERN_RE.search(tag)
            if not m: continue
            outcome = classify_result(row.get('result'))
            if outcome is None: continue
            key = (sport, m.group('pattern'), m.group('pt'))
            buckets[key][1] += 1
            per_sport_totals[sport][1] += 1
            if outcome == 'W':
                buckets[key][0] += 1
                per_sport_totals[sport][0] += 1
            tagged += 1
        print(f"  {sport:6s} {table:30s} rows={len(rows):4d} tagged={tagged}")

    print()
    print("=" * 80)
    print(f"CROSS-SPORT HYPOTHESIS AUDIT — last {args.days}d, min n={args.min_n}")
    print("=" * 80)

    if not buckets:
        print()
        print("  No cross-sport hypothesis rows found yet.")
        print("  Hypotheses activate when non-MLB props run through")
        print("  apply_calibration() and get graded. Check back after")
        print("  first NFL/NCAAF/NBA/etc. slates have resolved.")
        return

    # Sort by status severity (FAILING first — needs attention)
    def sort_key(item):
        (sport, pattern, pt), (w, n) = item
        pct = 100.0 * w / n if n else 0.0
        st = status(pct, n)
        order = {'FAILING': 0, 'NEUTRAL': 1, 'HOLDING': 2, 'THIN': 3}.get(st, 4)
        return (order, -n)

    print()
    print(f"  {'SPORT':6s} {'PATTERN':30s} {'PROP':22s} {'N':>4s} {'HIT%':>6s} STATUS")
    print(f"  {'-'*6} {'-'*30} {'-'*22} {'-'*4} {'-'*6} {'-'*8}")

    for (sport, pattern, pt), (w, n) in sorted(buckets.items(), key=sort_key):
        if n < args.min_n: continue
        pct = 100.0 * w / n
        st = status(pct, n)
        flag = '⚠ ' if st == 'FAILING' else '✓ ' if st == 'HOLDING' else '· '
        pat_short = pattern.replace('fair_price_', 'fp_').replace('_scored_tier', '')
        print(f"  {sport:6s} {pat_short:30s} {pt:22s} {n:4d} {pct:5.1f}% {flag}{st}")

    print()
    print(f"  {'PER-SPORT TOTALS':50s}")
    for sport, (w, n) in sorted(per_sport_totals.items()):
        pct = 100.0 * w / n if n else 0.0
        st = status(pct, n)
        print(f"  {sport:6s} n={n:4d}  hit={pct:5.1f}%  {st}")

    # Actionable recommendations
    failing = [k for k, (w, n) in buckets.items()
               if n >= args.min_n and 100.0*w/n < 45.0]
    if failing:
        print()
        print("  ⚠ RECOMMENDATION: retire or replace these hypotheses (n>=min, hit<45%):")
        for sport, pattern, pt in failing[:10]:
            w, n = buckets[(sport, pattern, pt)]
            print(f"      {sport} · {pattern} · {pt}  ({w}/{n} = {100.0*w/n:.0f}%)")


if __name__ == '__main__':
    main()
