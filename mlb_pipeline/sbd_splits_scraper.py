"""Public betting splits from SBD's trends page -> public_splits_v2.

Andy 2026-09-28, pointing at the NHL public-betting-trends page: "What about
here i see pilsit here". He was right, and it closes the gap flagged the same
day: NHL money flow was single-sourced (fadereport, plus ScoresAndOdds), while
MLB has twelve sources. A lone source has no cross-source agreement behind it,
so it is context rather than signal.

── WHY THIS NEEDS A BROWSER, AND WHY page.content() IS NOT ENOUGH ──
The page renders through web components, and the data lives in SHADOW DOM:

  plain requests        HTTP 200, 406KB -> 0 <table>, 1 team name
  playwright .content() 9s wait          -> identical, still nothing

`content()` does not serialize shadow roots, so the usual render-and-parse
path sees an empty page and would report success with no data — the exact
silent-blank failure this repo keeps hitting. There is no __NEXT_DATA__ blob,
and the API endpoint is not recoverable from the webpack chunks (779/511/311/72
carry zero path-like strings), so a plain HTTP call is not available either.

What works is evaluating JS that walks `el.shadowRoot` recursively. That is
also the fragile part: a component rewrite breaks it silently, which is why
this asserts a row count and exits non-zero rather than writing nothing and
looking green.

── PARSED PER CELL, NEVER PER FLAT STRING ──
A row reads like:

  Sep 29, 5:00pm EDT
  FLA -- 26% 45% -- 85% 88% -- 85% 91%
  CAR -- 74% 55% -- 15% 12% -- 15% 9%

and the tempting parse is "pull every percentage in order". That breaks on the
first game without a posted puck line:

  NYI -- 34% 37% -- -- -- -- 15% 26%

Four percentages, not six, so positional assignment silently shifts the TOTAL
numbers into the SPREAD slot. Cells are read individually so an empty market
stays empty.

── MARKET ORDER IS READ FROM THE HEADER, NOT ASSUMED ──
The table's own THEAD says:

  Matchup | Moneyline BET% $% | Spread BET% $% | Total BET% $%

which is verified at runtime. If that header ever changes, this refuses to
parse rather than mapping moneyline numbers onto the spread.

For the TOTAL market the two team rows carry OVER (first row) and UNDER
(second) rather than the teams themselves — standard for these tables, and
consistent with the observed values (85% of tickets on the over in a 6.5-total
game reads correctly; 85% "on Florida's total" would not mean anything).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Two-letter source code, matching the existing vocabulary in
# public_splits_v2 (oc / so / cz / fr) — and the ToS practice of not naming
# scraped sources in anything user-facing.
SOURCE = 'sd'
BASE = 'https://www.sportsbettingdime.com'

# league path -> our sport code. Only NHL is wired today; the page exists for
# the others and the parser is league-agnostic, so adding one is a map entry
# plus a workflow step.
LEAGUES = {'nhl': 'NHL'}

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/125.0 Safari/537.36')

# The site's abbreviations differ from nhl_game_context's for four clubs.
# Mapped explicitly rather than fuzzily: a wrong team here attaches real
# money-flow numbers to the wrong side, which is worse than no data.
ABBREV_ALIAS = {
    'LA': 'LAK', 'NJ': 'NJD', 'TB': 'TBL', 'SJ': 'SJS',
    'WAS': 'WSH', 'CLS': 'CBJ', 'MON': 'MTL', 'VEG': 'VGK',
}

# Market column order, asserted against the live header before use.
EXPECTED_HEAD = ['moneyline', 'spread', 'total']
MARKET_CODE = {'moneyline': 'ml', 'spread': 'rl', 'total': 'total'}

JS_EXTRACT = r"""
() => {
  const host = document.querySelector('srwc-public-betting-trends');
  if (!host || !host.shadowRoot) return {error: 'component not found'};

  const deepAll = (root, tag, acc, d) => {
    if (d > 8) return acc;
    for (const el of root.children || []) {
      if (el.tagName === tag) acc.push(el);
      deepAll(el, tag, acc, d);
      if (el.shadowRoot) deepAll(el.shadowRoot, tag, acc, d + 1);
    }
    return acc;
  };

  const heads = deepAll(host.shadowRoot, 'THEAD', [], 0)
      .map(h => (h.innerText || h.textContent || '').replace(/\s+/g, ' ').trim())
      .filter(Boolean);

  const games = [];
  for (const body of deepAll(host.shadowRoot, 'TBODY', [], 0)) {
    const logos = [];
    for (const im of body.querySelectorAll('img')) {
      const m = (im.src || '').match(/\/[a-z]+\/([A-Z0-9]{2,4})\.png/);
      if (m) logos.push(m[1]);
    }
    if (logos.length !== 2) continue;
    const when = (body.innerText || '').split('\n')[0].trim();
    const rows = [];
    for (const tr of body.querySelectorAll('tr')) {
      const cells = [];
      for (const td of tr.querySelectorAll('td, th')) {
        // VISIBLE CELLS ONLY. The table renders the moneyline group TWICE —
        // a responsive layout keeps one copy hidden — so the raw cell list
        // reads ["FLA","--","26%","45%","--","26%","45%","--","85%","88%",...]
        // with the first group duplicated. innerText collapses it, cells do
        // not, and a positional parse over the raw list assigns the spread
        // numbers to the total. offsetParent is null for a hidden cell.
        if (td.offsetParent === null && td.getClientRects().length === 0) continue;
        cells.push((td.innerText || td.textContent || '').replace(/\s+/g, ' ').trim());
      }
      if (cells.length) rows.push(cells);
    }
    games.push({logos: logos, when: when, rows: rows});
  }
  return {heads: heads, games: games};
}
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch(league: str, wait_ms: int = 15000) -> dict:
    """Render the trends page and return {heads, games} from the shadow DOM."""
    from playwright.sync_api import sync_playwright
    url = f'{BASE}/{league}/public-betting-trends/'
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(user_agent=UA)
            pg.goto(url, wait_until='domcontentloaded', timeout=60000)
            pg.wait_for_timeout(wait_ms)
            return pg.evaluate(JS_EXTRACT) or {}
        finally:
            b.close()


