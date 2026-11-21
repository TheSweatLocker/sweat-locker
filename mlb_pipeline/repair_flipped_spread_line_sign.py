"""Give a flipped spread pick back the SIGN it was published with.

══ 2026-11-21 · "WE PUBLISH A NUMBER NOBODY CAN BET" — EXCEPT WE DIDN'T ══

`locked_pick_line_drift` went CRITICAL with 5 unbettable picks inside 72h.
Two of the five were not drift at all:

    NCAAF Arkansas @ Texas   published +20.5, market -20.5  (+41.0)
    NCAAF Iowa @ Illinois    published  +8.5, market  -8.5  (+17.0)

A gap of exactly 2x the magnitude, with the same absolute value on both
sides, is never a market move -- it is a sign. Both games' picks matched
the market EXACTLY. Measured at the number actually published, both gaps
are +0.0.

THE DEFECT, AND WHY IT ONLY HIT ONE FIELD
`defensive_gates.apply_oc_flip_gate` (the OddsCrowd dissent fade) flipped
`side`, `label` and `tier` and left `line` on the side we no longer held.
It computed the correctly-signed number -- for the label string -- and threw
the value away. So a flip from the dog to the favourite produced

    label "Texas -20.5"   line +20.5

the right number with the wrong sign, on 48 of 52 `_oc_flipped` NCAAF
spread picks since 2026-08-25.

THE RECORD WAS NEVER WRONG, AND THAT WAS MEASURED, NOT ASSUMED
Every graded receipt whose stored line disagrees in sign with its own label
was re-graded here from final scores: 16 of 16 match the PUBLISHED LABEL,
including six that would have settled the other way had the line been used
(Clemson -19.5, Oregon -23.5, LSU -35.5, Tulsa -11.5, Alabama -17.5,
Dodgers -1.5). Graders settle off the label. So this blinded a detector; it
did not cost a unit. public_receipts.pick_line is immutable by trigger and
is NOT touched here -- it is inert for grading and rewriting frozen
receipts is a records decision, not a repair
(feedback_fix_the_cache_not_the_frozen_receipt).

WHY THIS IS A REPAIR AND NOT A REPICK
The label is the published truth: it is what the user saw, and it is what
the grader settles. This moves `line` to agree with it. The side, the
market, the tier and the MAGNITUDE are untouched -- the only thing that
changes is a sign that never matched what we showed. Same reasoning as
repair_spread_labels.py, which completes a line the label was built without.

A magnitude difference is NOT repaired. |label| != |line| means the two
disagree about the bet itself, not about its orientation, and guessing
which one is right is how a repair becomes a repick. Those are reported and
left alone.

The source is fixed in defensive_gates.apply_oc_flip_gate (2026-11-21), so
this is a backfill over rows written before that commit, not a standing
cleanup. Run it once; `--dry-run` should come back empty afterwards.

USAGE
  python repair_flipped_spread_line_sign.py --dry-run
  python repair_flipped_spread_line_sign.py --apply
  python repair_flipped_spread_line_sign.py --self-test
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: sport -> context table. No spread column and no orientation sign appears
#: here on purpose: this repair reads the number off the LABEL and never
#: derives one from close_spread, so it needs no per-sport convention and
#: cannot inherit the sign bug it is cleaning up
#: (feedback_edge_formula_sign_audit).
CTX = {
    'NBA':   'nba_game_context',
    'NCAAB': 'ncaab_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NFL':   'nfl_game_context',
    'NHL':   'nhl_game_context',
}

#: Trailing signed number on a label: "Texas -20.5", "Iowa +8.5".
_LABEL_NUM = re.compile(r'([+-]\s?\d+(?:\.\d+)?)\s*$')


def label_line(label) -> float | None:
    """The number a published label prints, or None if it prints none."""
    m = _LABEL_NUM.search(str(label or ''))
    if not m:
        return None
    try:
        return float(m.group(1).replace(' ', ''))
    except ValueError:
        return None


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def classify(pp) -> tuple[str, float | None]:
    """('repair', new_line) | ('magnitude', labn) | ('ok', None) | ('skip', None)

    Pure and side-effect free, so the self-test can cover every branch
    without a database.
    """
    if not isinstance(pp, dict) or not pp:
        return 'skip', None
    if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
        return 'skip', None
    ln = _f(pp.get('line'))
    labn = label_line(pp.get('label'))
    if ln is None or labn is None:
        # No line, or a label with no number: repair_spread_labels.py's job,
        # not this one. Saying 'skip' rather than 'ok' keeps the two tools
        # from each believing the other handled it.
        return 'skip', None
    if abs(labn) != abs(ln):
        return 'magnitude', labn
    if (labn < 0) == (ln < 0):
        return 'ok', None
    return 'repair', labn


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:180]}')
            return out, False
        d = r.json() or []
        out += d
        if len(d) < 1000:
            return out, True
        off += 1000


def run(apply: bool, sports: list) -> int:
    print(f'=== repair_flipped_spread_line_sign · '
          f'{"APPLY" if apply else "DRY RUN"} ===')
    tot = {'repair': 0, 'magnitude': 0, 'ok': 0, 'failed': 0}
    read_ok = True
    for sport in sports:
        tbl = CTX[sport]
        rows, got = page(tbl, {'select': 'game_id,game_date,home_team,'
                                         'away_team,primary_play'})
        read_ok = read_ok and got
        n = {'repair': 0, 'magnitude': 0, 'ok': 0, 'failed': 0}
        for row in rows:
            pp = row.get('primary_play')
            verdict, newline = classify(pp)
            if verdict == 'skip':
                continue
            if verdict == 'ok':
                n['ok'] += 1
                continue
            tag = (f"{row.get('away_team')} @ {row.get('home_team')} "
                   f"{str(row.get('game_date'))[:10]}")
            if verdict == 'magnitude':
                n['magnitude'] += 1
                print(f'    ? MAGNITUDE DIFFERS, not repaired: {tag} '
                      f'label={pp.get("label")!r} line={pp.get("line")} '
                      f'— these disagree about the bet, not its sign')
                continue
            was = pp.get('line')
            new_pp = dict(pp)
            new_pp['line'] = newline
            new_pp['_line_sign_repair'] = {
                'from': was, 'to': newline,
                'label': pp.get('label'),
                'at': datetime.now(timezone.utc).isoformat(),
                'note': 'primary_play.line carried the pre-flip side\'s sign; '
                        'moved to agree with the published label. side, '
                        'market, tier and magnitude unchanged.',
                'cause': 'defensive_gates.apply_oc_flip_gate flipped side/'
                         'label/tier and left line (fixed 2026-11-21)',
            }
            print(f'    {tag}: line {was:+g} -> {newline:+g}   '
                  f'(published {pp.get("label")!r})')
            if not apply:
                n['repair'] += 1
                continue
            pr = requests.patch(
                f'{SB}/rest/v1/{tbl}', headers=H_WRITE, timeout=60,
                params={'game_id': f'eq.{row["game_id"]}',
                        'game_date': f'eq.{row["game_date"]}'},
                json={'primary_play': new_pp})
            # A 204 is not a write, and a lock trigger can revert a 200.
            # Confirm from the row that comes back
            # (feedback_204_is_not_a_write, feedback_gate_the_stored_row...).
            good = False
            if pr.status_code in (200, 201):
                back = pr.json() or []
                good = bool(back) and _f(
                    (back[0].get('primary_play') or {}).get('line')) == newline
            if good:
                n['repair'] += 1
            else:
                n['failed'] += 1
                print(f'      ! NOT CONFIRMED (HTTP {pr.status_code}) — a '
                      f'lock trigger may be reverting it')
        print(f'  {sport:6} {len(rows):5} games · '
              f'{n["repair"]:3} sign-repaired · {n["ok"]:4} already agreed · '
              f'{n["magnitude"]:3} magnitude differs · {n["failed"]:3} failed')
        for k in tot:
            tot[k] += n[k]
    print(f'\n=== {"applied" if apply else "DRY RUN"}: {tot["repair"]} '
          f'sign-repaired, {tot["ok"]} already agreed, {tot["magnitude"]} '
          f'magnitude differs, {tot["failed"]} failed ===')
    if not apply and tot['repair']:
        print('Re-run with --apply to write.')
    if not read_ok:
        # A read that failed is not a clean slate
        # (feedback_zero_rows_is_a_wiring_error).
        print('!! at least one context read FAILED — this run is incomplete')
        return 2
    return 1 if tot['failed'] else 0


def _self_test() -> int:
    cases = []

    def chk(name, cond):
        cases.append((name, bool(cond)))

    # the live defect
    chk('a flipped pick whose label disagrees in sign is repaired to the '
        'label', classify({'type': 'rl', 'line': 20.5,
                           'label': 'Texas -20.5'}) == ('repair', -20.5))
    chk('...and in the other direction too',
        classify({'type': 'rl', 'line': -7.5,
                  'label': 'Iowa +7.5'}) == ('repair', 7.5))
    # the control: an agreeing pick must NOT be touched
    chk('an agreeing pick is left alone',
        classify({'type': 'rl', 'line': -6.0,
                  'label': 'DET -6'}) == ('ok', None))
    chk('an agreeing positive pick is left alone',
        classify({'type': 'rl', 'line': 13.5,
                  'label': 'Washington State +13.5'}) == ('ok', None))
    # a magnitude difference is a different problem and must not be "fixed"
    chk('a magnitude difference is reported, never repaired',
        classify({'type': 'rl', 'line': 15.0,
                  'label': 'South Carolina +16.5'})[0] == 'magnitude')
    # things this tool must not claim
    chk('a pick with no line is left to repair_spread_labels',
        classify({'type': 'rl', 'line': None,
                  'label': 'Toronto Raptors RL'}) == ('skip', None))
    chk('a label with no number is left alone',
        classify({'type': 'rl', 'line': 3.0,
                  'label': 'Marshall ATS'}) == ('skip', None))
    chk('a moneyline is not a spread',
        classify({'type': 'ml', 'line': None,
                  'label': 'DET ML'}) == ('skip', None))
    chk('a total is not a spread',
        classify({'type': 'total', 'line': 47.5,
                  'label': 'Under 47.5'}) == ('skip', None))
    chk('an empty primary_play is skipped', classify({}) == ('skip', None))
    chk('a non-dict primary_play is skipped',
        classify(None) == ('skip', None))
    # a pick'em cannot be mis-signed: 0 == -0
    chk('a zero line is treated as agreeing, not flipped',
        classify({'type': 'rl', 'line': 0.0,
                  'label': 'Marshall +0'}) == ('ok', None))
    # label parsing
    chk('a label number is read off the END, not the team name',
        label_line('Oregon State -3 76ers') is None
        and label_line('Philadelphia 76ers -3') == -3.0)
    chk('an unsigned trailing number is not treated as a line',
        label_line('Over 47.5') is None)

    f = 0
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        f += 0 if ok else 1
    print(f'\n  {len(cases) - f}/{len(cases)} self-test cases passed')
    return 1 if f else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append', choices=sorted(CTX))
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    return run(a.apply and not a.dry_run, a.sport or sorted(CTX))


if __name__ == '__main__':
    raise SystemExit(main())
