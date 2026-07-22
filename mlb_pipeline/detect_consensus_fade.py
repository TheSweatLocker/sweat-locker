"""Consensus-fade alert detector — flags games where external consensus
looks like public heat, not signal.

Runs after pull_externals writes today's picks. For each game_id on
today's slate, aggregates external picks and detects the fade pattern:

  1. Consensus >= FADE_PCT_THRESHOLD (default 75%) on one side
  2. AT LEAST ONE audit-flagged fade source is on that side, OR
  3. total book count >= FADE_MIN_SOURCES (default 5)

Writes to mlb_game_context.consensus_fade_flag + side + pct + n + note.
App reads flag to render Tier-1 chip on the card.

Sport-parameterized: --sport MLB (default). NFL/NCAAB will drop in
once their game_context tables exist and pull_externals populates them.

USAGE:
    python detect_consensus_fade.py                    # today MLB
    python detect_consensus_fade.py --date 2026-07-22
    python detect_consensus_fade.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

# Thresholds — calibrate against actual outcome data after 30d.
FADE_PCT_THRESHOLD = 0.75   # 75%+ unanimous
FADE_MIN_SOURCES = 5        # or at least 5 books agreeing

# Sport-specific game_context table
SPORT_CONTEXT_TABLE = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAB': 'ncaab_game_context',
}


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def fetch_picks(game_date: str, sport: str) -> list:
    r = requests.get(
        f'{SB}/rest/v1/external_picks?'
        f'game_date=eq.{game_date}&sport=eq.{sport}'
        f'&select=game_id,source,surface,pick_side,fade_flag'
        f'&limit=1000',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def compute_alerts(picks: list) -> dict:
    """Group picks by game_id + surface + side and detect fade patterns.

    Returns {game_id: {flag, side, pct, n, note}} for games that trigger.
    """
    # picks_by[(gid, surface)] = list of picks
    picks_by = defaultdict(list)
    for p in picks:
        gid = p['game_id']
        surface = p['surface']
        picks_by[(gid, surface)].append(p)

    alerts = {}
    for (gid, surface), plist in picks_by.items():
        if surface not in ('ml', 'spread', 'rl', 'total'):
            continue
        # Count by side (HOME/AWAY for sides, OVER/UNDER for totals)
        by_side = defaultdict(list)
        for p in plist:
            side = (p.get('pick_side') or '').upper()
            if not side:
                continue
            by_side[side].append(p)
        total = sum(len(v) for v in by_side.values())
        if total < FADE_MIN_SOURCES:
            continue
        # Find dominant side
        dominant = max(by_side.items(), key=lambda kv: len(kv[1]))
        side, side_picks = dominant
        pct = len(side_picks) / total
        if pct < FADE_PCT_THRESHOLD:
            continue
        # Fade-tagged source on dominant side?
        fade_sources = [p['source'] for p in side_picks
                        if p.get('fade_flag') == 'fade']
        # Trigger criteria: high consensus (auto-trigger at ≥75%) OR
        # medium consensus + fade-tagged source present.
        triggered = pct >= FADE_PCT_THRESHOLD
        note_bits = [
            f'{len(side_picks)}/{total} books on {side}'
        ]
        if fade_sources:
            note_bits.append(f'audit-fade sources: {", ".join(fade_sources)}')
        # Only stamp the ONE strongest per game — prefer ml, then total, then spread
        surface_rank = {'ml': 0, 'total': 1, 'spread': 2, 'rl': 3}
        candidate = {
            'flag': True, 'side': side, 'pct': round(pct, 3),
            'n': total, 'surface': surface,
            'note': ' · '.join(note_bits),
            'rank': surface_rank.get(surface, 9),
        }
        existing = alerts.get(gid)
        if existing is None or candidate['rank'] < existing['rank']:
            alerts[gid] = candidate
    return alerts


def patch_context(gid: str, alert: dict, sport: str, dry_run: bool = False) -> bool:
    if dry_run:
        return True
    tbl = SPORT_CONTEXT_TABLE[sport]
    payload = {
        'consensus_fade_flag': alert['flag'],
        'consensus_fade_side': alert['side'],
        'consensus_fade_pct': alert['pct'],
        'consensus_fade_n': alert['n'],
        'consensus_fade_note': alert['note'],
    }
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
        headers=H_WRITE, json=payload, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def clear_stale_flags(game_date: str, active_gids: set, sport: str,
                      dry_run: bool = False) -> int:
    """Games that USED to have consensus fade but no longer meet criteria
    should get flag reset. Prevents yesterday's alerts from persisting."""
    tbl = SPORT_CONTEXT_TABLE[sport]
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?game_date=eq.{game_date}'
        f'&consensus_fade_flag=eq.true&select=game_id',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        return 0
    prev = {g['game_id'] for g in r.json()}
    stale = prev - active_gids
    if not stale:
        return 0
    if dry_run:
        print(f'  [DRY] would clear stale fade flag on {len(stale)} games')
        return len(stale)
    for gid in stale:
        payload = {
            'consensus_fade_flag': None, 'consensus_fade_side': None,
            'consensus_fade_pct': None, 'consensus_fade_n': None,
            'consensus_fade_note': None,
        }
        requests.patch(
            f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
            headers=H_WRITE, json=payload, timeout=15,
        )
    return len(stale)


def run(sport: str = 'MLB', game_date: Optional[str] = None,
        dry_run: bool = False) -> None:
    game_date = game_date or _et_now().date().isoformat()
    print(f'=== consensus-fade detector · {sport} · {game_date} ===')

    picks = fetch_picks(game_date, sport)
    print(f'  external picks pulled: {len(picks)}')
    if not picks:
        return

    alerts = compute_alerts(picks)
    print(f'  games flagged: {len(alerts)}')

    total_written = 0
    for gid, a in alerts.items():
        if dry_run:
            print(f"    [DRY] {gid[:12]}...  {a['surface']:6}:{a['side']:5}  "
                  f"{a['pct']*100:5.1f}% n={a['n']}  {a['note']}")
        else:
            if patch_context(gid, a, sport):
                total_written += 1

    # Clear stale alerts on today's games not in this run
    cleared = clear_stale_flags(game_date, set(alerts.keys()), sport, dry_run=dry_run)

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {total_written} fade flags · cleared {cleared} stale')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB',
                    choices=list(SPORT_CONTEXT_TABLE.keys()))
    ap.add_argument('--date', default=None,
                    help='YYYY-MM-DD (default today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport=args.sport, game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
