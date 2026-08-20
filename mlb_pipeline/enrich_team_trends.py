"""Universal team_season_trends → game_context enrichment (2026-08-17).

For today's games in a given sport, joins team_season_trends onto
per-game ctx rows so playbook signals can read via ctx.home_season_cover_pct etc.

Sport-universal: MLB / NFL / NCAAF / NCAAB / NBA.

Runs AFTER pull_teamrankings_trends.py (source data current) and AFTER
<sport>_game_context.py (rows exist to patch).

CLI:
  python enrich_team_trends.py --sport NCAAF
  python enrich_team_trends.py --sport NFL --date 2026-09-08
  python enrich_team_trends.py --all
  python enrich_team_trends.py --sport MLB --dry-run
"""
from __future__ import annotations
import argparse, os, re, sys
from datetime import datetime, timezone, timedelta
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

CTX_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    'NBA':   'nba_game_context',
}


def _norm(name: str) -> str:
    if not name: return ''
    s = name.lower().strip()
    s = re.sub(r'\bstate\b', 'st', s)
    s = re.sub(r'\buniversity\b', '', s)
    s = re.sub(r'\bcollege\b', '', s)
    # 2026-08-20: MLB alias table added because teamrankings uses
    # short forms ("Chi Sox", "Chi Cubs") + city-only names ("Arizona",
    # "Boston") while game_context stores full names ("Chicago White Sox",
    # "Chicago Cubs", "Arizona Diamondbacks", "Boston Red Sox"). Zero
    # matches at MLB pre-fix. Explicit aliases override normalization.
    MLB_ALIASES = {
        'arizona diamondbacks': 'arizona', 'atlanta braves': 'atlanta',
        'baltimore orioles': 'baltimore', 'boston red sox': 'boston',
        'chicago cubs': 'chicubs', 'chicago white sox': 'chisox',
        'chi cubs': 'chicubs', 'chi sox': 'chisox', 'chi white sox': 'chisox',
        'cincinnati reds': 'cincinnati', 'cleveland guardians': 'cleveland',
        'colorado rockies': 'colorado', 'detroit tigers': 'detroit',
        'houston astros': 'houston', 'kansas city royals': 'kansascity',
        'los angeles angels': 'laangels', 'la angels': 'laangels',
        'los angeles dodgers': 'ladodgers', 'la dodgers': 'ladodgers',
        'miami marlins': 'miami', 'milwaukee brewers': 'milwaukee',
        'minnesota twins': 'minnesota', 'new york mets': 'nymets', 'ny mets': 'nymets',
        'new york yankees': 'nyyankees', 'ny yankees': 'nyyankees',
        'athletics': 'oakland', 'oakland athletics': 'oakland',
        'philadelphia phillies': 'philadelphia', 'pittsburgh pirates': 'pittsburgh',
        'san diego padres': 'sandiego', 'san francisco giants': 'sanfrancisco',
        'seattle mariners': 'seattle', 'st. louis cardinals': 'stlouis',
        'st louis cardinals': 'stlouis', 'tampa bay rays': 'tampabay',
        'texas rangers': 'texas', 'toronto blue jays': 'toronto',
        'washington nationals': 'washington',
    }
    if s in MLB_ALIASES:
        return MLB_ALIASES[s]
    # Common mascot suffixes teamrankings drops (NCAAF/NCAAB heavy)
    for suffix in [' wildcats', ' bulldogs', ' tigers', ' bears', ' eagles',
                   ' cardinals', ' hurricanes', ' longhorns', ' sooners',
                   ' aggies', ' cougars', ' rebels', ' spartans', ' hawkeyes',
                   ' badgers', ' terrapins', ' wolverines', ' cavaliers',
                   ' seminoles', ' commodores', ' bruins', ' huskies',
                   ' gators', ' rams', ' knights', ' panthers']:
        if s.endswith(suffix): s = s[:-len(suffix)]
    s = re.sub(r'[^a-z0-9]+', '', s)
    return s


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _current_season(sport: str) -> str:
    now = datetime.now()
    if sport in ('NCAAB', 'NBA'):
        return f'{now.year}-{str(now.year+1)[-2:]}' if now.month >= 8 else f'{now.year-1}-{str(now.year)[-2:]}'
    if sport in ('NFL', 'NCAAF'):
        return str(now.year if now.month >= 7 else now.year - 1)
    return str(now.year)


def run_for_sport(sport: str, game_date: str, season: str, dry_run: bool = False) -> tuple[int, int, int]:
    """Returns (games_patched, home_matched, away_matched)."""
    ctx_tbl = CTX_TABLE.get(sport)
    if not ctx_tbl:
        print(f'  [{sport}] no ctx table registered — skip')
        return 0, 0, 0

    # 1. Load trends for season
    r = requests.get(f'{SB}/rest/v1/team_season_trends'
                     f'?sport=eq.{sport}&season=eq.{season}&select=*&limit=500',
                     headers=H_READ, timeout=15)
    trends = r.json() if r.status_code == 200 else []
    if not trends:
        print(f'  [{sport}] no trends yet for {season} — run pull_teamrankings_trends.py --sport {sport} first')
        return 0, 0, 0

    trends_by_key = {_norm(t['team']): t for t in trends}

    # 2. Today's games
    r = requests.get(f'{SB}/rest/v1/{ctx_tbl}'
                     f'?game_date=eq.{game_date}&select=game_id,home_team,away_team',
                     headers=H_READ, timeout=15)
    games = r.json() if r.status_code == 200 else []
    if not games:
        print(f'  [{sport}] no games on {game_date}')
        return 0, 0, 0

    now_iso = datetime.now(timezone.utc).isoformat()
    matched_h = matched_a = written = 0
    for g in games:
        h_tr = trends_by_key.get(_norm(g.get('home_team','')))
        a_tr = trends_by_key.get(_norm(g.get('away_team','')))
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
        if dry_run:
            print(f'  [DRY] {g["away_team"][:20]:<20} @ {g["home_team"][:20]:<20}  '
                  f'H={patch["home_season_cover_pct"] or "?"}%  A={patch["away_season_cover_pct"] or "?"}%')
            written += 1; continue
        pr = requests.patch(f'{SB}/rest/v1/{ctx_tbl}?game_id=eq.{g["game_id"]}',
                            headers=H_WRITE, json=patch, timeout=15)
        if pr.status_code in (200, 204):
            written += 1
        else:
            print(f'    ✗ patch {g["game_id"]}: {pr.status_code} {pr.text[:120]}')
    print(f'  [{sport}] {written}/{len(games)} patched · home match {matched_h}/{len(games)} · away {matched_a}/{len(games)}')
    return written, matched_h, matched_a


def run(sport: str | None, game_date: str | None = None,
        season: str | None = None, dry_run: bool = False):
    gd = game_date or _et_today()
    sports = list(CTX_TABLE.keys()) if sport == 'ALL' else [sport]
    for sp in sports:
        sn = season or _current_season(sp)
        print(f'\n=== enrich team trends · {sp} · {gd} · season {sn} ===')
        run_for_sport(sp, gd, sn, dry_run=dry_run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', help=f'One of {list(CTX_TABLE.keys())} or ALL')
    p.add_argument('--all', action='store_true')
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--season', help='Season label (auto-detected)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport='ALL' if args.all else args.sport,
        game_date=args.date, season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
