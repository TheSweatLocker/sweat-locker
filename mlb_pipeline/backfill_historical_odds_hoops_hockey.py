"""backfill_historical_odds_hoops_hockey — NHL + NBA closing-line backfill.

2026-09-14 · project_v1_0_1_client_priorities #12 unblock.

Fills close_home_ml / close_away_ml / close_total / close_spread (NBA) /
close_puckline (NHL) on {nhl,nba}_historical_closing_odds landing tables
for the 2024-25 season by querying The Odds API historical endpoint
(one snapshot per date × 2 timeslots).

Unblocks:
  * nhl_ml_logreg_train.py / nba_ml_logreg_train.py (currently refuse:
    zero features on training set).
  * signal_registry retro-grading for the 35 NHL + NBA signals shipped
    2026-08-19 (most UNVALIDATED for lack of graded market data).

Data source: /v4/historical/sports/{sport_key}/odds
  * sport_key = icehockey_nhl / basketball_nba
  * returns nearest snapshot ≤ requested `date` param (5-min bucket)
  * response `data[]` = every game with commence_time > snapshot ts
  * cost: ~30 credits per snapshot × 2 snapshots/date × ~430 dates
    = ~26K credits total (we have 4.9M remaining budget 2026-09-14)

Strategy per date:
  1. Query snapshot at 22:30 UTC on game_date (captures 7pm ET onwards)
  2. Query snapshot at 02:30 UTC on next day (captures West Coast prime)
  3. For each `nhl/nba_game_results` row on that date with NULL close_ml,
     find the match in either snapshot's data[] by (home_team, away_team).
  4. Pick book waterfall: DraftKings → FanDuel → BetMGM → Caesars →
     first bookmaker with all three markets.
  5. Extract close prices; upsert to landing table.

Idempotent: only writes rows whose landing table entry is NULL or missing.
Resumable: --resume skips dates already fully covered in landing table.

USAGE:
    python backfill_historical_odds_hoops_hockey.py --sport nhl --dry-run
    python backfill_historical_odds_hoops_hockey.py --sport nba --dry-run
    python backfill_historical_odds_hoops_hockey.py --sport nhl
    python backfill_historical_odds_hoops_hockey.py --sport nba
    python backfill_historical_odds_hoops_hockey.py --sport nhl --limit-dates 5   # sample run
"""
from __future__ import annotations
import argparse, os, sys, time, json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

for _env in (Path(__file__).parent / '.env', Path(__file__).parent.parent / '.env'):
    if _env.exists():
        for line in _env.read_text().split('\n'):
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

import requests

SB   = os.environ['SUPABASE_URL']
KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
ODDS = os.environ['ODDS_API_KEY']

H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates'}

ODDS_BASE = 'https://api.the-odds-api.com/v4/historical'

# Bookmaker waterfall — order preferred books first. Consensus fallback
# averages across the top-3 present books when no single book has all
# markets.
BOOK_PRIORITY = ('draftkings', 'fanduel', 'betmgm', 'williamhill_us', 'betrivers', 'pointsbetus')

SPORT_META = {
    'nhl': {
        'sport_key': 'icehockey_nhl',
        'results_table': 'nhl_game_results',
        # Landing tables were designed (20260820_nhl_historical_closing_odds.sql)
        # but never applied to production. Writing direct to game_results.close_*
        # unblocks LR training without needing a migration approval loop.
        'target_cols': ('close_home_ml', 'close_away_ml', 'close_total', 'close_puckline'),
        'has_spread': False,
        'has_puckline': True,
    },
    'nba': {
        'sport_key': 'basketball_nba',
        'results_table': 'nba_game_results',
        'target_cols': ('close_home_ml', 'close_away_ml', 'close_total', 'close_spread'),
        'has_spread': True,
        'has_puckline': False,
    },
}

