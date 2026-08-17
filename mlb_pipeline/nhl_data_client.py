"""NHL data client — NHL API + MoneyPuck (2026-08-16).

Two free data sources, one abstraction. Handles:

  NHL API (api-web.nhle.com/v1) — free, no key needed
    * Daily schedule + confirmed starters
    * Team + player stats
    * Live scoreboard for resolver
    * Standings

  MoneyPuck (moneypuck.com) — free CSV downloads
    * Advanced team analytics (xGF/60, xGA/60, high-danger, corsi)
    * Player advanced stats (goalie GSAA, etc.)

Cached responses per-run to avoid hammering endpoints. Returns clean
dicts ready to write to nhl_game_context.

CLI (smoke tests):
  python nhl_data_client.py schedule 2026-10-07
  python nhl_data_client.py team-stats TOR 2027
  python nhl_data_client.py goalie-stats "Igor Shesterkin" 2027
  python nhl_data_client.py score 2026-10-07
"""
from __future__ import annotations
import argparse, csv, io, sys
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


NHL_API_BASE = 'https://api-web.nhle.com/v1'
MP_BASE = 'https://moneypuck.com/moneypuck/playerData/seasonSummary'
USER_AGENT = 'SweatLocker/1.0 (data-collection)'

# ═══════════════════════════════════════════════════════════════════════
# NHL API helpers
# ═══════════════════════════════════════════════════════════════════════

def _get(url: str, timeout: float = 15) -> Optional[dict]:
    try:
        r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def get_schedule(game_date: str) -> list[dict]:
    """Return list of games on `game_date` (YYYY-MM-DD).

    Each game dict includes:
      game_id, home_abbrev, away_abbrev, home_team, away_team,
      commence_time (UTC), venue, home_goalie, away_goalie (if confirmed).
    """
    data = _get(f'{NHL_API_BASE}/schedule/{game_date}')
    if not data or 'gameWeek' not in data:
        return []
    out = []
    for day in data.get('gameWeek', []):
        if day.get('date') != game_date:
            continue
        for g in day.get('games', []):
            home = g.get('homeTeam', {})
            away = g.get('awayTeam', {})
            out.append({
                'game_id': str(g.get('id')),
                'commence_time': g.get('startTimeUTC'),
                'venue': g.get('venue', {}).get('default'),
                'home_team_abbrev': home.get('abbrev'),
                'away_team_abbrev': away.get('abbrev'),
                'home_team': home.get('placeName', {}).get('default'),
                'away_team': away.get('placeName', {}).get('default'),
                # goalies exist on gamecenter, not schedule — enrich via
                # get_gamecenter(game_id) at lineup-lock time.
            })
    return out


def get_gamecenter(game_id: str) -> Optional[dict]:
    """Full game center data — includes confirmed starters, injuries,
    line combos. Useful once lineups are set (~1h before puck drop)."""
    return _get(f'{NHL_API_BASE}/gamecenter/{game_id}/landing')


def get_scoreboard(game_date: str) -> list[dict]:
    """Final scores for resolver. Returns list of {game_id, home_score,
    away_score, went_to_ot, went_to_so}."""
    data = _get(f'{NHL_API_BASE}/scoreboard/{game_date}')
    if not data:
        return []
    out = []
    for g in data.get('games', []):
        state = g.get('gameState', '')
        if state not in ('OFF', 'FINAL', 'OFFICIAL'):
            continue  # still in progress or scheduled
        period = g.get('periodDescriptor', {}).get('periodType', 'REG')
        out.append({
            'game_id': str(g.get('id')),
            'home_score': g.get('homeTeam', {}).get('score'),
            'away_score': g.get('awayTeam', {}).get('score'),
            'went_to_ot': period == 'OT',
            'went_to_so': period == 'SO',
        })
    return out


