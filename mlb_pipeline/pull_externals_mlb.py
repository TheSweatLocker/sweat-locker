"""External picks pull — MLB (noon + 5PM ET daily).

2026-07-21: Aggregates public handicapper picks, sharp $ signals, and
weather/park data into external_picks table for the "External Sources"
app tab. Each pull attempt logged to external_pull_log for provenance.

CADENCE (MLB):
  - Noon ET: primary pull (handicappers post 10-noon, Action $ settles by lunch)
  - 5 PM ET: refresh pull (catches late-day expert additions)

SOURCE TIERS (from 7/20 audit — project_audit_battery_721):
  BOOST:   Dimers (≥60% wp), CBS staff, Public ≥70% + price ≤-150, Action ≥+35 gap
  TRUST:   VSiN, Doc Sports, BettingPros, PickDawgz, Covers expert, OddsShark, Fangraphs
  NEUTRAL: Pickswise 3-star, SCP, Rotogrinders
  FADE:    Pickswise 5-STAR, Ballpark Pal wind, Action +15-34 mid-gap

Every pull writes 2 things:
  1. Row(s) in external_pull_log (one per source × timestamp)
  2. Rows in external_picks with pull_id FK back to log

USAGE:
  python pull_externals_mlb.py                # today's noon pull
  python pull_externals_mlb.py --refresh      # 5PM refresh
  python pull_externals_mlb.py --date 2026-07-22  # specific date
  python pull_externals_mlb.py --dry-run      # print, don't write

  --source dimers                             # test one source in isolation
"""
import argparse
import os
import re
import sys
import time
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
import uuid

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# ─────────────────────────────────────────────────────────────
# Source registry — one entry per external source
# ─────────────────────────────────────────────────────────────
# Each source has:
#   key: matches external_picks.source column
#   fade_flag: default per 7/20 audit (individual picks can override)
#   fetcher: the function that pulls picks for this source
#   base_url: for attribution + link-back
#   ttl_hours: how long the pick stays "fresh" before expiry

SOURCE_REGISTRY = {
    'dimers': {
        'fade_flag': 'boost', 'ttl_hours': 12,
        'base_url': 'https://www.dimers.com/bet-hub/mlb/schedule',
        'label': 'Dimers',
    },
    'covers': {
        'fade_flag': 'trust', 'ttl_hours': 12,
        'base_url': 'https://contests.covers.com/consensus/topconsensus/mlb/overall',
        'label': 'Covers',
    },
    'cbs': {
        'fade_flag': 'boost', 'ttl_hours': 12,
        'base_url': 'https://www.cbssports.com/mlb/expert-picks/',
        'label': 'CBS Sports',
    },
    'action': {
        'fade_flag': 'trust', 'ttl_hours': 6,      # sharp $ moves fast
        'base_url': 'https://www.actionnetwork.com/mlb/public-betting',
        'label': 'Action Network',
    },
    'vsin': {
        'fade_flag': 'trust', 'ttl_hours': 12,
        'base_url': 'https://vsin.com/mlb/',
        'label': 'VSiN',
    },
    'bettingpros': {
        'fade_flag': 'trust', 'ttl_hours': 12,
        'base_url': 'https://www.bettingpros.com/mlb/',
        'label': 'BettingPros',
    },
    'oddsshark': {
        'fade_flag': 'neutral', 'ttl_hours': 12,
        'base_url': 'https://www.oddsshark.com/mlb/computer-picks',
        'label': 'OddsShark',
    },
    'pickswise': {
        'fade_flag': 'neutral', 'ttl_hours': 12,
        'base_url': 'https://www.pickswise.com/mlb/picks/',
        'label': 'Pickswise',
    },
    'pickdawgz': {
        'fade_flag': 'trust', 'ttl_hours': 12,
        'base_url': 'https://www.pickdawgz.com/mlb-picks',
        'label': 'PickDawgz',
    },
    'docsports': {
        'fade_flag': 'trust', 'ttl_hours': 12,
        'base_url': 'https://www.docsports.com/free-picks/baseball/',
        'label': 'Doc Sports',
    },
    'scp': {
        'fade_flag': 'neutral', 'ttl_hours': 12,
        'base_url': 'https://www.sportschatplace.com/mlb-picks-today/',
        'label': 'Sports Chat Place',
    },
    'fangraphs': {
        'fade_flag': 'neutral', 'ttl_hours': 24,
        'base_url': 'https://www.fangraphs.com/scoreboard.aspx',
        'label': 'Fangraphs',
    },
    'ballparkpal': {
        'fade_flag': 'fade', 'ttl_hours': 6,        # single-source wind, per audit
        'base_url': 'https://www.ballparkpal.com/Park-Factors.php',
        'label': 'Ballpark Pal',
    },
    'oddscrowd': {
        'fade_flag': 'trust', 'ttl_hours': 6,       # money% drifts intraday
        'base_url': 'https://oddscrowd.com/games/upcoming/baseball',
        'label': 'OddsCrowd',
    },
    'sbr': {
        'fade_flag': 'fade', 'ttl_hours': 6,        # extreme public → fade signal
        'base_url': 'https://www.sportsbookreview.com/betting-odds/mlb-baseball/consensus/',
        'label': 'SBR Consensus',
    },
}


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────
@dataclass
class ExternalPick:
    game_id: str
    sport: str
    game_date: str
    source: str
    surface: str          # 'ml' | 'total' | 'rl' | 'prop' | 'sharp_signal' | 'other'
    pick_side: Optional[str] = None       # 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
    pick_line: Optional[float] = None
    odds_american: Optional[int] = None
    confidence: Optional[str] = None      # '3-star','5-star','best-bet',...
    raw_text: Optional[str] = None
    source_url: Optional[str] = None
    fade_flag: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Pull log helpers
# ─────────────────────────────────────────────────────────────
def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _today_et() -> str:
    return _et_now().date().isoformat()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return 'unknown'


