"""Compute external_source_records rollup (2026-08-15 pm).

Nightly job: reads all graded rows from external_picks, groups by
(source × sport × market × window), computes hit rate + ROI, upserts.

Play_of_day can then read this table to weight external picks by their
actual track record instead of treating them all as equal-weight votes.

CLI
  python compute_external_source_records.py              # all sports
  python compute_external_source_records.py --sport MLB  # single sport
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

WINDOWS = [7, 30, 90, 9999]  # 9999 = lifetime


def american_to_decimal(am):
    try: am = int(am)
    except (TypeError, ValueError): return None
    if am >= 100:  return 1 + am / 100.0
    if am <= -100: return 1 + 100.0 / abs(am)
    return None


def pick_roi(result: str, odds_american) -> float:
    """1u flat stake ROI."""
    if result == 'Push': return 0.0
    if result == 'Loss': return -1.0
    if result == 'Win':
        d = american_to_decimal(odds_american)
        if d: return d - 1.0
        return 0.91  # default -110 assumption
    return 0.0


def run(sport_filter: str | None = None) -> None:
    print(f'=== external_source_records rollup ===')
    # Pull all graded external picks
    rows = []
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/external_picks?result=not.is.null'
            f'&select=source,sport,surface,odds_american,result,resolved_at,game_date'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        if r.status_code != 200: break
        chunk = r.json() or []
        rows += chunk
        if len(chunk) < 1000: break
    if sport_filter:
        rows = [r for r in rows if r.get('sport') == sport_filter]
    print(f'  graded external picks: {len(rows)}')

    today = date.today()

    # For each (source, sport, market, window) — compute
    groups = defaultdict(list)
    for r in rows:
        source = (r.get('source') or 'unknown').lower()
        sport = r.get('sport') or 'ALL'
        market = (r.get('surface') or 'ALL').lower()
        # Age of pick in days
        try:
            resolved_str = r.get('resolved_at') or r.get('game_date')
            if isinstance(resolved_str, str):
                if 'T' in resolved_str:
                    d = datetime.fromisoformat(resolved_str.replace('Z','+00:00')).date()
                else:
                    d = date.fromisoformat(resolved_str[:10])
            else:
                continue
            age_days = (today - d).days
        except (ValueError, TypeError):
            continue

        for win_days in WINDOWS:
            if win_days == 9999 or age_days <= win_days:
                # Also group by market='ALL' aggregate
                groups[(source, sport, market, win_days)].append(r)
                groups[(source, sport, 'ALL', win_days)].append(r)

    payloads = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for (source, sport, market, win_days), items in groups.items():
        w = sum(1 for x in items if x.get('result') == 'Win')
        l = sum(1 for x in items if x.get('result') == 'Loss')
        p = sum(1 for x in items if x.get('result') == 'Push')
        n = w + l + p
        n_graded = w + l  # exclude pushes from hit-rate math
        hit_rate = round(100 * w / n_graded, 2) if n_graded else None
        total_units = sum(pick_roi(x.get('result'), x.get('odds_american')) for x in items)
        roi_pct = round(100 * total_units / n, 2) if n else None
        payloads.append({
            'source': source, 'sport': sport, 'market': market,
            'window_days': win_days,
            'wins': w, 'losses': l, 'pushes': p, 'n_graded': n_graded,
            'hit_rate': hit_rate, 'roi_pct': roi_pct,
            'computed_at': now_iso,
        })

    print(f'  rollup rows to write: {len(payloads)}')
    written = 0
    for i in range(0, len(payloads), 500):
        chunk = payloads[i:i+500]
        pr = requests.post(
            f'{SB}/rest/v1/external_source_records'
            f'?on_conflict=source,sport,market,window_days',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:150]}')
    print(f'  ✓ wrote {written} rollup rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport')
    args = p.parse_args()
    run(sport_filter=args.sport)


if __name__ == '__main__':
    main()
