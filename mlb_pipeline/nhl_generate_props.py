"""NHL prop generator (2026-08-17).

Sweep The Odds API for NHL player props (goalie + skater), upsert into
nhl_pipeline_props. Attaches signals from ctx (goalie GSAA, matchup pace)
so playbook scorer can fire real reasoning.

Markets covered:
  * player_shots_on_goal (skater SOG)
  * player_points (skater)
  * player_goals (skater)
  * player_assists (skater)
  * player_total_saves (goalie)

For each prop:
  1. Insert/update row with book_line + odds
  2. Attach signals JSONB with any known info (goalie_gsaa if starter,
     pace tag if opponent is high-pace team)
  3. Tier stays SKIP/COVERAGE — playbook + refit layer decide load

CLI:
  python nhl_generate_props.py                  # today ET
  python nhl_generate_props.py --date 2026-10-07
  python nhl_generate_props.py --dry-run
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import Counter
from datetime import date, datetime, timezone, timedelta
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
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

ODDS_BASE = 'https://api.the-odds-api.com/v4/sports/icehockey_nhl'
UA = 'Mozilla/5.0 (SweatLocker research)'

# Odds API market key → our prop_type prefix
MARKET_MAP = {
    'player_shots_on_goal': 'sog',
    'player_points':        'points',
    'player_goals':          'goals',
    'player_assists':        'assists',
    'player_total_saves':    'saves',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_events(game_date: Optional[str] = None) -> list:
    """Odds API events in a window around `game_date` (default: now).

    2026-09-21 FIX: this ignored the caller's date entirely and always
    used now-8h..now+36h, so `--date` was accepted by argparse and
    silently did nothing. Any run for a future or past slate fetched
    today's window and reported "0 events fetched" — indistinguishable
    from a genuinely empty slate. Same accepted-but-ignored shape as the
    other flag bugs found today.
    """
    if not ODDS_KEY: return []
    if game_date:
        # ET day -> UTC window covering it, with slack for late starts
        base = datetime.fromisoformat(game_date).replace(tzinfo=timezone.utc)
        start = base + timedelta(hours=4)      # 00:00 ET
        from_iso = (start - timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
        to_iso = (start + timedelta(hours=32)).strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        from_iso = (datetime.now(timezone.utc) - timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M:%SZ')
        to_iso = (datetime.now(timezone.utc) + timedelta(hours=36)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = requests.get(f'{ODDS_BASE}/events',
                     params={'apiKey': ODDS_KEY,
                             'commenceTimeFrom': from_iso,
                             'commenceTimeTo': to_iso},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


# 2026-09-21: distinguish the reasons a run yields zero props. Verified
# against the live API: an invalid market key returns HTTP 422
# INVALID_MARKET, it does NOT fail silently — and our five keys return 200,
# so they are all valid including player_goals. Today 0 props is correct:
# no book posts NHL player markets 8 days out (checked three events, only
# game markets offered). But "0 props" currently reads the same whether the
# markets are absent, the key set was rejected, or parsing produced
# nothing — and on Oct 8 those need to be tellable apart, because two of
# them are bugs and one is a Tuesday.
_FETCH_DIAG = Counter()


def fetch_props_for_event(event_id: str) -> dict:
    """Return {(player, prop_type, direction, line): {book_line, over_odds, under_odds, book}}."""
    r = requests.get(
        f'{ODDS_BASE}/events/{event_id}/odds',
        params={'apiKey': ODDS_KEY, 'regions': 'us,us2',
                'markets': ','.join(MARKET_MAP.keys()),
                'oddsFormat': 'american'},
        timeout=20)
    if r.status_code == 422:
        _FETCH_DIAG['invalid_market_key'] += 1
        print(f'  🚨 Odds API rejected our market keys: {r.text[:160]}')
        return {}
    if r.status_code != 200:
        _FETCH_DIAG[f'http_{r.status_code}'] += 1
        return {}
    data = r.json()
    if not (data.get('bookmakers') or []):
        # 200 with no bookmakers = nobody is pricing these markets yet.
        _FETCH_DIAG['no_books_offering'] += 1

    # ══ 2026-10-09 · STORE BOTH SIDES OF EVERY PRICE ═══════════════════════
    # The old key was (player, prop_type, direction, line) where prop_type
    # ALREADY embedded the direction. So the over outcome and the under
    # outcome for the same player and line landed in two SEPARATE slots, and
    # each slot only ever had its own side filled — `under_odds` was None on
    # every over row and vice versa. Measured consequence:
    # nhl_pipeline_props had 0.0% of 27,890 rows carrying both prices, while
    # MLB (which does this correctly) carries both on 91-96%.
    #
    # WHY IT MATTERS beyond tidiness: without the opposite side's price you
    # cannot evaluate what the other side WOULD have returned. That blocks
    # every "should we have taken the other side" question — the NHL prop
    # fade test (our overs hit 44.9%, below the 45% our discipline says to
    # fade, and it is unmeasurable without the under price) and the
    # reweighting shadow in B66a, which is blocked for exactly this reason.
    #
    # AND THE PAIR MUST COME FROM ONE BOOK. The old code let prices overwrite
    # across bookmakers while `book` kept the FIRST one, so a stored price
    # could be attributed to the wrong book. Worse, an over from book A
    # paired with an under from book B is a FABRICATED no-vig line — stitching
    # two books' best sides invents a market nobody offered. So prices are
    # collected per book, and the chosen book is one that quotes BOTH sides
    # where such a book exists.
    per_book = {}          # (player_lc, base, line) -> {book: {over,under}}
    display = {}
    for bk in data.get('bookmakers', []):
        book = bk.get('key')
        for mkt in bk.get('markets', []):
            base = MARKET_MAP.get(mkt.get('key'))
            if not base: continue
            for out in mkt.get('outcomes', []):
                player = (out.get('description') or '').strip()
                side = (out.get('name') or '').lower()
                direction = 'over' if 'over' in side else ('under' if 'under' in side else None)
                if not direction or not player: continue
                line = out.get('point'); price = out.get('price')
                if line is None or price is None: continue
                # NOTE: no direction in the key — that is the whole fix.
                key = (player.lower(), base, float(line))
                display[key] = player
                per_book.setdefault(key, {}).setdefault(
                    book, {'over_odds': None, 'under_odds': None})
                per_book[key][book][f'{direction}_odds'] = int(price)

    by_key = {}
    for key, books in per_book.items():
        # Prefer a book quoting BOTH sides so the pair is a real market.
        two_sided = [(b, q) for b, q in books.items()
                     if q['over_odds'] is not None and q['under_odds'] is not None]
        book, quote = (two_sided[0] if two_sided
                       else next(iter(books.items())))
        _player_lc, base, line = key
        by_key[key] = {
            'display': display[key], 'line': line, 'base': base,
            'over_odds': quote['over_odds'],
            'under_odds': quote['under_odds'],
            'book': book,
            # Recorded so a one-sided row is distinguishable from a bug.
            'two_sided': bool(two_sided),
        }
    return by_key


def upsert_prop(payload: dict, dry_run: bool = False) -> bool:
    if dry_run: return True
    try:
        pr = requests.post(
            f'{SB}/rest/v1/nhl_pipeline_props?on_conflict=game_date,player_name,prop_type,direction,prop_line',
            headers=H_WRITE, json=payload, timeout=15)
        return pr.status_code in (200, 201, 204)
    except Exception as e:
        print(f'    ✗ {e}')
        return False


def run(game_date: Optional[str] = None, dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== nhl_generate_props · {gd} ===')
    if not ODDS_KEY:
        print('  ⚠ ODDS_API_KEY missing — skipping')
        return

    events = fetch_events(gd)
    # Filter to target date's events (commence_time roughly matches gd in ET)
    print(f'  {len(events)} NHL events fetched')

    total_props = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for ev in events:
        commence = ev.get('commence_time', '')
        # Rough date filter — commence is UTC, we're looking for ET game_date
        # Include events commencing on gd OR the calendar-day after (late games).
        if not (commence.startswith(gd) or commence.startswith((date.fromisoformat(gd) + timedelta(days=1)).isoformat())):
            continue
        home = ev.get('home_team'); away = ev.get('away_team')
        matchup = f'{away} @ {home}'
        event_id = ev.get('id')
        props = fetch_props_for_event(event_id)
        if not props: continue

        # One slot now holds BOTH sides for a (player, prop, line), so emit
        # one row per priced direction and give EACH row the full price pair.
        # Row count is unchanged versus the old per-direction slots — what
        # changes is that both rows now carry both prices.
        for (player_lc, base, line), slot in props.items():
            for direction in ('over', 'under'):
                if slot.get(f'{direction}_odds') is None:
                    continue    # that side genuinely was not quoted
                payload = {
                    'game_date':      gd,
                    'game_id':        event_id,
                    'player_name':    slot['display'],
                    'matchup':        matchup,
                    'prop_type':      f'{base}_{direction}',
                    'direction':      direction,
                    'prop_line':      line,
                    'book_line':      line,
                    # Both sides, from the SAME book — see fetch_props_for_event.
                    'book_over_odds': slot.get('over_odds'),
                    'book_under_odds': slot.get('under_odds'),
                    'book_source':    slot.get('book'),
                    'tier':           'COVERAGE',
                    'conviction':     0,
                    'signals':        {'two_sided_price': slot.get('two_sided')},
                    'last_attached_at': now_iso,
                }
                if upsert_prop(payload, dry_run=dry_run):
                    total_props += 1

    print(f'\n  {"[DRY] " if dry_run else ""}upserted {total_props} NHL props')
    if total_props == 0 and events:
        # Say WHY, so an empty run is diagnosable rather than ambiguous.
        d = dict(_FETCH_DIAG)
        print(f'  ℹ 0 props from {len(events)} event(s) · reasons={d or "none recorded"}')
        if d.get('invalid_market_key'):
            print('     → market keys rejected by the API. This is a BUG, fix the keys.')
        elif d.get('no_books_offering'):
            print('     → no book is pricing NHL player markets yet. Expected this far '
                  'from puck drop; player props post ~1-2 days out.')
        elif not d:
            print('     → no event reached the prop fetch (date filter excluded them all), '
                  'or markets parsed to nothing. Check the date window first.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date'); p.add_argument('--dry-run', action='store_true')
    # season_gate reads this off sys.argv, but strict argparse rejects
    # the unknown arg first — so the documented bypass could not be
    # passed. Declared only so argparse lets it through.
    p.add_argument('--force-offseason', action='store_true',
                   help='run even when the sport is out of season')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NHL')
    except ImportError:
        pass
    main()
