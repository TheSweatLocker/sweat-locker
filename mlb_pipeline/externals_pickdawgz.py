"""PickDawgz free picks — shared fetcher for every sport.

Andy 2026-09-21: "Pickdawgz is across all sports so lets wire that up."

It was wired for MLB only (250 picks, 58.5%). The NFL puller registered
`fetch_pickdawgz` but the function was a STUB returning `([], 200)` — a
silent success with zero picks, so the source looked present and
contributed nothing. Same for fetch_vsin and fetch_bettingpros on NFL.

Rather than copy the 110-line MLB parser per sport (which drifts), this
generalises it. Verified live 2026-09-21 — every sport's landing page is
up and carries date-stamped article links:

    mlb-picks                 26 article links
    nfl-picks                 26
    college-football-picks    17     (ncaaf-picks 404s — do not use)
    nba-picks                 26
    college-basketball-picks  17
    nhl-picks                 26

Article shape (checked on a real NFL page):
    title  "New York Giants vs Los Angeles Rams Prediction 9/21/2026 ..."
    body   "... Chris Ruffolo's Pick: Los Angeles Rams -7 Need More? ..."

The sign-off pick carries three shapes, so all three are parsed:
    "Los Angeles Rams -7"        -> rl    (NFL/NCAAF/NBA/NCAAB spread)
    "Boston Red Sox ML"          -> ml
    "Over 47.5"                  -> total
"""
from __future__ import annotations

import re
import time

import requests

UA = {'User-Agent': 'Mozilla/5.0 (Sweat Locker aggregator)'}
BASE = 'https://www.pickdawgz.com'

# ncaaf-picks 404s; college-football-picks is the real slug.
SPORT_SLUG = {
    'MLB': 'mlb-picks',
    'NFL': 'nfl-picks',
    'NCAAF': 'college-football-picks',
    'NBA': 'nba-picks',
    'NCAAB': 'college-basketball-picks',
    'NHL': 'nhl-picks',
}

_MATCHUP_RE = re.compile(
    r"([A-Z][A-Za-z .&'\-]+?)\s+(?:vs\.?|at)\s+([A-Z][A-Za-z .&'\-]+?)"
    r"\s+(?:Prediction|Pick|Odds|Today)", re.I)

# The author sign-off. Straight and curly apostrophes both appear.
_PICK_RE = re.compile(
    r"[A-Za-z]+[’']s\s+Pick\s*:\s*(.{3,80}?)"
    r"\s+(?:Need More|Get Premium|Hot Cappers|Buy|Add to)", re.I | re.S)

_TOTAL_RE = re.compile(r'^(Over|Under)\s+([\d.]+)', re.I)
_SPREAD_RE = re.compile(r'^(.+?)\s+([+-]\d+(?:\.\d+)?)\s*$')
_ML_RE = re.compile(r'^(.+?)\s+(?:ML|Moneyline)\s*$', re.I)
_ODDS_RE = re.compile(r'\(([+-]\d{2,4})\)')


def _article_urls(landing_html: str, game_date: str) -> set:
    """Links whose slug or text carries this game_date.

    Slug form is M-D-YYYY ('prediction-9-21-2026'); headline form is
    M/D/YYYY. Neither is zero-padded, so build both from the ISO date.
    """
    from bs4 import BeautifulSoup
    y, m, d = game_date.split('-')
    slug = f'{int(m)}-{int(d)}-{y}'
    head = f'{int(m)}/{int(d)}/{y}'
    soup = BeautifulSoup(landing_html, 'html.parser')
    out = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(' ', strip=True)
        if slug not in href and head not in text and slug not in text:
            continue
        if 'prediction' not in href.lower() and 'pick' not in href.lower():
            continue
        if href.startswith('/'):
            href = BASE + href
        if href.startswith('http'):
            out.add(href)
    return out


def _side_for(team: str, home_hint: str, away_hint: str):
    """Which side a team string names, or None when it is ambiguous.

    Tiers, strongest first. A "longest name wins" shortcut is WRONG here
    and fails the case that has already cost a real grade:
    _side_for('Iowa', home='Iowa', away='Northern Iowa') — 'iowa' is an
    exact match for home AND a substring of away, and picking the longer
    name hands it to Northern Iowa. Exactness has to outrank length.

    Anything still ambiguous after all four tiers returns None. A pick we
    cannot attribute is dropped, never guessed onto a side.
    """
    norm = lambda s: re.sub(r'[^a-z0-9 ]', '', (s or '').lower()).strip()
    t, h, a = norm(team), norm(home_hint), norm(away_hint)
    if not t:
        return None

    # 1. exact
    if t == h and t != a:
        return 'HOME'
    if t == a and t != h:
        return 'AWAY'

    # 2. pick text CONTAINS a full team name ("los angeles rams" in text)
    ch, ca = bool(h) and h in t, bool(a) and a in t
    if ch and not ca:
        return 'HOME'
    if ca and not ch:
        return 'AWAY'

    # 3. pick text is a SUBSET of exactly one team name ("rams" -> "los
    #    angeles rams"). Both -> ambiguous ("new york" vs Giants/Jets).
    sh, sa = bool(h) and t in h, bool(a) and t in a
    if sh and not sa:
        return 'HOME'
    if sa and not sh:
        return 'AWAY'

    # 4. nickname (last word) appears as a whole word
    hw = h.split()[-1] if h else ''
    aw = a.split()[-1] if a else ''
    nh = bool(hw) and re.search(rf'\b{re.escape(hw)}\b', t) is not None
    na = bool(aw) and re.search(rf'\b{re.escape(aw)}\b', t) is not None
    if nh and not na:
        return 'HOME'
    if na and not nh:
        return 'AWAY'
    return None


