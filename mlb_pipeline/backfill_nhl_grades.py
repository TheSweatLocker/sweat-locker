"""backfill_nhl_grades — grade historical NHL totals (sides are blocked).

nhl_game_results holds 1,382 rows with 1,349 final scores and 1,153
closing pucklines/totals, and `spread_result` / `total_result` were
populated on ZERO of them. NHL sides and totals have never been graded.

WHY. nhl_resolve_results does compute both, correctly — but it reads the
closing lines out of nhl_game_context, one date at a time, defaulting to
yesterday. nhl_game_context has 59 rows, all recent. So for any historical
game there was no context row, therefore no line, therefore no grade. The
results table was carrying the lines the whole time; nobody looked there.

WHAT IT BLOCKED. Every NHL ATS/OU tendency (backfill_nhl_team_tendencies
returned None for every team), the L10 form columns, per-side cohort work,
and the receipts grader — which keys off spread_result / total_result.

SIDES ARE NOT GRADED, and that is deliberate. close_puckline is
contaminated: an NHL puck line is always +/-1.5, but the column holds
MONEYLINE prices on every historical row (range -290..+240; only 33 of
1,153 rows have |value| <= 3, and those are the ones the new odds puller
wrote). Grading a spread off a moneyline yields an aggregate that looks
perfectly reasonable — 52.5% home_covered / 47.5% away — while being
fabricated on every single row, and it would then feed NHL ATS tendencies,
cohorts and the receipts grader. So the spread is refused and counted, and
the contamination reported loudly. Fixing it needs a re-pull of historical
pucklines, which is a separate job.

Totals are unaffected: close_total is clean on all 1,153 rows (5.5-7.0,
correct NHL range).

Reuses nhl_resolve_results._grade_spread / _grade_total verbatim rather
than reimplementing them. Two copies of a grading rule is how the two
copies drift, and a silent disagreement about who covered is worse than
no grade at all.

IDEMPOTENT: only touches rows where the grade IS NULL and the inputs exist.

CLI
  python backfill_nhl_grades.py --dry-run
  python backfill_nhl_grades.py
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from pathlib import Path

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

# Hundreds of per-row PATCHes on a fresh socket each is how two earlier
# backfills today died mid-run having written nothing while exiting 0.
_S = requests.Session()
if Retry is not None:
    _S.mount('https://', HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=0.5,
                          status_forcelist=(500, 502, 503, 504, 429),
                          allowed_methods=frozenset(['GET', 'PATCH'])),
        pool_connections=8, pool_maxsize=8))

from nhl_resolve_results import _grade_spread, _grade_total


def fetch_rows() -> list:
    out = []
    for off in range(0, 20000, 1000):
        r = _S.get(f'{SB}/rest/v1/nhl_game_results', headers=H_READ, timeout=40,
                   params={'select': 'game_id,game_date,home_score,away_score,'
                                     'close_puckline,close_total,spread_result,'
                                     'total_result,total_goals',
                           'order': 'game_date.asc',
                           'limit': 1000, 'offset': off})
        if r.status_code != 200:
            print(f'  ⚠ read {r.status_code}: {r.text[:150]}')
            break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        out += chunk
        if len(chunk) < 1000:
            break
    return out


def run(dry_run: bool = False) -> int:
    rows = fetch_rows()
    print(f'=== backfill_nhl_grades {"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  nhl_game_results rows: {len(rows)}')

    patches = []
    skip = Counter()
    for r in rows:
        hs, as_ = r.get('home_score'), r.get('away_score')
        if hs is None or as_ is None:
            skip['no_final_score'] += 1
            continue
        upd = {}
        if r.get('spread_result') is None:
            pk = r.get('close_puckline')
            # close_puckline is CONTAMINATED on historical rows. An NHL puck
            # line is always +/-1.5, but this column holds MONEYLINE prices
            # for everything the old backfill wrote: measured range -290 to
            # +240, and only 33 of 1,153 rows have |value| <= 3 (those are
            # the ones the new odds puller wrote).
            #
            # Grading a spread off a moneyline produces a result that looks
            # entirely reasonable in aggregate — 52.5% home_covered, 47.5%
            # away — while being fabricated on every single row. That would
            # then feed NHL ATS tendencies, cohorts and the receipts grader.
            # A wrong grade is far worse than a missing one, so refuse and
            # count it rather than guess.
            if pk is not None and abs(float(pk)) > 3:
                skip['puckline_is_moneyline'] += 1
            else:
                g = _grade_spread(int(hs), int(as_), pk)
                if g:
                    upd['spread_result'] = g
                else:
                    skip['no_puckline'] += 1
        if r.get('total_result') is None:
            g = _grade_total(int(hs), int(as_), r.get('close_total'))
            if g:
                upd['total_result'] = g
            else:
                skip['no_close_total'] += 1
        # total_goals is the denominator for every OU cohort and is free here.
        if r.get('total_goals') is None:
            upd['total_goals'] = int(hs) + int(as_)
        if upd:
            patches.append((r['game_id'], upd))

    print(f'  gradeable rows: {len(patches)}')
    if skip:
        print(f'  skipped: {dict(skip)}')
    if skip.get('puckline_is_moneyline'):
        print(f"  🚨 {skip['puckline_is_moneyline']} rows have MONEYLINE values in "
              f"close_puckline — spread left ungraded on those. The historical "
              f"NHL backfill wrote the wrong field; needs a separate re-pull.")
    if not patches:
        return 0
    if dry_run:
        for gid, upd in patches[:5]:
            print(f'    [DRY] {gid} -> {upd}')
        print(f'  [DRY] would patch {len(patches)}')
        return len(patches)

    ok = fail = 0
    tally = Counter()
    for gid, upd in patches:
        r = _S.patch(f'{SB}/rest/v1/nhl_game_results',
                     params={'game_id': f'eq.{gid}'}, headers=H_WRITE,
                     json=upd, timeout=25)
        if r.status_code in (200, 204):
            ok += 1
            if 'spread_result' in upd: tally[upd['spread_result']] += 1
            if 'total_result' in upd: tally[upd['total_result']] += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  ⚠ patch {gid}: {r.status_code} {r.text[:120]}')
    print(f'  patched {ok}, failed {fail}')
    print(f'  outcome mix: {dict(tally)}')
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    run(dry_run=a.dry_run)


if __name__ == '__main__':
    main()
