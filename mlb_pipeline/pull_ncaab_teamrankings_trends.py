"""Scrape NCAAB team ATS + O/U season trends from teamrankings.com (2026-08-17).

Unblocks NCAAB tendencies which are blocked from per-game backfill by
missing spread/total data on ncaab_game_results (0/5911 games have odds).

Season-level aggregates from teamrankings:
  * /ncb/trends/ats_trends/?range=yearly_YYYY_YYYY → Team | ATS Record | Cover % | MOV | ATS +/-
  * /ncb/trends/ou_trends/?range=yearly_YYYY_YYYY  → Team | Over Record | Over % | Under % | Total +/-

Merges both into ncaab_team_trends table. Weekly cron during NCAAB season
(Nov-Apr). Enrichment step in ncaab_game_context.py joins these onto
per-game rows so signals read via ctx.home_season_cover_pct etc.

CLI:
  python pull_ncaab_teamrankings_trends.py                    # current season
  python pull_ncaab_teamrankings_trends.py --season 2024-25   # historical
  python pull_ncaab_teamrankings_trends.py --dry-run
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

BASE = 'https://www.teamrankings.com/ncb/trends'
UA = 'Mozilla/5.0 (SweatLocker research; contact via app)'


def _fetch_table(url: str) -> list[list[str]]:
    """Return the first HTML table as list of rows (each row = list of cells)."""
    r = requests.get(url, headers={'User-Agent': UA}, timeout=15)
    if r.status_code != 200: return []
    m = re.search(r'<table[^>]*>(.*?)</table>', r.text, re.DOTALL)
    if not m: return []
    tbody = m.group(1)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody, re.DOTALL)
    out = []
    for row in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
        # Strip HTML tags + entities from each cell
        clean = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c)).strip() for c in cells]
        clean = [c.replace('&#039;', "'").replace('&amp;', '&') for c in clean]
        if clean: out.append(clean)
    return out


def _parse_record(rec: str) -> tuple[int, int, int]:
    """'22-8-1' → (22, 8, 1). Handles '22-8-0' or '22-8'."""
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


def scrape_season(season_label: str) -> list[dict]:
    """Scrape ATS + O/U trends for a season and merge by team.

    season_label ex: '2025-26' → range param 'yearly_2025_2026'."""
    yrs = season_label.split('-')
    range_param = f'yearly_20{yrs[0][-2:]}_20{yrs[1]}' if len(yrs) == 2 else f'yearly_{yrs[0]}_{yrs[0]}'

    ats_url = f'{BASE}/ats_trends/?range={range_param}'
    ou_url  = f'{BASE}/ou_trends/?range={range_param}'

    ats_rows = _fetch_table(ats_url)
    ou_rows  = _fetch_table(ou_url)
    print(f'  ATS table: {len(ats_rows)-1 if ats_rows else 0} teams')
    print(f'  O/U table: {len(ou_rows)-1 if ou_rows else 0} teams')

    teams: dict = {}
    # ATS table: Team | ATS Record | Cover % | MOV | ATS +/-
    for row in ats_rows[1:] if ats_rows else []:
        if len(row) < 5: continue
        team = row[0].strip()
        if not team: continue
        w, l, p = _parse_record(row[1])
        teams.setdefault(team, {})['team'] = team
        teams[team].update({
            'ats_wins': w, 'ats_losses': l, 'ats_pushes': p,
            'cover_pct': _parse_pct(row[2]),
            'mov': _parse_signed_num(row[3]),
            'ats_plus_minus': _parse_signed_num(row[4]),
        })

    # O/U table: Team | Over Record | Over % | Under % | Total +/-
    for row in ou_rows[1:] if ou_rows else []:
        if len(row) < 5: continue
        team = row[0].strip()
        if not team: continue
        w, l, p = _parse_record(row[1])
        teams.setdefault(team, {})['team'] = team
        teams[team].update({
            'ou_overs': w, 'ou_unders': l, 'ou_pushes': p,
            'over_pct': _parse_pct(row[2]),
            'total_plus_minus': _parse_signed_num(row[4]),
        })

    for t in teams.values():
        t['season'] = season_label

    return list(teams.values())


def upsert_trends(rows: list[dict], dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows[:10]:
            print(f'  [DRY] {r.get("team"):<22} '
                  f'ATS {r.get("ats_wins")}-{r.get("ats_losses")}-{r.get("ats_pushes")} '
                  f'({r.get("cover_pct")}%) '
                  f'O/U {r.get("ou_overs")}o-{r.get("ou_unders")}u ({r.get("over_pct")}%)')
        return len(rows)
    written = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in rows:
        r['updated_at'] = now_iso
    # Batch upsert (100 at a time)
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        pr = requests.post(f'{SB}/rest/v1/ncaab_team_trends?on_conflict=team,season',
                           headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204): written += len(chunk)
        else: print(f'  ✗ upsert failed: {pr.status_code} {pr.text[:200]}')
    return written


def _current_season_label() -> str:
    """Current NCAAB season label per typical Nov-Apr calendar."""
    now = datetime.now()
    if now.month >= 8:  # Aug onward = start of new season
        return f'{now.year}-{str(now.year+1)[-2:]}'
    return f'{now.year-1}-{str(now.year)[-2:]}'


def run(season: str | None = None, dry_run: bool = False):
    season = season or _current_season_label()
    print(f'=== pull ncaab teamrankings trends · season {season} ===')
    rows = scrape_season(season)
    print(f'  scraped {len(rows)} teams')
    written = upsert_trends(rows, dry_run=dry_run)
    print(f'  {"[DRY] " if dry_run else ""}upserted {written} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', help='e.g. 2025-26 (default: current)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
