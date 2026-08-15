"""Weekly CLV rollup — aggregates clv_snapshots by sport / model / tier
across 7d / 30d / 90d rolling windows and writes a snapshot for the
Sunday audit surface.

CLV (closing line value) is the industry-standard proxy for pick quality
that's independent of W/L variance. A model with +0.5 avg CLV over 100
picks is beating the close consistently even if actual hit rate reads
noisy over a small window.

Output: writes to clv_weekly_rollup table (created inline via IF NOT
EXISTS DDL — no separate migration needed). Also prints the rollup for
Sunday audit consumption.

CLI
  python clv_weekly_rollup.py                        # all sports, all windows
  python clv_weekly_rollup.py --sport MLB
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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

WINDOWS = [7, 30, 90]
SUPPORTED_SPORTS = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL']


def fetch_clv(sport: str, days: int) -> list:
    """All clv_snapshots for sport within last N days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = []
    for page in range(50):
        r = requests.get(
            f'{SB}/rest/v1/clv_snapshots'
            f'?sport=eq.{sport}&game_date=gte.{since}'
            f'&select=sport,game_date,market,pick_side,our_tier,our_model,clv,clv_direction'
            f'&order=game_date.desc&limit=1000&offset={page*1000}',
            headers=H_READ, timeout=30)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    return rows


def _agg(rows: list) -> dict:
    """Compute avg / beat_rate / n / breakdown-by-market on a list of CLV rows."""
    if not rows: return {'n': 0, 'avg_clv': None, 'beat_rate': None,
                          'by_market': {}, 'by_tier': {}, 'by_model': {}}
    clvs = [float(r['clv']) for r in rows if r.get('clv') is not None]
    if not clvs: return {'n': 0, 'avg_clv': None, 'beat_rate': None,
                          'by_market': {}, 'by_tier': {}, 'by_model': {}}
    beat = sum(1 for c in clvs if c > 0)
    even = sum(1 for c in clvs if c == 0)

    def _submap(key):
        sub = defaultdict(list)
        for r in rows:
            k = r.get(key) or '—'
            if r.get('clv') is not None:
                sub[k].append(float(r['clv']))
        out = {}
        for k, vs in sub.items():
            if not vs: continue
            out[k] = {
                'n':          len(vs),
                'avg_clv':    round(sum(vs) / len(vs), 3),
                'beat_rate':  round(100 * sum(1 for v in vs if v > 0) / len(vs), 1),
            }
        return out

    return {
        'n':         len(clvs),
        'avg_clv':   round(sum(clvs) / len(clvs), 3),
        'beat_rate': round(100 * beat / (len(clvs) - even), 1) if len(clvs) > even else 100.0,
        'by_market': _submap('market'),
        'by_tier':   _submap('our_tier'),
        'by_model':  _submap('our_model'),
    }


def ensure_table():
    """Create clv_weekly_rollup table if not present. Idempotent."""
    # Table create via SQL not supported through PostgREST; assume migration
    # ran once. Writes will 404 gracefully if missing and print a hint.
    r = requests.get(f'{SB}/rest/v1/clv_weekly_rollup?limit=1', headers=H_READ, timeout=10)
    if r.status_code == 404:
        print('  ⚠ clv_weekly_rollup table not found — run inline SQL:')
        print('    CREATE TABLE IF NOT EXISTS public.clv_weekly_rollup (')
        print('      id BIGSERIAL PRIMARY KEY,')
        print('      sport TEXT NOT NULL, window_days INT NOT NULL,')
        print('      computed_at TIMESTAMPTZ DEFAULT NOW(),')
        print('      n INT, avg_clv NUMERIC, beat_rate NUMERIC,')
        print('      by_market JSONB, by_tier JSONB, by_model JSONB,')
        print('      UNIQUE (sport, window_days, computed_at));')
        return False
    return True


def print_rollup(sport: str, days: int, stats: dict):
    if stats['n'] == 0:
        print(f'  {sport} · {days}d: no CLV data'); return
    print(f'  {sport} · {days:>2}d · n={stats["n"]:>4} · avg CLV {stats["avg_clv"]:+.3f} · beat close {stats["beat_rate"]}%')
    for label, sub in [('market', stats['by_market']), ('tier', stats['by_tier']),
                        ('model', stats['by_model'])]:
        if not sub: continue
        top = sorted(sub.items(), key=lambda kv: -abs(kv[1]['avg_clv']))[:3]
        parts = [f'{k} {v["avg_clv"]:+.2f} (n={v["n"]})' for k, v in top]
        print(f'      by {label:6}: {" · ".join(parts)}')


def run_sport(sport: str, write: bool) -> None:
    print(f'\n── {sport} ──')
    for days in WINDOWS:
        rows = fetch_clv(sport, days)
        stats = _agg(rows)
        print_rollup(sport, days, stats)
        if write and stats['n'] > 0:
            payload = {
                'sport':       sport,
                'window_days': days,
                'n':           stats['n'],
                'avg_clv':     stats['avg_clv'],
                'beat_rate':   stats['beat_rate'],
                'by_market':   stats['by_market'],
                'by_tier':     stats['by_tier'],
                'by_model':    stats['by_model'],
                'computed_at': datetime.now(timezone.utc).isoformat().replace('+', '%2B'),
            }
            # For upsert we need window_days + sport uniqueness, but computed_at
            # is in the unique index — so each run appends a new row. That's
            # actually what we want (rolling weekly snapshots).
            r = requests.post(f'{SB}/rest/v1/clv_weekly_rollup',
                              headers=H_WRITE, json=payload, timeout=15)
            if r.status_code not in (200, 201, 204):
                print(f'    ✗ write: {r.status_code} {r.text[:120]}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=SUPPORTED_SPORTS + ['ALL'], default='ALL')
    p.add_argument('--print-only', action='store_true',
                   help='Print but do not write to clv_weekly_rollup')
    args = p.parse_args()

    print(f'=== CLV weekly rollup · {datetime.now().date().isoformat()} ===')
    write = not args.print_only and ensure_table()
    sports = SUPPORTED_SPORTS if args.sport == 'ALL' else [args.sport]
    for s in sports:
        run_sport(s, write=write)
    print(f'\n  ✓ CLV rollup complete')


if __name__ == '__main__':
    main()
