"""External consensus fetchers — Batch 5 (2026-07-30).

Currently ships:
  - sbr:  SportsBookReview consensus (per-market bets%). Parses __NEXT_DATA__
          Next.js SSR JSON — clean structured data, no DOM scraping.

Deferred:
  - sao:  ScoresAndOdds — no __NEXT_DATA__ ships; requires Playwright to
          render. Same money-flow signal as oddscrowd, low incremental
          value. Skipped here; revisit if we want a second confirmation
          source for money%.
  - vegasinsider: paywalled (picks hidden behind "Premium" wall).

Both output the same ExternalPick shape as legacy scrapers — no schema
changes downstream.
"""
import json
import re
from typing import Callable

import requests


UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    # NOTE: intentionally NO Accept-Encoding — requests handles gzip/deflate
    # automatically. Advertising 'br' without the brotli package installed
    # gives us Brotli-compressed bytes we can't decode.
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


# ─── SBR ─────────────────────────────────────────────────────────────────
SBR_URL = 'https://www.sportsbookreview.com/betting-odds/mlb-baseball/consensus/'


def fetch_sbr(slate: list, game_date: str,
              find_game_id_fn: Callable) -> tuple[list, int]:
    """SportsBookReview MLB consensus — Next.js SSR path.

    Emits ExternalPick rows per game per market where a meaningful lean
    exists. Consensus JSON shape (per-game):
      {
        awayTeam: {fullName, shortName, ...},
        homeTeam: {...},
        consensus: {
          homeMoneyLinePickPercent, awayMoneyLinePickPercent,
          homeSpreadPickPercent,    awaySpreadPickPercent,
          overPickPercent,          underPickPercent,
        }
      }
    Spread % often 0 (SBR doesn't always publish) — we skip those.

    fade_flag policy:
      >= 75% one side → 'fade' (extreme public — classic fade candidate)
      60-74%           → 'neutral'
      < 60%            → skipped (not a lean)
    """
    try:
        r = requests.get(SBR_URL, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f'  ⚠ SBR fetch failed: {e}')
        return [], 599
    if r.status_code != 200:
        return [], r.status_code

    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        print('  ⚠ SBR: no __NEXT_DATA__ block on page')
        return [], 200
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        print(f'  ⚠ SBR: __NEXT_DATA__ parse failed: {e}')
        return [], 200

    games = _find_sbr_games(data)
    picks = []

    for g in games:
        away = g.get('awayTeam') or {}
        home = g.get('homeTeam') or {}
        cons = g.get('consensus') or {}
        away_hint = (away.get('fullName') or away.get('name') or '').strip()
        home_hint = (home.get('fullName') or home.get('name') or '').strip()
        if not (away_hint and home_hint and cons):
            continue
        gid = find_game_id_fn(slate, home_hint=home_hint, away_hint=away_hint)
        if not gid:
            continue

        # ML
        h_ml = _clean_pct(cons.get('homeMoneyLinePickPercent'))
        a_ml = _clean_pct(cons.get('awayMoneyLinePickPercent'))
        if h_ml is not None and a_ml is not None and (h_ml + a_ml) >= 95:
            picks.extend(_emit_lean(gid, 'ml', h_ml, a_ml, ('HOME', 'AWAY'),
                                    f'SBR ML: away {a_ml}% / home {h_ml}%'))

        # Spread — often 0 on SBR; only emit when both non-zero
        h_sp = _clean_pct(cons.get('homeSpreadPickPercent'))
        a_sp = _clean_pct(cons.get('awaySpreadPickPercent'))
        if h_sp is not None and a_sp is not None and h_sp > 0 and a_sp > 0 \
                and (h_sp + a_sp) >= 95:
            picks.extend(_emit_lean(gid, 'rl', h_sp, a_sp, ('HOME', 'AWAY'),
                                    f'SBR RL: away {a_sp}% / home {h_sp}%'))

        # Total
        over = _clean_pct(cons.get('overPickPercent'))
        under = _clean_pct(cons.get('underPickPercent'))
        if over is not None and under is not None and (over + under) >= 95:
            picks.extend(_emit_lean(gid, 'total', over, under, ('OVER', 'UNDER'),
                                    f'SBR Total: over {over}% / under {under}%'))

    return picks, 200


