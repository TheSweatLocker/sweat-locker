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

    2026-08-08 fixes:
    - Date filter expanded to target_date + next UTC day (late-night ET
      cards fall on next UTC day: Aug 8 9pm ET = Aug 9 01:00 UTC).
      Missed 5/8 fights on 8/8 slate before this fix.
    - Loose match now also strips 'del ', 'de ', 'da ', 'la ' prefixes
      and squashes concatenations ('DelValle' vs 'del Valle') by also
      trying the raw alphabetic characters only.
    - Substring match: if all last-name tokens of one side appear in
      other, accept (handles "Billy Ray Goff" vs "Billy Goff",
      "Carlos Diego Ferreira" vs "Diego Ferreira").
    """
    a_norm = _normalize(pick['fighter_a'])
    b_norm = _normalize(pick['fighter_b'])
    # Build date window: target_date + next UTC day
    date_prefixes = [target_date]
    if target_date:
        try:
            dt = datetime.strptime(target_date, '%Y-%m-%d')
            date_prefixes.append((dt + timedelta(days=1)).strftime('%Y-%m-%d'))
        except ValueError:
            pass

    def _alpha_only(s: str) -> str:
        return re.sub(r'[^a-z]', '', s or '')

    for e in events:
        commence = e.get('commence_time', '')
        if date_prefixes and not any(dp in commence for dp in date_prefixes):
            continue
        home = _normalize(e.get('home_team', ''))
        away = _normalize(e.get('away_team', ''))
        if {home, away} == {a_norm, b_norm}:
            return e
        # Loose match via last-name if exact fails
        a_last = a_norm.split()[-1] if a_norm else ''
        b_last = b_norm.split()[-1] if b_norm else ''
        home_last = home.split()[-1] if home else ''
        away_last = away.split()[-1] if away else ''
        if {home_last, away_last} == {a_last, b_last} and all([a_last, b_last]):
            return e
        # Alpha-only concatenation match (DelValle vs del Valle)
        a_alpha = _alpha_only(a_norm); b_alpha = _alpha_only(b_norm)
        home_alpha = _alpha_only(home); away_alpha = _alpha_only(away)
        if {home_alpha, away_alpha} == {a_alpha, b_alpha}:
            return e
        # Substring last-token match (Billy Goff ⊂ Billy Ray Goff)
        def _tokens(s): return set(s.split()) if s else set()
        a_toks, b_toks = _tokens(a_norm), _tokens(b_norm)
        h_toks, w_toks = _tokens(home), _tokens(away)
        # If home's tokens are a subset of a's OR a's tokens ⊂ home's, and same for away/b
        def _sub(x, y): return len(x & y) >= min(2, min(len(x), len(y)))
        if ((_sub(h_toks, a_toks) and _sub(w_toks, b_toks)) or
            (_sub(w_toks, a_toks) and _sub(h_toks, b_toks))):
            return e
    return None


def extract_prices(event: dict, pick: dict) -> tuple[list, list]:
    """Return (a_prices, b_prices) — decimal odds per book. Handles home/away swap.

    2026-08-08 hardening: match logic now uses last-name, alpha-only
    concatenation, and token-subset in that priority order — same
    strategy as match_pick_to_event, so any match found there succeeds
    at the outcome level too.
    """
    a_norm = _normalize(pick['fighter_a'])
    b_norm = _normalize(pick['fighter_b'])
    a_alpha = re.sub(r'[^a-z]', '', a_norm)
    b_alpha = re.sub(r'[^a-z]', '', b_norm)
    a_last = a_norm.split()[-1] if a_norm else ''
    b_last = b_norm.split()[-1] if b_norm else ''
    a_tokens = set(a_norm.split()) if a_norm else set()
    b_tokens = set(b_norm.split()) if b_norm else set()

    def _side_for(name_norm: str) -> str | None:
        """Return 'a', 'b', or None for a bookmaker outcome name."""
        if name_norm == a_norm: return 'a'
        if name_norm == b_norm: return 'b'
        name_alpha = re.sub(r'[^a-z]', '', name_norm)
        if name_alpha == a_alpha: return 'a'
        if name_alpha == b_alpha: return 'b'
        name_last = name_norm.split()[-1] if name_norm else ''
        if name_last and name_last == a_last: return 'a'
        if name_last and name_last == b_last: return 'b'
        name_tokens = set(name_norm.split()) if name_norm else set()
        if a_tokens and len(name_tokens & a_tokens) >= min(2, min(len(a_tokens), len(name_tokens))): return 'a'
        if b_tokens and len(name_tokens & b_tokens) >= min(2, min(len(b_tokens), len(name_tokens))): return 'b'
        return None

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
                side = _side_for(name_norm)
                if side == 'a': a_prices.append(price)
                elif side == 'b': b_prices.append(price)
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
