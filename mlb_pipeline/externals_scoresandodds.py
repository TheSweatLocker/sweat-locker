"""Shared ScoresAndOdds fetcher — sport-agnostic (2026-08-25).

ScoresAndOdds.com exposes public bet% + money% per game for MLB/NFL/NCAAF/
NBA/NCAAB/NHL. Mirror of externals_oddscrowd.py architecture — the two
sources are complementary: OC has better MLB coverage, SO has better
football coverage. Together they seed the SHARP TRIPLE_CONFIRMED tier.

URL structure (verified 2026-08-25):
  List:   /{league}                      (e.g. /mlb, /nfl, /ncaaf)
  Detail: /{league}/{away-slug}-vs-{home-slug}
          (no date in slug; page implicitly means "today's/nearest matchup")

Text pattern on detail page:
  **TB** % of Bets **DET** 81% 19% 75% 25% % of Money
  ^team1  ^label   ^team2  ^t1B ^t2B ^t1M ^t2M
  Rows repeat for Moneyline / Spread / Total; sometimes labeled with
  the line value (e.g. `Total o8` or `Runline +1.5`).

Callers (pull_externals_{mlb,nfl,ncaaf,ncaab,nba,nhl}.py) invoke
fetch_scoresandodds_generic() and wrap dicts into their local ExternalPick
dataclass before writing. Output shape is IDENTICAL to externals_oddscrowd
so downstream consumers (source=`scoresandodds`) get the same fields.
"""
from __future__ import annotations
import re
from typing import Callable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


HEADERS = {'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'}
BASE = 'https://www.scoresandodds.com'


def _parse_american(tok: str):
    if not tok:
        return None
    t = tok.strip().replace('EVEN', '+100').replace('even', '+100')
    m = re.match(r'([+-]?\d{2,4})', t)
    if not m:
        return None
    v = int(m.group(1))
    return v if -900 <= v <= 900 else None


