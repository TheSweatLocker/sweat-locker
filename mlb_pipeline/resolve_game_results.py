import requests
import os
from dotenv import load_dotenv
from datetime import datetime, date, timedelta

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
}

def run():
    print('Resolving game results...')
    # Get games missing scores from last 7 days
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_game_results?home_score=is.null&game_date=gte.{week_ago}&game_date=lte.{yesterday}&select=*',
        headers=HEADERS
    )
    games = r.json()
    print(f'Found {len(games)} games missing scores')

    resolved = 0
    for game in games:
        home_team = game.get('home_team')
        away_team = game.get('away_team')
        game_date = game.get('game_date')
        game_id = game.get('game_id')
        close_total = game.get('close_total')

        # Find MLB game PK
        try:
            r2 = requests.get(
                'https://statsapi.mlb.com/api/v1/schedule',
                params={'sportId': 1, 'date': game_date, 'hydrate': 'linescore,officials'},
                timeout=15
            )
            dates = r2.json().get('dates', [])
            for d in dates:
                for mlb_game in d.get('games', []):
                    mlb_home = mlb_game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                    mlb_away = mlb_game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                    home_match = home_team.lower() in mlb_home.lower() or mlb_home.lower() in home_team.lower()
                    away_match = away_team.lower() in mlb_away.lower() or mlb_away.lower() in away_team.lower()

                    if home_match and away_match and mlb_game.get('status', {}).get('abstractGameState') == 'Final':
                        linescore = mlb_game.get('linescore', {})
                        home_score = linescore.get('teams', {}).get('home', {}).get('runs')
                        away_score = linescore.get('teams', {}).get('away', {}).get('runs')

                        if home_score is not None and away_score is not None:
                            total_runs = home_score + away_score
                            home_win = home_score > away_score
                            margin = abs(home_score - away_score)
                            total_result = None
                            total_line = close_total or game.get('open_total')
                            if total_line:
                                total_result = 'Over' if total_runs > float(total_line) else 'Under' if total_runs < float(total_line) else 'Push'
                            # Run line result — home covers if they win by 2+
                            if (home_score - away_score) > 1.5:
                                run_line = 'home'
                            elif (away_score - home_score) > 1.5:
                                run_line = 'away'
                            else:
                                run_line = 'push'

                            # Backfill umpire if missing from original log
                            umpire = None
                            if not game.get('umpire'):
                                officials = mlb_game.get('officials', [])
                                hp_ump = next(
                                    (o.get('official', {}).get('fullName')
                                     for o in officials
                                     if o.get('officialType') == 'Home Plate'),
                                    None
                                )
                                if hp_ump:
                                    umpire = hp_ump

                            # Spread result — compare margin against posted spread
                            spread = game.get('close_spread') or game.get('open_spread')
                            spread_result = None
                            if spread is not None:
                                margin = home_score - away_score
                                spread_cover = margin + float(spread)
                                if spread_cover > 0:
                                    spread_result = 'home_covered'
                                elif spread_cover < 0:
                                    spread_result = 'away_covered'
                                else:
                                    spread_result = 'push'

                            # Update Supabase
                            print(f'  Attempting patch for game_id: {game_id}')
                            patch_resp = requests.patch(
                                f'{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{game_id}',
                                headers=HEADERS,
                                json={
                                    'home_score': home_score,
                                    'away_score': away_score,
                                    'home_win': home_win,
                                    'total_result': total_result,
                                    'run_line_result': run_line,
                                    'spread_result': spread_result,
                                    **(({'umpire': umpire} if umpire else {})),
                                    'result_logged_at': datetime.utcnow().isoformat()
                                }
                            )
                            print(f'  Patch status: {patch_resp.status_code}')
                            if patch_resp.status_code not in [200, 204]:
                                print(f'  Patch error: {patch_resp.text[:200]}')
                            print(f'  ✅ {away_team} {away_score} @ {home_team} {home_score} | Total {total_runs} → {total_result} | Spread → {spread_result or "no line"} | Ump: {umpire or "already logged"}')
                            resolved += 1
        except Exception as e:
            print(f'  Error: {e}')

    print(f'Done! {resolved} game results resolved')

if __name__ == '__main__':
    run()
