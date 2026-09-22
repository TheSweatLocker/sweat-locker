"""backfill_game_grades — grade spread/total on results that now have lines.

Companion to backfill_historical_closing_odds. That script recovers the
closing line; this one turns it into spread_result / total_result, which is
what every signal validator, cohort builder and ATS tendency reads.

Both sports use the SAME rule, which is worth stating because they are
written differently in two places today:

    nhl_resolve_results._grade_spread:  (home - away) + puckline > 0
    ncaab_backfill_results:             (home - away) > -close_spread

Those are algebraically identical — "home covers if home margin plus the
home-perspective line is positive". One implementation here, so the two
cannot drift apart and quietly disagree about who covered.

SANITY BANDS. A spread outside the sport's plausible range is refused, not
graded. nhl_game_results.close_puckline held MONEYLINE prices (-290..+240)
on every historical row, and grading off those produced a 52.5%/47.5%
home/away split — an aggregate unremarkable enough that nobody would have
questioned it, while being fabricated on all 1,120 rows. A wrong grade is
worse than a missing one because it looks like evidence.

IDEMPOTENT: only fills a grade that is NULL, and only where the inputs are
present and plausible.

CLI
  python backfill_game_grades.py --sport NCAAB --dry-run
  python backfill_game_grades.py --sport NHL
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    Retry = None

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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

_S = requests.Session()
if Retry is not None:
    _S.mount('https://', HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=0.5,
                          status_forcelist=(500, 502, 503, 504, 429),
                          allowed_methods=frozenset(['GET', 'PATCH'])),
        pool_connections=8, pool_maxsize=8))

SPORTS = {
    # `scored_col` differs by sport — NCAAB counts points, NHL counts goals.
    # Hardcoding total_points 400'd the NHL read on column-does-not-exist,
    # which at least failed loudly rather than silently grading nothing.
    'NCAAB': {'results': 'ncaab_game_results', 'spread_col': 'close_spread',
              'total_col': 'close_total', 'scored_col': 'total_points',
              'spread_sane': 60.0, 'total_range': (100.0, 200.0)},
    'NHL':   {'results': 'nhl_game_results', 'spread_col': 'close_puckline',
              'total_col': 'close_total', 'scored_col': 'total_goals',
              'spread_sane': 3.0, 'total_range': (3.0, 10.0)},
}


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def grade_spread(home: int, away: int, line: Optional[float]) -> Optional[str]:
    """`line` is the HOME-perspective handicap (negative = home favoured)."""
    if line is None:
        return None
    m = (home - away) + float(line)
    if m > 1e-9:
        return 'home_covered'
    if m < -1e-9:
        return 'away_covered'
    return 'push'


def grade_total(home: int, away: int, total: Optional[float]) -> Optional[str]:
    if total is None:
        return None
    t = home + away
    if t > float(total):
        return 'over'
    if t < float(total):
        return 'under'
    return 'push'


def run(sport: str, dry_run: bool) -> int:
    cfg = SPORTS[sport]
    rows = []
    for off in range(0, 40000, 1000):
        r = _S.get(f'{SB}/rest/v1/{cfg["results"]}', headers=H_READ, timeout=40,
                   params={'select': f'game_id,home_score,away_score,'
                                     f'{cfg["spread_col"]},{cfg["total_col"]},'
                                     f'spread_result,total_result,{cfg["scored_col"]}',
                           'order': 'game_date.asc', 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            print(f'  ⚠ read {r.status_code}: {r.text[:140]}'); break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        rows += chunk
        if len(chunk) < 1000:
            break

    print(f'=== backfill_game_grades · {sport} {"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  {cfg["results"]} rows: {len(rows)}')

    patches, skip, mix = [], Counter(), Counter()
    for r in rows:
        hs, as_ = r.get('home_score'), r.get('away_score')
        if hs is None or as_ is None:
            skip['no_final_score'] += 1
            continue
        hs, as_ = int(hs), int(as_)
        upd = {}

        if r.get('spread_result') is None:
            sp = _f(r.get(cfg['spread_col']))
            if sp is None:
                skip['no_line'] += 1
            elif abs(sp) > cfg['spread_sane']:
                # Not a spread. This is the guard that catches a moneyline
                # sitting in a spread column.
                skip['line_implausible'] += 1
            else:
                g = grade_spread(hs, as_, sp)
                if g:
                    upd['spread_result'] = g
                    mix[g] += 1

        if r.get('total_result') is None:
            tot = _f(r.get(cfg['total_col']))
            lo, hi = cfg['total_range']
            if tot is None:
                skip['no_total'] += 1
            elif not (lo <= tot <= hi):
                skip['total_implausible'] += 1
            else:
                g = grade_total(hs, as_, tot)
                if g:
                    upd['total_result'] = g
                    mix[g] += 1

        if r.get(cfg['scored_col']) is None:
            upd[cfg['scored_col']] = hs + as_

        if upd:
            patches.append((r['game_id'], upd))

    print(f'  gradeable rows: {len(patches)}')
    if skip:
        print(f'  skipped: {dict(skip)}')
    if mix:
        print(f'  outcome mix: {dict(mix)}')
    if not patches or dry_run:
        if dry_run:
            for gid, upd in patches[:5]:
                print(f'    [DRY] {gid} -> {upd}')
            print(f'  [DRY] would patch {len(patches)}')
        return len(patches)

    ok = fail = 0
    for gid, upd in patches:
        r = _S.patch(f'{SB}/rest/v1/{cfg["results"]}',
                     params={'game_id': f'eq.{gid}'}, headers=H_WRITE,
                     json=upd, timeout=30)
        if r.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  ⚠ patch {gid}: {r.status_code} {r.text[:120]}')
    print(f'  patched {ok}, failed {fail}')
    if ok < len(patches):
        print(f'  ⚠ INCOMPLETE — {len(patches) - ok} unwritten. Re-run to finish.')
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORTS.keys()), required=True)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    run(a.sport, a.dry_run)


if __name__ == '__main__':
    main()
