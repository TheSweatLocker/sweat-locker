"""Audit v7 shadow predictions vs composite vs actuals.

Pulls all jerry_cache entries matching v7_total_shadow_*, joins to actual
game outcomes from mlb_game_results, reports running v7 vs composite hit rate.

The composite is what we ship today (projected_total vs line). v7 is the
shadow model. If v7 beats composite over 14 days, promote v7 to production.

Usage:
    python audit_v7_shadow.py            # All available days
    python audit_v7_shadow.py --days 7   # Last 7 days only
"""
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


def pull_shadow_caches():
    """All v7_total_shadow_* cache entries."""
    r = requests.get(
        f'{SU}/rest/v1/jerry_cache?cache_key=like.v7_total_shadow_%25'
        f'&select=cache_key,data,fetched_at&order=cache_key.desc',
        headers=H, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def pull_actuals(dates):
    """Pull actual results for the given dates."""
    if not dates:
        return {}
    rows = []
    for d in dates:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?game_date=eq.{d}'
            f'&home_score=not.is.null&close_total=not.is.null'
            f'&select=game_id,game_date,away_team,home_team,close_total,'
            f'home_score,away_score,projected_total',
            headers=H, timeout=15,
        )
        if r.status_code == 200:
            rows.extend(r.json())
    return {str(g['game_id']): g for g in rows}


