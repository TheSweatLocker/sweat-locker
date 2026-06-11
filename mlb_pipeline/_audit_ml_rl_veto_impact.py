"""
Show exactly what the recency veto stripped from ML/RL.
For every ML/RL play_type + cohort + direction, show:
  - Lifetime raw_pct + n
  - 30d raw_pct + n
  - What tier it WOULD have hit without recency veto
  - What recency_status the veto applied
"""
import sys
import os
from collections import Counter
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

import refresh_cohort_signals as rcs
from cohort_features import get_features


def run():
    print('[veto-audit] fetching graded games...')
    all_rows = rcs.fetch_rows()
    cutoff_30 = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    last30 = [r for r in all_rows if r.get('game_date') and r['game_date'] >= cutoff_30]
    print(f'  {len(all_rows)} lifetime / {len(last30)} last-30d')

    life_play_base, life_dir_base, life_tally = rcs.tally(all_rows, get_features)
    _, _, last30_tally = rcs.tally(last30, get_features)

    # Re-run rule emission logic but track WHICH rules were vetoed
    kept = []
    vetoed = []

    for (play, ck, dirn), t in life_tally.items():
        n = t['w'] + t['l']
        if n < rcs.MIN_RAW_N:
            continue
        raw_pct = round(100 * t['w'] / n, 1)
        if dirn == 'any':
            baseline = life_play_base.get(play) or 50.0
        else:
            baseline = life_dir_base.get((play, dirn)) or life_play_base.get(play) or 50.0
        shrunken = rcs.shrink(raw_pct, n, baseline)
        tier_name, delta, eligibility = rcs.tier_for(shrunken)
        if tier_name == 'NEUTRAL':
            continue

        # Apply recency veto
        last30_t = last30_tally.get((play, ck, dirn), {'w': 0, 'l': 0})
        last30_n = last30_t['w'] + last30_t['l']
        last30_pct = None
        if last30_n >= 5:
            last30_pct = round(100 * last30_t['w'] / last30_n, 1)
            drift = last30_pct - raw_pct
            life_dir = 'above' if raw_pct >= baseline else 'below'
            recent_dir = 'above' if last30_pct >= baseline else 'below'
            if abs(drift) >= rcs.RECENCY_VETO_DROP_PP and last30_n >= 10:
                vetoed.append({
                    'play': play, 'ck': ck, 'dir': dirn,
                    'raw_pct': raw_pct, 'n': n,
                    'last30_pct': last30_pct, 'last30_n': last30_n,
                    'drift': drift,
                    'shrunken': shrunken, 'tier': tier_name,
                    'reason': 'reversed' if life_dir != recent_dir else 'decaying',
                })
                continue
        kept.append({
            'play': play, 'ck': ck, 'dir': dirn,
            'raw_pct': raw_pct, 'n': n,
            'last30_pct': last30_pct, 'last30_n': last30_n,
            'shrunken': shrunken, 'tier': tier_name,
        })

    # Focus on ML/RL
    ml_rl_plays = ('v3_ml', 'v4_ml', 'jerry_ml', 'conf_ml', 'v3_rl', 'v4_rl', 'jerry_rl', 'conf_rl')
    ml_rl_vetoed = [v for v in vetoed if v['play'] in ml_rl_plays]
    ml_rl_kept = [k for k in kept if k['play'] in ml_rl_plays]

    print()
    print(f"ML/RL RULES TOTAL: {len(ml_rl_kept) + len(ml_rl_vetoed)}")
    print(f"  Kept after veto:  {len(ml_rl_kept)}")
    print(f"  Vetoed:           {len(ml_rl_vetoed)}")

    # Now break down by direction at the STRONG_EDGE+LOCK tier
    print()
    print('STRONG_EDGE+LOCK RULES BY DIRECTION (ML/RL only):')
    print(f"{'PLAY':<11}{'DIR':<7}{'KEPT':<7}{'VETOED':<8}{'TOTAL IF NO VETO'}")
    print('-' * 60)
    for play in ml_rl_plays:
        for dirn in ('home', 'away', 'any'):
            kept_n = sum(1 for k in ml_rl_kept
                         if k['play'] == play and k['dir'] == dirn
                         and k['tier'] in ('LOCK', 'STRONG_EDGE'))
            vetoed_n = sum(1 for v in ml_rl_vetoed
                           if v['play'] == play and v['dir'] == dirn
                           and v['tier'] in ('LOCK', 'STRONG_EDGE'))
            if kept_n + vetoed_n == 0:
                continue
            print(f'{play:<11}{dirn:<7}{kept_n:<7}{vetoed_n:<8}{kept_n + vetoed_n}')

    # Show actual vetoed STRONG_EDGE/LOCK rules
    print()
    print('VETOED STRONG_EDGE+LOCK ML/RL RULES (top 15 by lifetime shrunken_pct):')
    high_value_vetoed = sorted(
        [v for v in ml_rl_vetoed if v['tier'] in ('LOCK', 'STRONG_EDGE')],
        key=lambda v: -v['shrunken']
    )
    print(f"{'PLAY':<11}{'COHORT':<28}{'DIR':<7}{'LIFE %':<11}{'30d %':<10}{'DRIFT':<9}{'TIER LOST'}")
    print('-' * 95)
    for v in high_value_vetoed[:20]:
        print(f"{v['play']:<11}{v['ck'][:26]:<28}{v['dir']:<7}{v['raw_pct']:<6}({v['n']:<3}){'':<2}"
              f"{v['last30_pct']:<5}({v['last30_n']:<3}){'':<2}{v['drift']:+5.1f}    "
              f"{v['tier']} ({v['reason']})")


if __name__ == '__main__':
    run()
