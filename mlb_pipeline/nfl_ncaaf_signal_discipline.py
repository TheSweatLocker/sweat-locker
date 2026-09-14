"""Signal-driven tier discipline for NFL + NCAAF primary_play.

Reads real per-signal performance data from v_signal_records and
applies two hard gates to nfl_game_context.primary_play + ncaaf_game_context.primary_play:

  1. LR-warn hard-cap: if LR shadow DISAGREES with pick direction
     strongly (p_home_win >= 0.60 opposite pick OR <= 0.40 opposite
     pick), cap the tier at PASS (or LEAN if we want to keep it on
     the card as a low-conviction lean). Backed by 4.3% hit rate on
     LR-warn games (n=22 NCAAF 9/12) vs 93.6% on LR-agree.

  2. Anchor cap: if spread_anchor_weight > 0 AND tier in
     PRIME/STRONG, downgrade to LEAN. Anchored picks hit 30% (n=40).
     The anchor fires when the model is uncertain — anchored picks
     should never ride at top tier.

Runs post-nfl_game_context.build (after primary_play is set) and
BEFORE sharp_card composition (so Sharp Card sees the gated tier).

Idempotent: reads primary_play, applies gates, PATCHes back. Safe
to run multiple times per day.

CLI:
  python nfl_ncaaf_signal_discipline.py                 # today
  python nfl_ncaaf_signal_discipline.py --date 2026-09-14
  python nfl_ncaaf_signal_discipline.py --sport NCAAF
  python nfl_ncaaf_signal_discipline.py --dry-run
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p); break

SB = os.environ.get('SUPABASE_URL')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
if not (SB and K):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')

H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Gate constants
LR_WARN_HARD_THRESHOLD = 0.60   # LR probability on OPPOSITE side must exceed this to trigger warn-cap
ANCHOR_CAP_TIERS = {'PRIME', 'STRONG'}   # tiers to downgrade when anchor fires
ANCHOR_CAP_NEW_TIER = 'LEAN'


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _fetch_games(sport: str, game_date: str) -> list[dict]:
    tbl = 'nfl_game_context' if sport == 'NFL' else 'ncaaf_game_context'
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-499'},
                     params={
                         'game_date': f'eq.{game_date}',
                         'select': 'game_id,home_team,away_team,primary_play,spread_anchor_weight',
                     },
                     timeout=20)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def _apply_gates(pp: dict, spread_anchor_weight) -> tuple[dict, list[str]]:
    """Return (new_pp, applied_gates_list). new_pp is a copy with
    tier / conviction possibly capped + gate reasons appended to `sub`."""
    if not isinstance(pp, dict):
        return pp, []

    tier = str(pp.get('tier') or '').upper()
    conv = pp.get('conviction') or 0
    side = str(pp.get('side') or '').upper()
    market = str(pp.get('type') or '').lower()
    label = pp.get('label') or ''
    applied: list[str] = []

    new_pp = dict(pp)

    # Gate 1: LR-warn hard-cap (only applies to ML / spread / rl picks)
    if market in ('ml', 'spread', 'rl') and side in ('HOME', 'AWAY'):
        lr = pp.get('_lr_ml_shadow') or {}
        if isinstance(lr, dict):
            try:
                p_home = float(lr.get('p_home_win'))
                # Opposite of pick: HOME pick → LR says AWAY strongly (p_home <= 1 - threshold)
                #                   AWAY pick → LR says HOME strongly (p_home >= threshold)
                lr_disagrees_strongly = (
                    (side == 'HOME' and p_home <= (1.0 - LR_WARN_HARD_THRESHOLD)) or
                    (side == 'AWAY' and p_home >= LR_WARN_HARD_THRESHOLD)
                )
                if lr_disagrees_strongly:
                    # Cap to LEAN — the pick still appears but with dampened conviction
                    # + explicit reason. Don't PASS entirely; user can see the flag.
                    if tier in ('PRIME', 'STRONG'):
                        new_pp['tier'] = 'LEAN'
                        tier = 'LEAN'  # for cascade with anchor check below
                        new_pp['conviction'] = min(conv, 55)
                        conv = new_pp['conviction']
                    _pre_sub = str(new_pp.get('sub') or '').strip()
                    _flag = (f'⚠ LR shadow warns other way (p_home={p_home:.2f}) — '
                             f'capped to LEAN. LR-warn hits 4.3% historically.')
                    new_pp['sub'] = f'{_pre_sub} · {_flag}' if _pre_sub else _flag
                    applied.append(f'lr_warn_cap:p={p_home:.2f}')
            except (TypeError, ValueError):
                pass

    # Gate 2: Anchor cap (any market type, if anchor fired)
    try:
        aw = float(spread_anchor_weight) if spread_anchor_weight is not None else 0.0
        if aw > 0 and tier in ANCHOR_CAP_TIERS:
            new_pp['tier'] = ANCHOR_CAP_NEW_TIER
            new_pp['conviction'] = min(conv, 60)
            _pre_sub = str(new_pp.get('sub') or '').strip()
            _flag = (f'⚠ Market anchor active (w={aw:.2f}) — '
                     f'model uncertain, capped to LEAN. Anchored picks hit 30% historically.')
            new_pp['sub'] = f'{_pre_sub} · {_flag}' if _pre_sub else _flag
            applied.append(f'anchor_cap:w={aw:.2f}')
    except (TypeError, ValueError):
        pass

    return new_pp, applied


def _patch(tbl: str, game_id: str, new_pp: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{game_id}',
        headers=H_WRITE,
        json={'primary_play': new_pp},
        timeout=10,
    )
    return r.status_code in (200, 204)


def run(sport: Optional[str] = None,
        game_date: Optional[str] = None,
        dry_run: bool = False) -> None:
    gd = game_date or _today_et()
    sports = [sport] if sport else ['NFL', 'NCAAF']
    print(f'=== nfl_ncaaf_signal_discipline · {gd} · sports={sports} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    for sp in sports:
        tbl = 'nfl_game_context' if sp == 'NFL' else 'ncaaf_game_context'
        games = _fetch_games(sp, gd)
        print(f'  {sp}: {len(games)} games in ctx')
        capped = 0
        no_change = 0
        for g in games:
            pp = g.get('primary_play') or {}
            aw = g.get('spread_anchor_weight')
            new_pp, gates = _apply_gates(pp, aw)
            if not gates:
                no_change += 1
                continue
            gid = g.get('game_id')
            label = pp.get('label', '?')
            old_tier = pp.get('tier', '?')
            new_tier = new_pp.get('tier', '?')
            print(f'    {label:24s} {old_tier} → {new_tier}  · gates: {",".join(gates)}')
            if not dry_run and gid:
                if _patch(tbl, gid, new_pp):
                    capped += 1
                else:
                    print(f'      ⚠ PATCH failed for {gid}')
        print(f'  {sp}: {capped} capped · {no_change} unchanged')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=['NFL', 'NCAAF'])
    p.add_argument('--date', dest='game_date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.game_date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
