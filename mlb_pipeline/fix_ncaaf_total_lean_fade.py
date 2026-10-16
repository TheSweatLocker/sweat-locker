#!/usr/bin/env python3
"""Invert the NCAAF total signals: our projection's lean is a fade.

== 2026-10-16 - WHY ==
ANDY approved this directly ("do it"), and his own standing rule is the basis:
"<45% buckets -> FADE the other side" (feedback_fade_not_suppress_803). He was
also explicit that suppression is not an acceptable answer. So these signals
keep firing — on the other side.

MEASURED, NCAAF, temporal split, test slice, ATS at -110:

    follow projection, any delta   60-74  44.8%  ROI -14.5%  n=134
    follow projection |delta|>=2.0 35-47  42.7%  ROI -18.5%  n=82
    follow projection |delta|>=3.5 20-27  42.6%  ROI -18.8%  n=47
    follow projection |delta|>=5.0 12-16  42.9%  ROI -18.2%  n=28
    follow projection |delta|>=7.0  5-6   45.5%  ROI -13.2%  n=11

    FADE projection |delta|>=2.0   47-35  57.3%  ROI  +9.4%  n=82
    FADE projection |delta|>=3.5   27-20  57.4%  ROI  +9.7%  n=47
    FADE projection |delta|>=5.0   16-12  57.1%  ROI  +9.1%  n=28

    controls: always OVER 49.3% ROI -6.0% | always UNDER 50.7% ROI -3.1%
    by direction: our OVER lean 46.0% (-12.2%), our UNDER lean 41.2% (-21.4%)

Four independent thresholds all land at ~42.7% follow / ~57.3% fade, both lean
directions lose when followed, and fading beats BOTH blind controls by ~12
points. The projection is also less accurate than the market (MAE 12.977 vs
12.114) and its deviation slope is noise (-0.333, CI[-0.847,+0.159]) — so there
is no information in the magnitude, only in the sign, and the sign is backwards.

THE THREE SIGNALS ALL SIDE *WITH* THE PROJECTION. The word "contradicts" in two
of their names refers to the projection contradicting the BOOK; the side they
emit follows us:

  ncaaf_projected_total                     |delta|>=2.5, side = OVER if
                                            projected>close else UNDER
  ncaaf_projection_contradicts_total_over   close-projected>=4  -> UNDER
                                            (we are low, so this follows us)
  ncaaf_projection_contradicts_total_under  projected-close>=4  -> OVER
                                            (we are high, so this follows us)

CAVEAT STATED PLAINLY, NOT BURIED: no single cut clears 95%. follow|delta|>=2.0
is 42.7% with CI[32.5,53.5], whose upper bound (53.5%) sits just above the
52.38% breakeven, and the fade's CI floor is 46.5%, below it. What makes this
actionable is not one interval but the consistency — four thresholds, both
directions, and worse than two free controls. Treat the fade as the better of
two known-bad options, not as a proven edge, and let the forward record decide
whether the threshold should move.

Reversible: --revert restores the original side expressions, which are recorded
verbatim below.

USAGE
    python fix_ncaaf_total_lean_fade.py --dry-run
    python fix_ncaaf_total_lean_fade.py --apply
    python fix_ncaaf_total_lean_fade.py --revert
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: signal_key -> (original side_expr, faded side_expr)
FLIPS = {
    'ncaaf_projected_total': (
        '"OVER" if float(ctx.projected_total) > float(ctx.close_total) '
        'else "UNDER"',
        '"UNDER" if float(ctx.projected_total) > float(ctx.close_total) '
        'else "OVER"'),
    'ncaaf_projection_contradicts_total_over': ('"UNDER"', '"OVER"'),
    'ncaaf_projection_contradicts_total_under': ('"OVER"', '"UNDER"'),
}
NOTE = ('2026-10-16 FADED: the NCAAF total projection lean is 42.7% / '
        '-18.5% ROI at |delta|>=2.0 (n=82) and loses at every threshold '
        'tested (44.8% any delta n=134; 42.6% >=3.5; 42.9% >=5.0), both lean '
        'directions losing, while fading it returns +9.4% and beats both '
        'blind controls (always OVER -6.0%, always UNDER -3.1%). Side '
        'expression INVERTED per feedback_fade_not_suppress_803. No single '
        'CI clears 95%; the consistency across four thresholds is the basis. '
        'Revert with fix_ncaaf_total_lean_fade.py --revert.')


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()

    for key, (orig, faded) in FLIPS.items():
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': '*', 'signal_key': f'eq.{key}',
                                 'sport': 'eq.NCAAF'}, timeout=120)
        rows = r.json() if r.status_code in (200, 206) else []
        if not rows:
            print(f'{key}: NOT FOUND (HTTP {r.status_code})')
            continue
        cur = rows[0]
        have = str(cur.get('side_expr') or '')
        want = orig if a.revert else faded
        other = faded if a.revert else orig
        print(f'\n{key}  enabled={cur.get("enabled")}')
        print(f'  current: {have[:92]}')
        print(f'  target : {want[:92]}')
        if have.strip() == want.strip():
            print('  already in the target state — leaving it alone')
            continue
        if have.strip() != other.strip():
            print(f'  ⚠ side_expr is neither the original nor the faded form. '
                  f'REFUSING to overwrite an expression I do not recognise — '
                  f'someone changed it since this script was written.')
            continue
        if a.dry_run:
            print('  DRY RUN — would rewrite side_expr')
            continue
        patch = {'side_expr': want}
        if 'description' in cur:
            base = str(cur.get('description') or '')
            patch['description'] = (
                (NOTE + ' | PRIOR: ' + base)[:1500] if not a.revert
                else base.replace(NOTE, '').strip(' |')[:1500])
        w = requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_WRITE,
                           params={'signal_key': f'eq.{key}',
                                   'sport': 'eq.NCAAF'},
                           data=json.dumps(patch), timeout=120)
        if w.status_code not in (200, 204):
            print(f'  WRITE FAILED {w.status_code}: {w.text[:200]}')
            continue
        print('  side_expr rewritten')

    print('\nREAD BACK:')
    for key in FLIPS:
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': 'signal_key,enabled,side_expr',
                                 'signal_key': f'eq.{key}',
                                 'sport': 'eq.NCAAF'}, timeout=120)
        for x in (r.json() if r.status_code in (200, 206) else []):
            print(f'  {x.get("signal_key"):<44} enabled={x.get("enabled")}  '
                  f'side={str(x.get("side_expr"))[:58]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
