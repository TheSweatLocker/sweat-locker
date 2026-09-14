"""Snapshot every fired signal per game into signal_attribution.

Andy 9/13: "we should be tracking what each says and keeping record."
Per-game per-signal audit trail so we can measure whether each signal
we surface (EPA gap, LR shadow, GOAT, cohorts, anchor, etc.) actually
predicts the pick side hitting.

Runs post-Sharp-Card-lock (11am ET) — captures the FROZEN version of
primary_play + signals that the user saw. Grader then resolves each
snapshot against the final result.

Signals captured per game (NFL + NCAAF):
  EPA_GAP        — home_off_epa_pp - away_off_epa_pp (kind: ok|warn)
  LR_SHADOW      — primary_play._lr_ml_shadow (side + p_home_win)
  GOAT           — align_status.chips_extra where key='goat'
  ANCHOR         — spread_anchor_weight (0 = off, 0.4 = mid, 0.75 = heavy)
  COHORT_<tag>   — each cohort_tag from ctx.cohort_tags
  CPOE_GAP       — QB CPOE differential (when available)

Run:
  python signal_attribution_snapshot.py                 # today, NFL + NCAAF
  python signal_attribution_snapshot.py --sport NFL
  python signal_attribution_snapshot.py --date 2026-09-13
  python signal_attribution_snapshot.py --dry-run
"""
import argparse
import os
import sys
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p)
        break

SB = os.environ.get('SUPABASE_URL')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
     or os.environ.get('SUPABASE_KEY'))
if not (SB and K):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')

