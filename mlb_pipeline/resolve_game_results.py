import requests
import os
import sys
import json
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

# Postponement detection: how stale a missing-score row can sit before
# we treat the absence as a real postponement. Raised to 3 days as a
# fallback safety net only — the canonical detection is now an MLB API
# round-trip in _is_postponed() (see below). Keep at >=2 so the resolver
# never auto-Pushes a row whose scores are merely lagging by a few hours.
_POSTPONEMENT_DAYS_OLD = 3


_MLB_STATE_CACHE = {}  # {(home_team, away_team, game_date): ('postponed'|'final'|'pending', score_dict_or_None)}


def _fetch_mlb_game_state(home_team, away_team, game_date_str):
    """Hit the MLB schedule API for ground-truth game status + final scores.

    Returns one of:
      ('postponed', None)              — MLB API says game was postponed
      ('final', {home_score, away_score, home_win})  — game completed, scores fetched
      ('pending', None)                — scheduled but not final / not postponed / unknown

    Cached per (teams, date) tuple within a single resolver run so we don't
    re-hit the API for every pick on the same game. On any network/JSON
    failure returns 'pending' (conservative — never mark as Push when we
    can't verify)."""
    if not (home_team and away_team and game_date_str):
        return ('pending', None)
    key = (home_team, away_team, game_date_str)
    if key in _MLB_STATE_CACHE:
        return _MLB_STATE_CACHE[key]
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={game_date_str}',
            timeout=10,
        )
        if r.status_code != 200:
            _MLB_STATE_CACHE[key] = ('pending', None)
            return _MLB_STATE_CACHE[key]
        data = r.json()
        for date_block in (data.get('dates') or []):
            for game in (date_block.get('games') or []):
                teams = game.get('teams') or {}
                api_home = (teams.get('home') or {}).get('team', {}).get('name', '') or ''
                api_away = (teams.get('away') or {}).get('team', {}).get('name', '') or ''
                if api_home != home_team or api_away != away_team:
                    continue
                status = (game.get('status') or {}).get('detailedState', '')
                status_code = (game.get('status') or {}).get('statusCode', '')
                if status == 'Postponed' or status_code == 'DR':
                    _MLB_STATE_CACHE[key] = ('postponed', None)
                    return _MLB_STATE_CACHE[key]
                if status in ('Final', 'Game Over', 'Completed Early'):
                    home_sc = (teams.get('home') or {}).get('score')
                    away_sc = (teams.get('away') or {}).get('score')
                    if home_sc is not None and away_sc is not None:
                        _MLB_STATE_CACHE[key] = ('final', {
                            'home_score': home_sc,
                            'away_score': away_sc,
                            'home_win': home_sc > away_sc,
                            'total_runs': home_sc + away_sc,
                        })
                        return _MLB_STATE_CACHE[key]
                # Anything else (Scheduled, In Progress, Delayed): pending
                _MLB_STATE_CACHE[key] = ('pending', None)
                return _MLB_STATE_CACHE[key]
        # Match not found on the slate
        _MLB_STATE_CACHE[key] = ('pending', None)
        return _MLB_STATE_CACHE[key]
    except Exception:
        _MLB_STATE_CACHE[key] = ('pending', None)
        return _MLB_STATE_CACHE[key]


