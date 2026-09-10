"""Cleatz.com full-slate public splits scraper (2026-08-15 pm).

Third public-splits source alongside OddsCrowd + Fadereport. Cleatz
publishes full-slate splits for every game across ML / Total / Run Line
with BOTH handle% and bets% per side. No curation — every game covered.

DATA SOURCE
  https://cleatz.com/public-betting/mlb/

  Parses HTML sections (~15 games/day) into per-market rows.

PARSER STRATEGY
  Split page at `<div class="ccsp-game-head"` — yields one block per game.
  For each block, extract 3 markets (Moneyline, Total, Run Line).
  Each market has 2 sides with Bets % and Handle % per side.
  Sharp side = the side with higher handle% relative to bets% (money
  divergence). Store both bets% + handle% per side plus derived sharp
  side for downstream cross-source classifier.

TABLE
  cleatz_signals (created by migration 20260815b_cleatz_signals.sql)
  Same shape as fadereport_signals so cross-source join is uniform.

CLI
  python cleatz_scraper.py                     # MLB today
  python cleatz_scraper.py --sport MLB
  python cleatz_scraper.py --dry-run
"""
from __future__ import annotations
import argparse, os, re, sys, json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from html import unescape

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SPORT_URL = {
    'MLB':   'https://cleatz.com/public-betting/mlb/',
    'NFL':   'https://cleatz.com/public-betting/nfl/',
    'NCAAF': 'https://cleatz.com/public-betting/college-football/',  # CFB — Cleatz URL uses full name
    # 2026-08-23 Phase 3 — NBA + NCAAB re-enabled. Both URLs return HTTP 200
    # with the page template; game blocks appear only in-season. Running
    # them off-season is a safe no-op (scraper finds 0 games, writes 0 rows).
    'NBA':   'https://cleatz.com/public-betting/nba/',
    'NCAAB': 'https://cleatz.com/public-betting/college-basketball/',
    # 'NHL':   HTTP 404 (Cleatz doesn't cover hockey as of 2026-08-23)
}

SPORT_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA':   'nba_game_context',
    'NCAAB': 'ncaab_game_context',
}

# Common team-code → full-team-name map for fuzzy join
TEAM_CODE = {
    'BAL': 'Baltimore Orioles', 'TB': 'Tampa Bay Rays', 'NYY': 'New York Yankees',
    'TOR': 'Toronto Blue Jays', 'BOS': 'Boston Red Sox',
    'CHI White Sox': 'Chicago White Sox', 'CHI Cubs': 'Chicago Cubs',
    'CHI': 'Chicago', 'CLE': 'Cleveland Guardians', 'DET': 'Detroit Tigers',
    'HOU': 'Houston Astros', 'SEA': 'Seattle Mariners', 'LA Angels': 'Los Angeles Angels',
    'LA Dodgers': 'Los Angeles Dodgers', 'MIN': 'Minnesota Twins',
    'KC': 'Kansas City Royals', 'ATL': 'Atlanta Braves', 'ARI': 'Arizona Diamondbacks',
    'PHI': 'Philadelphia Phillies', 'PIT': 'Pittsburgh Pirates',
    'STL': 'St. Louis Cardinals', 'MIA': 'Miami Marlins', 'CIN': 'Cincinnati Reds',
    'MIL': 'Milwaukee Brewers', 'NY Yankees': 'New York Yankees',
    'NY Mets': 'New York Mets', 'WAS': 'Washington Nationals',
    'SF': 'San Francisco Giants', 'SD': 'San Diego Padres', 'COL': 'Colorado Rockies',
    'TEX': 'Texas Rangers', 'Athletics': 'Athletics',
}


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _load_todays_games(sport: str, snap: date) -> dict:
    """Return {(away_last, home_last): game_id} lookup.

    2026-09-10: widen date filter for football sports — cleatz lists every
    game on this week's slate (10-day horizon), not just kicks-off-today.
    Prior narrow filter (=snap only) meant only 1 NFL game (TNF) landed in
    the lookup and Sunday's 14 games returned 'no match' → 0 signals written.
    """
    tbl = SPORT_TABLE.get(sport)
    if not tbl: return {}
    if sport in ('NFL', 'NCAAF', 'NBA', 'NCAAB'):
        end = (snap + timedelta(days=10)).isoformat()
        date_filter = f'and=(game_date.gte.{snap.isoformat()},game_date.lte.{end})'
    else:
        date_filter = f'game_date=eq.{snap.isoformat()}'
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?select=game_id,away_team,home_team&{date_filter}',
        headers=H_READ, timeout=15)
    if r.status_code != 200: return {}
    lookup = {}
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        gid = row.get('game_id')
        away = (row.get('away_team') or '').lower()
        home = (row.get('home_team') or '').lower()
        a_last = away.split()[-1] if away else ''
        h_last = home.split()[-1] if home else ''
        lookup[(a_last, h_last)] = gid
    return lookup


