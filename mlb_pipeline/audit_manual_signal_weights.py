# -*- coding: utf-8 -*-
"""Audit: every hand-set weight, beside what the measurement now says.

== WHY THIS EXISTS (2026-11-26) =========================================
Shipped with 20261126a and set_signal_weight.py. A manual weight override
is the one number in the engine that no nightly job can correct, so the
only thing standing between it and becoming a dead value pricing live
picks is this audit.

WHAT IT CHECKS, and each is a trap this repo has already paid for:

  EXPIRED / LAPSING. weight_review_at is enforced in
    ensemble_scorer._manual_weight_of -- past it the measured weight takes
    over silently. That is the safe direction, but Andy should know a lever
    he set has stopped applying, not discover it
    ([[feedback_a_dead_value_keyed_on_itself_is_immortal]]).

  THE MEASUREMENT MOVED AGAINST IT. The whole point of keeping
    recommended_weight as "what the measurement says" and manual_weight as
    "what Andy says" is that the GAP stays visible. An override set when a
    signal had n=6 deserves a second look once it has n=120
    ([[feedback_a_lag_is_not_a_disagreement]] -- a gap is information, not
    automatically a defect, so this REPORTS and does not auto-clear).

  ANTI_VALIDATED. The scorer refuses the override there, by design. An
    override sitting on an ANTI_VALIDATED row is set and not firing, which
    is the worst state to be in silently
    ([[feedback_a_lens_can_be_dark_behind_a_404]]).

  UNREACHABLE. 218 of 867 signal_sources rows cannot carry a manual weight
    at all (207 with an inline hit_rate_pct, 11 of class scenario /
    external_pick). set_signal_weight.py refuses to create these, but a row
    written before that check existed, or by hand in SQL, would read as set
    and do nothing.

  NO EXPIRY. Allowed via --no-expiry, reported every run.

EXIT CODE is 1 when any override is set-but-not-firing (expired,
ANTI_VALIDATED, unreachable) so this can gate a workflow. A healthy
override, and no overrides at all, both exit 0.

    python audit_manual_signal_weights.py
    python audit_manual_signal_weights.py --days-warn 14
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL') or ''
KEY = os.environ.get('SUPABASE_KEY') or ''
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

UNREACHABLE_CLASSES = {'scenario', 'external_pick'}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _et_today() -> str:
    from sweat_dates import et_today
    return et_today()


def _get(table: str, params: dict) -> list:
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=params,
                     timeout=30)
    if r.status_code != 200:
        print(f'  ! {table} read {r.status_code}: {r.text[:200]}')
        return []
    out = r.json()
    return out if isinstance(out, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days-warn', type=int, default=14,
                    help='flag overrides lapsing within this many days')
    a = ap.parse_args()

    if not SB or not KEY:
        print('! SUPABASE_URL / SUPABASE_KEY not set')
        return 2

    probe = requests.get(f'{SB}/rest/v1/signal_registry', headers=H,
                         params={'select': 'manual_weight', 'limit': 1},
                         timeout=20)
    if probe.status_code != 200:
        # Not a failure: the override simply does not exist yet. Saying so is
        # better than a green run that checked nothing
        # ([[feedback_a_detector_on_an_out_of_season_sport]]).
        print('signal_registry.manual_weight is absent -- migration '
              '20261126a is not applied, so NO override can be set.')
        print('Nothing to audit. This is not a failure.')
        return 0

    rows = _get('signal_registry',
                {'select': 'id,signal_name,sport,market_scope,tier,hit_rate,'
                           'sample_n,recommended_weight,baseline_pct,'
                           'price_breakeven_pct,edge_pp,manual_weight,'
                           'weight_source,weight_note,weight_set_at,'
                           'weight_set_by,weight_review_at',
                 'weight_source': 'eq.manual',
                 'order': 'sport.asc,signal_name.asc'})

    print('=' * 72)
    print('MANUAL SIGNAL WEIGHT AUDIT   %s' % _et_today())
    print('=' * 72)
    if not rows:
        print('\nno manual weight overrides are set.')
        print('The engine is running entirely on measured weights.')
        return 0

    today = _et_today()
    warn_by = (_dt.date.fromisoformat(today)
               + _dt.timedelta(days=a.days_warn)).isoformat()

    # One read of signal_sources, keyed for reachability.
    src_rows = _get('signal_sources',
                    {'select': 'signal_key,sport,class,hit_rate_pct,'
                               'weight_registry_key,enabled'})
    src_by = {}
    for r in src_rows:
        for k in (r.get('signal_key'), r.get('weight_registry_key')):
            if k:
                src_by.setdefault((k, r.get('sport')), []).append(r)

    dead, warn = [], []
    print(f'\n{len(rows)} override(s) set:\n')
    for r in rows:
        sport = r.get('sport')
        name = r.get('signal_name')
        mw = _f(r.get('manual_weight'))
        meas = _f(r.get('recommended_weight'))
        tier = str(r.get('tier') or '')
        rev = str(r.get('weight_review_at') or '')[:10]
        label = f'{sport} {name} [{r.get("market_scope")}]'

        problems, notes = [], []
        if tier == 'ANTI_VALIDATED':
            problems.append('ANTI_VALIDATED -- the scorer REFUSES this '
                            'override; it is set and not firing')
        if rev and rev < today:
            problems.append(f'EXPIRED {rev} -- the measured weight '
                            f'({meas}) is what applies now')
        elif rev and rev <= warn_by:
            notes.append(f'lapses {rev} (within {a.days_warn}d)')
        elif not rev:
            notes.append('NO EXPIRY -- never lapses on its own')

        for s in src_by.get((name, sport), []):
            cls = str(s.get('class') or '')
            if cls in UNREACHABLE_CLASSES:
                problems.append(f'UNREACHABLE: class={cls}, whose handler '
                                f'never reads signal_registry')
            if s.get('hit_rate_pct') is not None:
                problems.append('UNREACHABLE: signal_sources carries an '
                                'inline hit_rate_pct, so no registry row '
                                'is looked up')
            if s.get('enabled') is False:
                problems.append('signal_sources.enabled is false -- the '
                                'signal never fires at all')

        if mw is not None and meas is not None:
            gap = mw - meas
            if abs(gap) >= 0.25:
                notes.append(f'the measurement now says {meas:.3f}, '
                             f'{abs(gap):.3f} {"below" if gap > 0 else "above"}'
                             f' the hand value -- worth a second look')

        mark = '!!' if problems else ('  ' if not notes else ' ~')
        print(f' {mark} {label}')
        print(f'      manual={mw}   measured={meas}   tier={tier}   '
              f'hit={r.get("hit_rate")}  n={r.get("sample_n")}')
        print(f'      set {str(r.get("weight_set_at"))[:10]} by '
              f'{r.get("weight_set_by")}'
              + (f', review {rev}' if rev else ', no expiry'))
        if r.get('weight_note'):
            print(f'      "{r["weight_note"]}"')
        for p in problems:
            print(f'      !! {p}')
        for n in notes:
            print(f'      ~  {n}')
        print()
        if problems:
            dead.append(label)
        elif notes:
            warn.append(label)

    print('-' * 72)
    live = len(rows) - len(dead)
    print(f'{live} override(s) applying, {len(dead)} set-but-NOT-firing, '
          f'{len(warn)} with a note')
    if dead:
        print('\nSET BUT NOT FIRING -- these change no pick:')
        for d in dead:
            print(f'  * {d}')
        print('\nEither clear them (set_signal_weight.py --clear) or fix the '
              'cause. An override that silently does nothing is the exact '
              'defect manual_weight was built to replace.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
