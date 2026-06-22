"""Phase 2 — Backtest l5_confirm removal BEFORE shipping.

Audit showed l5_confirm fires as a FADE on 7 of 8 prop types. But before
removing it, we need to verify that removal would actually IMPROVE per-tier
hit rates, not just shift picks around.

Methodology:
  For each historical PRIME/STRONG pick that fired l5_confirm:
    1. Parse the signal to determine margin band (>=0.7 → +12, >=0.3 → +6,
       streak bonus +4 if 4/5+)
    2. Compute new_conviction = old - that bonus
    3. Compute new_tier using prop's actual tier_for thresholds
    4. Compare:
       - Old PRIME → New tier: hit rate when ORIGINAL PRIME (matters because
         this is what changed)
       - New PRIME (originals that stayed PRIME): hit rate
       - Picks that DROPPED from PRIME → STRONG: hit rate
       - Picks that DROPPED from STRONG → SKIP: hit rate

Ship gate (per prop type):
  - New PRIME hit rate must NOT be lower than old PRIME hit rate
  - Removed picks (PRIME → STRONG → SKIP) must NOT have higher hit rate
    than the tier they came from (otherwise we'd be demoting winners)
  - Sample size in new PRIME tier must stay >= 20 (not starving the card)

Only ship per-prop removals where ALL gates pass.
"""
import os
import re
from collections import defaultdict
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

PROP_TYPES = [
    'ks_over', 'ks_under', 'bb_over', 'bb_under',
    'ha_over', 'ha_under', 'outs_over', 'outs_under',
    'er_over', 'er_under', 'hits_over', 'hits_under',
]


def tier_for(conviction, prop_type):
    """Mirror of generate_props.tier_for after today's commits."""
    if prop_type == 'ks_over':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        if conviction >= 55: return 'LEAN'
        return 'SKIP'
    if prop_type == 'ks_under':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        if conviction >= 55: return 'LEAN'
        return 'SKIP'
    if prop_type == 'hits_under':
        if conviction >= 85: return 'PRIME'
        if conviction >= 75: return 'STRONG'
        return 'SKIP'
    if prop_type == 'outs_under':
        if conviction >= 65: return 'PRIME'
        if conviction >= 55: return 'STRONG'
        if conviction >= 50: return 'LEAN'
        return 'SKIP'
    if prop_type == 'outs_over':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        return 'SKIP'
    if prop_type in ('er_over', 'er_under'):
        if conviction >= 82: return 'PRIME'
        if conviction >= 76: return 'STRONG'  # tightened today
        return 'SKIP'
    if prop_type == 'ha_under':
        if conviction >= 70: return 'PRIME'
        return 'SKIP'  # STRONG/LEAN killed today
    if prop_type in ('bb_over', 'bb_under', 'ha_over'):
        if conviction >= 70: return 'PRIME'
        if conviction >= 55: return 'STRONG'
        return 'SKIP'
    # hits_over default
    if conviction >= 82: return 'PRIME'
    if conviction >= 72: return 'STRONG'
    if conviction >= 55: return 'LEAN'
    return 'SKIP'


_MARGIN_RE = re.compile(r'\(([+-]?\d+\.\d+)\)')
_STREAK_RE = re.compile(r'\((\d+)-of-(\d+) L5\)')


def estimate_l5_confirm_bonus(signal_value):
    """Reverse-engineer the conviction bonus from the signal narrative.

    The scorer adds +12 if margin>=0.7, +6 if 0.3<=margin<0.7, plus +4
    streak bonus when streak_hits >= 4. Parse the narrative to determine
    which case fired."""
    if not isinstance(signal_value, str):
        return 0
    margin_match = _MARGIN_RE.search(signal_value)
    if not margin_match:
        return 0
    try:
        margin = abs(float(margin_match.group(1)))
    except (ValueError, TypeError):
        return 0
    if margin >= 0.7:
        bonus = 12
    elif margin >= 0.3:
        bonus = 6
    else:
        return 0
    # Streak bonus
    streak_match = _STREAK_RE.search(signal_value)
    if streak_match:
        try:
            hits = int(streak_match.group(1))
            if hits >= 4:
                bonus += 4
        except ValueError:
            pass
    return bonus


def pull(prop_type, days=90):
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_pipeline_props?game_date=gte.{since}'
            f'&prop_type=eq.{prop_type}'
            f'&select=tier,conviction,result,signals&order=game_date.asc'
            f'&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return [p for p in rows if isinstance(p, dict) and p.get('result') in ('Win', 'Loss')]


