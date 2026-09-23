"""UFC jerry_reads grader (2026-08-09).

UFC calls in `jerry_reads` use:
  sport      = 'UFC'
  game_id    = 'ufc_YYYY-MM-DD_<fight_order>'
  call_market = 'fight' | 'pass'
  call_side  = 'A' (fighter_a wins) | 'B' (fighter_b wins)

Join key to `ufc_fight_results` is (event_date, fight_order) parsed from
game_id. `winner` column in fight_results is 'A', 'B', 'draw', or null
(no-contest / pending). We only grade rows with a decisive winner.

Coverage audit 2026-08-09 found 11 ungraded UFC jerry_reads from 8/1
and 8/8 events, and no automated grader path — this fills that gap.

Usage:
  python grade_ufc_jerry_reads.py [--date YYYY-MM-DD] [--dry-run]
  python grade_ufc_jerry_reads.py --backfill-days 30
"""
from __future__ import annotations
import argparse, os, re, sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import json
import unicodedata

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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

GAME_ID_RE = re.compile(r'^ufc_(\d{4}-\d{2}-\d{2})_(\d+)$')


def parse_gid(gid: str) -> tuple[str, int] | None:
    m = GAME_ID_RE.match(gid or '')
    if not m: return None
    return (m.group(1), int(m.group(2)))


def _norm_fighter(name: str) -> str:
    """Casefold and strip accents/punctuation. 'Uroš Medić' -> 'uros medic'."""
    n = unicodedata.normalize('NFKD', str(name or ''))
    n = ''.join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r'[^a-zA-Z ]', '', n).lower()
    return re.sub(r'\s+', ' ', n).strip()


def _same_fighter(a: str, b: str) -> bool:
    """True when two spellings name the same fighter.

    Surname match is allowed because the card and the results table
    disagree on given names often enough to matter (Uros vs Uroš,
    'Donte Johnson' vs 'Donte Johnson Jr.'), but only when the surname
    is distinctive enough to be worth trusting.
    """
    na, nb = _norm_fighter(a), _norm_fighter(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = na.split(), nb.split()
    return bool(sa and sb and len(sa[-1]) >= 4 and sa[-1] == sb[-1])


def grade_one(read: dict, results_by_key: dict) -> str | None:
    market = (read.get('call_market') or '').lower()
    if market == 'pass':
        return 'NO_ACTION'
    if market != 'fight':
        return None
    parsed = parse_gid(read.get('game_id', ''))
    if not parsed: return None
    key = parsed
    fight = results_by_key.get(key)
    if not fight:
        return None
    winner = (fight.get('winner') or '').strip().upper()
    if not winner: return None  # not yet resolved
    if winner in ('DRAW', 'NC', 'NO_CONTEST'):
        return 'Void'

    # ── 2026-09-22 ROOT-CAUSE FIX — grade by NAME, never by letter ──
    #
    # This used to be:
    #
    #     return 'Win' if winner == side else 'Loss'
    #
    # comparing jerry_reads.call_side ('A'/'B') to
    # ufc_fight_results.winner ('a'/'b'). Those letters index DIFFERENT
    # orderings. ufc_fight_results is stored winner-first — 1,118 rows
    # say 'a' against 70 that say 'b' — while call_side follows the
    # card's ordering. Any fight where the card's B is the results
    # table's A graded backwards.
    #
    # Measured: 7 of 15 graded UFC calls were wrong, almost all of them
    # a win recorded as a loss. Stored record 5-10; corrected 10-5. The
    # published UFC record was inverted by a letter comparison.
    #
    # Names do not have an ordering, so they cannot disagree about one.
    snap = read.get('input_snapshot') or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            snap = {}
    side = (read.get('call_side') or '').strip().upper()
    picked = (snap.get('model_pick_fighter')
              or (snap.get('fighter_a') if side == 'A' else
                  snap.get('fighter_b') if side == 'B' else None))
    won_name = fight.get('fighter_a') if winner == 'A' else                fight.get('fighter_b') if winner == 'B' else None
    if picked and won_name:
        return 'Win' if _same_fighter(picked, won_name) else 'Loss'

    # No names on either side — refuse rather than guess. A letter
    # comparison is what produced the inverted record; returning None
    # leaves the row ungraded and visible instead of confidently wrong.
    print(f'  ⚠ ungradeable (no fighter names): game_id={read.get("game_id")} '
          f'side={side or "?"}')
    return None


def run(game_date: str | None = None, backfill_days: int = 60,
        dry_run: bool = False, regrade: bool = False) -> int:
    if game_date:
        dates = [game_date]
    else:
        base = (datetime.now(timezone.utc) - timedelta(hours=28)).date()
        dates = [(base - timedelta(days=i)).isoformat() for i in range(backfill_days + 1)]

    total = 0
    for gd in dates:
        r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
            params={'sport': 'eq.UFC', 'game_date': f'eq.{gd}',
                    # --regrade re-settles rows that already carry a
                    # result. Needed once: the letter-comparison bug
                    # fixed 2026-09-22 left 7 of 15 graded calls
                    # inverted, and they will never be revisited by
                    # the normal `result IS NULL` sweep.
                    **({} if regrade else {'result': 'is.null'}),
                    'select': 'id,game_id,call_market,call_side,input_snapshot,result'}, timeout=15)
        reads = r.json() if r.status_code == 200 else []
        if not reads: continue

        rr = requests.get(f'{SB}/rest/v1/ufc_fight_results', headers=H_READ,
            params={'event_date': f'eq.{gd}',
                    'select': 'event_date,fight_order,fighter_a,fighter_b,winner,method'},
            timeout=15)
        fights = rr.json() if rr.status_code == 200 else []
        by_key = {(f['event_date'], f['fight_order']): f for f in fights if f.get('fight_order') is not None}

        graded = 0
        for read in reads:
            result = grade_one(read, by_key)
            if not result: continue
            parsed = parse_gid(read.get('game_id',''))
            fight = by_key.get(parsed) if parsed else None
            actual = {
                'winner': (fight or {}).get('winner'),
                'method': (fight or {}).get('method'),
                'fighter_a': (fight or {}).get('fighter_a'),
                'fighter_b': (fight or {}).get('fighter_b'),
            } if fight else None
            prior = str(read.get('result') or '')
            if regrade and prior and prior == result:
                continue                      # already correct, leave it
            if regrade and prior and prior != result:
                print(f'  ↻ {gd} id={read["id"]} {prior} -> {result}')
            if dry_run:
                print(f'  [DRY] {gd} id={read["id"]} side={read.get("call_side")} winner={actual and actual.get("winner")} → {result}')
                graded += 1
                continue
            pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{read["id"]}',
                                headers=H_WRITE,
                                json={'result': result,
                                      'actual_outcome': actual,
                                      'resolved_at': datetime.now(timezone.utc).isoformat()},
                                timeout=15)
            if pr.status_code in (200, 204):
                graded += 1
            else:
                print(f'  patch {read["id"]}: {pr.status_code} {pr.text[:120]}')
        if graded or reads:
            print(f'  [{gd}] graded {graded}/{len(reads)} UFC jerry_reads')
        total += graded
    print(f'\n=== graded {total} UFC jerry_reads across {len(dates)} date(s) ===')
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--backfill-days', type=int, default=60)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--regrade', action='store_true',
                   help='re-settle rows that already have a result')
    args = p.parse_args()
    run(game_date=args.date, backfill_days=args.backfill_days,
        dry_run=args.dry_run, regrade=args.regrade)


if __name__ == '__main__':
    main()
