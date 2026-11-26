#!/usr/bin/env python3
"""Run the guard unit tests. Exits non-zero if any case fails.

== WHY THIS EXISTS (2026-11-16) ===========================================
ANDY: "for naythign that we just dfound it needs to be fixed so it stops
fucking ahppening, same shit conotinue to ahpen day after day this is
unsat".

He is right, and this file is the answer to the "stops happening" half.
Measured today: **no unit test runs in CI anywhere in this repo.** Every
workflow step is a pipeline script or a detector against live data, so a
guard can be silently wrong for weeks and only the symptom shows up —
which is exactly how a bare spread label reached the public record three
separate times (2026-10-31, 2026-11-08, 2026-11-16) while
`public_receipt.unactionable`, the guard written to stop it, sat there
being asked the wrong question.

Live-data detectors cannot cover this. They can only see what today's
board happens to contain, so a parser that mishandles "Cincinnati
Bearcats ML" is invisible until Cincinnati plays. Tests pin the
adversarial cases that production rarely produces, and the two bugs they
caught WHILE BEING WRITTEN make the point:
  * `_AFTER_MARKET_LINE_RE` recovered a **55-point spread** out of
    "Golden State Warriors ATS (Jerry 55/100)" — under _LINE_SANITY_MAX,
    so nothing downstream would have refused it.
  * `_bet_market` read a bare ±1.5 as a puckline on sports that have none.

FAST AND OFFLINE BY DESIGN for the guard tests, so this can gate the card
build rather than run after it. `published_positions` needs the DB for its
live lookup, but its self-test covers only the pure label parser and the
`opposing` comparison, so it runs anywhere.

Named explicitly rather than glob-discovered: this directory also holds
`test_fade_jerry_agreement.py` and `test_sor_fade_vs_fav_control.py`,
which are measurement studies against live data, not unit tests. Add a
line here when you add a test.

    python run_guard_tests.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: (label, argv). Each must exit 0 on pass and non-zero on failure.
SUITES = [
    ('receipt line guard (public_receipt)',
     [sys.executable, 'test_receipt_line_guard.py']),
    ('published positions / label parser',
     [sys.executable, 'published_positions.py']),
    # The card's bet-identity keys and the CARD_FLOOR refill ladder. Pins
    # the 2026-11-24 duplicate (one bet, two slots, the card's only winner
    # counted twice) and the two-stage floor, whose job is to keep the
    # DotD/prop caps from either cutting the card off or quietly undoing
    # themselves. Offline; verified to import with .env moved aside.
    ('card dedupe keys + CARD_FLOOR ladder',
     [sys.executable, 'test_card_floor_and_dedupe.py']),
    # Wager identity in public_receipts and the write screen both scheduled
    # inserters share. Pins the 2026-11-24 potd/total pair as ONE bet, the
    # 2026-10-03 two-games-one-label pair as TWO, and that a VOID row no
    # longer licenses an endless re-mint. Offline, pure functions only.
    ('receipt bet identity + write screen',
     [sys.executable, 'test_receipt_bet_identity.py']),
    # The LR shadow must be RECORDED even when the model abstains, or the
    # POTD's LR-confidence gate reads None and admits the candidate
    # unchecked -- NBA spreads were blind on 23 of 25. And a recorded
    # ~0.50 must not be read as a 0.50 verdict, or the gate refuses every
    # abstention. Both halves pinned here; stubs the model, writes nothing.
    ('LR shadow recorded on abstention',
     [sys.executable, 'test_lr_shadow_recorded.py']),
    # A prop may only reach the card if its game is still to be played.
    # On 2026-11-26 two of four card items were NBA props on games that
    # had finished the night before, on a day the NBA did not play: a
    # -4h EDT constant in nba_generate_props put 210 rows on the wrong
    # slate date, and both card readers trusted that column. Pins the
    # writer's date math and the readers' slate check, including that an
    # UNREADABLE slate passes rows rather than emptying the prop path.
    ('prop slate guard + ET slate dates',
     [sys.executable, 'test_prop_slate_guard.py']),
    # An NCAAB result row must carry `season` or it is in NO team record:
    # the 2026-11-20 fix derived the label and BOTH write paths dropped it,
    # so 41 scored games went NULL on every date since that commit. Also
    # pins that the haslam XML feed is entity-decoded at the source --
    # 'Texas A&amp;M' and 18 other spellings were orphan keys, and the
    # panel ran 21 teams on 2 of 3 rating systems. Offline; the live alias
    # half is asserted by audit_ncaab_name_integrity.py.
    ('NCAAB season + feed names',
     [sys.executable, 'test_ncaab_season_and_names.py']),
    # A signal's bar must be the price it FIRES at. Below 80% price
    # coverage the tier bar fell to a cross-sport ml constant (58.33%,
    # measured on MLB/NHL/NCAAF receipts), which credited
    # ncaab_home_efficiency_edge with +24.78pp where its own prices say
    # +2.97pp, and gave ncaab_confluence_home weight 0.670 while it sat
    # 1.19pp BELOW its own price. Pins the partial-coverage floor, that a
    # partial bar is never STORED, and that nothing is retired.
    ('partial price bar (signal bar vs cross-sport constant)',
     [sys.executable, 'test_partial_price_bar.py']),
]


def main() -> int:
    print(f'=== guard tests · {len(SUITES)} suite(s) ===')
    failed = []
    for label, argv in SUITES:
        try:
            p = subprocess.run(argv, cwd=HERE, capture_output=True,
                               text=True, timeout=180,
                               env={**os.environ,
                                    'PYTHONIOENCODING': 'utf-8'})
        except Exception as e:                                 # noqa: BLE001
            print(f'  !! {label}: could not run ({type(e).__name__}: {e})')
            failed.append(label)
            continue
        ok = p.returncode == 0
        print(f'  {"OK  " if ok else "FAIL"} {label}')
        tail = (p.stdout or '') + (p.stderr or '')
        if not ok:
            failed.append(label)
            for ln in tail.strip().splitlines()[-40:]:
                print(f'       {ln}')
        else:
            last = [ln for ln in tail.strip().splitlines() if ln.strip()]
            if last:
                print(f'       {last[-1]}')

    if failed:
        print(f'\n*** {len(failed)} GUARD SUITE(S) FAILED: '
              f'{", ".join(failed)} ***')
        print('    A guard test failing means a guard is now wrong, not '
              'that the test is stale. Fix the guard.')
        return 1
    print('\nall guard suites pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