def start_pull_log(source: str, sport: str, scheduled_at: datetime,
                   triggered_by: str, source_url: str) -> Optional[str]:
    """Insert a 'running' row, return pull_id UUID."""
    pull_id = str(uuid.uuid4())
    payload = {
        'pull_id': pull_id,
        'sport': sport,
        'source': source,
        'scheduled_at': scheduled_at.isoformat(),
        'status': 'running',
        'triggered_by': triggered_by,
        'source_url': source_url,
        'agent_version': _git_sha(),
    }
    try:
        r = requests.post(
            f'{SB}/rest/v1/external_pull_log',
            headers=H_WRITE, json=payload, timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f'  ⚠ pull_log start failed: {r.status_code} {r.text[:120]}')
            return None
        return pull_id
    except Exception as e:
        print(f'  ⚠ pull_log start exception: {e}')
        return None


def complete_pull_log(pull_id: Optional[str], status: str,
                      picks_pulled: int = 0, games_covered: int = 0,
                      error_message: Optional[str] = None,
                      http_status: Optional[int] = None,
                      duration_ms: Optional[int] = None) -> None:
    if not pull_id:
        return
    payload = {
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'picks_pulled': picks_pulled,
        'games_covered': games_covered,
        'error_message': error_message,
        'http_status': http_status,
        'duration_ms': duration_ms,
    }
    try:
        requests.patch(
            f'{SB}/rest/v1/external_pull_log?pull_id=eq.{pull_id}',
            headers={**H_WRITE, 'Prefer': 'return=minimal'},
            json=payload, timeout=10,
        )
    except Exception as e:
        print(f'  ⚠ pull_log complete exception: {e}')


def write_picks(picks: list, pull_id: Optional[str]) -> int:
    """Batch UPSERT picks with pull_id FK. Returns count written.

    2026-07-27 fix: previously plain INSERT — each cron pull created
    a new row for same (source, game_id, surface, pick_side) combo,
    inflating consensus counts 2-3x (source posts pick at noon → row 1,
    5pm cron pulls same pick → row 2, etc). Fade detector triggered off
    inflated counts. Now uses on_conflict=merge-duplicates against the
    unique index shipped in 20260727_external_picks_dedup.sql:
      (source, game_id, surface, pick_side, game_date).
    pull_id + pulled_at refresh on each merge to reflect latest pull.
    """
    if not picks:
        return 0
    payload = []
    for p in picks:
        d = asdict(p)
        d['pull_id'] = pull_id
        payload.append(d)
    try:
        r = requests.post(
            f'{SB}/rest/v1/external_picks?on_conflict=source,game_id,surface,pick_side,game_date',
            headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json=payload, timeout=20,
        )
        if r.status_code not in (200, 201, 204):
            print(f'  ⚠ picks upsert failed {r.status_code}: {r.text[:120]}')
            return 0
        return len(payload)
    except Exception as e:
        print(f'  ⚠ picks upsert exception: {e}')
        return 0


# ─────────────────────────────────────────────────────────────
# Slate lookup — map source picks to our game_ids
# ─────────────────────────────────────────────────────────────
def load_slate(game_date: str) -> list:
    """Return list of {game_id, home_team, away_team, matchup} for the date."""
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context'
        f'?game_date=eq.{game_date}&select=game_id,home_team,away_team',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def _team_matches(name: str, target: str) -> bool:
    """Fuzzy team-name match (Dodgers vs Los Angeles Dodgers)."""
    name = (name or '').lower().strip()
    target = (target or '').lower().strip()
    if not name or not target:
        return False
    return name in target or target in name or \
           name.split()[-1] == target.split()[-1]


def find_game_id(slate: list, home_hint: str, away_hint: str) -> Optional[str]:
    """Match a scraped matchup back to our game_id."""
    for g in slate:
        if _team_matches(g['home_team'], home_hint) and \
           _team_matches(g['away_team'], away_hint):
            return g['game_id']
    return None


# ─────────────────────────────────────────────────────────────
# Source-specific fetchers — one per source
# ─────────────────────────────────────────────────────────────
# STUBBED for now — real scrapers will call WebFetch or use per-source APIs.
# Each returns a list of ExternalPick objects.

def fetch_dimers(slate: list, game_date: str) -> tuple[list, int]:
    """Dimers.com/bet-hub/mlb/schedule — JS-rendered via Playwright.

    Renders game cards as:
        JUL 21, 6:40 PM ET
        Dodgers            <- away team
        65.7%              <- away win probability
        Phillies           <- home team
        34.3%              <- home win probability

    Live games interleave scores between team and %. Regex handles both.
    Audit tag: BOOST when win_prob >= 60% (Dimers has +7pt lift at 60+).
    """
    from _playwright_helper import render_page
    text, err = render_page('https://www.dimers.com/bet-hub/mlb/schedule')
    if err == 'unavailable':
        print('  ⚠ Dimers: Playwright unavailable — skip')
        return [], 200
    if err:
        print(f'  ⚠ Dimers render error: {err}')
        return [], 500
    if not text:
        return [], 200

    picks = []
    seen = set()
    # Match "TeamA\n(score\n)?WP%\nTeamB\n(score\n)?WP%" chunks
    chunk_re = re.compile(
        r'([A-Z][A-Za-z. ]{2,20}?)\s*\n\s*(?:\d+\s*\n\s*)?(\d{1,2}\.\d)%\s*\n'
        r'\s*([A-Z][A-Za-z. ]{2,20}?)\s*\n\s*(?:\d+\s*\n\s*)?(\d{1,2}\.\d)%',
    )
    for m in chunk_re.finditer(text):
        away_name, away_wp, home_name, home_wp = m.groups()
        away_wp = float(away_wp); home_wp = float(home_wp)
        # Sanity: probabilities sum ~100
        if not (95 <= away_wp + home_wp <= 105):
            continue
        # Find matching game
        gid = None; pick_side = None; pick_wp = None
        for g in slate:
            if _team_matches(g.get('away_team'), away_name) and \
               _team_matches(g.get('home_team'), home_name):
                gid = g['game_id']
                if home_wp >= away_wp:
                    pick_side, pick_wp = 'HOME', home_wp
                else:
                    pick_side, pick_wp = 'AWAY', away_wp
                break
        if not gid or gid in seen:
            continue
        seen.add(gid)
        fade = 'boost' if pick_wp >= 60 else 'neutral'
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='dimers',
            surface='ml', pick_side=pick_side, odds_american=None,
            confidence=f'{pick_wp:.1f}% wp',
            raw_text=f'Dimers wp: {away_name} {away_wp:.1f}% / {home_name} {home_wp:.1f}%',
            fade_flag=fade,
        ))
    return picks, 200


