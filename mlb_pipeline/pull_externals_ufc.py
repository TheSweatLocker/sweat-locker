"""External picks pull — UFC (Thursday + Saturday cadence).

Ships 2026-07-27 as a SCAFFOLD. Mirrors pull_externals_mlb.py structure
so future work adds sources one at a time without redesigning the
pipeline.

CADENCE (UFC):
  - Thursday: primary pull (analysts post picks Mon-Thu for weekend cards)
  - Saturday morning: refresh (catches late experts + line-move picks)

Writes to same external_picks table as MLB (sport='UFC'). Same dedup
constraint applies: on_conflict (source, game_id, surface, pick_side,
game_date). Fights use ufc_picks.event_name + fight_order as game_id
proxy — see _match_fight_to_ufc_picks().

STATUS OF SOURCES:
  ✓ SCAFFOLD    — code path exists, parser is a stub
  ⚠ TODO        — noted as future work
  ✗ BLOCKED     — anti-scrape / needs Playwright

Sources to add (roughly by ease):
  BestFightOdds consensus         ⚠ TODO — 1.9MB HTML, needs careful parser
  Sherdog fight-preview picks     ⚠ TODO — public URL structure known
  MMA Junkie staff picks          ⚠ TODO — HTML scrape
  Doc Sports MMA                  ✗ BLOCKED  — needs playwright
  Covers MMA experts              ✗ BLOCKED  — needs playwright

USAGE:
  python pull_externals_ufc.py                # this week's card
  python pull_externals_ufc.py --refresh      # Sat refresh
  python pull_externals_ufc.py --dry-run
  python pull_externals_ufc.py --source bfo   # test one source in isolation

Required env: SUPABASE_URL, SUPABASE_KEY
"""
import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
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
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


# ─────────────────────────────────────────────────────────────
# Data shape (mirrors MLB)
# ─────────────────────────────────────────────────────────────
@dataclass
class ExternalPick:
    source: str
    surface: str                   # 'ml' | 'method' (KO/SUB/DEC) | 'distance' (OVER/UNDER)
    pick_side: str                 # 'FIGHTER_A' | 'FIGHTER_B' for ML;
                                   # 'KO' | 'SUB' | 'DEC' | 'OVER' | 'UNDER' for method/distance
    game_id: str                   # ufc_picks game_id — matched by event_name + fight_order
    game_date: str                 # event_date ISO
    sport: str = 'UFC'
    pick_line: Optional[float] = None
    odds_american: Optional[int] = None
    confidence: Optional[str] = None   # source's own conviction (e.g. "3-star", "PRIME")
    fade_flag: Optional[str] = None    # boost | trust | neutral | fade
    raw_text: Optional[str] = None
    source_url: Optional[str] = None
    ttl_hours: int = 24


# ─────────────────────────────────────────────────────────────
# Source registry — one entry per external source
# ─────────────────────────────────────────────────────────────
SOURCE_REGISTRY = {
    'bfo': {
        'label': 'BestFightOdds Consensus',
        'fade_flag': 'trust',   # BFO consensus tracks sharp $ well historically
        'base_url': 'https://www.bestfightodds.com/',
        'ttl_hours': 24,
        'fetcher_todo': True,   # parser not yet built
    },
    'sherdog': {
        'label': 'Sherdog Staff',
        'fade_flag': 'neutral',
        'base_url': 'https://www.sherdog.com/',
        'ttl_hours': 24,
        'fetcher_todo': True,
    },
    'mmajunkie': {
        'label': 'MMA Junkie Staff',
        'fade_flag': 'trust',
        'base_url': 'https://www.mmajunkie.usatoday.com/',
        'ttl_hours': 24,
        'fetcher_todo': True,
    },
}


# ─────────────────────────────────────────────────────────────
# Fight-lookup helpers
# ─────────────────────────────────────────────────────────────
def load_upcoming_ufc_card() -> tuple:
    """Return (event_name, event_date_iso, [fights]) — the upcoming card
    that ufc_card_scraper_v3 already scraped and ufc_score_card scored."""
    r = requests.get(
        f'{SB}/rest/v1/ufc_upcoming_event?select=event_name,event_date,fight_card,updated_at'
        f'&order=updated_at.desc&limit=1',
        headers=H_READ, timeout=15,
    ).json()
    if not r: return None, None, []
    row = r[0]
    return row.get('event_name'), row.get('event_date'), (row.get('fight_card') or [])


def load_ufc_picks_for_event(event_name: str) -> list:
    """Look up ufc_picks rows so we can match external picks to the same
    (event_name, fighter_a, fighter_b) → game_id."""
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks?event_name=eq.{event_name}&select=id,event_date,event_name,fighter_a,fighter_b',
        headers=H_READ, timeout=15,
    ).json()
    return r if isinstance(r, list) else []


def match_fight(picks_rows: list, fighter_name: str) -> Optional[dict]:
    """Fuzzy match a source's fighter reference to a ufc_picks row.
    Returns the picks row (has id used as game_id proxy)."""
    if not fighter_name: return None
    tgt = _norm(fighter_name)
    for row in picks_rows:
        if _norm(row.get('fighter_a', '')) == tgt or _norm(row.get('fighter_b', '')) == tgt:
            return row
        # Last-name fallback for source variants
        if _norm(row.get('fighter_a', '').split()[-1] if row.get('fighter_a') else '') == tgt.split()[-1]:
            return row
        if _norm(row.get('fighter_b', '').split()[-1] if row.get('fighter_b') else '') == tgt.split()[-1]:
            return row
    return None


