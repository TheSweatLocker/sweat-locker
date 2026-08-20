"""Prop playbook shadow-mode audit (2026-08-17).

Compares legacy prop tier/conviction against playbook shadow decisions
over a rolling window. Cutover from legacy to playbook triggers when
playbook meets or beats legacy hit-rate on the same picks over 14
consecutive days.

Runs nightly after grading. Reports:
  * Agreement rate (both same tier / both BACK / both FADE)
  * Disagreement wins/losses (when they differ, who's right?)
  * Hit-rate by (system, tier): legacy PRIME/STRONG vs playbook PRIME/STRONG
  * ROI at flat 1u, real book odds where available

CLI:
  python audit_prop_playbook_shadow.py                # last 14d
  python audit_prop_playbook_shadow.py --days 7 --sport MLB
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict, Counter

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

PROPS_TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}


def _american_to_payout(odds):
    if odds is None: return 0.91
    try: o = int(odds)
    except (TypeError, ValueError): return 0.91
    if o >= 100: return o / 100.0
    if o <= -100: return 100.0 / abs(o)
    return 0.91


def fetch_decisions(sport: str, days: int) -> list[dict]:
    """Playbook decisions in window."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                     headers=H,
                     params={'sport': f'eq.{sport}',
                             'game_date': f'gte.{cutoff}',
                             'game_date': f'lte.{yday}',
                             'select': '*', 'limit': '5000'},
                     timeout=30)
    return r.json() if r.status_code == 200 else []


def fetch_prop_results(sport: str, days: int) -> dict:
    """Map (player, prop_type, direction, prop_line, game_date) → (result, book_line)."""
    table = PROPS_TABLE.get(sport)
    if not table: return {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H,
                     params={'game_date': f'gte.{cutoff}',
                             'game_date': f'lte.{yday}',
                             'result': 'not.is.null',
                             'select': 'player_name,prop_type,direction,prop_line,game_date,result,book_line,tier,refit_conviction',
                             'limit': '5000'},
                     timeout=30)
    rows = r.json() if r.status_code == 200 else []
    return {(r['player_name'], r['prop_type'], r['direction'],
             r.get('prop_line'), r['game_date']): r for r in rows}


