"""NBA team four-factors + pace puller (2026-09-01).

Scrapes basketball-reference.com season pages for team advanced stats
(eFG%, TOV%, ORB%, FTr + opponent variants + Pace). Populates the
existing nba_team_stats schema columns which have sat NULL since
nba_elo.py only writes PPG-based ratings.

Basketball-reference structure for NBA_YYYY.html:
  - Contains multiple tables; the one we want is `advanced-team`
  - Some tables are inside HTML comments (anti-scraping obfuscation);
    fetch handles this by uncommenting before parsing
  - Team names use full ESPN-style display names

Populates:
  efg_pct, tov_pct, orb_pct, ft_rate,
  opp_efg_pct, opp_tov_pct, opp_orb_pct, opp_ft_rate,
  pace

Team lookup: nba_data_client.get_teams() maps full names → ESPN abbrevs
which are the PK of nba_team_stats (from nba_elo.py rewrite 2026-09-01).

Season format: nba_team_stats uses 'YYYY-YY' (e.g. '2024-25'). Basketball-
reference URLs use ending year ('NBA_2025.html' = 2024-25 season).

CLI:
    python nba_four_factors_pull.py                    # current season
    python nba_four_factors_pull.py --season 2024-25
    python nba_four_factors_pull.py --dry-run
"""
from __future__ import annotations
import argparse, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
H_WRITE = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
           'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# UA + accept headers to look like a real browser. Basketball-reference
# 403s vanilla Python requests otherwise.
BREF_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'en-US,en;q=0.9',
}


def _season_url(season: str) -> tuple[str, int]:
    """'2024-25' → ('https://.../leagues/NBA_2025.html', 2025)"""
    parts = season.split('-')
    if len(parts) != 2:
        raise ValueError(f'season must be YYYY-YY format, got: {season}')
    start_yr = int(parts[0])
    end_yr = start_yr + 1
    return f'https://www.basketball-reference.com/leagues/NBA_{end_yr}.html', end_yr


def _fetch_html(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=BREF_HEADERS, timeout=20)
        if r.status_code == 200:
            return r.text
        print(f'  ✗ bref fetch failed: {r.status_code}')
    except Exception as e:
        print(f'  ✗ bref fetch error: {e}')
    return None


def _uncomment_hidden_tables(html: str) -> str:
    """Basketball-reference wraps some tables in HTML comments as a light
    anti-scrape measure. Strip the <!-- --> around table markup so
    BeautifulSoup can parse them."""
    # Naive but works for their comment-wrap pattern
    return re.sub(r'<!--\s*(<table[\s\S]*?</table>)\s*-->', r'\1', html)


def _parse_float(v) -> Optional[float]:
    if v is None: return None
    s = str(v).strip().replace(',', '')
    if not s or s == '—': return None
    try: return float(s)
    except (TypeError, ValueError): return None


