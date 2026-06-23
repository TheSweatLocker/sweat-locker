"""Re-grade the 114 outs_over/under props that got stuck at final_value=0.

These were graded prematurely (before game finalized) so the boxscore showed
inningsPitched='0.0' and the function returned 0 outs. Then result was locked
and never re-graded.

Now that resolve_props.py has the is_mlb_game_final() check + IP-0 guard,
re-grade these manually to set correct final_value + result.
"""
import os
import sys
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, '.')
from resolve_props import get_pitcher_outs, get_mlb_game_result

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}
W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def main():
    r = requests.get(
        f'{SU}/rest/v1/mlb_pipeline_props?prop_type=in.(outs_over,outs_under)'
        f'&result=in.(Win,Loss)&final_value=eq.0'
        f'&select=id,game_date,player_name,prop_type,prop_line,direction,matchup,result'
        f'&order=game_date.desc&limit=500',
        headers=H, timeout=30,
    )
    buggy = r.json()
    print(f'Buggy outs props to re-grade: {len(buggy)}')
    print()

    fixed = 0
    confirmed = 0
    no_change = 0
    failed = 0

    for p in buggy:
        gd = p['game_date']
        player = p['player_name']
        prop_type = p['prop_type']
        line = float(p['prop_line'])
        direction = p['direction']
        matchup = p['matchup']
        try:
            away, home = matchup.split(' @ ')
        except Exception:
            failed += 1
            continue

        mlb_game = get_mlb_game_result(home.strip(), away.strip(), gd)
        if not mlb_game:
            print(f'  [SKIP] {gd} {player} — no MLB game record')
            failed += 1
            continue

        actual = get_pitcher_outs(mlb_game, player)
        if actual is None:
            print(f'  [SKIP] {gd} {player} — pitcher not found / game not final')
            no_change += 1
            continue

        # Determine new result
        if direction == 'over':
            new_result = 'Win' if actual > line else ('Loss' if actual < line else 'Push')
        else:
            new_result = 'Win' if actual < line else ('Loss' if actual > line else 'Push')

        old_result = p['result']
        result_changed = new_result != old_result

        # Update DB
        payload = {'result': new_result, 'final_value': actual,
                   'resolved_at': datetime.now().isoformat()}
        rr = requests.patch(
            f'{SU}/rest/v1/mlb_pipeline_props?id=eq.{p["id"]}',
            json=payload, headers=W, timeout=15,
        )
        if rr.status_code in (200, 201, 204):
            fixed += 1
            tag = 'FLIPPED' if result_changed else 'confirmed'
            if result_changed:
                print(f'  [{tag}] {gd} {player:>22s} {prop_type:>11s} line={line}  was {old_result} -> now {new_result} (actual {actual})')
            if not result_changed:
                confirmed += 1
        else:
            print(f'  [FAIL] {gd} {player} update failed: {rr.status_code} {rr.text[:100]}')
            failed += 1
        time.sleep(0.2)

    print()
    print(f'Done: {fixed} updated ({fixed - confirmed} flipped + {confirmed} confirmed)')
    print(f'      {no_change} skipped (game not final / no pitcher data)')
    print(f'      {failed} failed')


if __name__ == '__main__':
    main()
