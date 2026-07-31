"""Shared OddsCrowd fetcher — sport-agnostic.

OddsCrowd.com exposes per-market money% + bets% + opener lines per game.
Money%/bets% divergence is a strong sharp-money signal — captured for every
sport as data-collection-only for now (NOT wired into any scorer). Future
work backtests "our model X + oddscrowd sharp side ≥N pp = win rate".

Callers (pull_externals_{mlb,nfl,ncaaf,ncaab,nba,nhl,ufc}.py) invoke
fetch_oddscrowd_generic() and wrap the returned dicts into their local
ExternalPick dataclass before writing.

URL structure (verified 7/28):
  List:   /games/upcoming/{sport_url_slug}?hide_leagues=1
  Detail: /games/{away-slug}-vs-{home-slug}-{league_slug}-{month}-{d}-{yyyy}/{id}/best-odds
  Title:  "Best Odds - {Away} at {Home} - {Month} {D} {YYYY} | OddsCrowd"

ID space is cross-sport monotonic — probing beyond the list-page range
picks up late-starting games the list truncates.
"""
import re
import time
from datetime import datetime as _dt
from urllib.parse import urljoin
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup


HEADERS = {'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'}


def _parse_american(tok: str):
    if not tok: return None
    t = tok.strip().replace('EVEN', '+100').replace('even', '+100')
    m = re.match(r'([+-]?\d{2,4})', t)
    if not m: return None
    v = int(m.group(1))
    return v if -900 <= v <= 900 else None


def _parse_line(tok: str):
    if not tok: return None
    m = re.match(r'([+-]?\d+(?:\.\d+)?)', tok.strip())
    if not m: return None
    try: return float(m.group(1))
    except ValueError: return None


