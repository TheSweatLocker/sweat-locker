"""NBA data client — ESPN API (2026-08-17 rebuild).

Free public source, no auth, no anti-bot blocks. Handles:

  ESPN Sports API (site.api.espn.com) — free, reliable
    * Daily scoreboard + game IDs (works from any IP)
    * Team season records + standings
    * Boxscores (for resolver)
    * Odds (basic — books ESPN partners with)

Notes on why NOT stats.nba.com:
  * stats.nba.com aggressively blocks residential + serverless IPs
    (Cloudflare + rate limiting), returns hangs/timeouts.
  * ESPN endpoint is unauthenticated and works from GHA + local dev.
  * Trade-off: ESPN's odds coverage is thinner than a dedicated odds
    feed — we rely on The Odds API separately for market lines.

CLI (smoke tests):
  python nba_data_client.py schedule 2024-11-10
  python nba_data_client.py score 2024-11-10
  python nba_data_client.py teams
"""
from __future__ import annotations
import argparse, sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


ESPN_NBA_BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'


def _get(url: str, params: dict | None = None, timeout: float = 15, retries: int = 2) -> Optional[dict]:
    """GET with automatic retry on connection reset — ESPN sometimes
    resets when hit repeatedly. 2 retries with exponential backoff."""
    import time
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 200: return r.json()
            return None
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ChunkedEncodingError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)  # 1s, 2s
                continue
            return None
        except Exception:
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════
# Endpoint wrappers
# ═══════════════════════════════════════════════════════════════════════

def get_schedule(game_date: str) -> list[dict]:
    """Games on `game_date` (YYYY-MM-DD). Returns list of {game_id,
    home_team, away_team, home_abbrev, away_abbrev, commence_time_utc,
    venue, home_record, away_record}."""
    d = game_date.replace('-', '')  # ESPN wants YYYYMMDD
    data = _get(f'{ESPN_NBA_BASE}/scoreboard', {'dates': d})
    if not data: return []
    out = []
    for ev in data.get('events', []):
        comp = ev.get('competitions', [{}])[0]
        comps = comp.get('competitors', [])
        home = next((c for c in comps if c.get('homeAway') == 'home'), {})
        away = next((c for c in comps if c.get('homeAway') == 'away'), {})
        home_t = home.get('team', {})
        away_t = away.get('team', {})
        out.append({
            'game_id':          str(ev.get('id')),
            'game_date':        game_date,
            'commence_time_utc': ev.get('date'),
            'home_team_id':     home_t.get('id'),
            'away_team_id':     away_t.get('id'),
            'home_team':        home_t.get('displayName'),
            'away_team':        away_t.get('displayName'),
            'home_abbrev':      home_t.get('abbreviation'),
            'away_abbrev':      away_t.get('abbreviation'),
            'home_record':      (home.get('records') or [{}])[0].get('summary'),
            'away_record':      (away.get('records') or [{}])[0].get('summary'),
            'venue':            comp.get('venue', {}).get('fullName'),
            'neutral_site':     comp.get('neutralSite', False),
        })
    return out


def get_scoreboard(game_date: str) -> list[dict]:
    """Finalized game scores for resolver. Returns list of {game_id,
    home_score, away_score, went_to_ot, home_team, away_team, home_win}."""
    d = game_date.replace('-', '')
    data = _get(f'{ESPN_NBA_BASE}/scoreboard', {'dates': d})
    if not data: return []
    out = []
    for ev in data.get('events', []):
        comp = ev.get('competitions', [{}])[0]
        status = ev.get('status', {}).get('type', {})
        # completed status
        if not status.get('completed'): continue
        comps = comp.get('competitors', [])
        home = next((c for c in comps if c.get('homeAway') == 'home'), {})
        away = next((c for c in comps if c.get('homeAway') == 'away'), {})
        try: hs = int(home.get('score', 0))
        except (TypeError, ValueError): hs = None
        try: as_ = int(away.get('score', 0))
        except (TypeError, ValueError): as_ = None
        # OT detection — status.period > 4 means overtime
        period = status.get('period', 0)
        went_to_ot = period > 4
        out.append({
            'game_id':      str(ev.get('id')),
            'home_team':    home.get('team', {}).get('displayName'),
            'away_team':    away.get('team', {}).get('displayName'),
            'home_abbrev':  home.get('team', {}).get('abbreviation'),
            'away_abbrev':  away.get('team', {}).get('abbreviation'),
            'home_score':   hs,
            'away_score':   as_,
            'total_points': (hs + as_) if hs is not None and as_ is not None else None,
            'home_win':     hs > as_ if hs is not None and as_ is not None else None,
            'went_to_ot':   went_to_ot,
        })
    return out


