#!/usr/bin/env python3
"""audit_read_conviction_coverage — a published read that makes a CALL must
carry a conviction, on every sport.

WHY THIS EXISTS (2026-11-17)
============================
NCAAB's 4-lens resolver stopped stamping `ncaab_game_context.primary_play`
and nobody noticed for ten days, because the only visible symptom was a
NULL.

`sync_jerry_reads_from_ctx` is the ONLY writer of `jerry_reads.conviction`
and it reads `primary_play['conviction']` (its line ~409). When the
resolver does not run, `ncaab_game_context.py` has already written a
PRELIMINARY primary_play -- the cap/floor shape, with no `conviction`, no
`score` and no `_engine` -- so the sync writes `conviction: None`.

Conviction is the input EVERY gate uses: the POTD threshold, the Sweat
Card tier floor, The Sharp. A NULL does not fail a gate, it silently fails
to reach one, so the biggest board in the book went invisible rather than
loud:

    resolver-stamped ctx rows   100% on 11-04..11-06  ->  0 of 43 on 11-17
    NULL conviction on calls    1 of 37 on 11-07      ->  32 of 32 on 11-17

NCAAB was the only sport with a single NULL; every other sport was at
zero. One number, printed once a night, would have caught it on 11-07.

WHAT COUNTS AS A FINDING
========================
A row where `call_market` is a real market AND `call_side` is set -- i.e.
the app renders a playable call -- and `conviction IS NULL`.

Deliberately NOT a finding:
  - `call_market='pass'` / no side. That is a withdrawal, and the two
    refusal paths in the sync produce exactly that shape on purpose.
  - `conviction = 0`. That is a WITHDRAWAL too, not a low score
    ([[feedback_conviction_zero_is_a_withdrawal_not_a_low_score]]), and
    COVERAGE picks are pinned to 0 by the tier cap by design. Counted and
    printed, never a finding.
  - A sport with no reads at all. An empty slate is not a defect
    ([[feedback_a_detector_must_know_the_intentional_case]]).

EXIT CODES (detector contract, see .github/scripts/run_step.sh --detector)
  0  every called read on every sport carries a conviction
  1  FINDING: at least one called read has a NULL conviction
  2  could not measure (read failed) -- never reported as a clean pass
     ([[feedback_zero_rows_is_a_wiring_error]])

USAGE
    python audit_read_conviction_coverage.py
    python audit_read_conviction_coverage.py --date 2026-11-17
    python audit_read_conviction_coverage.py --days 3
"""
from __future__ import annotations
import argparse, os, sys
import datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY or '', 'Authorization': f'Bearer {KEY or ""}'}

#: markets that render as a playable call in the app. Anything else --
#: 'pass', '', None -- is a withdrawal and carries no conviction by design.
CALL_MARKETS = {'ml', 'rl', 'spread', 'total'}


def et_today() -> str:
    try:
        from sweat_dates import et_today as _e
        return _e()
    except Exception:
        # Never a hardcoded -4/-5: that is wrong twice a year
        # ([[feedback_hardcoded_et_offset_breaks_in_est]]).
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo('America/New_York')).date().isoformat()


def read_rows(d0: str, d1: str) -> list | None:
    """None means the READ failed -- distinct from 'no rows'."""
    try:
        r = requests.get(
            f'{SB}/rest/v1/jerry_reads',
            params={'game_date': f'gte.{d0}',
                    'and': f'(game_date.lte.{d1})',
                    'select': 'sport,game_id,game_date,call_market,'
                              'call_side,call_line,call_text,conviction',
                    'limit': '5000'},
            headers=H, timeout=30,
        )
    except Exception as e:                                    # noqa: BLE001
        print(f'  !! jerry_reads read raised: {e!r}', file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f'  !! jerry_reads read {r.status_code}: {r.text[:200]}',
              file=sys.stderr)
        return None
    return r.json()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to ET today')
    p.add_argument('--days', type=int, default=1,
                   help='how many days forward from --date (default 1)')
    args = p.parse_args()

    if not SB or not KEY:
        print('!! SUPABASE_URL / key not set - cannot measure',
              file=sys.stderr)
        return 2

    d0 = args.date or et_today()
    d1 = (dt.date.fromisoformat(d0)
          + dt.timedelta(days=max(1, args.days) - 1)).isoformat()
    print(f'=== audit_read_conviction_coverage · {d0}'
          f'{"" if d0 == d1 else f"..{d1}"} ===')

    rows = read_rows(d0, d1)
    if rows is None:
        print('!! could not read jerry_reads - reporting UNMEASURED, '
              'not a pass', file=sys.stderr)
        return 2

    by_sport: dict[str, dict] = {}
    offenders: list[tuple] = []
    for x in rows:
        sport = x.get('sport') or '?'
        s = by_sport.setdefault(sport, {'reads': 0, 'called': 0,
                                        'null': 0, 'zero': 0,
                                        'withdrawn': 0})
        s['reads'] += 1
        mkt = str(x.get('call_market') or '').lower()
        if mkt not in CALL_MARKETS or not x.get('call_side'):
            s['withdrawn'] += 1
            continue
        s['called'] += 1
        conv = x.get('conviction')
        if conv is None:
            s['null'] += 1
            offenders.append((sport, x.get('game_date'), x.get('game_id'),
                              mkt, x.get('call_side'), x.get('call_line'),
                              str(x.get('call_text') or '')[:44]))
        elif conv == 0:
            s['zero'] += 1

    if not by_sport:
        print('  no reads on this window - nothing to audit '
              '(an empty slate is not a defect)')
        return 0

    print(f'\n  {"sport":<8}{"reads":>6}{"called":>8}'
          f'{"NULL conv":>11}{"conv 0":>8}{"withdrawn":>11}')
    for sport in sorted(by_sport):
        s = by_sport[sport]
        print(f'  {sport:<8}{s["reads"]:>6}{s["called"]:>8}'
              f'{s["null"]:>11}{s["zero"]:>8}{s["withdrawn"]:>11}'
              f'{"   <<<" if s["null"] else ""}')

    if not offenders:
        print('\n  ✓ every called read carries a conviction')
        return 0

    print(f'\n  🚨 {len(offenders)} called read(s) with NULL conviction.')
    print('     A NULL does not FAIL a gate, it never REACHES one - so')
    print('     these picks are invisible to the POTD threshold, the')
    print('     Sweat Card tier floor and The Sharp.')
    print('     Usual cause: the sport\'s primary_play resolver did not')
    print('     run, so ctx holds a preliminary pick with no conviction')
    print('     and sync_jerry_reads_from_ctx wrote None.')
    for o in offenders[:25]:
        print(f'       {o[0]:<6} {o[1]}  {o[3]}/{o[4]} '
              f'{"" if o[5] is None else o[5]}  {o[6]}')
    if len(offenders) > 25:
        print(f'       ... and {len(offenders) - 25} more')
    return 1


if __name__ == '__main__':
    sys.exit(main())
