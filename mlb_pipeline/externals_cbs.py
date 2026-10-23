"""CBS Sports expert picks — shared fetcher for every sport.

ANDY 2026-10-22: "CBS does this for every sport
https://www.cbssports.com/nba/expert-picks/"

He is right that it is every sport: the path is /{league}/expert-picks/ and
only the league slug changes.

╔══ WHY THIS ONE IS CHEAP ══════════════════════════════════════════════════╗
The page is SERVER-RENDERED. Verified live before writing a line of this:
498KB of HTML with no __NEXT_DATA__, no window.__INITIAL, no ld+json, and
"CLE +2.5" sitting in the raw markup. So it needs requests and
BeautifulSoup, not Playwright — which matters, because the four NBA sources
that silently produced nothing for a month were the browser-driven ones.

Structure, confirmed against the live page:

    div.picks-table
      div.picks-tbody
        div.picks-tr
          div.picks-td            <- game info
            div.game-info-team    <- AWAY first
              span.team > a[href=".../nba/teams/CLE/cleveland-cavaliers/"]
              span.score
            div.game-info-team    <- HOME second (may carry .winner)
            div.game-info-options > div.current-status   "FINAL" / time
          div.picks-td            <- current odds (o237.5, -2.5)
          div.picks-td            <- expert picks
            div.BetButton-text    "CLE +2.5"  /  "u 237.5"  /  "PHI -2.5"

The team ABBREVIATION comes out of the href path segment rather than the
link text. Both happen to read "CLE" today, but the href is structural and
the text is a label, and a label is the thing that gets restyled.
╚═══════════════════════════════════════════════════════════════════════════╝

╔══ WHAT IT REFUSES TO DO ══════════════════════════════════════════════════╗
FINISHED GAMES ARE SKIPPED. The page leads with completed games — the first
row when this was written was CLE 106 @ PHI 114 FINAL. Storing a "pick" on a
settled game would manufacture a record nobody made before the result was
known, which is the one thing an external-picks table must never do.

A ROW WITH NO MATCHING SLATE GAME IS DROPPED, not guessed. Matching goes
through the caller's find_game_id_fn on team hints, the same path every
other source uses, so a CBS abbreviation that does not resolve is reported
rather than attached to whichever game looked close.
╚═══════════════════════════════════════════════════════════════════════════╝

Used via externals_pro_core.SportPuller.fetch_cbs, so NHL/NBA get it on the
same terms as MLB/NFL rather than through a per-sport copy that drifts.
"""
from __future__ import annotations

import re
from typing import Optional

import requests

BASE = 'https://www.cbssports.com'
UA = {'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0 Safari/537.36')}

#: CBS league slug per sport. Mirrors the site's own URL space.
LEAGUE_SLUG = {
    'NFL': 'nfl', 'NBA': 'nba', 'MLB': 'mlb', 'NHL': 'nhl',
    'NCAAF': 'college-football', 'NCAAB': 'college-basketball',
}

#: A status that means the game is over. Anything here and the row is
#: skipped rather than stored.
_FINAL_MARKERS = ('final', 'postponed', 'canceled', 'cancelled')

#: "CLE +2.5" / "PHI -2.5" / "u 237.5" / "o 237.5" / "CLE ML"
_SPREAD_RE = re.compile(r'^([A-Z]{2,4})\s*([+-]\d+(?:\.\d+)?)$')
_TOTAL_RE = re.compile(r'^([ou])\s*(\d+(?:\.\d+)?)$', re.I)
_ML_RE = re.compile(r'^([A-Z]{2,4})\s*ML$', re.I)
_TEAM_HREF_RE = re.compile(r'/teams/([A-Za-z0-9]+)/')


def _abbrev_from_team_div(div) -> Optional[str]:
    """Team code from the href path, falling back to the link text."""
    a = div.find('a', href=True)
    if a:
        m = _TEAM_HREF_RE.search(a['href'])
        if m:
            return m.group(1).upper()
        if a.get_text(strip=True):
            return a.get_text(strip=True).upper()
    span = div.find('span', class_='team')
    return span.get_text(strip=True).upper() if span else None


def fetch_cbs_generic(sport: str, slate: list, game_date: str,
                      find_game_id_fn, make_pick_fn) -> tuple:
    """Return (picks, http_status).

    find_game_id_fn(slate, home_hint=..., away_hint=...) -> game_id | None
    make_pick_fn(**kwargs) -> the caller's ExternalPick
    """
    from bs4 import BeautifulSoup

    slug = LEAGUE_SLUG.get(str(sport).upper())
    if not slug:
        # A sport we have not mapped returns an empty success, matching
        # covers' behaviour, so an unmapped sport is not an error.
        return [], 200
    url = f'{BASE}/{slug}/expert-picks/'
    try:
        r = requests.get(url, headers=UA, timeout=20)
    except requests.RequestException as e:
        print(f'  cbs {sport}: fetch failed {type(e).__name__}: {e}')
        return [], 0
    if r.status_code != 200:
        return [], r.status_code

    soup = BeautifulSoup(r.text, 'html.parser')
    body = soup.find('div', class_='picks-tbody')
    if not body:
        # Loud, not silent: a layout change here must not look like a
        # quiet day with no picks.
        print(f'  cbs {sport}: no div.picks-tbody — layout changed?')
        return [], 200

    picks, skipped_final, unmatched = [], 0, 0
    for tr in body.find_all('div', class_='picks-tr'):
        teams = tr.find_all('div', class_='game-info-team')
        if len(teams) < 2:
            continue
        away = _abbrev_from_team_div(teams[0])
        home = _abbrev_from_team_div(teams[1])
        if not away or not home:
            continue

        status_div = tr.find('div', class_='current-status')
        status = (status_div.get_text(' ', strip=True).lower()
                  if status_div else '')
        if any(m in status for m in _FINAL_MARKERS):
            skipped_final += 1
            continue

        gid = find_game_id_fn(slate, home_hint=home, away_hint=away)
        if not gid:
            unmatched += 1
            continue

        for btn in tr.find_all('div', class_='BetButton-text'):
            txt = ' '.join(btn.get_text(' ', strip=True).split())
            if not txt:
                continue
            side = line = None
            surface = None
            m = _SPREAD_RE.match(txt)
            if m:
                side, line, surface = m.group(1).upper(), float(m.group(2)), 'spread'
            if side is None:
                m = _TOTAL_RE.match(txt)
                if m:
                    side = 'OVER' if m.group(1).lower() == 'o' else 'UNDER'
                    line, surface = float(m.group(2)), 'total'
            if side is None:
                m = _ML_RE.match(txt)
                if m:
                    side, surface = m.group(1).upper(), 'ml'
            if side is None:
                continue        # a button we do not understand is not a pick
            picks.append(make_pick_fn(
                game_id=gid, sport=str(sport).upper(), game_date=game_date,
                source='cbs', surface=surface, pick_side=side,
                pick_line=line, raw_text=txt, source_url=url))

    print(f'  cbs {sport}: {len(picks)} picks · skipped {skipped_final} '
          f'finished · {unmatched} row(s) matched no slate game')
    return picks, 200
