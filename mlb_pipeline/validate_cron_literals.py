"""Assert every cron string a workflow COMPARES against still exists in it.

== WHY ====================================================================
Five workflows dispatch on the exact cron expression that fired, via
`github.event.schedule` -- the mode `case` in ncaab/ncaaf/nfl/ufc,
daily_card's scorecard gate, ncaab's kenpom and card_only tests. That design
is deliberate: `date -u +%H` was a lottery once GitHub started running these
7-10h late (feedback_cron_mode_from_runtime_clock).

But it makes the cron string LOAD-BEARING, and a stale literal fails
silently -- the `case` falls through to its default and an `if` takes the
other branch, with no error anywhere.

That is not hypothetical. Run for the first time on 2026-11-03 it found:

    ncaaf_pipeline.yml:413
      if [ "${{ github.event.schedule }}" = "0 12 * * 6" ]

against a Saturday cron staggered to '15 12 * * 6' on **2026-09-08**. The
test had been FALSE every Saturday for two months, so the Saturday run did
the FULL externals pull instead of the intended --refresh subset.

Worth running after ANY cron edit, and cheap enough to wire into CI.

    python validate_cron_literals.py          # exit 1 if a literal is stale
    python validate_cron_literals.py --minutes  # also report the minute spread
"""
from __future__ import annotations

import collections
import glob
import os
import re
import sys

WF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  '.github', 'workflows')

#: (file, cron) pairs that are deliberately NOT in the on: block. Each needs
#: a reason, because the default assumption is that a mismatch is a bug.
ALLOW = {
    ('mlb_pipeline.yml', '0 10 * * *'):
        'legacy heartbeat slot kept so pre-2026-09-28 rows still map',
    ('cross_sport_lines.yml', '0 10 * * *'):
        'prose in a comment; this workflow declares no crons at all',
}


def active_crons(src: str) -> list:
    """Cron expressions actually declared -- commented-out lines excluded.

    A first pass counted a commented-out `keep_alive` hourly cron and
    overstated the :00 share by 24 firings.
    """
    out = []
    for line in src.split('\n'):
        s = line.strip()
        if s.startswith('#'):
            continue
        m = re.match(r"-?\s*cron:\s*'([^']+)'", s)
        if m:
            out.append(m.group(1))
    return out


def compared_crons(src: str) -> set:
    """Cron strings this file tests against, in any of the three shapes used."""
    found = set()
    # if: github.event.schedule == '0 20 * * *'
    found |= set(re.findall(r"github\.event\.schedule\s*==\s*'([^']+)'", src))
    # if [ "${{ github.event.schedule }}" = "0 20 * * *" ]
    found |= set(re.findall(
        r'github\.event\.schedule\s*\}\}"\s*\]?\s*=\s*"([^"]+)"', src))
    # case "$SCHED" in  '0 11 * * *') ...
    if 'SCHED=' in src or 'CRON_SLOT=' in src:
        found |= set(re.findall(
            r"^\s*'([\d*/,\- ]+)'\)", src, re.M))
    return {f for f in found if len(f.split()) == 5}


def minute_spread() -> tuple:
    def mins(c):
        mn = c.split()[0]
        if mn.startswith('*/'):
            return list(range(0, 60, int(mn[2:])))
        if mn == '*':
            return list(range(60))
        return [int(x) for x in mn.split(',')]
    counter = collections.Counter()
    total = entries = 0
    for f in sorted(glob.glob(os.path.join(WF, '*.yml'))):
        with open(f, encoding='utf-8') as fh:
            for c in active_crons(fh.read()):
                entries += 1
                for m in mins(c):
                    counter[m] += 1
                    total += 1
    return counter, total, entries


def main() -> int:
    files = sorted(glob.glob(os.path.join(WF, '*.yml')))
    if not files:
        print(f'!! no workflows found under {WF} — refusing to pass')
        return 1
    bad = 0
    checked = 0
    for f in files:
        name = os.path.basename(f)
        with open(f, encoding='utf-8') as fh:
            src = fh.read()
        declared = set(active_crons(src))
        compared = compared_crons(src)
        if not compared:
            continue
        checked += 1
        for c in sorted(compared):
            if c in declared:
                continue
            why = ALLOW.get((name, c))
            if why:
                print(f'  {name}: {c!r} not declared — ALLOWED ({why})')
                continue
            bad += 1
            print(f'  !! {name}: compares against {c!r}, which is NOT in its '
                  f'on: block')
            print(f'     declared: {sorted(declared)}')
            print(f'     a stale literal falls through SILENTLY — fix the '
                  f'comparison or restore the cron')
    print(f'\n{len(files)} workflows, {checked} with schedule-dependent logic, '
          f'{bad} stale literal(s)')
    if '--minutes' in sys.argv:
        counter, total, entries = minute_spread()
        print(f'\n{entries} active cron entries, {total} expanded firings')
        print(f'minute :00 share = {counter[0]}/{total} = '
              f'{100.0 * counter[0] / total:.1f}%')
        print('busiest minutes:', counter.most_common(8))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