def _backfill_score_to_results(game_id, home_team, away_team, game_date_str, score_dict):
    """When MLB API confirms a final score but mlb_game_results has no row
    (or no score), upsert a minimal scored row so all later picks referencing
    the same game grade off the actual outcome instead of falling back to
    'Pending' or worse, the postponement Push.

    This is the inline self-heal for the silent-fail class of bug that
    caused the 6/6 recap blackout. Best-effort — failures are non-fatal."""
    if not (game_id and home_team and away_team and score_dict):
        return False
    try:
        hs = score_dict.get('home_score')
        as_ = score_dict.get('away_score')
        # 2026-08-16 morning-audit fix: prior payload omitted total_runs and
        # margin, leaving them NULL on every self-heal row. Downstream
        # audits that group by total (e.g. project_totals_cohort_framework)
        # dropped those games from every histogram. Compute + write here
        # whenever both scores are present.
        total = hs + as_ if (hs is not None and as_ is not None) else None
        mgn = abs(hs - as_) if (hs is not None and as_ is not None) else None
        payload = {
            'game_id': game_id,
            'game_date': game_date_str,
            'season': 2026,
            'home_team': home_team,
            'away_team': away_team,
            'home_score': hs,
            'away_score': as_,
            'home_win': score_dict.get('home_win'),
            'total_runs': total,
            'margin': mgn,
        }
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results?on_conflict=game_id',
            headers={**HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json=payload, timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _is_postponed(game_date_str, has_scores, home_team=None, away_team=None):
    """Authoritative postponement check.

    PRIMARY path (when home_team + away_team are supplied): asks the MLB
    schedule API for the actual game status. This is the canonical fix for
    the 6/6 recap silent blackout: when our internal `mlb_game_results`
    table was empty due to an upstream pipeline bug, the old heuristic
    "missing scores + day-old game = Postponed" incorrectly graded every
    Dawg/POTD as Push. The API knows whether the game actually played.

    FALLBACK path (legacy callers without team names): retains the old
    date-based heuristic but with the threshold raised to 3 days so a
    one-day data lag never auto-pushes. Real rain-outs surface in the API
    within hours, so the fallback should rarely fire.

    Returns True only when we can affirmatively confirm postponement.
    Unknown / unreachable / lagging → False so the row sits as Pending
    rather than being misgraded."""
    if has_scores:
        return False
    if home_team and away_team:
        state, _ = _fetch_mlb_game_state(home_team, away_team, game_date_str)
        return state == 'postponed'
    try:
        gd = datetime.strptime(game_date_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return False
    return (_et_today() - gd).days >= _POSTPONEMENT_DAYS_OLD


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
    # Get games missing scores from last 7 days OR stuck at 0-0 (which is
    # essentially impossible in modern MLB regular season — last 0-0 final
    # was 2018, and even those settle in 9 innings with at least 1 run).
    # 6/12 PHI@MIL graded 6-0 final per MLB API but our row had home_score=0
    # AND away_score=0 (from a partial earlier write that filled defaults
    # instead of nulls). The is.null gate skipped it silently and a
    # 5-pick public card lost grading on two legs (MIL RL + Painter ER
    # Over) until the morning audit caught it manually.
    et_today = _et_today()
    week_ago = (et_today - timedelta(days=7)).isoformat()
    yesterday = (et_today - timedelta(days=1)).isoformat()
    # Pull null-score rows
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_game_results?home_score=is.null&game_date=gte.{week_ago}&game_date=lte.{yesterday}&select=*',
        headers=HEADERS
    )
    games_null = r.json()
    # Pull stale 0-0 rows separately (or.is.null doesn't compose cleanly
    # across two columns in PostgREST — do as a second query and dedupe)
    r2 = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_game_results?home_score=eq.0&away_score=eq.0&game_date=gte.{week_ago}&game_date=lte.{yesterday}&select=*',
        headers=HEADERS
    )
    games_zero = r2.json() if r2.status_code == 200 else []
    seen = {g.get('game_id') for g in games_null}
    games = games_null + [g for g in games_zero if g.get('game_id') not in seen]
    if games_zero:
        print(f'  ⚠ {len(games_zero)} stale 0-0 row(s) re-checked (defensive: covers partial-write defaults)')
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
                                    # 2026-08-29: previously only game_context.py::log_game_result
                                    # wrote home_spread_covered, but that path doesn't
                                    # run in production — 1815 graded rows had
                                    # spread_result populated but home_spread_covered
                                    # NULL. Downstream cohort/audit code reads the
                                    # boolean form. Derive it from spread_result:
                                    #   'home_covered' → True
                                    #   'away_covered' → False
                                    #   'push' or None → leave None (unset)
                                    **(({'home_spread_covered': spread_result == 'home_covered'}
                                        if spread_result in ('home_covered', 'away_covered') else {})),
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
            r.raise_for_status()
            dates = r.json().get('dates', [])
            if not dates:
                # MLB API returned 200 but empty dates array — possible API
                # quirk; log so future blackouts are visible.
                print(f"  ⚠️  MLB schedule for {game_date} returned empty dates array — no games to resolve for this date")
            schedule_cache[game_date] = dates
        except Exception as e:
            # 2026-06-09: was a SILENT [] fallback — same missing-data
            # anti-pattern as the 6/6 log_game_result blackout. Now logs
            # the actual exception so a cron run that can't reach the
            # MLB API doesn't silently produce zero resolutions.
            print(f"  🚨 MLB schedule fetch FAILED for {game_date}: {type(e).__name__}: {e}")
            print(f"     Any game on {game_date} will be UNRESOLVABLE this pass — re-run after the MLB API recovers")
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

    # --- Void sweep: anything still unresolved >2 days post-game gets
    # marked Void. These are props where the player never appeared in the
    # boxscore (scratched, unused bench, postponed, or alias-match failure).
    # Previously these sat as NULL forever, polluting the record display
    # and the audit cohorts. (Added 2026-05-17.)
    void_cutoff = (et_today - timedelta(days=2)).strftime('%Y-%m-%d')
    vp = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_pipeline_props'
        f'?result=is.null&game_date=lt.{void_cutoff}'
        f'&select=id,game_date,player_name,prop_type',
        headers=HEADERS
    )
    void_targets = vp.json() if vp.status_code == 200 else []
    voided = 0
    for vt in void_targets:
        try:
            r = requests.patch(
                f'{SUPABASE_URL}/rest/v1/mlb_pipeline_props?id=eq.{vt["id"]}',
                headers=HEADERS,
                json={'result': 'Void', 'resolved_at': datetime.utcnow().isoformat()}
            )
            if r.status_code in (200, 204):
                voided += 1
        except Exception as e:
            print(f'  Void error on prop {vt.get("id")}: {e}')
    if voided:
        print(f'Voided {voided} stale unresolved props (player did not appear in boxscore, >2d old)')
    elif void_targets:
        print(f'Found {len(void_targets)} stale unresolved but all PATCH calls failed')

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
            has_scores = bool(gr_data) and gr_data[0].get('home_score') is not None
            # Derive teams for API verification — try existing row, then matchup parse.
            d_home = d_away = None
            if gr_data:
                d_home = gr_data[0].get('home_team')
                d_away = gr_data[0].get('away_team')
            if not (d_home and d_away):
                parts = (dawg.get('matchup') or '').split(' @ ')
                if len(parts) == 2:
                    d_away, d_home = parts[0].strip(), parts[1].strip()
            # Postponement check is now API-authoritative — see _is_postponed.
            if not has_scores:
                if _is_postponed(dawg['game_date'], False, home_team=d_home, away_team=d_away):
                    requests.patch(
                        f'{SUPABASE_URL}/rest/v1/daily_dawg?game_date=eq.{dawg["game_date"]}',
                        headers=HEADERS,
                        json={'result': 'Push', 'final_score': 'Postponed'},
                    )
                    dawg_resolved += 1
                    print(f'  🐕 {dawg["game_date"]} {dawg["team"]} ML → Push (postponed, API-confirmed)')
                    continue
                # Self-heal: if MLB API has a final score but our row doesn't,
                # backfill mlb_game_results inline and grade off the live data.
                state, score = _fetch_mlb_game_state(d_home, d_away, dawg['game_date'])
                if state == 'final' and score:
                    _backfill_score_to_results(dawg['game_id'], d_home, d_away, dawg['game_date'], score)
                    gr_data = [{'home_team': d_home, 'away_team': d_away,
                                'home_score': score['home_score'],
                                'away_score': score['away_score'],
                                'home_win': score['home_win']}]
                    has_scores = True
                    print(f'  🩹 {dawg["game_date"]} {dawg["team"]}: backfilled score from MLB API ({score["away_score"]}-{score["home_score"]})')
                else:
                    continue  # still pending — leave the row as-is

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

    # ─── Resolve Sweat Card top_8 curated picks ──────────────────────────
    # Walks the curated 8-pick set on each pending sweat_card_YYYY-MM-DD
    # jerry_cache entry, looks up each pick's result from its source table,
    # and writes back to the JSON. This makes the 8-pick set a single
    # auditable unit — the "Sweat Card: 7-1" number is now computable
    # directly from these cached rows.
    print('\nResolving Sweat Card top_8 picks...')
    sc_resolved = _resolve_sweat_card_top8(week_ago, yesterday)
    print(f'Done! {sc_resolved} sweat card sets walked')

    # ─── Live tier × category track record (added 2026-06-14) ────────────
    # After everything has been graded, walk the resolver tier × category
    # cross-cut and update jerry_cache.live_tier_records so pick
    # recommendations can weight by what's actually working forward —
    # the retroactive audit is the prior, this is the observed posterior.
    # Fails open so a tracker bug never blocks the main grader.
    try:
        print('\nUpdating live tier × category track record...')
        import track_live_tier_record
        track_live_tier_record.run(dryrun=False)
    except Exception as e:
        print(f'  ⚠ live tier tracker failed: {type(e).__name__}: {e}')

    # ─── Archive panel-implied snapshot (added 2026-06-27) ────────────────
    # The tier-discipline gate (commits d5e5552/6ae1426/122ae70) treats the
    # per-pitcher Numbers Panel as a 4th vote. Snapshot panel_implied_total
    # + margin per game so future backtests can validate the gate forward
    # against real outcomes. Without this hook the values would only exist
    # at runtime in tier_discipline_gate and be unreachable post-grade.
    # Fails open — never blocks resolver if backfill module/columns missing.
    try:
        print('\nArchiving panel-implied to mlb_game_results...')
        import backfill_panel_implied
        # Only the day we just graded — keeps the call light (<1s)
        backfill_panel_implied.run(date_filter=f'eq.{yesterday}',
                                   only_null=True, verbose=False)
    except Exception as e:
        print(f'  ⚠ panel-implied archive failed: {type(e).__name__}: {e}')

    # ─── End-of-run blackout sanity check (added 2026-06-09) ────────────
    # If every resolution counter is 0 AND we just attempted multiple
    # categories, that's a strong signal something went wrong upstream
    # (MLB API down, network blip, postponement detection failed). The
    # 6/6 chain reaction started with this kind of silent zero-state.
    # Print a loud banner so the cron log surfaces the blackout.
    if props_resolved == 0 and dawg_resolved == 0 and sc_resolved == 0:
        print('')
        print('🚨🚨🚨 RESOLVER ZERO-STATE WARNING 🚨🚨🚨')
        print('   props_resolved=0, dawg_resolved=0, sc_resolved=0')
        print('   Either yesterday had no graded picks (unusual) OR an upstream')
        print('   data source (MLB schedule API, mlb_game_results table) is')
        print('   silently empty. Check schedule fetch logs above — same class')
        print('   as the 6/6 silent-blackout chain. Investigate before next cron.')
        print('🚨🚨🚨')