def _find_sbr_games(node) -> list:
    """Recursively find dict nodes that have awayTeam+homeTeam+consensus."""
    out = []

    def _walk(n):
        if isinstance(n, dict):
            if 'awayTeam' in n and 'homeTeam' in n and 'consensus' in n:
                out.append(n)
            else:
                for v in n.values():
                    _walk(v)
        elif isinstance(n, list):
            for it in n:
                _walk(it)
    _walk(node)
    return out


def _clean_pct(v):
    """SBR percentages are floats like 74.02862985685071 — round to int."""
    try:
        f = float(v)
        if f < 0 or f > 100:
            return None
        return round(f)
    except (TypeError, ValueError):
        return None


def _emit_lean(gid, surface, pct_a, pct_b, sides, raw_text) -> list:
    """Emit an ExternalPick dict if either side is a meaningful lean (>=60%).
    Below 60% we treat as split/no-lean and skip (avoids noise rows)."""
    if pct_a >= 60:
        return [{
            'game_id': gid, 'source': 'sbr', 'surface': surface,
            'pick_side': sides[0], 'confidence': f'{pct_a}%',
            'raw_text': raw_text, 'source_url': SBR_URL,
            'fade_flag': 'fade' if pct_a >= 75 else 'neutral',
        }]
    if pct_b >= 60:
        return [{
            'game_id': gid, 'source': 'sbr', 'surface': surface,
            'pick_side': sides[1], 'confidence': f'{pct_b}%',
            'raw_text': raw_text, 'source_url': SBR_URL,
            'fade_flag': 'fade' if pct_b >= 75 else 'neutral',
        }]
    return []


# ─── Betfirm ─────────────────────────────────────────────────────────────
BETFIRM_URL = 'https://www.betfirm.com/free-baseball-picks/'


