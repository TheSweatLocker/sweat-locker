"""The supervised dataset for a pick-ranking score, and whether one is learnable.

WHY (2026-10-09)
----------------
Andy: "there has to be a way to tier effieicntly to the point that prime picks
are pfortiable rigth, we have so much data i just dont understadn" — then
"Ranking score should be ncaaf and nfl right? lets start there".

He is right that gating harder was the wrong tool. The reason NCAAF PRIME is
not profitable is not that the threshold sits in the wrong place; it is that
the number under the threshold does not rank:

    NCAAF   correlation(conviction, win) = +0.025   n=188
    NFL     correlation(conviction, win) = +0.029   n=47
    mean conviction on wins vs losses: 67.0 vs 66.4 (NCAAF), 60.8 vs 60.1 (NFL)

A tier is only as good as the ordering beneath it. Moving a cutoff on a score
that is indistinguishable from noise just relabels random picks.

WHAT THIS BUILDS
One row per GRADED pick that carries `_ensemble_sources`, with the label
(win=1 / loss=0) and features derived from what actually drove the pick. It
then screens each feature against the label — because the first question is
not "which model" but "is there any signal in here at all". If nothing
separates, no amount of model choice helps and the answer is that the inputs
need to change, not the weighting.

FEATURE DESIGN, and why it is deliberately coarse
NCAAF has 188 graded picks and ~229 distinct signal keys. Per-signal dummies
would be 229 features on 188 rows — guaranteed overfitting, and the kind of
in-sample number that has already misled this project twice this week. So the
features are AGGREGATES: how much total evidence, how concentrated, which
CLASSES of evidence (model / situational / sharp / external / market), and the
market and side. Roughly ten features, which n=188 can support.

TWO DISCIPLINES THAT ARE NOT OPTIONAL
  * TIME-ORDERED split. Train on earlier games, test on later. A random split
    leaks, and leaky backtests are a documented repeat offender here
    (project_sp_plus_backtests_are_leaky_926, project_models_dont_beat_the_close_1005
    where 65-79%% ATS turned out to be entirely leakage).
  * Every number is reported next to the BASELINE it has to beat: current
    conviction, and flat always-home. A feature that "correlates" below those
    is not a finding.

`_ensemble_sources` stores only the CHOSEN side's contributions, so this can
rank our own picks — it cannot re-choose the side. That is the right scope for
a tiering score and it is why persisting `_ensemble_runner_up` (509a35d3)
matters separately.

WRITES NOTHING.

CLI
    python build_rank_dataset.py --sport NCAAF
    python build_rank_dataset.py --sport NFL
    python build_rank_dataset.py --sport NCAAF --dump rows.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
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
CTX = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context',
       'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context'}


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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(sport: str) -> list[dict]:
    tbl = CTX[sport]
    reads = {str(x['game_id']): x for x in _page(
        'jerry_reads', {'select': 'game_id,game_date,result,conviction,'
                                  'call_market,call_side,price_american',
                        'sport': f'eq.{sport}'})}
    rows = []
    for g in _page(tbl, {'select': 'game_id,game_date,primary_play',
                         'game_date': 'gte.2026-08-20'}):
        rd = reads.get(str(g['game_id']))
        if not rd:
            continue
        res = str(rd.get('result') or '').upper()
        res = {'W': 'WIN', 'L': 'LOSS'}.get(res, res)
        if res not in ('WIN', 'LOSS'):
            continue
        pp = _jl(g.get('primary_play'))
        srcs = [s for s in (pp.get('_ensemble_sources') or [])
                if isinstance(s, dict)]
        if not srcs:
            continue
        contribs = [abs(_f(s.get('contribution')) or 0.0) for s in srcs]
        total = sum(contribs)
        cls = collections.Counter()
        for s, c in zip(srcs, contribs):
            cls[str(s.get('class') or 'other')] += c
        hrs = [_f(s.get('hit_rate')) for s in srcs
               if _f(s.get('hit_rate')) is not None]
        ns = [_f(s.get('n')) or 0 for s in srcs]
        rows.append({
            'game_date': str(g['game_date'])[:10],
            'game_id': str(g['game_id']),
            'y': 1 if res == 'WIN' else 0,
            'conviction': _f(rd.get('conviction')) or 0.0,
            'tier': str(pp.get('tier') or '-'),
            'market': str(rd.get('call_market') or '-'),
            'is_home': 1.0 if str(rd.get('call_side')) == 'HOME' else 0.0,
            'price': _f(rd.get('price_american')),
            # evidence volume and shape
            'n_signals': float(len(srcs)),
            'total_contrib': total,
            'top_contrib': max(contribs) if contribs else 0.0,
            'top_share': (max(contribs) / total) if total else 0.0,
            'n_classes': float(len(cls)),
            # class mix, as share of total contribution
            'sh_model': (cls.get('model', 0.0) / total) if total else 0.0,
            'sh_situational': (cls.get('situational', 0.0) / total) if total else 0.0,
            'sh_sharp': (cls.get('sharp', 0.0) / total) if total else 0.0,
            'sh_external': (cls.get('external', 0.0) / total) if total else 0.0,
            'sh_market': (cls.get('market', 0.0) / total) if total else 0.0,
            # the claimed quality of the evidence, as stored at scoring time
            'mean_claim': statistics.fmean(hrs) if hrs else 0.0,
            'max_claim': max(hrs) if hrs else 0.0,
            'mean_sig_n': statistics.fmean(ns) if ns else 0.0,
        })
    rows.sort(key=lambda r: r['game_date'])
    return rows


FEATURES = ['conviction', 'n_signals', 'total_contrib', 'top_contrib',
            'top_share', 'n_classes', 'sh_model', 'sh_situational',
            'sh_sharp', 'sh_external', 'sh_market', 'mean_claim',
            'max_claim', 'mean_sig_n', 'is_home']


def screen(rows: list[dict], sport: str) -> None:
    y = [r['y'] for r in rows]
    base = statistics.fmean(y) * 100
    print(f'=== {sport}: {len(rows)} graded picks with ensemble sources · '
          f'win rate {base:.1f}%')
    print(f'    date range {rows[0]["game_date"]} .. {rows[-1]["game_date"]}')
    print()
    print('  FEATURE SCREEN — correlation with winning, and the split it makes')
    print(f"    {'feature':<18}{'r':>8}{'lo-half':>10}{'hi-half':>10}"
          f"{'gap':>8}")
    out = []
    for f in FEATURES:
        xs = [r[f] for r in rows]
        if len(set(xs)) < 2:
            continue
        try:
            r_ = statistics.correlation(xs, y)
        except Exception:                                  # noqa: BLE001
            continue
        med = statistics.median(xs)
        lo = [r['y'] for r in rows if r[f] <= med]
        hi = [r['y'] for r in rows if r[f] > med]
        if len(lo) < 15 or len(hi) < 15:
            continue
        lop, hip = statistics.fmean(lo) * 100, statistics.fmean(hi) * 100
        out.append((abs(r_), f, r_, lop, hip, hip - lop))
    out.sort(reverse=True)
    for _, f, r_, lop, hip, gap in out:
        flag = '  <<' if abs(gap) >= 8 else ''
        print(f'    {f:<18}{r_:+8.3f}{lop:9.1f}%{hip:9.1f}%{gap:+8.1f}{flag}')
    print()
    print('  BASELINES this has to beat:')
    cs = [r['conviction'] for r in rows]
    try:
        rc = statistics.correlation(cs, y)
    except Exception:                                      # noqa: BLE001
        rc = float('nan')
    print(f'    current conviction          r = {rc:+.3f}')
    print(f'    flat (always pick)          {base:.1f}%')
    best = out[0] if out else None
    if best:
        print(f'    best single feature         {best[1]} r = {best[2]:+.3f} '
              f'· median split gap {best[5]:+.1f}pp')
        if abs(best[2]) < 0.12:
            print('    => NOTHING here separates meaningfully. A ranking score')
            print('       cannot be learned from these features; the INPUTS')
            print('       need to change, not the weighting.')
        else:
            print('    => there is separation to work with. Next step is a')
            print('       time-ordered fit, never a random split.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--dump', default=None)
    a = ap.parse_args()
    sport = a.sport.upper()
    if sport not in CTX:
        raise SystemExit(f'no ctx table for {sport}')
    rows = build(sport)
    if not rows:
        raise SystemExit(f'{sport}: no graded picks with _ensemble_sources')
    screen(rows, sport)
    if a.dump:
        with open(a.dump, 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f'\n  wrote {len(rows)} rows to {a.dump}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
