"""Enrich today's ncaab_game_context rows with season team trends (2026-08-17).

Reads ncaab_team_trends (populated by pull_ncaab_teamrankings_trends.py)
and copies home + away season cover_pct / over_pct / ATS record / O/U
record onto each ncaab_game_context row for `game_date`.

This is the "join" step — signals in signal_sources read from
ctx.home_season_cover_pct etc., which are populated here.

Runs AFTER ncaab_game_context.py (rows exist) and AFTER
pull_ncaab_teamrankings_trends.py (source data current) in the workflow.

Team name matching: teamrankings uses display names like "Arizona",
"St John's", "Michigan St". ncaab_game_context may use slightly
different names (KenPom style, ESPN style). Uses fuzzy fallback:
  1. Exact match
  2. Case-insensitive
  3. Strip common suffixes (Wildcats, State → St, University)

CLI:
  python enrich_ncaab_team_trends.py                # today
  python enrich_ncaab_team_trends.py --date 2026-11-04
  python enrich_ncaab_team_trends.py --dry-run
"""
from __future__ import annotations
import argparse, os, re, sys
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _norm(name: str) -> str:
    """Canonical form for team-name matching."""
    if not name: return ''
    s = name.lower().strip()
    s = re.sub(r'\bstate\b', 'st', s)
    s = re.sub(r'\buniversity\b', '', s)
    s = re.sub(r'\bcollege\b', '', s)
    # Strip common mascot suffixes we know teamrankings drops
    for suffix in [' wildcats', ' bulldogs', ' tigers', ' bears', ' eagles',
                   ' cardinals', ' hurricanes', ' longhorns', ' sooners',
                   ' aggies', ' cougars', ' rebels', ' spartans', ' hawkeyes',
                   ' badgers', ' terrapins', ' wolverines']:
        if s.endswith(suffix): s = s[:-len(suffix)]
    s = re.sub(r'[^a-z0-9]+', '', s)
    return s


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _current_season_label() -> str:
    now = datetime.now()
    if now.month >= 8:
        return f'{now.year}-{str(now.year+1)[-2:]}'
    return f'{now.year-1}-{str(now.year)[-2:]}'


def run(game_date: str | None = None, season: str | None = None,
        dry_run: bool = False):
    gd = game_date or _et_today()
    season = season or _current_season_label()
    print(f'=== enrich ncaab team trends · {gd} · season {season} ===')

    # 1. Load all trends for the season into an index by normalized team name
    r = requests.get(f'{SB}/rest/v1/ncaab_team_trends'
                     f'?season=eq.{season}&select=*&limit=500',
                     headers=H_READ, timeout=15)
    trends = r.json() if r.status_code == 200 else []
    print(f'  {len(trends)} team trends loaded for season {season}')
    if not trends:
        print(f'  no trends yet — run pull_ncaab_teamrankings_trends.py --season {season} first')
        return

    trends_by_key: dict = {}
    for t in trends:
        trends_by_key[_norm(t['team'])] = t

    # 2. Fetch today's ncaab_game_context rows
    r = requests.get(f'{SB}/rest/v1/ncaab_game_context'
                     f'?game_date=eq.{gd}&select=game_id,home_team,away_team',
                     headers=H_READ, timeout=15)
    games = r.json() if r.status_code == 200 else []
    if not games:
        print(f'  no games on {gd}')
        return
    print(f'  {len(games)} games on {gd}')

    def _match(team: str) -> dict | None:
        key = _norm(team)
        return trends_by_key.get(key)

    now_iso = datetime.now(timezone.utc).isoformat()
    matched_h = matched_a = 0
    for g in games:
        h_tr = _match(g.get('home_team',''))
        a_tr = _match(g.get('away_team',''))
        if h_tr: matched_h += 1
        if a_tr: matched_a += 1

        patch = {
            'home_season_cover_pct':   h_tr.get('cover_pct') if h_tr else None,
            'home_season_ats_wins':    h_tr.get('ats_wins') if h_tr else None,
            'home_season_ats_losses':  h_tr.get('ats_losses') if h_tr else None,
            'home_season_over_pct':    h_tr.get('over_pct') if h_tr else None,
            'home_season_ou_overs':    h_tr.get('ou_overs') if h_tr else None,
            'home_season_ou_unders':   h_tr.get('ou_unders') if h_tr else None,
            'away_season_cover_pct':   a_tr.get('cover_pct') if a_tr else None,
            'away_season_ats_wins':    a_tr.get('ats_wins') if a_tr else None,
            'away_season_ats_losses':  a_tr.get('ats_losses') if a_tr else None,
            'away_season_over_pct':    a_tr.get('over_pct') if a_tr else None,
            'away_season_ou_overs':    a_tr.get('ou_overs') if a_tr else None,
            'away_season_ou_unders':   a_tr.get('ou_unders') if a_tr else None,
            'team_trends_updated_at':  now_iso,
        }
        note = (f'  {g["away_team"][:20]:<20} @ {g["home_team"][:20]:<20}  '
                f'home={h_tr["cover_pct"] if h_tr else "?"}% away={a_tr["cover_pct"] if a_tr else "?"}%')
        print(note)

        if dry_run: continue
        pr = requests.patch(f'{SB}/rest/v1/ncaab_game_context?game_id=eq.{g["game_id"]}',
                            headers=H_WRITE, json=patch, timeout=15)
        if pr.status_code not in (200, 204):
            print(f'    ✗ patch failed: {pr.status_code} {pr.text[:120]}')

    print(f'\n  match rate: home {matched_h}/{len(games)} · away {matched_a}/{len(games)}')
    if matched_h + matched_a < len(games):
        print(f'  ⚠ mismatched names — check _norm() suffix logic for unmatched teams')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--season', help='Season label like 2025-26 (default: current)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
