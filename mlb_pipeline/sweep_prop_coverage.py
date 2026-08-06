"""Prop coverage sweeper (2026-07-31 · Tier 3 · batter added 2026-08-05).

Guarantees every player prop the sportsbook publishes has book_odds
attached to mlb_pipeline_props. Two paths:

  PATH A (PATCH): row already exists but book_line / book_odds is null
    → attach line + odds + source. Used for BOTH pitcher and batter
    families. Closes the "PRIME hits_over @ NULL odds" trap that let
    -300 juice batter picks ship all summer.

  PATH B (INSERT): no row exists → insert COVERAGE-tier stub with
    signals block. Used ONLY for pitchers — generate_props gates
    batters on lineup+sample, so an un-scored batter shouldn't be
    back-doored via the sweeper.

Downstream generate_prop_jerry_synthesis has an edge gate — only
rates props with a real signal (legacy scored it OR meaningful
projection delta), so coverage stays complete but Jerry-take
volume stays manageable (~30–60/day).

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
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MARKET_MAP = {
    'pitcher_strikeouts':  'ks',
    'pitcher_earned_runs': 'er',
    'pitcher_walks':       'bb',
    'pitcher_hits_allowed':'ha',
    'pitcher_outs':        'outs',
    # 2026-08-05: batter markets. Batter props are PATCH-only (fill book_odds
    # on rows generate_props already scored); we don't insert new batter stubs
    # because generate_props gates on lineup + season sample. Adding batters
    # here closes the "PRIME hits_over @ NULL odds" trap that shipped -300
    # juice batter picks all summer.
    'batter_hits':         'hits',
}
BATTER_PREFIXES = {'hits'}  # markets that map to batter prop families

PREFERRED_BOOKS = ['hardrockbet', 'hardrockbet_oh', 'draftkings', 'betmgm',
                   'fanduel', 'bovada', 'espnbet', 'betrivers']

# Per-prop-type: which context projection key aligns with the line
# (pitcher families only; batters use lineup/xstats projections downstream)
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


def fetch_player_props(event_id: str) -> dict:
    """Returns {(player_lc, prop_type): {'line': .., 'over_odds': .., 'under_odds': .., 'book': .., 'display': ..}}
    Bundles over+under from the same preferred book so both odds columns populate.
    Handles both pitcher (bb/ks/er/ha/outs) and batter (hits) markets in one pull."""
    r = requests.get(
        f'https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds',
        params={'apiKey': ODDS_API_KEY, 'regions': 'us,us2',
                'markets': ','.join(MARKET_MAP.keys()),
                'oddsFormat': 'american'}, timeout=20)
    if r.status_code != 200: return {}
    data = r.json()

    # Collect by (player, prop_type, book, LINE) → both sides.
    # Line must be in the key: batter markets return alt lines per book
    # (Bohm over 1.5 @ +250 AND over 2.5 @ +900), and merging them under
    # one book slot would over-write line each iteration → wrong "line"
    # paired with wrong odds. 2026-08-05: was silently pairing alt lines.
    by_bl: dict = {}  # (player_lc, prop_type, book, line) -> {over, under, display, line}
    for bk in data.get('bookmakers', []):
        book = bk.get('key')
        for mkt in bk.get('markets', []):
            mkt_key = mkt.get('key')
            if mkt_key not in MARKET_MAP: continue
            prop_type = MARKET_MAP[mkt_key]
            for out in mkt.get('outcomes', []):
                player = (out.get('description') or '').strip()
                if not player: continue
                side = (out.get('name') or '').lower()
                direction = 'over' if 'over' in side else ('under' if 'under' in side else None)
                if not direction: continue
                line = out.get('point'); price = out.get('price')
                if line is None or price is None: continue
                key = (player.lower(), prop_type, book, float(line))
                slot = by_bl.setdefault(key, {'display': player, 'line': float(line),
                                              'over_odds': None, 'under_odds': None})
                slot[f'{direction}_odds'] = int(price)

    # Group all (book, line) offerings per (player, prop_type)
    combos: dict = {}
    for (p_lc, ptype, book, _line), slot in by_bl.items():
        combos.setdefault((p_lc, ptype), []).append({**slot, 'book': book})

    def _tightness(e: dict) -> float:
        """Distance from 50/50 based on over_odds. Smaller = closer to standard line."""
        o = e.get('over_odds')
        if o is None: return 1.0
        implied = 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)
        return abs(implied - 0.5)

    out: dict = {}
    for key, entries in combos.items():
        # Prefer both-sided offerings first
        both_sided = [e for e in entries if e['over_odds'] is not None and e['under_odds'] is not None]
        pool = both_sided or entries
        # Sort by tightness (line closest to 50/50 — the "standard" line for that market)
        pool_sorted = sorted(pool, key=_tightness)
        # Walk PREFERRED_BOOKS in order; take first hit at the tightest line that book has
        chosen = None
        for pref in PREFERRED_BOOKS:
            hit = next((e for e in pool_sorted if e['book'] == pref), None)
            if hit: chosen = hit; break
        if not chosen: chosen = pool_sorted[0]
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
                             'select': 'id,player_name,prop_type,direction,book_line,book_over_odds,book_under_odds'},
                     timeout=15)
    existing_rows = [p for p in (r.json() if r.status_code == 200 else []) if p.get('player_name')]
    existing_by_key: dict = {}
    for p in existing_rows:
        existing_by_key[(p['player_name'].lower(), p['prop_type'], p['direction'])] = p
    print(f'  existing prop rows: {len(existing_by_key)}')

    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}', 'select': '*'},
                     timeout=15)
    context = r.json() if r.status_code == 200 else []

    events = fetch_upcoming_events()
    print(f'  {len(events)} upcoming events')
    written = skipped = 0
    edge_ct = 0

    patched = 0
    for ev in events:
        ctx = find_game(context, ev.get('home_team',''), ev.get('away_team',''))
        if not ctx: continue
        matchup = f"{ev.get('away_team','?')} @ {ev.get('home_team','?')}"

        for (player_lc, prop_type), entry in fetch_player_props(ev['id']).items():
            display = entry['display']
            is_batter = prop_type in BATTER_PREFIXES
            # Pitcher signals only; batter attribution comes from existing DB row
            if is_batter:
                signals, edge = None, None
                team = None
            else:
                team = _resolve_team(display, ctx)
                signals, edge = build_signals(display, prop_type, entry['line'], ctx)
                if edge is not None and abs(edge) >= 0.10: edge_ct += 1

            for direction in ('over', 'under'):
                # 2026-08-02: store as full {family}_{direction} form to match
                # the convention generate_props.py already uses. Bare family
                # broke grader natural-key JOIN → 152 orphan Jerry reads.
                full_type = f'{prop_type}_{direction}'
                key = (display.lower(), full_type, direction)
                existing = existing_by_key.get(key)

                # PATH A: row already exists → PATCH book fields if missing
                if existing:
                    odds_col = 'book_over_odds' if direction == 'over' else 'book_under_odds'
                    needs_patch = (
                        existing.get(odds_col) is None
                        or existing.get('book_line') is None
                    )
                    if not needs_patch:
                        continue
                    patch = {
                        'book_line': entry['line'],
                        'book_over_odds': entry.get('over_odds'),
                        'book_under_odds': entry.get('under_odds'),
                        'book_source': entry['book'],
                        'last_attached_at': datetime.now(timezone.utc).isoformat(),
                    }
                    if dry_run:
                        patched += 1
                        continue
                    pr = requests.patch(
                        f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{existing["id"]}',
                        headers=H_WRITE, json=patch, timeout=15,
                    )
                    if pr.status_code in (200, 201, 204):
                        patched += 1
                    else:
                        skipped += 1
                        if skipped <= 3: print(f'  ⚠ patch {pr.status_code}: {pr.text[:180]}')
                    continue

                # PATH B: no existing row. Insert stub only for pitcher families.
                # Batter families are PATCH-only: generate_props gates on lineup+
                # sample, so an un-scored batter shouldn't be back-doored here.
                if is_batter:
                    continue

                payload = {
                    'game_date': game_date,
                    'game_id': ctx['game_id'],
                    'player_name': display,
                    'player_team': team,
                    'matchup': matchup,
                    'prop_type': full_type,
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

    print(f'\n=== wrote {written} stubs · patched {patched} · {skipped} skipped · {edge_ct} with |edge|≥10% ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sweep(game_date=args.date or today_et(), dry_run=args.dry_run)