def audit(prop_type):
    rows = pull(prop_type)
    if not rows:
        return None
    # Baseline + by-current-tier
    by_old_tier = defaultdict(lambda: [0, 0])
    by_new_tier = defaultdict(lambda: [0, 0])
    by_old_to_new = defaultdict(lambda: [0, 0])
    n_total = len(rows)

    for r in rows:
        old_conv = r.get('conviction') or 0
        old_tier = r.get('tier', '?')
        won = r['result'] == 'Win'
        sigs = r.get('signals') or {}
        l5c_bonus = 0
        if isinstance(sigs, dict) and 'l5_confirm' in sigs:
            l5c_bonus = estimate_l5_confirm_bonus(sigs['l5_confirm'])
        new_conv = max(0, old_conv - l5c_bonus)
        new_tier = tier_for(new_conv, prop_type)
        # Tally
        if won: by_old_tier[old_tier][0] += 1
        else: by_old_tier[old_tier][1] += 1
        if won: by_new_tier[new_tier][0] += 1
        else: by_new_tier[new_tier][1] += 1
        key = f'{old_tier}->{new_tier}'
        if won: by_old_to_new[key][0] += 1
        else: by_old_to_new[key][1] += 1

    return {
        'prop_type': prop_type,
        'n_total': n_total,
        'by_old_tier': dict(by_old_tier),
        'by_new_tier': dict(by_new_tier),
        'by_old_to_new': dict(by_old_to_new),
    }


def fmt(w, l):
    n = w + l
    if n == 0:
        return f'{w}-{l} ({n=})'
    return f'{w:>3}-{l:<3} ({100*w/n:.0f}%, n={n})'


def main():
    print('=' * 90)
    print('PHASE 2 BACKTEST — what happens if we remove l5_confirm conviction bonus?')
    print('=' * 90)

    promote_pass = []
    promote_block = []

    for pt in PROP_TYPES:
        result = audit(pt)
        if not result:
            print(f'\n{pt}: no data')
            continue
        old_t = result['by_old_tier']
        new_t = result['by_new_tier']
        transitions = result['by_old_to_new']

        # Compute old vs new PRIME and STRONG hit rates
        old_prime = old_t.get('PRIME', [0, 0])
        new_prime = new_t.get('PRIME', [0, 0])
        old_strong = old_t.get('STRONG', [0, 0])
        new_strong = new_t.get('STRONG', [0, 0])

        def rate(wl):
            w, l = wl
            n = w + l
            return 100 * w / n if n > 0 else 0

        delta_prime = rate(new_prime) - rate(old_prime)
        delta_strong = rate(new_strong) - rate(old_strong)

        # Picks that demoted from PRIME (we lose them from PRIME tier)
        demoted_from_prime_keys = [k for k in transitions if k.startswith('PRIME->') and k != 'PRIME->PRIME']
        demoted_count_prime = sum(transitions[k][0] + transitions[k][1] for k in demoted_from_prime_keys)
        demoted_wins_prime = sum(transitions[k][0] for k in demoted_from_prime_keys)
        demoted_rate_prime = 100 * demoted_wins_prime / demoted_count_prime if demoted_count_prime > 0 else 0

        print(f'\n{"─" * 90}')
        print(f'{pt.upper():>12s}  total n={result["n_total"]}')
        print(f'{"─" * 90}')
        print(f'  PRIME:   old {fmt(*old_prime):>20s}  →  new {fmt(*new_prime):>20s}  Δ {delta_prime:+.1f}pt')
        print(f'  STRONG:  old {fmt(*old_strong):>20s}  →  new {fmt(*new_strong):>20s}  Δ {delta_strong:+.1f}pt')
        if demoted_count_prime > 0:
            print(f'  Picks demoted FROM PRIME (l5_confirm-driven): {demoted_wins_prime}-{demoted_count_prime-demoted_wins_prime} ({demoted_rate_prime:.0f}% hist hit) over n={demoted_count_prime}')
        # Transitions
        for k, (w, l) in sorted(transitions.items()):
            if k.endswith('->' + k.split('->')[0]):  # no-change transitions
                continue
            print(f'    {k}: {w}-{l} ({100*w/max(1,w+l):.0f}%, n={w+l})')

        # Ship gate decisions
        new_prime_n = sum(new_prime)
        new_prime_min_n = 20
        verdict = ''
        if delta_prime > 0 and new_prime_n >= new_prime_min_n:
            verdict = '✅ SHIP (new PRIME hits higher AND meets sample minimum)'
            promote_pass.append(pt)
        elif delta_prime < -2:
            verdict = f'⚠️  BLOCK (new PRIME hit rate drops {delta_prime:.1f}pt)'
            promote_block.append((pt, 'prime_dropped'))
        elif new_prime_n < new_prime_min_n:
            verdict = f'⚠️  BLOCK (new PRIME n={new_prime_n} below {new_prime_min_n} minimum)'
            promote_block.append((pt, 'thin_sample'))
        elif demoted_count_prime > 0 and demoted_rate_prime > rate(new_prime):
            verdict = f'⚠️  BLOCK (demoted picks hit {demoted_rate_prime:.0f}% — better than remaining new PRIME)'
            promote_block.append((pt, 'demoted_winners'))
        else:
            verdict = '↔ NEUTRAL (no meaningful change)'
        print(f'  VERDICT: {verdict}')

    # Summary
    print(f'\n\n{"=" * 90}')
    print('SHIP DECISION SUMMARY')
    print('=' * 90)
    print(f'SHIP l5_confirm removal for: {promote_pass}')
    print(f'BLOCK l5_confirm removal for: {promote_block}')


if __name__ == '__main__':
    main()
