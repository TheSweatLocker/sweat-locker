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
    'Accept-Encoding': 'gzip, deflate, br',
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
