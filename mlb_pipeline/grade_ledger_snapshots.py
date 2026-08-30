"""Grade ledger snapshots (2026-08-18).

Runs after all sports have resolved (nightly). For each ungraded
ledger_snapshot, look up per-leg results:
  - ML leg: home_score vs away_score (from sport's game_results table)
  - RL/spread leg: home_score - away_score vs spread (teased line if teased)
  - Total leg: home_score + away_score vs total (teased line if teased)

Combo wins if ALL legs win. Any leg push → drop that leg (recompute odds).
Any leg loss → whole combo loses. Compute unit_pnl at 1u stake.

Idempotent: only regrades snapshots where result IS NULL.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SPORT_RESULTS = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NHL':   'nhl_game_results',
    'NBA':   'nba_game_results',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _american_to_decimal(o: float) -> float:
    return 1 + (o / 100 if o >= 0 else 100 / abs(o))


def _combined_odds(decs: list[float]) -> int:
    prod = 1.0
    for d in decs: prod *= d
    payout = prod - 1
    return int(round(100 * payout)) if payout >= 1 else int(round(-100 / payout))


def _fetch_result(sport: str, game_id: str) -> dict | None:
    table = SPORT_RESULTS.get(sport)
    if not table: return None
    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_id': f'eq.{game_id}',
                             'select': 'game_id,home_score,away_score,home_team,away_team',
                             'limit': '1'},
                     timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


def _resolve_pick_side(leg: dict, result: dict) -> str | None:
    """Return 'HOME' or 'AWAY' for the pick, or None if unresolvable.

    2026-08-22: prior grader defaulted to 'AWAY' whenever `leg.side` was
    missing AND 'home' wasn't literally in the pick label ('Philadelphia
    Phillies ML' contains neither 'home' nor 'HOME'). Every chalk_parlay
    leg had no `side` field so ALL ML picks were graded as AWAY picks —
    home wins showed as leg losses, home losses showed as leg wins.

    Fix: prefer explicit leg.side; fall back to matching the team name
    embedded in leg.pick against result.home_team / result.away_team.
    """
    side = (leg.get('side') or '').upper()
    if side in ('HOME', 'AWAY'): return side
    pick_label = (leg.get('pick') or '').strip()
    if not pick_label: return None
    # Strip trailing " ML" / " RL" / " +/-N.N" / " Over/Under N.N" tokens
    import re
    label_clean = re.sub(r'\s+(ML|RL|[+-]?\d+(\.\d+)?|Over|Under)\s*$', '', pick_label, flags=re.I).strip()
    home_team = str(result.get('home_team') or '').strip()
    away_team = str(result.get('away_team') or '').strip()
    if home_team and label_clean.lower() in home_team.lower(): return 'HOME'
    if away_team and label_clean.lower() in away_team.lower(): return 'AWAY'
    if home_team and home_team.lower() in label_clean.lower(): return 'HOME'
    if away_team and away_team.lower() in label_clean.lower(): return 'AWAY'
    return None


# Prop market → stat key (MLB Stats API). Mirrors grade_props.STAT_MAP_MLB.
_PROP_MARKET_STAT = {
    'prop_hits_over': 'h_bat', 'prop_hits_under': 'h_bat',
    'prop_ks_over':   'ks',    'prop_ks_under':   'ks',
    'prop_bb_over':   'bb',    'prop_bb_under':   'bb',
    'prop_er_over':   'er',    'prop_er_under':   'er',
    'prop_ha_over':   'h_pit', 'prop_ha_under':   'h_pit',
    'prop_outs_over': 'outs',  'prop_outs_under': 'outs',
}


def _grade_prop_leg(leg: dict, player_stats: dict | None) -> str:
    """Grade a prop leg using MLB Stats API player stats.

    player_stats: {player_name_lower: {ks, bb, er, h_pit, outs, h_bat}}
    Returns 'W', 'L', 'P', or 'NR'.
    """
    if not player_stats: return 'NR'
    market = str(leg.get('market') or '').lower()
    stat_key = _PROP_MARKET_STAT.get(market)
    if not stat_key: return 'NR'
    # Prefer explicit player_name; fall back to parsing 'pick' string
    # ("Miguel Vargas Over 0.5 Hits" → "Miguel Vargas")
    player_name = (leg.get('player_name') or '').strip()
    if not player_name:
        pick = str(leg.get('pick') or '')
        for kw in (' Over ', ' Under '):
            if kw in pick:
                player_name = pick.split(kw, 1)[0].strip()
                break
    if not player_name: return 'NR'
    stats = player_stats.get(player_name.lower())
    if not stats: return 'NR'
    actual = stats.get(stat_key)
    if actual is None: return 'NR'
    line = leg.get('teased_line') if leg.get('teased_line') is not None else leg.get('original_line')
    if line is None: return 'NR'
    line = float(line)
    # Direction: side field wins, else parse from pick, else from market suffix
    side = (leg.get('side') or '').upper()
    if side not in ('OVER', 'UNDER'):
        pick_up = str(leg.get('pick') or '').upper()
        if 'OVER' in pick_up: side = 'OVER'
        elif 'UNDER' in pick_up: side = 'UNDER'
        elif market.endswith('_over'):  side = 'OVER'
        elif market.endswith('_under'): side = 'UNDER'
        else: return 'NR'
    if side == 'OVER':
        if actual > line: return 'W'
        if actual < line: return 'L'
        return 'P'
    if actual < line: return 'W'
    if actual > line: return 'L'
    return 'P'


def _grade_leg(leg: dict, result: dict, player_stats: dict | None = None) -> str:
    """Return 'W', 'L', 'P', or 'NR' (not resolved)."""
    market = str(leg.get('market') or '').lower()
    if market.startswith('prop_'):
        return _grade_prop_leg(leg, player_stats)
    hs = result.get('home_score') if result else None
    as_ = result.get('away_score') if result else None
    if hs is None or as_ is None: return 'NR'
    market = str(leg.get('market') or '').lower()
    # Use teased line if present, else original_line
    line = leg.get('teased_line') if leg.get('teased_line') is not None else leg.get('original_line')
    if market == 'ml':
        pick_side = _resolve_pick_side(leg, result)
        if pick_side is None: return 'NR'
        actual = 'HOME' if hs > as_ else 'AWAY' if as_ > hs else 'PUSH'
        if actual == 'PUSH': return 'P'
        return 'W' if pick_side == actual else 'L'
    if market in ('rl', 'spread', 'runline'):
        if line is None: return 'NR'
        pick_side = _resolve_pick_side(leg, result)
        if pick_side is None: return 'NR'
        # 2026-08-22 FIX: line is stored PICK-perspective (+3 means the
        # pick side gets +3), not HOME-perspective. Prior code assumed
        # home-perspective and produced opposite-sign results for AWAY
        # picks with positive lines — Pirates +3 covering easily was
        # graded L. Now: line_home = line if pick=HOME else -line.
        line_home = float(line) if pick_side == 'HOME' else -float(line)
        margin_home = hs - as_
        cover = margin_home + line_home
        if abs(cover) < 0.01: return 'P'
        if pick_side == 'HOME': return 'W' if cover > 0 else 'L'
        else:                    return 'W' if cover < 0 else 'L'
    if market == 'total':
        if line is None: return 'NR'
        side_label = (leg.get('side') or '').upper()
        pick_label = (leg.get('pick') or '').upper()
        pick_side = side_label if side_label in ('OVER', 'UNDER') \
                    else ('OVER' if 'OVER' in pick_label else 'UNDER' if 'UNDER' in pick_label else None)
        if pick_side is None: return 'NR'
        actual = hs + as_
        if abs(actual - float(line)) < 0.01: return 'P'
        return ('W' if actual > float(line) else 'L') if pick_side == 'OVER' \
               else ('W' if actual < float(line) else 'L')
    return 'NR'


def grade_date(gd: str, dry_run: bool = False) -> tuple[int, int]:
    r = requests.get(f'{SB}/rest/v1/ledger_snapshots',
                     headers=H_READ,
                     params={'game_date': f'eq.{gd}', 'result': 'is.null',
                             'select': '*'},
                     timeout=20)
    snaps = r.json() if r.status_code == 200 else []
    if not snaps: return 0, 0

    # 2026-08-30: lazy-load MLB batter/pitcher stats once per date if any
    # snapshot has a prop_* leg. Feeds _grade_prop_leg for hits_parlay etc.
    player_stats = None
    needs_props = any(
        str(l.get('market') or '').lower().startswith('prop_')
        for s in snaps for l in (s.get('legs') or [])
    )
    if needs_props:
        try:
            from grade_props import fetch_player_stats_for_date
            player_stats, _n_games = fetch_player_stats_for_date(gd)
            print(f'  loaded MLB player stats: {len(player_stats)} players from {_n_games} games')
        except Exception as e:
            print(f'  ⚠ player stats fetch failed: {e}')

    graded = skipped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for snap in snaps:
        legs = snap.get('legs') or []
        results_per_leg = []
        decs_after_pushes = []
        combo_status = 'W'  # assume win, downgrade on any L/NR
        legs_hit = legs_pushed = 0
        for leg in legs:
            sport = leg.get('sport')
            gid = leg.get('game_id')
            if not sport or not gid:
                combo_status = 'NR'; break
            market = str(leg.get('market') or '').lower()
            # Prop legs bypass sport-results fetch — graded via player stats
            if market.startswith('prop_'):
                outcome = _grade_leg(leg, {}, player_stats=player_stats)
            else:
                res = _fetch_result(sport, gid)
                if not res:
                    combo_status = 'NR'; break
                outcome = _grade_leg(leg, res, player_stats=player_stats)
            results_per_leg.append(outcome)
            if outcome == 'W':
                legs_hit += 1
                odds = leg.get('teased_odds') if leg.get('teased_odds') is not None else leg.get('original_odds')
                if odds is not None:
                    try: decs_after_pushes.append(_american_to_decimal(float(odds)))
                    except (TypeError, ValueError): pass
            elif outcome == 'P':
                legs_pushed += 1
            elif outcome == 'L':
                combo_status = 'L'
            elif outcome == 'NR':
                combo_status = 'NR'

        if combo_status == 'NR':
            skipped += 1
            continue
        # Recompute odds after pushes (dropped legs)
        if combo_status == 'W' and decs_after_pushes:
            new_combined = _combined_odds(decs_after_pushes)
            pnl = new_combined / 100 if new_combined >= 0 else 100 / abs(new_combined)
        elif combo_status == 'L':
            pnl = -1.0
        else:
            pnl = 0.0

        payload = {
            'result': combo_status,
            'legs_hit': legs_hit,
            'legs_pushed': legs_pushed,
            'graded_at': now_iso,
            'unit_pnl': round(pnl, 3),
        }
        if dry_run:
            graded += 1
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/ledger_snapshots?id=eq.{snap["id"]}',
            headers=H_WRITE, json=payload, timeout=10,
        )
        if pr.status_code in (200, 204):
            graded += 1
    return graded, skipped


def run(game_date: str = None, days: int = 1, dry_run: bool = False):
    if game_date:
        dates = [game_date]
    else:
        dates = [(date.today() - timedelta(days=d+1)).isoformat() for d in range(days)]
    total_g = total_s = 0
    for gd in dates:
        g, s = grade_date(gd, dry_run=dry_run)
        total_g += g; total_s += s
        print(f'  {gd}: graded={g} skipped={s}')
    print(f'\n  {"[DRY] " if dry_run else ""}total: graded={total_g} skipped={total_s}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--days', type=int, default=7, help='backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