def fetch_betfirm(slate: list, game_date: str,
                  find_game_id_fn: Callable) -> tuple[list, int]:
    """Betfirm free MLB picks — 8-12 picks/day, one per handicapper.

    Static HTML, no auth. Each pick sits inside a div with class
    'pick-result'; walk up to nearest ancestor that has an <h3> for
    the capper name + a 'free-pick-game' for teams + a 'free-pick-time'
    for start.

    Play text follows one of two patterns:
        "Play on: <Team> [±1½] <odds> [at <book>]"    (ML or RL)
        "Play on: OVER|UNDER <line> <odds> [at <book>]"  (total)

    Emits ExternalPick per pick. `raw_text` keeps the play text verbatim
    so downstream can show the capper's quote.
    """
    try:
        r = requests.get(BETFIRM_URL, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f'  ⚠ betfirm fetch failed: {e}')
        return [], 599
    if r.status_code != 200:
        return [], r.status_code

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, 'html.parser')
    picks = []

    for pr in soup.find_all(class_='pick-result'):
        # Walk up to a container that has capper name (h3) + game info
        container = pr
        capper = game_str = time_str = None
        for _ in range(6):
            container = container.parent
            if not container:
                break
            h3 = container.find('h3')
            game_el = container.find(class_='free-pick-game')
            time_el = container.find(class_='free-pick-time')
            if h3 and game_el:
                capper = h3.get_text(strip=True)
                game_str = game_el.get_text(' ', strip=True)
                time_str = time_el.get_text(strip=True) if time_el else ''
                break
        if not (capper and game_str):
            continue

        # Filter to MLB only (page also carries other sports if present)
        if 'MLB' not in game_str and 'Baseball' not in game_str:
            continue
        # Filter to today only — "Jul 30 '26" prefix on today's picks
        today_dt = _parse_game_date(game_date)  # yy '26 short form
        if today_dt and today_dt not in (time_str or ''):
            # tonyspicks/betfirm often show future picks — skip anything
            # whose game date isn't today's slate. Best-effort match.
            continue

        # Parse team names from "MLB | Mariners vs Dodgers".
        # Betfirm mixes short codes (BOS, LAD) with nicknames (Red Sox, A's)
        # — normalize both before find_game_id so it matches against
        # slate team names via _team_matches.
        gm = re.search(r'\|\s*(.+?)\s+vs\.?\s+(.+)', game_str)
        if not gm:
            continue
        away_raw = gm.group(1).strip()
        home_raw = gm.group(2).strip()
        away_hint = _mlb_norm(away_raw) or away_raw
        home_hint = _mlb_norm(home_raw) or home_raw
        gid = find_game_id_fn(slate, home_hint=home_hint, away_hint=away_hint)
        if not gid:
            continue

        play_text = pr.get_text(' ', strip=True)
        # Strip "Play on:" prefix
        play_clean = re.sub(r'^.*?Play on\s*:\s*', '', play_text, flags=re.I).strip()

        # Try total first (OVER/UNDER)
        tot = re.search(r'^(OVER|UNDER)\s+([\d.½]+)\s*([+-]?\d{2,4})?', play_clean, re.I)
        if tot:
            side = tot.group(1).upper()
            line_raw = tot.group(2).replace('½', '.5')
            try:
                line = float(line_raw)
            except ValueError:
                line = None
            odds = int(tot.group(3)) if tot.group(3) else None
            picks.append({
                'game_id': gid, 'source': 'betfirm', 'surface': 'total',
                'pick_side': side, 'pick_line': line, 'odds_american': odds,
                'confidence': _betfirm_conf(play_text),
                'raw_text': f'Betfirm ({capper}): {play_clean}',
                'source_url': BETFIRM_URL, 'fade_flag': 'neutral',
            })
            continue

        # ML or RL: "Team ±1½ -160 at Bovada"
        # RL if a spread like "+1½" or "-1½" appears. Team-name char class
        # includes apostrophe for A's / Blue Jays etc.
        rl = re.search(r"([A-Z][A-Za-z .\-'’]+?)\s+([+\-])1[½\.5]+\s*([+\-]?\d{2,4})", play_clean)
        if rl:
            team = rl.group(1).strip()
            sign = rl.group(2)
            odds = int(rl.group(3))
            pick_side = _side_for_team(team, home_hint, away_hint)
            if not pick_side:
                continue
            picks.append({
                'game_id': gid, 'source': 'betfirm', 'surface': 'rl',
                'pick_side': pick_side, 'pick_line': 1.5 if sign == '+' else -1.5,
                'odds_american': odds, 'confidence': _betfirm_conf(play_text),
                'raw_text': f'Betfirm ({capper}): {play_clean}',
                'source_url': BETFIRM_URL, 'fade_flag': 'neutral',
            })
            continue

        # ML — "Team -150 at book"
        ml = re.search(r"([A-Z][A-Za-z .\-'’]+?)\s+([+\-]\d{2,4})(?:\s+at\s+\w+)?", play_clean)
        if ml:
            team = ml.group(1).strip()
            odds = int(ml.group(2))
            pick_side = _side_for_team(team, home_hint, away_hint)
            if not pick_side:
                continue
            picks.append({
                'game_id': gid, 'source': 'betfirm', 'surface': 'ml',
                'pick_side': pick_side, 'odds_american': odds,
                'confidence': _betfirm_conf(play_text),
                'raw_text': f'Betfirm ({capper}): {play_clean}',
                'source_url': BETFIRM_URL, 'fade_flag': 'neutral',
            })

    # Dedupe: if multiple handicappers picked the same side of the same
    # market, collapse to one row. The unique index is
    # (source, game_id, surface, pick_side, game_date) — same key = same
    # row. Combine raw_text to preserve capper names as consensus signal;
    # use median odds so we don't misrepresent as one shop's line.
    dedup: dict[tuple, dict] = {}
    for p in picks:
        key = (p['game_id'], p['surface'], p['pick_side'])
        if key in dedup:
            existing = dedup[key]
            # Combine raw_text (capper A + capper B all agree)
            existing['raw_text'] = existing['raw_text'] + ' | ' + p['raw_text']
            # Median-ish odds — take avg of the two, keep the line
            if p.get('odds_american') is not None and existing.get('odds_american') is not None:
                existing['odds_american'] = round((existing['odds_american'] + p['odds_american']) / 2)
        else:
            dedup[key] = p
    return list(dedup.values()), 200


