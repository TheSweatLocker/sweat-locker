"""Universal teamrankings.com ATS + O/U trend scraper (2026-08-17).

Sport-universal: pulls team-level season aggregates from teamrankings for
NCAAB / NCAAF / NFL / NBA / MLB. Writes to universal team_season_trends
table.

Sport slug map:
  NCAAB → ncb
  NCAAF → ncf
  NFL   → nfl
  NBA   → nba
  MLB   → mlb
  (NHL not published on teamrankings)

CLI:
  python pull_teamrankings_trends.py --sport NCAAF                  # current season
  python pull_teamrankings_trends.py --sport NFL --season 2024
  python pull_teamrankings_trends.py --sport MLB                    # current MLB season
  python pull_teamrankings_trends.py --all --dry-run                # every supported sport
"""
from __future__ import annotations
import argparse, os, re, sys
from datetime import datetime, timezone
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

BASE = 'https://www.teamrankings.com'
UA = 'Mozilla/5.0 (SweatLocker research; contact via app)'

SPORT_SLUG = {
    'NCAAB': 'ncb',
    'NCAAF': 'ncf',
    'NFL':   'nfl',
    'NBA':   'nba',
    'MLB':   'mlb',
}


def _fetch_table(url: str) -> list[list[str]]:
    r = requests.get(url, headers={'User-Agent': UA}, timeout=15)
    if r.status_code != 200: return []
    m = re.search(r'<table[^>]*>(.*?)</table>', r.text, re.DOTALL)
    if not m: return []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.DOTALL)
    out = []
    for row in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
        clean = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c)).strip() for c in cells]
        clean = [c.replace('&#039;', "'").replace('&amp;', '&') for c in clean]
        if clean: out.append(clean)
    return out


def _parse_record(rec: str) -> tuple[int, int, int]:
    parts = rec.split('-')
    try:
        w = int(parts[0]); l = int(parts[1])
        p = int(parts[2]) if len(parts) >= 3 else 0
        return w, l, p
    except (ValueError, IndexError):
        return 0, 0, 0


def _parse_pct(s: str) -> float | None:
    m = re.match(r'([\d.]+)%?', s or '')
    return float(m.group(1)) if m else None


def _parse_signed_num(s: str) -> float | None:
    m = re.search(r'-?\d+\.?\d*', s or '')
    return float(m.group()) if m else None


def _season_range_param(sport: str, season: str) -> str:
    """Build teamrankings ?range= param. Basketball/hockey seasons cross
    years (2025-26); football/MLB are single-year."""
    # If season has a dash → cross-year (basketball style)
    if '-' in season:
        yrs = season.split('-')
        y1 = f'20{yrs[0][-2:]}' if len(yrs[0]) == 2 else yrs[0]
        y2 = f'20{yrs[1]}' if len(yrs[1]) == 2 else yrs[1]
        return f'yearly_{y1}_{y2}'
    # Single-year (NFL, NCAAF, MLB)
    return f'yearly_{season}_{season}'


def scrape_sport(sport: str, season: str) -> list[dict]:
    slug = SPORT_SLUG[sport]
    range_param = _season_range_param(sport, season)
    ats_url = f'{BASE}/{slug}/trends/ats_trends/?range={range_param}'
    ou_url  = f'{BASE}/{slug}/trends/ou_trends/?range={range_param}'
    ats_rows = _fetch_table(ats_url)
    ou_rows  = _fetch_table(ou_url)
    print(f'  [{sport}] ATS rows: {max(0,len(ats_rows)-1)} · O/U rows: {max(0,len(ou_rows)-1)}')

    teams: dict = {}
    for row in ats_rows[1:] if ats_rows else []:
        if len(row) < 5: continue
        team = row[0].strip()
        if not team: continue
        w, l, p = _parse_record(row[1])
        teams.setdefault(team, {'team': team, 'sport': sport, 'season': season})
        teams[team].update({
            'ats_wins': w, 'ats_losses': l, 'ats_pushes': p,
            'cover_pct': _parse_pct(row[2]),
            'mov': _parse_signed_num(row[3]),
            'ats_plus_minus': _parse_signed_num(row[4]),
        })
    for row in ou_rows[1:] if ou_rows else []:
        if len(row) < 5: continue
        team = row[0].strip()
        if not team: continue
        w, l, p = _parse_record(row[1])
        teams.setdefault(team, {'team': team, 'sport': sport, 'season': season})
        teams[team].update({
            'ou_overs': w, 'ou_unders': l, 'ou_pushes': p,
            'over_pct': _parse_pct(row[2]),
            'total_plus_minus': _parse_signed_num(row[4]),
        })
    return list(teams.values())


def upsert(rows: list[dict], dry_run: bool = False) -> int:
    if not rows: return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in rows: r['updated_at'] = now_iso
    if dry_run:
        for r in rows[:8]:
            print(f'  [DRY] {r.get("sport"):<6} {r.get("team"):<22} '
                  f'ATS {r.get("ats_wins")}-{r.get("ats_losses")} ({r.get("cover_pct")}%) '
                  f'O/U {r.get("ou_overs") or "?"}o-{r.get("ou_unders") or "?"}u '
                  f'({r.get("over_pct") or "?"}%)')
        print(f'  [DRY] would upsert {len(rows)} rows')
        return len(rows)
    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        pr = requests.post(f'{SB}/rest/v1/team_season_trends?on_conflict=sport,team,season',
                           headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204): written += len(chunk)
        else: print(f'  ✗ upsert failed: {pr.status_code} {pr.text[:200]}')
    return written


def _current_season(sport: str) -> str:
    now = datetime.now()
    # Cross-year sports (basketball, hockey)
    if sport in ('NCAAB', 'NBA'):
        if now.month >= 8: return f'{now.year}-{str(now.year+1)[-2:]}'
        return f'{now.year-1}-{str(now.year)[-2:]}'
    # Football uses starting year (2024 = 2024-25 season)
    if sport in ('NFL', 'NCAAF'):
        return str(now.year if now.month >= 7 else now.year - 1)
    # MLB uses calendar year
    return str(now.year)


def run(sport: str | None, season: str | None = None, dry_run: bool = False):
    if sport == 'ALL':
        for sp in SPORT_SLUG.keys():
            print(f'\n=== {sp} ===')
            rows = scrape_sport(sp, season or _current_season(sp))
            upsert(rows, dry_run=dry_run)
        return
    if not sport or sport not in SPORT_SLUG:
        raise SystemExit(f'--sport required, one of {list(SPORT_SLUG.keys())} or ALL')
    season = season or _current_season(sport)
    print(f'=== pull teamrankings trends · {sport} · {season} ===')
    rows = scrape_sport(sport, season)
    written = upsert(rows, dry_run=dry_run)
    print(f'  {"[DRY] " if dry_run else ""}upserted {written} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', help=f'One of {list(SPORT_SLUG.keys())} or ALL')
    p.add_argument('--all', action='store_true', help='All supported sports')
    p.add_argument('--season', help='Season label (auto-detected if omitted)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport='ALL' if args.all else args.sport, season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