def fetch_covers(slate: list, game_date: str) -> tuple[list, int]:
    """Covers.com consensus table (server-rendered).

    Table columns: Matchup | Date | Consensus % | Sides odds | Picks count.
    Matchup format: "MLB Ath Az" (short team codes).
    Consensus format: "30% 70%" (away% then home%).
    Sides format: "+115 -140" (away odds then home odds).

    We parse each row into an ML pick for each side + track sharp picks
    count as auxiliary metadata.
    """
    from bs4 import BeautifulSoup
    r = requests.get(
        'https://contests.covers.com/consensus/topconsensus/mlb/overall',
        headers={'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'},
        timeout=10,
    )
    if r.status_code != 200:
        return [], r.status_code
    soup = BeautifulSoup(r.text, 'html.parser')
    table = soup.find('table')
    if not table:
        return [], 200

    picks = []
    for row in table.find_all('tr')[1:]:  # skip header
        cells = [c.get_text(' ', strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) < 4:
            continue

        matchup_txt = cells[0].replace('MLB', '').strip()   # e.g. "Ath Az"
        date_txt = cells[1]
        consensus_txt = cells[2]                             # "30% 70%"
        sides_txt = cells[3]                                 # "+115 -140"

        # Only pull rows for our target date
        if game_date not in _covers_date_to_iso(date_txt):
            continue

        # Split team abbreviations
        parts = matchup_txt.split()
        if len(parts) < 2:
            continue
        away_code, home_code = parts[0], parts[1]

        # Match to slate by abbreviation/short name
        gid = _match_covers_abbrev_to_gid(slate, away_code, home_code)
        if not gid:
            continue

        # Parse consensus %s
        pct_matches = re.findall(r'(\d+)%', consensus_txt)
        away_pct = int(pct_matches[0]) if len(pct_matches) >= 1 else None
        home_pct = int(pct_matches[1]) if len(pct_matches) >= 2 else None

        # Parse odds
        odds_matches = re.findall(r'([+-]\d+)', sides_txt)
        away_odds = int(odds_matches[0]) if len(odds_matches) >= 1 else None
        home_odds = int(odds_matches[1]) if len(odds_matches) >= 2 else None

        # Emit a "public consensus" pick for the higher-% side.
        # This is what users care about: which side the public is on.
        if home_pct is not None and away_pct is not None:
            if home_pct > away_pct:
                pick_side, pick_odds, pct = 'HOME', home_odds, home_pct
            else:
                pick_side, pick_odds, pct = 'AWAY', away_odds, away_pct
            fade_flag = 'boost' if pct >= 70 and pick_odds and pick_odds <= -150 else 'neutral'
            picks.append(ExternalPick(
                game_id=gid, sport='MLB', game_date=game_date, source='covers',
                surface='ml', pick_side=pick_side, odds_american=pick_odds,
                confidence=f'{pct}% public',
                raw_text=f'Public consensus: {away_pct}% away / {home_pct}% home @ {away_odds}/{home_odds}',
                fade_flag=fade_flag,
            ))
    return picks, 200


def _covers_date_to_iso(date_txt: str) -> str:
    """Convert 'Tue. Jul 21 9:40 pm ET' → '2026-07-21'. Best-effort."""
    from datetime import datetime as _dt
    m = re.search(r'(\w{3})\.?\s+(\w{3})\s+(\d+)', date_txt)
    if not m:
        return ''
    month_abbr, day = m.group(2), m.group(3)
    year = _et_now().year
    try:
        d = _dt.strptime(f'{month_abbr} {day} {year}', '%b %d %Y').date()
        return d.isoformat()
    except Exception:
        return ''


# Team abbreviation map for Covers short codes → our team names.
# Covers uses non-standard 2-3 letter codes; map them carefully.
_COVERS_ABBREV = {
    'Ath': 'Athletics', 'Az': 'Arizona Diamondbacks', 'Atl': 'Atlanta Braves',
    'Bal': 'Baltimore Orioles', 'Bos': 'Boston Red Sox', 'ChC': 'Chicago Cubs',
    'ChW': 'Chicago White Sox', 'Cin': 'Cincinnati Reds', 'Cle': 'Cleveland Guardians',
    'Col': 'Colorado Rockies', 'Det': 'Detroit Tigers', 'Hou': 'Houston Astros',
    'KC': 'Kansas City Royals', 'LA': 'Los Angeles Dodgers', 'LAA': 'Los Angeles Angels',
    'Mia': 'Miami Marlins', 'Mil': 'Milwaukee Brewers', 'Min': 'Minnesota Twins',
    'NYM': 'New York Mets', 'NYY': 'New York Yankees', 'Phi': 'Philadelphia Phillies',
    'Pit': 'Pittsburgh Pirates', 'SD': 'San Diego Padres', 'SF': 'San Francisco Giants',
    'Sea': 'Seattle Mariners', 'Stl': 'St. Louis Cardinals', 'TB': 'Tampa Bay Rays',
    'Tex': 'Texas Rangers', 'Tor': 'Toronto Blue Jays', 'Was': 'Washington Nationals',
}


def _match_covers_abbrev_to_gid(slate: list, away_code: str, home_code: str) -> Optional[str]:
    away_full = _COVERS_ABBREV.get(away_code, away_code)
    home_full = _COVERS_ABBREV.get(home_code, home_code)
    return find_game_id(slate, home_hint=home_full, away_hint=away_full)


def fetch_cbs(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_action(slate: list, game_date: str) -> tuple[list, int]:
    """Action Network public betting — JS-rendered via Playwright.

    Public $% / bet% split is Pro-locked, but the bet% column is free.
    Layout (moneyline view):
        9:38 PM
        Cardinals
        975
        Angels
        976

        -116 / -105
        +113 / -115
        48% / 52%   <- bet %

    We surface which side the public is on. Money % gap (the true sharp
    signal) requires Pro API access — deferred to Phase 3.
    """
    from _playwright_helper import render_page
    url = 'https://www.actionnetwork.com/mlb/public-betting'
    text, err = render_page(url, wait_ms=6000, wait_until='networkidle', timeout_ms=45000)
    if err == 'unavailable':
        print('  ⚠ Action: Playwright unavailable — skip')
        return [], 200
    if err:
        print(f'  ⚠ Action render error: {err}')
        return [], 500
    if not text:
        return [], 200

    picks = []
    seen = set()
    # Pattern: "TeamA\n<3-digit-id>\nTeamB\n<3-digit-id>\n(...odds...)\n<pct>%\n<pct>%"
    # The two %s at the end are the bet% for away/home respectively.
    chunk_re = re.compile(
        r'([A-Z][A-Za-z ]{2,20}?)\s*\n\s*\d{3,4}\s*\n'
        r'\s*([A-Z][A-Za-z ]{2,20}?)\s*\n\s*\d{3,4}\s*\n'
        r'.*?(\d{1,3})%\s*\n\s*(\d{1,3})%',
        re.DOTALL,
    )
    for m in chunk_re.finditer(text):
        away_name = m.group(1).strip()
        home_name = m.group(2).strip()
        away_pct = int(m.group(3))
        home_pct = int(m.group(4))
        # Sanity: percentages should sum ~100
        if not (90 <= away_pct + home_pct <= 110):
            continue
        gid = None
        for g in slate:
            if _team_matches(g.get('away_team'), away_name) and \
               _team_matches(g.get('home_team'), home_name):
                gid = g['game_id']; break
        if not gid or gid in seen:
            continue
        seen.add(gid)
        if home_pct > away_pct:
            side, pct = 'HOME', home_pct
        else:
            side, pct = 'AWAY', away_pct
        # Audit tag: >=70% public + heavy fav → boost (public+sharp align)
        # else neutral (mid-gap trap zone was covered by money%, which we lack)
        fade = 'boost' if pct >= 70 else 'neutral'
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='action',
            surface='ml', pick_side=side, odds_american=None,
            confidence=f'{pct}% public bets',
            raw_text=f'Action bet%: {away_name} {away_pct}% / {home_name} {home_pct}%',
            fade_flag=fade,
        ))
    return picks, 200


