#!/usr/bin/env python3
"""Shadow variant: record what FADING our own NCAAF total picks would do.

Andy 2026-09-30. Companion to apply_ncaaf_total_suppression, which stops
publishing NCAAF totals as of today.

WHY A SHADOW AND NOT A SHIPPED FADE
Andy's standing rule (feedback_fade_not_suppress_803) is that a sub-45% bucket
should fade the other side rather than go quiet, and on the raw numbers that is
tempting: fading all 48 NCAAF total picks would have gone 27-12, +12.55u.

Two reasons I suppressed instead and only shadow the fade:

  * Per side the evidence is thin. Fading its OVER picks is 14-4 (77.8%), but
    fading its UNDER picks is only 13-8 (61.9%, z=+1.09) — that half does not
    clear 2 SE, and the combined 69.2% is carried by the OVER half.
  * The mechanism argues against durability. projected_total's MAE is 12.81
    against the market's 11.82 (n=199) — the model is WORSE than the number it
    bets against. That makes it noise, and noise regresses toward 50%; it does
    not stay invertible. A genuine anti-signal needs a reason the model is
    systematically backwards, and "it has a larger error than the close" is a
    reason for it to be uninformative, not reversed.

Publishing a fade also means telling subscribers to bet against our own engine.
That is a strong claim about a model we already know is inert, and it earns its
own record before it earns the card.

WHAT IT WRITES
primary_play_shadow_v2 on TOTAL rows only, variant 'total_fade_v1', with the
side flipped and the tier carried across unchanged. No collision with
ncaaf_shadow_sweat_tier.py: that variant filters to SIDE markets, so the two
never touch the same row. Verified before writing, not assumed.

Keeps the live pick's market and tier so the only difference is the SIDE — the
thing being tested. Records the live side too, so the harness can compare
without re-deriving it.

HOW IT IS JUDGED — Andy's promotion gate:
    picks_count >= 50 AND (shadow_hit_rate - live_hit_rate) >= 0.03
    sustained >= 4 weeks

and read shadow_v2_backtest.py's SCOPE LIMIT note first: unlike the sweat_tier
variant, this one DOES change the pick, so the harness's within-bucket
comparison is the right shape for it.

NOT WIRED TO ANYTHING USER-FACING.

USAGE
    python ncaaf_shadow_total_fade.py --dry-run
    python ncaaf_shadow_total_fade.py
    python ncaaf_shadow_total_fade.py --backfill
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

VARIANT = 'total_fade_v1'
FLIP = {'OVER': 'UNDER', 'UNDER': 'OVER'}

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
    print('ncaaf_shadow_total_fade: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json'}


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
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--days-back', type=int, default=21)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    extra = {'season': 'eq.2026'}
    if not a.backfill:
        extra['game_date'] = f'gte.{(today - timedelta(days=a.days_back))}'

    rows = _page('ncaaf_game_context',
                 'game_id,game_date,away_team,home_team,close_total,'
                 'primary_play,primary_play_shadow_v2', extra)
    print(f'=== ncaaf_shadow_total_fade · variant={VARIANT} ===')
    print(f'  ctx rows in scope: {len(rows)}')

    payload, skipped = [], collections.Counter()
    now_iso = datetime.now(timezone.utc).isoformat()

    for c in rows:
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:
                pp = None
        if not isinstance(pp, dict):
            skipped['no primary_play'] += 1
            continue
        if str(pp.get('type') or '').lower() != 'total':
            continue                      # sides are sweat_tier_v1's business
        live_side = str(pp.get('side') or '').upper()
        if live_side not in FLIP:
            skipped[f'side={live_side or "(blank)"}'] += 1
            continue

        # Refuse to clobber another variant's shadow. sweat_tier_v1 is
        # sides-only so this should never fire, but "should never" is how the
        # last four bugs got in.
        ex = c.get('primary_play_shadow_v2')
        if isinstance(ex, str):
            try:
                ex = json.loads(ex)
            except Exception:
                ex = None
        if isinstance(ex, dict) and ex.get('variant') not in (None, VARIANT):
            skipped[f"occupied by {ex.get('variant')}"] += 1
            continue

        payload.append({
            'game_id': c['game_id'],
            'primary_play_shadow_v2': {
                'variant': VARIANT,
                'type': 'total',
                # The ONLY difference from live. Tier and market carried over
                # so any hit-rate delta is attributable to the side flip.
                'side': FLIP[live_side],
                'live_side': live_side,
                'tier': pp.get('tier'),
                'conviction': pp.get('conviction'),
                'close_total': c.get('close_total'),
                'generated_at': now_iso,
            },
            'shadow_v2_generated_at': now_iso,
        })

    print(f'  total picks flipped: {len(payload)}')
    if skipped:
        for k, v in skipped.most_common():
            print(f'    skipped · {k}: {v}')

    if a.dry_run:
        print('\n  (dry run — nothing written)')
        return 0
    if not payload:
        print('\n  nothing to write')
        return 0

    # Per-row PATCH, not a batched upsert: an on_conflict=game_id upsert fails
    # 23502 here because the conflict target does not match a unique
    # constraint, so PostgREST attempts a real INSERT and trips NOT NULL on
    # columns this payload has no business supplying. Same finding as
    # ncaaf_shadow_sweat_tier.py.
    wrote = failed = 0
    for row in payload:
        r = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context', headers=H_W,
            params={'game_id': f'eq.{row["game_id"]}'},
            json={k: v for k, v in row.items() if k != 'game_id'}, timeout=30)
        if r.status_code in (200, 204):
            wrote += 1
        else:
            failed += 1
            if failed <= 3:
                print(f'  ! patch {row["game_id"][:44]} -> {r.status_code} '
                      f'{(r.text or "")[:120]}')
    if failed:
        print(f'  {failed} patch(es) failed')

    # Read back — a 204 is not proof the row changed.
    back = _page('ncaaf_game_context', 'game_id,primary_play_shadow_v2',
                 {'season': 'eq.2026',
                  'primary_play_shadow_v2': 'not.is.null'})
    mine = sum(1 for x in back
               if isinstance(x.get('primary_play_shadow_v2'), dict)
               and x['primary_play_shadow_v2'].get('variant') == VARIANT)
    print(f'\n  wrote {wrote} · rows carrying {VARIANT}: {mine}')
    print(f'  next: python shadow_v2_backtest.py --variant {VARIANT} '
          f'--sport NCAAF')
    return 0 if mine else 1


if __name__ == '__main__':
    raise SystemExit(main())
