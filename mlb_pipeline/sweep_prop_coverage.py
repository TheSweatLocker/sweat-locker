"""Prop coverage sweeper (2026-07-31 · Tier 3).

Guarantees every pitcher prop the sportsbook publishes gets INTO
mlb_pipeline_props. Fills the gap where generate_props.py silently
skips (missing L5 data, sample-size gate, book-attach failure — all
silent bail conditions in the legacy 4218-line hand-tuned scorer).

Pattern:
  1. Pull all pitcher prop markets from The Odds API (us,us2 regions)
  2. For each (pitcher, prop_type, direction, prop_line) combo NOT yet
     in today's mlb_pipeline_props, insert a "COVERAGE" tier stub row
     with book odds + line + basic pitcher context pulled from
     mlb_game_context.
  3. Downstream generate_prop_jerry_synthesis has an edge gate — only
     rates props with a real signal (legacy scored it OR meaningful
     projection delta), so coverage in the props table is complete
     but Jerry-take volume stays manageable (~30–60/day).

Sport-universal: MLB today. Adding NBA/NFL = add market list +
table registry entry + team-context lookup.

Runs AFTER generate_props.py, BEFORE apply_prop_refit +
generate_prop_jerry_synthesis.

Usage:
    python sweep_prop_coverage.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

MARKET_MAP = {
    'pitcher_strikeouts':  'ks',
    'pitcher_earned_runs': 'er',
    'pitcher_walks':       'bb',
    'pitcher_hits_allowed':'ha',
    'pitcher_outs':        'outs',
}
PREFERRED_BOOKS = ['hardrockbet', 'hardrockbet_oh', 'draftkings', 'betmgm',
                   'fanduel', 'bovada', 'espnbet', 'betrivers']

# Per-prop-type: which context projection key aligns with the line
PROJECTION_KEY = {
    'ks': 'projected_ks',
    'er': 'projected_er',
    'bb': 'projected_bb',
    'ha': 'projected_hits',
    'outs': 'projected_outs',
}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch_upcoming_events() -> list:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = requests.get('https://api.the-odds-api.com/v4/sports/baseball_mlb/events',
                     params={'apiKey': ODDS_API_KEY, 'commenceTimeFrom': now}, timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_pitcher_props(event_id: str) -> dict:
    """Returns {(pitcher_lc, prop_type): {'line': .., 'over_odds': .., 'under_odds': .., 'book': .., 'display': ..}}
    Bundles over+under from the same preferred book so both odds columns populate."""
    r = requests.get(
        f'https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds',
        params={'apiKey': ODDS_API_KEY, 'regions': 'us,us2',
                'markets': ','.join(MARKET_MAP.keys()),
                'oddsFormat': 'american'}, timeout=20)
    if r.status_code != 200: return {}
    data = r.json()

    # Collect by (pitcher, prop_type, book) → both sides
    by_book: dict = {}  # (pitcher_lc, prop_type, book) -> {over, under, line, display}
    for bk in data.get('bookmakers', []):
        book = bk.get('key')
        for mkt in bk.get('markets', []):
            mkt_key = mkt.get('key')
            if mkt_key not in MARKET_MAP: continue
            prop_type = MARKET_MAP[mkt_key]
            for out in mkt.get('outcomes', []):
                pitcher = (out.get('description') or '').strip()
                if not pitcher: continue
                side = (out.get('name') or '').lower()
                direction = 'over' if 'over' in side else ('under' if 'under' in side else None)
                if not direction: continue
                line = out.get('point'); price = out.get('price')
                if line is None or price is None: continue
                key = (pitcher.lower(), prop_type, book)
                slot = by_book.setdefault(key, {'display': pitcher, 'line': float(line),
                                                'over_odds': None, 'under_odds': None})
                slot[f'{direction}_odds'] = int(price)
                slot['line'] = float(line)  # last write wins; matched line assumed

    # For each (pitcher, prop_type), pick preferred book that has BOTH sides
    combos: dict = {}
    for (p_lc, ptype, book), slot in by_book.items():
        combos.setdefault((p_lc, ptype), []).append({**slot, 'book': book})

    out: dict = {}
    for key, entries in combos.items():
        # Prefer books with both over+under
        both_sided = [e for e in entries if e['over_odds'] is not None and e['under_odds'] is not None]
        pool = both_sided or entries
        chosen = None
        for pref in PREFERRED_BOOKS:
            hit = next((e for e in pool if e['book'] == pref), None)
            if hit: chosen = hit; break
        if not chosen: chosen = pool[0]
        out[key] = chosen
    return out


def find_game(context: list, home: str, away: str) -> dict | None:
    for row in context:
        h, a = row.get('home_team',''), row.get('away_team','')
        if not h or not a: continue
        if _team_match(home, h) and _team_match(away, a): return row
    return None


def _team_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b: return True
    if a in b or b in a: return True
    return a.split()[-1] == b.split()[-1]


def _pitcher_is_home(pitcher_lc: str, ctx: dict) -> bool | None:
    hp = (ctx.get('home_pitcher') or '').lower()
    ap = (ctx.get('away_pitcher') or '').lower()
    if hp and (pitcher_lc in hp or hp in pitcher_lc or pitcher_lc.split()[-1] == hp.split()[-1]):
        return True
    if ap and (pitcher_lc in ap or ap in pitcher_lc or pitcher_lc.split()[-1] == ap.split()[-1]):
        return False
    return None


def build_signals(display: str, prop_type: str, prop_line: float, ctx: dict | None) -> tuple[dict, float | None]:
    """Return (signals_dict, edge_pct). edge_pct = (proj − line) / line when computable."""
    if not ctx: return {'_coverage_stub': True}, None
    is_home = _pitcher_is_home(display.lower(), ctx)
    if is_home is None: return {'_coverage_stub': True}, None
    prefix = 'home' if is_home else 'away'
    opp = 'away' if is_home else 'home'
    def _g(k):
        v = ctx.get(k); return v if v not in (None, '', 0) else None

    sigs: dict = {'_coverage_stub': True}
    edge_pct = None
    # Projection delta
    proj_key = PROJECTION_KEY.get(prop_type)
    proj = _g(f'{prefix}_pitcher_{proj_key}') if proj_key else None
    if proj is not None and prop_line:
        try:
            proj_f = float(proj)
            edge_pct = (proj_f - prop_line) / prop_line
            sigs['projection'] = f'Projected {prop_type} {proj_f:.2f} vs line {prop_line} · edge {edge_pct*100:+.1f}%'
            sigs['_edge_pct'] = round(edge_pct, 3)
        except (TypeError, ValueError): pass
    # Context signals
    for src, dst in [
        (f'{prefix}_sp_xera', 'xera'),
        (f'{prefix}_pitcher_last_3_era', 'l3_era'),
        (f'{prefix}_pitcher_last_3_k_pct', 'l3_k'),
        (f'{prefix}_pitcher_season_k_pct', 'season_k'),
        (f'{prefix}_pitcher_bb_pct', 'bb_pct'),
        (f'{opp}_team_k_pct', 'opp_k_rate'),
        (f'{opp}_wrc_plus', 'opp_wrc'),
        ('park_run_factor', 'park'),
    ]:
        v = _g(src)
        if v is not None: sigs[dst] = f'{dst} {v}'
    return sigs, edge_pct


def _resolve_team(display: str, ctx: dict | None) -> str:
    if not ctx: return 'UNKNOWN'
    is_home = _pitcher_is_home(display.lower(), ctx)
    if is_home is True: return ctx.get('home_team') or 'UNKNOWN'
    if is_home is False: return ctx.get('away_team') or 'UNKNOWN'
    return 'UNKNOWN'


def sweep(game_date: str, dry_run: bool = False) -> None:
    print(f'=== sweep_prop_coverage · {game_date} ===')
    if not ODDS_API_KEY:
        print('  ⛔ ODDS_API_KEY missing'); return

    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': 'player_name,prop_type,direction'},
                     timeout=15)
    existing_keys = {(p['player_name'].lower(), p['prop_type'], p['direction'])
                     for p in (r.json() if r.status_code == 200 else [])
                     if p.get('player_name')}
    print(f'  existing prop rows: {len(existing_keys)}')

    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}', 'select': '*'},
                     timeout=15)
    context = r.json() if r.status_code == 200 else []

    events = fetch_upcoming_events()
    print(f'  {len(events)} upcoming events')
    written = skipped = 0
    edge_ct = 0

    for ev in events:
        ctx = find_game(context, ev.get('home_team',''), ev.get('away_team',''))
        if not ctx: continue
        matchup = f"{ev.get('away_team','?')} @ {ev.get('home_team','?')}"

        for (pitcher_lc, prop_type), entry in fetch_pitcher_props(ev['id']).items():
            display = entry['display']
            team = _resolve_team(display, ctx)
            signals, edge = build_signals(display, prop_type, entry['line'], ctx)
            if edge is not None and abs(edge) >= 0.10: edge_ct += 1

            for direction in ('over', 'under'):
                key = (display.lower(), prop_type, direction)
                if key in existing_keys: continue
                payload = {
                    'game_date': game_date,
                    'game_id': ctx['game_id'],
                    'player_name': display,
                    'player_team': team,
                    'matchup': matchup,
                    'prop_type': prop_type,
                    'direction': direction,
                    'prop_line': entry['line'],
                    'book_line': entry['line'],
                    'book_over_odds': entry.get('over_odds'),
                    'book_under_odds': entry.get('under_odds'),
                    'book_source': entry['book'],
                    'signals': signals,
                    'tier': 'COVERAGE',
                    'conviction': 0,
                    'lineup_state': 'coverage_stub',
                }
                if dry_run:
                    written += 1; continue
                wr = requests.post(f'{SB}/rest/v1/mlb_pipeline_props',
                                   headers=H_WRITE, json=payload, timeout=15)
                if wr.status_code in (200, 201, 204):
                    written += 1
                else:
                    skipped += 1
                    if skipped <= 3: print(f'  ⚠ insert {wr.status_code}: {wr.text[:180]}')

    print(f'\n=== wrote {written} coverage stubs · {skipped} skipped · {edge_ct} with |edge|≥10% ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sweep(game_date=args.date or today_et(), dry_run=args.dry_run)
