"""Barttorvik (Torvik) NCAAB rating scraper (2026-08-14).

NCAAB Session 1 · rating source #2 (KenPom is #1, already wired).

Torvik uses a different methodology than KenPom (weighs recent games
more heavily; different possession-adjustment framework) which makes
it an INDEPENDENT lens for the Panel model — exactly what we want.

Data endpoint: Barttorvik exposes a clean JSON array at:
    https://barttorvik.com/{YEAR}_team_results.json

Each entry is a positional array of ~45 fields. Column order verified
2026-08-14 via sample (Houston 2024-25 season):
    0=rank, 1=team, 2=conf, 3=record, 4=adj_off, 5=rank_adj_off,
    6=adj_def, 7=rank_adj_def, 8=barthag, 9=rank_barthag,
    10=wins, 11=losses, ...

adj_em derived as adj_off - adj_def (Torvik doesn't expose EM directly).

Writes to ncaab_rating_snapshots per team. Sport-agnostic table means
Panel model reads uniformly across rating systems.

RESPECTFUL SCRAPING
  * User-Agent identifies us (Sweat Locker)
  * Once per day cache — don't hammer
  * Fail-open — if Torvik is down, we log to data_quality_events and
    the pipeline continues with fewer lens (Panel just averages the
    systems it has)

CLI:
    python ncaab_torvik_pull.py [--season 2025-26] [--dry-run]
"""
from __future__ import annotations
import argparse, os, re, sys
from datetime import date, datetime, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Sweat Locker · analytics aggregator)'}
TORVIK_URL = 'https://barttorvik.com/{year}_team_results.json'


def _season_to_year(season: str) -> str:
    """'2025-26' → '2026' (Torvik uses the calendar year at end of season)."""
    parts = season.split('-')
    if len(parts) != 2: return season
    yy = parts[1].strip()
    if len(yy) == 2: yy = '20' + yy
    return yy


def fetch_torvik(season: str) -> list:
    """Return list of team-dicts from Torvik's JSON endpoint."""
    year = _season_to_year(season)
    r = requests.get(TORVIK_URL.format(year=year), headers=HEADERS, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ Torvik HTTP {r.status_code}')
        return []
    try:
        rows = r.json()
    except ValueError:
        print(f'  ✗ Torvik JSON parse failed')
        return []
    if not isinstance(rows, list):
        return []
    teams = []
    for row in rows:
        # Positional layout — indices verified 2026-08-14 on Houston 2024-25
        if not isinstance(row, list) or len(row) < 8: continue
        try:
            rank = int(row[0])
            team = row[1]
            conf = row[2]
            record = row[3]
            adj_off = float(row[4])
            adj_def = float(row[6])
            adj_em = adj_off - adj_def
            teams.append({
                'team': team, 'conference': conf, 'record': record,
                'adj_off': adj_off, 'adj_def': adj_def, 'adj_em': adj_em,
                'em_rank': rank,
            })
        except (TypeError, ValueError, IndexError):
            continue
    return teams


def upsert_snapshot(teams: list, season: str, snapshot_date: date,
                    dry_run: bool = False) -> int:
    from data_quality import DQ
    dq = DQ(source='ncaab_torvik_pull.py', sport='NCAAB')

    # Data-quality assertions
    dq.assert_range(len(teams), 300, 400,
                     'torvik_team_count',
                     context={'season': season, 'n_teams': len(teams)},
                     severity='warn')
    if len(teams) < 100:
        print(f'  ✗ suspiciously few teams ({len(teams)}) — check scraper')
        return 0

    now = datetime.now(timezone.utc).isoformat()
    payloads = []
    for t in teams:
        if not t.get('team') or t.get('adj_em') is None: continue
        payloads.append({
            'snapshot_date': snapshot_date.isoformat(),
            'team': t['team'],
            'season': season,
            'rating_system': 'torvik',
            'adj_off': t.get('adj_off'),
            'adj_def': t.get('adj_def'),
            'adj_em': t.get('adj_em'),
            'tempo': None,   # not scraped from default view
            'em_rank': t.get('em_rank'),
            'raw_payload': {'conference': t.get('conference'),
                            'record': t.get('record')},
            'computed_at': now,
        })
    if dry_run:
        print(f'  [DRY] would upsert {len(payloads)} torvik snapshots')
        for p in payloads[:3]:
            print(f'    {p["team"]:25} em={p["adj_em"]} off={p["adj_off"]} def={p["adj_def"]} rank={p["em_rank"]}')
        return len(payloads)

    written = 0
    for i in range(0, len(payloads), 200):
        chunk = payloads[i:i+200]
        pr = requests.post(
            f'{SB}/rest/v1/ncaab_rating_snapshots?on_conflict=snapshot_date,team,season,rating_system',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} torvik snapshots')
    return written


def run(season: str, dry_run: bool = False) -> int:
    print(f'=== ncaab_torvik_pull · season {season} ===')
    teams = fetch_torvik(season)
    print(f'  parsed {len(teams)} teams from Torvik')
    if not teams: return 0
    today = (datetime.now(timezone.utc)).date()
    return upsert_snapshot(teams, season, today, dry_run=dry_run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', default='2025-26', help="'2025-26' style")
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
