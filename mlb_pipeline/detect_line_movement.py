"""Line movement pattern detector (2026-08-13).

Reads line_history snapshots for today/upcoming games, detects three
sharp-signal patterns, upserts flags to line_movement_flags. Powers the
Steam Room tab's "why is this line here" chips.

Patterns detected:

  STEAM — 2+ books shifted the same direction on the same market/side
          within a 15-min window. Classic sharp coordinated move.

  RLM   — Reverse line movement. Line moved AGAINST the majority bets%
          direction. Requires line_snapshot data (bets%) to confirm.

  LIMIT — Money% moved with the line but bets% didn't (or moved opposite).
          Whale / limit-raiser signal. Requires line_snapshot data.

Only STEAM is detected from line_history alone. RLM + LIMIT cross-join
with line_snapshot (OddsCrowd money%/bets% time-series) so the same
detector run produces all three when both feeds are populated.

Cron cadence: run every 30-60 min alongside odds pull. Idempotent —
upserts on (game_id, market, side, pattern) so re-runs refresh
last_seen_at without duplicating.

CLI:
    python detect_line_movement.py [--sport MLB] [--dry-run] [--lookback-hours 6]
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional

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

STEAM_WINDOW_MIN = 15    # Multi-book coordinated move must land inside this
STEAM_MIN_BOOKS = 2      # 2+ books shifted same direction to fire
RLM_DIVERGE_MIN = 15     # bets% must sit 15pp against line-move to fire RLM
LIMIT_DIVERGE_MIN = 15   # money-minus-bets divergence to fire LIMIT


def _fmt(v):
    if v is None: return '-'
    return f'{v:+.1f}' if isinstance(v, float) else str(v)


def detect_steam(sport: str, lookback_hours: int, now_iso: str,
                 dry_run: bool) -> list:
    """Compare each (game, market, book, side)'s two most-recent snapshots.
    When 2+ books shifted the same direction within STEAM_WINDOW_MIN, emit."""
    since = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    r = requests.get(f'{SB}/rest/v1/line_history', headers=H_READ,
        params={'sport': f'eq.{sport}',
                'captured_at': f'gte.{since}',
                'select': 'game_id,matchup,market,book,side,line,price,captured_at',
                'order': 'captured_at.asc', 'limit': 5000},
        timeout=30)
    if r.status_code != 200:
        print(f'  line_history read failed: {r.status_code}'); return []
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return []

    # Group snapshots per (game, market, book, side) → time-ordered list
    grouped: dict = defaultdict(list)
    for row in rows:
        key = (row['game_id'], row['market'], row['book'], row['side'])
        grouped[key].append(row)

    # For each (game, market, side), collect per-book directional moves.
    # Direction: +1 if line/price moved toward that side, -1 if against.
    moves_by_gms: dict = defaultdict(list)  # (game_id, market, side) → [(book, dir, ts)]
    for (gid, market, book, side), snaps in grouped.items():
        if len(snaps) < 2: continue
        first = snaps[0]; last = snaps[-1]
        # Direction of movement: for totals compare line; for spread compare
        # line; for ml compare price (spreads may not have price shift).
        if market == 'total':
            delta = (last.get('line') or 0) - (first.get('line') or 0)
            # UNDER benefits when line goes UP (more runs to hit under target);
            # OVER benefits when line goes DOWN. Actually inverse:
            # under wins easier when line goes UP.
            direction = 1 if (delta > 0 and side == 'under') or (delta < 0 and side == 'over') else -1
        elif market == 'spread':
            delta = (last.get('line') or 0) - (first.get('line') or 0)
            # HOME spread going more negative = home more favored (bad for home ML)
            # but a bettor on home spread would want it LESS negative = better payout.
            # Simpler: side "home" benefits when its spread moves toward 0 (gets less juice)
            # or when the ML price shortens. For steam-move detection we care about
            # coordinated shift — either direction is a "move" for that side.
            direction = 1 if delta != 0 else 0
        else:  # ml
            delta = (last.get('price') or 0) - (first.get('price') or 0)
            # Home ML price rising (from -150 to -130) = books think home LESS likely,
            # public/sharp shifted OFF home. Direction convention: +1 = TOWARD this side
            direction = -1 if delta > 0 else 1 if delta < 0 else 0
        if direction == 0: continue
        moves_by_gms[(gid, market, side)].append({
            'book': book, 'dir': direction, 'ts': last.get('captured_at'),
            'matchup': last.get('matchup')
        })

    # STEAM fires when 2+ books moved TOWARD same side within STEAM_WINDOW_MIN
    flags = []
    for (gid, market, side), books in moves_by_gms.items():
        toward = [b for b in books if b['dir'] == 1]
        if len(toward) < STEAM_MIN_BOOKS: continue
        # Timestamp check — within 15 min of each other
        times = sorted([datetime.fromisoformat(b['ts'].replace('Z','+00:00'))
                        for b in toward if b['ts']])
        if len(times) < 2: continue
        span = (times[-1] - times[0]).total_seconds() / 60
        if span > STEAM_WINDOW_MIN: continue
        book_list = ', '.join(b['book'] for b in toward)
        matchup = toward[0]['matchup']
        detail = f'{len(toward)} books shifted toward {side} within {span:.0f} min ({book_list})'
        flags.append({
            'sport': sport, 'game_id': gid, 'market': market, 'side': side,
            'pattern': 'steam', 'detail': detail,
            'first_seen_at': times[0].isoformat(),
            'last_seen_at': now_iso,
        })
        if dry_run:
            print(f'  [DRY steam] {matchup} · {market}/{side} · {detail}')
    return flags


def detect_rlm_and_limit(sport: str, lookback_hours: int, now_iso: str,
                         dry_run: bool) -> list:
    """Cross-reference line_history with line_snapshot (OddsCrowd bets%/money%).
    RLM: line moved against bets% majority. LIMIT: money% and bets% diverge."""
    since = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    # Read latest line_snapshot rows (money%/bets%/divergence per market)
    snap = requests.get(f'{SB}/rest/v1/line_snapshot', headers=H_READ,
        params={'sport': f'eq.{sport}',
                'snapshot_ts': f'gte.{since}',
                'select': 'game_id,market,pick_side,money_pct,bets_pct,divergence,snapshot_ts',
                'order': 'snapshot_ts.desc', 'limit': 2000},
        timeout=30)
    if snap.status_code != 200:
        return []
    snap_rows = snap.json()
    if not isinstance(snap_rows, list) or not snap_rows: return []

    # Latest snapshot per (game, market) — sharp/public flow
    latest_by_gm: dict = {}
    for r in snap_rows:
        key = (r['game_id'], r['market'])
        if key not in latest_by_gm:
            latest_by_gm[key] = r

    # Also pull latest line_history to compare movement direction
    lh = requests.get(f'{SB}/rest/v1/line_history', headers=H_READ,
        params={'sport': f'eq.{sport}',
                'captured_at': f'gte.{since}',
                'select': 'game_id,matchup,market,side,line,price,captured_at',
                'order': 'captured_at.asc', 'limit': 5000},
        timeout=30)
    if lh.status_code != 200: return []
    lh_rows = lh.json()
    if not isinstance(lh_rows, list): return []

    # Per (game, market, side) compute first→last delta
    grouped: dict = defaultdict(list)
    for row in lh_rows:
        grouped[(row['game_id'], row['market'], row['side'])].append(row)

    flags = []
    for (gid, market), snap_row in latest_by_gm.items():
        pick_side = (snap_row.get('pick_side') or '').lower()
        # snap uses HOME/AWAY/OVER/UNDER — normalize
        if market == 'total':
            other_side = 'under' if pick_side == 'over' else 'over'
        else:
            other_side = 'away' if pick_side == 'home' else 'home'
        bets_pct = snap_row.get('bets_pct')
        money_pct = snap_row.get('money_pct')
        div = snap_row.get('divergence')

        # RLM: line moved TOWARD pick_side while bets% is on OTHER side
        picks_snaps = grouped.get((gid, market, pick_side), [])
        if len(picks_snaps) >= 2:
            first_line = picks_snaps[0].get('line') or picks_snaps[0].get('price') or 0
            last_line = picks_snaps[-1].get('line') or picks_snaps[-1].get('price') or 0
            moved_toward = (last_line > first_line) if market == 'total' and pick_side == 'under' else (last_line < first_line)
            # bets% on OTHER side means public is on OTHER, line moved TOWARD pick
            if bets_pct is not None and moved_toward:
                bets_other = 100 - bets_pct if pick_side.upper() != (snap_row.get('pick_side') or '').upper() else bets_pct
                if (100 - bets_other) >= (50 + RLM_DIVERGE_MIN):
                    matchup = picks_snaps[0].get('matchup') or f'{gid[:8]}'
                    detail = f'Line moved toward {pick_side} while bets% on {other_side} ({100 - bets_other:.0f}%)'
                    flags.append({
                        'sport': sport, 'game_id': gid, 'market': market, 'side': pick_side,
                        'pattern': 'rlm', 'detail': detail,
                        'first_seen_at': picks_snaps[0].get('captured_at') or now_iso,
                        'last_seen_at': now_iso,
                    })
                    if dry_run:
                        print(f'  [DRY rlm] {matchup} · {market}/{pick_side} · {detail}')

        # LIMIT: money%-bets% divergence ≥ LIMIT_DIVERGE_MIN
        if div is not None and money_pct is not None and bets_pct is not None:
            if abs(div) >= LIMIT_DIVERGE_MIN and money_pct > bets_pct:
                detail = f'Money% {money_pct:.0f} vs bets% {bets_pct:.0f} (Δ+{money_pct-bets_pct:.0f}pp) — limit/whale on {pick_side}'
                flags.append({
                    'sport': sport, 'game_id': gid, 'market': market, 'side': pick_side,
                    'pattern': 'limit', 'detail': detail,
                    'first_seen_at': snap_row.get('snapshot_ts') or now_iso,
                    'last_seen_at': now_iso,
                })
                if dry_run:
                    print(f'  [DRY limit] {gid[:8]} · {market}/{pick_side} · {detail}')
    return flags


def upsert_flags(flags: list) -> int:
    written = 0
    for i in range(0, len(flags), 100):
        chunk = flags[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/line_movement_flags?on_conflict=game_id,market,side,pattern',
            headers=H_WRITE, json=chunk, timeout=20)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  chunk {i}: {pr.status_code} {pr.text[:200]}')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='ALL',
        help='MLB / NFL / NCAAF / NCAAB / NBA / NHL / UFC / ALL')
    p.add_argument('--lookback-hours', type=int, default=6,
        help='How far back to look for movement patterns')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = ['MLB','NFL','NCAAF','NCAAB','NBA','NHL','UFC'] if args.sport == 'ALL' else [args.sport]
    now_iso = datetime.now(timezone.utc).isoformat()
    total = 0
    for s in sports:
        print(f'=== detect_line_movement · {s} · lookback {args.lookback_hours}h ===')
        steam = detect_steam(s, args.lookback_hours, now_iso, args.dry_run)
        rlm = detect_rlm_and_limit(s, args.lookback_hours, now_iso, args.dry_run)
        all_flags = steam + rlm
        if args.dry_run:
            print(f'  [DRY] would upsert {len(all_flags)} flags ({len(steam)} steam · {len(rlm)} rlm/limit)')
        elif all_flags:
            n = upsert_flags(all_flags)
            print(f'  wrote {n} flags')
            total += n
    print(f'\nTOTAL flags {"would-write" if args.dry_run else "written"}: {total if not args.dry_run else "n/a"}')


if __name__ == '__main__':
    main()
