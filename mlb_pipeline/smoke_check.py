#!/usr/bin/env python3
"""Run the paths a commit touches BEFORE committing. Writes nothing.

== 2026-10-18 - WHY THIS EXISTS ==
My commit b530ce5e took the Sweat Card down for an entire NFL Sunday with a
one-line scope bug:

    seen_play_signatures |= sig     # augmented assignment -> rebinds the name
                                    # -> local to add() -> the READ two lines
                                    # above raises UnboundLocalError

The code was syntactically valid, imported cleanly, and passed the check I
wrote for it. I had exercised _play_signature, the function I WROTE, pulled
out of the module and fed five label cases. I never called add(), the
function I CHANGED. curate_top_8 died on the first pick it ever appends, so
there was no card at all - and because both build_card() callers wrap it in
try/except, the jobs logged "not fatal" and kept going. The outage was
invisible in the logs and total in the app.

So this does the one thing that would have caught it: it RUNS the code.

Three rules, each from a trap already paid for:
  * every check must EXECUTE the integration, not a function in isolation;
  * no check may pass vacuously - a check that ran against 0 rows is a
    wiring error, not a pass, so each asserts it actually did work;
  * it writes NOTHING, so it is safe against live data at any hour.

USAGE
    python smoke_check.py
    python smoke_check.py --verbose
"""
from __future__ import annotations

import sys
import traceback

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

VERBOSE = '--verbose' in sys.argv
RESULTS = []

CRITICAL_MODULES = [
    'generate_sweat_card', 'game_context', 'public_receipt', 'market_line',
    'defensive_gates', 'predict_runs', 'potd_publish_lock',
    'void_orphaned_picks', 'generate_jerry_synthesis',
]


def record(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, detail or ''))
        print(f'  PASS  {name}' + (f'  - {detail}' if detail else ''))
    except Exception as e:                                     # noqa: BLE001
        RESULTS.append((name, False, f'{type(e).__name__}: {e}'))
        print(f'  FAIL  {name}  - {type(e).__name__}: {e}')
        if VERBOSE:
            traceback.print_exc()


def c_imports():
    """A module-level error means every CI step using it dies instantly."""
    import importlib
    bad = []
    for m in CRITICAL_MODULES:
        try:
            importlib.import_module(m)
        except Exception as e:                                 # noqa: BLE001
            bad.append(f'{m} ({type(e).__name__}: {e})')
    if bad:
        raise RuntimeError('; '.join(bad))
    return f'{len(CRITICAL_MODULES)} modules'


def c_curate():
    """The path that broke. Driven with REAL props so add() genuinely runs."""
    import generate_sweat_card as G
    props, used_date = [], None
    probes = G.sb_get('mlb_pipeline_props', {
        'select': 'game_date', 'order': 'game_date.desc', 'limit': '400'}) or []
    for probe in probes:
        d = str(probe.get('game_date'))[:10]
        if d == used_date:
            continue
        used_date = d
        rows = G.sb_get('mlb_pipeline_props', {
            'game_date': f'eq.{d}', 'select': '*', 'limit': '400'}) or []
        if [p for p in rows if str(p.get('tier')) in ('PRIME', 'STRONG', 'LEAN')]:
            props = rows
            break
    if not props:
        raise RuntimeError('no props with a publishable tier - cannot exercise '
                           'add(); treat as a WIRING ERROR, not a pass')
    out = G.curate_top_8([], props, None, None, [], gate_window='off')
    if not isinstance(out, list):
        raise TypeError(f'curate_top_8 returned {type(out).__name__}, not list')
    if not out:
        raise RuntimeError(f'0 picks from {len(props)} props on {used_date} - '
                           f'add() never succeeded, so this proved nothing')
    return f'{len(out)} picks from {len(props)} props on {used_date}'


def c_dedupe():
    """A POTD and its own prop must collapse; two games must NOT."""
    import re
    import textwrap
    import generate_sweat_card as G
    src = open(G.__file__, encoding='utf-8').read()
    if '_PROP_LABEL_RE' not in src:
        raise RuntimeError('_PROP_LABEL_RE missing - dedupe fix not present')
    ns = {'re': re}
    i = src.index('_PROP_LABEL_RE = re.compile')
    exec(src[i:src.index('def curate_top_8')], ns)             # noqa: S102
    k = src.index('    def _play_signature(pick):')
    exec(textwrap.dedent(src[k:src.index('    def _is_ml_pick(pick):')]), ns)  # noqa: S102
    sig = ns['_play_signature']
    game = 'Tampa Bay Rays @ Cleveland Guardians'
    a = sig({'type': 'POTD', 'sport': 'MLB', 'game': game,
             'label': 'Nick Martinez Over 3.5 Hits Allowed (Jerry 82/100)'})
    b = sig({'type': 'prop_ha_over', 'sport': 'MLB', 'game': game,
             'label': 'Nick Martinez Over 3.5 Hits Allowed'})
    if not (a and b and (a & b)):
        raise RuntimeError(f'POTD and its prop did NOT collapse: {a} vs {b}')
    t1 = sig({'type': 'total', 'label': 'Over 7.5', 'game': 'Cubs @ Reds'})
    t2 = sig({'type': 'total', 'label': 'Over 7.5', 'game': 'Rays @ Guardians'})
    if t1 & t2:
        raise RuntimeError('two games sharing a total line COLLAPSED')
    return 'duplicate collapses, distinct games stay apart'


def c_recap():
    """Read-only. A recap with no date means the chain is broken."""
    import generate_sweat_card as G
    r = G.fetch_yesterday_recap()
    if not isinstance(r, dict) or not r.get('date'):
        raise RuntimeError(f'no dated recap: {r!r}')
    return (f"{r['date']} · {len(r.get('top_8') or [])} picks · "
            f"{r.get('top_8_summary')}")


def c_receipts():
    """Shape receipts off the LIVE card without writing them."""
    import json
    import generate_sweat_card as G
    from public_receipt import sweat_card_rows
    today = G.today_et()
    rows = G.sb_get('jerry_cache', {'cache_key': f'eq.sweat_card_{today}',
                                    'select': 'data'})
    if not rows:
        return f'no card for {today} yet - nothing to shape'
    data = rows[0].get('data') or {}
    if isinstance(data, str):
        data = json.loads(data)
    out = sweat_card_rows(data, today)
    npicks = len(data.get('top_8') or []) + len(data.get('football_picks') or [])
    if npicks and not out:
        raise RuntimeError(f'card has {npicks} picks but shaped 0 receipts')
    return f'{len(out)} receipts from {npicks} picks'


def main() -> int:
    print('=== smoke_check - writes nothing ===\n')
    for name, fn in (
            ('imports: every critical module', c_imports),
            ('curate_top_8: composes AND actually adds picks', c_curate),
            ('dedupe: POTD + its own prop collapse to one', c_dedupe),
            ('fetch_yesterday_recap: returns a dated recap', c_recap),
            ('sweat_card_rows: shapes receipts off the live card', c_receipts)):
        record(name, fn)
    bad = [n for n, ok, _d in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed')
    if bad:
        print('FAILED: ' + ', '.join(bad))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
