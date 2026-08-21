"""Coverage audit — flags any prop/game where <N factors were evaluated OR
single class dominates score. Enforces SIGNAL_FRAMEWORK.md standard.

CLI:
    python coverage_audit.py                     # today
    python coverage_audit.py --date 2026-08-21
    python coverage_audit.py --min-factors 12    # threshold
"""
import argparse, json, os, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Framework: expected factor slots per prop type
FACTOR_SLOTS = {
    'pitcher_prop': [
        'form_recent', 'form_season', 'home_road_split', 'first_inning', 'rest',
        'vs_team_career_baa', 'vs_team_career_era', 'vs_team_career_k9',
        'vs_team_career_outs', 'vs_team_recent',
        'opp_lineup_form', 'opp_lineup_heat', 'opp_k_rate',
        'opp_babip', 'opp_barrel', 'opp_ats',
        'own_bullpen', 'park', 'weather', 'ump', 'platoon',
        'sharp_split', 'projection_sanity', 'legacy_signals', 'refit',
    ],
    'batter_prop': [
        'batter_form_l7', 'batter_form_l14', 'batter_vs_pitcher_career',
        'batter_platoon', 'batter_home_road', 'lineup_spot',
        'team_offense', 'opp_starter', 'opp_bullpen', 'park', 'weather',
        'ump', 'rest', 'legacy_signals',
    ],
}

# Rough mapping from signal_key patterns to factor slot
def factor_of(key: str) -> str:
    k = key.lower()
    if 'projection_contradicts' in k: return 'projection_sanity'
    if 'recent_hot' in k or 'recent_cold' in k or 'l5_confirm' in k or 'l7_avg' in k: return 'form_recent'
    if 'xera' in k or 'siera' in k: return 'form_season'
    if 'home_road_split' in k or 'home_road' in k: return 'home_road_split'
    if 'first_inning' in k or '1st_inn' in k or 'slow_start' in k: return 'first_inning'
    if 'rest' in k or 'short_last' in k: return 'rest'
    if 'vs_team_career_baa' in k: return 'vs_team_career_baa'
    if 'vs_team_career_er' in k: return 'vs_team_career_era'
    if 'vs_team_career_k9' in k: return 'vs_team_career_k9'
    if 'vs_team_career_outs' in k or 'vs_team_ip_below' in k: return 'vs_team_career_outs'
    if 'vs_team_recent' in k or 'vs_team_dominant' in k or 'vs_team_hit_hard' in k: return 'vs_team_recent'
    if 'opp_lineup_hot' in k or 'opp_lineup_cold' in k: return 'opp_lineup_form'
    if 'opp_lineup_on_heater' in k or 'opp_lineup_frozen' in k: return 'opp_lineup_heat'
    if 'opp_lineup_k_heavy' in k or 'opp_k' in k: return 'opp_k_rate'
    if 'babip' in k: return 'opp_babip'
    if 'barrel' in k: return 'opp_barrel'
    if 'opp_ats' in k or 'ats_hot_on_road' in k or 'ats_cold_at_home' in k: return 'opp_ats'
    if 'bullpen' in k or 'bp_' in k: return 'own_bullpen'
    if 'park' in k: return 'park'
    if 'wind' in k or 'weather' in k or 'temp' in k: return 'weather'
    if 'ump' in k: return 'ump'
    if 'platoon' in k or 'vs_opp_hand' in k: return 'platoon'
    if 'sharp_split' in k or 'oddscrowd' in k or 'sharp_scenario' in k: return 'sharp_split'
    if 'refit' in k: return 'refit'
    if 'l7_hot' in k or 'l7_cold' in k or 'l14_heat' in k or 'l14_cold' in k or 'batter' in k: return 'legacy_signals'
    if 'lineup_spot' in k: return 'lineup_spot'
    if 'bvp_' in k: return 'batter_vs_pitcher_career'
    return 'legacy_signals'  # catchall


def audit_prop(row: dict, min_factors: int = 12) -> dict:
    sources = row.get('playbook_sources') or []
    if isinstance(sources, str):
        try: sources = json.loads(sources)
        except: sources = []
    if not isinstance(sources, list): sources = []

    # Factors touched (any contribution, even 0)
    factors_touched = set()
    class_share = defaultdict(float)
    total = 0.0
    top_chip = None; top_chip_share = 0.0
    for c in sources:
        if not isinstance(c, dict): continue
        contrib = c.get('contribution', 0) or 0
        total += contrib
        factors_touched.add(factor_of(c.get('signal_key', '')))
        class_share[c.get('class', '?')] += contrib
        if contrib > top_chip_share:
            top_chip = c.get('signal_key'); top_chip_share = contrib

    # Determine prop family
    prop_type = row.get('prop_type', '')
    is_pitcher_prop = any(prop_type.startswith(x) for x in ('ks_', 'ha_', 'bb_', 'er_', 'outs_'))
    family = 'pitcher_prop' if is_pitcher_prop else 'batter_prop'
    expected_slots = FACTOR_SLOTS[family]
    coverage = len(factors_touched & set(expected_slots))
    missing = set(expected_slots) - factors_touched

    # Dominance check
    dom_class = None; dom_share = 0
    if total > 0:
        for cls, contrib in class_share.items():
            share = contrib / total
            if share > dom_share: dom_class = cls; dom_share = share

    flags = []
    if coverage < min_factors:
        flags.append(f'LOW_COVERAGE: {coverage}/{len(expected_slots)} factors — missing {sorted(missing)[:5]}')
    if dom_share > 0.45:
        flags.append(f'CLASS_DOMINANCE: {dom_class} = {100*dom_share:.0f}% of score')
    top_chip_pct = 100 * top_chip_share / max(total, 0.01)
    if top_chip_pct > 30:
        flags.append(f'CHIP_DOMINANCE: {top_chip} = {top_chip_pct:.0f}% of score')

    return {
        'player_name': row.get('player_name'),
        'prop_type': prop_type,
        'tier': row.get('playbook_tier'),
        'conviction': row.get('playbook_conviction'),
        'coverage': coverage,
        'expected': len(expected_slots),
        'flags': flags,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', default=None)
    p.add_argument('--min-factors', type=int, default=12)
    p.add_argument('--tier', default='in.(PRIME,STRONG,LEAN)')
    args = p.parse_args()
    date = args.date or datetime.now(timezone.utc).date().isoformat()

    r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions', headers=H,
                     params={'game_date': f'eq.{date}',
                             'playbook_tier': args.tier,
                             'select': 'player_name,prop_type,playbook_tier,playbook_conviction,playbook_sources'},
                     timeout=30)
    rows = r.json() if isinstance(r.json(), list) else []

    print(f'Coverage audit — {date} — {len(rows)} rows evaluated')
    print(f'Min factors threshold: {args.min_factors}\n')

    flagged = 0
    coverage_dist = Counter()
    for row in rows:
        result = audit_prop(row, args.min_factors)
        coverage_dist[result['coverage']] += 1
        if result['flags']:
            flagged += 1
            print(f'🚨 {result["player_name"][:20]:20s} {result["prop_type"]:12s} {result["tier"]:6s} conv={result["conviction"]:>3}  '
                  f'coverage={result["coverage"]}/{result["expected"]}')
            for f in result['flags']:
                print(f'     {f}')

    print(f'\n=== SUMMARY ===')
    print(f'  Flagged: {flagged}/{len(rows)} ({100*flagged/max(1,len(rows)):.0f}%)')
    print(f'  Coverage distribution:')
    for cov, n in sorted(coverage_dist.items()):
        print(f'    {cov} factors: {n} props')


if __name__ == '__main__':
    main()