def _norm(name: str) -> str:
    if not name: return ''
    import unicodedata
    return re.sub(r'\s+', ' ',
                  unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
                  ).strip().lower()


# ─────────────────────────────────────────────────────────────
# SOURCE FETCHERS (STUBS — implement one at a time)
# ─────────────────────────────────────────────────────────────
def fetch_bfo(event_name: str, event_date: str, picks_rows: list) -> list:
    """BestFightOdds consensus fetcher — STUB.

    Real implementation:
      1. GET https://www.bestfightodds.com/ (1.9MB HTML)
      2. Find event section by event_name (fuzzy match — BFO uses short names)
      3. For each fight row, extract fighter names + odds columns
      4. Determine consensus favorite (lowest median odds among 5-7 books shown)
      5. Emit ExternalPick(source='bfo', surface='ml', pick_side=match_fight(picks_rows, fighter).id,
                           odds_american=median_odds, confidence='consensus')

    Complexity: BFO's HTML has changed selectors twice since 2024. Build
    with fallback selectors + fuzzy event-name match. Estimate ~2 hrs
    for solid v1 that handles edge cases (fight cancelled, missing books,
    weight class changes, etc.).
    """
    return []


def fetch_sherdog(event_name: str, event_date: str, picks_rows: list) -> list:
    """Sherdog fight-preview picks — STUB.

    URL pattern: /event/{event-slug}/{event-id}/staff-picks
    Their staff picks are structured HTML with fighter + method predictions.
    """
    return []


def fetch_mmajunkie(event_name: str, event_date: str, picks_rows: list) -> list:
    """MMA Junkie staff picks — STUB.

    URL pattern: /list/ufc-{event-slug}-staff-picks-predictions
    Post typically has 3-5 staff members each picking every fight.
    Emit one ExternalPick per (staff_member, fight) with source='mmajunkie_{staff_last_name}'
    so consensus math treats them as independent.
    """
    return []


SOURCE_FETCHERS = {
    'bfo': fetch_bfo,
    'sherdog': fetch_sherdog,
    'mmajunkie': fetch_mmajunkie,
}


# ─────────────────────────────────────────────────────────────
# Write path (identical dedup-safe pattern as MLB)
# ─────────────────────────────────────────────────────────────
def write_picks(picks: list, pull_id: str) -> int:
    if not picks: return 0
    payload = []
    for p in picks:
        d = asdict(p)
        d['pull_id'] = pull_id
        payload.append(d)
    r = requests.post(
        f'{SB}/rest/v1/external_picks?on_conflict=source,game_id,surface,pick_side,game_date',
        headers=H_WRITE,
        json=payload, timeout=20,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ picks upsert failed {r.status_code}: {r.text[:150]}')
        return 0
    return len(payload)


def write_pull_log(sources_attempted: list, pull_id: str) -> None:
    """Log the pull for provenance/debugging."""
    payload = [{
        'pull_id': pull_id,
        'sport': 'UFC',
        'source': s,
        'pulled_at': datetime.now(timezone.utc).isoformat(),
        'status': 'stub' if SOURCE_REGISTRY[s].get('fetcher_todo') else 'ok',
    } for s in sources_attempted]
    try:
        requests.post(
            f'{SB}/rest/v1/external_pull_log', headers=H_WRITE,
            json=payload, timeout=15,
        )
    except Exception as e:
        print(f'  ⚠ pull_log write exception: {e}')


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
def run(sources: Optional[list] = None, refresh: bool = False, dry_run: bool = False) -> None:
    print(f'=== UFC externals pull · {datetime.now(timezone.utc).date()} ===')
    event_name, event_date, fights = load_upcoming_ufc_card()
    if not event_name:
        print('  no upcoming UFC card in ufc_upcoming_event')
        return
    print(f'  event: {event_name}  date={event_date}  fights={len(fights)}')

    picks_rows = load_ufc_picks_for_event(event_name)
    print(f'  ufc_picks rows for event: {len(picks_rows)}')

    to_run = sources or list(SOURCE_REGISTRY.keys())
    all_picks = []
    pull_id = str(uuid.uuid4())

    for src in to_run:
        cfg = SOURCE_REGISTRY.get(src)
        if not cfg:
            print(f'  ✗ unknown source: {src}')
            continue
        if cfg.get('fetcher_todo'):
            print(f'  ⚠ {src} — fetcher stub (TODO). skipping.')
            continue
        fetcher = SOURCE_FETCHERS.get(src)
        if not fetcher:
            continue
        try:
            picks = fetcher(event_name, event_date, picks_rows)
            print(f'  {src}: {len(picks)} picks')
            all_picks.extend(picks)
        except Exception as e:
            print(f'  ✗ {src} fetcher error: {e}')

    if dry_run:
        print(f'\n[DRY] would write {len(all_picks)} picks + pull_log for {len(to_run)} sources')
        return

    if not all_picks:
        print(f'\n  no picks fetched (all sources still stubs) — writing pull_log only')
        write_pull_log(to_run, pull_id)
        return

    written = write_picks(all_picks, pull_id)
    write_pull_log(to_run, pull_id)
    print(f'\n✓ wrote {written} external picks for {event_name}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='Saturday refresh mode')
    ap.add_argument('--source', help='Test one source in isolation')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    sources = [args.source] if args.source else None
    run(sources=sources, refresh=args.refresh, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
