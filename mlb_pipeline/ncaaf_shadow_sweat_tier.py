#!/usr/bin/env python3
"""Shadow variant: tier NCAAF sides off sweat_score instead of conviction.

Andy 2026-09-30: "Think we should do the shadow variant but also should do some
current cal to what we already know, we cant just leave them inverted."

The cap shipped separately (apply_ncaaf_high_conviction_dog_cap). This is the
other half — a candidate tiering rule recorded WITHOUT going live, so it can be
judged on evidence rather than on today's thin sample.

WHY THIS VARIANT
Measured 2026-09-30 on leak-free graded NCAAF sides, the two scores the engine
already computes order outcomes very differently:

    ctx primary_play.conviction   r = -0.0991   (INVERTED, n=256)
    ctx sweat_score               r = +0.2004   (n=63 ML picks)

and by sweat_tier, on those ML picks:

    PRIME        10-2   83.3%   n=12   z=+2.31
    STRONG        8-5   61.5%   n=13
    LIGHT_LEAN   14-16  46.7%   n=30
    PASS          6-2   75.0%   n=8

sweat_score is 13x more correlated with winning than the conviction that
actually sets the published tier. That is a real hypothesis and a terrible thing
to act on today: n=12 in the headline band, the shape is not monotonic (0-50 also
wins at 75%), and the whole sample is 63 picks. Yesterday I nearly shipped a
"breakthrough" off a thin number and it was a leak; the discipline is to
instrument and wait.

HOW IT IS JUDGED — Andy's own promotion gate, already written into
shadow_v2_backtest.py on 09-17:

    picks_count >= 50 AND (shadow_hit_rate - live_hit_rate) >= 0.03
    sustained >= 4 weeks

So this writes the shadow, and shadow_v2_backtest.py --variant sweat_tier_v1
--sport NCAAF grades it against what was actually published. Nothing changes for
users until the gate is met.

WHAT THE VARIANT DOES
Keeps the live pick's market and side exactly as published — this tests TIERING,
not selection, so any difference in hit rate is attributable to the tier rule and
nothing else. Only the tier is recomputed, from sweat_score:

    sweat_score >= 80   PRIME
    sweat_score >= 70   STRONG
    sweat_score >= 60   LEAN
    else                COVERAGE

Thresholds come from the band edges measured above, not from fitting: 80+ is the
band that cleared 2 SE, 60 is where the sample stops being a coin flip. Round
numbers on purpose — a fitted cut on n=63 would be noise with extra decimals.

It also carries the live tier in the blob so the harness can compare without
re-deriving it, and records sweat_score so a later pass can re-cut the thresholds
against a bigger sample without regenerating anything.

NOT WIRED TO ANYTHING USER-FACING. primary_play_shadow_v2 is read only by
shadow_v2_backtest.py. The column has existed since 09-17 and was populated
0 of 422 rows — this is the first thing to write it.

USAGE
    python ncaaf_shadow_sweat_tier.py --dry-run
    python ncaaf_shadow_sweat_tier.py                  # upcoming + recent
    python ncaaf_shadow_sweat_tier.py --backfill       # whole season
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

VARIANT = 'sweat_tier_v1'

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    print('ncaaf_shadow_sweat_tier: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json'}

SIDE_MARKETS = ('ml', 'rl', 'spread')


def tier_from_sweat(score):
    """Band edges from the measured sweat_score buckets, deliberately round."""
    if score is None:
        return None
    if score >= 80:
        return 'PRIME'
    if score >= 70:
        return 'STRONG'
    if score >= 60:
        return 'LEAN'
    return 'COVERAGE'


def _page(tbl, select, extra=None):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        if extra:
            p.update(extra)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=p, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list) or not body:
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', action='store_true',
                    help='whole season instead of the recent window')
    ap.add_argument('--days-back', type=int, default=21)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    extra = {'season': 'eq.2026'}
    if not a.backfill:
        extra['game_date'] = f'gte.{(today - timedelta(days=a.days_back))}'

    rows = _page('ncaaf_game_context',
                 'game_id,game_date,away_team,home_team,sweat_score,sweat_tier,'
                 'primary_play', extra)
    print(f'=== ncaaf_shadow_sweat_tier · variant={VARIANT} ===')
    print(f'  ctx rows in scope: {len(rows)}')

    payload, agree, differ = [], 0, 0
    moves = collections.Counter()
    now_iso = datetime.now(timezone.utc).isoformat()

    for c in rows:
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:
                pp = None
        if not isinstance(pp, dict):
            continue
        ptype = str(pp.get('type') or '').lower()
        if ptype not in SIDE_MARKETS:
            continue          # tiering hypothesis is about sides
        try:
            score = float(c.get('sweat_score'))
        except (TypeError, ValueError):
            continue
        shadow_tier = tier_from_sweat(score)
        if shadow_tier is None:
            continue
        live_tier = str(pp.get('tier') or '').upper()
        if shadow_tier == live_tier:
            agree += 1
        else:
            differ += 1
            moves[f'{live_tier} -> {shadow_tier}'] += 1

        payload.append({
            'game_id': c['game_id'],
            'primary_play_shadow_v2': {
                'variant': VARIANT,
                # Side and market copied from live ON PURPOSE — this isolates
                # the tier rule, so any hit-rate delta is the tier and nothing
                # else.
                'type': ptype,
                'side': pp.get('side'),
                'tier': shadow_tier,
                'conviction': pp.get('conviction'),
                'live_tier': live_tier,
                'sweat_score': score,
                'generated_at': now_iso,
            },
            'shadow_v2_generated_at': now_iso,
        })

    print(f'  side picks scored : {agree + differ}')
    print(f'  tier AGREES with live : {agree}')
    print(f'  tier DIFFERS          : {differ}')
    for k, v in moves.most_common():
        print(f'      {k:24s} {v}')

    if a.dry_run:
        print('\n  (dry run — nothing written)')
        return 0
    if not payload:
        print('\n  nothing to write')
        return 0

    # PATCH per row, not a batched upsert. An on_conflict=game_id upsert was
    # tried first and failed with 23502: PostgREST issues INSERT .. ON CONFLICT,
    # the conflict target did not match a unique constraint, so it attempted a
    # real INSERT and tripped NOT NULL on columns this payload has no business
    # supplying (game_date, season_type, ...). PATCH is an UPDATE and cannot
    # create a partial row, which is what we want for a shadow column.
    wrote = failed = 0
    for row in payload:
        r = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context', headers=H_W,
            params={'game_id': f'eq.{row["game_id"]}'},
            json={k: v for k, v in row.items() if k != 'game_id'},
            timeout=30)
        if r.status_code in (200, 204):
            wrote += 1
        else:
            failed += 1
            if failed <= 3:
                print(f'  ! patch {row["game_id"][:44]} -> {r.status_code} '
                      f'{(r.text or "")[:120]}')
    if failed:
        print(f'  {failed} patch(es) failed')

    # Read back — a 2xx is not proof, and this column has never been written
    # before, so confirm it actually landed.
    back = _page('ncaaf_game_context', 'game_id,primary_play_shadow_v2',
                 {'season': 'eq.2026',
                  'primary_play_shadow_v2': 'not.is.null'})
    print(f'\n  wrote {wrote} · rows with a shadow now: {len(back)}')
    print(f'  next: python shadow_v2_backtest.py --variant {VARIANT} '
          f'--sport NCAAF --surface sides')
    return 0 if back else 1


if __name__ == '__main__':
    raise SystemExit(main())
