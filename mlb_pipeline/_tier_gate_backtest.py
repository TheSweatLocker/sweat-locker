"""Backtest the tier discipline gate on historical data.

Validates:
  - Picks per night (should be 1-5)
  - Hit rate per tier (PRIME 65-71%, STRONG 57-60%, LEAN 55%)
  - Total picks per month
  - vs current composite (which publishes everything with any gap)
"""
import os
from collections import defaultdict
import requests
import pandas as pd
from dotenv import load_dotenv

import tier_discipline_gate as gate

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}


def pull():
    rows = []; off = 0
    sel = 'game_date,home_team,away_team,close_total,projected_total,model_pred_total,jerry_pred_total,home_score,away_score,signal_confluence_net'
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'&close_total=not.is.null&projected_total=not.is.null'
            f'&select={sel}&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def main():
    df = pd.DataFrame(pull())
    for c in df.columns:
        if c not in ('game_date', 'home_team', 'away_team'):
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df['game_date'] = pd.to_datetime(df['game_date'])
    df['total'] = df['home_score'] + df['away_score']
    df['actual_dir'] = df.apply(lambda r: 'OVER' if r['total'] > r['close_total'] else ('UNDER' if r['total'] < r['close_total'] else 'PUSH'), axis=1)
    df = df[df['actual_dir'] != 'PUSH'].copy()

    print(f'Total games: {len(df)}')
    print(f'Date range: {df.game_date.min().date()} -> {df.game_date.max().date()}')
    print()

    # Apply gate to each game
    verdicts = []
    for _, row in df.iterrows():
        v = gate.evaluate_total(
            line=row['close_total'],
            proj_total=row['projected_total'],
            v4_total=row['model_pred_total'],
            jerry_total=row['jerry_pred_total'],
        )
        verdicts.append({
            'date': row['game_date'],
            'matchup': f'{row["away_team"][:14]} @ {row["home_team"][:14]}',
            'line': row['close_total'],
            'actual_total': row['total'],
            'actual_dir': row['actual_dir'],
            'tier': v.tier,
            'direction': v.direction,
            'composite_gap': v.composite_gap,
            'historical_hit_rate': v.historical_hit_rate,
            'reason': v.reason,
        })

    vdf = pd.DataFrame(verdicts)

    # Per-tier hit rate
    print('=' * 90)
    print('1) GATE OUTPUT — picks per tier and hit rate')
    print('=' * 90)
    for tier in ['ELITE', 'PRIME', 'STRONG', 'LEAN']:
        sub = vdf[vdf['tier'] == tier]
        if len(sub) == 0:
            print(f'  {tier:>8s}: 0 picks')
            continue
        hits = (sub['direction'] == sub['actual_dir']).sum()
        n = len(sub)
        avg_hist = sub['historical_hit_rate'].mean()
        print(f'  {tier:>8s}: n={n:>4d}, hit {hits}/{n} ({100*hits/n:>5.1f}%) | predicted {100*avg_hist:.0f}% historical')

    skip_n = (vdf['tier'] == 'SKIP').sum()
    publish_n = len(vdf) - skip_n
    print(f'\n  Total publishable: {publish_n} ({100*publish_n/len(vdf):.0f}% of slate)')
    print(f'  Skipped: {skip_n} ({100*skip_n/len(vdf):.0f}% of slate)')

    # Picks per day
    print()
    print('=' * 90)
    print('2) PICKS PER DAY DISTRIBUTION')
    print('=' * 90)
    daily = vdf[vdf['tier'] != 'SKIP'].groupby(vdf['date'].dt.date).size()
    if len(daily) > 0:
        print(f'  Min picks/day: {daily.min()}')
        print(f'  Max picks/day: {daily.max()}')
        print(f'  Mean picks/day: {daily.mean():.1f}')
        print(f'  Median picks/day: {daily.median():.0f}')
        # Distribution
        print('  Distribution:')
        bins = pd.cut(daily, bins=[0, 1, 2, 3, 5, 99], labels=['0', '1', '2', '3-5', '5+'])
        print(bins.value_counts().sort_index().to_string())

    # Per direction
    print()
    print('=' * 90)
    print('3) HIT RATE BY DIRECTION')
    print('=' * 90)
    for d in ['OVER', 'UNDER']:
        sub = vdf[(vdf['tier'] != 'SKIP') & (vdf['direction'] == d)]
        n = len(sub)
        if n == 0: continue
        hits = (sub['actual_dir'] == d).sum()
        print(f'  {d:>6s}: {hits}/{n} ({100*hits/n:.1f}%)')

    # Per month
    print()
    print('=' * 90)
    print('4) PER-MONTH PERFORMANCE')
    print('=' * 90)
    print(f'{"month":>10s} | {"n picks":>10s} | {"hit rate":>10s} | {"OVER":>8s} | {"UNDER":>8s}')
    pub = vdf[vdf['tier'] != 'SKIP'].copy()
    pub['month'] = pub['date'].dt.strftime('%Y-%m')
    for m in sorted(pub['month'].unique()):
        sub = pub[pub['month'] == m]
        n = len(sub)
        hits = (sub['direction'] == sub['actual_dir']).sum()
        over_n = (sub['direction'] == 'OVER').sum()
        under_n = (sub['direction'] == 'UNDER').sum()
        print(f'  {m:>8s} | {n:>10d} | {hits}/{n} ({100*hits/n:.0f}%) | {over_n:>8d} | {under_n:>8d}')

    # Compare against "publish everything composite >0.3" (current behavior approximation)
    print()
    print('=' * 90)
    print('5) GATE vs CURRENT COMPOSITE')
    print('=' * 90)
    naive_publishes = df[df['projected_total'].notna() & df['close_total'].notna()].copy()
    naive_publishes['gap'] = naive_publishes['projected_total'] - naive_publishes['close_total']
    naive_pub = naive_publishes[abs(naive_publishes['gap']) >= 0.3].copy()
    naive_pub['pred_dir'] = naive_pub['gap'].apply(lambda g: 'OVER' if g > 0 else 'UNDER')
    naive_hits = (naive_pub['pred_dir'] == naive_pub['actual_dir']).sum()
    print(f'  Current behavior (any gap >= 0.3): n={len(naive_pub)}, hits {naive_hits}/{len(naive_pub)} ({100*naive_hits/len(naive_pub):.1f}%)')
    gate_pub = vdf[vdf['tier'] != 'SKIP']
    gate_hits = (gate_pub['direction'] == gate_pub['actual_dir']).sum()
    print(f'  Tier gate: n={len(gate_pub)}, hits {gate_hits}/{len(gate_pub)} ({100*gate_hits/len(gate_pub):.1f}%)')

    # Last 14d sample
    print()
    print('=' * 90)
    print('6) LAST 14 DAYS — example slate filter')
    print('=' * 90)
    recent = vdf[vdf['date'] >= vdf['date'].max() - pd.Timedelta(days=14)].copy()
    for d in sorted(recent['date'].dt.date.unique())[-7:]:
        day = recent[recent['date'].dt.date == d]
        pubs = day[day['tier'] != 'SKIP']
        n_total = len(day)
        n_pub = len(pubs)
        if n_pub == 0:
            print(f'  {d}: {n_total} games -> 0 published')
            continue
        hits = (pubs['direction'] == pubs['actual_dir']).sum()
        print(f'  {d}: {n_total} games -> {n_pub} pub ({hits}/{n_pub} hit)')
        for _, r in pubs.iterrows():
            mark = 'OK' if r['direction'] == r['actual_dir'] else 'XX'
            print(f'    {mark} {r["tier"]:>6} {r["direction"]:>5} line={r["line"]:.1f} actual={int(r["actual_total"]):>2}  {r["matchup"][:30]}')


if __name__ == '__main__':
    main()