def parse_advanced_team_table(html: str) -> dict:
    """Extract {team_full_name: {efg_pct, tov_pct, orb_pct, ft_rate,
    opp_efg_pct, opp_tov_pct, opp_orb_pct, opp_ft_rate, pace}} from the
    'advanced-team' table on the season page.

    All rates from basketball-reference are ALREADY IN PERCENT UNITS
    (e.g. 55.2 = 55.2%). Store as decimal (0.552) to match existing
    nba_team_stats convention (rest of app expects 0-1 decimals).
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # Table id is 'advanced-team' on modern bref layout
    table = soup.find('table', id='advanced-team')
    if table is None:
        # Try commented variant explicitly
        table = soup.find('table', id='advanced_team')
    if table is None:
        print('  ✗ advanced-team table not found in page')
        return {}

    out = {}
    for row in table.select('tbody tr'):
        # Team name in a cell with data-stat="team"
        team_cell = row.find('td', {'data-stat': 'team'}) or row.find('th', {'data-stat': 'team'})
        if team_cell is None: continue
        team = team_cell.get_text(strip=True)
        # BRef appends '*' to playoff teams — strip
        team = team.rstrip('*').strip()
        if not team or team == 'League Average': continue

        def cell(stat: str) -> Optional[float]:
            c = row.find('td', {'data-stat': stat})
            if c is None: return None
            return _parse_float(c.get_text(strip=True))

        pace = cell('pace') or cell('pace_o')

        # Four factors — bref column names
        efg = cell('efg_pct')
        tov = cell('tov_pct')
        orb = cell('orb_pct')
        ftr = cell('ft_rate') or cell('ft_fga')

        # Opp four factors
        opp_efg = cell('opp_efg_pct')
        opp_tov = cell('opp_tov_pct')
        opp_drb = cell('opp_drb_pct')  # bref uses opp_drb; convert to opp_orb by 100 - drb
        opp_ftr = cell('opp_ft_rate') or cell('opp_ft_fga')

        # Basketball-reference column formats (verified 2026-09-01):
        #   efg_pct / opp_efg_pct : DECIMAL already (0.547 = 54.7%)
        #   tov_pct / opp_tov_pct : PERCENT (12.7 = 12.7%)
        #   orb_pct / opp_drb_pct : PERCENT (22.3 = 22.3%)
        #   ft_rate / opp_ft_rate : DECIMAL ratio (0.240 = FT/FGA)
        #   pace                  : raw poss/48min (98.5)
        # Target schema (nba_team_stats + team_stats_rolling matview):
        #   *_pct columns stored as DECIMAL (0-1) — matview multiplies
        #   by 100 for display. Normalize accordingly.
        def _to_decimal_from_percent(v):
            return round(v / 100.0, 4) if v is not None else None
        def _keep_decimal(v):
            return round(v, 4) if v is not None else None

        efg_dec     = _keep_decimal(efg)          # already decimal
        opp_efg_dec = _keep_decimal(opp_efg)      # already decimal
        tov_dec     = _to_decimal_from_percent(tov)
        orb_dec     = _to_decimal_from_percent(orb)
        opp_tov_dec = _to_decimal_from_percent(opp_tov)
        # opp_drb_pct → opp_orb_pct (offensive rebounding view of same event)
        opp_orb_dec = (1.0 - opp_drb / 100.0) if opp_drb is not None else None

        out[team] = {
            'efg_pct':      efg_dec,
            'tov_pct':      tov_dec,
            'orb_pct':      orb_dec,
            'ft_rate':      round(ftr, 3) if ftr is not None else None,  # already a ratio
            'opp_efg_pct':  opp_efg_dec,
            'opp_tov_pct':  opp_tov_dec,
            'opp_orb_pct':  round(opp_orb_dec, 4) if opp_orb_dec is not None else None,
            'opp_ft_rate':  round(opp_ftr, 3) if opp_ftr is not None else None,
            'pace':         round(pace, 1) if pace is not None else None,
        }
    return out


# Basketball-reference uses slightly different names than ESPN for some
# teams. This map bridges bref → ESPN full name so our abbrev_map hits.
_BREF_NAME_ALIASES = {
    'Los Angeles Clippers': 'LA Clippers',   # ESPN uses "LA Clippers"
}


def _load_espn_abbrev_map() -> dict:
    """Full team name → 3-letter ESPN abbrev. Same map used by nba_elo.py."""
    try:
        from nba_data_client import get_teams
        teams = get_teams()
        if teams:
            return {t['name']: t['abbrev'] for t in teams
                    if t.get('name') and t.get('abbrev')}
    except Exception as e:
        print(f'  ESPN team lookup failed ({e}), using hardcoded fallback')
    return {
        'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
        'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE',
        'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
        'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
        'LA Clippers': 'LAC', 'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM',
        'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL', 'Minnesota Timberwolves': 'MIN',
        'New Orleans Pelicans': 'NOP', 'New York Knicks': 'NYK', 'Oklahoma City Thunder': 'OKC',
        'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI', 'Phoenix Suns': 'PHX',
        'Portland Trail Blazers': 'POR', 'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SAS',
        'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA', 'Washington Wizards': 'WAS',
    }


def run(season: str, dry_run: bool = False) -> int:
    print(f'=== NBA four-factors pull · season {season} ===')
    url, end_yr = _season_url(season)
    print(f'  fetching {url}')
    html = _fetch_html(url)
    if not html:
        return 0
    html = _uncomment_hidden_tables(html)
    stats = parse_advanced_team_table(html)
    if not stats:
        print('  no team rows parsed'); return 0
    print(f'  parsed {len(stats)} teams from bref')

    abbrev_map = _load_espn_abbrev_map()

    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    unmapped = []
    for bref_name, s in stats.items():
        # Resolve bref → ESPN name via alias (bref "Los Angeles Clippers"
        # vs ESPN "LA Clippers"), then look up abbrev
        team_name = _BREF_NAME_ALIASES.get(bref_name, bref_name)
        abbrev = abbrev_map.get(team_name)
        if not abbrev:
            unmapped.append(bref_name)
            continue
        row = {
            'team_abbrev': abbrev,
            'team_name':   team_name,
            'season':      season,
            'updated_at':  now_iso,
            **s,
        }
        payloads.append(row)
        print(f'  {team_name:25} eFG {(s["efg_pct"] or 0)*100:5.1f}%  '
              f'TOV {(s["tov_pct"] or 0)*100:5.1f}%  '
              f'ORB {(s["orb_pct"] or 0)*100:5.1f}%  '
              f'Pace {s["pace"] or "—"}')
    if unmapped:
        print(f'\n  ⚠ unmapped bref names (no ESPN abbrev): {unmapped}')

    if dry_run:
        print(f'\n[DRY] would upsert {len(payloads)} rows'); return len(payloads)

    written = 0
    for i in range(0, len(payloads), 30):
        chunk = payloads[i:i+30]
        r = requests.post(f'{SB}/rest/v1/nba_team_stats?on_conflict=team_abbrev,season',
                          headers=H_WRITE, json=chunk, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ upsert failed: {r.status_code} {r.text[:180]}')
    print(f'\nwrote {written} rows to nba_team_stats (four-factors + pace)')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', help="NBA season YYYY-YY (default: current)")
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    season = args.season
    if not season:
        # Current season: if before Oct → prior season active, else this year
        now = datetime.now(timezone.utc)
        if now.month >= 10:
            season = f'{now.year}-{str(now.year + 1)[-2:]}'
        else:
            season = f'{now.year - 1}-{str(now.year)[-2:]}'
    run(season=season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