def fetch_vsin(slate: list, game_date: str) -> tuple[list, int]:
    """VSiN — Peterson's daily MLB best bets column.

    Landing page has an article link with title matching "MLB Picks Today: Greg
    Peterson Best Bets". We fetch the article, extract the best-bet picks.
    """
    from bs4 import BeautifulSoup
    landing = requests.get(
        'https://vsin.com/mlb/',
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=10,
    )
    if landing.status_code != 200:
        return [], landing.status_code
    lsoup = BeautifulSoup(landing.text, 'html.parser')

    # Find Peterson's MLB article link — needs to be scoped to /mlb/ path
    # (bare 'peterson in href' also matches his college basketball columns)
    peterson_link = None
    for a in lsoup.find_all('a', href=True):
        href = a.get('href', '').lower()
        title = a.get_text(strip=True).lower()
        is_mlb = '/mlb/' in href
        is_peterson = 'peterson' in href or 'peterson' in title
        if is_mlb and is_peterson and 'best bets' in title:
            peterson_link = a.get('href')
            break

    if not peterson_link:
        return [], 200
    if peterson_link.startswith('/'):
        peterson_link = 'https://vsin.com' + peterson_link

    article = requests.get(peterson_link, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    if article.status_code != 200:
        return [], article.status_code
    asoup = BeautifulSoup(article.text, 'html.parser')
    text = asoup.get_text(' ', strip=True)

    picks = []
    # Pattern: "TEAM Moneyline -ODDS" — Peterson's format
    ml_matches = re.findall(
        r'([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)\s+Moneyline\s+([+-]\d{2,4})',
        text,
    )
    seen = set()
    for team, odds in ml_matches[:15]:
        if team in seen:
            continue
        seen.add(team)
        # Match team → game
        for g in slate:
            if _team_matches(g.get('home_team'), team):
                pick_side, gid = 'HOME', g['game_id']
                break
            if _team_matches(g.get('away_team'), team):
                pick_side, gid = 'AWAY', g['game_id']
                break
        else:
            continue
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='vsin',
            surface='ml', pick_side=pick_side, odds_american=int(odds),
            raw_text=f"VSiN Peterson: {team} ML ({odds})",
            source_url=peterson_link,
        ))
    return picks, 200


def fetch_bettingpros(slate: list, game_date: str) -> tuple[list, int]:
    """BettingPros MLB — JS-rendered player-prop picks via Playwright.

    Free tier surfaces top player prop picks with:
        Luis Castillo             <- player name
        U 17.5 OUTS (+102) vs CIN <- pick text
        65% Cover                 <- EV cover rate
        +31% EV                   <- expected value
        Proj: 16.1                <- projection
        5 out of 5 stars          <- star rating

    Attribution: BettingPros (bettingpros.com/mlb/picks/).
    Surface: 'prop'. Audit tag: BOOST when stars=5 AND ev%>=+15.
    """
    from _playwright_helper import render_page
    text, err = render_page('https://www.bettingpros.com/mlb/picks/')
    if err == 'unavailable':
        print('  ⚠ BettingPros: Playwright unavailable — skip')
        return [], 200
    if err:
        print(f'  ⚠ BettingPros render error: {err}')
        return [], 500
    if not text:
        return [], 200

    picks = []
    # Match: "Player Name\nU/O <line> <stat> (odds) vs <TEAM>"
    prop_re = re.compile(
        r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\s*\n\s*'      # Player name (2+ words)
        r'(U|O)\s+([\d.]+)\s+([A-Z]{1,6})\s+'            # U/O line STAT
        r'\(([+-]\d{2,4})\)\s+vs\s+([A-Z]{2,4})',        # (odds) vs TEAM
    )
    for m in prop_re.finditer(text):
        player = m.group(1).strip()
        side = 'UNDER' if m.group(2).upper() == 'U' else 'OVER'
        line = float(m.group(3))
        stat = m.group(4)
        odds = int(m.group(5))
        opp_code = m.group(6)

        # Grab star rating + EV from a nearby window (next ~400 chars)
        window = text[m.end(): m.end() + 400]
        star_m = re.search(r'(\d)\s*out of 5 stars', window)
        stars = int(star_m.group(1)) if star_m else None
        ev_m = re.search(r'([+-]\d+)% EV', window)
        ev_pct = int(ev_m.group(1)) if ev_m else None
        cov_m = re.search(r'(\d+)% Cover', window)
        cov_pct = int(cov_m.group(1)) if cov_m else None

        # Map player → game via opp_code
        opp_full = _PICKSWISE_CODES.get(opp_code, opp_code)
        gid = None
        for g in slate:
            if _team_matches(g.get('home_team'), opp_full) or \
               _team_matches(g.get('away_team'), opp_full):
                gid = g['game_id']; break
        if not gid: continue

        fade = 'boost' if (stars == 5 and ev_pct and ev_pct >= 15) else \
               ('trust' if stars and stars >= 4 else 'neutral')
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='bettingpros',
            surface='prop', pick_side=side, pick_line=line, odds_american=odds,
            confidence=(f'{stars}-star' if stars else None),
            raw_text=(f'BettingPros: {player} {side} {line} {stat} ({odds}) '
                      f'vs {opp_code}'
                      + (f' [{stars}★' if stars else '')
                      + (f' {ev_pct:+d}%EV' if ev_pct is not None else '')
                      + (f' {cov_pct}%cov' if cov_pct is not None else '')
                      + (']' if stars else '')),
            fade_flag=fade,
        ))
    return picks, 200


