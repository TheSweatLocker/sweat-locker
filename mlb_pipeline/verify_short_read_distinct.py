#!/usr/bin/env python3
"""Assert short_read is a SUMMARY, not the first 480 characters of long_read.

== 2026-11-24 . WHY THIS EXISTS ==
Andy: the reads are hard to read and comprehend.

Measured across jerry_reads, short_read was a verbatim PREFIX of long_read on

    NCAAB 99.9% (748/749)   NBA 99.6%   NHL 99.1%   NCAAF 97.7%
    NFL    8.9%             MLB  5.6%   UFC  0%

Root cause was in the prompt, not the writer. Of nine active
prompt_templates.game_read_rules rows, only NFL's and MLB's mentioned
`---SHORT---`; for the other four, 0 of 300 sampled narratives carried the
section markers, so parse_synthesis fell through to its free-form branch --
`short = raw[:480]; long_ = raw` -- which is a prefix BY CONSTRUCTION. The
sports with a real summary were exactly the sports whose prompt asked for
one.

The four templates now carry the `---SHORT---` / `---LONG---` contract. This
script is how we know the model actually honours it: the fix was shipped
WITHOUT end-to-end verification, because the local ANTHROPIC_API_KEY returns
401 and only CI holds the working secret.

A SECOND PATH CAN RECREATE THE PREFIX even when the model complies:
upsert_jerry_read discards a short_read under 100 characters and rebuilds it
from the first two sentences of long_read. 25-45 words clears that, but a
terse response lands back on the old behaviour -- so this measures the
PUBLISHED result, not the model's output.

Threshold is deliberately loose. The question is "did the contract land at
all", not "is every read perfect": a sport sitting at 99% is broken, a sport
at 20% is working. A checker that cries wolf gets muted
(feedback_a_detector_must_know_the_intentional_case).

USAGE
    python verify_short_read_distinct.py --sport NCAAB
    python verify_short_read_distinct.py                  # every sport
    python verify_short_read_distinct.py --max-prefix-pct 60 --days 3
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import date, timedelta

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
SPORTS = ('MLB', 'NFL', 'NCAAF', 'NBA', 'NHL', 'NCAAB')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                                      # noqa: BLE001
        pass


def _page(params: dict) -> list:
    out, last = [], None
    hdr = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['order'] = 'id.asc'
        if last is not None:
            p['id'] = f'gt.{last}'
        r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=hdr, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! jerry_reads HTTP {r.status_code} {r.text[:160]}')
            return out
        d = r.json()
        if not d:
            return out
        out += d
        if len(d) < 1000:
            return out
        last = d[-1]['id']


def _norm(s) -> str:
    """Whitespace-insensitive, so a reflowed prefix still counts as one."""
    return ' '.join(str(s or '').split())


def is_prefix(short, long_) -> bool:
    s, lg = _norm(short), _norm(long_)
    if not s or not lg or len(s) < 40:
        return False
    return lg.startswith(s[:min(len(s), 120)])


def audit(sport: str, days: int, verbose: bool) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = _page({'select': 'id,game_date,generated_at,short_read,long_read',
                  'sport': f'eq.{sport}',
                  'generated_at': f'gte.{since}T00:00:00+00:00'})
    both = [r for r in rows
            if _norm(r.get('short_read')) and _norm(r.get('long_read'))]
    pref = [r for r in both if is_prefix(r['short_read'], r['long_read'])]
    pct = (100.0 * len(pref) / len(both)) if both else None
    print(f'  {sport:6s} reads generated since {since}: {len(rows):4d}  '
          f'with both fields: {len(both):4d}  '
          + (f'short_read is a PREFIX on {len(pref):4d} ({pct:5.1f}%)'
             if both else '(nothing to measure)'))
    if verbose and pref:
        r = pref[0]
        print(f'      e.g. id={r["id"]}  short={_norm(r["short_read"])[:110]!r}')
    return {'sport': sport, 'n': len(both), 'prefix': len(pref), 'pct': pct}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=SPORTS)
    ap.add_argument('--days', type=int, default=2)
    ap.add_argument('--max-prefix-pct', type=float, default=50.0)
    ap.add_argument('--min-n', type=int, default=10,
                    help='below this, report and pass -- too few to judge')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--warn-only', action='store_true')
    a = ap.parse_args()

    if not SB or not KEY:
        # Say so and fail: a silent skip is how a check reports success while
        # measuring nothing (feedback_a_green_step_that_writes_nothing).
        print('  ! SUPABASE_URL / key missing from the environment — '
              'cannot verify')
        return 1

    print(f'=== verify_short_read_distinct · {date.today().isoformat()} · '
          f'last {a.days}d of generations ===')
    rows = [audit(s, a.days, a.verbose)
            for s in ([a.sport] if a.sport else SPORTS)]

    bad = [r for r in rows
           if r['n'] >= a.min_n and r['pct'] is not None
           and r['pct'] > a.max_prefix_pct]
    thin = [r for r in rows if 0 < r['n'] < a.min_n]
    if thin:
        print(f'\n  · {", ".join(r["sport"] for r in thin)}: under '
              f'{a.min_n} reads, reported but not judged')
    if not bad:
        print(f'\n  >> every sport with n >= {a.min_n} is under the '
              f'{a.max_prefix_pct:.0f}% prefix threshold.')
        return 0
    print(f'\n  >> short_read is still a TRUNCATION, not a summary:')
    for r in bad:
        print(f'     {r["sport"]:6s} {r["pct"]:5.1f}% of {r["n"]} reads '
              f'(threshold {a.max_prefix_pct:.0f}%)')
    print('     Check that prompt_templates.game_read_rules for that sport '
          'still\n     carries the ---SHORT--- contract, and that the '
          'narrative in\n     jerry_cache actually contains the markers. '
          'Backups of the\n     pre-change templates: docs/prompt_templates/')
    return 0 if a.warn_only else 1


if __name__ == '__main__':
    raise SystemExit(main())
