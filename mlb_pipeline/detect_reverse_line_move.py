"""Reverse-line-move detector (2026-08-11 · queue option A).

Compares consecutive line_snapshot rows per (sport, game_id, market)
to detect the classic sharp signal:

  Public 70%+ on side A
  BUT line moved toward side B (against the public)
  → Sharp money is on side B

RLM is one of the most robust standalone sharp-money signals in
sports betting. The books move the line to protect against sharp
action, not retail. So when the line moves AGAINST the public
majority, sharp is on the other side.

## What it writes

Detected RLMs are written back to jerry_reads as an audit annotation:
`primary_play.rlm_flag` set on the game_context primary_play so
downstream (sweat card, Jerry synth) can consult. Also writes to
scenario_audit under `reverse_line_move` scenarios for aggregate
tracking.

Sport-universal: takes --sport arg, uses line_snapshot table which
is already sport-agnostic (registered in write_line_snapshot).

## Detection thresholds

  Public position: money_pct >= 70 on side A
  Line move: total moved by >= 0.5 OR spread by >= 0.5 OR ML by >= 15 cents
             (toward side B — away from side A)

Requires at least 2 snapshots per game/market to compare. Uses
first + last snapshot of the day for max signal.

## Usage

    python detect_reverse_line_move.py [--date YYYY-MM-DD] [--sport MLB]
    python detect_reverse_line_move.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

CTX_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NHL':   'nhl_game_context',
    'NBA':   'nba_game_context',
    'NCAAB': 'ncaab_game_context',
}

# Detection thresholds
PUBLIC_HEAVY_PCT = 70    # public side must be >= 70% money
TOTAL_MOVE_MIN = 0.5     # total line moved 0.5+
SPREAD_MOVE_MIN = 0.5    # spread moved 0.5+
ML_MOVE_MIN_CENTS = 15   # ML odds moved 15+ cents


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def american_move_cents(o1, o2) -> int | None:
    """Convert two American ML odds to a 'cents moved' distance.
    -110 → -120 = 10 cents. -110 → +105 = 15 cents. Simple absolute
    difference at the boundary."""
    if o1 is None or o2 is None: return None
    try: a = int(o1); b = int(o2)
    except: return None
    return abs(a - b)


def detect_rlm(snaps: list) -> dict | None:
    """Given a list of snapshots for (game_id, market) sorted by time,
    return RLM dict if detected, else None."""
    if len(snaps) < 2: return None
    first, last = snaps[0], snaps[-1]
    # Consistent side across snapshots (public position stable)
    if first.get('pick_side') != last.get('pick_side'): return None
    public_side = first.get('pick_side')
    if not public_side: return None
    latest_money = last.get('money_pct')
    if latest_money is None or latest_money < PUBLIC_HEAVY_PCT: return None

    # Detect line move AGAINST public_side
    market = first.get('market')
    moved_against = False
    move_detail = {}

    if market == 'total':
        # Public on OVER but total moved DOWN (toward UNDER) = sharp on UNDER
        # Public on UNDER but total moved UP (toward OVER) = sharp on OVER
        first_line = first.get('line'); last_line = last.get('line')
        if first_line is not None and last_line is not None:
            move = last_line - first_line
            if abs(move) >= TOTAL_MOVE_MIN:
                if public_side == 'OVER' and move < 0: moved_against = True
                elif public_side == 'UNDER' and move > 0: moved_against = True
                move_detail = {'from': first_line, 'to': last_line, 'delta': round(move, 1)}
    elif market == 'spread':
        first_line = first.get('line'); last_line = last.get('line')
        if first_line is not None and last_line is not None:
            move = last_line - first_line
            if abs(move) >= SPREAD_MOVE_MIN:
                # Spread convention: positive = away, so if public HOME and
                # spread moved up (more away), sharp is on AWAY.
                if public_side == 'HOME' and move > 0: moved_against = True
                elif public_side == 'AWAY' and move < 0: moved_against = True
                move_detail = {'from': first_line, 'to': last_line, 'delta': round(move, 1)}
    elif market == 'ml':
        first_odds = first.get('odds_pick'); last_odds = last.get('odds_pick')
        if first_odds and last_odds:
            # Decimal odds — if public_side odds got LONGER (higher decimal),
            # line moved away from public = sharp on other side.
            move = last_odds - first_odds
            if abs(move) >= 0.10:  # decimal ~10 cent shift
                if move > 0: moved_against = True  # public side got longer
                move_detail = {'from': first_odds, 'to': last_odds, 'delta': round(move, 3)}

    if not moved_against: return None
    return {
        'public_side': public_side,
        'public_money_pct': latest_money,
        'market': market,
        'move': move_detail,
        'snapshots_analyzed': len(snaps),
        'first_ts': first.get('snapshot_ts'),
        'last_ts': last.get('snapshot_ts'),
    }


def run(sport: str = 'MLB', game_date: str | None = None,
        dry_run: bool = False) -> int:
    ctx_table = CTX_TABLE.get(sport)
    if not ctx_table:
        print(f'  [{sport}] no ctx table — skip'); return 0
    gd = game_date or _et_today()
    print(f'=== detect_reverse_line_move · {sport} · {gd} ===')

    # Pull all snapshots for this sport + date — paginate (PostgREST 1k
    # cap). Prior version silently dropped later timestamps once a full
    # slate × hourly OC snapshots × sides exceeded 1000 rows, missing
    # the very RLM candidates the detector exists to catch.
    snaps = []
    for off in range(0, 20000, 1000):
        r = requests.get(f'{SB}/rest/v1/line_snapshot', headers=H_READ,
            params={'sport': f'eq.{sport}',
                    'snapshot_ts': f'gte.{gd}T00:00:00',
                    'select': 'game_id,market,snapshot_ts,pick_side,money_pct,'
                              'bets_pct,line,odds_pick',
                    'order': 'snapshot_ts.asc', 'limit': 1000, 'offset': off},
            timeout=15)
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list):
            print(f'  fetch failed: {chunk}'); return 0
        snaps.extend(chunk)
        if len(chunk) < 1000: break
    print(f'  {len(snaps)} snapshots pulled')

    # Group by (game_id, market)
    grouped = defaultdict(list)
    for s in snaps:
        grouped[(s['game_id'], s['market'])].append(s)

    rlm_hits = []
    for (game_id, market), group in grouped.items():
        # Sort by ts
        group.sort(key=lambda x: x.get('snapshot_ts') or '')
        rlm = detect_rlm(group)
        if rlm:
            rlm_hits.append({'game_id': game_id, 'market': market, **rlm})

    print(f'  {len(rlm_hits)} reverse-line-move signals detected')
    for r in rlm_hits:
        print(f'    {r["game_id"][:8]} · {r["market"]:6} · public {r["public_side"]} '
              f'{r["public_money_pct"]}% · line {r["move"].get("from")} -> '
              f'{r["move"].get("to")} ({r["move"].get("delta"):+}) · '
              f'suggests sharp on OTHER side')

    if dry_run or not rlm_hits:
        return len(rlm_hits)

    # Annotate ctx.primary_play with rlm_flag for downstream consumers
    written = 0
    for r in rlm_hits:
        cr = requests.get(f'{SB}/rest/v1/{ctx_table}?game_id=eq.{r["game_id"]}&select=primary_play',
                          headers=H_READ, timeout=8)
        rows = cr.json() if cr.status_code == 200 else []
        if not rows: continue
        pp = rows[0].get('primary_play') or {}
        if not isinstance(pp, dict): pp = {}
        pp['rlm_flag'] = {
            'market': r['market'],
            'public_side': r['public_side'],
            'suggested_side': 'other',
            'confidence': 'strong' if r['public_money_pct'] >= 75 else 'moderate',
            'detected_at': datetime.now(timezone.utc).isoformat(),
        }
        pr = requests.patch(f'{SB}/rest/v1/{ctx_table}?game_id=eq.{r["game_id"]}',
                            headers=H_WRITE, json={'primary_play': pp}, timeout=10)
        if pr.status_code in (200, 204): written += 1
    print(f'  {written} primary_play annotations written')
    return len(rlm_hits)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB',
                   help='MLB / NFL / NCAAF / NHL / NBA / NCAAB')
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