def fetch_oddsshark(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_pickswise(slate: list, game_date: str) -> tuple[list, int]:
    """Pickswise MLB picks — inline listing with 1-5 star ratings.

    Page layout: each pick renders as a card with matchup abbreviations
    (e.g. "MIN vs CLE"), pick text ("Moneyline - Cleveland Guardians"),
    odds ("-2000"), and a star row (⭐ × N). We match matchup to slate,
    parse pick side + odds, and OVERRIDE fade_flag to 'fade' on 5-STAR
    picks per 7/20 audit (5-star = public-heat = counterindicator).
    """
    from bs4 import BeautifulSoup
    r = requests.get(
        'https://www.pickswise.com/mlb/picks/',
        headers={'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'},
        timeout=12,
    )
    if r.status_code != 200:
        return [], r.status_code
    soup = BeautifulSoup(r.text, 'html.parser')
    text = soup.get_text('\n', strip=True)

    picks = []
    seen_games = set()

    # Anchor on the pick text itself, then match team back to the slate.
    # Pickswise renders picks as: "Moneyline - Cleveland Guardians\n-2400\nBet Now"
    ml_re = re.compile(
        r'Moneyline\s*[-–]\s*([A-Z][A-Za-z. ]+?)\s*\n\s*([+-]\d{2,4})\b',
    )
    total_re = re.compile(
        r'\b(Over|Under)\s+([\d.]+)\s*\n\s*([+-]\d{2,4})\b', re.I,
    )
    # Runline / spread pattern: "Run Line - Yankees -1.5\n+120"
    rl_re = re.compile(
        r'Run Line\s*[-–]\s*([A-Z][A-Za-z. ]+?)\s+([+-][\d.]+)\s*\n\s*([+-]\d{2,4})\b',
    )

    def _stars_near(pos: int) -> Optional[int]:
        window = text[max(0, pos-400): pos+200]
        n = window.count('⭐') + window.count('★')
        return n if 1 <= n <= 5 else None

    for m in ml_re.finditer(text):
        team_name = m.group(1).strip()
        odds = int(m.group(2))
        stars = _stars_near(m.start())
        side, gid = None, None
        for g in slate:
            if _team_matches(g.get('home_team'), team_name):
                side, gid = 'HOME', g['game_id']; break
            if _team_matches(g.get('away_team'), team_name):
                side, gid = 'AWAY', g['game_id']; break
        if side is None or gid in seen_games:
            continue
        fade = 'fade' if stars and stars >= 5 else ('boost' if stars and stars >= 4 else 'neutral')
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='pickswise',
            surface='ml', pick_side=side, odds_american=odds,
            confidence=f'{stars}-star' if stars else None,
            raw_text=f'Pickswise: {team_name} ML {odds}' + (f' [{stars}⭐]' if stars else ''),
            fade_flag=fade,
        ))
        seen_games.add(gid)

    for m in total_re.finditer(text):
        side = m.group(1).upper()
        line = float(m.group(2))
        odds = int(m.group(3))
        stars = _stars_near(m.start())
        # Total picks need a matchup — Pickswise usually shows two full team
        # names within ~300 chars above the total pick. Extract both.
        pre = text[max(0, m.start()-500): m.start()]
        team_hits = []
        for g in slate:
            for team_field in ('home_team', 'away_team'):
                t = g.get(team_field)
                if t and t.lower() in pre.lower():
                    team_hits.append((g['game_id'], team_field, t))
        gids_in_pre = {g for g, _, _ in team_hits}
        if len(gids_in_pre) != 1: continue  # ambiguous — skip
        gid = gids_in_pre.pop()
        if gid in seen_games: continue
        fade = 'fade' if stars and stars >= 5 else ('boost' if stars and stars >= 4 else 'neutral')
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='pickswise',
            surface='total', pick_side=side, pick_line=line, odds_american=odds,
            confidence=f'{stars}-star' if stars else None,
            raw_text=f'Pickswise: {side} {line} ({odds})' + (f' [{stars}⭐]' if stars else ''),
            fade_flag=fade,
        ))
        seen_games.add(gid)

    for m in rl_re.finditer(text):
        team_name = m.group(1).strip()
        rl_line = float(m.group(2))
        odds = int(m.group(3))
        stars = _stars_near(m.start())
        side, gid = None, None
        for g in slate:
            if _team_matches(g.get('home_team'), team_name):
                side, gid = 'HOME', g['game_id']; break
            if _team_matches(g.get('away_team'), team_name):
                side, gid = 'AWAY', g['game_id']; break
        if side is None or gid in seen_games: continue
        fade = 'fade' if stars and stars >= 5 else ('boost' if stars and stars >= 4 else 'neutral')
        picks.append(ExternalPick(
            game_id=gid, sport='MLB', game_date=game_date, source='pickswise',
            surface='rl', pick_side=side, pick_line=rl_line, odds_american=odds,
            confidence=f'{stars}-star' if stars else None,
            raw_text=f'Pickswise: {team_name} RL {rl_line} ({odds})' + (f' [{stars}⭐]' if stars else ''),
            fade_flag=fade,
        ))
        seen_games.add(gid)

    return picks, 200