H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _fetch_games(sport: str, game_date: str) -> list[dict]:
    tbl = 'nfl_game_context' if sport == 'NFL' else 'ncaaf_game_context'
    # 2026-09-13 sport-specific offense-rating column:
    #   NFL   → home_off_rating (only exists on NFL ctx)
    #   NCAAF → home_off_epa_pp (only exists on NCAAF ctx)
    # Aliasing to a common name (`home_off_rating`) via PostgREST select
    # so build_signal_rows can read one canonical field regardless of sport.
    base_cols = ('game_id,season,week,season_week,game_date,'
                 'home_team,away_team,cohort_tags,align_status,'
                 'primary_play,spread_anchor_weight')
    if sport == 'NFL':
        cols = f'{base_cols},home_off_rating,away_off_rating'
    else:  # NCAAF
        cols = f'{base_cols},home_off_rating:home_off_epa_pp,away_off_rating:away_off_epa_pp'
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-499'},
                     params={'game_date': f'eq.{game_date}', 'select': cols},
                     timeout=20)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def _build_rows(sport: str, ctx: dict) -> list[dict]:
    """Extract fired signals for one game → list of snapshot rows."""
    pp = ctx.get('primary_play') or {}
    if not isinstance(pp, dict):
        return []

    game_id = ctx.get('game_id')
    game_date = ctx.get('game_date')
    if not (game_id and game_date):
        return []

    pick_market = pp.get('type')
    pick_side = (pp.get('side') or '').upper() or None
    pick_tier = str(pp.get('tier') or '').upper() or None
    is_ml_or_spread = pick_market in ('ml', 'spread', 'rl')

    base = {
        'sport': sport,
        'game_id': game_id,
        'game_date': game_date,
        'season': ctx.get('season'),
        'season_week': ctx.get('season_week'),
        'pick_market': pick_market,
        'pick_side': pick_side,
        'pick_tier': pick_tier,
    }
    out: list[dict] = []

    # OFF_RATING_GAP (was EPA_GAP; renamed for column-name accuracy —
    # home_off_rating is the aggregate offense signal both sports carry.
    # Real EPA-per-game / EPA-per-play cols exist per sport but under
    # different names — this is the cleanest single field).
    he = ctx.get('home_off_rating')
    ae = ctx.get('away_off_rating')
    try:
        if he is not None and ae is not None:
            gap = float(he) - float(ae)
            # Threshold: 0.5 rating pts is meaningful on the ~50-120 scale
            if abs(gap) >= 0.5:
                leader = 'HOME' if gap > 0 else 'AWAY'
                kind = ('ok' if (is_ml_or_spread and leader == pick_side)
                        else 'warn' if is_ml_or_spread
                        else 'neutral')
                out.append({**base, 'signal_key': 'OFF_RATING_GAP',
                            'signal_value': round(gap, 2),
                            'signal_side': leader, 'kind': kind})
    except (TypeError, ValueError): pass

    # LR_SHADOW (from primary_play._lr_ml_shadow)
    lr = pp.get('_lr_ml_shadow') or {}
    if isinstance(lr, dict):
        try:
            p = float(lr.get('p_home_win'))
            lr_side = ('HOME' if p >= 0.55 else 'AWAY' if p < 0.45 else 'NEUTRAL')
            if lr_side != 'NEUTRAL':
                kind = ('ok' if (is_ml_or_spread and lr_side == pick_side)
                        else 'warn' if is_ml_or_spread
                        else 'neutral')
                out.append({**base, 'signal_key': 'LR_SHADOW',
                            'signal_value': round(p, 4),
                            'signal_side': lr_side, 'kind': kind})
        except (TypeError, ValueError): pass

    # ANCHOR
    aw = ctx.get('spread_anchor_weight')
    try:
        if aw is not None and float(aw) > 0:
            out.append({**base, 'signal_key': 'ANCHOR',
                        'signal_value': round(float(aw), 2),
                        'signal_side': 'NEUTRAL', 'kind': 'neutral'})
    except (TypeError, ValueError): pass

    # GOAT — from align_status.chips_extra
    als = ctx.get('align_status') or {}
    if isinstance(als, dict):
        for chip in (als.get('chips_extra') or []):
            if isinstance(chip, dict) and chip.get('key') == 'goat':
                val = chip.get('value', '')
                if val and val != 'PASS':
                    # Format: "PIT · LEAN" — first token is side
                    tok = str(val).split('·')[0].strip().upper()
                    kind = ('ok' if (is_ml_or_spread and tok == pick_side)
                            else 'neutral')
                    out.append({**base, 'signal_key': 'GOAT',
                                'signal_value': None,
                                'signal_side': tok if tok in ('HOME','AWAY') else 'NEUTRAL',
                                'kind': kind})

    # Cohort tags
    for tag in (ctx.get('cohort_tags') or []):
        if not isinstance(tag, str): continue
        key = f'COHORT_{tag.upper().replace(" ", "_")}'
        out.append({**base, 'signal_key': key, 'signal_value': None,
                    'signal_side': 'NEUTRAL', 'kind': 'ok'})

    return out


def upsert_batch(rows: list[dict]) -> int:
    if not rows: return 0
    r = requests.post(
        f'{SB}/rest/v1/signal_attribution?on_conflict=sport,game_id,signal_key',
        headers=H_WRITE, json=rows, timeout=20,
    )
    if r.status_code in (200, 201, 204):
        return len(rows)
    print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
    return 0


def run(sport: Optional[str] = None,
        game_date: Optional[str] = None,
        dry_run: bool = False) -> None:
    gd = game_date or _today_et()
    sports = [sport] if sport else ['NFL', 'NCAAF']
    print(f'=== signal_attribution_snapshot · {gd} · sports={sports} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    for sp in sports:
        games = _fetch_games(sp, gd)
        print(f'  {sp}: {len(games)} games in ctx')
        all_rows: list[dict] = []
        for ctx in games:
            if isinstance(ctx, dict):
                all_rows.extend(_build_rows(sp, ctx))
        print(f'  {sp}: {len(all_rows)} signal rows to snapshot')
        if dry_run:
            for r in all_rows[:8]:
                print(f'    [DRY] {r["signal_key"]:20s} side={r["signal_side"]} '
                      f'kind={r["kind"]} · game={r["game_id"][:20]}')
        else:
            written = upsert_batch(all_rows)
            print(f'  {sp}: wrote/merged {written} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=['NFL', 'NCAAF'])
    p.add_argument('--date', dest='game_date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.game_date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