def _resolve_sweat_card_top8(start_date, end_date):
    """Walk daily sweat_card cache entries with pending top_8 picks and
    resolve each pick from its source table."""
    # Pull pending sweat card entries — sorted by cache_key desc so we get
    # the MOST RECENT 14 cards, not an arbitrary 14. Bug fix 2026-05-24:
    # without order, Supabase returned 5/3-5/22 and missed yesterday's
    # card entirely (resolver did nothing for the day that mattered).
    rows = requests.get(
        f'{SUPABASE_URL}/rest/v1/jerry_cache',
        params={
            'cache_key': f'like.sweat_card_%',
            'sport': 'eq.MLB',
            'select': 'cache_key,data',
            'order': 'cache_key.desc',
            'limit': '14',
        },
        headers=HEADERS,
    ).json() or []

    resolved_count = 0
    for row in rows:
        cache_key = row.get('cache_key', '')
        if not cache_key.startswith('sweat_card_'):
            continue
        slate_date = cache_key.replace('sweat_card_', '')
        if slate_date < start_date or slate_date > end_date:
            continue

        data = row.get('data') or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as e:
                # Was a silent `continue`. Bad JSON in a sweat_card row
                # silently skips that day's resolution — same anti-pattern
                # as the 6/6 blackout class. Log so we know which row
                # couldn't be parsed and someone can fix the data.
                print(f"  ⚠️  sweat_card {cache_key} has malformed JSON ({type(e).__name__}: {e}) — skipped")
                continue
        top_8 = data.get('top_8') or []
        if not top_8:
            continue

        # Re-process when ANY pick is Pending OR Push. The Push re-check is
        # the recovery mechanism for the 6/6 silent-blackout class of bug:
        # when log_game_result silently failed, picks landed as Push via the
        # now-fixed postponement heuristic. Without a Push re-grade, those
        # rows stay wrong even after upstream data backfills. The single-pick
        # walker below verifies Pushes against current MLB API ground truth
        # and only flips them if the game actually played.
        needs_walk = any(
            (not p.get('result')) or p.get('result') in ('Pending', 'Push')
            for p in top_8
        )
        if not needs_walk:
            continue

        changed = False
        for pick in top_8:
            current = pick.get('result')
            # Skip terminal Win/Loss — those are settled, no re-grade
            if current in ('Win', 'Loss'):
                continue
            # For Push, only overwrite if the new grade is a real outcome
            # (Win/Loss). Don't downgrade Push → Pending or Push → Push.
            result = _resolve_single_pick(pick, slate_date)
            if not result:
                continue
            if current == 'Push' and result not in ('Win', 'Loss'):
                continue
            if result != current:
                pick['result'] = result
                changed = True

        if changed:
            # Compute summary
            wins = sum(1 for p in top_8 if p.get('result') == 'Win')
            losses = sum(1 for p in top_8 if p.get('result') == 'Loss')
            pushes = sum(1 for p in top_8 if p.get('result') == 'Push')
            pending = sum(1 for p in top_8 if p.get('result') == 'Pending')
            data['top_8'] = top_8
            data['top_8_summary'] = {
                'wins': wins,
                'losses': losses,
                'pushes': pushes,
                'pending': pending,
                'resolved': wins + losses + pushes,
            }
            data['top_8_resolved_at'] = datetime.now(timezone.utc).isoformat()

            requests.patch(
                f'{SUPABASE_URL}/rest/v1/jerry_cache',
                params={'cache_key': f'eq.{cache_key}'},
                headers={**HEADERS, 'Prefer': 'return=minimal'},
                json={'data': data},
            )
            print(f'  📋 {slate_date} Sweat Card: {wins}-{losses}{" ("+str(pushes)+"P)" if pushes else ""} ({pending} pending)')
            resolved_count += 1

            # Self-heal: today's sweat_card has a yesterday_recap snapshot
            # baked in. When the 6am card-walker grades a row that didn't
            # resolve before sweat_card built (e.g. resolver bug on a new
            # bet type), the snapshot stays stale. Whenever we patch ANY
            # sweat_card, also refresh the *following day's* yesterday_recap
            # if it points at the date we just patched. 5/28 LAD -1.5 was
            # the trigger — run-line POTD stayed Pending in the snapshot
            # all day until manual fix.
            try:
                next_day = (datetime.strptime(slate_date, '%Y-%m-%d').date() + timedelta(days=1)).isoformat()
                _refresh_next_day_yesterday_recap(next_day, slate_date, top_8, data.get('top_8_summary'))
            except Exception as e:
                print(f'  ⚠️  self-heal of next-day recap failed for {slate_date}: {e}')

    return resolved_count


