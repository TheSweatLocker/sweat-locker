"""Which upcoming picks lean on signals we have measured as over-claiming?

WHY (2026-10-08)
----------------
B66 measured each signal's registry hit_rate against its real performance on
the picks it actually drove. The gap is large and one-directional at the top:

    ncaaf_confluence_home       claim 85.6%  actual 63.6%   -22.0pp
    ncaaf_home_spread_edge      claim 62.2%  actual 40.0%   -22.2pp
    ncaaf_home_underdog_bark    claim 54.8%  actual 33.3%   -21.5pp
    ncaaf_home_field_baseline   claim 74.5%  actual 57.1%   -17.4pp
    ncaaf_projected_spread      claim 73.9%  actual 64.3%    -9.6pp

`edge_weight_v2` turns the CLAIM into weight, so a pick resting on those
signals was sized off a number reality has not supported. Fixing the weighting
is blocked until `_ensemble_runner_up` accrues (B66a). But the exposure is
knowable right now, per pick, from `_ensemble_sources` — which records the
contribution each signal actually made to each stored pick.

This answers the only question that matters before a slate locks: of the picks
about to be published, which ones are mostly held up by evidence we have
already measured as overstated?

HOW THE NUMBER IS BUILT
For each pick, sum |contribution| by signal, then compute the share coming
from signals whose measured gap is worse than -8pp on an actual sample of at
least MIN_ACTUAL_N. A pick at 60% exposure is one whose case is mostly made by
signals that have not delivered.

It is a FLAG, not a verdict. A signal over-claiming does not make a pick wrong
— the market may still be mispriced — and the observed samples behind these
gaps are small (n=11..28 on the worst offenders). The honest use is triage:
look at the high-exposure picks first.

WRITES NOTHING. Reporting only, deliberately, for the same reason the
reweighting is shadowed rather than shipped.

CLI
    python audit_pick_signal_exposure.py --sport NCAAF
    python audit_pick_signal_exposure.py --sport NCAAF --min-exposure 50
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
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

#: A signal counts as over-claiming once its measured gap is worse than this.
GAP_FLOOR = -8.0
#: and only when its observed sample is at least this big. Below it the gap is
#: noise and flagging on it would be the same sin as trusting the claim.
MIN_ACTUAL_N = 10


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


def measure_gaps(sport: str, tbl: str, since: str) -> dict:
    """signal_key -> (claim, actual, n, gap) from graded picks it drove."""
    graded = {str(a['game_id']): str(a.get('result') or '').upper()
              for a in _page('jerry_reads',
                             {'select': 'game_id,result', 'sport': f'eq.{sport}',
                              'result': 'not.is.null'})}
    per = collections.defaultdict(lambda: [0, 0])
    claim: dict = {}
    for g in _page(tbl, {'select': 'game_id,primary_play',
                         'game_date': f'gte.{since}'}):
        pp = _jl(g.get('primary_play'))
        srcs = [s for s in (pp.get('_ensemble_sources') or [])
                if isinstance(s, dict)]
        for s in srcs:
            if s.get('hit_rate') is not None:
                claim.setdefault(str(s.get('signal_key')),
                                 float(s['hit_rate']) * 100)
        res = graded.get(str(g['game_id']))
        if res not in ('WIN', 'W', 'LOSS', 'L'):
            continue
        for key in {str(s.get('signal_key')) for s in srcs}:
            per[key][0 if res in ('WIN', 'W') else 1] += 1

    out = {}
    for key, (w, l) in per.items():
        n = w + l
        if n < MIN_ACTUAL_N or key not in claim:
            continue
        actual = w / n * 100
        out[key] = (claim[key], actual, n, actual - claim[key])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--since', default='2026-08-20')
    ap.add_argument('--min-exposure', type=float, default=0.0)
    ap.add_argument('--days', type=int, default=6)
    a = ap.parse_args()
    sport = a.sport.upper()
    tbl = CTX.get(sport)
    if not tbl:
        raise SystemExit(f'no context table for {sport}')

    gaps = measure_gaps(sport, tbl, a.since)
    bad = {k: v for k, v in gaps.items() if v[3] < GAP_FLOOR}
    print(f'=== {sport}: {len(gaps)} signals with a measured gap '
          f'(actual n>={MIN_ACTUAL_N})')
    print(f'    {len(bad)} over-claim by more than {abs(GAP_FLOOR):.0f}pp:')
    for k, (c, act, n, gap) in sorted(bad.items(), key=lambda kv: kv[1][3]):
        print(f'      {k[:40]:<42}claim {c:5.1f}%  actual {act:5.1f}% '
              f'(n={n})  {gap:+.1f}pp')

    today = dt.date.today().isoformat()
    until = (dt.date.today() + dt.timedelta(days=a.days)).isoformat()
    games = _page(tbl, {'select': 'game_date,away_team,home_team,primary_play,'
                                  'kickoff_utc',
                        'and': f'(game_date.gte.{today},game_date.lte.{until})'})
    rows = []
    for g in games:
        pp = _jl(g.get('primary_play'))
        srcs = [s for s in (pp.get('_ensemble_sources') or [])
                if isinstance(s, dict) and s.get('contribution') is not None]
        if not srcs:
            continue
        tot = sum(abs(float(s['contribution'])) for s in srcs)
        if tot <= 0:
            continue
        exposed = sum(abs(float(s['contribution'])) for s in srcs
                      if str(s.get('signal_key')) in bad)
        drivers = sorted(
            [(str(s.get('signal_key')), abs(float(s['contribution'])))
             for s in srcs if str(s.get('signal_key')) in bad],
            key=lambda t: -t[1])
        rows.append((exposed / tot * 100, g, pp, drivers))

    rows.sort(key=lambda r: -r[0])
    shown = [r for r in rows if r[0] >= a.min_exposure]
    print()
    print(f'=== {len(rows)} upcoming {sport} picks {today}..{until} · '
          f'showing {len(shown)} at exposure >= {a.min_exposure:.0f}%')
    print(f"  {'exp%':>5}  {'date':<11}{'matchup':<32}{'pick':<22}"
          f"{'tier':<9}{'conv':>4}  top over-claiming driver")
    for exp, g, pp, drivers in shown:
        d = f'{drivers[0][0][:30]}' if drivers else '-'
        print(f"  {exp:5.0f}  {str(g['game_date'])[:10]:<11}"
              f"{(g['away_team'] + ' @ ' + g['home_team'])[:30]:<32}"
              f"{str(pp.get('label'))[:20]:<22}{str(pp.get('tier')):<9}"
              f"{str(pp.get('conviction')):>4}  {d}")

    if rows:
        hi = [r for r in rows if r[0] >= 50]
        print()
        print(f'  picks with MAJORITY of their case from over-claiming '
              f'signals: {len(hi)}/{len(rows)}')
        print('  This is triage, not a verdict — the observed samples behind')
        print('  these gaps are small (n=10..28). Look at these first.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
