"""Audit ML v1 shadow predictions vs composite vs actuals."""
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}


def main():
    days_filter = None
    if '--days' in sys.argv:
        days_filter = int(sys.argv[sys.argv.index('--days') + 1])

    r = requests.get(
        f'{SU}/rest/v1/jerry_cache?cache_key=like.ml_v1_shadow_%25'
        f'&select=cache_key,data,fetched_at&order=cache_key.desc',
        headers=H, timeout=15,
    )
    caches = r.json() if r.status_code == 200 else []
    if not caches:
        print('No ML v1 shadow cache entries yet.')
        return

    cutoff = (date.today() - timedelta(days=days_filter)).isoformat() if days_filter else '1900-01-01'

    all_preds = []
    for c in caches:
        if not isinstance(c, dict): continue
        key = c.get('cache_key', '')
        d = key.replace('ml_v1_shadow_', '')
        if d < cutoff: continue
        data = c.get('data') or {}
        for gid, pred in (data.get('predictions_by_game_id') or {}).items():
            all_preds.append({**pred, 'game_date': d, 'game_id': gid})

    if not all_preds:
        print(f'No predictions in window (cutoff: {cutoff})')
        return

    dates = sorted(set(p['game_date'] for p in all_preds))
    print(f'Shadow dates: {len(dates)} ({dates[0]} to {dates[-1]})')

    actuals = {}
    for d in dates:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?game_date=eq.{d}'
            f'&home_score=not.is.null'
            f'&select=game_id,game_date,away_team,home_team,close_spread,'
            f'home_score,away_score,projected_spread,home_ml_close',
            headers=H, timeout=15,
        )
        if r.status_code == 200:
            for g in r.json():
                actuals[str(g['game_id'])] = g

    print(f'Resolved games: {len(actuals)}')

    overall_v1 = defaultdict(lambda: [0, 0])
    overall_composite = defaultdict(lambda: [0, 0])
    per_day = defaultdict(lambda: {'v1': [0, 0], 'comp': [0, 0]})
    detailed = []

    for p in all_preds:
        gid = p.get('game_id')
        actual = actuals.get(str(gid))
        if not actual: continue
        if actual['home_score'] == actual['away_score']: continue
        actual_dir = 'HOME' if actual['home_score'] > actual['away_score'] else 'AWAY'

        v1_p = float(p['v1_p_home'])
        if v1_p >= 0.60:
            v1_call = ('HOME', 'conf')
        elif v1_p <= 0.40:
            v1_call = ('AWAY', 'conf')
        elif v1_p > 0.50:
            v1_call = ('HOME', 'lean')
        else:
            v1_call = ('AWAY', 'lean')
        bucket = f'{v1_call[0]}_{v1_call[1]}'
        won = (v1_call[0] == actual_dir)
        overall_v1[bucket][0 if won else 1] += 1
        per_day[p['game_date']]['v1'][0 if won else 1] += 1

        # Composite call: from projected_spread sign (positive = home)
        cs = actual.get('projected_spread')
        if cs is not None:
            cs = float(cs)
            if cs > 0.3: cc = 'HOME'
            elif cs < -0.3: cc = 'AWAY'
            else: cc = None
            if cc:
                cwon = (cc == actual_dir)
                overall_composite[cc][0 if cwon else 1] += 1
                per_day[p['game_date']]['comp'][0 if cwon else 1] += 1

        detailed.append({
            'date': p['game_date'],
            'matchup': f'{p["away_team"][:14]}@{p["home_team"][:14]}',
            'home_ml': p.get('home_ml'),
            'actual': actual_dir,
            'v1_p_home': v1_p,
            'v1_call': '/'.join(v1_call),
            'v1_win': 'OK' if won else 'X',
        })

    print()
    print('=== v1 ML by bucket ===')
    for b in ['HOME_conf', 'HOME_lean', 'AWAY_conf', 'AWAY_lean']:
        w, l = overall_v1.get(b, [0, 0])
        n = w + l
        rate = f'{100*w/n:.0f}%' if n else '-'
        print(f'  {b:>11s}: {w}-{l} ({rate}, n={n})')

    print()
    print('=== composite by direction ===')
    for d in ['HOME', 'AWAY']:
        w, l = overall_composite.get(d, [0, 0])
        n = w + l
        rate = f'{100*w/n:.0f}%' if n else '-'
        print(f'  {d:>11s}: {w}-{l} ({rate}, n={n})')

    print()
    v1_total = sum(sum(v) for v in overall_v1.values())
    v1_wins = sum(v[0] for v in overall_v1.values())
    c_total = sum(sum(v) for v in overall_composite.values())
    c_wins = sum(v[0] for v in overall_composite.values())
    print(f'OVERALL v1 (all picks):  {v1_wins}/{v1_total} ({100*v1_wins/max(1,v1_total):.1f}%)')
    print(f'OVERALL composite:       {c_wins}/{c_total} ({100*c_wins/max(1,c_total):.1f}%)')

    print()
    print('=== per-day comparison ===')
    print(f'{"date":>12s} | {"v1 W-L":>10s} | {"comp W-L":>10s} | diff')
    for d in sorted(per_day):
        v = per_day[d]['v1']
        c = per_day[d]['comp']
        diff = (100*v[0]/max(1,sum(v))) - (100*c[0]/max(1,sum(c)))
        print(f'  {d:>10s} | {v[0]}-{v[1]:<5} | {c[0]}-{c[1]:<5} | {diff:+.0f}pp')

    print()
    print('=== last 20 detailed ===')
    print(f'{"date":>10s} | {"matchup":>30s} | {"home_ml":>7s} | {"result":>6s} | {"p_home":>6s} | {"call":>12s} | win')
    for r in detailed[-20:]:
        print(f'  {r["date"]:>8s} | {r["matchup"]:>28s} | {str(r["home_ml"]):>7s} | {r["actual"]:>6s} | {r["v1_p_home"]:>6.2f} | {r["v1_call"]:>12s} | {r["v1_win"]}')


if __name__ == '__main__':
    main()