def _resolve_gid(away_short: str, home_short: str, lookup: dict) -> Optional[str]:
    """Cleatz uses codes like 'BAL Orioles', 'TB Rays', 'NY Yankees'.
    Match on the LAST word (mascot name) which is unique per team."""
    a = (away_short or '').lower().split()
    h = (home_short or '').lower().split()
    # Handle 'Red Sox' / 'White Sox' / 'Blue Jays' — take last 2 words
    a_key = a[-1] if a else ''
    h_key = h[-1] if h else ''
    # Two-word mascots
    if len(a) >= 2 and a[-2] in ('red','white','blue'): a_key = a[-1]  # 'sox'/'jays'
    if len(h) >= 2 and h[-2] in ('red','white','blue'): h_key = h[-1]
    # Direct match
    if (a_key, h_key) in lookup: return lookup[(a_key, h_key)]
    # Fuzzy - substring match
    for (ak, hk), gid in lookup.items():
        if a_key and a_key in ak and h_key and h_key in hk:
            return gid
    return None


def _clean_html(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' | ', s)
    s = re.sub(r'\|\s*\|', '|', s)
    s = re.sub(r'\s+', ' ', s)
    return unescape(s.strip())


def _parse_market(section_text: str, market_name: str, next_market_names: list) -> dict:
    """Extract per-side bets%/handle% for a market from cleaned section text.

    2026-09-10 REWRITE: cleatz removed per-side "Bets" / "Handle" inline labels
    from their HTML — those words now appear once as column headers only.
    Prior regex `Bets[\\s\\|]+(\\d+)%[\\s\\|]+Handle[\\s\\|]+(\\d+)%` matched 0
    per game after the change, silently dropping the entire NFL slate.

    New strategy: locate the market slice (between market header and next
    market header), enumerate ALL N% occurrences within, and take them in
    pairs — first two = side A (bets, handle), next two = side B. Walk
    backward from each pair to grab the side name from the nearest
    "| <text> |" pipe-chunk that isn't obvious junk (market label / odds).
    Confirmed correct against SF 49ers @ LA Rams and 14 other NFL games on
    2026-09-10.
    """
    start = section_text.find(f'| {market_name} |')
    if start < 0:
        start = section_text.find(f'|{market_name} ')
        if start < 0:
            return {}
    end = len(section_text)
    for nm in next_market_names:
        p = section_text.find(f'| {nm} |', start + 5)
        if p > 0 and p < end: end = p
    slice_ = section_text[start:end]

    # Find all "N%" occurrences in order; expect exactly 4 (2 sides × 2 pcts).
    pct_matches = list(re.finditer(r'(\d+)%', slice_))
    sides = []
    labels = {'Moneyline','Total','Run Line','Spread','Bets','Handle','▼','▲'}
    for i in range(0, len(pct_matches), 2):
        if i + 1 >= len(pct_matches): break
        bets = int(pct_matches[i].group(1))
        handle = int(pct_matches[i + 1].group(1))
        # Walk back from the first % of this pair to find the side name.
        back = slice_[:pct_matches[i].start()]
        chunks = re.findall(r'\|\s*([^|]{2,40}?)\s*\|', back)
        # Drop obvious junk (market names, odds numbers, divergence markers, %)
        odds_re = re.compile(r'^[+\-]?\d')
        good = [c.strip() for c in chunks
                if c.strip() not in labels
                and not odds_re.match(c.strip())
                and not c.strip().endswith('%')
                and not c.strip().startswith('+')]  # divergence "+29"
        if not good: continue
        side_name = good[-1]
        # Also try to extract the odds token — nearest token AFTER side name
        # that looks like odds ("-118" / "+164" / "o8.5" / "u48.5").
        after_name_idx = back.rfind(side_name)
        odds_val = None
        if after_name_idx >= 0:
            after = slice_[after_name_idx + len(side_name):pct_matches[i].start()]
            om = re.search(r'\|\s*([+\-]\d{2,4}|[+\-]?\d+\.\d+|[ou]\d[\d\.]*)\s*\|', after)
            if om: odds_val = om.group(1)
        sides.append({'side_name': side_name, 'odds': odds_val,
                      'bets_pct': bets, 'handle_pct': handle})
        if len(sides) == 2: break
    return {'market': market_name, 'sides': sides}