def _refresh_next_day_yesterday_recap(next_day, slate_date, top_8, summary):
    """Patch the {next_day} sweat_card's yesterday_recap to mirror the
    freshly-walked picks for slate_date. No-op if the next-day row doesn't
    exist or its recap points at a different date. Idempotent."""
    rows = requests.get(
        f'{SUPABASE_URL}/rest/v1/jerry_cache',
        params={'cache_key': f'eq.sweat_card_{next_day}', 'select': 'data'},
        headers=HEADERS,
        timeout=15,
    ).json() or []
    if not rows:
        return
    data = rows[0].get('data') or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return
    y = data.get('yesterday_recap') or {}
    if y.get('date') != slate_date:
        # Next-day card is showing a different yesterday — don't clobber.
        return

    # Mirror the walked top_8 grades (rank/tier/label/result/game)
    y['top_8'] = [
        {'rank': p.get('rank'), 'tier': p.get('tier'), 'label': p.get('label'),
         'result': p.get('result'), 'game': p.get('game'), 'type': p.get('type')}
        for p in top_8
    ]
    if summary:
        y['top_8_summary'] = summary
    # Mirror POTD / Dawg result from the type-tagged top_8 entries
    potd_pk = next((p for p in top_8 if p.get('type') == 'POTD'), None)
    if potd_pk and y.get('potd'):
        y['potd']['result'] = potd_pk.get('result')
    dawg_pk = next((p for p in top_8 if p.get('type') == 'DotD'), None)
    if dawg_pk and y.get('dawg'):
        y['dawg']['result_status'] = dawg_pk.get('result')

    data['yesterday_recap'] = y
    requests.patch(
        f'{SUPABASE_URL}/rest/v1/jerry_cache',
        params={'cache_key': f'eq.sweat_card_{next_day}'},
        headers={**HEADERS, 'Prefer': 'return=minimal'},
        json={'data': data},
        timeout=15,
    )
    s = y.get('top_8_summary') or {}
    print(f'  🔁 healed {next_day}.yesterday_recap ← {slate_date} ({s.get("wins","?")}-{s.get("losses","?")})')


