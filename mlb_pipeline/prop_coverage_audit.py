"""Sport-universal prop coverage audit (2026-08-23).

PURPOSE
-------
For each active prop family (ks, ha, bb, hits, passing_yards, etc.),
check what signals SHOULD fire vs what actually did fire. Reports gaps
so the wire-up backlog per sport is visible.

Uses render_prop_template._STAT_META as the source of truth for the
per-family "relevant_ctx" checklist. Same table drives the coverage
chip on the app card — so audit findings map 1:1 to what users see.

Runs post-cron. Writes a summary to stdout + optionally to a report
file for tracking gap-closure velocity over time.

DESIGN
------
This is a READ-ONLY diagnostic — never modifies the DB. Runs in a
few seconds per sport. Safe to add to any cron.

Not sport-specific: --sport MLB / NFL / NCAAF / NCAAB / NHL / UFC.
Registry below maps sport to (ctx_table, props_table).

Usage
-----
    python prop_coverage_audit.py                  # today ET, MLB
    python prop_coverage_audit.py --sport NFL      # NFL today
    python prop_coverage_audit.py --date 2026-08-23
    python prop_coverage_audit.py --sport MLB --json report.json
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict, Counter
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

# Sport → (props_table, ctx_table, sport_str_for_prop_jerry)
SPORT_REG = {
    'MLB':   ('mlb_pipeline_props', 'mlb_game_context', 'MLB'),
    'NFL':   ('nfl_pipeline_props', 'nfl_game_context', 'NFL'),
    'NCAAF': ('ncaaf_pipeline_props', 'ncaaf_game_context', 'NCAAF'),
    'NCAAB': ('ncaab_pipeline_props', 'ncaab_game_context', 'NCAAB'),
    'NHL':   ('nhl_pipeline_props', 'nhl_game_context', 'NHL'),
    'NBA':   ('nba_pipeline_props', 'nba_game_context', 'NBA'),
}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _stat_family(prop_type: str) -> str:
    if not prop_type: return ''
    for suf in ('_over', '_under'):
        if prop_type.endswith(suf): return prop_type[:-len(suf)]
    return prop_type


def _fired_keys(signals) -> set:
    if not isinstance(signals, dict): return set()
    return {k for k in signals.keys() if not k.startswith('_')}


def audit(sport: str, game_date: str, verbose: bool = False) -> dict:
    """Return report dict for the sport/date."""
    if sport not in SPORT_REG:
        return {'error': f'unknown sport: {sport}'}
    props_table, ctx_table, sport_str = SPORT_REG[sport]

    # Pull STAT_META checklist from template
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from render_prop_template import _STAT_META, _coverage_from_ctx
    except ImportError as e:
        return {'error': f'render_prop_template import failed: {e}'}

    # Fetch props for the date
    r = requests.get(f'{SB}/rest/v1/{props_table}',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': 'id,player_name,prop_type,direction,game_id,tier,'
                                       'conviction,refit_conviction,signals',
                             'limit': 1000},
                     timeout=20)
    if r.status_code != 200:
        return {'error': f'props fetch {r.status_code}'}
    props = r.json() or []
    if not props:
        return {'sport': sport, 'game_date': game_date, 'props_count': 0,
                'summary': 'no props for this date'}

    # Fetch ctx rows to enrich coverage check
    cr = requests.get(f'{SB}/rest/v1/{ctx_table}',
                      headers=H_READ,
                      params={'game_date': f'eq.{game_date}', 'select': '*'},
                      timeout=20)
    ctx_by_gid = {}
    if cr.status_code == 200:
        for row in (cr.json() or []):
            ctx_by_gid[row.get('game_id')] = row

    # Aggregate per family
    per_family: dict = defaultdict(lambda: {
        'count': 0, 'fired_counts': [], 'missing_signals': Counter(),
        'checklist_size': 0, 'checklist_items': None,
    })

    for p in props:
        fam = _stat_family(p.get('prop_type') or '')
        if not fam: continue
        meta = _STAT_META.get(fam, {})
        checklist = meta.get('relevant_ctx', []) or []

        fired = _fired_keys(p.get('signals'))
        ctx = ctx_by_gid.get(p.get('game_id')) or {}

        # Use template's coverage-check to know which items are missing
        covered_n, missing = _coverage_from_ctx(checklist, ctx, p.get('signals'), None)

        per_family[fam]['count'] += 1
        per_family[fam]['fired_counts'].append(len(fired))
        per_family[fam]['checklist_size'] = len(checklist)
        per_family[fam]['checklist_items'] = checklist
        for m in missing:
            per_family[fam]['missing_signals'][m] += 1

    # Build report
    report = {
        'sport': sport, 'game_date': game_date, 'props_count': len(props),
        'families': {},
    }
    for fam, agg in sorted(per_family.items(), key=lambda x: -x[1]['count']):
        n = agg['count']
        avg_fired = sum(agg['fired_counts'])/n if n else 0
        checklist_size = agg['checklist_size']
        gap_leaderboard = agg['missing_signals'].most_common()
        report['families'][fam] = {
            'count': n,
            'avg_fired_signals': round(avg_fired, 1),
            'checklist_size': checklist_size,
            'signals_missing_by_pct': [
                {'signal': sig, 'missing_count': cnt, 'missing_pct': round(100*cnt/n, 1)}
                for sig, cnt in gap_leaderboard
            ],
            'checklist': agg['checklist_items'],
        }
    return report


def _print_report(rep: dict) -> None:
    if rep.get('error'):
        print(f'ERROR: {rep["error"]}'); return
    if rep.get('props_count') == 0:
        print(f'{rep["sport"]} {rep["game_date"]}: no props to audit'); return
    print(f'\n=== PROP COVERAGE AUDIT · {rep["sport"]} · {rep["game_date"]} ===')
    print(f'Total props: {rep["props_count"]}\n')
    for fam, data in rep['families'].items():
        avg = data['avg_fired_signals']
        cs = data['checklist_size']
        n = data['count']
        pct = round(100 * avg / max(1, cs), 0) if cs else 0
        print(f'{fam:<20} {n:>3} props · avg {avg}/{cs} signals fired ({pct}% coverage)')
        for gap in data['signals_missing_by_pct'][:5]:
            flag = '🚨' if gap['missing_pct'] >= 80 else ('⚠️' if gap['missing_pct'] >= 40 else '·')
            print(f"  {flag} {gap['signal']:<25} missing on {gap['missing_count']}/{n} props ({gap['missing_pct']}%)")
        print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=list(SPORT_REG.keys()))
    p.add_argument('--date', default=None)
    p.add_argument('--json', default=None, help='write JSON report to path')
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()
    gd = args.date or _today_et()
    rep = audit(args.sport, gd, verbose=args.verbose)
    _print_report(rep)
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(rep, f, indent=2, default=str)
        print(f'\nJSON report written to {args.json}')


if __name__ == '__main__':
    main()
