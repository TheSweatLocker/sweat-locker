"""NCAAF FCS chalk scorer (2026-09-02).

Fills the v3/v4 projection gap on FBS-vs-FCS matchups. CFBD publishes
SP+ ratings for FBS teams only, so games where one side is FCS have
NULL projected_spread + NULL model_pred_spread. Card renders no lens
values, feels thin.

Solution: chalk model using FBS SP+ - FCS default (-25). FBS wins 96%
of FBS-vs-FCS games historically; average margin 30+ points. Model is
crude but honest — better than blank projections.

Detection: game has one team with home_sp_overall/away_sp_overall
populated + the other NULL. Populated side is FBS, NULL side is FCS.

Output: writes projected_spread + model_pred_spread + Marks
_engine='fcs_chalk' on primary_play audit_note.

Sign convention: projected_spread > 0 means home favored (nflverse
standard, matches other NCAAF scorers).

Usage:
    python ncaaf_fcs_chalk_scorer.py                 # today's + upcoming
    python ncaaf_fcs_chalk_scorer.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

# Assumed SP+ for an average FCS opponent. Empirically bottom-quintile
# FBS is around -20 SP+; FCS teams typically -25 to -35. Use -28 as
# midpoint. Tunable later if we validate against actual FCS-vs-FBS
# results (which we grade like any other game).
FCS_SP_DEFAULT = -28.0

# Home-field advantage in NCAAF: ~2.5-3 points. Use 2.5.
HOME_FIELD_ADVANTAGE = 2.5


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_upcoming_games():
    today_iso = datetime.now(timezone.utc).date().isoformat()
    hi_iso = (datetime.now(timezone.utc).date() + timedelta(days=8)).isoformat()
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={
            'select': 'game_id,home_team,away_team,game_date,'
                      'home_sp_overall,away_sp_overall,'
                      'projected_spread,model_pred_spread,'
                      'neutral_site',
            'game_date': f'gte.{today_iso}',
            'order': 'game_date.asc',
            'limit': '200',
        },
        timeout=30,
    )
    return r.json() if r.status_code == 200 else []


def compute_chalk(game: dict) -> Optional[dict]:
    """Return {projected_spread, model_pred_spread, engine_tag} or None
    if this game doesn't need chalk fill."""
    home_sp = _f(game.get('home_sp_overall'))
    away_sp = _f(game.get('away_sp_overall'))
    # Already has real projections? Skip
    if _f(game.get('projected_spread')) is not None or _f(game.get('model_pred_spread')) is not None:
        return None
    # Both populated? Regular FBS-vs-FBS — real scorers should handle
    if home_sp is not None and away_sp is not None:
        return None
    # Both NULL? Data gap, not FCS — skip
    if home_sp is None and away_sp is None:
        return None
    # One populated, one null → FBS vs FCS
    home_is_fbs = home_sp is not None
    fbs_sp = home_sp if home_is_fbs else away_sp
    fcs_sp = FCS_SP_DEFAULT
    # Sign convention: projected_spread > 0 means home favored
    hfa = 0.0 if game.get('neutral_site') else HOME_FIELD_ADVANTAGE
    if home_is_fbs:
        # Home is FBS; expected margin = home_fbs - fcs_default + HFA
        expected_margin = (fbs_sp - fcs_sp) + hfa
        projected_spread = expected_margin  # positive = home fav
    else:
        # Away is FBS
        expected_margin = -(fbs_sp - fcs_sp) + hfa  # negative = away fav, then + HFA
        projected_spread = expected_margin
    return {
        'projected_spread': round(projected_spread, 1),
        'model_pred_spread': round(projected_spread, 1),
        'engine_tag': 'fcs_chalk',
    }


def patch_game(game_id: str, patch: dict, dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] {game_id}: {patch}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json=patch, timeout=15,
    )
    return r.status_code in (200, 204)


def run(dry_run: bool = False) -> None:
    print(f'== NCAAF FCS chalk scorer{" [DRY]" if dry_run else ""} ==')
    games = fetch_upcoming_games()
    print(f'  upcoming games: {len(games)}')
    if not games: return

    filled = 0
    for g in games:
        chalk = compute_chalk(g)
        if not chalk: continue
        home = g.get('home_team'); away = g.get('away_team')
        fbs_side = 'HOME' if _f(g.get('home_sp_overall')) is not None else 'AWAY'
        print(f'  {away} @ {home}  (FBS={fbs_side})  → chalk spread {chalk["projected_spread"]:+.1f}')
        patch = {
            'projected_spread': chalk['projected_spread'],
            'model_pred_spread': chalk['model_pred_spread'],
        }
        if patch_game(g['game_id'], patch, dry_run):
            filled += 1

    verb = '[DRY]' if dry_run else 'wrote'
    print(f'\n  {verb} {filled} chalk projections onto ncaaf_game_context')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