def get_player_boxscores(game_date: str, finals_only: bool = True) -> dict:
    """Player stat lines for every NBA game on `game_date`.

    Returns {player_name_lower: {pts, reb, ast, threes, blocks, steals,
    turnovers, pra, minutes, played}} — the keys grade_props needs.

    PLAYED IS THE POINT. Every grading bug found on 2026-09-19 was the
    same shape: a player who never appeared scored 0, and 0 silently
    graded an UNDER as a Win (NFL C/ATT off 0.0; Zach Thornton
    outs_under 16.5 graded Win on final_value 0). A DNP is not a low
    stat line, it is a void bet — so `played` is reported explicitly and
    callers must check it instead of inferring from a zero.

    ESPN marks this two ways and both are honoured: an explicit
    `didNotPlay` flag, and a missing/zero `minutes` entry.

    finals_only: skip games still in progress, so a player at 4 points in
    the 2nd quarter is never graded under 20.5.
    """
    d = game_date.replace('-', '')
    board = _get(f'{ESPN_NBA_BASE}/scoreboard', params={'dates': d})
    if not board:
        return {}
    event_ids = []
    for e in board.get('events', []):
        comp = (e.get('competitions') or [{}])[0]
        completed = (comp.get('status', {}).get('type', {}) or {}).get('completed')
        if finals_only and not completed:
            continue
        event_ids.append(e.get('id'))

    # ESPN label -> our stat key. Read by LABEL, not by index: the stats
    # array is positional and a layout change would silently shift every
    # value one column over, which is the kind of error that grades as a
    # plausible number instead of an obvious crash.
    _WANT = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'BLK': 'blocks',
             'STL': 'steals', 'TO': 'turnovers', '3PT': 'threes',
             'MIN': 'minutes'}

    out: dict = {}
    for eid in event_ids:
        summary = _get(f'{ESPN_NBA_BASE}/summary', params={'event': eid})
        if not summary:
            continue
        for team in (summary.get('boxscore', {}).get('players') or []):
            for grp in (team.get('statistics') or []):
                labels = grp.get('labels') or []
                for a in (grp.get('athletes') or []):
                    name = (a.get('athlete') or {}).get('displayName')
                    if not name:
                        continue
                    vals = a.get('stats') or []
                    row: dict = {}
                    for lbl, val in zip(labels, vals):
                        key = _WANT.get(lbl)
                        if key is None:
                            continue
                        if key == 'threes':
                            # "3-7" -> made
                            try: row[key] = int(str(val).split('-')[0])
                            except (ValueError, IndexError): row[key] = None
                        elif key == 'minutes':
                            try: row[key] = int(str(val))
                            except (ValueError, TypeError): row[key] = 0
                        else:
                            try: row[key] = int(str(val))
                            except (ValueError, TypeError): row[key] = None
                    dnp = bool(a.get('didNotPlay'))
                    played = (not dnp) and bool(vals) and (row.get('minutes') or 0) > 0
                    row['played'] = played
                    if row.get('pts') is not None and row.get('reb') is not None \
                       and row.get('ast') is not None:
                        row['pra'] = row['pts'] + row['reb'] + row['ast']
                    else:
                        row['pra'] = None
                    out[name.lower()] = row
    return out


def get_teams() -> list[dict]:
    """All 30 NBA teams. Returns list of {team_id, abbrev, name}."""
    data = _get(f'{ESPN_NBA_BASE}/teams')
    if not data: return []
    out = []
    for sport in data.get('sports', []):
        for league in sport.get('leagues', []):
            for t in league.get('teams', []):
                team = t.get('team', {})
                out.append({
                    'team_id': team.get('id'),
                    'abbrev':  team.get('abbreviation'),
                    'name':    team.get('displayName'),
                    'location': team.get('location'),
                    'nickname': team.get('name'),
                })
    return out


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['schedule', 'score', 'teams'])
    p.add_argument('args', nargs='*')
    args = p.parse_args()
    import json
    if args.cmd == 'schedule':
        gd = args.args[0] if args.args else datetime.now().date().isoformat()
        print(json.dumps(get_schedule(gd), indent=2, default=str))
    elif args.cmd == 'score':
        gd = args.args[0] if args.args else datetime.now().date().isoformat()
        print(json.dumps(get_scoreboard(gd), indent=2, default=str))
    elif args.cmd == 'teams':
        print(json.dumps(get_teams(), indent=2, default=str)[:3000])


if __name__ == '__main__':
    main()
