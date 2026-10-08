"""Does a signal's registry hit_rate predict how its own picks perform?

WHY (2026-10-08)
----------------
Andy: "then the leak inflated weights". The NCAAF registry advertises hit rates
no ATS signal sustains — ncaaf_home_fav_week1_chalk 94.3% (n=70),
ncaaf_sp_plus_edge_home 91.2% (n=68), ncaaf_confluence_home 85.6% (n=139),
ncaaf_home_field_baseline 74.5% (n=376) — while NCAAF published reads run
50.8%. Both cannot be true if those signals drive the picks.

`ensemble_scorer.edge_weight_v2` converts hit_rate into weight through a Beta
prior centred on breakeven. That prior protects against SMALL-SAMPLE luck, and
only that. Worked for ncaaf_home_field_baseline at 74.5%/n=376:

    posterior = (10.48 + 280) / (20 + 376) = 0.7335
    edge_pp   = 0.2095
    weight    = 1 - exp(-0.2095/0.06) = 0.97 of maximum

A leak-inflated claim with a LARGE sample earns near-maximum weight. Nothing
in the chain asks whether the claim is believable.

WHAT THIS MEASURES
`primary_play._ensemble_sources` records every signal that contributed to a
pick, with the hit_rate and contribution it was given at scoring time. Joining
that to the graded read gives each signal's hit rate ON THE PICKS IT ACTUALLY
DROVE — out of sample, by construction, because the pick was made before the
game was played. Measured 2026-10-08 on NCAAF, 246 graded picks:

    signal                      claim        actual        gap
    ncaaf_confluence_home       85.6% n=139  63.6% n=11   -22.0pp
    ncaaf_home_field_baseline   74.5% n=376  57.1% n=28   -17.4pp
    ncaaf_projected_spread      73.9% n=207  64.3% n=14    -9.6pp
    ncaaf_home_spread_edge      62.2% n=45   40.0% n=20   -22.2pp
    ncaaf_home_underdog_bark    54.8% n=42   33.3% n=18   -21.5pp

and the summary that matters more than any single row:

    correlation(claimed, actual) = +0.204   over 17 signals with actual n>=10
    mean ABS gap                 = 11.0pp
    over-claims by >5pp 7/17 · under-claims by >5pp 4/17

So the registry number is not merely inflated, it is close to uninformative
about the signal's real performance, in BOTH directions. That reframes the fix:
a plausibility ceiling on the >60% claims would touch only 10.2% of total
contribution and would not address an 11pp average error.

WHAT THIS SCRIPT IS, AND IS NOT
It is the instrument: it reports the gap per signal and the summary stats, and
it writes nothing. It deliberately does NOT reweight anything. Changing how
picks are scored is a suppression-gate-class change and those get shadowed and
decided on evidence, not shipped the night before a slate locks.

THE FIX IT POINTS AT (Andy's call, not shipped here)
Feed the OBSERVED on-pick rate back as the weight input instead of the
backfilled hit_rate. The small samples that produces are exactly what
edge_weight_v2's Beta prior is built to handle — it shrinks toward breakeven
until a signal earns otherwise. That is a real calibration loop grounded in
our own graded picks rather than in a backfill whose provenance we cannot
reconstruct.

CLI
    python audit_signal_claims.py --sport NCAAF
    python audit_signal_claims.py                 # all football
    python audit_signal_claims.py --sport NCAAF --min-n 5
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context'}

#: No side/ATS signal credibly sustains better than this. Above it, treat the
#: claim as evidence of leakage rather than of edge. Used for REPORTING only.
PLAUSIBLE_MAX = 0.60
BREAKEVEN = 52.38


def _page(t, p, cap=60000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def run(sport: str, min_n: int, since: str) -> None:
    tbl = CTX.get(sport)
    if not tbl:
        print(f'{sport}: no context table')
        return
    graded = {str(a['game_id']): str(a.get('result') or '').upper()
              for a in _page('jerry_reads',
                             {'select': 'game_id,result', 'sport': f'eq.{sport}',
                              'result': 'not.is.null'})}
    ctx = _page(tbl, {'select': 'game_id,primary_play',
                      'game_date': f'gte.{since}'})

    per = collections.defaultdict(lambda: [0, 0])
    claim_at_score: dict = {}
    contrib = collections.defaultdict(float)
    total_contrib = 0.0
    used = 0
    for g in ctx:
        res = graded.get(str(g['game_id']))
        pp = _jl(g.get('primary_play'))
        srcs = pp.get('_ensemble_sources') or []
        for s in srcs:
            if not isinstance(s, dict):
                continue
            key = str(s.get('signal_key') or s.get('name') or '?')
            c = s.get('contribution')
            if c is not None:
                total_contrib += abs(float(c))
                contrib[key] += abs(float(c))
            if s.get('hit_rate') is not None:
                claim_at_score.setdefault(key, float(s['hit_rate']) * 100)
        if res not in ('WIN', 'W', 'LOSS', 'L'):
            continue
        used += 1
        for key in {str(s.get('signal_key') or s.get('name') or '?')
                    for s in srcs if isinstance(s, dict)}:
            per[key][0 if res in ('WIN', 'W') else 1] += 1

    reg = {str(x['signal_name']): x
           for x in _page('signal_registry',
                          {'select': 'signal_name,sport,hit_rate,sample_n,tier',
                           'sport': f'eq.{sport}'})}

    print('=' * 78)
    print(f'{sport} · {used} graded picks carrying _ensemble_sources '
          f'· since {since}')
    print('=' * 78)
    rows = []
    for key, (w, l) in per.items():
        n = w + l
        if n < min_n:
            continue
        actual = w / n * 100
        r = reg.get(key)
        claim = (float(r['hit_rate']) if r and r.get('hit_rate') is not None
                 else claim_at_score.get(key))
        rows.append((key, claim, (r or {}).get('sample_n'), actual, n,
                     (r or {}).get('tier')))
    rows.sort(key=lambda z: -(z[1] if z[1] is not None else -1))

    print(f"  {'signal':<38}{'claim':>7}{'cl n':>6}{'actual':>8}{'act n':>6}"
          f"{'gap':>9}  tier")
    for key, claim, cn, actual, n, tier in rows:
        cs = f'{claim:.1f}%' if claim is not None else '-'
        gap = f'{actual - claim:+.1f}pp' if claim is not None else '-'
        flag = ''
        if claim is not None and claim / 100 > PLAUSIBLE_MAX:
            flag = '  <-- claim above plausible max'
        print(f'  {key[:37]:<38}{cs:>7}{str(cn):>6}{actual:>7.1f}%{n:>6}'
              f'{gap:>9}  {str(tier or "-")}{flag}')

    pairs = [(c, a) for _, c, _, a, n, _ in rows if c is not None]
    print()
    if len(pairs) >= 5:
        cl = [p[0] for p in pairs]
        ac = [p[1] for p in pairs]
        try:
            rr = statistics.correlation(cl, ac)
        except Exception:                                  # noqa: BLE001
            rr = float('nan')
        gaps = [a - c for c, a in pairs]
        print(f'  correlation(claimed, actual) = {rr:+.3f}  over {len(pairs)} '
              f'signals with actual n>={min_n}')
        print(f'  mean gap {statistics.fmean(gaps):+.1f}pp · '
              f'mean ABS gap {statistics.fmean([abs(g) for g in gaps]):.1f}pp')
        print(f'  over-claims >5pp: {sum(1 for g in gaps if g < -5)} · '
              f'under-claims >5pp: {sum(1 for g in gaps if g > 5)}')
        if abs(rr) < 0.30:
            print('  => the registry hit_rate is close to UNINFORMATIVE about '
                  'real performance.')
            print('     A plausibility ceiling caps the worst claims but does '
                  'not fix this.')
    else:
        print(f'  too few signals with actual n>={min_n} to summarise')

    imp = sum(v for k, v in contrib.items()
              if (claim_at_score.get(k) or 0) / 100 > PLAUSIBLE_MAX)
    if total_contrib:
        print()
        print(f'  weight through claims >{PLAUSIBLE_MAX * 100:.0f}%: '
              f'{imp:.1f} of {total_contrib:.1f} '
              f'({imp / total_contrib * 100:.1f}% of all contribution)')
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--min-n', type=int, default=10)
    ap.add_argument('--since', default='2026-08-20')
    a = ap.parse_args()
    for s in ([a.sport.upper()] if a.sport else ['NCAAF', 'NFL']):
        run(s, a.min_n, a.since)
    return 0


if __name__ == '__main__':
    sys.exit(main())
