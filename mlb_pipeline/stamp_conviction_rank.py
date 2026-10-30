"""Stamp `_conviction_rank` + `_conv_inputs` onto every primary_play.

══ 2026-10-30 · ANDY: "NBA and NHL conviction needs to be established so we
   can tune as we go... we need NBA takes and reads and some need conviction" ══

The ensemble's `conviction` is sport-blind:
    conviction = 50 + min(win_score*10, 30) + min(margin*20, 15)
which saturates at win_score = 3.0. Measured spans of the ensemble's own
score, per sport, on stored primary_play rows:

    NBA    p50 0.07  p90 0.11  max 0.20   -> conviction 51-53, every pick
    NHL    p50 0.17  p90 0.73  max 1.28   -> 59-78
    NFL    p50 0.21  p90 0.98  max 2.25   -> 55-86
    NCAAF  p50 0.43  p90 1.43  max 3.51   -> 61-100

NCAAF uses the whole range. NBA's MAXIMUM buys 2 of the available 30 points,
so no NBA pick can ever be ranked above another and there is nothing for
tuning to grip.

`_conviction_rank` normalises each sport's own measured spans onto one axis
(every sport's p90 -> 82.8), so NBA now spreads 55 to 97 and a decisive NBA
pick reads like a decisive NCAAF one.

WHY A SEPARATE FIELD, NOT A REWRITE OF `conviction`
`conviction` is consumed as a WIN PROBABILITY: model_edge.edge_pp() is
`conviction - implied_prob*100`, and that edge drives NO_EDGE_TIER_CAP, the
gate that demotes a pick to COVERAGE when the price is better than our number.
Rescaling NBA conviction from 51 to 75 would print a ~20pp edge on every NBA
pick, switch off the gate, and manufacture plays out of nothing. So the
probability keeps its scale and the ranking gets its own number.

WHY A POST-PASS AND NOT THE BUILDERS
Five builders write primary_play (game_context, nba_, nhl_, ncaaf_, nfl_, plus
three recompute_* scripts). One idempotent post-pass covers all of them,
cannot drift between them, and backfills history so weekly attribution has
data on day one — feedback_fix_every_writer_to_the_table.

USAGE
  python stamp_conviction_rank.py --dry-run
  python stamp_conviction_rank.py --sport NBA --days 4
  python stamp_conviction_rank.py --backfill 2026-10-01   # history too
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import conviction_scale as CS

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

CTX = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context',
    'NCAAB': 'ncaab_game_context',
}

FORMULA = 'conviction_scale.rank_from_signals/2026-10-30'


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _margin_from(pp: dict):
    """The ensemble records margin only inside audit_note. Parse it, and fall
    back to None rather than 0.0 — a missing margin is not a zero margin."""
    m = pp.get('margin')
    if isinstance(m, (int, float)):
        return float(m)
    note = str(pp.get('audit_note') or '')
    hit = re.search(r'margin=([+-]?\d+(?:\.\d+)?)', note)
    return float(hit.group(1)) if hit else None


def run(sports=None, days=8, dry_run=False, backfill_from=None):
    base = _et_today()
    if backfill_from:
        lo, hi = backfill_from, base
    else:
        lo = base
        hi = (datetime.strptime(base, '%Y-%m-%d')
              + timedelta(days=max(1, int(days)) - 1)).strftime('%Y-%m-%d')
    targets = sports or list(CTX)
    grand = {'seen': 0, 'stamped': 0, 'skipped': 0, 'failed': 0}
    for sport in targets:
        tbl = CTX.get(sport.upper())
        if not tbl:
            print(f'  ! unknown sport {sport}')
            continue
        rows, off = [], 0
        while True:
            r = requests.get(
                f'{SB}/rest/v1/{tbl}', headers=H_READ, timeout=90,
                params={'select': 'game_id,game_date,primary_play',
                        'game_date': [f'gte.{lo}', f'lte.{hi}'],
                        'order': 'game_date', 'limit': 1000, 'offset': off})
            if r.status_code != 200:
                print(f'  ! {sport} read HTTP {r.status_code} {r.text[:160]}')
                rows = []
                break
            batch = r.json()
            rows += batch
            if len(batch) < 1000:
                break
            off += 1000
        n_seen = n_ok = n_skip = n_bad = 0
        for row in rows:
            pp = row.get('primary_play')
            if not isinstance(pp, dict) or pp.get('type') in (None, 'pass'):
                continue
            n_seen += 1
            score = pp.get('score')
            margin = _margin_from(pp)
            if not isinstance(score, (int, float)) or margin is None:
                n_skip += 1
                continue
            rank = CS.rank_from_signals(sport, score, margin)
            if rank is None:
                n_skip += 1
                continue
            spans = CS.SIGNAL_SPANS.get(sport.upper())
            # Idempotent: same rank AND same formula version -> nothing to do.
            prior = pp.get('_conviction_rank')
            prior_f = (pp.get('_conv_inputs') or {}).get('formula') \
                if isinstance(pp.get('_conv_inputs'), dict) else None
            if prior == rank and prior_f == FORMULA:
                n_skip += 1
                continue
            new = dict(pp)
            new['_conviction_rank'] = rank
            new['_conv_inputs'] = {
                'win_score': round(float(score), 4),
                'margin': round(float(margin), 4),
                'sport': sport.upper(),
                'spans': list(spans) if spans else None,
                'formula': FORMULA,
            }
            if dry_run:
                n_ok += 1
                continue
            # Confirm by READBACK. A PATCH refused by a trigger returns 204
            # with zero rows affected, byte-identical to success
            # (feedback_pick_lock_refuses_with_a_silent_204).
            pr = requests.patch(
                f'{SB}/rest/v1/{tbl}', headers=H_WRITE, timeout=60,
                params={'game_id': f'eq.{row["game_id"]}',
                        'game_date': f'eq.{row["game_date"]}'},
                json={'primary_play': new})
            ok = False
            if pr.status_code in (200, 201):
                back = pr.json() or []
                ok = bool(back) and (
                    (back[0].get('primary_play') or {}).get('_conviction_rank') == rank)
            if ok:
                n_ok += 1
            else:
                n_bad += 1
                if n_bad <= 3:
                    print(f'    ! {sport} {row["game_id"]} PATCH '
                          f'HTTP {pr.status_code} not confirmed by readback')
        tag = ' [DRY]' if dry_run else ''
        print(f'  {sport:6s} picks {n_seen:4d}  stamped {n_ok:4d}  '
              f'unchanged/no-signals {n_skip:4d}  failed {n_bad:3d}{tag}')
        grand['seen'] += n_seen
        grand['stamped'] += n_ok
        grand['skipped'] += n_skip
        grand['failed'] += n_bad
    print(f'\n=== conviction_rank: {grand["stamped"]} stamped, '
          f'{grand["skipped"]} unchanged, {grand["failed"]} failed, '
          f'of {grand["seen"]} picks {lo}..{hi} ===')
    return grand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append',
                    help='repeatable; default every sport')
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--backfill', dest='backfill_from',
                    help='YYYY-MM-DD; stamp history from this date to today')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    print(f'=== stamp_conviction_rank · {_et_today()} ===')
    run(sports=a.sport, days=a.days, dry_run=a.dry_run,
        backfill_from=a.backfill_from)


if __name__ == '__main__':
    main()
