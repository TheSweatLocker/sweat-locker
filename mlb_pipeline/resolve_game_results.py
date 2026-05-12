import requests
import os
from dotenv import load_dotenv
from datetime import datetime, date, timedelta, timezone

load_dotenv()

def _et_today():
    """ET date — NOT UTC/local. Resolver's 'yesterday' must match pipeline's ET stamping."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
}

_NAME_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv'}


def _last_name(full_name):
    if not full_name:
        return ''
    parts = [p for p in full_name.strip().split() if p.lower().rstrip('.') not in _NAME_SUFFIXES]
    return parts[-1].lower() if parts else ''


def _matches_pitcher_hint(mlb_game, home_sp_name, away_sp_name):
    """DH disambiguation: True if MLB API game's probable pitcher last names
    match the row's stored sp_names. Returns True when no hint available so
    non-DH days behave unchanged."""
    if not home_sp_name and not away_sp_name:
        return True
    teams = mlb_game.get('teams', {})
    mlb_home_p = teams.get('home', {}).get('probablePitcher', {}).get('fullName', '')
    mlb_away_p = teams.get('away', {}).get('probablePitcher', {}).get('fullName', '')
    if not mlb_home_p and not mlb_away_p:
        return True
    home_ok = (not home_sp_name) or _last_name(home_sp_name) == _last_name(mlb_home_p)
    away_ok = (not away_sp_name) or _last_name(away_sp_name) == _last_name(mlb_away_p)
    return home_ok and away_ok


def run():
    print('Resolving game results...')
    # Get games missing scores from last 7 days
    et_today = _et_today()
    week_ago = (et_today - timedelta(days=7)).isoformat()
    yesterday = (et_today - timedelta(days=1)).isoformat()
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
        # DH FIX (2026-05-01): row's stored starters disambiguate which DH
        # gamePk this row corresponds to.
        row_home_sp = game.get('home_sp_name')
        row_away_sp = game.get('away_sp_name')

        # Find MLB game PK
        try:
            r2 = requests.get(
                'https://statsapi.mlb.com/api/v1/schedule',
                params={'sportId': 1, 'date': game_date, 'hydrate': 'linescore,officials,probablePitcher'},
                timeout=15
            )
            dates = r2.json().get('dates', [])
            done = False
            for d in dates:
                if done:
                    break
                for mlb_game in d.get('games', []):
                    if done:
                        break
                    mlb_home = mlb_game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                    mlb_away = mlb_game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                    home_match = home_team.lower() in mlb_home.lower() or mlb_home.lower() in home_team.lower()
                    away_match = away_team.lower() in mlb_away.lower() or mlb_away.lower() in away_team.lower()
                    if not (home_match and away_match):
                        continue
                    if mlb_game.get('status', {}).get('abstractGameState') != 'Final':
                        continue
                    if not _matches_pitcher_hint(mlb_game, row_home_sp, row_away_sp):
                        continue  # DH game w/ different starter
                    if True:
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

                            # F5 result — first 5 innings scoring from linescore
                            f5_result = None
                            f5_total_line = game.get('f5_total_line')
                            innings = linescore.get('innings', [])
                            if len(innings) >= 5 and f5_total_line:
                                f5_home = sum(inn.get('home', {}).get('runs', 0) or 0 for inn in innings[:5])
                                f5_away = sum(inn.get('away', {}).get('runs', 0) or 0 for inn in innings[:5])
                                f5_total = f5_home + f5_away
                                f5_result = 'Over' if f5_total > float(f5_total_line) else 'Under' if f5_total < float(f5_total_line) else 'Push'

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
                                    **(({'f5_total_result': f5_result} if f5_result else {})),
                                    **(({'umpire': umpire} if umpire else {})),
                                    'result_logged_at': datetime.utcnow().isoformat()
                                }
                            )
                            print(f'  Patch status: {patch_resp.status_code}')
                            if patch_resp.status_code not in [200, 204]:
                                print(f'  Patch error: {patch_resp.text[:200]}')
                            print(f'  ✅ {away_team} {away_score} @ {home_team} {home_score} | Total {total_runs} → {total_result} | Spread → {spread_result or "no line"} | Ump: {umpire or "already logged"}')
                            resolved += 1
                            done = True
        except Exception as e:
            print(f'  Error: {e}')

    print(f'Done! {resolved} game results resolved')

    # --- Resolve pipeline props (Hits O/U 0.5 + Ks O/U) ---
    print('\nResolving pipeline props...')
    pp = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_pipeline_props'
        f'?result=is.null&game_date=gte.{week_ago}&game_date=lte.{yesterday}'
        f'&select=id,game_id,game_date,player_name,prop_type,prop_line',
        headers=HEADERS
    )
    pending_props = pp.json() if pp.status_code == 200 else []
    print(f'Found {len(pending_props)} pending pipeline props')

    # Cache schedule lookups and box scores by game_date
    schedule_cache = {}
    boxscore_cache = {}

    def get_schedule(game_date):
        if game_date in schedule_cache:
            return schedule_cache[game_date]
        try:
            r = requests.get(
                'https://statsapi.mlb.com/api/v1/schedule',
                params={'sportId': 1, 'date': game_date, 'hydrate': 'probablePitcher'},
                timeout=15
            )
            schedule_cache[game_date] = r.json().get('dates', [])
        except Exception:
            schedule_cache[game_date] = []
        return schedule_cache[game_date]

    def find_game_pk(game_date, home_team, away_team, commence_time_hint=None,
                     home_sp_hint=None, away_sp_hint=None, player_hint=None):
        """Find MLB Stats API gamePk by team + date. Doubleheader-aware.

        DH disambiguation order (most reliable first):
          1. probable-pitcher last-name match against stored home_sp_hint/away_sp_hint
             (works for stale audits where mlb_game_context is wiped)
          2. boxscore contains player_hint (player_name from a prop row)
          3. closest-start-time vs commence_time_hint
          4. first match (with warning)
        """
        candidates = []
        for d in get_schedule(game_date):
            for g in d.get('games', []):
                mh = g.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                ma = g.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                if (home_team.lower() in mh.lower() or mh.lower() in home_team.lower()) \
                   and (away_team.lower() in ma.lower() or ma.lower() in away_team.lower()):
                    if g.get('status', {}).get('abstractGameState') == 'Final':
                        candidates.append(g)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].get('gamePk')

        # 1. Probable-pitcher last-name match (suffix-aware: McCullers Jr. → McCullers)
        if home_sp_hint or away_sp_hint:
            _ln = _last_name
            for g in candidates:
                teams = g.get('teams', {})
                mhp = (teams.get('home', {}).get('probablePitcher') or {}).get('fullName', '')
                map_ = (teams.get('away', {}).get('probablePitcher') or {}).get('fullName', '')
                if mhp or map_:
                    home_ok = (not home_sp_hint) or _ln(home_sp_hint) == _ln(mhp)
                    away_ok = (not away_sp_hint) or _ln(away_sp_hint) == _ln(map_)
                    if home_ok and away_ok:
                        return g.get('gamePk')

        # 2. Player-in-boxscore match (suffix-aware)
        if player_hint:
            last = _last_name(player_hint)
            for g in candidates:
                pk = g.get('gamePk')
                box = get_boxscore(pk)
                if not box:
                    continue
                for side in ('home', 'away'):
                    players = box.get('teams', {}).get(side, {}).get('players', {}) or {}
                    for _pid, p in players.items():
                        full = (p.get('person') or {}).get('fullName', '').lower()
                        if last in full:
                            return pk

        # 3. Closest-start-time fallback
        if commence_time_hint:
            try:
                from datetime import datetime
                hint_dt = datetime.fromisoformat(commence_time_hint.replace('Z', '+00:00'))
                best = min(
                    candidates,
                    key=lambda g: abs((datetime.fromisoformat(g.get('gameDate', '').replace('Z', '+00:00')) - hint_dt).total_seconds())
                )
                return best.get('gamePk')
            except Exception:
                pass
        print(f"  ⚠️ DH detected ({len(candidates)} games {away_team}@{home_team} {game_date}), no hint resolved — using first")
        return candidates[0].get('gamePk')

    def get_boxscore(game_pk):
        if game_pk in boxscore_cache:
            return boxscore_cache[game_pk]
        try:
            r = requests.get(
                f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
                timeout=15
            )
            boxscore_cache[game_pk] = r.json()
        except Exception:
            boxscore_cache[game_pk] = None
        return boxscore_cache[game_pk]

    def find_player_stat(boxscore, player_name, stat_group, stat_key):
        """Search both home and away players for the subject + return the stat value."""
        if not boxscore:
            return None
        player_name_lower = player_name.lower()
        for side in ('home', 'away'):
            players = boxscore.get('teams', {}).get(side, {}).get('players', {}) or {}
            for _pid, p in players.items():
                full = (p.get('person') or {}).get('fullName', '')
                if full.lower() == player_name_lower:
                    stats = (p.get('stats') or {}).get(stat_group) or {}
                    val = stats.get(stat_key)
                    try:
                        return int(val) if val is not None else 0
                    except (ValueError, TypeError):
                        return 0
            # Fallback — last-name match (suffix-aware: McCullers Jr. → McCullers)
            last = _last_name(player_name)
            for _pid, p in players.items():
                full = (p.get('person') or {}).get('fullName', '')
                if last in full.lower() and full.lower().endswith(last):
                    stats = (p.get('stats') or {}).get(stat_group) or {}
                    val = stats.get(stat_key)
                    try:
                        return int(val) if val is not None else 0
                    except (ValueError, TypeError):
                        return 0
        return None

    props_resolved = 0
    for prop in pending_props:
        try:
            # Look up matching game result row — pull starter names for DH disambig
            # (mlb_game_context is wiped for old dates, so commence_time hint is
            # unreliable; pitcher-name match works regardless).
            gr = requests.get(
                f'{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{prop["game_id"]}&select=home_team,away_team,home_score,home_sp_name,away_sp_name',
                headers=HEADERS
            )
            gr_data = gr.json()
            if not gr_data or gr_data[0].get('home_score') is None:
                continue  # game not finalized yet
            g = gr_data[0]

            # Pull commence_time from game_context for DH disambig (best-effort,
            # often None for stale games)
            ct_hint = None
            try:
                ctx_r = requests.get(
                    f'{SUPABASE_URL}/rest/v1/mlb_game_context?game_id=eq.{prop["game_id"]}&select=commence_time',
                    headers=HEADERS
                )
                ctx_data = ctx_r.json()
                if ctx_data:
                    ct_hint = ctx_data[0].get('commence_time')
            except Exception:
                pass

            game_pk = find_game_pk(
                prop['game_date'], g['home_team'], g['away_team'],
                commence_time_hint=ct_hint,
                home_sp_hint=g.get('home_sp_name'),
                away_sp_hint=g.get('away_sp_name'),
                player_hint=prop.get('player_name'),
            )
            if not game_pk:
                continue

            boxscore = get_boxscore(game_pk)
            if not boxscore:
                continue

            prop_type = prop['prop_type']
            player_name = prop['player_name']
            line = float(prop['prop_line'] or 0)

            if prop_type == 'hits_over':
                hits = find_player_stat(boxscore, player_name, 'batting', 'hits')
                if hits is None:
                    continue
                result = 'Win' if hits > line else 'Loss'
                final_val = hits
            elif prop_type == 'hits_under':
                hits = find_player_stat(boxscore, player_name, 'batting', 'hits')
                if hits is None:
                    continue
                # Push when the actual exactly equals the line (rare on 0.5 lines)
                if hits < line:
                    result = 'Win'
                elif hits == line:
                    result = 'Push'
                else:
                    result = 'Loss'
                final_val = hits
            elif prop_type == 'ks_over':
                ks = find_player_stat(boxscore, player_name, 'pitching', 'strikeOuts')
                if ks is None:
                    continue
                result = 'Win' if ks > line else 'Loss'
                final_val = ks
            elif prop_type == 'ks_under':
                ks = find_player_stat(boxscore, player_name, 'pitching', 'strikeOuts')
                if ks is None:
                    continue
                if ks < line:
                    result = 'Win'
                elif ks == line:
                    result = 'Push'
                else:
                    result = 'Loss'
                final_val = ks
            elif prop_type == 'bb_over':
                bb = find_player_stat(boxscore, player_name, 'pitching', 'baseOnBalls')
                if bb is None:
                    continue
                result = 'Win' if bb > line else ('Push' if bb == line else 'Loss')
                final_val = bb
            elif prop_type == 'bb_under':
                bb = find_player_stat(boxscore, player_name, 'pitching', 'baseOnBalls')
                if bb is None:
                    continue
                result = 'Win' if bb < line else ('Push' if bb == line else 'Loss')
                final_val = bb
            elif prop_type in ('outs_over', 'outs_under'):
                ip_str = find_player_stat(boxscore, player_name, 'pitching', 'inningsPitched')
                if ip_str is None:
                    continue
                # MLB encodes 6.1 IP as 6.1 (= 6⅓), 6.2 as 6.2 (= 6⅔). Convert to outs.
                try:
                    whole, _, frac = str(ip_str).partition('.')
                    outs = int(whole) * 3 + (int(frac) if frac else 0)
                except (ValueError, TypeError):
                    continue
                if prop_type == 'outs_over':
                    result = 'Win' if outs > line else ('Push' if outs == line else 'Loss')
                else:
                    result = 'Win' if outs < line else ('Push' if outs == line else 'Loss')
                final_val = outs
            elif prop_type in ('er_over', 'er_under'):
                er = find_player_stat(boxscore, player_name, 'pitching', 'earnedRuns')
                if er is None:
                    continue
                if prop_type == 'er_over':
                    result = 'Win' if er > line else ('Push' if er == line else 'Loss')
                else:
                    result = 'Win' if er < line else ('Push' if er == line else 'Loss')
                final_val = er
            elif prop_type in ('ha_over', 'ha_under'):
                ha = find_player_stat(boxscore, player_name, 'pitching', 'hits')
                if ha is None:
                    continue
                if prop_type == 'ha_over':
                    result = 'Win' if ha > line else ('Push' if ha == line else 'Loss')
                else:
                    result = 'Win' if ha < line else ('Push' if ha == line else 'Loss')
                final_val = ha
            else:
                continue

            requests.patch(
                f'{SUPABASE_URL}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                headers=HEADERS,
                json={
                    'result': result,
                    'final_value': final_val,
                    'resolved_at': datetime.utcnow().isoformat(),
                }
            )
            props_resolved += 1
            print(f'  🎯 {prop["game_date"]} {player_name} {prop_type} {line} → {final_val} → {result}')
        except Exception as e:
            print(f'  Prop error: {e}')

    print(f'Done! {props_resolved} pipeline props resolved')

    # --- Resolve Dawg of the Day results ---
    print('\nResolving Dawg of the Day picks...')
    r_dawg = requests.get(
        f'{SUPABASE_URL}/rest/v1/daily_dawg?result=is.null&game_date=gte.{week_ago}&game_date=lte.{yesterday}&select=*',
        headers=HEADERS
    )
    pending_dawgs = r_dawg.json() if r_dawg.status_code == 200 else []
    print(f'Found {len(pending_dawgs)} pending Dawg picks')

    dawg_resolved = 0
    for dawg in pending_dawgs:
        try:
            # Pull the finalized game row — should have scores by now
            gr = requests.get(
                f'{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{dawg["game_id"]}&select=home_team,away_team,home_score,away_score,home_win',
                headers=HEADERS
            )
            gr_data = gr.json()
            if not gr_data or gr_data[0].get('home_score') is None:
                continue  # game not finalized yet

            g = gr_data[0]
            home_win = g.get('home_win')
            # Dawg picked their team's ML. Did that team win?
            dawg_won = (dawg['team'] == g['home_team'] and home_win) or \
                       (dawg['team'] == g['away_team'] and home_win is False)
            result = 'Win' if dawg_won else 'Loss'

            requests.patch(
                f'{SUPABASE_URL}/rest/v1/daily_dawg?game_date=eq.{dawg["game_date"]}',
                headers=HEADERS,
                json={
                    'result': result,
                    'final_score': f"{g['away_team']} {g['away_score']} @ {g['home_team']} {g['home_score']}",
                }
            )
            dawg_resolved += 1
            print(f'  🐕 {dawg["game_date"]} {dawg["team"]} ML → {result}')
        except Exception as e:
            print(f'  Dawg error: {e}')

    print(f'Done! {dawg_resolved} Dawg picks resolved')

if __name__ == '__main__':
    run()
