"""Per-signal ROI + contribution tracking (2026-08-23).

PURPOSE
-------
For every signal_key that has ever fired on a graded prop, aggregate
its historical hit rate. Answers: "when signal X fires, does the prop
actually hit more often than random?" — the empirical version of
"which signals earn their weight."

Two levels of analysis:

1. PER-SIGNAL ROI — for each signal_key, count props where that key
   was in signals dict at grade time. Report win / loss / push / n / hit%.

2. FAMILY-BREAKDOWN — same but restricted per prop family
   (ks_over: signal X hits 62%; ks_under: signal X hits 48%).

Sport-universal via SPORT_REG. When NFL / NCAAF / etc props start
producing graded rows, same script audits them.

DATA SOURCE
-----------
Reads `mlb_pipeline_props` (or sport equivalent) where result IS NOT
NULL. Windows: default 30d, configurable via --days.

OUTPUT
------
Prints ranked table to stdout. Optionally writes JSON.

Findings feed into:
- Which signals are proven predictors (keep in scoring functions)
- Which are noise (candidate for removal)
- Which fire rarely but hit hugely (candidate for higher weight)
- Which fire often but hit low (candidate for lower weight)

Usage
-----
    python signal_contribution_tracker.py                 # MLB 30d
    python signal_contribution_tracker.py --sport MLB --days 90
    python signal_contribution_tracker.py --family ks_over
    python signal_contribution_tracker.py --json report.json
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

SPORT_REG = {
    'MLB':   'mlb_pipeline_props',
    'NFL':   'nfl_pipeline_props',
    'NCAAF': 'ncaaf_pipeline_props',
    'NCAAB': 'ncaab_pipeline_props',
    'NHL':   'nhl_pipeline_props',
    'NBA':   'nba_pipeline_props',
}

# Signals that should NEVER be counted (internal metadata)
IGNORE_PREFIXES = ('_',)  # underscore-prefixed keys are metadata


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _stat_family(prop_type: str) -> str:
    if not prop_type: return ''
    for suf in ('_over', '_under'):
        if prop_type.endswith(suf): return prop_type
    return prop_type


def _fetch_graded(sport: str, days: int, family_filter: str | None = None) -> list:
    tbl = SPORT_REG[sport]
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    start = (et_now - timedelta(days=days)).strftime('%Y-%m-%d')
    params = {
        'game_date': f'gte.{start}',
        'result': 'not.is.null',
        'select': 'prop_type,direction,tier,result,signals',
        'limit': '10000',
    }
    if family_filter:
        params['prop_type'] = f'eq.{family_filter}'
    rows = []
    offset = 0
    while True:
        p = {**params, 'offset': offset}
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, params=p, timeout=30)
        if r.status_code != 200: break
        batch = r.json() or []
        rows.extend(batch)
        if len(batch) < 10000: break
        offset += 10000
    return rows


def _outcome(res) -> str:
    """Normalize result to W / L / P."""
    if res is None: return None
    r = str(res).upper()[:1]
    if r == 'W': return 'W'
    if r == 'L': return 'L'
    if r == 'P': return 'P'
    return None


def analyze(sport: str, days: int, family_filter: str | None = None,
            min_sample: int = 20) -> dict:
    rows = _fetch_graded(sport, days, family_filter)
    if not rows:
        return {'sport': sport, 'days': days, 'total_graded': 0, 'signals': []}

    # Per-signal stats
    sig_stats: dict = defaultdict(lambda: {'n': 0, 'w': 0, 'l': 0, 'p': 0,
                                            'by_family': defaultdict(lambda: {'n':0,'w':0,'l':0,'p':0})})
    for row in rows:
        sig = row.get('signals') or {}
        if not isinstance(sig, dict): continue
        outcome = _outcome(row.get('result'))
        if not outcome: continue
        family = _stat_family(row.get('prop_type') or '')
        for k in sig.keys():
            if k.startswith(IGNORE_PREFIXES): continue
            sig_stats[k]['n'] += 1
            sig_stats[k][outcome.lower()] += 1
            sig_stats[k]['by_family'][family]['n'] += 1
            sig_stats[k]['by_family'][family][outcome.lower()] += 1

    # Filter by min_sample + compute hit rate
    filtered = []
    for k, d in sig_stats.items():
        graded = d['w'] + d['l']
        if graded < min_sample: continue
        filtered.append({
            'signal': k,
            'n': d['n'],
            'w': d['w'], 'l': d['l'], 'p': d['p'],
            'hit_pct': round(100*d['w']/graded, 1) if graded else 0,
            'by_family': {fam: {
                'n': fd['n'], 'w': fd['w'], 'l': fd['l'], 'p': fd['p'],
                'hit_pct': round(100*fd['w']/(fd['w']+fd['l']), 1) if (fd['w']+fd['l']) else 0,
            } for fam, fd in d['by_family'].items() if (fd['w']+fd['l']) >= 10},
        })
    filtered.sort(key=lambda x: -x['hit_pct'])

    return {
        'sport': sport, 'days': days,
        'family_filter': family_filter,
        'min_sample': min_sample,
        'total_graded': len([r for r in rows if _outcome(r.get('result'))]),
        'signals': filtered,
    }


def _print(rep: dict) -> None:
    print(f'\n{"=" * 80}')
    filt = f' family={rep["family_filter"]}' if rep.get('family_filter') else ''
    print(f'  SIGNAL CONTRIBUTION · {rep["sport"]} · last {rep["days"]}d{filt}')
    print(f'{"=" * 80}')
    print(f'Total graded props: {rep["total_graded"]}')
    print(f'Signals with n>={rep["min_sample"]}: {len(rep["signals"])}\n')

    # Legend
    print(f'{"SIGNAL":<32} {"n":>5} {"W":>4} {"L":>4} {"P":>3} {"HIT%":>7} {"vs 52.4":>9}')
    print('-' * 80)
    for s in rep['signals']:
        edge_pp = s['hit_pct'] - 52.4  # -110 breakeven
        flag = '✅' if edge_pp >= 5 else ('🟢' if edge_pp >= 2 else ('🟡' if edge_pp >= -2 else ('🟠' if edge_pp >= -5 else '🚨')))
        print(f'  {flag} {s["signal"]:<27} {s["n"]:>5} {s["w"]:>4} {s["l"]:>4} {s["p"]:>3} {s["hit_pct"]:>6.1f}% {edge_pp:>+8.1f}')

    # Family drill-down on top movers
    top_5 = rep['signals'][:5]
    if top_5 and any(s['by_family'] for s in top_5):
        print(f'\n{"-" * 80}\nTop 5 signals — family breakdown:')
        for s in top_5:
            if not s['by_family']: continue
            print(f'\n  {s["signal"]} (overall {s["hit_pct"]}% n={s["n"]}):')
            for fam, fd in sorted(s['by_family'].items(), key=lambda x:-x[1]['hit_pct']):
                edge = fd['hit_pct'] - 52.4
                flag = '✅' if edge >= 5 else ('🟢' if edge >= 2 else ('🟡' if edge >= -2 else '🚨'))
                print(f'    {flag} {fam:<20} {fd["hit_pct"]:>5.1f}% ({fd["w"]}-{fd["l"]}-{fd["p"]})')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=list(SPORT_REG.keys()))
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--family', default=None, help='restrict to one prop family (e.g. ks_over)')
    p.add_argument('--min-sample', type=int, default=20)
    p.add_argument('--json', default=None)
    args = p.parse_args()
    rep = analyze(args.sport, args.days, args.family, args.min_sample)
    _print(rep)
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(rep, f, indent=2, default=str)
        print(f'\nJSON written to {args.json}')


if __name__ == '__main__':
    main()
