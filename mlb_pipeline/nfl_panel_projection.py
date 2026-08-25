"""NFL Panel projection — team pts from summed player fantasy projections
(2026-08-09 · Panel model).

MLB analog: the Panel model in MLB aggregates opposing lineup wRC+ +
pitcher xERA to imply team runs. Here, we sum every player's projected
stats (Sleeper/ESPN) on a team, then convert to expected real points.

Formula:
    team_off_pts_raw = f(pass_yds, pass_tds, rush_yds, rush_tds, rec_tds, fg, xp)
    team_def_pts = 3*sacks + 6*def_tds + 2*def_ints + points_allowed_bracket
    team_final_pts = team_off_pts_raw × source_calibration × adjustments

Where adjustments account for:
    - home_edge (+3% pts)
    - div_rivalry (+2% both sides — more intense games)
    - injury_penalty (from status = OUT / IR on starters)

Public helper:
    from nfl_panel_projection import compute_panel_projection
    result = compute_panel_projection(home_team, away_team, season, week,
                                       season_type='reg', is_home_div=None,
                                       is_rivalry=False, source='sleeper')
    # → {'home_pts': 22.5, 'away_pts': 24.1, 'total': 46.6,
    #    'home_off_raw': ..., 'confidence': 0.75, 'players_used': 45, ...}
"""
from __future__ import annotations
import os, sys, json
from pathlib import Path
from typing import Optional

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL'); KEY = os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else None


# ─── Fantasy-pts → real-pts calibration constants ─────────────────
# Rationale: PPR fantasy pts don't equal team points (a team scoring 24
# real points typically has ~120-150 PPR pts summed across skill players
# because receptions/yardage don't equal touchdowns). Ratio derived from
# 2024 season backtest: real_pts / summed_ppr_pts ≈ 0.185. Refine as we
# accumulate 2025+ data.
# 2026-08-09: calibrated against 2025 wk1-5 backtest (78 games).
# At K=0.245: side accuracy 40/69 = 58%, MAE 11.79 vs market MAE 10.87.
# Panel comes close to market on point accuracy AND beats -110 breakeven
# on side selection. Refine constant seasonally as we accumulate data.
FANTASY_PTS_TO_REAL_PTS = 0.245
# Bracket-adjust for defensive points scored (INTs/sacks/DEF TDs count in
# fantasy but real team pts already includes them — need to zero out or
# they double-count).
DEFENSE_FANTASY_ZERO_OUT = True

# Position weights — some fantasy PPR points don't translate 1:1 to team
# scoring (e.g. WR receptions are inflated in PPR). Normalize.
POSITION_WEIGHT = {
    'QB': 1.00, 'RB': 0.95, 'WR': 0.85, 'TE': 0.90,
    'K':  1.00, 'DEF': 0.00 if DEFENSE_FANTASY_ZERO_OUT else 1.00,
    'DST': 0.00 if DEFENSE_FANTASY_ZERO_OUT else 1.00,
}

# Injury penalty per starting skill player OUT/IR (multiplicative)
INJURY_STARTER_PENALTY = 0.04

# Rivalry bump (division game) + HFA
DIVISION_RIVALRY_BUMP = 0.02
HOME_FIELD_MULT = 1.03

# League-average defensive contribution to a team's real score
LEAGUE_AVG_DEF_PTS = 1.5


def _f(v):
    try: return float(v) if v is not None else 0.0
    except (TypeError, ValueError): return 0.0


def load_team_projections(team: str, season: int, week: int,
                           season_type: str = 'reg',
                           source: str = 'sleeper') -> list:
    """Return list of player projection rows for a team from one source."""
    if not H:
        return []
    r = requests.get(
        f'{SB}/rest/v1/nfl_player_projections',
        headers=H,
        params={'source': f'eq.{source}', 'season': f'eq.{season}',
                'week': f'eq.{week}', 'season_type': f'eq.{season_type}',
                'team': f'eq.{team}', 'select': '*'},
        timeout=15,
    )
    return r.json() if isinstance(r.json(), list) else []