def _norm_side(side_name: str, market: str, away: str, home: str) -> Optional[str]:
    """Convert 'BAL Orioles' → 'away'/'home'; 'Over 7.5' → 'over'."""
    if market == 'total':
        n = side_name.lower()
        if 'over' in n: return 'over'
        if 'under' in n: return 'under'
        return None
    n = side_name.lower()
    # RL sides have '+1.5' / '-1.5' suffix
    n = re.sub(r'[+\-]\d+(\.\d+)?$', '', n).strip()
    a_key = away.lower().split()[-1] if away else ''
    h_key = home.lower().split()[-1] if home else ''
    if a_key and a_key in n: return 'away'
    if h_key and h_key in n: return 'home'
    return None


def scrape_sport(sport: str, dry_run: bool = False) -> int:
    url = SPORT_URL.get(sport)
    if not url:
        print(f'  ✗ unknown sport {sport}'); return 0

    snap = _et_today()
    lookup = _load_todays_games(sport, snap)
    print(f'  · loaded {len(lookup)} game_context rows for {snap}')

    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (SweatLocker)'}, timeout=25)
    except Exception as e:
        print(f'  ✗ fetch fail: {e}'); return 0
    if r.status_code != 200:
        print(f'  ✗ HTTP {r.status_code}'); return 0
    txt = r.text

    # Split at game-head divs to get individual game sections
    sections = re.split(r'<div class="ccsp-game-head"', txt)
    print(f'  · {len(sections) - 1} game sections found')

    all_rows = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for section_html in sections[1:]:
        # Bound section — until next ccsp-game-head or end
        clean = _clean_html(section_html[:10000])  # first 10k chars per section is enough

        # Team names — first two ALL-CAPS-prefixed team strings in cleaned text
        # Pattern: "| BAL Orioles | @ | TB Rays |" or "| SF 49ers | @ | LA Rams |"
        # 2026-09-10 · added digit support in mascot ([A-Za-z0-9\.]+ instead of
        # [A-Za-z\.]+) — the ONLY digit-starting NFL mascot is 49ers. Without
        # this SF games were silently dropped from every scrape.
        team_m = re.search(r'\|\s*([A-Z][A-Z]?\s*[A-Za-z0-9\.]+(?:\s+[A-Z][a-z]+)?)\s*\|\s*@\s*\|\s*([A-Z][A-Z]?\s*[A-Za-z0-9\.]+(?:\s+[A-Z][a-z]+)?)\s*\|',
                            clean)
        if not team_m:
            # Try loose pattern
            team_m = re.search(r'\|\s*([A-Z][A-Za-z0-9\s\.]+?)\s*\|\s*@\s*\|\s*([A-Z][A-Za-z0-9\s\.]+?)\s*\|', clean)
        if not team_m: continue
        away_short = team_m.group(1).strip()
        home_short = team_m.group(2).strip()
        gid = _resolve_gid(away_short, home_short, lookup)

        # Parse each market. 2026-09-10: cleatz labels the football spread
        # market as "Spread"; MLB uses "Run Line". Both map to the same
        # normalized market key 'rl' downstream so the aggregator lens is
        # consistent regardless of sport terminology.
        markets = {'Moneyline': 'ml', 'Total': 'total', 'Run Line': 'rl', 'Spread': 'rl'}
        if sport in ('NFL', 'NCAAF', 'NBA', 'NCAAB'):
            market_order = ['Spread', 'Total', 'Moneyline']
        else:
            market_order = ['Moneyline', 'Total', 'Run Line']
        for i, mkt_name in enumerate(market_order):
            next_names = market_order[i+1:]
            parsed = _parse_market(clean, mkt_name, next_names)
            if not parsed.get('sides'): continue
            mkt_key = markets[mkt_name]

            # Determine sharp side per Cleatz convention (handle > bets by 5+)
            sharp_side_norm = None
            sharp_side_raw = None
            sharp_bets_pct = None; sharp_handle_pct = None
            other_bets_pct = None; other_handle_pct = None
            best_diff = 0
            for s in parsed['sides']:
                diff = s['handle_pct'] - s['bets_pct']
                if diff > best_diff:
                    best_diff = diff
                    sharp_side_raw = s['side_name']
                    sharp_side_norm = _norm_side(s['side_name'], mkt_key, away_short, home_short)
                    sharp_bets_pct = s['bets_pct']
                    sharp_handle_pct = s['handle_pct']
            if sharp_side_norm and len(parsed['sides']) == 2:
                for s in parsed['sides']:
                    if s['side_name'] != sharp_side_raw:
                        other_bets_pct = s['bets_pct']
                        other_handle_pct = s['handle_pct']

            # If no side beat other by 5+, still record the majority-handle side (for consistency)
            if sharp_side_norm is None and parsed['sides']:
                maj = max(parsed['sides'], key=lambda x: x['handle_pct'])
                sharp_side_raw = maj['side_name']
                sharp_side_norm = _norm_side(maj['side_name'], mkt_key, away_short, home_short)
                sharp_bets_pct = maj['bets_pct']
                sharp_handle_pct = maj['handle_pct']
                if len(parsed['sides']) == 2:
                    other = min(parsed['sides'], key=lambda x: x['handle_pct'])
                    other_bets_pct = other['bets_pct']
                    other_handle_pct = other['handle_pct']

            all_rows.append({
                'snapshot_date': snap.isoformat(),
                'sport': sport,
                'game_id': gid,
                'away_team': away_short[:100],
                'home_team': home_short[:100],
                'market': mkt_key,
                'sharp_side_raw': (sharp_side_raw or '')[:100],
                'sharp_side_norm': sharp_side_norm,
                'sharp_bets_pct': sharp_bets_pct,
                'sharp_handle_pct': sharp_handle_pct,
                'other_bets_pct': other_bets_pct,
                'other_handle_pct': other_handle_pct,
                'divergence': (sharp_handle_pct - sharp_bets_pct) if (sharp_handle_pct is not None and sharp_bets_pct is not None) else None,
                'raw_snapshot': {'sides': parsed['sides']},
                'fetched_at': now_iso,
            })

    print(f'  parsed {len(all_rows)} market rows')
    matched = sum(1 for r in all_rows if r.get('game_id'))
    print(f'  matched to game_context: {matched}/{len(all_rows)}')

    if dry_run:
        for r in all_rows:
            print(f"    {r['away_team']:<20} @ {r['home_team']:<20} · {r['market']:<5} · "
                  f"sharp={r['sharp_side_norm']:<5} ({r['sharp_side_raw'][:20]}) · "
                  f"handle {r['sharp_handle_pct']}/{r['other_handle_pct']} · "
                  f"bets {r['sharp_bets_pct']}/{r['other_bets_pct']} · gid={r['game_id']}")
        return len(all_rows)

    written = 0
    for i in range(0, len(all_rows), 100):
        chunk = all_rows[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/cleatz_signals?on_conflict=snapshot_date,sport,away_team,home_team,market',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} signals')
    return written


def run(sports: list, dry_run: bool = False):
    total = 0
    for sport in sports:
        print(f'\n=== cleatz_scraper · {sport} ===')
        try:
            n = scrape_sport(sport, dry_run=dry_run)
            total += n
        except Exception as e:
            print(f'  ✗ {sport} failed: {e}')
    print(f'\n✓ done · {total} signals total')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORT_URL.keys()))
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = [args.sport] if args.sport else list(SPORT_URL.keys())
    run(sports, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
