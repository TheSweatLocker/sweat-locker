"""classify_line_moves — cross-reference line movement with sharp / public
splits to emit SHARP_MOVE / PUBLIC_MOVE / RLM / CONSENSUS tags.

Runs AFTER detect_line_movement.py (which populates raw steam/RLM/limit
flags from line_history alone). This script upgrades each flag with a
proper classification by cross-referencing:
  * line_history       — where did the line move to? (per-book snapshots)
  * line_snapshot      — OddsCrowd money% / bets% (sharp vs public split)
  * fadereport_signals — cross-verification split (if table exists)

Per project_sharp_money_fade_808:
  * When OddsCrowd money% ≥ 60 AND bets% low → sharp side (line SHOULD move here)
  * When line moves TO side that has high bets% but low money% → public trap
  * When line moves AWAY from side with heavy public $ → RLM (classic sharp signal)

Writes classification + supporting split snapshot back to line_movement_flags.
Sport-universal — driven by line_movement_config.SPORT_CONFIG.

CLI
  python classify_line_moves.py                       # all sports, today
  python classify_line_moves.py --sport MLB
  python classify_line_moves.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

from line_movement_config import get_config, classify_split, combine_classifications

SUPPORTED_SPORTS = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL', 'UFC']


def fetch_flags(sport: str, since_hours: int = 24) -> list:
    """Pull unclassified (or recently re-fired) line_movement_flags for sport."""
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    r = requests.get(
        f'{SB}/rest/v1/line_movement_flags'
        f'?sport=eq.{sport}&first_seen_at=gte.{since}'
        f'&select=id,game_id,market,side,pattern,detail,first_seen_at,classification',
        headers=H_READ, timeout=20)
    if r.status_code != 200:
        print(f'  ✗ flags fetch {r.status_code}')
        return []
    return r.json() or []


def _fetch_oddscrowd_split(game_id: str, market: str) -> dict | None:
    """Return latest oddscrowd snapshot for (game, market)."""
    r = requests.get(
        f'{SB}/rest/v1/line_snapshot'
        f'?game_id=eq.{game_id}&market=eq.{market}&source=eq.oddscrowd'
        f'&order=snapshot_ts.desc&limit=1',
        headers=H_READ, timeout=15)
    if r.status_code != 200:
        return None
    rows = r.json() or []
    return rows[0] if rows else None


def _fetch_fadereport_split(game_id: str, market: str) -> dict | None:
    """Return latest fadereport snapshot for (game, market). Returns None if
    table doesn't exist yet (migration 20260814_fadereport_signals pending)."""
    r = requests.get(
        f'{SB}/rest/v1/fadereport_signals'
        f'?game_id=eq.{game_id}&market=eq.{market}'
        f'&order=captured_at.desc&limit=1',
        headers=H_READ, timeout=10)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return None
    rows = r.json() or []
    return rows[0] if rows else None


def _split_on_side(snap: dict | None, side: str, key_money: str, key_bets: str) -> tuple:
    """Return (money_pct, bets_pct) ON `side` from a snapshot row.

    Snapshot's pick_side is the side the snapshot's percentages describe.
    If pick_side matches side → percentages are ON side; if opposite → invert.
    """
    if not snap:
        return (None, None)
    money = snap.get(key_money); bets = snap.get(key_bets)
    if money is None or bets is None:
        return (None, None)
    pick = (snap.get('pick_side') or '').upper()
    side_up = (side or '').upper()
    # Normalize direction words
    same_side_pairs = {
        ('HOME', 'HOME'), ('AWAY', 'AWAY'),
        ('OVER', 'OVER'), ('UNDER', 'UNDER'),
    }
    if (pick, side_up) in same_side_pairs:
        return (float(money), float(bets))
    return (100.0 - float(money), 100.0 - float(bets))