# Pickswise / most sportsbook 2-3 letter code map
_PICKSWISE_CODES = {
    'ATH': 'Athletics', 'ARI': 'Arizona Diamondbacks', 'ATL': 'Atlanta Braves',
    'BAL': 'Baltimore Orioles', 'BOS': 'Boston Red Sox', 'CHC': 'Chicago Cubs',
    'CWS': 'Chicago White Sox', 'CHW': 'Chicago White Sox', 'CIN': 'Cincinnati Reds',
    'CLE': 'Cleveland Guardians', 'COL': 'Colorado Rockies', 'DET': 'Detroit Tigers',
    'HOU': 'Houston Astros', 'KC': 'Kansas City Royals', 'KCR': 'Kansas City Royals',
    'LAD': 'Los Angeles Dodgers', 'LAA': 'Los Angeles Angels', 'MIA': 'Miami Marlins',
    'MIL': 'Milwaukee Brewers', 'MIN': 'Minnesota Twins', 'NYM': 'New York Mets',
    'NYY': 'New York Yankees', 'PHI': 'Philadelphia Phillies', 'PIT': 'Pittsburgh Pirates',
    'SD': 'San Diego Padres', 'SDP': 'San Diego Padres', 'SF': 'San Francisco Giants',
    'SFG': 'San Francisco Giants', 'SEA': 'Seattle Mariners', 'STL': 'St. Louis Cardinals',
    'TB': 'Tampa Bay Rays', 'TBR': 'Tampa Bay Rays', 'TEX': 'Texas Rangers',
    'TOR': 'Toronto Blue Jays', 'WAS': 'Washington Nationals', 'WSH': 'Washington Nationals',
}


def _match_pickswise_codes_to_gid(slate: list, away_code: str, home_code: str) -> Optional[str]:
    away_full = _PICKSWISE_CODES.get(away_code, away_code)
    home_full = _PICKSWISE_CODES.get(home_code, home_code)
    return find_game_id(slate, home_hint=home_full, away_hint=away_full)


def fetch_pickdawgz(slate: list, game_date: str) -> tuple[list, int]:
    """PickDawgz free MLB picks — landing has article cards with headline
    format "Team A vs Team B Prediction M/D/YYYY". Crawl each linked article
    for the actual pick + odds. Mirrors Doc Sports pattern.
    """
    from bs4 import BeautifulSoup
    landing = requests.get(
        'https://www.pickdawgz.com/mlb-picks',
        headers={'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'},
        timeout=12,
    )
    if landing.status_code != 200:
        return [], landing.status_code
    soup = BeautifulSoup(landing.text, 'html.parser')

    # PickDawgz uses TWO date formats depending on surface:
    #   - Headlines:  "7/28/2026"  (slashes)
    #   - URL slugs:  "prediction-7-28-2026"  (dashes, M-D-YYYY, NOT ISO)
    parts = game_date.split('-')  # ['2026','07','28']
    date_slug_headline = f'{int(parts[1])}/{int(parts[2])}/{parts[0]}'   # 7/28/2026
    date_slug_url = f'{int(parts[1])}-{int(parts[2])}-{parts[0]}'         # 7-28-2026
    date_iso_url = f'{parts[0]}-{parts[1]}-{parts[2]}'                    # 2026-07-28 (safety)

    article_urls = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        headline = a.get_text(' ', strip=True)
        # Match either date format anywhere in headline OR URL
        if (date_slug_headline in headline or date_slug_url in href
                or date_slug_url in headline or date_iso_url in href):
            if 'prediction' in href.lower() or 'pick' in href.lower():
                if href.startswith('/'):
                    href = 'https://www.pickdawgz.com' + href
                if href.startswith('http'):
                    article_urls.add(href)

    if not article_urls:
        return [], 200

    picks = []
    for url in list(article_urls)[:25]:  # up to 25 games — safely covers 16-game MLB slate
        try:
            article = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            if article.status_code != 200:
                continue
            asoup = BeautifulSoup(article.text, 'html.parser')
            title = (asoup.find('h1') or asoup.find('title'))
            title_txt = title.get_text(' ', strip=True) if title else ''
            body = asoup.get_text(' ', strip=True)

            # Extract matchup from headline: "Oakland Athletics vs Arizona Diamondbacks..."
            matchup_re = re.search(
                r'([A-Z][A-Za-z .]+?)\s+vs\.?\s+([A-Z][A-Za-z .]+?)\s+(?:Prediction|Pick|Today)',
                title_txt,
            )
            if not matchup_re:
                continue
            away_hint, home_hint = matchup_re.group(1).strip(), matchup_re.group(2).strip()
            gid = find_game_id(slate, home_hint=home_hint, away_hint=away_hint)
            if not gid:
                continue

            # PickDawgz pick format at the author sign-off, e.g.:
            #   "Nikos Lagouretos's Pick: Boston Red Sox ML Need More? Get Premium"
            # Article body is a single line (soup.get_text(' ')) so we anchor on the
            # apostrophe-s Pick pattern and stop at the "Need More" / "Get Premium"
            # tail that always follows the pick. Support both straight ' and curly '.
            pick_matches = list(re.finditer(
                r"[A-Za-z]+[’']s\s+Pick\s*:\s*([A-Z][A-Za-z .]{2,60}?)"
                r"(?:\s+(?:ML|Moneyline|-1\.5|\+1\.5|RL|Run Line))?"
                r"\s*(?:\(([+-]\d{2,4})\))?"
                r"\s+(?:Need More|Get Premium|Hot Cappers|Buy|Add to)",
                body[:15000],
            ))
            total_matches = list(re.finditer(
                r"[A-Za-z]+[’']s\s+Pick\s*:\s*(Over|Under)\s+([\d.]+)"
                r"\s*(?:\(([+-]\d{2,4})\))?"
                r"\s+(?:Need More|Get Premium|Hot Cappers)",
                body[:15000], re.I,
            ))

            emitted = False
            if pick_matches:
                # Use the LAST match (author's sign-off pick)
                pm = pick_matches[-1]
                team = pm.group(1).strip()
                odds = int(pm.group(2)) if pm.group(2) else None
                # Clean team name — strip trailing "ML" or "Moneyline"
                team = re.sub(r'\s+(?:ML|Moneyline|RL|Run Line|\-?\d+\.\d+)\s*$', '', team, flags=re.I).strip()
                side = None
                if home_hint.lower().find(team.lower()) >= 0 or team.lower().find(home_hint.lower()) >= 0:
                    side = 'HOME'
                elif away_hint.lower().find(team.lower()) >= 0 or team.lower().find(away_hint.lower()) >= 0:
                    side = 'AWAY'
                if side:
                    picks.append(ExternalPick(
                        game_id=gid, sport='MLB', game_date=game_date, source='pickdawgz',
                        surface='ml', pick_side=side, odds_american=odds,
                        raw_text=f'PickDawgz: {team} ML' + (f' ({odds})' if odds else ''),
                        source_url=url,
                    ))
                    emitted = True

            if not emitted and total_matches:
                tm2 = total_matches[-1]
                side = tm2.group(1).upper()
                line = float(tm2.group(2))
                odds = int(tm2.group(3)) if tm2.group(3) else None
                picks.append(ExternalPick(
                    game_id=gid, sport='MLB', game_date=game_date, source='pickdawgz',
                    surface='total', pick_side=side, pick_line=line, odds_american=odds,
                    raw_text=f'PickDawgz: {side} {line}' + (f' ({odds})' if odds else ''),
                    source_url=url,
                ))
            time.sleep(0.4)  # be polite
        except Exception:
            continue

    return picks, 200