def fetch_oddscrowd_generic(
    sport_url_slug: str,        # e.g. 'baseball', 'football', 'basketball', 'hockey', 'mma'
    league_slug: str,           # e.g. 'mlb', 'nfl', 'ncaaf', 'nba', 'ncaab', 'nhl', 'ufc'
    sport_code: str,            # e.g. 'MLB', 'NFL', 'NCAAF', ...
    game_date: str,             # YYYY-MM-DD
    slate: list,                # list of {game_id, home_team, away_team, ...}
    find_game_id_fn: Callable,  # takes (slate, home_hint, away_hint) -> game_id | None
    probe_forward: int = 20,    # how many IDs past the list-page range to probe
    probe_back: int = 3,
    pace_secs: float = 0.3,
) -> tuple[list, int]:
    """Return (list_of_pick_dicts, http_status).

    Each dict has keys:
      game_id, surface ('ml'|'rl'|'total'), pick_side ('HOME'|'AWAY'|'OVER'|'UNDER'),
      pick_line, odds_american, confidence, raw_text, source_url, fade_flag,
      money_pct, bets_pct, divergence_pp   (extras for downstream analytics)
    """
    LIST_URL = f'https://oddscrowd.com/games/upcoming/{sport_url_slug}?hide_leagues=1'
    landing = requests.get(LIST_URL, headers=HEADERS, timeout=15)
    if landing.status_code != 200:
        return [], landing.status_code

    # Date scoping — URL slug carries "-<league>-<month>-<d>-<yyyy>".
    # Late West Coast games (starts >= ~8pm ET / 00:00 UTC) get dated on
    # the NEXT day by OddsCrowd's slug — otherwise our scraper misses
    # SEA@LAD / SF@SD / BOS@ATH etc. Build slugs for today AND today+1
    # and accept either in the date-verification step below.
    from datetime import timedelta as _td
    parts = game_date.split('-')  # ['2026','07','28']
    year = parts[0]
    day = str(int(parts[2]))
    month_full = _dt.strptime(parts[1], '%m').strftime('%B').lower()  # 'july'
    date_slug = f'-{league_slug}-{month_full}-{day}-{year}'

    d1 = _dt.strptime(game_date, '%Y-%m-%d') + _td(days=1)
    year_next = str(d1.year)
    day_next = str(d1.day)
    month_next_full = d1.strftime('%B').lower()
    date_slug_next = f'-{league_slug}-{month_next_full}-{day_next}-{year_next}'

    accepted_dates = {
        (month_full, day, year),
        (month_next_full, day_next, year_next),
    }

    # Discover known game URLs from list page for THIS sport + today OR
    # tomorrow's slug (covers late-night games slugged as tomorrow).
    detail_paths = sorted(set(
        re.findall(rf'/games/[a-z0-9\-]+{re.escape(date_slug)}/\d+/best-odds',      landing.text)
      + re.findall(rf'/games/[a-z0-9\-]+{re.escape(date_slug_next)}/\d+/best-odds', landing.text)
    ))

    # Broader per-sport URL discovery (catches leagues sharing a page, e.g. NFL+NCAAF on /football)
    broader = sorted(set(re.findall(
        r'/games/[a-z0-9\-]+/(\d+)/best-odds',
        landing.text,
    )))
    known_ids = set()
    if broader:
        try:
            known_ids = {int(x) for x in broader}
        except ValueError:
            pass

    # Add probe range around known IDs — detail pages accept any slug once ID is known.
    # Skip IDs already covered by canonical URLs from the list page (otherwise the
    # same game gets scraped twice → duplicate rows in the batch upsert).
    probe_paths = []
    if known_ids:
        lo = min(known_ids) - probe_back
        hi = max(known_ids) + probe_forward
        for gid_int in range(lo, hi + 1):
            if gid_int in known_ids:
                continue
            probe_paths.append(f'/games/probe-vs-probe-{league_slug}-{month_full}-{day}-{year}/{gid_int}/best-odds')

    all_paths = list(dict.fromkeys(detail_paths + probe_paths))
    if not all_paths:
        return [], 200

    # De-dupe safety: dict keyed by (game_id, surface) — last write wins per game×market.
    # Multiple URL paths mapping to the same game_id (e.g. slate has two rows for
    # a doubleheader) will otherwise emit duplicate picks that violate the
    # (source, game_id, surface, pick_side, game_date) unique constraint.
    picks_by_key = {}
    picks = []
    for path in all_paths:
        url = urljoin('https://oddscrowd.com', path)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue

            # Verify sport + date via <title> tag
            tm = re.search(r'<title>\s*Best Odds - (.+?) at (.+?) - ([A-Za-z]+)\s+(\d+)\s+(\d{4})',
                           r.text)
            if not tm:
                continue
            away_name, home_name = tm.group(1).strip(), tm.group(2).strip()
            page_month = tm.group(3).lower()
            page_day = str(int(tm.group(4)))
            page_year = tm.group(5)
            # Accept either today's OR tomorrow's slug — late West Coast
            # games (>= 10pm ET) get dated on the next UTC day by
            # OddsCrowd. We still bucket the pick under our requested
            # game_date because that's how our slate is keyed.
            if (page_month, page_day, page_year) not in accepted_dates:
                continue
            gid = find_game_id_fn(slate, home_hint=home_name, away_hint=away_name)
            if not gid:
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            text = soup.get_text('\n', strip=True)
            anchor = text.find('Odds Comparison')
            if anchor == -1:
                continue
            block = text[anchor: anchor + 4000]

            for surface, header, side_a_lbl, side_b_lbl in [
                ('ml',    'Moneyline', 'AWAY',  'HOME'),
                ('rl',    'Spread',    'AWAY',  'HOME'),
                ('total', 'Total',     'OVER',  'UNDER'),
            ]:
                pat = re.compile(
                    rf'\b{header}\b\s*\n\s*\b{header}\b\s*\n'
                    r'([^\n]+)\n([^\n]+)\n'
                    r'\s*Bets\s*\n\s*(\d+)%\s*\n\s*(\d+)%\s*\n'
                    r'\s*Money\s*\n\s*(\d+)%\s*\n\s*(\d+)%',
                )
                sm = pat.search(block)
                if not sm:
                    continue
                a_bets, b_bets = int(sm.group(3)), int(sm.group(4))
                a_money, b_money = int(sm.group(5)), int(sm.group(6))
                if not (85 <= a_bets + b_bets <= 115): continue
                if not (85 <= a_money + b_money <= 115): continue

                opener_raw = ''
                op = re.search(r'Opener\s*\n([^\n]+(?:\n[^\n]+){0,5})',
                               block[sm.end(): sm.end() + 400])
                if op:
                    opener_raw = op.group(1)
                a_line, a_odds, b_line, b_odds = None, None, None, None
                tokens = [t.strip() for t in opener_raw.split('\n') if t.strip()]
                if surface == 'ml' and len(tokens) >= 2:
                    a_odds, b_odds = _parse_american(tokens[0]), _parse_american(tokens[1])
                elif surface in ('rl', 'total') and len(tokens) >= 4:
                    a_line = _parse_line(tokens[0]); a_odds = _parse_american(tokens[1])
                    b_line = _parse_line(tokens[2]); b_odds = _parse_american(tokens[3])

                if a_money >= b_money:
                    pick_side, money_pct, bets_pct = side_a_lbl, a_money, a_bets
                    other_money, other_bets = b_money, b_bets
                    pick_line, pick_odds = a_line, a_odds
                else:
                    pick_side, money_pct, bets_pct = side_b_lbl, b_money, b_bets
                    other_money, other_bets = a_money, a_bets
                    pick_line, pick_odds = b_line, b_odds

                divergence = money_pct - bets_pct
                if divergence >= 10:
                    fade = 'boost'
                elif divergence >= -5:
                    fade = 'neutral'
                elif other_bets - other_money >= 15:
                    fade = 'boost'
                else:
                    fade = 'neutral'

                a_odds_s = f'{a_odds:+d}' if a_odds is not None else ''
                b_odds_s = f'{b_odds:+d}' if b_odds is not None else ''
                a_ext = f' @ {a_line}({a_odds_s})' if a_line is not None and a_odds is not None else (f' @ {a_odds_s}' if a_odds is not None else '')
                b_ext = f' @ {b_line}({b_odds_s})' if b_line is not None and b_odds is not None else (f' @ {b_odds_s}' if b_odds is not None else '')

                picks_by_key[(gid, surface)] = {
                    'game_id': gid,
                    'sport': sport_code,
                    'game_date': game_date,
                    'source': 'oddscrowd',
                    'surface': surface,
                    'pick_side': pick_side,
                    'pick_line': pick_line,
                    'odds_american': pick_odds,
                    'confidence': f'money {money_pct}% / bets {bets_pct}% (div {divergence:+d}pp)',
                    'raw_text': (f'OddsCrowd {header}: '
                                 f'{side_a_lbl} money {a_money}%/bets {a_bets}%{a_ext} · '
                                 f'{side_b_lbl} money {b_money}%/bets {b_bets}%{b_ext}'),
                    'source_url': url,
                    'fade_flag': fade,
                    'money_pct': money_pct,
                    'bets_pct': bets_pct,
                    'divergence_pp': divergence,
                }
            time.sleep(pace_secs)
        except Exception:
            continue

    return list(picks_by_key.values()), 200
