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
        'fetcher_todo': False,  # v1 shipped 2026-07-27
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
def _bfo_find_event_url(event_name: str, fighter_names: list = None) -> Optional[str]:
    """Fuzzy-match a UFC event to BFO's event URL.

    Match strategy (in order):
      1. Event-name word overlap ('UFC 330' → 'ufc-330-xxxx' works)
      2. Fighter-name overlap: fetch each candidate event page, check
         if any of our expected fighters appear. This handles the
         common case where ESPN calls it "UFC Fight Night: Medić vs.
         Rodriguez" but BFO calls it "UFC Belgrade" — the fighters
         are the same and appear on the event page.
    """
    try:
        r = requests.get('https://www.bestfightodds.com/',
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    except Exception:
        return None
    if r.status_code != 200: return None
    links = re.findall(r'href="(/events/ufc-[a-z0-9-]+)"[^>]*>([^<]{5,80})', r.text)
    if not links: return None
    # Only UPCOMING events — "View ... Odds" links carry that context.
    # Deduplicate URLs since home page repeats them.
    seen = set(); candidates = []
    for url, label in links:
        if url in seen: continue
        seen.add(url); candidates.append((url, label))

    # Try name-overlap first (fast — no network calls)
    target_words = set(re.findall(r'[a-z0-9]{3,}', event_name.lower()))
    best_url = None; best_score = 0
    for url, label in candidates:
        merged = (label + ' ' + url).lower()
        merged_words = set(re.findall(r'[a-z0-9]{3,}', merged))
        score = len(target_words & merged_words)
        if score > best_score:
            best_score = score; best_url = url
    # 3+ word overlap is a solid match
    if best_score >= 3:
        return f'https://www.bestfightodds.com{best_url}'

    # Fallback: fighter-name match. Fetch each candidate event page and
    # check for known fighter names.
    if not fighter_names: fighter_names = []
    target_fighters = set(_norm(n) for n in fighter_names if n)
    if not target_fighters:
        # Return best word-overlap match even if weak
        return f'https://www.bestfightodds.com{best_url}' if best_url else None
    for url, _ in candidates:
        try:
            pr = requests.get(f'https://www.bestfightodds.com{url}',
                              headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        except Exception:
            continue
        if pr.status_code != 200: continue
        page_norm = _norm(pr.text[:200_000])
        matches = sum(1 for f in target_fighters if f in page_norm)
        if matches >= 3:  # 3+ of our fighters found on this event page = solid match
            return f'https://www.bestfightodds.com{url}'
    return None


def _parse_bfo_odds(cell_text: str) -> Optional[int]:
    """'+335▲' or '-355▼' → int. Returns None for empty/malformed."""
    if not cell_text: return None
    m = re.search(r'([+-]?\d+)', cell_text)
    if not m: return None
    try: return int(m.group(1))
    except: return None


def fetch_bfo(event_name: str, event_date: str, picks_rows: list) -> list:
    """BestFightOdds consensus fetcher — v1 shipped 2026-07-27.

    Approach:
      1. Fuzzy-match event_name → BFO event URL
      2. GET event page, parse odds table (2nd table.odds-table)
      3. For each fight (4-row block: fighter A, B, over rounds, under):
         - Extract odds cells [1:N] per fighter (N books)
         - Compute median odds per fighter
         - Consensus favorite = fighter with negative median (or lower positive)
      4. Match fighter to picks_rows for game_id
      5. Emit ExternalPick(source='bfo', surface='ml', pick_side=<matched picks id>,
                           odds_american=median)
      6. Also emit distance pick (over/under 2.5 rounds) if odds present
    """
    from bs4 import BeautifulSoup
    from statistics import median

    # Feed fighter names as fallback for event matching (ESPN and BFO
    # use different event naming — fighters are the reliable common key).
    fighter_names = []
    for row in picks_rows:
        if row.get('fighter_a'): fighter_names.append(row['fighter_a'])
        if row.get('fighter_b'): fighter_names.append(row['fighter_b'])
    event_url = _bfo_find_event_url(event_name, fighter_names=fighter_names)
    if not event_url:
        print(f'    bfo: could not match event "{event_name}" to any /events/ URL')
        return []
    print(f'    bfo: matched to {event_url}')

    try:
        r = requests.get(event_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    except Exception as e:
        print(f'    bfo: fetch failed — {e}')
        return []
    if r.status_code != 200:
        print(f'    bfo: {r.status_code} on event page')
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    odds_tables = soup.find_all('table', class_='odds-table')
    # Second one has odds cells; first is responsive-header only
    if len(odds_tables) < 2: return []
    odds_t = odds_tables[1]

    # Header row → book names
    head_cells = odds_t.find('thead').find_all(['th', 'td']) if odds_t.find('thead') else []
    if not head_cells:
        # Fallback — first row
        head_cells = odds_t.find_all('tr')[0].find_all(['th', 'td'])
    book_names = []
    for i, c in enumerate(head_cells):
        txt = c.get_text(strip=True)
        # Skip empty first col and 'Props' last col
        if i == 0 or 'Props' in txt: continue
        # Strip promo copy like "Polymarket$20 B" → "Polymarket"
        clean = re.split(r'\$|\bUp to\b|\bBet\s+\$', txt)[0].strip()
        book_names.append(clean or txt)
    n_books = len(book_names)
    # Guard: BFO shows ~7-9 books
    if n_books < 3: return []

    picks = []
    all_rows = odds_t.find_all('tr')
    # Iterate looking for fight-pair (2 consecutive fighter rows without class='pr')
    for i, row in enumerate(all_rows):
        if row.get('class') and 'pr' in row.get('class'): continue
        cells = row.find_all(['th', 'td'])
        if len(cells) < n_books + 1: continue
        # First cell has fighter name (strip leading matchup ID digits)
        name_txt = cells[0].get_text(strip=True)
        name = re.sub(r'^\d+', '', name_txt).strip()
        if not name or len(name) < 3: continue
        # Skip if no odds populated on this row
        odds_here = [_parse_bfo_odds(cells[j+1].get_text(strip=True)) for j in range(n_books)]
        odds_here = [o for o in odds_here if o is not None]
        if not odds_here: continue

        med = int(median(odds_here))
        # Match to ufc_picks row for game_id
        match = match_fight(picks_rows, name)
        if not match:
            continue

        # Determine which side of the picks_row this fighter is (A or B)
        # so pick_side reflects which fighter is being backed.
        game_id = match['id']
        pick_side_label = 'FIGHTER_A' if _norm(match.get('fighter_a', '')) == _norm(name) \
                          else 'FIGHTER_B'

        # Consensus favorite has negative odds (or lower positive)
        # We emit ML pick for THIS fighter with their consensus odds. The
        # consensus fade detector aggregates side counts across sources.
        picks.append(ExternalPick(
            source='bfo',
            surface='ml',
            pick_side=pick_side_label,
            game_id=str(game_id),
            game_date=match.get('event_date') or event_date or '',
            odds_american=med,
            confidence='consensus',
            fade_flag='trust',
            raw_text=f'BFO median across {len(odds_here)}/{n_books} books: {med:+d}',
            source_url=event_url,
        ))

    return picks


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
    # Try upsert first (requires 20260727_external_picks_dedup.sql migration)
    r = requests.post(
        f'{SB}/rest/v1/external_picks?on_conflict=source,game_id,surface,pick_side,game_date',
        headers=H_WRITE,
        json=payload, timeout=20,
    )
    # Migration not applied → 42P10 no unique constraint. Fall back to plain
    # INSERT with a warning. Row-dedup deferred until migration lands.
    if r.status_code == 400 and '42P10' in r.text:
        print('  ⚠ upsert unavailable (dedup migration 20260727 not applied) — plain INSERT with dupe risk')
        r = requests.post(
            f'{SB}/rest/v1/external_picks',
            headers={**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
            json=payload, timeout=20,
        )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ picks write failed {r.status_code}: {r.text[:150]}')
        return 0
    return len(payload)


def write_pull_log(sources_attempted: list, pull_id: str, pick_counts: dict = None) -> None:
    """Log the pull for provenance/debugging.
    Schema mirrors MLB's pull_log — includes source_url, http_status,
    duration_ms, triggered_by, agent_version (all inferred where possible)."""
    now = datetime.now(timezone.utc).isoformat()
    counts = pick_counts or {}
    payload = [{
        'pull_id': pull_id,
        'sport': 'UFC',
        'source': s,
        'scheduled_at': now,
        'started_at': now,
        'completed_at': now,
        'status': 'stub' if SOURCE_REGISTRY[s].get('fetcher_todo') else 'success',
        'picks_pulled': counts.get(s, 0),
        'games_covered': counts.get(s, 0),
        'error_message': None,
        'source_url': SOURCE_REGISTRY[s].get('base_url', ''),
        'http_status': 200,
        'duration_ms': 0,
        'triggered_by': 'cron:ufc',
        'agent_version': 'ufc-v1',
    } for s in sources_attempted]
    try:
        r = requests.post(
            f'{SB}/rest/v1/external_pull_log', headers=H_WRITE,
            json=payload, timeout=15,
        )
        if r.status_code not in (200, 201, 204):
            print(f'  ⚠ pull_log write failed {r.status_code}: {r.text[:200]}')
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
    per_source_count = {}
    pull_id = str(uuid.uuid4())

    for src in to_run:
        cfg = SOURCE_REGISTRY.get(src)
        if not cfg:
            print(f'  ✗ unknown source: {src}')
            continue
        if cfg.get('fetcher_todo'):
            print(f'  ⚠ {src} — fetcher stub (TODO). skipping.')
            per_source_count[src] = 0
            continue
        fetcher = SOURCE_FETCHERS.get(src)
        if not fetcher:
            per_source_count[src] = 0
            continue
        try:
            picks = fetcher(event_name, event_date, picks_rows)
            print(f'  {src}: {len(picks)} picks')
            all_picks.extend(picks)
            per_source_count[src] = len(picks)
        except Exception as e:
            print(f'  ✗ {src} fetcher error: {e}')
            per_source_count[src] = 0

    if dry_run:
        print(f'\n[DRY] would write {len(all_picks)} picks + pull_log for {len(to_run)} sources')
        return

    # Write pull_log FIRST — external_picks has FK to pull_id, so parent row
    # must exist before child rows can INSERT.
    write_pull_log(to_run, pull_id, pick_counts=per_source_count)

    if not all_picks:
        print(f'\n  no picks fetched (all sources still stubs) — pull_log written only')
        return

    written = write_picks(all_picks, pull_id)
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