def check_header(heads: list) -> bool:
    """True when the live header still matches the assumed market order.

    Guards the one assumption that cannot fail loudly on its own: if the site
    reorders its columns, every number still parses and every number is wrong.
    """
    for h in heads or []:
        low = h.lower()
        if all(k in low for k in EXPECTED_HEAD):
            return (low.index('moneyline') < low.index('spread') < low.index('total'))
    return False


def _pcts(cell: str) -> list:
    return [int(x) for x in re.findall(r'(\d{1,3})\s*%', cell or '')]


def parse_game(game: dict) -> list:
    """-> [{team, market, side, bets_pct, money_pct}] for one matchup.

    With hidden cells filtered out the layout is uniform: the team cell, then
    THREE CELLS PER MARKET — a line cell (always "--" on this page) followed by
    BET% and $%. A market with no posted line fills all three with dashes
    rather than collapsing, so fixed-width chunking keeps every market in its
    own slot:

      FLA  |-- 26% 45%|-- 85% 88%|-- 85% 91%|
      NYI  |-- 34% 37%|-- --  -- |-- 15% 26%|   <- no puck line posted

    Chunking is what makes the missing-market case safe. Reading percentages
    as a flat sequence would hand NYI's TOTAL numbers to the SPREAD.
    """
    rows = [r for r in game.get('rows') or [] if len(r) > 3]
    if len(rows) != 2:
        return []
    out = []
    for idx, cells in enumerate(rows):
        body = cells[1:]
        for pos in range(len(EXPECTED_HEAD)):
            grp = body[pos * 3:pos * 3 + 3]
            if len(grp) < 3:
                break
            vals = [_pcts(c) for c in grp[1:]]
            if not all(len(v) == 1 for v in vals):
                continue          # market not posted — leave the hole
            bets, money = vals[0][0], vals[1][0]
            market = MARKET_CODE[EXPECTED_HEAD[pos]]
            if market == 'total':
                # Total rows carry OVER (first) / UNDER (second), not teams.
                side = 'OVER' if idx == 0 else 'UNDER'
            else:
                side = 'AWAY' if idx == 0 else 'HOME'
            out.append({'team': game['logos'][idx], 'market': market,
                        'side': side, 'bets_pct': bets, 'money_pct': money})
    return out