def _side_for_team(team: str, home_hint: str, away_hint: str) -> str | None:
    """Match capper's team name to home/away. Tolerant of abbreviations
    (BOS ↔ Red Sox), curly apostrophes (A's), and multi-word nicknames."""
    t = _mlb_norm(team)
    h = _mlb_norm(home_hint)
    a = _mlb_norm(away_hint)
    if not t:
        return None
    if t == h or (h and t in h) or (t and h in t):
        return 'HOME'
    if t == a or (a and t in a) or (t and a in t):
        return 'AWAY'
    return None


# Abbrev / nickname → canonical last-name for matching (all lowercase).
# Not every team; only the ones our external scrapers hit as short codes.
_MLB_TEAM_MAP: dict[str, str] = {
    'bos': 'red sox', 'oak': 'athletics', "a's": 'athletics', 'a’s': 'athletics',
    'sea': 'mariners', 'lad': 'dodgers', 'laa': 'angels',
    'nyy': 'yankees', 'nym': 'mets',
    'cws': 'white sox', 'chw': 'white sox', 'chc': 'cubs',
    'stl': 'cardinals', 'kc': 'royals', 'tb': 'rays', 'tbr': 'rays',
    'sf': 'giants', 'sfg': 'giants', 'sd': 'padres', 'sdp': 'padres',
    'mil': 'brewers', 'was': 'nationals', 'wsh': 'nationals',
    'pit': 'pirates', 'cin': 'reds', 'col': 'rockies',
    'min': 'twins', 'tex': 'rangers', 'hou': 'astros',
    'atl': 'braves', 'ari': 'diamondbacks', 'mia': 'marlins',
    'det': 'tigers', 'cle': 'guardians', 'tor': 'blue jays',
    'bal': 'orioles', 'phi': 'phillies', 'nyi': 'islanders',  # nyi ignored (wrong sport)
}


def _mlb_norm(name: str) -> str:
    """Canonicalize an MLB team name for matching. Applies abbrev map +
    strips city prefix (e.g. 'Los Angeles Dodgers' → 'dodgers')."""
    if not name:
        return ''
    n = name.lower().strip().replace('’', "'").strip()  # curly → straight apostrophe
    if n in _MLB_TEAM_MAP:
        return _MLB_TEAM_MAP[n]
    # Drop leading city — take last 1-2 words (Red Sox / White Sox / Blue Jays)
    words = n.split()
    if len(words) >= 2 and (words[-2] + ' ' + words[-1]) in {'red sox', 'white sox', 'blue jays'}:
        return words[-2] + ' ' + words[-1]
    return words[-1] if words else ''


_STAR_RE = re.compile(r'(\d+)\s*\*')
def _betfirm_conf(text: str) -> str | None:
    """Extract confidence tier — '1*', '3*', '7* Play' etc."""
    m = _STAR_RE.search(text)
    return f'{m.group(1)}*' if m else None


def _parse_game_date(iso_date: str) -> str | None:
    """Convert '2026-07-30' → 'Jul 30' (matches Betfirm's date prefix)."""
    from datetime import datetime
    try:
        return datetime.strptime(iso_date, '%Y-%m-%d').strftime('%b %-d')
    except Exception:
        try:
            return datetime.strptime(iso_date, '%Y-%m-%d').strftime('%b %#d')  # windows
        except Exception:
            return None


# ─── Tony's Picks ────────────────────────────────────────────────────────
TONYS_URL = 'https://www.tonyspicks.com/category/freepicks/free-mlb-picks/'


