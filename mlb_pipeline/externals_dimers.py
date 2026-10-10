"""Dimers win-probability picks — sport-parameterised.

2026-10-10 · B72. NHL had 3 external sources and NBA had 0 against MLB's 13,
and the cause was not a failing scraper: externals_pro_core offered only
three fetchers, so the two sports that are taking over from MLB could never
have more than three no matter how often they ran.

WHY THIS IS AN EXTRACTION AND NOT A NEW SCRAPER. pull_externals_ncaaf.py and
pull_externals_mlb.py each carry their own fetch_dimers, and the two differ
ONLY in the URL slug ('cfb' vs 'mlb') and the sport label. The chunk regex,
the win-probability sanity check and the game matching are already generic.
Copying it a third time for NHL and a fourth for NBA is how five sources
ended up MLB-only in the first place, so the shared logic lives here once and
takes the slug as an argument.

VERIFIED AGAINST THE LIVE SITE before being wired, because a regex written
for one sport's team names is exactly the kind of thing that silently returns
zero rows:

    nhl  17,226 chars · 10 regex matches · 10 valid wp pairs
         Blue Jackets 47.3% / Blues 52.7%
         Hurricanes 67.6% / Blackhawks 32.3%
         Predators 40.7% / Senators 59.3%
    cfb  20,664 chars ·  5 matches ·  5 valid
    nba  13,546 chars ·  0 matches ·  0 valid

NBA's zero is the SEASON, not the parser — the league opens 2026-10-21 and
the schedule page carries no game blocks yet. It must be re-verified once
games appear rather than assumed.

RETURNS ([], 200) rather than an error when the page renders but nothing
matches, because an empty slate and a broken parser look identical from here
and only the caller knows whether games were expected. The row count is
printed so a silent zero is at least visible in the log.
"""
from __future__ import annotations

import re
from typing import Callable

#: Per-game block Dimers renders:
#:     AWAY_NAME \n WP% \n [±]SPREAD \n HOME_NAME \n WP%
#: 2026-09-16: the older regex expected an optional rank column between the
#: name and the WP%, which never matched after Dimers moved the spread AFTER
#: the percentage. Every NCAAF pull returned 0 picks while the page rendered
#: fine — the failure mode this module's verification block exists to catch.
CHUNK_RE = re.compile(
    r'([A-Z][A-Za-z. ]{2,20}?)\s*\n\s*(\d{1,2}\.\d)%\s*\n'
    r'\s*[+\-]?\d+\.?\d*\s*\n'
    r'\s*([A-Z][A-Za-z. ]{2,20}?)\s*\n\s*(\d{1,2}\.\d)%',
)

#: Two win probabilities for one game must sum to ~100. Anything else means
#: the regex straddled two games, which is the mis-attribution risk here:
#: emitting a pick on the wrong game is worse than emitting none.
WP_SUM_LO, WP_SUM_HI = 95.0, 105.0

#: Above this, Dimers' model is confident enough to call it a boost rather
#: than a coin flip. Mirrors the NCAAF/MLB copies so the fade_flag means the
#: same thing across sports.
BOOST_WP = 60.0


def fetch_dimers_generic(sport: str, slug: str, slate: list, game_date: str,
                         find_game_id_fn: Callable,
                         make_pick_fn: Callable) -> tuple[list, int]:
    """Dimers model picks for one league. (picks, http_status)."""
    try:
        from _playwright_helper import render_page
    except ImportError:
        print('  ⚠ dimers: _playwright_helper not importable — skip')
        return [], 500
    url = f'https://www.dimers.com/bet-hub/{slug}/schedule'
    text, err = render_page(url)
    if err == 'unavailable':
        print('  ⚠ dimers: Playwright unavailable — skip')
        return [], 200
    if err:
        print(f'  ⚠ dimers render error: {err}')
        return [], 500
    if not text:
        print('  dimers: empty page')
        return [], 200

    picks, seen, straddled = [], set(), 0
    for m in CHUNK_RE.finditer(text):
        away_name, away_wp_s, home_name, home_wp_s = m.groups()
        away_wp, home_wp = float(away_wp_s), float(home_wp_s)
        if not (WP_SUM_LO <= away_wp + home_wp <= WP_SUM_HI):
            straddled += 1
            continue
        # All-or-nothing exact matching via the caller's resolver. No loose
        # fallback: a near-match across two similarly named schools is how
        # a pick gets attributed to the wrong game.
        gid = find_game_id_fn(slate, home_name, away_name)
        if not gid or gid in seen:
            continue
        seen.add(gid)
        pick_side, pick_wp = (('HOME', home_wp) if home_wp >= away_wp
                              else ('AWAY', away_wp))
        picks.append(make_pick_fn(
            game_id=gid, sport=sport, game_date=game_date, source='dimers',
            surface='ml', pick_side=pick_side, odds_american=None,
            confidence=f'{pick_wp:.1f}% wp',
            raw_text=(f'Dimers wp: {away_name} {away_wp:.1f}% / '
                      f'{home_name} {home_wp:.1f}%'),
            fade_flag='boost' if pick_wp >= BOOST_WP else 'neutral',
            source_url=url,
        ))
    print(f'  dimers {slug}: {len(picks)} picks '
          f'({len(seen)} games matched'
          + (f', {straddled} regex straddles discarded' if straddled else '')
          + ')')
    return picks, 200
