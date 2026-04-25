"""
Audit line movement vs game outcomes.

Pulls resolved games where we have BOTH open and close ML odds for both teams,
computes line_movement = close_ml - open_ml (negative = team got more juiced),
buckets by movement magnitude/direction, reports hit rate.

Reverse line movement (line moves toward UNDERDOG against public money) is one
of the few +EV signals retail bettors can capture. We want to know if our
captured movement correlates with winners.

Usage:
  python mlb_pipeline/audit_line_movement.py
  python mlb_pipeline/audit_line_movement.py --since 2026-04-01
"""
import os
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def fetch(since=None):
    params = {
        'home_score': 'not.is.null',
        'home_ml_open': 'not.is.null',
        'home_ml_close': 'not.is.null',
        'away_ml_open': 'not.is.null',
        'away_ml_close': 'not.is.null',
        'select': '*',
        'limit': '2000',
    }
    if since:
        params['game_date'] = f'gte.{since}'
    r = requests.get(f'{SUPABASE_URL}/rest/v1/mlb_game_results', params=params, headers=HEADERS, timeout=30)
    return r.json()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--since', default='2026-04-25', help='earliest game_date to include')
    args = p.parse_args()

    games = fetch(args.since)
    print(f'Pulled {len(games)} resolved games with full open/close ML coverage (since {args.since})')

    if len(games) < 30:
        print(f'\nInsufficient sample for meaningful audit (need 30+). Re-run after more games resolve.')
        return

    records = []
    for g in games:
        ho, hc = int(g['home_ml_open']), int(g['home_ml_close'])
        ao, ac = int(g['away_ml_open']), int(g['away_ml_close'])
        # Negative ML = favorite. Movement TOWARD a side = its ML gets more negative.
        home_move = hc - ho
        away_move = ac - ao
        winner = 'home' if g['home_score'] > g['away_score'] else 'away'
        records.append({
            'date': g.get('game_date'),
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'home_o': ho, 'home_c': hc, 'home_move': home_move,
            'away_o': ao, 'away_c': ac, 'away_move': away_move,
            'winner': winner,
        })

    print(f'\n=== LINE MOVEMENT AUDIT ===')
    print(f'\n--- Home team line movement ---')
    for thresh, label in [(-30, '30+ cents toward home (heavy steam)'),
                           (-15, '15+ cents toward home (steam)'),
                           (-5, '5+ cents toward home (light)'),
                           (5, '5+ cents away from home'),
                           (15, '15+ cents away from home (rev)')]:
        if thresh < 0:
            bucket = [r for r in records if r['home_move'] <= thresh]
        else:
            bucket = [r for r in records if r['home_move'] >= thresh]
        if not bucket:
            continue
        wins = sum(1 for r in bucket if r['winner'] == 'home')
        pct = wins / len(bucket) * 100
        print(f'  {label:<45} n={len(bucket):3d}  home wins {wins}/{len(bucket)} = {pct:5.1f}%')

    print(f'\n--- Away team line movement ---')
    for thresh, label in [(-30, '30+ cents toward away (heavy steam)'),
                           (-15, '15+ cents toward away (steam)'),
                           (-5, '5+ cents toward away (light)'),
                           (5, '5+ cents away from away'),
                           (15, '15+ cents away from away (rev)')]:
        if thresh < 0:
            bucket = [r for r in records if r['away_move'] <= thresh]
        else:
            bucket = [r for r in records if r['away_move'] >= thresh]
        if not bucket:
            continue
        wins = sum(1 for r in bucket if r['winner'] == 'away')
        pct = wins / len(bucket) * 100
        print(f'  {label:<45} n={len(bucket):3d}  away wins {wins}/{len(bucket)} = {pct:5.1f}%')

    # Reverse line movement check — line moves toward UNDERDOG (favorite gets less juiced)
    rlm = []
    for r in records:
        # Underdog at open = team with positive ML (or less negative)
        home_was_dog = r['home_o'] > r['away_o']
        if home_was_dog and r['home_move'] < -10:
            # Home was dog at open, line moved toward home (got juicier) — reverse line move
            rlm.append({**r, 'rlm_side': 'home', 'magnitude': abs(r['home_move'])})
        elif not home_was_dog and r['away_move'] < -10:
            rlm.append({**r, 'rlm_side': 'away', 'magnitude': abs(r['away_move'])})
    if rlm:
        wins = sum(1 for r in rlm if r['winner'] == r['rlm_side'])
        print(f'\n--- REVERSE LINE MOVEMENT (line moves toward open underdog by 10+ cents) ---')
        print(f'  n={len(rlm)}  underdog (RLM side) wins {wins}/{len(rlm)} = {wins/len(rlm)*100:.1f}%')
    else:
        print(f'\nNo reverse line movement examples in this sample yet.')


if __name__ == '__main__':
    main()