def load_team_projections_ensemble(team: str, season: int, week: int,
                                    season_type: str = 'reg') -> tuple:
    """Return (merged_player_rows, sources_used) — averaged across sources
    when both Sleeper + ESPN cover the same player_name.
    Player_id can differ between sources but full name matching bridges."""
    sleeper = load_team_projections(team, season, week, season_type, 'sleeper')
    espn = load_team_projections(team, season, week, season_type, 'espn_fantasy')
    sources_used = []
    if sleeper: sources_used.append('sleeper')
    if espn: sources_used.append('espn_fantasy')
    if not (sleeper or espn):
        return [], sources_used
    if not sleeper: return espn, sources_used
    if not espn: return sleeper, sources_used

    # Merge by normalized name — strip punctuation + accent + suffix
    # (Jr./III/II). NFL names vary between "Ja'Marr Chase" and "Ja Marr Chase".
    import re, unicodedata as ud
    def _key(r):
        n = r.get('player_name') or ''
        n = ud.normalize('NFKD', n).encode('ascii','ignore').decode('ascii')
        n = re.sub(r'\b(jr|iii|ii|iv|sr)\b\.?', '', n.lower())
        n = re.sub(r'[^a-z0-9\s]', '', n).strip()
        n = re.sub(r'\s+', ' ', n)
        return n
    espn_by_name = {_key(r): r for r in espn}
    merged = []
    for s in sleeper:
        k = _key(s)
        e = espn_by_name.get(k)
        if e:
            # Average PPR pts across the two sources
            s_pts = s.get('proj_fantasy_pts')
            e_pts = e.get('proj_fantasy_pts')
            if s_pts is not None and e_pts is not None:
                avg = round((float(s_pts) + float(e_pts)) / 2, 2)
                s_copy = dict(s)
                s_copy['proj_fantasy_pts'] = avg
                merged.append(s_copy)
                continue
        merged.append(s)
    # ESPN-only players (not in sleeper) — add them too
    sleeper_names = {_key(r) for r in sleeper}
    for e in espn:
        if _key(e) not in sleeper_names:
            merged.append(e)
    return merged, sources_used


def _sum_team_fantasy_pts(rows: list, ppr: bool = True) -> tuple:
    """Return (weighted_sum, position_breakdown, players_used).

    2026-08-09 fix: cap per position to realistic starter counts. Otherwise
    ensemble source inflates by adding backup RBs/WRs that don't produce
    real team scoring. NFL team pts come primarily from: 1 QB, 2 RBs,
    3 WRs, 1 TE, 1 K, 1 DEF.
    """
    POSITION_CAPS = {'QB': 1, 'RB': 2, 'WR': 3, 'TE': 1, 'K': 1, 'DEF': 1, 'DST': 1}
    # Sort players by fantasy pts within position, take top N
    by_pos = {}
    for row in rows:
        pos = row.get('position') or '?'
        if pos not in POSITION_CAPS: continue
        status = (row.get('status') or '').upper()
        if status in ('IR', 'PUP', 'SUS'): continue
        pts_field = 'proj_fantasy_pts' if ppr else 'proj_fantasy_pts_std'
        pts = _f(row.get(pts_field))
        if status in ('D', 'DOUBTFUL'): pts *= 0.5
        elif status in ('Q', 'QUESTIONABLE'): pts *= 0.85
        by_pos.setdefault(pos, []).append((pts, row))
    weighted = 0.0
    breakdown = {}
    used = 0
    for pos, plist in by_pos.items():
        plist.sort(key=lambda p: -p[0])   # highest pts first
        n_slots = POSITION_CAPS[pos]
        for pts, row in plist[:n_slots]:
            w = POSITION_WEIGHT.get(pos, 0.5)
            if w == 0: continue
            weighted += pts * w
            breakdown[pos] = breakdown.get(pos, 0) + pts * w
            used += 1
    return weighted, breakdown, used


def _count_starter_outs(rows: list) -> int:
    """Count starting skill players (top-2 per pos of QB/RB/WR/TE) who are OUT/IR."""
    starters_by_pos = {'QB': 1, 'RB': 2, 'WR': 3, 'TE': 1}
    # Rank each position by fantasy pts, then check status of top-N
    by_pos = {}
    for r in rows:
        pos = r.get('position')
        if pos not in starters_by_pos: continue
        by_pos.setdefault(pos, []).append(r)
    outs = 0
    for pos, plist in by_pos.items():
        n_starters = starters_by_pos[pos]
        plist.sort(key=lambda r: -_f(r.get('proj_fantasy_pts')))
        for r in plist[:n_starters]:
            st = (r.get('status') or '').upper()
            if st in ('OUT', 'IR', 'PUP', 'SUS'):
                outs += 1
    return outs