def main():
    days_filter = None
    if '--days' in sys.argv:
        days_filter = int(sys.argv[sys.argv.index('--days') + 1])

    caches = pull_shadow_caches()
    if not caches:
        print('No v7 shadow cache entries found yet.')
        return

    cutoff = (date.today() - timedelta(days=days_filter)).isoformat() if days_filter else '1900-01-01'

    # Collect all dates and game predictions
    all_preds = []  # list of {date, game_id, line, composite_proj, v7_p_over, v7_rec, away, home}
    for c in caches:
        if not isinstance(c, dict): continue
        key = c.get('cache_key', '')
        d = key.replace('v7_total_shadow_', '')
        if d < cutoff: continue
        data = c.get('data') or {}
        for gid, pred in (data.get('predictions_by_game_id') or {}).items():
            all_preds.append({**pred, 'game_date': d, 'game_id': gid})

    if not all_preds:
        print(f'No predictions in window (cutoff: {cutoff})')
        return

    dates = sorted(set(p['game_date'] for p in all_preds))
    print(f'Shadow dates: {len(dates)} ({dates[0]} to {dates[-1]})')
    print(f'Total predictions: {len(all_preds)}')

    actuals = pull_actuals(dates)
    print(f'Resolved games: {len(actuals)}')

    # Tally per-day and overall
    overall_v7 = defaultdict(lambda: [0, 0])  # bucket -> [wins, losses]
    overall_composite = defaultdict(lambda: [0, 0])
    per_day = defaultdict(lambda: {'v7': [0, 0], 'comp': [0, 0]})

    detailed = []
    for p in all_preds:
        gid = p.get('game_id')
        actual = actuals.get(str(gid))
        if not actual: continue
        line = float(actual['close_total'])
        total = float(actual['home_score']) + float(actual['away_score'])
        if total == line: continue  # push
        actual_dir = 'OVER' if total > line else 'UNDER'

        v7_p = float(p['v7_p_over'])
        composite_proj = p.get('composite_proj')

        # v7 calls
        v7_call = None
        if v7_p <= 0.40: v7_call = ('UNDER', 'conf')
        elif v7_p >= 0.60: v7_call = ('OVER', 'conf')
        elif v7_p <= 0.50: v7_call = ('UNDER', 'lean')
        elif v7_p > 0.50: v7_call = ('OVER', 'lean')

        if v7_call:
            bucket = f'{v7_call[0]}_{v7_call[1]}'
            won = (v7_call[0] == actual_dir)
            overall_v7[bucket][0 if won else 1] += 1
            per_day[p['game_date']]['v7'][0 if won else 1] += 1

        # Composite call (projected_total vs line)
        if composite_proj is not None:
            cp = float(composite_proj)
            if cp > line + 0.3:
                cc = 'OVER'
            elif cp < line - 0.3:
                cc = 'UNDER'
            else:
                cc = None
            if cc:
                won = (cc == actual_dir)
                overall_composite[cc][0 if won else 1] += 1
                per_day[p['game_date']]['comp'][0 if won else 1] += 1

        detailed.append({
            'date': p['game_date'],
            'matchup': f'{p["away_team"][:14]} @ {p["home_team"][:14]}',
            'line': line,
            'actual': total,
            'result': actual_dir,
            'v7_p_over': v7_p,
            'v7_call': '/'.join(v7_call) if v7_call else '-',
            'v7_win': 'OK' if v7_call and v7_call[0] == actual_dir else 'X',
            'composite': composite_proj,
            'comp_win': 'OK' if composite_proj is not None and ((float(composite_proj) > line + 0.3 and actual_dir == 'OVER') or (float(composite_proj) < line - 0.3 and actual_dir == 'UNDER')) else ('X' if composite_proj is not None and abs(float(composite_proj) - line) > 0.3 else '-'),
        })

    print()
    print('=== v7 by bucket ===')
    for b in ['UNDER_conf', 'UNDER_lean', 'OVER_conf', 'OVER_lean']:
        w, l = overall_v7.get(b, [0, 0])
        n = w + l
        rate = f'{100*w/n:.0f}%' if n else '-'
        print(f'  {b:>12s}: {w}-{l} ({rate}, n={n})')

    print()
    print('=== composite by direction ===')
    for d in ['OVER', 'UNDER']:
        w, l = overall_composite.get(d, [0, 0])
        n = w + l
        rate = f'{100*w/n:.0f}%' if n else '-'
        print(f'  {d:>12s}: {w}-{l} ({rate}, n={n})')

    print()
    v7_total = sum(sum(v) for v in overall_v7.values())
    v7_wins = sum(v[0] for v in overall_v7.values())
    c_total = sum(sum(v) for v in overall_composite.values())
    c_wins = sum(v[0] for v in overall_composite.values())
    print(f'OVERALL v7 (all picks):   {v7_wins}/{v7_total} ({100*v7_wins/max(1,v7_total):.1f}%)')
    print(f'OVERALL composite:        {c_wins}/{c_total} ({100*c_wins/max(1,c_total):.1f}%)')

    # Per-day table
    print()
    print('=== per-day comparison ===')
    print(f'{"date":>12s} | {"v7 W-L":>10s} | {"comp W-L":>10s} | diff')
    for d in sorted(per_day):
        v = per_day[d]['v7']
        c = per_day[d]['comp']
        v_str = f'{v[0]}-{v[1]}'
        c_str = f'{c[0]}-{c[1]}'
        diff = (100*v[0]/max(1,sum(v))) - (100*c[0]/max(1,sum(c)))
        print(f'  {d:>10s} | {v_str:>10s} | {c_str:>10s} | {diff:+.0f}pp')

    print()
    print('=== detailed game-by-game ===')
    print(f'{"date":>10s} | {"matchup":>30s} | {"line":>5s} | {"total":>5s} | {"result":>6s} | {"v7_p":>5s} | {"v7":>16s} | {"v7_W":>5s} | {"comp":>5s} | {"cW":>4s}')
    for r in detailed[-25:]:  # last 25 games
        print(f'  {r["date"]:>8s} | {r["matchup"]:>28s} | {r["line"]:>5} | {r["actual"]:>5.0f} | {r["result"]:>6s} | {r["v7_p_over"]:>5.2f} | {r["v7_call"]:>16s} | {r["v7_win"]:>5s} | {str(r["composite"]):>5s} | {r["comp_win"]:>4s}')


if __name__ == '__main__':
    main()