def run(sport: str, days: int):
    decisions = fetch_decisions(sport, days)
    results = fetch_prop_results(sport, days)
    print(f'=== prop playbook shadow audit · {sport} · last {days}d ===')
    print(f'  {len(decisions)} playbook decisions · {len(results)} graded legacy props\n')

    if not decisions:
        print('  no playbook data yet — run prop_ensemble_scorer.py in shadow mode first')
        return

    # Match decisions to graded results
    matched = 0
    agree_tier = 0
    agree_side = 0  # both BACK or both FADE
    playbook_only_pick = 0  # legacy PASS, playbook picks
    legacy_only_pick = 0    # playbook PASS, legacy picks
    stat = {
        'legacy': defaultdict(lambda: {'w':0,'l':0,'p':0,'units':0.0}),
        'playbook': defaultdict(lambda: {'w':0,'l':0,'p':0,'units':0.0}),
        'agree_win': 0, 'agree_loss': 0,
        'diff_playbook_right': 0, 'diff_legacy_right': 0, 'diff_both_wrong': 0,
    }
    tier_dist_p = Counter()
    tier_dist_l = Counter()

    for d in decisions:
        key = (d['player_name'], d['prop_type'], d['direction'],
               d.get('prop_line'), d['game_date'])
        r = results.get(key)
        if not r: continue
        matched += 1
        legacy_tier = (r.get('tier') or '').upper()
        pb_tier = (d.get('playbook_tier') or '').upper()
        pb_side = (d.get('playbook_side') or '').upper()  # BACK/FADE/PASS
        # Legacy side: BACK if tier PRIME/STRONG/LEAN, PASS if SKIP/COVERAGE
        legacy_side = 'BACK' if legacy_tier in ('PRIME','STRONG','LEAN') else 'PASS'

        tier_dist_p[pb_tier] += 1
        tier_dist_l[legacy_tier] += 1

        # Legacy result — grades over/under directly
        result = r.get('result')  # 'Win'/'Loss'/'Push'
        payout = _american_to_payout(r.get('book_line'))

        # Legacy scoring: 1u per BACK (skip PASS)
        if legacy_side == 'BACK':
            s = stat['legacy'][legacy_tier or 'OTHER']
            if result == 'Win': s['w'] += 1; s['units'] += payout
            elif result == 'Loss': s['l'] += 1; s['units'] -= 1.0
            elif result == 'Push': s['p'] += 1

        # Playbook scoring: BACK/FADE flip the outcome
        if pb_side == 'BACK':
            s = stat['playbook'][pb_tier or 'OTHER']
            if result == 'Win': s['w'] += 1; s['units'] += payout
            elif result == 'Loss': s['l'] += 1; s['units'] -= 1.0
            elif result == 'Push': s['p'] += 1
        elif pb_side == 'FADE':
            s = stat['playbook'][pb_tier or 'OTHER']
            if result == 'Loss': s['w'] += 1; s['units'] += payout  # fade wins on Loss
            elif result == 'Win': s['l'] += 1; s['units'] -= 1.0
            elif result == 'Push': s['p'] += 1

        # Agreement
        if pb_tier == legacy_tier: agree_tier += 1
        if pb_side == legacy_side and pb_side != 'PASS': agree_side += 1
        if pb_side != 'PASS' and legacy_side == 'PASS': playbook_only_pick += 1
        if pb_side == 'PASS' and legacy_side != 'PASS': legacy_only_pick += 1

        # Disagreement analysis
        if pb_side != legacy_side:
            # Determine who was right
            legacy_right = (legacy_side == 'BACK' and result == 'Win') or (legacy_side == 'PASS')
            pb_right = (pb_side == 'BACK' and result == 'Win') or \
                        (pb_side == 'FADE' and result == 'Loss') or (pb_side == 'PASS')
            if legacy_right and not pb_right: stat['diff_legacy_right'] += 1
            elif pb_right and not legacy_right: stat['diff_playbook_right'] += 1
            elif not pb_right and not legacy_right: stat['diff_both_wrong'] += 1

    def _fmt(s):
        n = s['w'] + s['l']
        if n == 0: return '(no bets)'
        hr = round(100 * s['w'] / n, 1)
        roi = round(100 * s['units'] / n, 1)
        return f'{s["w"]}-{s["l"]}-{s["p"]}  HR={hr}%  ROI={roi:+.1f}%  ({s["units"]:+.2f}u)'

    print(f'  matched: {matched}/{len(decisions)}')
    print(f'  agree on tier: {agree_tier}/{matched} ({100*agree_tier/matched:.1f}% if matched else "-")%')
    print(f'  agree on side: {agree_side}/{matched}')
    print(f'  playbook picks WHERE legacy passed: {playbook_only_pick}')
    print(f'  legacy picks WHERE playbook passed: {legacy_only_pick}\n')

    print('LEGACY tier performance:')
    for t in ('PRIME','STRONG','LEAN','OTHER'):
        s = stat['legacy'].get(t)
        if s and s['w']+s['l']+s['p'] > 0:
            print(f'  {t:<7} {_fmt(s)}')
    print()
    print('PLAYBOOK tier performance:')
    for t in ('PRIME','STRONG','LEAN','OTHER'):
        s = stat['playbook'].get(t)
        if s and s['w']+s['l']+s['p'] > 0:
            print(f'  {t:<7} {_fmt(s)}')

    print(f'\nDISAGREEMENT winners:')
    print(f'  playbook right: {stat["diff_playbook_right"]}')
    print(f'  legacy right:   {stat["diff_legacy_right"]}')
    print(f'  both wrong:     {stat["diff_both_wrong"]}')

    print(f'\nTier distribution:')
    print(f'  playbook: {dict(tier_dist_p.most_common())}')
    print(f'  legacy:   {dict(tier_dist_l.most_common())}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=list(PROPS_TABLE.keys()))
    p.add_argument('--days', type=int, default=14)
    args = p.parse_args()
    run(args.sport, args.days)


if __name__ == '__main__':
    main()
