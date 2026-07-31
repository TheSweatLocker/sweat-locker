"""Jerry-anchor Daily Degen (2026-07-31 · Tabletop C sport-universal).

Runs AFTER generate_prop_jerry_synthesis in the cron chain. Rebuilds
the daily parlay by selecting the highest-conviction Jerry BACK reads
across:
  - jerry_reads (game-level: MLB game synthesis)
  - prop_jerry_reads (prop-level)

Same post-hoc pattern as jerry_anchor_potd — legacy generate_daily_degen
still runs first for the fallback output; this script overwrites the
jerry_cache entry with the Jerry-anchored legs if enough qualify.

Diversity rules:
  - Max 3-4 legs (parlay sweet spot)
  - Max 1 leg per game (avoid same-game correlation)
  - Prefer verdict=BACK on props; conviction >= 70 across the board
  - Mix at least one game-level + one prop-level leg when both available

Sport-universal via registry — new sports add themselves by writing to
jerry_reads / prop_jerry_reads with sport tag.

Usage:
    python jerry_anchor_daily_degen.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

H_READ = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

CONVICTION_THRESHOLD = 70   # min Jerry conviction to be eligible for degen leg
MAX_LEGS = 4                # parlay cap
MIN_LEGS = 2                # anything less isn't a parlay; fall back to legacy


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch_game_reads(gd: str) -> list:
    """Every sport's jerry_reads for the day (currently MLB only; NBA/NFL
    when their synthesizers ship)."""
    r = requests.get(f'{SUPABASE_URL}/rest/v1/jerry_reads',
        headers=H_READ,
        params={'game_date': f'eq.{gd}',
                'select': 'sport,game_id,call_text,conviction,call_market,call_side,short_read',
                'order': 'conviction.desc'},
        timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_prop_reads(gd: str) -> list:
    r = requests.get(f'{SUPABASE_URL}/rest/v1/prop_jerry_reads',
        headers=H_READ,
        params={'game_date': f'eq.{gd}', 'call_verdict': 'eq.BACK',
                'select': 'sport,game_id,player_name,prop_type,direction,prop_line,'
                          'book_odds,conviction,short_read',
                'order': 'conviction.desc'},
        timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_team_names(sport: str, game_ids: list) -> dict:
    """Look up (away, home) for each game_id from the sport's context table."""
    if not game_ids: return {}
    table = {'MLB': 'mlb_game_context', 'NBA': 'nba_game_context',
             'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
             'NCAAB': 'ncaab_game_context'}.get(sport)
    if not table: return {}
    ids = ','.join(f'"{g}"' for g in game_ids)
    r = requests.get(f'{SUPABASE_URL}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_id': f'in.({ids})', 'select': 'game_id,home_team,away_team'},
                     timeout=15)
    return {row['game_id']: (row['away_team'], row['home_team']) for row in (r.json() if r.status_code == 200 else [])}


def build_jerry_parlay(gd: str) -> dict | None:
    games = fetch_game_reads(gd)
    props = fetch_prop_reads(gd)
    print(f'  game reads: {len(games)}  prop reads: {len(props)}')

    # Only eligible: conviction >= threshold, no PASS markets
    game_eligible = [g for g in games
                     if (g.get('conviction') or 0) >= CONVICTION_THRESHOLD
                     and (g.get('call_market') or '').lower() != 'pass']
    prop_eligible = [p for p in props if (p.get('conviction') or 0) >= CONVICTION_THRESHOLD]

    print(f'  eligible (conv>={CONVICTION_THRESHOLD}): {len(game_eligible)} game · {len(prop_eligible)} prop')

    # Prefer 1 game leg + 2-3 prop legs when both available.
    # Fall back to all-props or all-games if one side is empty.
    picks = []
    used_games = set()
    # 1 game leg first
    for g in game_eligible:
        if g['game_id'] not in used_games:
            picks.append({'kind': 'game', 'data': g})
            used_games.add(g['game_id']); break
    # Fill with props, one per game
    for p in prop_eligible:
        if len(picks) >= MAX_LEGS: break
        if p['game_id'] in used_games: continue
        picks.append({'kind': 'prop', 'data': p})
        used_games.add(p['game_id'])
    # Backfill with more game legs if props ran out
    for g in game_eligible:
        if len(picks) >= MAX_LEGS: break
        if g['game_id'] in used_games: continue
        picks.append({'kind': 'game', 'data': g})
        used_games.add(g['game_id'])

    if len(picks) < MIN_LEGS:
        return None   # not enough to build a parlay; let legacy stand

    # Team-name lookup for game legs
    mlb_game_ids = [pk['data']['game_id'] for pk in picks
                    if pk['kind'] == 'game' and pk['data'].get('sport') == 'MLB']
    teams = fetch_team_names('MLB', mlb_game_ids)

    legs = []
    for pk in picks:
        d = pk['data']
        if pk['kind'] == 'game':
            away, home = teams.get(d['game_id'], ('?', '?'))
            matchup = f'{away} @ {home}'
            legs.append({
                'kind': 'game', 'sport': d.get('sport') or 'MLB',
                'matchup': matchup, 'game_id': d['game_id'],
                'pick': d.get('call_text') or '?',
                'tier': _conv_tier(d.get('conviction')),
                'conviction': d.get('conviction'),
                'reason': (d.get('short_read') or '')[:220],
            })
        else:
            legs.append({
                'kind': 'prop', 'sport': d.get('sport') or 'MLB',
                'matchup': d.get('player_name') or '?', 'game_id': d['game_id'],
                'pick': f'{d["player_name"]} · {d["direction"].upper()} {d["prop_line"]} {d["prop_type"]}',
                'tier': _conv_tier(d.get('conviction')),
                'conviction': d.get('conviction'),
                'reason': (d.get('short_read') or '')[:220],
            })

    return {
        'legs': legs, 'n_legs': len(legs),
        'anchor': 'jerry_synthesis_v1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'game_date': gd,
        'avg_conviction': round(sum((l['conviction'] or 0) for l in legs) / len(legs), 1),
    }


def _conv_tier(c) -> str:
    if c is None: return 'lean'
    if c >= 80: return 'prime'
    if c >= 70: return 'strong'
    return 'lean'


def upsert_degen(gd: str, parlay: dict, dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] would upsert daily_degen_{gd} with {parlay["n_legs"]} legs')
        for l in parlay['legs']:
            print(f'    · {l["sport"]}/{l["kind"]}  {l["pick"][:60]}  conv={l["conviction"]}')
        return True
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport',
        headers=H_WRITE,
        json={
            'cache_key': f'daily_degen_{gd}',
            'game_id': f'daily_degen_{gd}',
            'sport': 'MLB',
            'narrative': f'Daily Degen · Jerry-anchored · {parlay["n_legs"]} legs (avg conv {parlay["avg_conviction"]})',
            'data': parlay,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f'  ✅ daily_degen_{gd} anchored to Jerry ({parlay["n_legs"]} legs)')
        return True
    print(f'  ⚠ upsert {r.status_code}: {r.text[:200]}')
    return False


def run(game_date: str | None = None, dry_run: bool = False):
    gd = game_date or today_et()
    print(f'=== jerry_anchor_daily_degen · {gd} ===')
    parlay = build_jerry_parlay(gd)
    if not parlay:
        print(f'  ⚠ not enough Jerry BACK legs (need >= {MIN_LEGS} at conv >= {CONVICTION_THRESHOLD})')
        print(f'  ⤳ legacy generate_daily_degen output stands unchanged')
        return
    upsert_degen(gd, parlay, dry_run=dry_run)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