def classify_flag(sport: str, flag: dict) -> dict | None:
    """Classify one flag. Returns payload dict for PATCH, or None if skip."""
    gid = flag['game_id']; market = flag['market']; side = flag['side']
    pattern = (flag.get('pattern') or '').lower()

    oc = _fetch_oddscrowd_split(gid, market)
    fr = _fetch_fadereport_split(gid, market)

    oc_money, oc_bets = _split_on_side(oc, side, 'money_pct', 'bets_pct')
    fr_handle, fr_bettors = _split_on_side(fr, side, 'handle_pct', 'bettors_pct')

    # RLM: line moved AWAY from the side that had heavy public money.
    # Other patterns (steam/limit): line moved TOWARD the side listed.
    line_moved_toward = pattern != 'rlm'

    # Classify from each source independently.
    oc_cls = classify_split(sport, oc_money, oc_bets, line_moved_toward) \
             if oc_money is not None else None
    fr_cls = classify_split(sport, fr_handle, fr_bettors, line_moved_toward) \
             if fr_handle is not None else None

    # Combine via cross-source agreement — only *_CONFIRMED classifications
    # come from BOTH sources agreeing. Everything else muted (_LEAN, SPLIT,
    # PATTERN_ONLY, NEUTRAL) so we don't surface false certainty.
    classification, _ = combine_classifications(oc_cls, fr_cls)

    # Only surface split numbers when they carry weight: CONFIRMED shows
    # both sources (they agreed); LEAN shows whichever source spoke; SPLIT
    # shows both so user sees the disagreement; PATTERN_ONLY/NEUTRAL don't
    # surface any numbers (would mislead).
    show_numbers = classification.endswith('_CONFIRMED') or \
                   classification.endswith('_LEAN') or \
                   classification == 'SOURCES_SPLIT'

    payload = {
        'classification': classification,
        'money_pct':     round(oc_money, 1) if show_numbers and oc_money is not None else None,
        'bets_pct':      round(oc_bets, 1)  if show_numbers and oc_bets  is not None else None,
        'handle_pct':    round(fr_handle, 1)   if show_numbers and fr_handle   is not None else None,
        'bettors_pct':   round(fr_bettors, 1)  if show_numbers and fr_bettors  is not None else None,
        'classified_at': datetime.now(timezone.utc).isoformat(),
    }
    return payload


def patch_flag(flag_id: int, payload: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/line_movement_flags?id=eq.{flag_id}',
        headers=H_WRITE, json=payload, timeout=15)
    return r.status_code in (200, 204)


def run_sport(sport: str, dry_run: bool = False) -> tuple:
    flags = fetch_flags(sport)
    print(f'  {sport}: {len(flags)} recent flags')
    if not flags:
        return (0, 0)

    counts = {}
    written = 0
    for flag in flags:
        payload = classify_flag(sport, flag)
        if not payload:
            continue
        cls = payload['classification']
        counts[cls] = counts.get(cls, 0) + 1
        if dry_run:
            print(f'    [DRY] flag#{flag["id"]:>4} {flag["market"]:<8} {flag["side"]:<6} '
                  f'{flag["pattern"]:<7} → {cls:<12} · '
                  f'money%={payload["money_pct"]} bets%={payload["bets_pct"]}')
            written += 1
            continue
        if patch_flag(flag['id'], payload):
            written += 1

    print(f'  {sport}: classifications → ' +
          ', '.join(f'{k}={v}' for k, v in counts.items() if v > 0))
    return (written, len(flags))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=SUPPORTED_SPORTS + ['ALL'], default='ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sports = SUPPORTED_SPORTS if args.sport == 'ALL' else [args.sport]
    print(f'=== classify_line_moves · {"/".join(sports)} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    total_written = total_flags = 0
    for s in sports:
        w, n = run_sport(s, dry_run=args.dry_run)
        total_written += w; total_flags += n
    print(f'\n  ✓ {total_written}/{total_flags} flags classified')


if __name__ == '__main__':
    main()