def compute_panel_projection(home_team: str, away_team: str,
                              season: int, week: int,
                              season_type: str = 'reg',
                              is_division_game: bool = False,
                              source: str = 'sleeper') -> dict:
    """Team-level Panel projection. Returns dict with home_pts, away_pts, total,
    plus diagnostic breakdown. Callers store this in nfl_game_context.panel_pred_*."""
    out = {
        'home_team': home_team, 'away_team': away_team,
        'season': season, 'week': week, 'source': source,
        'home_pts': None, 'away_pts': None, 'total': None,
        'home_off_raw': None, 'away_off_raw': None,
        'home_players_used': 0, 'away_players_used': 0,
        'home_injury_outs': 0, 'away_injury_outs': 0,
        'confidence': 0.0,
        'error': None,
    }
    # 2026-08-09: use ensemble (Sleeper + ESPN averaged) when source='ensemble'
    # or 'sleeper' (default) with ESPN as backup. Falls back to single-source
    # if only one is populated for this week.
    if source == 'ensemble':
        home_rows, h_sources = load_team_projections_ensemble(home_team, season, week, season_type)
        away_rows, a_sources = load_team_projections_ensemble(away_team, season, week, season_type)
        out['source_used'] = f'ensemble({h_sources})'
    else:
        home_rows = load_team_projections(home_team, season, week, season_type, source)
        away_rows = load_team_projections(away_team, season, week, season_type, source)
        out['source_used'] = source
    if not home_rows or not away_rows:
        out['error'] = f'missing projections: home={len(home_rows)} away={len(away_rows)}'
        return out

    h_pts, h_bd, h_used = _sum_team_fantasy_pts(home_rows)
    a_pts, a_bd, a_used = _sum_team_fantasy_pts(away_rows)
    h_outs = _count_starter_outs(home_rows)
    a_outs = _count_starter_outs(away_rows)

    # Convert to real pts
    h_off = h_pts * FANTASY_PTS_TO_REAL_PTS
    a_off = a_pts * FANTASY_PTS_TO_REAL_PTS

    # Add league-average defensive contribution
    h_final = h_off + LEAGUE_AVG_DEF_PTS
    a_final = a_off + LEAGUE_AVG_DEF_PTS

    # Injury penalty
    h_final *= (1 - INJURY_STARTER_PENALTY * h_outs)
    a_final *= (1 - INJURY_STARTER_PENALTY * a_outs)

    # Rivalry bump (division games — both sides play harder)
    if is_division_game:
        h_final *= (1 + DIVISION_RIVALRY_BUMP)
        a_final *= (1 + DIVISION_RIVALRY_BUMP)

    # Home field advantage
    h_final *= HOME_FIELD_MULT

    total = h_final + a_final
    # Confidence: how many players contributed, cap 0-1
    total_used = h_used + a_used
    confidence = min(1.0, total_used / 50.0)  # 50 players = full confidence

    out.update({
        'home_pts': round(h_final, 2), 'away_pts': round(a_final, 2),
        'total': round(total, 2),
        'home_off_raw': round(h_off, 2), 'away_off_raw': round(a_off, 2),
        'home_players_used': h_used, 'away_players_used': a_used,
        'home_injury_outs': h_outs, 'away_injury_outs': a_outs,
        'confidence': round(confidence, 2),
        'home_pos_breakdown': {k: round(v * FANTASY_PTS_TO_REAL_PTS, 1) for k, v in h_bd.items()},
        'away_pos_breakdown': {k: round(v * FANTASY_PTS_TO_REAL_PTS, 1) for k, v in a_bd.items()},
    })
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--home', required=True)
    ap.add_argument('--away', required=True)
    ap.add_argument('--season', type=int, required=True)
    ap.add_argument('--week', type=int, required=True)
    ap.add_argument('--season-type', default='reg')
    ap.add_argument('--division', action='store_true')
    ap.add_argument('--source', default='sleeper')
    args = ap.parse_args()
    result = compute_panel_projection(args.home, args.away, args.season, args.week,
                                       season_type=args.season_type,
                                       is_division_game=args.division,
                                       source=args.source)
    print(json.dumps(result, indent=2, default=str))
