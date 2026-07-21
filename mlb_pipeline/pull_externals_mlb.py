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
    """Batch insert picks with pull_id FK. Returns count written."""
    if not picks:
        return 0
    payload = []
    for p in picks:
        d = asdict(p)
        d['pull_id'] = pull_id
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
    """Dimers.com/bet-hub/mlb/schedule. Returns (picks, http_status).

    Dimers publishes per-team win probability and total predictions.
    Attribution: link back to their schedule page.
    """
    # TODO Phase 1 scraper — for now, return empty + http_status 200 as a
    # smoke test that pull_log flow works. Wire real scraper next.
    return [], 200


def fetch_covers(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_cbs(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_action(slate: list, game_date: str) -> tuple[list, int]:
    """Action Network public bet%/money% split. Sharp $ ≥+35 gap = signal."""
    return [], 200


def fetch_vsin(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_bettingpros(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_oddsshark(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_pickswise(slate: list, game_date: str) -> tuple[list, int]:
    """5-STAR picks flagged as fade per audit — override fade_flag per pick."""
    return [], 200


def fetch_pickdawgz(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_docsports(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_scp(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_fangraphs(slate: list, game_date: str) -> tuple[list, int]:
    return [], 200


def fetch_ballparkpal(slate: list, game_date: str) -> tuple[list, int]:
    """Wind/park factor calls. Auto-tag as fade per audit."""
    return [], 200


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
