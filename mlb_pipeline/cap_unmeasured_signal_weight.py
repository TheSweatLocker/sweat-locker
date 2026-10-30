"""Stop a blanket prior from outweighing every signal that has evidence.

══ 2026-10-30 · THE INVERSION IS LIVE ON SUNDAY'S BOARD ═══════════════════
ANDY: NFL is the weakest engine and Sunday is coming.

Measured on NFL this morning, from signal_attribution.signal_was_right --
the SIGNAL's own call, not `result`, which is the PICK's outcome and
punishes a signal for disagreeing (project_signal_hit_rate_was_our_pick_
rate_1028):

  signal                          own evidence     weight
  nfl_home_qb_recent_home_hot     n=0              0.300   <- fires on 5 of
  h2h_home_dominant               n=1              0.300      14 forward
  nfl_division_game               n=2              0.300      picks
  ---------------------------------------------------------------------
  v3_spread_nfl                   53.85% n=13      0.153
  panel_pred_spread               62.50% n=8       0.139
  external:dimers                 50.00% n=4       0.049
  power_diff_edge                 44.44% n=9       0.042
  nfl_offense_mismatch_home_edge  44.44% n=9       0.042
  nfl_penalty_away_undisciplined  50.00% n=16      0.016

A signal with ZERO samples carries 0.300 while every signal that has ever
been graded carries 0.016-0.153 -- 2x to 19x less. That is
feedback_unmeasured_signal_must_not_outweigh_measured exactly, and 0.300 is
the same blanket default that sat on all 65 NCAAB signals.

WHY A CAP AND NOT A ZERO
Zeroing NCAAB's 65 priors left NCAAB unable to produce any pick at all.
Doing that to 76 NFL signals three days before a Sunday slate would take
NFL from "few picks" to "no picks". Removing an UNEARNED weight needs no new
claim; asserting a signal is WORTHLESS does. So this only lowers the
unmeasured prior to the most any evidenced signal in the same sport earned
-- it never zeroes, never raises, and never touches a measured signal.

WHAT THIS DELIBERATELY DOES NOT CLAIM
None of NFL's evidenced signals has a confidence interval clearing the
52.38% spread breakeven; every one spans it on n=4-16. This does not make
NFL's weights correct, it makes them stop being inverted. The real repair is
a refit from attribution -- and note the registry cannot be trusted for it:
its sample_n is EXACTLY 2x the attribution count on every NFL signal with
both, and its hit_rate is higher every single time (61.11% vs 44.44% on
power_diff_edge). The three signals whose registry row matches attribution
exactly are the three already refit that way.

USAGE
  python cap_unmeasured_signal_weight.py --sport NFL --dry-run
  python cap_unmeasured_signal_weight.py --sport NFL --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: A signal counts as EVIDENCED at this many graded appearances of its own.
#: NOT 4. At n=4 the observed rates were 25%, 50% and 75% -- noise -- and
#: five signals sitting at the blanket 0.300 happened to have exactly 4
#: graded rows, which made THEM the ceiling and left the cap toothless at
#: 0.300. The bar has to be high enough that a blanket-prior signal cannot
#: certify itself.
MIN_EVIDENCE_N = 8

#: never raise a weight, and never cap below this (a floor keeps an
#: unmeasured signal contributing rather than silently disabling it)
MIN_CAP = 0.05


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
            return out
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def run(sport: str, apply: bool):
    print(f'=== cap_unmeasured_signal_weight · {sport} · '
          f'{"APPLY" if apply else "DRY RUN"} ===\n')

    att = page('signal_attribution',
               {'select': 'signal_key,signal_was_right', 'sport': f'eq.{sport}'})
    own = {}
    for r in att:
        if r.get('signal_was_right') is None:
            continue
        k = r.get('signal_key')
        a = own.setdefault(k, [0, 0])
        a[1] += 1
        if r.get('signal_was_right') is True:
            a[0] += 1
    print(f'  attribution: {len(att)} rows, '
          f'{len(own)} signals with a graded own-outcome')

    reg_all = page('signal_registry',
                   {'select': 'id,signal_name,hit_rate,sample_n,'
                              'recommended_weight',
                    'sport': f'eq.{sport}'})

    # ══ SCOPE: GAME SIGNALS ONLY ══════════════════════════════════════
    # The first run of this script trimmed NFL PROP families (opp_adj_live,
    # l5_confirm, target_share_low ...) because signal_registry pools both.
    # NFL props are the healthiest surface in the repo -- 16 PRIME and 92
    # STRONG of 504 on the forward slate -- and this script has measured
    # nothing about them. The population is therefore exactly what the GAME
    # engine reads: enabled, non-prop rows of signal_sources.
    src = page('signal_sources',
               {'select': 'signal_key,market_scope,enabled',
                'sport': f'eq.{sport}', 'enabled': 'is.true'})
    game_keys = {
        s_['signal_key'] for s_ in src
        if 'prop' not in str(s_.get('signal_key') or '').lower()
        and 'prop' not in str(s_.get('market_scope') or '').lower()
    }
    reg = [r for r in reg_all if r.get('signal_name') in game_keys]
    print(f'  signal_registry: {len(reg_all)} rows for {sport}; '
          f'{len(reg)} are enabled GAME signals (props excluded)')

    # The ceiling is the largest weight any EVIDENCED signal holds. Evidence
    # means this sport's own attribution rows, never the registry sample_n --
    # that column is 2x inflated (see module note).
    # The CEILING and the POPULATION are different questions, and conflating
    # them under-capped the first time. Model/panel signals such as
    # v3_spread_nfl (0.153, own 53.8% n=13) and panel_pred_spread (0.139,
    # n=8) demonstrably fire on game picks but have no signal_sources row,
    # so scoping the ceiling to that table dropped the best-evidenced
    # weights and pulled the ceiling down to 0.091. The ceiling therefore
    # comes from every non-prop signal with evidence; only what gets CAPPED
    # is restricted to the enabled game library.
    evidenced = []
    for r in reg_all:
        nm = str(r.get('signal_name') or '')
        if 'prop' in nm.lower():
            continue
        k, n = r.get('signal_name'), own.get(r.get('signal_name'))
        if n and n[1] >= MIN_EVIDENCE_N:
            w = _f(r.get('recommended_weight'))
            if w is not None and w > 0:
                evidenced.append((k, w, n[0], n[1]))
    if not evidenced:
        print('  ! no evidenced signal carries weight — refusing to invent a '
              'ceiling. Nothing written.')
        return 0, 0
    ceiling = max(w for _k, w, _a, _b in evidenced)
    ceiling = max(ceiling, MIN_CAP)
    print(f'\n  evidenced signals carrying weight (n >= {MIN_EVIDENCE_N} of '
          f'their OWN graded outcomes):')
    for k, w, a, b in sorted(evidenced, key=lambda z: -z[1]):
        print(f'     {k:40s} w={w:.3f}  own {100.0*a/b:5.1f}% n={b}')
    print(f'\n  => ceiling for an UNMEASURED prior: {ceiling:.3f}')

    todo = []
    for r in reg:
        k = r.get('signal_name')
        w = _f(r.get('recommended_weight'))
        if w is None or w <= ceiling:
            continue
        n = own.get(k)
        if n and n[1] >= MIN_EVIDENCE_N:
            continue          # it has evidence; this script never touches it
        todo.append((r, w, (n[1] if n else 0)))

    print(f'\n  signals above the ceiling with no evidence: {len(todo)}')
    ok = fail = 0
    for r, w, n in sorted(todo, key=lambda z: -z[1]):
        label = f'{r.get("signal_name")} w={w:.3f} -> {ceiling:.3f} (own n={n})'
        if not apply:
            print(f'    [DRY] {label}')
            ok += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/signal_registry', headers=H_WRITE,
                            params={'id': f'eq.{r["id"]}'},
                            json={'recommended_weight': ceiling}, timeout=60)
        good = False
        if pr.status_code in (200, 201):
            back = pr.json() or []
            good = bool(back) and abs(
                (_f(back[0].get('recommended_weight')) or -1) - ceiling) < 1e-9
        if good:
            print(f'    ok   {label}')
            ok += 1
        else:
            print(f'    !    {label}: NOT CONFIRMED (HTTP {pr.status_code})')
            fail += 1

    print(f'\n=== {"applied" if apply else "DRY RUN"}: {ok} capped, '
          f'{fail} failed ===')
    if not apply:
        print('Re-run with --apply to write.')
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    run(a.sport.upper(), a.apply and not a.dry_run)


if __name__ == '__main__':
    main()