def fetch_docsports(slate: list, game_date: str) -> tuple[list, int]:
    """Doc Sports free MLB picks — crawls today's article links + parses pick.

    Landing page has ~40 links to individual game articles for today+tomorrow.
    Article URL format: /baseball/2026/<away-team>-vs-<home-team>-prediction-M-D-YYYY-...

    Each article contains the pick + short reasoning. We fetch each article
    and extract the recommendation.
    """
    from bs4 import BeautifulSoup
    landing = requests.get(
        'https://www.docsports.com/free-picks/baseball/',
        headers={'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'},
        timeout=10,
    )
    if landing.status_code != 200:
        return [], landing.status_code

    soup = BeautifulSoup(landing.text, 'html.parser')
    # Convert 2026-07-21 → 7-21-2026 (Doc Sports URL format)
    parts = game_date.split('-')  # ['2026','07','21']
    date_slug = f'{int(parts[1])}-{int(parts[2])}-{parts[0]}'
    all_links = soup.find_all('a', href=re.compile(f'/baseball/2026/.+{date_slug}'))
    unique_urls = list({a.get('href') for a in all_links if a.get('href')})
    if not unique_urls:
        return [], 200

    picks = []
    for url in unique_urls[:15]:  # cap at slate size
        try:
            article = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            if article.status_code != 200:
                continue
            asoup = BeautifulSoup(article.text, 'html.parser')
            text = asoup.get_text(' ', strip=True)

            # Extract matchup from URL: /baseball/2026/cincinnati-reds-vs-seattle-mariners-...
            slug_match = re.search(r'/baseball/2026/([a-z\-]+)-vs-([a-z\-]+)-prediction', url)
            if not slug_match:
                continue
            away_slug = slug_match.group(1).replace('-', ' ').title()
            home_slug = slug_match.group(2).replace('-', ' ').title()
            gid = find_game_id(slate, home_hint=home_slug, away_hint=away_slug)
            if not gid:
                continue

            # Find "'s Pick: Take TEAM (+ODDS)" pattern — Doc Sports format
            pick_match = re.search(
                r"Pick:\s+(?:Take\s+)?([A-Z][a-zA-Z\s]+?)\s*\(([+-]\d+)\)",
                text[:10000],
            )
            if not pick_match:
                # Try Under/Over pattern
                pick_match = re.search(
                    r"Pick:\s+(?:Take\s+)?(Under|Over)\s+([\d.]+)",
                    text[:10000], re.I,
                )
                if pick_match:
                    side = pick_match.group(1).upper()
                    line = float(pick_match.group(2))
                    picks.append(ExternalPick(
                        game_id=gid, sport='MLB', game_date=game_date, source='docsports',
                        surface='total', pick_side=side, pick_line=line,
                        raw_text=f'Doc Sports: {side} {line}',
                        source_url=url,
                    ))
                    continue
                continue

            team = pick_match.group(1).strip()
            odds = int(pick_match.group(2))
            # Determine home/away
            pick_side = 'HOME' if team.lower() in home_slug.lower() or home_slug.lower() in team.lower() else 'AWAY'
            picks.append(ExternalPick(
                game_id=gid, sport='MLB', game_date=game_date, source='docsports',
                surface='ml', pick_side=pick_side, odds_american=odds,
                raw_text=f'Doc Sports: {team} ML ({odds})',
                source_url=url,
            ))
            time.sleep(0.5)  # be polite
        except Exception as e:
            continue

    return picks, 200


