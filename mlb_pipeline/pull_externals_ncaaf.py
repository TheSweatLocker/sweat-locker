"""External picks pull — NCAAF (Wed 6pm ET + Sat 10am ET during season).

2026-07-23: Ports the NFL template (pull_externals_nfl.py) with NCAAF
sport-slug URLs. All shared scrapers (Playwright helper, source registry
shape, pull_log flow, ExternalPick dataclass, write_picks/complete_pull_log)
reused from the same code paths — differences are:
  1. sport='NCAAF' throughout
  2. Sport slugs per site — CFB varies widely: /cfb/ (Dimers, BettingPros),
     /ncaaf/ (Covers, Action), /college-football/ (VSiN, Pickswise),
     /college-football-picks (PickDawgz)
  3. Team-name aliases from ncaaf_team_aliases (134 FBS teams)
  4. Weekly cadence (Wed pre-slate + Sat lock) — same as NFL
  5. FCS opponents skipped (not in alias table) — correct for FBS-only v1.0

Source lineup (start subset — expand as we validate structure per site):
  BOOST:   Dimers CFB wp, Action Network sharp $
  TRUST:   Covers CFB consensus, BettingPros expert picks, PickDawgz free
  NEUTRAL: Pickswise 3-star, VSiN CFB Peterson
  FADE:    Pickswise 5-STAR (audit-flagged public heat)

Every pull writes:
  1. Row(s) in external_pull_log (one per source × timestamp)
  2. Rows in external_picks with pull_id FK

USAGE:
  python pull_externals_ncaaf.py                        # weekly cron pull
  python pull_externals_ncaaf.py --refresh              # Sat 10am refresh
  python pull_externals_ncaaf.py --date 2026-09-06      # specific week
  python pull_externals_ncaaf.py --dry-run              # print, no write
  python pull_externals_ncaaf.py --source pickswise     # single source test
"""
import argparse
import os
import re
import sys
import time
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional
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
# Source registry — NCAAF sport-slug per URL (varies per site)
# ─────────────────────────────────────────────────────────────
SOURCE_REGISTRY = {
    'dimers': {
        'fade_flag': 'boost', 'ttl_hours': 48,
        'base_url': 'https://www.dimers.com/bet-hub/cfb/schedule',
        'label': 'Dimers CFB',
    },
    'covers': {
        'fade_flag': 'trust', 'ttl_hours': 48,
        'base_url': 'https://contests.covers.com/consensus/topconsensus/ncaaf/overall',
        'label': 'Covers CFB',
    },
    'action': {
        'fade_flag': 'trust', 'ttl_hours': 24,
        'base_url': 'https://www.actionnetwork.com/ncaaf/public-betting',
        'label': 'Action Network CFB',
    },
    'vsin': {
        'fade_flag': 'trust', 'ttl_hours': 48,
        'base_url': 'https://vsin.com/college-football/',
        'label': 'VSiN CFB',
    },
    'bettingpros': {
        'fade_flag': 'trust', 'ttl_hours': 48,
        'base_url': 'https://www.bettingpros.com/cfb/picks/',
        'label': 'BettingPros CFB',
    },
    'pickswise': {
        'fade_flag': 'neutral', 'ttl_hours': 48,
        'base_url': 'https://www.pickswise.com/college-football/picks/',
        'label': 'Pickswise CFB',
    },
    'pickdawgz': {
        'fade_flag': 'trust', 'ttl_hours': 48,
        'base_url': 'https://www.pickdawgz.com/college-football-picks',
        'label': 'PickDawgz CFB',
    },
    'oddscrowd': {
        'fade_flag': 'trust', 'ttl_hours': 12,
        'base_url': 'https://oddscrowd.com/games/upcoming/football',
        'label': 'OddsCrowd CFB',
    },
    'scoresandodds': {
        'fade_flag': 'trust', 'ttl_hours': 12,   # 2026-08-25 SO scraper
        'base_url': 'https://www.scoresandodds.com/ncaaf',
        'label': 'ScoresAndOdds CFB',
    },
}