def _resolve_single_pick(pick, slate_date):
    """Look up a single top_8 pick's result based on its source_table.
    Returns 'Win' | 'Loss' | 'Push' | 'Pending'."""
    source = pick.get('source_table')
    key = pick.get('source_key')

    if source == 'daily_best_bet_history':
        # POTD lookup: bet_date + sport
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/daily_best_bet_history',
            params={'bet_date': f'eq.{slate_date}', 'sport': 'eq.MLB', 'select': 'result'},
            headers=HEADERS, timeout=10,
        ).json()
        if r and r[0].get('result') in ('Win', 'Loss', 'Push'):
            return r[0]['result']
        return 'Pending'

    if source == 'daily_dawg':
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/daily_dawg',
            params={'game_date': f'eq.{slate_date}', 'select': 'result'},
            headers=HEADERS, timeout=10,
        ).json()
        if r and r[0].get('result') in ('Win', 'Loss', 'Push'):
            return r[0]['result']
        return 'Pending'

    if source == 'mlb_pipeline_props':
        # Composite key: "PlayerName|prop_type|prop_line"
        try:
            player, ptype, pline = (key or '').split('|', 2)
        except ValueError:
            return 'Pending'
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_pipeline_props',
            params={
                'game_date': f'eq.{slate_date}',
                'player_name': f'eq.{player}',
                'prop_type': f'eq.{ptype}',
                'prop_line': f'eq.{pline}',
                'select': 'result',
            },
            headers=HEADERS, timeout=10,
        ).json()
        if r and r[0].get('result') in ('Win', 'Loss', 'Push'):
            return r[0]['result']
        return 'Pending'

    if source == 'mlb_game_results':
        # Evaluate game-side picks (ML / RL / total) against final scores
        if not key:
            return 'Pending'
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results',
            params={'game_id': f'eq.{key}', 'select': 'home_team,away_team,home_score,away_score,home_win'},
            headers=HEADERS, timeout=10,
        ).json()
        if not r or r[0].get('home_score') is None:
            # Look up team names from mlb_game_context (the row may exist
            # there even when mlb_game_results doesn't yet) so we can ask
            # the MLB API for authoritative game state.
            sg_home = sg_away = None
            if r:
                sg_home = r[0].get('home_team')
                sg_away = r[0].get('away_team')
            if not (sg_home and sg_away):
                ctx_r = requests.get(
                    f'{SUPABASE_URL}/rest/v1/mlb_game_context',
                    params={'game_id': f'eq.{key}', 'select': 'home_team,away_team'},
                    headers=HEADERS, timeout=10,
                ).json()
                if ctx_r:
                    sg_home = ctx_r[0].get('home_team')
                    sg_away = ctx_r[0].get('away_team')
            if _is_postponed(slate_date, False, home_team=sg_home, away_team=sg_away):
                return 'Push'
            # Self-heal: API may have final score even when our DB doesn't.
            state, score = _fetch_mlb_game_state(sg_home, sg_away, slate_date)
            if state == 'final' and score and sg_home and sg_away:
                _backfill_score_to_results(key, sg_home, sg_away, slate_date, score)
                g = {'home_team': sg_home, 'away_team': sg_away,
                     'home_score': score['home_score'],
                     'away_score': score['away_score'],
                     'home_win': score['home_win']}
            else:
                return 'Pending'
        else:
            g = r[0]
        ev = pick.get('eval') or {}
        etype = ev.get('type')
        try:
            if etype == 'ml':
                # 'side' contains the label e.g. "Atlanta Braves ML"
                # Determine which team is the pick by matching prefix to home/away
                side = (ev.get('side') or '').lower()
                home_in_side = (g['home_team'] or '').lower() in side
                away_in_side = (g['away_team'] or '').lower() in side
                if home_in_side and not away_in_side:
                    return 'Win' if g['home_win'] else 'Loss'
                if away_in_side and not home_in_side:
                    return 'Win' if not g['home_win'] else 'Loss'
                # Last-name match fallback
                home_last = (g['home_team'] or '').split()[-1].lower()
                away_last = (g['away_team'] or '').split()[-1].lower()
                if home_last in side and away_last not in side:
                    return 'Win' if g['home_win'] else 'Loss'
                if away_last in side and home_last not in side:
                    return 'Win' if not g['home_win'] else 'Loss'
                return 'Pending'

            if etype == 'over':
                line = float(ev.get('line') or 0)
                total = (g['home_score'] or 0) + (g['away_score'] or 0)
                if total > line: return 'Win'
                if total < line: return 'Loss'
                return 'Push'
            if etype == 'under':
                line = float(ev.get('line') or 0)
                total = (g['home_score'] or 0) + (g['away_score'] or 0)
                if total < line: return 'Win'
                if total > line: return 'Loss'
                return 'Push'

            if etype == 'nrfi':
                # Need nrfi_result on the game row
                rg = requests.get(
                    f'{SUPABASE_URL}/rest/v1/mlb_game_results',
                    params={'game_id': f'eq.{key}', 'select': 'nrfi_result'},
                    headers=HEADERS, timeout=10,
                ).json()
                if not rg or not rg[0].get('nrfi_result'):
                    # Same team-lookup pattern as the game_results branch above
                    # so postponement detection has MLB API ground truth.
                    nrfi_home = nrfi_away = None
                    if rg:
                        nrfi_home = (rg[0] or {}).get('home_team')
                        nrfi_away = (rg[0] or {}).get('away_team')
                    if not (nrfi_home and nrfi_away):
                        ctx_r = requests.get(
                            f'{SUPABASE_URL}/rest/v1/mlb_game_context',
                            params={'game_id': f'eq.{key}', 'select': 'home_team,away_team'},
                            headers=HEADERS, timeout=10,
                        ).json()
                        if ctx_r:
                            nrfi_home = ctx_r[0].get('home_team')
                            nrfi_away = ctx_r[0].get('away_team')
                    if _is_postponed(slate_date, False, home_team=nrfi_home, away_team=nrfi_away):
                        return 'Push'
                    return 'Pending'
                return 'Win' if rg[0]['nrfi_result'] == 'NRFI' else 'Loss'
            if etype == 'yrfi':
                rg = requests.get(
                    f'{SUPABASE_URL}/rest/v1/mlb_game_results',
                    params={'game_id': f'eq.{key}', 'select': 'nrfi_result'},
                    headers=HEADERS, timeout=10,
                ).json()
                if not rg or not rg[0].get('nrfi_result'):
                    # Same team-lookup pattern as the game_results branch above
                    # so postponement detection has MLB API ground truth.
                    nrfi_home = nrfi_away = None
                    if rg:
                        nrfi_home = (rg[0] or {}).get('home_team')
                        nrfi_away = (rg[0] or {}).get('away_team')
                    if not (nrfi_home and nrfi_away):
                        ctx_r = requests.get(
                            f'{SUPABASE_URL}/rest/v1/mlb_game_context',
                            params={'game_id': f'eq.{key}', 'select': 'home_team,away_team'},
                            headers=HEADERS, timeout=10,
                        ).json()
                        if ctx_r:
                            nrfi_home = ctx_r[0].get('home_team')
                            nrfi_away = ctx_r[0].get('away_team')
                    if _is_postponed(slate_date, False, home_team=nrfi_home, away_team=nrfi_away):
                        return 'Push'
                    return 'Pending'
                return 'Win' if rg[0]['nrfi_result'] == 'YRFI' else 'Loss'
        except Exception as e:
            print(f'  Sweat Card pick eval error: {e}')
            return 'Pending'

    return 'Pending'