def parse_pick(raw: str, home_hint: str, away_hint: str):
    """-> (surface, pick_side, pick_line, odds) or None.

    Pure and side-effect free so it can be unit-tested without the network.
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    odds_m = _ODDS_RE.search(raw)
    odds = int(odds_m.group(1)) if odds_m else None
    body = _ODDS_RE.sub('', raw).strip(' .')

    m = _TOTAL_RE.match(body)
    if m:
        return 'total', m.group(1).upper(), float(m.group(2)), odds

    m = _ML_RE.match(body)
    if m:
        side = _side_for(m.group(1), home_hint, away_hint)
        return ('ml', side, None, odds) if side else None

    m = _SPREAD_RE.match(body)
    if m:
        side = _side_for(m.group(1), home_hint, away_hint)
        return ('rl', side, float(m.group(2)), odds) if side else None

    # Bare team name with no market marker = moneyline.
    side = _side_for(body, home_hint, away_hint)
    return ('ml', side, None, odds) if side else None


def fetch_pickdawgz_generic(sport: str, game_date: str, slate: list,
                            find_game_id_fn, make_pick_fn,
                            max_articles: int = 30) -> tuple:
    """Return (picks, http_status).

    find_game_id_fn(slate, home_hint=..., away_hint=...) -> game_id | None
    make_pick_fn(**kwargs) -> whatever the caller's ExternalPick is
    """
    slug = SPORT_SLUG.get(str(sport).upper())
    if not slug:
        return [], 200
    try:
        landing = requests.get(f'{BASE}/{slug}', headers=UA, timeout=15)
    except requests.RequestException as e:
        print(f'  pickdawgz {sport}: landing failed {type(e).__name__}: {e}')
        return [], 0
    if landing.status_code != 200:
        return [], landing.status_code

    # 2026-09-25: was `_article_urls(landing.text, game_date)` — a SINGLE date.
    # That is right for a daily sport (every MLB game shares one date) and
    # structurally wrong for a weekly one. NFL's slate spans Thu/Sun/Mon, and
    # PickDawgz dates each article to its own game day, so filtering to the
    # anchor date could only ever match the games played on that one day: on a
    # Thursday, the single TNF game. NFL collected 15 picks all season against
    # MLB's 3,008 for exactly this reason. Andy: "pickdawgz has every game
    # usually posted by Fridays" — they do, we were only ever looking at one day
    # of them.
    #
    # Derive the dates from the slate instead of guessing a horizon; the slate
    # is already the ±7d window the caller wants covered.
    dates = sorted({r.get('game_date') for r in (slate or []) if r.get('game_date')})
    if not dates:
        dates = [game_date]
    urls = set()
    for d in dates[:10]:
        urls |= _article_urls(landing.text, d)
    if not urls:
        print(f'  pickdawgz {sport}: no articles matched {len(dates)} slate '
              f'date(s) {dates[0]}..{dates[-1]}')
        return [], 200

    # Each pick must carry ITS OWN game date. Stamping every pick with the
    # anchor date put Sunday games on Thursday, which misfiles them for grading
    # and for any per-date reporting.
    gid_date = {r.get('game_id'): r.get('game_date')
                for r in (slate or []) if r.get('game_id')}

    from bs4 import BeautifulSoup
    picks = []
    for url in list(urls)[:max_articles]:
        try:
            art = requests.get(url, headers=UA, timeout=12)
            if art.status_code != 200:
                continue
            s = BeautifulSoup(art.text, 'html.parser')
            head = s.find('h1') or s.find('title')
            title = head.get_text(' ', strip=True) if head else ''
            mm = _MATCHUP_RE.search(title)
            if not mm:
                continue
            away_hint, home_hint = mm.group(1).strip(), mm.group(2).strip()
            gid = find_game_id_fn(slate, home_hint=home_hint, away_hint=away_hint)
            if not gid:
                continue
            body = s.get_text(' ', strip=True)
            pm = list(_PICK_RE.finditer(body[:25000]))
            if not pm:
                continue
            parsed = parse_pick(pm[-1].group(1), home_hint, away_hint)
            if not parsed:
                continue
            surface, side, line, odds = parsed
            picks.append(make_pick_fn(
                game_id=gid, sport=str(sport).upper(),
                game_date=gid_date.get(gid) or game_date,
                source='pickdawgz', surface=surface, pick_side=side,
                pick_line=line, odds_american=odds,
                raw_text=f'PickDawgz: {pm[-1].group(1).strip()[:120]}',
                source_url=url,
            ))
            time.sleep(0.4)      # be polite
        except Exception:
            continue
    return picks, 200