@dataclass
class ExternalPick:
    game_id: str
    sport: str
    game_date: str
    source: str
    surface: str        # 'ml' | 'total' | 'rl' | 'prop' | 'sharp_signal'
    pick_side: Optional[str] = None      # 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
    pick_line: Optional[float] = None
    odds_american: Optional[int] = None
    confidence: Optional[str] = None
    raw_text: Optional[str] = None
    source_url: Optional[str] = None
    fade_flag: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Pull log helpers (identical to MLB)
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
    pull_id = str(uuid.uuid4())
    payload = {
        'pull_id': pull_id, 'sport': sport, 'source': source,
        'scheduled_at': scheduled_at.isoformat(),
        'status': 'running', 'triggered_by': triggered_by,
        'source_url': source_url, 'agent_version': _git_sha(),
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
    if not pull_id: return
    payload = {
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'status': status, 'picks_pulled': picks_pulled,
        'games_covered': games_covered, 'error_message': error_message,
        'http_status': http_status, 'duration_ms': duration_ms,
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
    if not picks: return 0
    payload = []
    for p in picks:
        d = asdict(p); d['pull_id'] = pull_id
        payload.append(d)
    try:
        r = requests.post(
            f'{SB}/rest/v1/external_picks',
            headers={**H_WRITE, 'Prefer': 'return=minimal'},
            json=payload, timeout=20,
        )
        if r.status_code not in (200, 201, 204):
            print(f'  ⚠ picks write failed {r.status_code}: {r.text[:120]}')
            return 0
        return len(payload)
    except Exception as e:
        print(f'  ⚠ picks write exception: {e}')
        return 0


# ─────────────────────────────────────────────────────────────
# NCAAF slate lookup — ncaaf_game_context or ncaaf_game_results fallback
# ─────────────────────────────────────────────────────────────
def load_slate(week_start: str) -> list:
    """Return list of upcoming NCAAF games in a ±7 day window from week_start.

    Reads ncaaf_game_context first (in-season), falls back to ncaaf_game_results
    for the week. CFB cadence is weekly (Thu/Fri/Sat), so a single date is
    treated as a week anchor and games within 7 days are returned.
    """
    dt = datetime.fromisoformat(week_start)
    horizon = (dt + timedelta(days=7)).date().isoformat()

    # Try ncaaf_game_context first (upcoming games with model context)
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context'
        f'?game_date=gte.{week_start}&game_date=lte.{horizon}'
        f'&select=game_id,home_team,away_team,game_date',
        headers=H_READ, timeout=15,
    )
    if r.status_code == 200 and r.json():
        return r.json()

    # Fallback: ncaaf_game_results (may only exist post-schedule pull)
    r2 = requests.get(
        f'{SB}/rest/v1/ncaaf_game_results'
        f'?game_date=gte.{week_start}&game_date=lte.{horizon}'
        f'&select=game_id,home_team,away_team,game_date',
        headers=H_READ, timeout=15,
    )
    return r2.json() if r2.status_code == 200 else []


def _team_matches(name: str, target: str) -> bool:
    name = (name or '').lower().strip()
    target = (target or '').lower().strip()
    if not name or not target: return False
    return name in target or target in name or \
           name.split()[-1] == target.split()[-1]


def find_game_id(slate: list, home_hint: str, away_hint: str) -> Optional[str]:
    for g in slate:
        if _team_matches(g['home_team'], home_hint) and \
           _team_matches(g['away_team'], away_hint):
            return g['game_id']
    return None


def load_ncaaf_alias_map() -> dict:
    """Map full team name → canonical abbrev via ncaaf_team_aliases.

    2026-08-09 fix: previous select requested `odds_api_name` which
    doesn't exist in the schema. Actual columns per audit:
    canonical_name, full_name, location, nickname, alt_names.
    Use alt_names (JSONB/text) for aliases beyond full_name.
    """
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_team_aliases?select=canonical_name,full_name,location,nickname,alt_names',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    aliases = {}
    for row in r.json():
        canon = row.get('canonical_name')
        if not canon: continue
        for field in ('full_name', 'location', 'nickname'):
            n = row.get(field)
            if n: aliases[n] = canon
        # alt_names may be an array or comma-separated string
        alts = row.get('alt_names')
        if isinstance(alts, list):
            for n in alts:
                if n: aliases[n] = canon
        elif isinstance(alts, str):
            for n in alts.split(','):
                n = n.strip()
                if n: aliases[n] = canon
    return aliases


# ─────────────────────────────────────────────────────────────
# Source fetchers — start with the 3 shared Playwright ones
# (Dimers, Action, BettingPros) + Pickswise + PickDawgz
# ─────────────────────────────────────────────────────────────
def fetch_dimers(slate: list, game_date: str, aliases: dict) -> tuple:
    """Dimers CFB — same layout as MLB (Playwright helper)."""
    try:
        from _playwright_helper import render_page
    except ImportError:
        print('  ⚠ Dimers: _playwright_helper not importable')
        return [], 500
    text, err = render_page('https://www.dimers.com/bet-hub/cfb/schedule')
    if err == 'unavailable':
        print('  ⚠ Dimers: Playwright unavailable — skip')
        return [], 200
    if err:
        print(f'  ⚠ Dimers render error: {err}')
        return [], 500
    if not text: return [], 200

    picks = []; seen = set()
    chunk_re = re.compile(
        r'([A-Z][A-Za-z. ]{2,20}?)\s*\n\s*(?:\d+\s*\n\s*)?(\d{1,2}\.\d)%\s*\n'
        r'\s*([A-Z][A-Za-z. ]{2,20}?)\s*\n\s*(?:\d+\s*\n\s*)?(\d{1,2}\.\d)%',
    )
    for m in chunk_re.finditer(text):
        away_name, away_wp, home_name, home_wp = m.groups()
        away_wp = float(away_wp); home_wp = float(home_wp)
        if not (95 <= away_wp + home_wp <= 105): continue
        gid = None; pick_side = None; pick_wp = None
        for g in slate:
            if _team_matches(g.get('away_team'), aliases.get(away_name, away_name)) and \
               _team_matches(g.get('home_team'), aliases.get(home_name, home_name)):
                gid = g['game_id']
                if home_wp >= away_wp: pick_side, pick_wp = 'HOME', home_wp
                else: pick_side, pick_wp = 'AWAY', away_wp
                break
        if not gid or gid in seen: continue
        seen.add(gid)
        fade = 'boost' if pick_wp >= 60 else 'neutral'
        picks.append(ExternalPick(
            game_id=gid, sport='NCAAF', game_date=game_date, source='dimers',
            surface='ml', pick_side=pick_side, odds_american=None,
            confidence=f'{pick_wp:.1f}% wp',
            raw_text=f'Dimers wp: {away_name} {away_wp:.1f}% / {home_name} {home_wp:.1f}%',
            fade_flag=fade,
        ))
    return picks, 200


def fetch_action(slate: list, game_date: str, aliases: dict) -> tuple:
    """Action Network public betting NFL (Playwright)."""
    try:
        from _playwright_helper import render_page
    except ImportError:
        return [], 500
    text, err = render_page(
        'https://www.actionnetwork.com/ncaaf/public-betting',
        wait_ms=6000, wait_until='networkidle', timeout_ms=45000,
    )
    if err == 'unavailable':
        print('  ⚠ Action: Playwright unavailable — skip')
        return [], 200
    if err:
        print(f'  ⚠ Action render error: {err}')
        return [], 500
    if not text: return [], 200

    picks = []; seen = set()
    chunk_re = re.compile(
        r'([A-Z][A-Za-z ]{2,20}?)\s*\n\s*\d{3,4}\s*\n'
        r'\s*([A-Z][A-Za-z ]{2,20}?)\s*\n\s*\d{3,4}\s*\n'
        r'.*?(\d{1,3})%\s*\n\s*(\d{1,3})%',
        re.DOTALL,
    )
    for m in chunk_re.finditer(text):
        away_name = m.group(1).strip(); home_name = m.group(2).strip()
        away_pct = int(m.group(3)); home_pct = int(m.group(4))
        if not (90 <= away_pct + home_pct <= 110): continue
        gid = None
        for g in slate:
            if _team_matches(g.get('away_team'), aliases.get(away_name, away_name)) and \
               _team_matches(g.get('home_team'), aliases.get(home_name, home_name)):
                gid = g['game_id']; break
        if not gid or gid in seen: continue
        seen.add(gid)
        side, pct = ('HOME', home_pct) if home_pct > away_pct else ('AWAY', away_pct)
        fade = 'boost' if pct >= 70 else 'neutral'
        picks.append(ExternalPick(
            game_id=gid, sport='NCAAF', game_date=game_date, source='action',
            surface='ml', pick_side=side, odds_american=None,
            confidence=f'{pct}% public bets',
            raw_text=f'Action bet%: {away_name} {away_pct}% / {home_name} {home_pct}%',
            fade_flag=fade,
        ))
    return picks, 200


def fetch_pickswise(slate: list, game_date: str, aliases: dict) -> tuple:
    """Pickswise CFB — star ratings + moneyline/total/rl patterns.
    Same anchoring approach as MLB but with NFL team-name universe."""
    from bs4 import BeautifulSoup
    r = requests.get(
        'https://www.pickswise.com/college-football/picks/',
        headers={'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'},
        timeout=12,
    )
    if r.status_code != 200: return [], r.status_code
    soup = BeautifulSoup(r.text, 'html.parser')
    text = soup.get_text('\n', strip=True)

    picks = []; seen_games = set()

    def _stars_near(pos: int) -> Optional[int]:
        window = text[max(0, pos-400): pos+200]
        n = window.count('⭐') + window.count('★')
        return n if 1 <= n <= 5 else None

    ml_re = re.compile(
        r'Moneyline\s*[-–]\s*([A-Z][A-Za-z. ]+?)\s*\n\s*([+-]\d{2,4})\b',
    )
    total_re = re.compile(
        r'\b(Over|Under)\s+([\d.]+)\s*\n\s*([+-]\d{2,4})\b', re.I,
    )
    rl_re = re.compile(
        r'(?:Spread|Point Spread)\s*[-–]\s*([A-Z][A-Za-z. ]+?)\s+([+-][\d.]+)\s*\n\s*([+-]\d{2,4})\b',
    )

    for m in ml_re.finditer(text):
        team_name = m.group(1).strip(); odds = int(m.group(2))
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
            game_id=gid, sport='NCAAF', game_date=game_date, source='pickswise',
            surface='ml', pick_side=side, odds_american=odds,
            confidence=f'{stars}-star' if stars else None,
            raw_text=f'Pickswise: {team_name} ML {odds}' + (f' [{stars}⭐]' if stars else ''),
            fade_flag=fade,
        ))
        seen_games.add(gid)

    for m in total_re.finditer(text):
        side = m.group(1).upper(); line = float(m.group(2)); odds = int(m.group(3))
        stars = _stars_near(m.start())
        pre = text[max(0, m.start()-500): m.start()]
        gids_in_pre = set()
        for g in slate:
            for team_field in ('home_team', 'away_team'):
                t = g.get(team_field)
                if t and t.lower() in pre.lower():
                    gids_in_pre.add(g['game_id'])
        if len(gids_in_pre) != 1: continue
        gid = gids_in_pre.pop()
        if gid in seen_games: continue
        fade = 'fade' if stars and stars >= 5 else ('boost' if stars and stars >= 4 else 'neutral')
        picks.append(ExternalPick(
            game_id=gid, sport='NCAAF', game_date=game_date, source='pickswise',
            surface='total', pick_side=side, pick_line=line, odds_american=odds,
            confidence=f'{stars}-star' if stars else None,
            raw_text=f'Pickswise: {side} {line} ({odds})' + (f' [{stars}⭐]' if stars else ''),
            fade_flag=fade,
        ))
        seen_games.add(gid)

    for m in rl_re.finditer(text):
        team_name = m.group(1).strip(); rl_line = float(m.group(2)); odds = int(m.group(3))
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
            game_id=gid, sport='NCAAF', game_date=game_date, source='pickswise',
            surface='rl', pick_side=side, pick_line=rl_line, odds_american=odds,
            confidence=f'{stars}-star' if stars else None,
            raw_text=f'Pickswise: {team_name} RL {rl_line} ({odds})' + (f' [{stars}⭐]' if stars else ''),
            fade_flag=fade,
        ))
        seen_games.add(gid)

    return picks, 200