def _norm(a: str) -> str:
    a = (a or '').upper()
    return ABBREV_ALIAS.get(a, a)


def load_ctx(sport: str, days: int = 8) -> dict:
    """(away_abbrev, home_abbrev) -> {game_id, game_date} for upcoming games."""
    tbl = {'NHL': 'nhl_game_context'}.get(sport)
    if not tbl:
        return {}
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, params={
        'select': 'game_id,game_date,home_team_abbrev,away_team_abbrev',
        'game_date': f'gte.{_today_et()}', 'limit': '400'}, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ {tbl} read {r.status_code}: {r.text[:140]}')
        return {}
    out = {}
    for g in r.json():
        a, h = _norm(g.get('away_team_abbrev')), _norm(g.get('home_team_abbrev'))
        if a and h:
            out[(a, h)] = g
    return out


def upsert(rows: list, dry: bool = False) -> int:
    if not rows or dry:
        return 0
    n = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        r = requests.post(
            f'{SB}/rest/v1/public_splits_v2'
            '?on_conflict=game_id,market,side,source,metric,snapshot_ts',
            headers=H_WRITE, json=chunk, timeout=40)
        if r.status_code not in (200, 201, 204):
            print(f'  ⚠ upsert {r.status_code}: {r.text[:180]}')
        else:
            n += len(chunk)
    return n


def run(league: str, dry: bool = False, min_games: int = 1) -> int:
    sport = LEAGUES[league]
    print(f'=== sbd_splits · {league.upper()} · {_today_et()} ===')
    data = fetch(league)
    if data.get('error'):
        print(f'  ✗ {data["error"]}')
        return 1
    games = data.get('games') or []
    print(f'  rendered {len(games)} matchup rows')

    if not check_header(data.get('heads') or []):
        print('  ✗ HEADER CHANGED — refusing to parse.')
        print(f'    expected Moneyline -> Spread -> Total; saw: {data.get("heads")}')
        print('    Every number would still parse and every number would be wrong.')
        return 1

    ctx = load_ctx(sport)
    print(f'  {len(ctx)} upcoming {sport} games in context to join against')

    snap = _now_iso()
    payload, matched, unmatched = [], 0, []
    for g in games:
        parsed = parse_game(g)
        if not parsed:
            continue
        a, h = _norm(g['logos'][0]), _norm(g['logos'][1])
        hit = ctx.get((a, h))
        if not hit:
            unmatched.append(f'{a}@{h}')
            continue
        matched += 1
        for p in parsed:
            for metric in ('bets_pct', 'money_pct'):
                payload.append({
                    'game_id': hit['game_id'], 'sport': sport,
                    'market': p['market'], 'side': p['side'],
                    'source': SOURCE, 'metric': metric,
                    'value': float(p[metric]), 'snapshot_ts': snap,
                    'source_url': f'{BASE}/{league}/public-betting-trends/',
                })
    print(f'  matched {matched}/{len(games)} to our slate · {len(payload)} rows')
    if unmatched:
        print(f'  unmatched (not on our board or abbrev alias missing): '
              f'{unmatched[:8]}')

    if matched < min_games:
        # The whole point of a scraper on shadow DOM: it breaks silently. A
        # zero-row run must be a failure, not a green check.
        print(f'  ✗ only {matched} games matched (need >= {min_games}) — '
              f'treating as failure rather than reporting success with no data')
        return 1

    wrote = upsert(payload, dry)
    print(f'  {"[DRY] would write" if dry else "wrote"} '
          f'{len(payload) if dry else wrote} rows')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--league', default='nhl', choices=sorted(LEAGUES))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--min-games', type=int, default=1,
                    help='fail the run below this many matched games')
    a = ap.parse_args()
    sys.exit(run(a.league, dry=a.dry_run, min_games=a.min_games))


if __name__ == '__main__':
    main()
