"""Covers.com public consensus — shared fetcher for football sports.

Andy 2026-09-25: "lets get more [externals] and surface".

NCAAF's fetch_covers was a STUB (`return [], 200`) while NFL's worked and has
collected 557 picks. Same site, same table, same markup — verified live:

    contests.covers.com/consensus/topconsensus/nfl/overall   -> 61 rows
    contests.covers.com/consensus/topconsensus/ncaaf/overall -> 72 rows
    contests.covers.com/consensus/topconsensus/ncaab/overall ->  0 rows (offseason)

Row shape is identical across leagues:
    ['NCAAF Jmu Odu', 'Sat. Sep 26 6:00 pm ET', '73% 27%', '-6.5 +6.5', '267 97']
     matchup           date                      consensus%  spreads      volume

Generalised rather than copied. The 09-21 pickdawgz note made the case: a
per-sport copy of a 100-line parser drifts, and this repo has now been bitten
three times in one day by a fix applied to one sport and not its siblings
(NCAAF's pull-date stamping fixed 08-29 but not NFL's; NCAAF's prose fix
09-05 but not the other five sports; pickdawgz wired for MLB but stubbed
elsewhere).

WHAT THIS IS. Covers publishes PUBLIC BETTING CONSENSUS — the share of bets on
each side — not a handicapper's pick. We emit the majority side so it grades,
but a record built from it measures "follow the public", which is why heavy
consensus is tagged fade rather than boost. See
[[project_externals_are_mostly_money_flow_925]]: most of our "externals" are
signals of this kind, and only pickswise + pickdawgz are real picks.
"""
from __future__ import annotations

import re
from datetime import datetime as _dt

import requests

UA = {'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'}
BASE = 'https://contests.covers.com/consensus/topconsensus'

# Covers loads its table client-side on some leagues but serves it in the HTML
# here; if that ever changes the row count drops to zero and the externals
# watchdog reports the source dark rather than logging a silent success.
LEAGUE_SLUG = {'NFL': 'nfl', 'NCAAF': 'ncaaf', 'NCAAB': 'ncaab'}

# Public consensus at or above this share is a fade signal, not a follow.
FADE_PCT = 75


def covers_date_to_iso(date_txt: str, year: int) -> str:
    """'Sun. Sep 27 1:00 pm ET' -> '2026-09-27'. Best-effort."""
    m = re.search(r'(\w{3})\.?\s+(\w{3})\s+(\d+)', date_txt or '')
    if not m:
        return ''
    try:
        return _dt.strptime(f'{m.group(2)} {m.group(3)} {year}',
                            '%b %d %Y').date().isoformat()
    except ValueError:
        return ''


def fetch_covers_generic(sport: str, slate: list, year: int,
                         find_game_id_fn, make_pick_fn) -> tuple:
    """Return (picks, http_status).

    find_game_id_fn(slate, home_hint=..., away_hint=...) -> game_id | None
    make_pick_fn(**kwargs) -> the caller's ExternalPick
    """
    from bs4 import BeautifulSoup

    slug = LEAGUE_SLUG.get(str(sport).upper())
    if not slug:
        return [], 200
    try:
        r = requests.get(f'{BASE}/{slug}/overall', headers=UA, timeout=15)
    except requests.RequestException as e:
        print(f'  covers {sport}: fetch failed {type(e).__name__}: {e}')
        return [], 0
    if r.status_code != 200:
        return [], r.status_code

    soup = BeautifulSoup(r.text, 'html.parser')
    table = soup.find('table')
    if not table:
        print(f'  covers {sport}: no table in response')
        return [], 200

    # Accept any date on the slate, not just today. Football runs weekly and
    # the puller runs mid-week, so filtering to the run date would drop the
    # whole Saturday/Sunday card — the same fixed-horizon mistake that cost
    # pickdawgz most of its NFL season.
    slate_dates = {g.get('game_date') for g in slate if g.get('game_date')}
    picks, skipped_date, skipped_match = [], 0, 0

    for row in table.find_all('tr')[1:]:
        cells = [c.get_text(' ', strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) < 4:
            continue

        matchup_txt = re.sub(r'^(NCAAF|NCAAB|NFL)\s*', '', cells[0]).strip()
        row_date = covers_date_to_iso(cells[1], year)
        if not row_date or row_date not in slate_dates:
            skipped_date += 1
            continue

        parts = matchup_txt.split()
        if len(parts) < 2:
            continue
        away_hint, home_hint = parts[0], parts[1]
        gid = find_game_id_fn(slate, home_hint=home_hint, away_hint=away_hint)
        if not gid:
            skipped_match += 1
            continue

        pct = re.findall(r'(\d+)%', cells[2])
        if len(pct) < 2:
            continue
        away_pct, home_pct = int(pct[0]), int(pct[1])

        spreads = re.findall(r'([+\-]\d+\.?\d*)', cells[3])
        away_line = float(spreads[0]) if len(spreads) >= 1 else None
        home_line = float(spreads[1]) if len(spreads) >= 2 else None

        if home_pct > away_pct:
            side, line, share = 'HOME', home_line, home_pct
        else:
            side, line, share = 'AWAY', away_line, away_pct

        picks.append(make_pick_fn(
            game_id=gid, sport=str(sport).upper(), game_date=row_date,
            source='covers',
            # external_picks has a check constraint (ext_picks_surface_ck)
            # allowing only ml/rl/total/prop, so an against-the-spread pick
            # rides on 'rl'.
            surface='rl', pick_side=side, pick_line=line,
            odds_american=None,   # consensus, not a book price
            confidence=f'{share}% public',
            raw_text=f'Public spread consensus: {away_pct}% away / {home_pct}% home',
            fade_flag='fade' if share >= FADE_PCT else 'neutral',
        ))

    if not picks:
        # Say which half failed. A silent `return [], 200` here is what let
        # five sources sit dead for a season.
        print(f'  covers {sport}: 0 picks — {skipped_date} row(s) outside the '
              f'slate window, {skipped_match} unmatched to a game_id')
    return picks, 200