# Stubbed for Phase 3 iteration — reserve source keys so the pull_log
# structure is complete and expansion doesn't need registry changes.
def fetch_covers(slate: list, game_date: str, aliases: dict) -> tuple:
    return [], 200


def fetch_vsin(slate: list, game_date: str, aliases: dict) -> tuple:
    return [], 200


def fetch_bettingpros(slate: list, game_date: str, aliases: dict) -> tuple:
    return [], 200


def fetch_pickdawgz(slate: list, game_date: str, aliases: dict) -> tuple:
    return [], 200


def fetch_oddscrowd(slate: list, game_date: str, aliases: dict) -> tuple:
    """OddsCrowd h2h/rl/total for NCAAF. Shared module handles the football
    page (mixed with NFL); slate + team-name matching filters cleanly."""
    from externals_oddscrowd import fetch_oddscrowd_generic
    picks_dicts, status = fetch_oddscrowd_generic(
        sport_url_slug='football',
        league_slug='ncaaf',
        sport_code='NCAAF',
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


def fetch_scoresandodds(slate: list, game_date: str, aliases: dict) -> tuple:
    """2026-08-25 SO scraper for NCAAF."""
    from externals_scoresandodds import fetch_scoresandodds_generic
    picks_dicts, status = fetch_scoresandodds_generic(
        league_slug='ncaaf', sport_code='NCAAF', game_date=game_date,
        slate=slate, find_game_id_fn=find_game_id,
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


FETCHERS = {
    'dimers': fetch_dimers,
    'covers': fetch_covers,
    'action': fetch_action,
    'vsin': fetch_vsin,
    'bettingpros': fetch_bettingpros,
    'pickswise': fetch_pickswise,
    'pickdawgz': fetch_pickdawgz,
    'oddscrowd': fetch_oddscrowd,
    'scoresandodds': fetch_scoresandodds,
}


# ─────────────────────────────────────────────────────────────
# Main pull orchestrator
# ─────────────────────────────────────────────────────────────
def run_pull(game_date: str, sources: list, triggered_by: str,
             dry_run: bool = False) -> dict:
    print(f'\n=== NCAAF external pull · {game_date} · {triggered_by} ===')
    slate = load_slate(game_date)
    print(f'  slate: {len(slate)} games in ±7d window')
    if not slate:
        print('  ⚠ no NCAAF games in window — abort')
        return {'games': 0, 'sources_pulled': 0, 'picks_written': 0}

    aliases = load_ncaaf_alias_map()
    print(f'  aliases: {len(aliases)} name→abbrev mappings')

    scheduled_at = datetime.now(timezone.utc)
    summary = {
        'games': len(slate), 'sources_pulled': 0, 'sources_failed': 0,
        'picks_written': 0, 'source_records': [],
    }

    for source in sources:
        cfg = SOURCE_REGISTRY.get(source)
        if not cfg:
            print(f'  ⚠ unknown source: {source} — skip'); continue
        fetcher = FETCHERS.get(source)
        if not fetcher:
            print(f'  ⚠ no fetcher for {source} — skip'); continue

        pull_id = None
        if not dry_run:
            pull_id = start_pull_log(
                source, 'NCAAF', scheduled_at,
                triggered_by=triggered_by, source_url=cfg['base_url'],
            )

        started = time.time()
        try:
            picks, http_status = fetcher(slate, game_date, aliases)
            for p in picks:
                if not p.source_url: p.source_url = cfg['base_url']
                if not p.fade_flag: p.fade_flag = cfg['fade_flag']

            games_covered = len({p.game_id for p in picks})
            duration_ms = int((time.time() - started) * 1000)

            if dry_run:
                print(f'  [DRY] {cfg["label"]}: {len(picks)} picks / '
                      f'{games_covered} games / {duration_ms}ms')
                for p in picks[:3]:
                    print(f'      {p.game_id[:12]}... {p.surface}:{p.pick_side} '
                          f'{p.confidence or ""}')
            else:
                count = write_picks(picks, pull_id)
                complete_pull_log(
                    pull_id, status='success',
                    picks_pulled=count, games_covered=games_covered,
                    http_status=http_status, duration_ms=duration_ms,
                )
                print(f'  ✓ {cfg["label"]}: {count} picks / {games_covered} games / '
                      f'{duration_ms}ms')
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

    # Compute alignment + oddscrowd snapshot for NCAAF app UX layer
    if not dry_run and summary['picks_written'] > 0:
        try:
            from compute_align_status_ncaaf import run as compute_align_run
            print('\n=== Alignment + oddscrowd snapshot (NCAAF) ===')
            compute_align_run(game_date=game_date, dry_run=False)
        except Exception as e:
            print(f'  ⚠ compute_align_status_ncaaf failed: {type(e).__name__}: {e}')

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None,
                    help='YYYY-MM-DD week anchor (defaults to today ET)')
    ap.add_argument('--refresh', action='store_true',
                    help='Saturday 10am refresh (subset of sources)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--source', default=None,
                    help='Test a single source in isolation')
    args = ap.parse_args()

    date = args.date or _today_et()

    if args.source:
        sources = [args.source]; triggered_by = f'manual:single:{args.source}'
    elif args.refresh:
        # Sat 10am ET — subset that publishes late-week (Peterson lock,
        # Doc Sports finalizations, Action money-line % settlement)
        sources = ['action', 'vsin', 'bettingpros']
        triggered_by = 'cron:sat_ncaaf_refresh'
    else:
        # Wed 6pm ET — everything (or Thu 2pm TNF card lock)
        sources = list(SOURCE_REGISTRY.keys())
        triggered_by = 'cron:wed_ncaaf'

    run_pull(date, sources, triggered_by, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
