"""nfl_prop_signal_discipline — post-generation cleanup for NFL props.

Fixes two systemic bugs surfaced 2026-09-08 on Week 1 slate audit:

1. **Alt-line conflicts** — book posts multiple lines per (player, prop_type).
   Stevenson rush_yds OVER 44.5 (conv 93) AND UNDER 58.5 (conv 100) both
   shipped as STRONG because scorer processed each line independently.
   Reads as "model is broken" to users; is actually alt-line +EV on both.
   Fix: dedupe to ONE row per (player, prop_type) — keep highest conviction.

2. **Empty signal_sources calibration → all STRONG default** — signals field
   is populated with l4/season_avg/edge_pct but there's no per-signal
   hit-rate registry for NFL props, so every edge-detected prop defaults
   to STRONG. 515 Week 1 props: 416 STRONG (81%). Not remotely calibrated.
   Fix (interim): hard-cap NFL prop tier at LEAN pre-Week 4. Kills the
   "everything is STRONG" default while real calibration is built.

Run after nfl_generate_props.py. Idempotent.

USAGE:
    python nfl_prop_signal_discipline.py                    # today+7d window
    python nfl_prop_signal_discipline.py --days 14
    python nfl_prop_signal_discipline.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# 2026-09-08 hard cap. Remove ONCE per-signal hit-rate calibration lands
# for NFL props (signal_sources sport=NFL class=prop rows populated with
# real hit_rate + n from graded outcomes). Until then, STRONG is unearned.
PROP_TIER_CAP_NFL = 'LEAN'
TIER_ORDER = {'PRIME': 4, 'STRONG': 3, 'LEAN': 2, 'LIGHT': 1, 'COVERAGE': 0, 'SKIP': -1, 'PASS': -2}


def _cap_tier(tier: str, cap: str) -> str:
    """Return min(tier, cap) by ranking. Preserves lower tiers unchanged."""
    if TIER_ORDER.get(tier, 0) > TIER_ORDER.get(cap, 0):
        return cap
    return tier


def _fetch_props(date_from: str, date_to: str) -> list:
    url = (f'{SB}/rest/v1/nfl_pipeline_props'
           f'?game_date=gte.{date_from}&game_date=lte.{date_to}'
           f'&select=id,game_id,player_name,prop_type,direction,tier,'
           f'conviction,book_line,player_team,opp_team')
    r = requests.get(url, headers=H_R, timeout=30)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def _fetch_game_teams(game_ids: set) -> dict:
    """Return {game_id: (home_team, away_team)} for validating player_team."""
    if not game_ids: return {}
    ids = ','.join(f'"{g}"' for g in game_ids)
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_context', headers=H_R,
        params={'game_id': f'in.({ids})',
                'select': 'game_id,home_team,away_team'},
        timeout=30,
    )
    if r.status_code != 200 or not isinstance(r.json(), list): return {}
    return {g['game_id']: (g.get('home_team'), g.get('away_team')) for g in r.json()}


def find_cross_team_leaks(props: list) -> list:
    """Props where player_team is neither the home nor away team of the
    game they're attached to. Legacy rows written before the source-guard
    landed in nfl_generate_props.py (2026-09-08)."""
    gids = {p.get('game_id') for p in props if p.get('game_id')}
    team_map = _fetch_game_teams(gids)
    leaks = []
    for p in props:
        gid = p.get('game_id')
        team = p.get('player_team')
        if not team: continue  # null-team rows are broken but a separate class
        game = team_map.get(gid)
        if not game: continue
        home, away = game
        if team != home and team != away:
            leaks.append(p)
    return leaks


def _patch(row_id: int, updates: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/nfl_pipeline_props', headers=H_W,
        params={'id': f'eq.{row_id}'},
        json=updates, timeout=15,
    )
    return r.status_code < 300


def dedupe_alt_lines(props: list) -> list:
    """Group by (game_id, player_name, prop_type_family) — across BOTH
    directions and BOTH alt lines. Keep ONE winner (highest conviction).
    Everything else demoted to SKIP.

    Why cross-direction dedupe: Stevenson case — rush_yds OVER 44.5
    (STRONG conv 93) AND UNDER 58.5 (STRONG conv 100) both technically
    +EV (projection is between the lines), but shipping both reads as
    the model is broken. Winner-take-all surfaces the strongest edge and
    hides alt-line noise. Alt-line stacking can come back post-launch
    with proper "range" display UX.
    """
    to_demote = []  # rows losing the alt-line contest
    by_group = defaultdict(list)
    for p in props:
        pt = (p.get('prop_type') or '')
        if pt.endswith('_over'):    fam = pt[:-5]
        elif pt.endswith('_under'): fam = pt[:-6]
        else:                       fam = pt
        # 2026-09-08: no direction in key — one winner per player+family
        # regardless of over/under. Prevents Stevenson-class conflicts.
        key = (p.get('game_id'), p.get('player_name'), fam)
        by_group[key].append(p)
    for key, rows in by_group.items():
        if len(rows) <= 1: continue
        # Winner = highest conviction; tiebreak on tier rank; tiebreak on lowest id
        rows_sorted = sorted(
            rows,
            key=lambda r: (-(r.get('conviction') or 0),
                           -TIER_ORDER.get(r.get('tier') or '', 0),
                           r.get('id') or 0)
        )
        keeper = rows_sorted[0]
        for loser in rows_sorted[1:]:
            to_demote.append((loser, keeper))
    return to_demote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--date', type=str)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    et = datetime.now(timezone.utc) - timedelta(hours=4)
    if args.date:
        date_from = date_to = args.date
    else:
        date_from = et.date().isoformat()
        date_to = (et.date() + timedelta(days=args.days)).isoformat()

    print(f'== nfl_prop_signal_discipline == window={date_from}→{date_to} dry_run={args.dry_run}')
    props = _fetch_props(date_from, date_to)
    print(f'  fetched {len(props)} NFL props in window')
    if not props:
        return 0

    # ── Fix 0: cross-team leaks ─────────────────────────
    leaks = find_cross_team_leaks(props)
    leak_ids = {p['id'] for p in leaks}
    # Filter these out of downstream so we don't dedupe/cap them separately
    props = [p for p in props if p['id'] not in leak_ids]

    # ── Fix 1: tier cap ──────────────────────────────────
    tier_capped = 0
    for p in props:
        cur_tier = p.get('tier') or ''
        new_tier = _cap_tier(cur_tier, PROP_TIER_CAP_NFL)
        if new_tier != cur_tier:
            p['_new_tier'] = new_tier
            tier_capped += 1

    # ── Fix 2: dedupe alt-line conflicts ────────────────
    demotions = dedupe_alt_lines(props)
    print(f'  cross-team leaks to SKIP: {len(leaks)}')
    print(f'  tier caps needed: {tier_capped}')
    print(f'  alt-line duplicates to demote to SKIP: {len(demotions)}')

    # ── Write ─────────────────────────────────────────────
    if args.dry_run:
        # print a few examples
        for p in props[:5]:
            if p.get('_new_tier'):
                print(f'    would cap: {p["player_name"]:20} {p["prop_type"]:22} {p["tier"]}→{p["_new_tier"]}')
        for loser, keeper in demotions[:5]:
            print(f'    would demote: {loser["player_name"]:20} {loser["prop_type"]:22} '
                  f'line={loser.get("book_line")} conv={loser.get("conviction")} → SKIP  '
                  f'(keeper line={keeper.get("book_line")} conv={keeper.get("conviction")})')
        return 0

    written = 0
    fails = 0
    # Cross-team leaks first — demote to SKIP (safer than delete; preserves audit trail)
    for p in leaks:
        if _patch(p['id'], {'tier': 'SKIP', 'conviction': 0}): written += 1
        else: fails += 1
    # Apply cap
    for p in props:
        if p.get('_new_tier'):
            if _patch(p['id'], {'tier': p['_new_tier']}): written += 1
            else: fails += 1
    # Apply demotions (skip if row already got tier-capped to something below)
    for loser, keeper in demotions:
        # Demote loser to SKIP so composers ignore it
        if _patch(loser['id'], {'tier': 'SKIP', 'conviction': 0}):
            written += 1
        else: fails += 1

    print(f'\n== TOTAL == patched={written} failed={fails}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
