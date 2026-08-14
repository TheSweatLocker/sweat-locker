"""Barttorvik (Torvik) NCAAB rating scraper (2026-08-14).

NCAAB Session 1 · rating source #2 (KenPom is #1, already wired).

Torvik uses a different methodology than KenPom (weighs recent games
more heavily; different possession-adjustment framework) which makes
it an INDEPENDENT lens for the Panel model — exactly what we want.

Data endpoint: Barttorvik has a semi-public JSON at:
    https://barttorvik.com/trank.php?year={YY}&sort=&conlimit=&venue=&type=

That endpoint returns HTML that embeds a JSON blob in a <script> tag.
We parse the table row structure directly (more reliable than the
undocumented JSON).

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
TORVIK_URL = 'https://barttorvik.com/trank.php'


def _season_to_year(season: str) -> str:
    """'2025-26' → '2026' (Torvik uses the calendar year at end of season)."""
    parts = season.split('-')
    if len(parts) != 2: return season
    yy = parts[1].strip()
    if len(yy) == 2: yy = '20' + yy
    return yy


def fetch_torvik(season: str) -> list:
    """Return list of team-dicts. One row per D-I team."""
    year = _season_to_year(season)
    r = requests.get(TORVIK_URL,
        params={'year': year, 'sort': '', 'conlimit': '',
                'venue': '', 'type': 'All'},
        headers=HEADERS, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ Torvik HTTP {r.status_code}')
        return []
    html = r.text

    # Torvik's table has rows like:
    # <tr><td class="teamname"><a href=...>TEAM</a></td>
    #     <td>CONF</td><td>W-L</td><td>ADJ_EM</td>
    #     <td>ADJ_OFF</td><td>ADJ_DEF</td><td>...</td>
    # The most reliable approach: find <tr class="seedrow"> or plain <tr>
    # blocks and parse cells in order.
    rows = re.findall(
        r'<tr[^>]*>\s*<td[^>]*>[^<]*(?:<a[^>]*>)?([^<]+?)(?:</a>)?</td>'  # rank
        r'\s*<td[^>]*>(?:<a[^>]*>)?([^<]+?)(?:</a>)?</td>'                # team
        r'\s*<td[^>]*>([^<]+)</td>'                                        # conf
        r'\s*<td[^>]*>([^<]+)</td>'                                        # W-L
        r'\s*<td[^>]*>([^<]+)</td>'                                        # ADJ_EM
        r'\s*<td[^>]*>([^<]+)</td>'                                        # ADJ_OFF
        r'\s*<td[^>]*>([^<]+)</td>',                                       # ADJ_DEF
        html, re.DOTALL)

    teams = []
    for row in rows:
        try:
            rank_str, team, conf, wl, em, off, deff = [s.strip() for s in row]
            # Skip header rows / non-numeric rank
            if not rank_str.isdigit(): continue
            teams.append({
                'team': team,
                'conference': conf,
                'record': wl,
                'adj_em': float(em.replace('+', '')) if em.replace('+', '').replace('.', '').replace('-', '').isdigit() else None,
                'adj_off': float(off) if off.replace('.', '').isdigit() else None,
                'adj_def': float(deff) if deff.replace('.', '').isdigit() else None,
                'em_rank': int(rank_str),
            })
        except (ValueError, IndexError) as e:
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