def run_card_only():
    """Re-walk just the sweat-card top_8 picks (no game logging / no
    props or dawg resolution). Intended to run AFTER resolve_potd.py so the
    POTD result that was just written to daily_best_bet_history propagates
    into the cached card.

    Background (2026-05-26 incident, recurring for 2 days): the workflow
    runs resolve_game_results.py BEFORE resolve_potd.py. The card walk
    inside resolve_game_results.py reads daily_best_bet_history for the
    POTD row's result — but resolve_potd.py hasn't run yet, so the POTD
    result is still Pending in history. Card top_8[0] stays Pending until
    next day's resolver. Same race two nights in a row (NRFI POTD 5/24
    Loss, NRFI POTD 5/25 Win — both stuck Pending on the card).

    Fix: add this --card-only entry point and call it from a NEW workflow
    step that runs AFTER resolve_potd.py. The card gets a second walk with
    the POTD result now graded; the race condition is gone.
    """
    et_today = _et_today()
    week_ago = (et_today - timedelta(days=7)).isoformat()
    yesterday = (et_today - timedelta(days=1)).isoformat()
    print('Re-walking Sweat Card top_8 picks (post-POTD pass)...')
    sc_resolved = _resolve_sweat_card_top8(week_ago, yesterday)
    print(f'Done! {sc_resolved} sweat card sets walked')


if __name__ == '__main__':
    if '--card-only' in sys.argv:
        run_card_only()
    else:
        run()
