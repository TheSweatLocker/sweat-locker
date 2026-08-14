"""Haslametrics NCAAB rating scraper (2026-08-14).

NCAAB Session 1 · rating source #3 (KenPom #1, Torvik #2).

Haslametrics uses a different methodology than KenPom + Torvik — heavier
weighting on offensive/defensive four factors (eFG%, TO%, OR%, FTR)
and a proprietary schedule adjustment. Independent lens for the Panel.

Data endpoint: XML feed at https://haslametrics.com/ratings.xml
Requires brotli decompression (server force-sends br even when we ask
for gzip; requests library auto-handles when brotli lib installed).

XML shape (verified 2026-08-14):
  <mydata>
    <mr rk="239" t="Abil. Christian" c="WAC" id="2000"
        oe="102.585802" de="109.270944"
        ftpct="71.67" ou="67.24" ... 40+ more attrs />
    ...
  </mydata>

Universal fields:
  rk → em_rank
  t  → team name (needs alias mapping for canonical name)
  c  → conference
  oe → adj_off (points per 100 possessions offense)
  de → adj_def (points per 100 possessions defense)
  ou or du → tempo proxy (possessions per 40 min)
  adj_em derived as oe - de

Massey ratings were considered as source #4 but their site returns 403
for scrapers (Cloudflare anti-bot). Skipping — 3-system Panel is
sufficient for anti-consensus rule.

CLI:
    python ncaab_haslam_pull.py [--dry-run]

Haslam always shows current season only (no season param). If we need
historical, snapshot the XML file over time — which is what
ncaab_rating_snapshots is designed for (append-only history).
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
HASLAM_URL = 'https://haslametrics.com/ratings.xml'


def fetch_haslam() -> list:
    """Return list of team-dicts from Haslam's XML feed."""
    r = requests.get(HASLAM_URL, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ Haslam HTTP {r.status_code}'); return []
    xml = r.text
    # Match every <mr .../> element (self-closing team row)
    teams = []
    for m in re.finditer(r'<mr\s+([^/>]+?)\s*/?>', xml):
        attrs_str = m.group(1)
        # Parse key="value" pairs
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', attrs_str))
        try:
            rk = int(attrs['rk'])
            team = attrs['t']
            conf = attrs.get('c', '')
            oe = float(attrs['oe'])
            de = float(attrs['de'])
            # Tempo: Haslam has "ou" (offensive pace) and "du" (defensive pace opp)
            # Their tempo isn't a single value like KenPom; use the mean.
            ou = float(attrs.get('ou', 0)) if attrs.get('ou') else None
            du = float(attrs.get('du', 0)) if attrs.get('du') else None
            tempo = (ou + du) / 2 if (ou and du) else None
            teams.append({
                'team': team, 'conference': conf, 'em_rank': rk,
                'adj_off': oe, 'adj_def': de, 'adj_em': oe - de,
                'tempo': tempo,
            })
        except (KeyError, ValueError):
            continue
    return teams


def _current_season_str() -> str:
    """Return '2025-26' style for current season.
    NCAAB season runs Nov Y → Apr Y+1; use current year vs next."""
    now = datetime.now(timezone.utc)
    # If we're July-Oct, no active season — return last completed
    if 7 <= now.month <= 10:
        y = now.year
        return f'{y-1}-{str(y)[-2:]}'
    # Nov-Dec: season Y - Y+1
    if now.month >= 11:
        y = now.year
        return f'{y}-{str(y+1)[-2:]}'
    # Jan-June: current season is (Y-1)-Y
    y = now.year
    return f'{y-1}-{str(y)[-2:]}'


def upsert_snapshot(teams: list, snapshot_date: date, dry_run: bool = False) -> int:
    from data_quality import DQ
    dq = DQ(source='ncaab_haslam_pull.py', sport='NCAAB')

    dq.assert_range(len(teams), 300, 400, 'haslam_team_count',
                     context={'n_teams': len(teams)})
    if len(teams) < 100:
        print(f'  ✗ suspiciously few teams ({len(teams)})')
        return 0

    season_str = _current_season_str()
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for t in teams:
        # Range guards
        if t.get('adj_em') is not None:
            dq.assert_range(t['adj_em'], -50, 50, 'haslam_adj_em',
                             context={'team': t.get('team'), 'value': t['adj_em']})
        payloads.append({
            'snapshot_date': snapshot_date.isoformat(),
            'team': t['team'],
            'season': season_str,
            'rating_system': 'haslam',
            'adj_off': t.get('adj_off'),
            'adj_def': t.get('adj_def'),
            'adj_em': t.get('adj_em'),
            'tempo': t.get('tempo'),
            'em_rank': t.get('em_rank'),
            'raw_payload': {'conference': t.get('conference')},
            'computed_at': now_iso,
        })
    if dry_run:
        print(f'  [DRY] would upsert {len(payloads)} haslam snapshots')
        for p in payloads[:3]:
            print(f'    {p["team"]:22} em={p["adj_em"]:.2f} off={p["adj_off"]:.2f} def={p["adj_def"]:.2f} rank={p["em_rank"]}')
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
    print(f'  ✓ wrote {written} haslam snapshots')
    return written


def run(dry_run: bool = False) -> int:
    print(f'=== ncaab_haslam_pull ===')
    teams = fetch_haslam()
    print(f'  parsed {len(teams)} teams from Haslam XML')
    if not teams: return 0
    return upsert_snapshot(teams, datetime.now(timezone.utc).date(), dry_run=dry_run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
