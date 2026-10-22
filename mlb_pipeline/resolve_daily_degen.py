"""Resolve Daily Degen parlays.

For each un-resolved daily_degen row (result IS NULL and game_date <= yesterday):
  1. Walk the legs JSONB array
  2. For each leg, look up outcome:
     - PROP legs → mlb_pipeline_props.result via game_id + player + sub_type + direction + line
     - ML/runline legs → mlb_game_results (spread cover / straight ML)
  3. Compute parlay result:
     - Any Loss → Loss
     - Any Pending → Pending (unless already Loss)
     - Otherwise Win (Pushes remove that leg but don't kill parlay)
  4. PATCH daily_degen with legs_resolved + result + resolved_at

Usage:
    python resolve_daily_degen.py               # yesterday
    python resolve_daily_degen.py --date 2026-07-25
    python resolve_daily_degen.py --backfill    # all pending
    python resolve_daily_degen.py --dry-run
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4, days=1)).strftime('%Y-%m-%d')


# ══ 2026-10-22 · THE DEGEN WENT MULTI-SPORT AND THIS FILE DID NOT ══
# Andy: "Fix all pending eveything should be graded."
#
# Nine Daily Degen slips sat at result='Pending', and every stuck leg carried
# the same note: "no game_results row". The rows exist. Yesterday's slip
# names 'New Mexico State ML', 'Los Angeles Lakers -6.5' and 'Portland Trail
# Blazers ML', all three of which I had resolved hours before the resolver
# last ran at 11:18 UTC.
#
# The lookups were hardcoded to mlb_game_results and mlb_pipeline_props. The
# Degen now builds legs across NCAAF, NBA and NHL, so every non-MLB leg
# queried the MLB table by a game_id that table has never seen, got zero
# rows, and reported the data as missing. It would have stayed Pending
# forever - nothing about waiting longer makes an NBA game appear in
# mlb_game_results.
#
# Same class as promote_playbook_conviction.PROPS_TABLE listing MLB and NFL
# only, and as the sport-blind external handler: a table name that was
# correct when one sport existed.
RESULTS_TABLE = {
    'MLB': 'mlb_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NHL': 'nhl_game_results',
    'NBA': 'nba_game_results',
    'NCAAB': 'ncaab_game_results',
}
PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NHL': 'nhl_pipeline_props',
    'NBA': 'nba_pipeline_props',
}


def _results_table(leg: dict) -> str:
    """The results table for this leg's sport.

    Defaults to MLB when the leg carries no `sport`, which is what every
    pre-multi-sport leg looks like - so old slips keep resolving exactly as
    they did.
    """
    return RESULTS_TABLE.get(
        str(leg.get('sport') or 'MLB').upper(), 'mlb_game_results')


# ── Leg outcome resolvers ────────────────────────────────────────────────

def _extract_player_from_pick(pick: str) -> str | None:
    """'Miles Mikolas — Under 17.5 Outs  ·  proj 15.9' → 'Miles Mikolas'"""
    if not pick: return None
    m = re.split(r'\s+[—\-]\s+', pick, maxsplit=1)
    return m[0].strip() if m else None


def _resolve_prop_leg(leg: dict) -> dict:
    """Look up mlb_pipeline_props for this leg's outcome."""
    game_id = leg.get('game_id')
    sub_type = leg.get('sub_type')
    player = _extract_player_from_pick(leg.get('pick', ''))
    if not (game_id and sub_type and player):
        return {'outcome': 'Pending', 'note': 'insufficient leg metadata'}

    # PostgREST ilike for player name (handles unicode dashes / whitespace)
    # Prop legs frequently carry NO `sport` (the 10-19 slip's Christian
    # McCaffrey reception_yds_under leg is an NFL prop with none), so the
    # sport cannot simply be read off the leg. Try each sport's props table
    # and stop at the first hit. That is safe because the filter is an
    # IDENTITY match - game_id AND prop_type AND player - and a game_id
    # belongs to exactly one sport, so at most one table can answer.
    _tables = ([PROPS_TABLE[str(leg.get('sport')).upper()]]
               if str(leg.get('sport') or '').upper() in PROPS_TABLE
               else list(PROPS_TABLE.values()))
    rows = []
    for _tbl in _tables:
        r = requests.get(
            f'{SB}/rest/v1/{_tbl}'
            f'?game_id=eq.{game_id}&prop_type=eq.{sub_type}'
            f'&player_name=ilike.{requests.utils.quote(player)}'
            f'&select=result,final_value,prop_line,direction',
            headers=H, timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows:
            break
    if not rows:
        return {'outcome': 'Pending', 'note': f'no matching prop row for {player} {sub_type}'}
    row = rows[0]
    result = row.get('result')
    if result in ('Win', 'Loss', 'Push'):
        return {
            'outcome': result,
            'actual_value': row.get('final_value'),
            'line': row.get('prop_line'),
        }
    return {'outcome': 'Pending', 'actual_value': row.get('final_value')}


def _resolve_ml_leg(leg: dict) -> dict:
    """Look up mlb_game_results for ML / runline outcome.
    leg.pick example: 'Detroit Tigers -1.5' (runline) or 'Cubs ML'
    sub_type: 'runline' | 'ml'
    """
    game_id = leg.get('game_id')
    if not game_id:
        return {'outcome': 'Pending', 'note': 'missing game_id'}

    r = requests.get(
        # select=* DELIBERATELY. The explicit list here named
        # spread_result and close_spread, which nhl_game_results does not
        # have - its columns are close_puckline* - so PostgREST returned 400,
        # the caller read `rows == []`, and the leg reported "no
        # game_results row" for a game sitting right there (2026020138 =
        # Colorado @ Philadelphia 2-3). Same trap documented in
        # sync_receipt_grades: an explicit SELECT naming a column the table
        # lacks returns 400, which callers here have read as "no rows".
        f'{SB}/rest/v1/{_results_table(leg)}'
        f'?game_id=eq.{game_id}'
        f'&select=*',
        headers=H, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        return {'outcome': 'Pending', 'note': 'no game_results row'}
    g = rows[0]
    if g.get('home_score') is None or g.get('away_score') is None:
        return {'outcome': 'Pending', 'note': 'game not final'}

    pick_txt = (leg.get('pick') or '')
    sub_type = leg.get('sub_type')
    home_win = bool(g.get('home_win'))
    home_team = (g.get('home_team') or '').lower()
    away_team = (g.get('away_team') or '').lower()

    # Figure out which side the leg is picking
    pick_home = home_team and home_team.split()[-1].lower() in pick_txt.lower()
    pick_away = away_team and away_team.split()[-1].lower() in pick_txt.lower()
    if not (pick_home or pick_away):
        # Try full team match
        pick_home = home_team in pick_txt.lower()
        pick_away = away_team in pick_txt.lower()
    if not (pick_home or pick_away):
        return {'outcome': 'Pending', 'note': f'could not parse team from "{pick_txt}"'}
    if pick_home and pick_away:
        # Take whichever has longer word overlap
        pick_home = len(home_team) >= len(away_team)
        pick_away = not pick_home

    picked_side = 'home' if pick_home else 'away'

    # ══ 2026-10-22 · EVERY SPREAD LEG WAS GRADED AS A MONEYLINE ══
    # This read `sub_type == 'runline'`. The legs carry sub_type='rl':
    #
    #   {'sport':'NBA','type':'RL','sub_type':'rl',
    #    'pick':'Los Angeles Lakers -6.5'}
    #
    # So the branch never fired and every RL leg fell through to the straight
    # ML branch below. The tell was in the stored note: yesterday's
    # 'Los Angeles Lakers -6.5' resolved with note "home won", which is the
    # ML branch's wording. LAL won by 22 so it happened to agree - a team
    # that wins by LESS than its line would have been graded WIN on a spread
    # that lost. feedback_ml_vs_rl_conflation: split WINS_ML from
    # COVERS_SPREAD.
    #
    # The dead code was also wrong twice over. It expected spread_result in
    # ('HOME_COV','AWAY_COV') while nba_game_results writes
    # 'home_covered'/'away_covered', and its fallback hardcoded a 1.5 line
    # ("Assume standard -1.5 runline") - MLB's run line, applied to an NBA
    # -6.5 or an NCAAF -10.5.
    #
    # Now graded from the score and THE LEG'S OWN LINE, parsed from the pick
    # text where the line is already signed for the named team. No
    # convention to get wrong, and no dependence on spread_result, which is
    # keyed to the CLOSE line rather than the line we took.
    if str(sub_type or '').lower() in ('rl', 'runline', 'spread', 'puckline',
                                       'ats'):
        m_line = re.search(r'([+-]\s*\d+(?:\.\d+)?)', pick_txt)
        if not m_line:
            return {'outcome': 'Pending',
                    'note': f'no line in "{pick_txt}" - cannot grade a '
                            f'spread without one'}
        try:
            line = float(m_line.group(1).replace(' ', ''))
        except (TypeError, ValueError):
            return {'outcome': 'Pending', 'note': f'unparseable line in '
                                                  f'"{pick_txt}"'}
        hs, as_ = g.get('home_score'), g.get('away_score')
        margin = (hs - as_) if picked_side == 'home' else (as_ - hs)
        v = margin + line
        if abs(v) < 1e-9:
            return {'outcome': 'Push',
                    'note': f'{picked_side} margin {margin:+g} with '
                            f'{line:+g} lands on zero'}
        return {'outcome': 'Win' if v > 0 else 'Loss',
                'note': f'{hs}-{as_}, {picked_side} margin {margin:+g} '
                        f'with {line:+g} = {v:+g}'}
    else:
        # Straight ML
        won = (picked_side == 'home' and home_win) or (picked_side == 'away' and not home_win)
        return {'outcome': 'Win' if won else 'Loss',
                'note': f'{"home" if home_win else "away"} won'}


def _resolve_nrfi_leg(leg: dict) -> dict:
    """NRFI legs — look up nrfi_result on mlb_game_results."""
    game_id = leg.get('game_id')
    if not game_id: return {'outcome': 'Pending', 'note': 'no game_id'}
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_results?game_id=eq.{game_id}&select=nrfi_result',
        headers=H, timeout=15,
    ).json()
    if not r: return {'outcome': 'Pending', 'note': 'no game row'}
    nr = r[0].get('nrfi_result')
    if nr not in ('NRFI', 'YRFI'):
        return {'outcome': 'Pending'}
    # Leg type: NRFI-lean means we're betting NO run in first inning
    pick_txt = (leg.get('pick') or '').upper()
    picking_nrfi = 'NRFI' in pick_txt or 'NO RUN' in pick_txt
    won = (nr == 'NRFI') == picking_nrfi
    return {'outcome': 'Win' if won else 'Loss', 'note': f'first inning {nr}'}


def _resolve_total_leg(leg: dict) -> dict:
    """Over/Under game total.
    leg.pick = 'Over 8.5' or 'Under 9.0'; sub_type = 'over' | 'under'.
    Grade via mlb_game_results home_score + away_score.
    """
    game_id = leg.get('game_id')
    if not game_id:
        return {'outcome': 'Pending', 'note': 'no game_id'}

    r = requests.get(
        # select=* for the same reason as the ML/RL lookup above:
        # total_result is not on every sport's results table.
        f'{SB}/rest/v1/{_results_table(leg)}'
        f'?game_id=eq.{game_id}'
        f'&select=*',
        headers=H, timeout=15,
    ).json()
    if not r:
        return {'outcome': 'Pending', 'note': 'no game_results row'}
    g = r[0]
    hs, as_ = g.get('home_score'), g.get('away_score')
    if hs is None or as_ is None:
        return {'outcome': 'Pending', 'note': 'game not final'}

    pick_txt = (leg.get('pick') or '').strip()
    m = re.search(r'(\d+(?:\.\d+)?)', pick_txt)
    line = float(m.group(1)) if m else None
    if line is None:
        # Fall back to close_total from context
        line = g.get('close_total')
    if line is None:
        return {'outcome': 'Pending', 'note': 'no line to grade against'}

    picking_over = leg.get('sub_type') == 'over' or pick_txt.lower().startswith('over')
    actual = hs + as_

    if abs(actual - line) < 1e-6:
        return {'outcome': 'Push', 'actual_value': actual, 'line': line}
    over_won = actual > line
    won = over_won if picking_over else not over_won
    return {
        'outcome': 'Win' if won else 'Loss',
        'actual_value': actual,
        'line': line,
        'note': f'{"Over" if picking_over else "Under"} {line} · actual {actual}',
    }


def resolve_leg(leg: dict) -> dict:
    """Dispatch on leg type."""
    ltype = (leg.get('type') or '').upper()
    if ltype == 'PROP':
        return _resolve_prop_leg(leg)
    if ltype in ('ML', 'RL', 'RUNLINE'):
        return _resolve_ml_leg(leg)
    if ltype == 'NRFI':
        return _resolve_nrfi_leg(leg)
    if ltype == 'TOTAL':
        return _resolve_total_leg(leg)
    return {'outcome': 'Pending', 'note': f'unknown leg type {ltype}'}


def compute_parlay_result(leg_outcomes: list) -> str:
    """All-or-nothing parlay math.
    Loss if any leg is Loss.
    Pending if no Loss but any Pending.
    Win otherwise (Pushes replace with the remaining legs — treated as won).
    """
    if any(l.get('outcome') == 'Loss' for l in leg_outcomes):
        return 'Loss'
    if any(l.get('outcome') == 'Pending' for l in leg_outcomes):
        return 'Pending'
    non_push = [l for l in leg_outcomes if l.get('outcome') != 'Push']
    if not non_push:
        return 'Push'   # every leg pushed → parlay pushes
    return 'Win'


def resolve_date(date_str: str, dry_run: bool = False) -> None:
    print(f'=== resolve_daily_degen · {date_str} ===')
    r = requests.get(
        f'{SB}/rest/v1/daily_degen?game_date=eq.{date_str}&select=*',
        headers=H, timeout=15,
    ).json()
    if not r:
        print('  no daily_degen row')
        return
    row = r[0]
    if row.get('result') and row.get('result') != 'Pending':
        print(f'  already resolved: {row["result"]}')
        return
    legs = row.get('legs') or []
    if not legs:
        print('  no legs to resolve')
        return

    leg_outcomes = []
    for leg in legs:
        outcome = resolve_leg(leg)
        outcome['pick'] = leg.get('pick')
        outcome['type'] = leg.get('type')
        leg_outcomes.append(outcome)

    parlay = compute_parlay_result(leg_outcomes)
    print(f'  parlay: {parlay}')
    for lo in leg_outcomes:
        mark = 'W' if lo['outcome'] == 'Win' else 'L' if lo['outcome'] == 'Loss' else 'P' if lo['outcome'] == 'Push' else '?'
        note = f' ({lo.get("note", "")})' if lo.get('note') else ''
        print(f'    [{mark}] {lo["pick"]}{note}')

    if dry_run:
        return

    resp = requests.patch(
        f'{SB}/rest/v1/daily_degen?game_date=eq.{date_str}',
        headers=HW,
        json={
            'legs_resolved': leg_outcomes,
            'result': parlay,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if resp.status_code < 300:
        print(f'  ✓ patched')
    else:
        print(f'  ✗ patch failed {resp.status_code}: {resp.text[:200]}')


def backfill_all_pending(dry_run: bool = False) -> None:
    print(f'=== resolve_daily_degen · BACKFILL ===')
    yesterday = _yesterday_et()
    r = requests.get(
        f'{SB}/rest/v1/daily_degen'
        f'?or=(result.is.null,result.eq.Pending)'
        f'&game_date=lte.{yesterday}'
        f'&order=game_date.asc&select=game_date',
        headers=H, timeout=30,
    ).json()
    if not isinstance(r, list) or not r:
        print('  nothing to backfill')
        return
    dates = [row['game_date'] for row in r]
    print(f'  {len(dates)} dates to resolve')
    for d in dates:
        resolve_date(d, dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: yesterday ET)')
    ap.add_argument('--backfill', action='store_true', help='resolve all pending rows')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.backfill:
        backfill_all_pending(dry_run=args.dry_run)
    else:
        resolve_date(args.date or _yesterday_et(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