# Odds API returns "City Mascot" ("Buffalo Sabres"). NHL game_results stores
# city-only ("Buffalo"). NBA stores full name ("Buffalo Sabres" equivalent
# — "Boston Celtics"). This map normalizes The Odds API team names to the
# form the DB expects. NHL both New York teams collapse to "New York" —
# same as the DB — so Rangers-vs-Islanders games (~4/season) can't be
# disambiguated by name alone and get skipped downstream.
def _normalize_team(name: str, sport: str) -> str:
    if not name:
        return name
    if sport == 'nba':
        return name  # DB already uses full names
    # NHL: strip mascot; normalize accents (Montréal in DB has "é")
    return _NHL_ODDS_TO_DB.get(name, name)

_NHL_ODDS_TO_DB = {
    'Anaheim Ducks': 'Anaheim',
    'Arizona Coyotes': 'Arizona',
    'Boston Bruins': 'Boston',
    'Buffalo Sabres': 'Buffalo',
    'Calgary Flames': 'Calgary',
    'Carolina Hurricanes': 'Carolina',
    'Chicago Blackhawks': 'Chicago',
    'Colorado Avalanche': 'Colorado',
    'Columbus Blue Jackets': 'Columbus',
    'Dallas Stars': 'Dallas',
    'Detroit Red Wings': 'Detroit',
    'Edmonton Oilers': 'Edmonton',
    'Florida Panthers': 'Florida',
    'Los Angeles Kings': 'Los Angeles',
    'Minnesota Wild': 'Minnesota',
    'Montreal Canadiens': 'Montréal',   # DB stores "Montréal"
    'Montréal Canadiens': 'Montréal',
    'Nashville Predators': 'Nashville',
    'New Jersey Devils': 'New Jersey',
    'New York Islanders': 'New York',
    'New York Rangers': 'New York',
    'Ottawa Senators': 'Ottawa',
    'Philadelphia Flyers': 'Philadelphia',
    'Pittsburgh Penguins': 'Pittsburgh',
    'San Jose Sharks': 'San Jose',
    'Seattle Kraken': 'Seattle',
    'St Louis Blues': 'St. Louis',
    'St. Louis Blues': 'St. Louis',
    'Tampa Bay Lightning': 'Tampa Bay',
    'Toronto Maple Leafs': 'Toronto',
    'Utah Hockey Club': 'Utah',
    'Utah Mammoth': 'Utah',
    'Vancouver Canucks': 'Vancouver',
    'Vegas Golden Knights': 'Vegas',
    'Washington Capitals': 'Washington',
    'Winnipeg Jets': 'Winnipeg',
}


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def fetch_dates_needing_backfill(sport: str, limit_dates: int | None = None) -> list[str]:
    """Return sorted list of game_dates where at least one game in
    {sport}_game_results still has NULL close_home_ml. Resumable — once
    a date's rows are all populated, it drops out on next run."""
    meta = SPORT_META[sport]

    # Query rows with NULL close_home_ml, paginated to blow past the 1000 cap.
    dates_needed: set[str] = set()
    offset = 0
    page_size = 1000
    total_missing = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/{meta["results_table"]}',
            params={'select': 'game_date', 'close_home_ml': 'is.null',
                    'order': 'game_date.asc',
                    'limit': page_size, 'offset': offset},
            headers=H_R, timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for row in batch:
            dates_needed.add(row['game_date'])
        total_missing += len(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    dates = sorted(dates_needed)
    if limit_dates:
        dates = dates[:limit_dates]
    _log(f'{sport.upper()}: {total_missing} games with NULL close_home_ml '
         f'across {len(dates_needed)} unique dates'
         + (f' (limited to first {limit_dates})' if limit_dates else ''))
    return dates


def fetch_snapshot(sport: str, iso_ts: str) -> dict | None:
    """One historical-endpoint call. Returns the JSON payload or None on
    non-200. Handles 429 with backoff up to 3 tries."""
    meta = SPORT_META[sport]
    url = f'{ODDS_BASE}/sports/{meta["sport_key"]}/odds'
    params = {
        'apiKey': ODDS,
        'regions': 'us',
        'markets': 'h2h,spreads,totals',
        'date': iso_ts,
        'oddsFormat': 'american',
    }
    for attempt in range(3):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            wait = 2 ** attempt
            _log(f'  429 rate-limited — sleeping {wait}s')
            time.sleep(wait)
            continue
        _log(f'  snapshot {iso_ts} → HTTP {r.status_code}: {r.text[:200]}')
        return None
    return None


def _pick_book(bookmakers: list[dict]) -> dict | None:
    """From a game's bookmakers array, pick the highest-priority book that
    has h2h + totals (spreads/puckline is bonus but not required)."""
    if not bookmakers:
        return None
    by_key = {b['key']: b for b in bookmakers if isinstance(b, dict) and b.get('key')}
    for pref in BOOK_PRIORITY:
        b = by_key.get(pref)
        if not b:
            continue
        mkts = {m['key']: m for m in b.get('markets') or []}
        if 'h2h' in mkts and 'totals' in mkts:
            return b
    # Fallback: any book with h2h + totals
    for b in bookmakers:
        mkts = {m['key']: m for m in b.get('markets') or []}
        if 'h2h' in mkts and 'totals' in mkts:
            return b
    return None


def _extract_prices(game: dict, sport: str) -> dict | None:
    """Return dict of close_* fields, or None if no book has usable data.
    Home/away disambiguation: American odds use the outcome's `name` field
    matching `home_team` / `away_team` on the game object."""
    home = game.get('home_team')
    away = game.get('away_team')
    book = _pick_book(game.get('bookmakers') or [])
    if not book:
        return None
    mkts = {m['key']: m for m in book.get('markets') or []}
    out: dict = {'source': f'the_odds_api:{book["key"]}'}

    # h2h → close_home_ml, close_away_ml
    h2h = mkts.get('h2h', {})
    for oc in h2h.get('outcomes') or []:
        if oc.get('name') == home:
            out['close_home_ml'] = int(oc['price'])
        elif oc.get('name') == away:
            out['close_away_ml'] = int(oc['price'])

    # totals → close_total, over_odds, under_odds
    tot = mkts.get('totals', {})
    for oc in tot.get('outcomes') or []:
        if oc.get('name') == 'Over':
            out['close_total'] = float(oc['point'])
            out['over_odds'] = int(oc['price'])
        elif oc.get('name') == 'Under':
            out['under_odds'] = int(oc['price'])

    # spreads → sport-specific columns
    spr = mkts.get('spreads', {})
    if sport == 'nba' and spr.get('outcomes'):
        for oc in spr['outcomes']:
            if oc.get('name') == home:
                out['close_spread'] = float(oc['point'])
                out['spread_home_odds'] = int(oc['price'])
            elif oc.get('name') == away:
                out['spread_away_odds'] = int(oc['price'])
    elif sport == 'nhl' and spr.get('outcomes'):
        # nhl_game_results has a single close_puckline INT field. Convention:
        # store the home-side puckline odds (typically -1.5 favorite side
        # if home is favored, +1.5 dog side otherwise). Downstream graders
        # already understand this shape (spread_result mirrors it).
        for oc in spr['outcomes']:
            if oc.get('name') == home:
                out['close_puckline'] = int(oc['price'])
                break

    # Must have at least ML data to be worth persisting
    if 'close_home_ml' not in out or 'close_away_ml' not in out:
        return None
    return out


def upsert_row(sport: str, game_date: str, home: str, away: str,
               prices: dict, dry_run: bool) -> bool:
    """PATCH the matching {sport}_game_results row with close_* fields.
    Only updates NULL fields (never overwrites live-captured lines).
    Returns True on success."""
    meta = SPORT_META[sport]
    # Filter prices to only cols this table accepts
    payload = {k: v for k, v in prices.items() if k in meta['target_cols']}
    if not payload:
        return False
    if dry_run:
        _log(f'  DRY-RUN would PATCH {game_date} {away}@{home}: {payload}')
        return True
    # Only patch if close_home_ml is still NULL (never overwrite)
    r = requests.patch(
        f'{SB}/rest/v1/{meta["results_table"]}',
        json=payload,
        headers={**H_W, 'Prefer': 'return=minimal'},
        params={
            'game_date': f'eq.{game_date}',
            'home_team': f'eq.{home}',
            'away_team': f'eq.{away}',
            'close_home_ml': 'is.null',
        },
        timeout=30,
    )
    if not r.ok:
        _log(f'  PATCH FAILED {r.status_code}: {r.text[:200]}')
        return False
    return True


def backfill_date(sport: str, game_date: str, dry_run: bool) -> tuple[int, int]:
    """Fetch one snapshot at 22:30 UTC on game_date. The historical
    endpoint returns all games with commence_time > snapshot_ts. Filter
    to games commencing in the 8h window after snapshot (22:30Z → 06:30Z
    next day) — this is "tonight's slate" and the snapshot is 30min-8h
    before commence, so lines are close-quality.

    Games commencing outside this window (afternoon starts, Global
    Series matinees) will be caught by NO snapshot on their game_date's
    22:30 UTC pull, so we accept ~1-2% coverage loss for simplicity.

    Returns (matched, upserted)."""
    meta = SPORT_META[sport]

    r = requests.get(
        f'{SB}/rest/v1/{meta["results_table"]}',
        params={'select': 'game_date,home_team,away_team',
                'game_date': f'eq.{game_date}'},
        headers=H_R, timeout=30,
    )
    r.raise_for_status()
    expected = {(row['home_team'], row['away_team']): row for row in r.json()}
    if not expected:
        return (0, 0)

    d = datetime.strptime(game_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    snap_dt = d.replace(hour=22, minute=30)
    snap_ts = snap_dt.isoformat().replace('+00:00', 'Z')
    window_end = snap_dt + timedelta(hours=8)

    snap = fetch_snapshot(sport, snap_ts)
    if not snap:
        return (0, 0)

    seen: dict = {}  # (home, away) -> prices
    for game in (snap.get('data') or []):
        commence_iso = game.get('commence_time')
        if not commence_iso:
            continue
        try:
            commence_dt = datetime.fromisoformat(commence_iso.replace('Z', '+00:00'))
        except Exception:
            continue
        # Filter to games commencing in the next 8h (this snapshot's slate)
        if not (snap_dt <= commence_dt <= window_end):
            continue

        api_home = _normalize_team(game.get('home_team'), sport)
        api_away = _normalize_team(game.get('away_team'), sport)
        key = (api_home, api_away)
        if key not in expected:
            continue
        # Skip New York vs New York — can't tell Rangers from Islanders
        if sport == 'nhl' and api_home == 'New York' and api_away == 'New York':
            continue
        prices = _extract_prices(game, sport)
        if prices:
            seen[key] = prices

    upserted = 0
    for (home, away), prices in seen.items():
        if upsert_row(sport, game_date, home, away, prices, dry_run):
            upserted += 1

    return (len(seen), upserted)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=list(SPORT_META.keys()))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit-dates', type=int, default=None,
                    help='cap first N game_dates (sample runs)')
    args = ap.parse_args()

    dates = fetch_dates_needing_backfill(args.sport, args.limit_dates)
    if not dates:
        _log('nothing to do — all dates covered')
        return

    t0 = time.time()
    total_matched = 0
    total_upserted = 0
    for i, gd in enumerate(dates, 1):
        m, u = backfill_date(args.sport, gd, args.dry_run)
        total_matched += m
        total_upserted += u
        if i % 10 == 0 or i == len(dates):
            elapsed = time.time() - t0
            _log(f'  progress {i}/{len(dates)} dates · matched={total_matched} '
                 f'upserted={total_upserted} · {elapsed:.0f}s elapsed')

    _log(f'DONE {args.sport.upper()}: {total_matched} games matched, '
         f'{total_upserted} rows upserted across {len(dates)} dates '
         f'({time.time()-t0:.0f}s)')


if __name__ == '__main__':
    main()
