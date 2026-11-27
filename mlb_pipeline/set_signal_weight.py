# -*- coding: utf-8 -*-
"""Set, clear and list HAND-SET ensemble weights on signal_registry.

== WHY THIS EXISTS (2026-11-26) =========================================
Andy: "hand wigthing stats absed on previous matchups is legit data
analytics" -- after I told him hand-setting a weight does nothing. He was
right about the analytics and I was describing a broken lever as if it were
a verdict on the method.

Matchup history is the best-measured family in this repo:

    MLB pitcher_vs_team_dominant_history   63.09%  n=401  +12.16pp  w 0.89
    MLB pitcher_vs_team_recent_hit_hard    63.54%  n=384  +14.01pp  w 0.93
    MLB pitcher_vs_team_ip_below_outs_line 63.77%  n=276  +12.50pp  w 0.95
    MLB pitcher_vs_team_owned              69.70%  n= 99  +20.16pp  w 0.99

What was broken: `recommended_weight` is not wired to anything.
ensemble_scorer names it ONCE, inside the select at _load_registry, and
never reads the value; signal_registry_lookup.weight_of() is called
nowhere. So editing it moved every audit number and zero picks.

This CLI writes signal_registry.manual_weight (migration 20261126a), which
ensemble_scorer._manual_weight_of DOES read.

== WHY IT REFUSES AS MUCH AS IT WRITES ==================================
A lever with silent holes in it is the same defect I was explaining when
Andy pushed back. 218 of 867 signal_sources rows CANNOT carry a manual
weight, and setting one on them would look exactly like success:

  * 207 rows carry an INLINE hit_rate_pct. _resolve_weight_row returns
    (hr, n, None, None) for those -- it never looks the registry up, so
    there is no row for a weight to ride on.
  * 11 rows are class 'scenario' or 'external_pick'. Those handlers build
    their own hit rate and tier (_handler_external off
    external_source_track_record, _handler_scenario per scenario match)
    and never read signal_registry. Only 'split' and the main path carry
    the field through.

So every write here is checked for REACHABILITY first and refused with the
reason. Same class as [[feedback_a_gate_reading_an_unselected_column_always_passes]]:
the failure mode to design against is the one that reports success.

It also refuses ANTI_VALIDATED, because the scorer will not apply an
override there either -- a hand number must not outrank a signal measured
to be actively wrong
([[feedback_unmeasured_signal_must_not_outweigh_measured]]).

== THE EXPIRY IS NOT OPTIONAL BY ACCIDENT ===============================
--review-days defaults to 60 and writes weight_review_at. Past that date
_manual_weight_of returns None and the measured weight takes back over, by
itself. An override with no end date is a dead value pricing live picks
([[feedback_a_dead_value_keyed_on_itself_is_immortal]]). --no-expiry is
available and prints a warning.

USAGE
    # look first -- what does the measurement say today?
    python set_signal_weight.py --list
    python set_signal_weight.py --sport NCAAB --signal h2h_under_streak

    # dry run (default), then apply
    python set_signal_weight.py --sport NCAAB --signal h2h_under_streak \
        --scope total --weight 0.45 \
        --note "3-season h2h sample: 2,898 pairs have a prior meeting"
    python set_signal_weight.py ... --apply

    # hand it back to the measurement
    python set_signal_weight.py --sport NCAAB --signal h2h_under_streak \
        --scope total --clear --apply
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

# Same loader every committed script in this directory uses. setdefault, so a
# value already in the environment wins -- that is what lets the offline test
# suite import this module with no .env present
# ([[feedback_exit_1_after_0s_is_an_import_crash]]).
_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL') or ''
KEY = os.environ.get('SUPABASE_KEY') or ''
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

# The two signal_sources classes whose handlers never read signal_registry.
# 'split' is NOT here: _handler_split passes manual_weight through, as does
# the main scoring path.
UNREACHABLE_CLASSES = {'scenario', 'external_pick'}

OVERRIDE_COLS = ('manual_weight', 'weight_source', 'weight_note',
                 'weight_set_at', 'weight_set_by', 'weight_review_at')


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _et_today() -> str:
    from sweat_dates import et_today
    return et_today()


def _get(table: str, params: dict) -> list:
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ,
                     params=params, timeout=30)
    if r.status_code != 200:
        print(f'  ! {table} read {r.status_code}: {r.text[:200]}')
        return []
    out = r.json()
    return out if isinstance(out, list) else []


def _migration_applied() -> bool:
    """20261126a present? A 400 here means the columns do not exist yet."""
    r = requests.get(f'{SB}/rest/v1/signal_registry', headers=H_READ,
                     params={'select': 'manual_weight', 'limit': 1},
                     timeout=20)
    return r.status_code == 200


def _reachability(signal: str, sport: str) -> tuple[bool, str]:
    """Can ensemble_scorer actually read a registry weight for this signal?

    Returns (ok, reason). Checked against signal_sources by signal_key and
    by weight_registry_key, because _resolve_weight_row tries both.
    """
    rows = _get('signal_sources',
                {'select': 'signal_key,sport,class,hit_rate_pct,'
                           'weight_registry_key,enabled',
                 'sport': f'eq.{sport}'})
    mine = [r for r in rows
            if r.get('signal_key') == signal
            or r.get('weight_registry_key') == signal]
    if not mine:
        # Not every registry row has a signal_sources row -- prop signals and
        # some discovered families are registered elsewhere. Do not block on
        # absence, but say so, because an unreachable write is the one thing
        # this tool exists to prevent.
        return True, ('no signal_sources row for this key -- reachability '
                      'UNVERIFIED (prop/discovered families register '
                      'elsewhere); the audit will show whether it fires')
    bad = []
    for r in mine:
        cls = str(r.get('class') or '')
        if cls in UNREACHABLE_CLASSES:
            bad.append(f'class={cls} (its handler builds its own tier and '
                       f'never reads signal_registry)')
        if r.get('hit_rate_pct') is not None:
            bad.append(f'inline hit_rate_pct={r["hit_rate_pct"]} '
                       f'(_resolve_weight_row returns no registry row)')
        if r.get('enabled') is False:
            bad.append('signal_sources.enabled is false (it never fires)')
    if bad:
        return False, '; '.join(sorted(set(bad)))
    return True, 'reachable via the main scoring path'


def _rows_for(signal: str, sport: str, scope: str | None) -> list:
    p = {'select': ','.join((
        'id,signal_name,sport,market_scope,tier,hit_rate,sample_n,'
        'recommended_weight,baseline_pct,price_breakeven_pct,edge_pp',
        *OVERRIDE_COLS)),
        'signal_name': f'eq.{signal}', 'sport': f'eq.{sport}'}
    if scope:
        p['market_scope'] = f'eq.{scope}'
    return _get('signal_registry', p)


def _show(r: dict, indent: str = '    ') -> None:
    mw = _f(r.get('manual_weight'))
    src = r.get('weight_source') or 'measured'
    print(f'{indent}{r.get("sport")} {r.get("signal_name")} '
          f'[{r.get("market_scope")}]')
    print(f'{indent}  tier={r.get("tier")}  hit={r.get("hit_rate")}  '
          f'n={r.get("sample_n")}  edge_pp={r.get("edge_pp")}')
    print(f'{indent}  measured recommended_weight={r.get("recommended_weight")}'
          f'   baseline={r.get("baseline_pct")}'
          f'   price_bar={r.get("price_breakeven_pct")}')
    print(f'{indent}  weight_source={src}'
          + (f'  manual_weight={mw}' if mw is not None else ''))
    if r.get('weight_note'):
        print(f'{indent}  note: {r["weight_note"]}')
    if r.get('weight_review_at'):
        exp = str(r['weight_review_at'])[:10]
        flag = '  <-- EXPIRED, measured weight applies' \
            if exp < _et_today() else ''
        print(f'{indent}  review_at={exp}  set_by={r.get("weight_set_by")}'
              f'{flag}')
    elif src == 'manual':
        print(f'{indent}  review_at=NEVER  <-- no expiry')


def cmd_list() -> int:
    rows = _get('signal_registry',
                {'select': ','.join((
                    'signal_name,sport,market_scope,tier,hit_rate,sample_n,'
                    'recommended_weight,baseline_pct,price_breakeven_pct,'
                    'edge_pp', *OVERRIDE_COLS)),
                 'weight_source': 'eq.manual',
                 'order': 'sport.asc,signal_name.asc'})
    if not rows:
        print('no manual weight overrides are set')
        return 0
    print(f'{len(rows)} manual weight override(s):\n')
    for r in rows:
        _show(r, '  ')
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Hand-set an ensemble weight on signal_registry')
    ap.add_argument('--sport')
    ap.add_argument('--signal', help='signal_registry.signal_name')
    ap.add_argument('--scope', help='market_scope (ml/rl/total/spread/prop/*)')
    ap.add_argument('--weight', type=float,
                    help='FRACTION in [0,1], the scale edge_weight returns')
    ap.add_argument('--note', help='why -- the analytical basis. Required.')
    ap.add_argument('--by', default='andy')
    ap.add_argument('--review-days', type=int, default=60)
    ap.add_argument('--no-expiry', action='store_true')
    ap.add_argument('--clear', action='store_true',
                    help='hand this signal back to the measured weight')
    ap.add_argument('--list', action='store_true',
                    help='show every override that is set')
    ap.add_argument('--apply', action='store_true',
                    help='without this, nothing is written')
    a = ap.parse_args()

    if not SB or not KEY:
        print('! SUPABASE_URL / SUPABASE_KEY not set')
        return 2
    if not _migration_applied():
        print('! signal_registry.manual_weight does not exist.')
        print('  Apply supabase/migrations/20261126a_signal_registry_'
              'manual_weight.sql first.')
        return 2
    if a.list:
        return cmd_list()
    if not (a.sport and a.signal):
        ap.error('--sport and --signal are required (or use --list)')

    sport = a.sport.upper()
    rows = _rows_for(a.signal, sport, a.scope)
    if not rows:
        print(f'! no signal_registry row for {sport} {a.signal}'
              + (f' scope={a.scope}' if a.scope else ''))
        near = _get('signal_registry',
                    {'select': 'signal_name,sport,market_scope,tier,sample_n',
                     'sport': f'eq.{sport}',
                     'signal_name': f'ilike.*{a.signal}*', 'limit': '10'})
        if near:
            print('  did you mean:')
            for r in near:
                print(f'    {r["signal_name"]} [{r.get("market_scope")}] '
                      f'tier={r.get("tier")} n={r.get("sample_n")}')
        return 1
    if len(rows) > 1:
        # (signal_name, sport, market_scope) is the real key. Picking row 0
        # of an unordered multi-row read is how a weight lands on the wrong
        # market ([[feedback_unordered_data_zero_picks_a_vintage]]).
        print(f'! {len(rows)} rows match -- name the market with --scope:')
        for r in rows:
            print(f'    --scope {r.get("market_scope")}   '
                  f'tier={r.get("tier")} hit={r.get("hit_rate")} '
                  f'n={r.get("sample_n")}')
        return 1

    row = rows[0]
    print('CURRENT STATE')
    _show(row)
    print()

    if a.clear:
        payload = {'manual_weight': None, 'weight_source': 'measured',
                   'weight_note': None, 'weight_review_at': None,
                   'weight_set_by': a.by,
                   'weight_set_at': _dt.datetime.now(
                       _dt.timezone.utc).isoformat()}
        what = (f'CLEAR override on {sport} {a.signal} '
                f'[{row.get("market_scope")}] -- the measured weight '
                f'({row.get("recommended_weight")}) takes over')
    else:
        if a.weight is None:
            ap.error('--weight is required (or --clear)')
        if not (0.0 <= a.weight <= 1.0):
            print(f'! --weight {a.weight} is outside [0,1]. This is the '
                  f'fraction edge_weight returns, not a percent and not a '
                  f'multiplier.')
            return 1
        if not (a.note or '').strip():
            print('! --note is required. An override with no stated reason '
                  'cannot be reviewed, and the audit prints it beside the '
                  'measurement so a stale rationale is visible.')
            return 1

        ok, why = _reachability(a.signal, sport)
        print(f'REACHABILITY: {"ok" if ok else "REFUSED"} -- {why}')
        if not ok:
            print('\n! Refusing to write a weight the scorer cannot read. '
                  'It would change every audit number and zero picks -- '
                  'exactly the defect this tool was built to replace.')
            return 1
        if str(row.get('tier')) == 'ANTI_VALIDATED':
            print('\n! Refusing: this row is ANTI_VALIDATED. edge_weight '
                  'hard-zeros it and the scorer will not apply an override '
                  'there. A hand number must not outrank a signal measured '
                  'to be actively wrong. Nothing is retired -- when the '
                  'measurement moves off ANTI_VALIDATED this will apply.')
            return 1

        rev = None
        if not a.no_expiry:
            rev = (_dt.date.fromisoformat(_et_today())
                   + _dt.timedelta(days=a.review_days)).isoformat()
        else:
            print('  ! --no-expiry: this override never lapses. A hand value '
                  'with no end date keeps pricing picks after the reason for '
                  'it is gone.')
        payload = {'manual_weight': round(float(a.weight), 4),
                   'weight_source': 'manual',
                   'weight_note': a.note.strip(),
                   'weight_set_by': a.by,
                   'weight_review_at': rev,
                   'weight_set_at': _dt.datetime.now(
                       _dt.timezone.utc).isoformat()}
        _meas = _f(row.get('recommended_weight'))
        what = (f'SET {sport} {a.signal} [{row.get("market_scope")}] '
                f'manual_weight={payload["manual_weight"]}'
                + (f'  (measurement says {_meas})' if _meas is not None else '')
                + f'  review_at={rev or "NEVER"}')

    print(f'\n{what}')
    if not a.apply:
        print('\n[DRY RUN] nothing written. Re-run with --apply.')
        return 0

    pr = requests.patch(f'{SB}/rest/v1/signal_registry', headers=H_WRITE,
                        params={'id': f'eq.{row["id"]}'}, json=payload,
                        timeout=60)
    # A 204, and a 200 with an empty body, are NOT writes
    # ([[feedback_204_is_not_a_write]]). And signal_registry now carries a
    # BEFORE UPDATE trigger that silently restores these columns unless
    # weight_set_at advances -- so the read-back is the only proof.
    if pr.status_code not in (200, 201):
        print(f'! PATCH {pr.status_code}: {pr.text[:300]}')
        return 1
    back = _rows_for(a.signal, sport, row.get('market_scope'))
    if not back:
        print('! wrote, but the row cannot be re-read -- treat as FAILED')
        return 1
    got = back[0]
    want_mw = payload['manual_weight']
    got_mw = _f(got.get('manual_weight'))
    good = ((want_mw is None and got_mw is None)
            or (want_mw is not None and got_mw is not None
                and abs(got_mw - want_mw) < 1e-9))
    good = good and (str(got.get('weight_source') or '')
                     == payload['weight_source'])
    print('\nREAD BACK')
    _show(got)
    if not good:
        print('\n! NOT CONFIRMED. The trigger preserves these columns unless '
              'weight_set_at advances -- check that weight_set_at was sent.')
        return 1
    print('\nconfirmed. ensemble_scorer picks this up on its next run '
          '(revert: ENSEMBLE_MANUAL_WEIGHT=0).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
