"""NBA injury puller — ESPN /nba/injuries page (2026-08-25).

Populates nba_injuries table from ESPN's server-rendered injury report.
Schema: (team_abbrev, player_name, status, reason, updated_at) — the
unique constraint includes updated_at so every scrape appends a fresh
row (history preserved). The NBAInjuriesCard queries `.order updated_at
desc .limit 20` per team so latest state is what users see.

Status normalization → matches app query filter:
    Day-To-Day       → GTD
    Out              → OUT
    Doubtful         → DOUBTFUL
    Questionable     → QUESTIONABLE
    Probable         → PROBABLE

Cadence (add to nba_pipeline.yml):
    Daily 4pm ET (post-morning injury reports)
    Additional 30min before tipoff on gameday (later, if needed)

Usage:
    python nba_injuries_pull.py          # scrape all teams
    python nba_injuries_pull.py --dry-run
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests
from bs4 import BeautifulSoup


SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_WRITE = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
           'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

ESPN_URL = 'https://www.espn.com/nba/injuries'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'}


# Full ESPN team name → abbrev used in nba_game_context.home_abbrev.
TEAM_ABBREV: dict[str, str] = {
    'Atlanta Hawks': 'ATL',
    'Boston Celtics': 'BOS',
    'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA',
    'Chicago Bulls': 'CHI',
    'Cleveland Cavaliers': 'CLE',
    'Dallas Mavericks': 'DAL',
    'Denver Nuggets': 'DEN',
    'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GSW',
    'Houston Rockets': 'HOU',
    'Indiana Pacers': 'IND',
    'LA Clippers': 'LAC',
    'Los Angeles Clippers': 'LAC',
    'Los Angeles Lakers': 'LAL',
    'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA',
    'Milwaukee Bucks': 'MIL',
    'Minnesota Timberwolves': 'MIN',
    'New Orleans Pelicans': 'NOP',
    'New York Knicks': 'NYK',
    'Oklahoma City Thunder': 'OKC',
    'Orlando Magic': 'ORL',
    'Philadelphia 76ers': 'PHI',
    'Phoenix Suns': 'PHX',
    'Portland Trail Blazers': 'POR',
    'Sacramento Kings': 'SAC',
    'San Antonio Spurs': 'SAS',
    'Toronto Raptors': 'TOR',
    'Utah Jazz': 'UTA',
    'Washington Wizards': 'WAS',
}

# ESPN status → nba_injuries.status
STATUS_MAP = {
    'day-to-day':   'GTD',
    'day to day':   'GTD',
    'gtd':          'GTD',
    'out':          'OUT',
    'doubtful':     'DOUBTFUL',
    'questionable': 'QUESTIONABLE',
    'probable':     'PROBABLE',
}


def _norm_status(s: str) -> Optional[str]:
    if not s:
        return None
    key = s.strip().lower()
    return STATUS_MAP.get(key)


def _clean_reason(comment: str) -> Optional[str]:
    """ESPN comments are long ('Jul 18: Gueye underwent surgery Tuesday to
    repair a fractured left foot...'). Extract the body part / injury type
    for a compact stored value."""
    if not comment:
        return None
    c = comment.strip()
    # Common body parts / conditions ESPN mentions — keep it terse.
    body = re.search(r'\b(knee|ankle|hamstring|calf|shoulder|foot|hand|wrist|'
                     r'back|hip|elbow|thumb|finger|toe|neck|groin|'
                     r'concussion|illness|personal|rest|surgery|achilles|'
                     r'quad|thigh|abdominal|oblique|forearm)\b',
                     c, re.IGNORECASE)
    return body.group(1).lower() if body else c[:60]


def fetch_html() -> Optional[str]:
    try:
        r = requests.get(ESPN_URL, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f'  ⚠ fetch failed: {e}')
        return None
    if r.status_code != 200:
        print(f'  ⚠ ESPN {r.status_code}')
        return None
    return r.text


def parse_injuries(html: str) -> list[dict]:
    """Extract per-player rows tagged by team_abbrev.
    ESPN groups by team header (full name) → table of players."""
    soup = BeautifulSoup(html, 'html.parser')
    rows: list[dict] = []

    # ESPN structures each team section with a header + a Table__title
    # element containing the team name, followed by a Table with player rows.
    # Selector matches multiple possible ESPN layouts we've seen.
    for section in soup.select('.Table__Title, .injuries__teamHeader, section.injuries'):
        # Team name is text inside the section header; walk parent to find
        # the associated Table.
        team_name = None
        # Try the section text itself first
        text_candidates = [section.get_text(strip=True)] if section else []
        # Try nested title spans as fallback
        for t in text_candidates:
            for name in TEAM_ABBREV:
                if name in t:
                    team_name = name
                    break
            if team_name:
                break
        if not team_name:
            continue
        abbr = TEAM_ABBREV[team_name]
        # Find the associated Table — walk up to the section container, then
        # find the next Table__TR rows.
        container = section.find_parent() or section
        table = container.find('table') or container.find_next('table')
        if not table:
            continue
        for tr in table.select('tr'):
            cells = [td.get_text(' ', strip=True) for td in tr.find_all(['td', 'th'])]
            if len(cells) < 4:
                continue
            # Skip header rows (contain 'NAME' / 'POS' / 'STATUS' etc.)
            if cells[0].strip().lower() in ('name', ''):
                continue
            player_name = cells[0].strip()
            # cells[1] = POS, cells[2] = EST RETURN, cells[3] = STATUS, cells[4] = COMMENT
            status_raw = cells[3] if len(cells) > 3 else ''
            comment    = cells[4] if len(cells) > 4 else ''
            status = _norm_status(status_raw)
            if not status or not player_name:
                continue
            rows.append({
                'team_abbrev': abbr,
                'player_name': player_name,
                'status': status,
                'reason': _clean_reason(comment),
            })

    # ESPN sometimes renders each team as its own top-level container
    # (no shared section wrapper). Fallback: walk every table with
    # injury-like cells and try to associate via nearest preceding header.
    if not rows:
        for team_name, abbr in TEAM_ABBREV.items():
            # Find any node that's just the team name
            for node in soup.find_all(string=team_name):
                container = node.find_parent()
                if not container:
                    continue
                table = container.find_next('table')
                if not table:
                    continue
                for tr in table.select('tr'):
                    cells = [td.get_text(' ', strip=True) for td in tr.find_all(['td', 'th'])]
                    if len(cells) < 4:
                        continue
                    if cells[0].strip().lower() in ('name', ''):
                        continue
                    status = _norm_status(cells[3] if len(cells) > 3 else '')
                    if not status:
                        continue
                    rows.append({
                        'team_abbrev': abbr,
                        'player_name': cells[0].strip(),
                        'status': status,
                        'reason': _clean_reason(cells[4] if len(cells) > 4 else ''),
                    })
                break  # first matching header is enough per team

    # De-dupe by (team_abbrev, player_name) — same player might appear in
    # multiple table selectors above; keep first occurrence.
    seen = set()
    dedup = []
    for r in rows:
        k = (r['team_abbrev'], r['player_name'])
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def upsert_batch(rows: list, dry_run: bool = False) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows[:8]:
            print(f'  [DRY] {r["team_abbrev"]} · {r["player_name"]:24s} · '
                  f'{r["status"]:12s} · {r.get("reason") or ""}')
        print(f'  [DRY] ({len(rows)} total)')
        return len(rows)
    # nba_injuries unique constraint is (team_abbrev, player_name, updated_at)
    # with updated_at defaulting to NOW(). Every scrape appends fresh rows;
    # dedupe is downstream via .order updated_at desc.
    written = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/nba_injuries',
            headers=H_WRITE, json=chunk, timeout=45,
        )
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ batch {i}-{i+len(chunk)}: {r.status_code} — {r.text[:200]}')
    return written


def run(dry_run: bool = False) -> None:
    print('== NBA injuries pull (ESPN) ==')
    html = fetch_html()
    if not html:
        return
    rows = parse_injuries(html)
    print(f'  {len(rows)} injury rows parsed')
    if not rows:
        print('  (ESPN layout may have changed — inspect parse selectors)')
        return
    written = upsert_batch(rows, dry_run=dry_run)
    print(f'\nSummary: {written}/{len(rows)} rows persisted')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(dry_run=args.dry_run)
