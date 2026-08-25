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