def fetch_scp(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_fangraphs(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_ballparkpal(slate: list, game_date: str) -> tuple[list, int]:
    """Wind/park factor calls. Auto-tag as fade per audit."""
    return [], 200


# ─────────────────────────────────────────────────────────────
# OddsCrowd — per-market Money% / Bets% for every game
# ─────────────────────────────────────────────────────────────
# Data-collection-only signal (NOT wired into scorer). Money%/bets% divergence
# is the classic sharp-money proxy — we capture it to backtest patterns like
# "when our model X + oddscrowd sharp ≥+10pp align, hit rate = Y".
# Actual scraping lives in externals_oddscrowd.py so every sport reuses it.
def fetch_oddscrowd(slate: list, game_date: str) -> tuple[list, int]:
    from externals_oddscrowd import fetch_oddscrowd_generic
    picks_dicts, status = fetch_oddscrowd_generic(
        sport_url_slug='baseball',
        league_slug='mlb',
        sport_code='MLB',
        game_date=game_date,
        slate=slate,
        find_game_id_fn=find_game_id,
    )
    picks = []
    for d in picks_dicts:
        picks.append(ExternalPick(
            game_id=d['game_id'], sport=d['sport'], game_date=d['game_date'],
            source=d['source'], surface=d['surface'], pick_side=d['pick_side'],
            pick_line=d['pick_line'], odds_american=d['odds_american'],
            confidence=d['confidence'], raw_text=d['raw_text'],
            source_url=d['source_url'], fade_flag=d['fade_flag'],
        ))
    return picks, status


def fetch_sbr(slate: list, game_date: str) -> tuple[list, int]:
    """SportsBookReview consensus — thin wrapper around externals_consensus.
    See externals_consensus.py::fetch_sbr for parser + fade-flag policy."""
    from externals_consensus import fetch_sbr as _sbr
    picks_dicts, status = _sbr(slate, game_date, find_game_id)
    picks = []
    for d in picks_dicts:
        picks.append(ExternalPick(
            game_id=d['game_id'], sport='MLB', game_date=game_date,
            source=d['source'], surface=d['surface'], pick_side=d['pick_side'],
            confidence=d.get('confidence'), raw_text=d.get('raw_text'),
            source_url=d.get('source_url'), fade_flag=d.get('fade_flag'),
        ))
    return picks, status


FETCHERS = {
    'dimers': fetch_dimers,
    'covers': fetch_covers,
    'cbs': fetch_cbs,
    'action': fetch_action,
    'vsin': fetch_vsin,
    'bettingpros': fetch_bettingpros,
    'oddsshark': fetch_oddsshark,
    'pickswise': fetch_pickswise,
    'pickdawgz': fetch_pickdawgz,
    'docsports': fetch_docsports,
    'scp': fetch_scp,
    'fangraphs': fetch_fangraphs,
    'ballparkpal': fetch_ballparkpal,
    'oddscrowd': fetch_oddscrowd,
    'sbr': fetch_sbr,
}


# ─────────────────────────────────────────────────────────────
# Main pull orchestrator
# ─────────────────────────────────────────────────────────────
def run_pull(game_date: str, sources: list, triggered_by: str,
             dry_run: bool = False) -> dict:
    """Run pulls for the given sources on the given date. Returns summary."""
    print(f'\n=== MLB external pull · {game_date} · {triggered_by} ===')
    slate = load_slate(game_date)
    print(f'  slate: {len(slate)} games')
    if not slate:
        print('  ⚠ no games on slate — abort')
        return {'games': 0, 'sources_pulled': 0, 'picks_written': 0}

    scheduled_at = datetime.now(timezone.utc)
    summary = {
        'games': len(slate),
        'sources_pulled': 0,
        'sources_failed': 0,
        'picks_written': 0,
        'source_records': [],
    }

    for source in sources:
        cfg = SOURCE_REGISTRY.get(source)
        if not cfg:
            print(f'  ⚠ unknown source: {source} — skip')
            continue
        fetcher = FETCHERS.get(source)
        if not fetcher:
            print(f'  ⚠ no fetcher for {source} — skip')
            continue

        pull_id = None
        if not dry_run:
            pull_id = start_pull_log(
                source, 'MLB', scheduled_at,
                triggered_by=triggered_by, source_url=cfg['base_url'],
            )

        started = time.time()
        try:
            picks, http_status = fetcher(slate, game_date)
            # Attach source_url + fade_flag defaults + ttl if not set
            for p in picks:
                if not p.source_url:
                    p.source_url = cfg['base_url']
                if not p.fade_flag:
                    p.fade_flag = cfg['fade_flag']

            games_covered = len({p.game_id for p in picks})
            duration_ms = int((time.time() - started) * 1000)

            if dry_run:
                print(f'  [DRY] {cfg["label"]}: {len(picks)} picks / {games_covered} games / {duration_ms}ms')
                for p in picks[:3]:
                    print(f'      {p.game_id[:12]}... {p.surface}:{p.pick_side} {p.confidence or ""}')
            else:
                count = write_picks(picks, pull_id)
                complete_pull_log(
                    pull_id, status='success',
                    picks_pulled=count, games_covered=games_covered,
                    http_status=http_status, duration_ms=duration_ms,
                )
                print(f'  ✓ {cfg["label"]}: {count} picks / {games_covered} games / {duration_ms}ms')
                summary['picks_written'] += count

            summary['sources_pulled'] += 1
            summary['source_records'].append({
                'source': source, 'picks': len(picks),
                'games': games_covered, 'duration_ms': duration_ms,
            })
        except Exception as e:
            duration_ms = int((time.time() - started) * 1000)
            if not dry_run:
                complete_pull_log(
                    pull_id, status='failed',
                    error_message=f'{type(e).__name__}: {e}',
                    duration_ms=duration_ms,
                )
            print(f'  ✗ {cfg["label"]}: FAILED — {type(e).__name__}: {e}')
            summary['sources_failed'] += 1

    print(f'\n=== Summary ===')
    print(f'  Sources OK/FAIL: {summary["sources_pulled"]}/{summary["sources_failed"]}')
    print(f'  Picks written:   {summary["picks_written"]}')
    print(f'  Games covered:   up to {summary["games"]}')

    # Compute alignment + oddscrowd snapshot for the app UX layer
    if not dry_run and summary['picks_written'] > 0:
        try:
            from compute_align_status import run as compute_align_run
            print('\n=== Alignment + oddscrowd snapshot ===')
            compute_align_run(game_date=game_date, dry_run=False)
        except Exception as e:
            print(f'  ⚠ compute_align_status failed: {type(e).__name__}: {e}')

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None,
                    help='YYYY-MM-DD (defaults to today ET)')
    ap.add_argument('--refresh', action='store_true',
                    help='5 PM refresh pull (only sources that update mid-day)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--source', default=None,
                    help='Test a single source in isolation')
    args = ap.parse_args()

    date = args.date or _today_et()

    if args.source:
        sources = [args.source]
        triggered_by = f'manual:single:{args.source}'
    elif args.refresh:
        # 5 PM refresh — only sources that publish late (Action Network sharp $,
        # some VSiN column drops, Doc Sports finalizations).
        sources = ['action', 'vsin', 'docsports', 'bettingpros']
        triggered_by = 'cron:5pm_mlb_refresh'
    else:
        # Noon primary pull — everything
        sources = list(SOURCE_REGISTRY.keys())
        triggered_by = 'cron:noon_mlb'

    run_pull(date, sources, triggered_by, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
