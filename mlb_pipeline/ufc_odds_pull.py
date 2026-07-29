"""UFC market odds pull — Odds API v4 (h2h moneyline only for MMA tier).

Pulls decimal odds for each fighter across bookmakers, matches to our
ufc_picks rows via fighter names, computes median + best price + book
count, then updates ufc_picks with odds_{a,b}_{median,best} + book_count.

Odds API limits noted (verified 2026-07-29):
  - MMA endpoint (`mma_mixed_martial_arts`) exposes only `h2h` market at
    our tier. `fight_result_method`, `total_rounds`, `to_go_the_distance`,
    `round_betting` all return 422. When we scrape DK/FD directly or
    upgrade tier, add those markets here.
  - MMA response mixes UFC + PFL + Bellator + LFA. Filter to UFC by
    matching fighter names against ufc_picks for target event date.

USAGE:
  python ufc_odds_pull.py                     # today + next 14 days
  python ufc_odds_pull.py --event-date 2026-08-01
  python ufc_odds_pull.py --dry-run
"""
import argparse
import os
import sys
import unicodedata
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
ODDS_KEY = os.environ['ODDS_API_KEY']

H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

ODDS_URL = 'https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds'


def _normalize(name: str) -> str:
    """Accent-strip + lowercase + collapse whitespace for matching."""
    if not name:
        return ''
    n = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', n).strip().lower()


def _median(values: list) -> Optional[float]:
    xs = sorted([v for v in values if v is not None])
    n = len(xs)
    if n == 0:
        return None
    if n % 2 == 1:
        return round(xs[n // 2], 3)
    return round((xs[n // 2 - 1] + xs[n // 2]) / 2, 3)


def load_picks_for_date(event_date: str) -> list:
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks',
        params={'event_date': f'eq.{event_date}',
                'select': 'id,event_name,fight_order,fighter_a,fighter_b'},
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_mma_odds() -> list:
    """One call returns all upcoming MMA events (up to 500 events)."""
    r = requests.get(
        ODDS_URL,
        params={'apiKey': ODDS_KEY, 'regions': 'us', 'markets': 'h2h', 'oddsFormat': 'decimal'},
        timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ Odds API {r.status_code}: {r.text[:200]}')
        return []
    return r.json()


def match_pick_to_event(pick: dict, events: list, target_date: str) -> Optional[dict]:
    """Match a ufc_picks row to an Odds API event by fighter names (normalized).

    Odds API `home_team` / `away_team` are the two fighters — but their
    order does NOT map to our fighter_a / fighter_b booking order.
    Match by name-set intersection, then return (event, a_price, b_price).
    """
    a_norm = _normalize(pick['fighter_a'])
    b_norm = _normalize(pick['fighter_b'])
    # Prefer date match to avoid false positives across separate cards
    for e in events:
        commence = e.get('commence_time', '')
        if target_date and target_date not in commence:
            continue
        home = _normalize(e.get('home_team', ''))
        away = _normalize(e.get('away_team', ''))
        if {home, away} == {a_norm, b_norm}:
            return e
        # Loose match via last-name if exact fails (accent variations, hyphens)
        a_last = a_norm.split()[-1] if a_norm else ''
        b_last = b_norm.split()[-1] if b_norm else ''
        home_last = home.split()[-1] if home else ''
        away_last = away.split()[-1] if away else ''
        if {home_last, away_last} == {a_last, b_last} and all([a_last, b_last]):
            return e
    return None


def extract_prices(event: dict, pick: dict) -> tuple[list, list]:
    """Return (a_prices, b_prices) — decimal odds per book. Handles home/away swap."""
    a_norm = _normalize(pick['fighter_a'])
    b_norm = _normalize(pick['fighter_b'])
    a_prices = []
    b_prices = []
    for bm in event.get('bookmakers', []):
        for market in bm.get('markets', []):
            if market.get('key') != 'h2h':
                continue
            for out in market.get('outcomes', []):
                name_norm = _normalize(out.get('name', ''))
                price = out.get('price')
                if price is None:
                    continue
                if name_norm == a_norm or name_norm.split()[-1] == a_norm.split()[-1]:
                    a_prices.append(price)
                elif name_norm == b_norm or name_norm.split()[-1] == b_norm.split()[-1]:
                    b_prices.append(price)
    return a_prices, b_prices


def update_pick_odds(pick_id: int, odds: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/ufc_picks?id=eq.{pick_id}',
        headers={**H_WRITE, 'Content-Type': 'application/json'},
        json=odds,
        timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f'    ⚠ patch {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(event_date: str | None = None, dry_run: bool = False):
    if event_date is None:
        # Default: pull next Saturday's date + look up all picks on that date
        today = datetime.now(timezone.utc).date()
        # Find next Saturday (or today if Sat)
        days_until_sat = (5 - today.weekday()) % 7
        target = today + timedelta(days=days_until_sat)
        event_date = target.isoformat()

    print(f'== UFC odds pull for {event_date} ==')
    picks = load_picks_for_date(event_date)
    print(f'  {len(picks)} picks in ufc_picks for {event_date}')
    if not picks:
        print('  no picks — abort')
        return

    events = fetch_mma_odds()
    print(f'  {len(events)} MMA events on Odds API')

    matched = 0
    updated = 0
    for pick in picks:
        e = match_pick_to_event(pick, events, event_date)
        if not e:
            print(f'  ✗ NO MATCH: {pick["fighter_a"]} vs {pick["fighter_b"]}')
            continue
        matched += 1
        a_prices, b_prices = extract_prices(e, pick)
        if not a_prices or not b_prices:
            print(f'  ⚠ prices missing: {pick["fighter_a"]} ({len(a_prices)}) vs {pick["fighter_b"]} ({len(b_prices)})')
            continue

        odds = {
            'odds_a_median': _median(a_prices),
            'odds_b_median': _median(b_prices),
            'odds_a_best': round(max(a_prices), 3),
            'odds_b_best': round(max(b_prices), 3),
            'odds_book_count': min(len(a_prices), len(b_prices)),
            'odds_pulled_at': datetime.now(timezone.utc).isoformat(),
        }

        print(f'  ✓ {pick["fighter_a"]} vs {pick["fighter_b"]}: '
              f'A {odds["odds_a_median"]} / B {odds["odds_b_median"]} · {odds["odds_book_count"]} books')

        if not dry_run:
            if update_pick_odds(pick['id'], odds):
                updated += 1

    print(f'\nSummary: matched {matched}/{len(picks)} · updated {updated} rows')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--event-date', help='YYYY-MM-DD (defaults to next Saturday)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(event_date=args.event_date, dry_run=args.dry_run)