def get_team_stats(team_abbrev: str, season: int) -> Optional[dict]:
    """Season stats for a team. season = 20262027 format (start_year *
    10000 + end_year). Returns dict with pp_pct, pk_pct, and record."""
    # NHL API season format: '20262027'
    season_str = f'{season}{season+1}' if season < 3000 else str(season)
    data = _get(f'{NHL_API_BASE}/club-stats/{team_abbrev}/{season_str}/2')  # 2 = regular season
    if not data:
        return None
    skater_totals = {}
    for s in data.get('skaters', []):
        # Sum team-level counts
        for k in ('goals', 'assists', 'plusMinus', 'penaltyMinutes'):
            skater_totals[k] = skater_totals.get(k, 0) + (s.get(k) or 0)
    return {
        'games_played': data.get('gamesPlayed'),
        'wins': data.get('wins'),
        'losses': data.get('losses'),
        'ot_losses': data.get('otLosses'),
        'goals_for_per_game': data.get('goalsForPerGame'),
        'goals_against_per_game': data.get('goalsAgainstPerGame'),
        'pp_pct': data.get('powerPlayPct'),
        'pk_pct': data.get('penaltyKillPct'),
    }


def get_goalie_stats(goalie_name: str, season: int) -> Optional[dict]:
    """Look up goalie season stats + L5 form. Returns dict with sv_pct,
    gsaa, l5_sv_pct. Best-effort — NHL API endpoint changes seasonally."""
    # NHL API doesn't have a direct name-lookup — need player_id first.
    # For MVP, fall back to MoneyPuck goalie CSV which has fuzzy names.
    try:
        season_str = f'{season}' if season >= 3000 else f'{season}'
        url = f'{MP_BASE}/{season_str}/regular/goalies.csv'
        r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return None
        reader = csv.DictReader(io.StringIO(r.text))
        goalie_last = goalie_name.split()[-1].lower()
        for row in reader:
            row_name = (row.get('name') or '').lower()
            if goalie_last in row_name:
                # MoneyPuck fields: 'GSAx' (goals saved above expected), 'ice_situation'
                # Filter to 'all' situations
                if row.get('situation') and row['situation'] != 'all': continue
                try:
                    return {
                        'sv_pct': float(row.get('savePercentage', 0) or 0),
                        'gsaa': float(row.get('goalsSavedAboveExpected', 0) or 0),
                        'games': int(row.get('gamesPlayed', 0) or 0),
                    }
                except (TypeError, ValueError):
                    continue
        return None
    except Exception:
        return None


def get_team_analytics_mp(team_abbrev: str, season: int) -> Optional[dict]:
    """MoneyPuck team analytics — xGF/60, xGA/60, high-danger, corsi.
    These are the advanced stats the NHL API doesn't expose."""
    try:
        season_str = f'{season}' if season >= 3000 else f'{season}'
        url = f'{MP_BASE}/{season_str}/regular/teams.csv'
        r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return None
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            if row.get('team') != team_abbrev: continue
            if row.get('situation') and row['situation'] != '5on5': continue
            try:
                return {
                    'xgf_per60': float(row.get('xGoalsFor', 0) or 0) / max(float(row.get('iceTime', 1) or 1)/3600, 0.01),
                    'xga_per60': float(row.get('xGoalsAgainst', 0) or 0) / max(float(row.get('iceTime', 1) or 1)/3600, 0.01),
                    'high_danger_for': float(row.get('highDangerShotsFor', 0) or 0),
                    'high_danger_against': float(row.get('highDangerShotsAgainst', 0) or 0),
                    'corsi_for_pct': float(row.get('corsiPercentage', 0) or 0),
                }
            except (TypeError, ValueError):
                continue
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
# CLI smoke tests
# ═══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['schedule', 'gamecenter', 'score',
                                    'team-stats', 'goalie-stats', 'team-analytics'])
    p.add_argument('arg1', help='date / game_id / team / goalie name')
    p.add_argument('arg2', nargs='?', help='season if applicable')
    args = p.parse_args()
    import json
    if args.cmd == 'schedule':
        out = get_schedule(args.arg1)
    elif args.cmd == 'gamecenter':
        out = get_gamecenter(args.arg1)
    elif args.cmd == 'score':
        out = get_scoreboard(args.arg1)
    elif args.cmd == 'team-stats':
        out = get_team_stats(args.arg1, int(args.arg2 or 2026))
    elif args.cmd == 'goalie-stats':
        out = get_goalie_stats(args.arg1, int(args.arg2 or 2026))
    elif args.cmd == 'team-analytics':
        out = get_team_analytics_mp(args.arg1, int(args.arg2 or 2026))
    else:
        out = None
    print(json.dumps(out, indent=2, default=str))


if __name__ == '__main__':
    main()
