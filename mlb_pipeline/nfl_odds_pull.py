"""NFL live-lines pull from Odds API — Phase 1.

Pulls spread, total, and moneyline for upcoming/live NFL games. Upserts to
nfl_game_results on game_id (matches nflverse convention). Runs on the NFL
workflow cron (Tue/Wed/Thu/Sat/Sun) once season starts.

Preseason handling: Odds API sport key is `americanfootball_nfl` for regular
season, `americanfootball_nfl_preseason` for preseason. This script detects
season phase and hits both if needed.

Sign convention (verified against nflverse backfill):
  Odds API: home team spread (-3.5 = home favored by 3.5)
  nflverse: `spread_line` inverted (+3.5 = home favored) — see nfl_backfill
  This script writes the ODDS API convention (native) into
    close_spread / open_spread. Downstream consumers should NOT re-flip.
  A comment in nfl_backfill_results.py line 108 documents the flip; this
  script does NOT apply that flip (it comes from Odds API in the stored
  standard).

USAGE:
    python nfl_odds_pull.py                  # today + next 7 days
    python nfl_odds_pull.py --preseason      # preseason lines
    python nfl_odds_pull.py --dry-run        # print, no write
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ODDS_API_BASE = 'https://api.the-odds-api.com/v4/sports'


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v) -> Optional[float]:
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v) -> Optional[int]:
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_alias_map() -> dict:
    """Map Odds API team names → canonical (nflverse) abbreviation.

    Reads nfl_team_aliases table populated by nfl_seed_aliases.py.
    Returns {"Kansas City Chiefs": "KC", "Philadelphia Eagles": "PHI", ...}.
    """
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_aliases?select=canonical_name,odds_api_name,full_name',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code}')
        return {}
    aliases = {}
    for row in r.json():
        canonical = row.get('canonical_name')
        for name_field in ('odds_api_name', 'full_name'):
            name = row.get(name_field)
            if name and canonical:
                aliases[name] = canonical
    return aliases


def fetch_odds_api(sport_key: str, markets: str = 'spreads,totals,h2h') -> tuple[list, int]:
    """Hit Odds API for a sport key.

    sport_key: 'americanfootball_nfl' | 'americanfootball_nfl_preseason'
    Returns (events_list, http_status). Empty list if no games in window.
    """
    if not ODDS_KEY:
        return [], 401
    url = (f'{ODDS_API_BASE}/{sport_key}/odds/'
           f'?apiKey={ODDS_KEY}&regions=us&markets={markets}&oddsFormat=american')
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ Odds API {sport_key}: {r.status_code} {r.text[:120]}')
        return [], r.status_code
    return r.json(), 200


def _pick_best_line(event: dict, market_key: str) -> dict:
    """Extract best-of-books line for a market. Returns first available book's line.

    For simplicity, take the first bookmaker's line. In future can average
    across books or prefer specific books (Pinnacle, Circa) for sharp lines.
    """
    for book in event.get('bookmakers', []):
        for market in book.get('markets', []):
            if market['key'] == market_key:
                return {
                    'book': book['title'],
                    'outcomes': market['outcomes'],
                    'last_update': market.get('last_update'),
                }
    return {}


def event_to_game_row(event: dict, aliases: dict, sport_phase: str) -> Optional[dict]:
    """Convert one Odds API event → nfl_game_results row.

    Returns None if teams don't map to known nfl_team_aliases (usually
    means bad data or preseason exhibition with non-NFL opponent).
    """
    home_name = event.get('home_team')
    away_name = event.get('away_team')
    home_abbrev = aliases.get(home_name)
    away_abbrev = aliases.get(away_name)
    if not home_abbrev or not away_abbrev:
        return None

    # game_id: nflverse convention is <season>_<week>_<away>_<home>
    # We don't have week/season from Odds API directly. Use start-time-derived
    # game_id: YYYYMMDD_AWAY_HOME (unique per date + matchup).
    commence = event.get('commence_time', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        date_str = dt.date().isoformat()
    except Exception:
        dt = _et_now()
        date_str = dt.date().isoformat()
    game_id = f'{dt.strftime("%Y%m%d")}_{away_abbrev}_{home_abbrev}'

    # 2026-08-28 schema-drift cleanup: table column is `game_type` not
    # `season_type`, and `gametime` not `kickoff_utc`. Script had been
    # written against a schema that never landed.
    row = {
        'game_id': game_id,
        'game_date': date_str,
        'season': dt.year,
        'game_type': 'PRE' if sport_phase == 'preseason' else 'REG',
        'home_team': home_abbrev,
        'away_team': away_abbrev,
        'gametime': commence,
    }

    # Spreads — FLIP sign to match nflverse convention (positive = home fav).
    # Odds API native: home spread -3.5 = home favored by 3.5.
    # nflverse standard: home spread_line +3.5 = home favored by 3.5.
    # Downstream cohort_backfill + weekly_card assume nflverse convention.
    # 2026-08-28: dropped close_{home,away}_spread_ml + close_{over,under}_ml
    # writes — nfl_game_results has no columns for spread/total juice prices
    # and the whole 272-event upsert was 400ing on schema. If we ever need
    # CLV on spread/total juice, add a migration first.
    spread = _pick_best_line(event, 'spreads')
    if spread.get('outcomes'):
        for o in spread['outcomes']:
            point = _f(o.get('point'))
            if o['name'] == home_name:
                row['close_spread'] = -point if point is not None else None  # FLIPPED

    # Totals
    total = _pick_best_line(event, 'totals')
    if total.get('outcomes'):
        for o in total['outcomes']:
            if o['name'] == 'Over':
                row['close_total'] = _f(o.get('point'))

    # Moneyline (h2h)
    ml = _pick_best_line(event, 'h2h')
    if ml.get('outcomes'):
        for o in ml['outcomes']:
            price = _i(o.get('price'))
            if o['name'] == home_name:
                row['close_home_ml'] = price
                row['open_home_ml'] = price      # no history, seed both
            elif o['name'] == away_name:
                row['close_away_ml'] = price
                row['open_away_ml'] = price

    # Mirror close → open on first pull (open captured only if not already stored)
    if row.get('close_spread') is not None and row.get('open_spread') is None:
        row['open_spread'] = row['close_spread']
    if row.get('close_total') is not None and row.get('open_total') is None:
        row['open_total'] = row['close_total']

    return row


def upsert_games(rows: list, dry_run: bool = False) -> int:
    """Batch upsert games into nfl_game_results. Returns write count."""
    if not rows:
        return 0
    if dry_run:
        for r in rows:
            print(f"  [DRY] {r['game_id']}  sp={r.get('close_spread')} "
                  f"tot={r.get('close_total')} ml={r.get('close_home_ml')}/{r.get('close_away_ml')}")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/nfl_game_results?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(preseason: bool = False, dry_run: bool = False) -> None:
    print(f'=== NFL odds pull · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort')
        return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ nfl_team_aliases empty — run nfl_seed_aliases.py first')
        return
    print(f'  alias map: {len(aliases)} team-name variants')

    sport_keys = []
    if preseason:
        sport_keys.append(('americanfootball_nfl_preseason', 'preseason'))
    # Always also try regular season (Odds API will just return empty for out-of-window)
    sport_keys.append(('americanfootball_nfl', 'regular'))

    total_written = 0
    for sport_key, phase in sport_keys:
        events, status = fetch_odds_api(sport_key)
        if status != 200:
            continue
        print(f'  {sport_key}: {len(events)} events')
        if not events:
            continue

        rows = []
        skipped = 0
        for event in events:
            row = event_to_game_row(event, aliases, phase)
            if row is None:
                skipped += 1
                continue
            rows.append(row)
        if skipped:
            print(f'    ⚠ skipped {skipped} events with unmapped teams')

        written = upsert_games(rows, dry_run=dry_run)
        total_written += written
        prefix = '[DRY] ' if dry_run else '✓ '
        print(f'    {prefix}wrote {written} rows to nfl_game_results')

    print(f'\n=== Summary: {total_written} games ===')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preseason', action='store_true',
                    help='Also pull preseason lines (americanfootball_nfl_preseason)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(preseason=args.preseason, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