def fetch_tonyspicks(slate: list, game_date: str,
                     find_game_id_fn: Callable) -> tuple[list, int]:
    """Tony's Picks free MLB category — one pick per game/day from Ramon Scott.

    Category page = WordPress index of articles. Each article's TITLE
    encodes enough for surface + side classification. Line + odds live
    in article prose; skipped for MVP to avoid 12 extra HTTP fetches.

    Title format:
      "<Team1> vs <Team2> Betting Odds Pick, <Month Day>: <Capper> <action> …"

    Action → market map:
      "Lays the First-Five" / "First-Five Run Line"  → skip (F5 not tracked)
      "Lays the Run"                                  → RL on named team
      "Backs the Over"  / "Rides the Over"            → total/OVER
      "Backs the Under" / "Rides the Under"           → total/UNDER
      "Trusts the", "Takes the", "Rolls With",
      "Backs" (no Over/Under)                         → ML on named team

    URL slug encodes away-home order: `<away>-vs-<home>-<capper>-…-<date>`.
    """
    try:
        r = requests.get(TONYS_URL, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f'  ⚠ tonyspicks fetch failed: {e}')
        return [], 599
    if r.status_code != 200:
        return [], r.status_code

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, 'html.parser')

    # Match today's date in URL slug (e.g. "7-30-2026")
    from datetime import datetime
    try:
        dt = datetime.strptime(game_date, '%Y-%m-%d')
        slug_date = f'{dt.month}-{dt.day}-{dt.year}'   # 7-30-2026
        alt_slug  = f'/{dt.year}/{dt.month:02d}/{dt.day:02d}/'
    except Exception:
        return [], 200

    picks = []
    for a in soup.find_all('article'):
        h = a.find(['h1', 'h2', 'h3'])
        link = h.find('a') if h else None
        if not link:
            continue
        url = link.get('href', '')
        title = link.get_text(' ', strip=True)
        if slug_date not in url and alt_slug not in url:
            continue

        # Extract away/home from URL slug (before "-<capper-slug>")
        slug = url.rstrip('/').split('/')[-1]
        # e.g. "seattle-mariners-vs-los-angeles-dodgers-ramon-scott-betting-odds-pick-7-30-2026"
        m = re.search(r'^(.+?)-vs-(.+?)-(?:ramon-scott|betting-odds|pick|\d{1,2}-\d{1,2}-\d{4})', slug)
        if not m:
            # Prop-picks articles use different slug — skip for now
            continue
        away_slug = m.group(1).replace('-', ' ')
        home_slug = m.group(2).replace('-', ' ')
        away_hint = _mlb_norm(away_slug) or away_slug
        home_hint = _mlb_norm(home_slug) or home_slug
        gid = find_game_id_fn(slate, home_hint=home_hint, away_hint=away_hint)
        if not gid:
            continue

        # Post-colon action phrase — everything after ":  Ramon Scott "
        m2 = re.search(r':\s*Ramon Scott\s+(.+)$', title)
        if not m2:
            continue
        action = m2.group(1).strip()
        surface = pick_side = None

        low = action.lower()
        # Skip First-Five (not a market we track)
        if 'first-five' in low or 'first five' in low or ' f5 ' in low:
            continue
        if 'the under' in low:
            surface, pick_side = 'total', 'UNDER'
        elif 'the over' in low:
            surface, pick_side = 'total', 'OVER'
        elif 'run line' in low or 'lays the run' in low:
            surface = 'rl'
            # Team named in action or slug — check both
            pick_side = _infer_side_from_action(action, home_slug, away_slug)
        else:
            # ML — team name mentioned after action verb
            surface = 'ml'
            pick_side = _infer_side_from_action(action, home_slug, away_slug)

        if not (surface and pick_side):
            continue
        picks.append({
            'game_id': gid, 'source': 'tonyspicks', 'surface': surface,
            'pick_side': pick_side,
            'raw_text': f"Tony's Picks (Ramon Scott): {action}",
            'source_url': url, 'fade_flag': 'neutral',
        })

    return picks, 200


def _infer_side_from_action(action: str, home_slug: str, away_slug: str) -> str | None:
    """Given an action phrase like 'Trusts the Nats' Hot Road Offense',
    figure out which side is being backed. Match on nickname keywords
    from the URL slug (home/away)."""
    low = action.lower()
    home_words = home_slug.lower().split()
    away_words = away_slug.lower().split()
    # Common nickname aliases picked up in prose
    nicknames = {
        'nats': 'washington nationals', 'sox': None,  # ambiguous
        'fish': 'miami marlins', 'a\'s': 'athletics',
        'jays': 'toronto blue jays', 'stros': 'houston astros',
    }
    # Match by last-word (nickname) — check home first, then away
    for hw in home_words:
        if hw in low and len(hw) > 2:
            return 'HOME'
    for aw in away_words:
        if aw in low and len(aw) > 2:
            return 'AWAY'
    # Nickname fallback
    for nick, full in nicknames.items():
        if nick in low and full:
            if full in ' '.join(home_words):
                return 'HOME'
            if full in ' '.join(away_words):
                return 'AWAY'
    return None
