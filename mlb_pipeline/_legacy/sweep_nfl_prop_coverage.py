"""NFL prop coverage sweeper (Sprint 1 Day 1 · 2026-08-03).

Mirrors sweep_prop_coverage.py (MLB) — same architecture, NFL-specific
markets + player-context lookup. Guarantees every NFL player prop the
sportsbook publishes lands in nfl_pipeline_props.

Pattern:
  1. Pull upcoming NFL events from Odds API
  2. For each event, pull all Big 4 player prop markets across preferred books
  3. Bundle over+under from same book per (player, prop_type)
  4. Insert COVERAGE stubs into nfl_pipeline_props with book line + odds
  5. Downstream: generate_nfl_props scores + tiers, generate_nfl_prop_jerry_synthesis
     writes the actual read.

Big 4 markets for Week 1 launch (per 2026-08-02 user decision):
  player_pass_yds        → pass_yds
  player_rush_yds        → rush_yds
  player_reception_yds   → rec_yds
  player_anytime_td      → anytime_td

Prop_type convention: FULL FORM ({family}_{direction}), matching MLB
fix from 2026-08-02. e.g. pass_yds_over, rec_yds_under, anytime_td_over.
NEVER bare family — breaks grader natural-key JOIN.

Sport-parametric extension of the MLB sweeper. Runs before
generate_nfl_prop_jerry_synthesis in the NFL cron chain.

Usage:
    python sweep_nfl_prop_coverage.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MARKET_MAP = {
    'player_pass_yds':      'pass_yds',
    'player_rush_yds':      'rush_yds',
    'player_reception_yds': 'rec_yds',
    'player_anytime_td':    'anytime_td',
}

# Sprint 2 expansion (per decision 2026-08-02):
# player_receptions, player_pass_tds

PREFERRED_BOOKS = ['draftkings', 'fanduel', 'betmgm', 'hardrockbet',
                   'espnbet', 'betrivers', 'bovada', 'williamhill_us']


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch_upcoming_events(days_ahead: int = 14) -> list:
    """NFL events from now until N days ahead.

    Default 14d captures the current + next week (NFL runs Thu-Mon so a
    single week is 5 days but rolling window catches the next Thu opener).
    Note: Odds API does not appear to publish preseason player-prop odds —
    earliest events surface for regular season W1 (Sept 4+ 2026).
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    later = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = requests.get('https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events',
                     params={'apiKey': ODDS_API_KEY,
                             'commenceTimeFrom': now,
                             'commenceTimeTo': later},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_player_props(event_id: str) -> dict:
    """Returns {(player_lc, prop_type): {'line', 'over_odds', 'under_odds', 'book', 'display'}}.
    Bundles over+under from the same preferred book so both odds columns populate.
    For anytime_td: over_odds = Yes price, under_odds = No price (if book offers it)."""
    markets_csv = ','.join(MARKET_MAP.keys())
    r = requests.get(
        f'https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds',
        params={'apiKey': ODDS_API_KEY, 'regions': 'us,us2',
                'markets': markets_csv, 'oddsFormat': 'american'},
        timeout=15)
    if r.status_code != 200:
        return {}

    d = r.json()
    by_book = {}  # (player_lc, prop_type, book) -> slot

    for bk in d.get('bookmakers', []):
        book = bk['key']
        for mkt in bk.get('markets', []):
            mkt_key = mkt.get('key')
            if mkt_key not in MARKET_MAP: continue
            prop_type = MARKET_MAP[mkt_key]

            for outcome in mkt.get('outcomes', []):
                # anytime_td format: outcome.name = 'Yes'/'No', description = player name
                # yards format: outcome.name = 'Over'/'Under', description = player name
                player = outcome.get('description')
                if not player: continue
                side_name = (outcome.get('name') or '').lower()
                price = outcome.get('price')
                line = outcome.get('point')  # None for anytime_td (binary yes/no)

                if prop_type == 'anytime_td':
                    # Yes → over, No → under (per schema convention)
                    direction = 'over' if side_name == 'yes' else ('under' if side_name == 'no' else None)
                    if line is None: line = 0.5  # implicit 0.5 threshold for TD
                else:
                    direction = 'over' if 'over' in side_name else ('under' if 'under' in side_name else None)
                if not direction: continue

                key = (player.lower(), prop_type, book)
                slot = by_book.setdefault(key, {'over_odds': None, 'under_odds': None,
                                                'line': None, 'display': player})
                slot[f'{direction}_odds'] = int(price) if price is not None else None
                if slot['line'] is None and line is not None:
                    slot['line'] = float(line)

    # Pick preferred book per (player, prop_type) — must have BOTH sides
    out = {}
    seen = set()
    for pb in PREFERRED_BOOKS:
        for (player_lc, prop_type, book), slot in by_book.items():
            if book != pb: continue
            if (player_lc, prop_type) in seen: continue
            if slot['line'] is None: continue
            # anytime_td may only have Yes side on some books — allow single-sided
            if prop_type != 'anytime_td':
                if slot['over_odds'] is None or slot['under_odds'] is None: continue
            out[(player_lc, prop_type)] = {**slot, 'book': book}
            seen.add((player_lc, prop_type))

    # Fallback: any book with both sides for props not yet picked
    for (player_lc, prop_type, book), slot in by_book.items():
        if (player_lc, prop_type) in seen: continue
        if slot['line'] is None: continue
        if prop_type != 'anytime_td':
            if slot['over_odds'] is None or slot['under_odds'] is None: continue
        out[(player_lc, prop_type)] = {**slot, 'book': book}
        seen.add((player_lc, prop_type))

    return out


def _game_id_for_event(ev: dict, game_date: str) -> str:
    """Stable hash-free game_id derived from date + team abbreviations.
    Matches the pattern nfl_game_context will use once that pipeline ships."""
    import hashlib
    key = f"{game_date}_{ev.get('away_team','')}_{ev.get('home_team','')}"
    return hashlib.md5(key.encode()).hexdigest()


def sweep(game_date: str | None = None, dry_run: bool = False) -> None:
    gd = game_date or today_et()
    print(f'=== sweep_nfl_prop_coverage · {gd} ===')
    if not ODDS_API_KEY:
        print('  ⛔ ODDS_API_KEY missing'); return

    # Existing rows for dedup (natural key match)
    r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'gte.{gd}',
                             'select': 'game_id,player_name,prop_type,direction'},
                     timeout=15)
    if r.status_code == 200:
        existing_keys = {(p['game_id'], p['player_name'].lower(), p['prop_type'], p['direction'])
                         for p in r.json() if p.get('player_name')}
    else:
        existing_keys = set()
        if r.status_code == 404:
            print('  ⚠ nfl_pipeline_props table not found — apply migration first')
            return
    print(f'  existing rows on/after {gd}: {len(existing_keys)}')

    events = fetch_upcoming_events()
    print(f'  {len(events)} upcoming NFL events')

    written = skipped = 0

    for ev in events:
        commence = ev.get('commence_time', '')[:10]
        game_id = _game_id_for_event(ev, commence)
        matchup = f"{ev.get('away_team','?')} @ {ev.get('home_team','?')}"

        props = fetch_player_props(ev['id'])
        if not props:
            continue

        for (player_lc, prop_type), entry in props.items():
            display = entry['display']

            # Emit BOTH directions as separate rows (matches MLB convention)
            for direction in ('over', 'under'):
                full_type = f'{prop_type}_{direction}'
                key = (game_id, display.lower(), full_type, direction)
                if key in existing_keys: continue

                # Skip missing-side rows for non-anytime_td markets
                odds_key = f'{direction}_odds'
                if prop_type != 'anytime_td' and entry.get(odds_key) is None:
                    continue

                payload = {
                    'game_date': commence,
                    'game_id': game_id,
                    'player_name': display,
                    'player_team': None,        # populated when nfl_game_context ships
                    'position': None,           # populated by projection layer
                    'opp_team': None,
                    'home_away': None,
                    'matchup': matchup,
                    'prop_type': full_type,
                    'direction': direction,
                    'prop_line': entry['line'],
                    'book_line': entry['line'],
                    'book_over_odds': entry.get('over_odds'),
                    'book_under_odds': entry.get('under_odds'),
                    'book_source': entry['book'],
                    'signals': {},              # populated by projection + generator
                    'tier': 'COVERAGE',
                    'conviction': 0,
                    'lineup_state': 'coverage_stub',
                    'season_phase': 'preseason' if commence < '2026-09-04' else 'regular',
                }

                if dry_run:
                    written += 1; continue
                wr = requests.post(f'{SB}/rest/v1/nfl_pipeline_props',
                                   headers=H_WRITE, json=payload, timeout=15)
                if wr.status_code in (200, 201, 204):
                    written += 1
                    existing_keys.add(key)
                else:
                    skipped += 1
                    if skipped <= 3:
                        print(f'  ⚠ insert {wr.status_code}: {wr.text[:200]}')

    print(f'\n=== wrote {written} NFL coverage stubs · {skipped} skipped ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='sweep window start (default today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sweep(game_date=args.date, dry_run=args.dry_run)