def _parse_line(tok: str):
    if not tok:
        return None
    m = re.match(r'([+-]?\d+(?:\.\d+)?)', tok.strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _slugify(team_name: str) -> str:
    """Turn 'Tampa Bay Rays' into 'rays' (city+nickname → nickname only for MLB;
    for other sports the site uses various shortenings)."""
    if not team_name:
        return ''
    # Strategy: try common last-word (nickname) first, fall back to full slug.
    tokens = re.split(r'\s+', team_name.strip())
    last = tokens[-1].lower()
    return re.sub(r'[^a-z0-9]+', '-', last).strip('-')


def _extract_detail_paths(list_html: str, league_slug: str) -> list[str]:
    """Find every /{league}/{slug}-vs-{slug} link in the list HTML."""
    # href="/{league}/rays-vs-tigers" — no trailing date component.
    pattern = rf'/{re.escape(league_slug)}/([a-z0-9-]+-vs-[a-z0-9-]+)(?:\?[^"\'\s]*)?'
    hits = set(re.findall(pattern, list_html))
    # Filter out obvious non-game links (parlay/events sub-paths handled
    # by the vs pattern already).
    return sorted(f'/{league_slug}/{h}' for h in hits)


def _parse_splits_block(text: str) -> list[dict]:
    """Parse the '% of Bets' / '% of Money' rows.

    Each row on ScoresAndOdds looks like (line-broken):
       {team_left}          <- ML: 'TB'; Spread: 'TB (-1.5)'; Total: 'Over (o8)'
       % of Bets
       {team_right}         <- ML: 'DET'; Spread: 'DET (+1.5)'; Total: 'Under (u8)'
       {A}%                 <- team_left bets %
       {B}%                 <- team_right bets %
       {C}%                 <- team_left money %
       {D}%                 <- team_right money %
       % of Money

    Surface is inferred from the team_left label:
      - contains 'Over (o' → total
      - contains '(-' or '(+'  → rl / spread
      - else → ml
    """
    rows = []
    # ScoresAndOdds line-breaks team labels inconsistently — 'TB\n (-1.5)'
    # for spreads, 'Over\n(o8)' for totals. Rather than capture the labels
    # rigidly, anchor on the '% of Bets ... 4 percentages ... % of Money'
    # shape and grab the ~140 chars of context BEFORE the anchor for
    # surface classification.
    pat = re.compile(
        r'%\s*of\s*Bets\s*\n'
        r'(?:[^%\n]{0,80}\n)?'               # optional team_right line(s), no digits
        r'(?:[^%\n]{0,40}\n)?'               # optional 2nd line for split label
        r'\s*(\d{1,3})%\s*\n'                # a_bets
        r'\s*(\d{1,3})%\s*\n'                # b_bets
        r'\s*(\d{1,3})%\s*\n'                # a_money
        r'\s*(\d{1,3})%\s*\n'                # b_money
        r'\s*%\s*of\s*Money',
        re.IGNORECASE,
    )
    for m in pat.finditer(text):
        a_bets, b_bets = int(m.group(1)), int(m.group(2))
        a_money, b_money = int(m.group(3)), int(m.group(4))
        if not (85 <= a_bets + b_bets <= 115):
            continue
        if not (85 <= a_money + b_money <= 115):
            continue
        # Surface classification: look at ~140 chars before the anchor.
        pre_start = max(0, m.start() - 140)
        pre_ctx = text[pre_start:m.start()]
        if re.search(r'\bOver\b\s*\n?\s*\(?o?\d', pre_ctx, re.IGNORECASE):
            surface = 'total'
        elif re.search(r'\([+-]\s*\d', pre_ctx):
            surface = 'rl'
        else:
            surface = 'ml'
        left_lbl = pre_ctx.strip().split('\n')[-1] if pre_ctx else ''
        right_lbl = ''
        rows.append({
            'surface': surface,
            'a_bets': a_bets, 'b_bets': b_bets,
            'a_money': a_money, 'b_money': b_money,
            'left_lbl': left_lbl, 'right_lbl': right_lbl,
        })
    # De-dupe by surface (keep first — usually the highest-priority row).
    seen = set()
    dedup = []
    for r in rows:
        if r['surface'] in seen:
            continue
        seen.add(r['surface'])
        dedup.append(r)
    return dedup


def _fetch_slate_page(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return r.text


def _match_slate(slate: list, slug: str,
                 find_game_id_fn: Callable) -> tuple[Optional[str], bool]:
    """The slug is 'part1-vs-part2'. ScoresAndOdds' slug ordering is
    inconsistent — some slugs are away-vs-home, others home-vs-away — so
    we try both orderings before giving up.

    Returns (game_id, flipped) where flipped=True means the slug order
    was home-vs-away rather than the more common away-vs-home. Callers
    need `flipped` to correctly assign side_a_lbl / side_b_lbl to
    HOME / AWAY in the splits row.
    """
    if '-vs-' not in slug:
        return None, False
    p1, p2 = slug.split('-vs-', 1)
    p1_hint = p1.replace('-', ' ').strip()
    p2_hint = p2.replace('-', ' ').strip()
    try:
        gid = find_game_id_fn(slate, home_hint=p2_hint, away_hint=p1_hint)
        if gid:
            return gid, False  # away-vs-home ordering
        gid = find_game_id_fn(slate, home_hint=p1_hint, away_hint=p2_hint)
        if gid:
            return gid, True   # home-vs-away ordering (SO flipped)
    except Exception:
        pass
    return None, False


def fetch_scoresandodds_generic(
    league_slug: str,           # 'mlb', 'nfl', 'ncaaf', 'nba', 'ncaab', 'nhl'
    sport_code: str,            # 'MLB', 'NFL', 'NCAAF', ...
    game_date: str,             # YYYY-MM-DD — stamped on outgoing picks
    slate: list,                # [{game_id, home_team, away_team, ...}]
    find_game_id_fn: Callable,  # (slate, home_hint, away_hint) -> game_id | None
) -> tuple[list, int]:
    """Return (list_of_pick_dicts, http_status).

    Each dict shape matches externals_oddscrowd output — source stamped
    'scoresandodds' so downstream table constraints route it correctly.
    """
    list_url = f'{BASE}/{league_slug}'
    landing = _fetch_slate_page(list_url)
    if landing is None:
        return [], 0

    detail_paths = _extract_detail_paths(landing, league_slug)
    if not detail_paths:
        return [], 200

    picks_by_key = {}
    seen_paths = set()
    for path in detail_paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)

        slug = path.rsplit('/', 1)[-1]
        gid, slug_flipped = _match_slate(slate, slug, find_game_id_fn)
        if not gid:
            continue

        url = urljoin(BASE, path)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except Exception:
            continue
        if r.status_code != 200:
            continue

        soup = BeautifulSoup(r.text, 'html.parser')
        text = soup.get_text('\n', strip=True)

        # Locate the public splits block via anchor keyword.
        anchor = text.find('% of Bets')
        if anchor == -1:
            # Older/mobile layouts sometimes say "Public Consensus" first.
            anchor = text.find('Public Consensus')
        if anchor == -1:
            continue
        block = text[max(0, anchor - 200): anchor + 3000]

        for row in _parse_splits_block(block):
            surface = row['surface']
            a_bets, b_bets = row['a_bets'], row['b_bets']
            a_money, b_money = row['a_money'], row['b_money']

            # Side mapping. The team column labels on the SO page follow
            # the slug order, so if the slug is home-vs-away (slug_flipped),
            # the LEFT column is HOME. For totals, OVER is always left.
            if surface == 'total':
                side_a_lbl, side_b_lbl = 'OVER', 'UNDER'
            elif slug_flipped:
                side_a_lbl, side_b_lbl = 'HOME', 'AWAY'
            else:
                side_a_lbl, side_b_lbl = 'AWAY', 'HOME'

            if a_money >= b_money:
                pick_side, money_pct, bets_pct = side_a_lbl, a_money, a_bets
                other_money, other_bets = b_money, b_bets
            else:
                pick_side, money_pct, bets_pct = side_b_lbl, b_money, b_bets
                other_money, other_bets = a_money, a_bets

            divergence = money_pct - bets_pct
            if divergence >= 10:
                fade = 'boost'
            elif divergence >= -5:
                fade = 'neutral'
            elif other_bets - other_money >= 15:
                fade = 'boost'
            else:
                fade = 'neutral'

            picks_by_key[(gid, surface)] = {
                'game_id': gid,
                'sport': sport_code,
                'game_date': game_date,
                'source': 'scoresandodds',
                'surface': surface,
                'pick_side': pick_side,
                'pick_line': None,   # SO detail page lists line separately;
                                     # left None for now (opener parity with
                                     # earlier OC MVP; enrich in follow-up)
                'odds_american': None,
                'confidence': (f'money {money_pct}% / bets {bets_pct}% '
                               f'(div {divergence:+d}pp)'),
                'raw_text': (f'ScoresAndOdds {surface.upper()}: '
                             f'{side_a_lbl} money {a_money}%/bets {a_bets}% · '
                             f'{side_b_lbl} money {b_money}%/bets {b_bets}%'),
                'source_url': url,
                'fade_flag': fade,
                'money_pct': money_pct,
                'bets_pct': bets_pct,
                'divergence_pp': divergence,
            }

    return list(picks_by_key.values()), 200
