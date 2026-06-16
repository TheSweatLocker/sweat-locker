"""
NBA pick logger — captures per-game state + outcomes for offseason audit.

For every NBA game on today's slate, logs to nba_game_results:
  - Game info (teams, date)
  - Features at pick time (net ratings, def ratings, pace, injuries)
  - Market lines (open/close spread, total, ML) — same lock semantics as MLB
  - Score outcomes (filled in by resolver after games end)

Run daily via GitHub Actions cron alongside MLB pipeline. Resolver fills
home_score / away_score from BDL boxscores once games complete.

Goal: build 2-3 weeks of resolved data so we can audit NBA model performance
before committing to PRIME-tier elevations or other NBA-specific tuning.

Usage:
  python mlb_pipeline/nba_pick_logger.py           # Log today's slate
  python mlb_pipeline/nba_pick_logger.py --resolve # Fetch boxscores + fill results

Required SQL migration:
  CREATE TABLE IF NOT EXISTS nba_game_results (
    game_id TEXT PRIMARY KEY,
    game_date DATE,
    season TEXT,
    home_team TEXT,
    away_team TEXT,
    home_score INTEGER,
    away_score INTEGER,
    home_win BOOLEAN,
    total_points INTEGER,
    home_net_rating NUMERIC,
    away_net_rating NUMERIC,
    net_rating_gap NUMERIC,
    home_def_rating NUMERIC,
    away_def_rating NUMERIC,
    home_off_rating NUMERIC,
    away_off_rating NUMERIC,
    pace NUMERIC,
    home_injury_note TEXT,
    away_injury_note TEXT,
    open_spread NUMERIC,
    close_spread NUMERIC,
    open_total NUMERIC,
    close_total NUMERIC,
    home_ml_open INTEGER,
    away_ml_open INTEGER,
    home_ml_close INTEGER,
    away_ml_close INTEGER,
    spread_result TEXT,
    total_result TEXT,
    result_logged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
"""
import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from season_calendar import is_in_season, season_status

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
BDL_API_KEY = os.environ.get('BDL_API_KEY')

HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
WRITE_HEADERS = {**HEADERS, 'Content-Type': 'application/json',
                 'Prefer': 'resolution=merge-duplicates,return=minimal'}


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def is_open_run():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.hour < 13  # before 1pm ET = open run


def fetch_odds_games():
    """Pull today's NBA games + lines from Odds API"""
    if not ODDS_API_KEY:
        return []
    r = requests.get(
        'https://api.the-odds-api.com/v4/sports/basketball_nba/odds',
        params={'apiKey': ODDS_API_KEY, 'regions': 'us',
                'markets': 'h2h,spreads,totals', 'oddsFormat': 'american'},
        timeout=20
    )
    if r.status_code != 200:
        print(f'  Odds API error: {r.status_code}')
        return []
    return r.json()


def fetch_team_stats():
    """Map team_name -> stats from nba_team_stats"""
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/nba_team_stats?select=*',
        headers=HEADERS, timeout=15
    )
    if r.status_code != 200:
        return {}
    return {row.get('team') or row.get('full_name'): row for row in r.json()}


def extract_market_lines(game):
    """Pull spread, total, ML from bookmakers (median across)"""
    spreads, totals, hmls, amls = [], [], [], []
    for bm in game.get('bookmakers', []):
        for mkt in bm.get('markets', []):
            if mkt['key'] == 'spreads':
                home = next((o for o in mkt['outcomes'] if o['name'] == game['home_team']), None)
                if home and home.get('point') is not None:
                    spreads.append(home['point'])
            elif mkt['key'] == 'totals':
                t = mkt['outcomes'][0] if mkt.get('outcomes') else None
                if t and t.get('point') is not None:
                    totals.append(t['point'])
            elif mkt['key'] == 'h2h':
                for o in mkt['outcomes']:
                    if o['name'] == game['home_team']:
                        hmls.append(o['price'])
                    elif o['name'] == game['away_team']:
                        amls.append(o['price'])
    median = lambda arr: sorted(arr)[len(arr)//2] if arr else None
    return median(spreads), median(totals), median(hmls), median(amls)


def log_picks():
    games = fetch_odds_games()
    if not games:
        print('No NBA games on slate today')
        return
    team_stats = fetch_team_stats()
    print(f'Pulled {len(games)} NBA games, {len(team_stats)} teams in cache')

    open_run = is_open_run()
    print(f"  Run mode: {'OPEN (morning)' if open_run else 'CLOSE (afternoon)'}")

    for g in games:
        try:
            game_id = g['id']
            home_team = g['home_team']
            away_team = g['away_team']
            commence_time = g.get('commence_time')

            # Pre-game lock — skip if game has started
            if commence_time:
                game_dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= game_dt:
                    print(f'  ⏭ {away_team} @ {home_team} — game started, skipping line update')
                    continue

            spread, total, hml, aml = extract_market_lines(g)

            home_stats = team_stats.get(home_team, {})
            away_stats = team_stats.get(away_team, {})

            record = {
                'game_id': game_id,
                'game_date': today_et(),
                'season': '2025-26',
                'home_team': home_team,
                'away_team': away_team,
                'home_net_rating': home_stats.get('net_rating'),
                'away_net_rating': away_stats.get('net_rating'),
                'net_rating_gap': (
                    (home_stats.get('net_rating') - away_stats.get('net_rating'))
                    if home_stats.get('net_rating') is not None and away_stats.get('net_rating') is not None
                    else None
                ),
                'home_def_rating': home_stats.get('defensive_rating'),
                'away_def_rating': away_stats.get('defensive_rating'),
                'home_off_rating': home_stats.get('offensive_rating'),
                'away_off_rating': away_stats.get('offensive_rating'),
                'pace': home_stats.get('pace') or away_stats.get('pace'),
                'home_injury_note': home_stats.get('injury_note'),
                'away_injury_note': away_stats.get('injury_note'),
            }
            # Open/close lines — same omit-None pattern as MLB pre-game lock
            if open_run:
                if spread is not None: record['open_spread'] = spread
                if total is not None: record['open_total'] = total
                if hml is not None: record['home_ml_open'] = hml
                if aml is not None: record['away_ml_open'] = aml
            else:
                if spread is not None: record['close_spread'] = spread
                if total is not None: record['close_total'] = total
                if hml is not None: record['home_ml_close'] = hml
                if aml is not None: record['away_ml_close'] = aml

            r = requests.post(
                f'{SUPABASE_URL}/rest/v1/nba_game_results?on_conflict=game_id',
                headers=WRITE_HEADERS, json=record, timeout=15
            )
            if r.status_code in (200, 201, 204):
                lines_str = f"sp={spread} tot={total} hml={hml} aml={aml}"
                print(f'  ✅ {away_team} @ {home_team} | {lines_str}')
            else:
                print(f'  ❌ {away_team} @ {home_team}: {r.status_code} {r.text[:200]}')
        except Exception as e:
            print(f'  ⚠️ Error on {g.get("home_team", "?")}: {e}')


def resolve_results():
    """Fetch BDL boxscores for unresolved games, fill scores."""
    if not BDL_API_KEY:
        print('No BDL_API_KEY — cannot resolve')
        return
    # Pull unresolved games (any without home_score)
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/nba_game_results?home_score=is.null&select=game_id,game_date,home_team,away_team',
        headers=HEADERS, timeout=15
    )
    pending = r.json()
    if not pending:
        print('No NBA results to resolve')
        return
    print(f'Resolving {len(pending)} pending NBA games...')

    for p in pending:
        try:
            r2 = requests.get(
                'https://api.balldontlie.io/v1/games',
                headers={'Authorization': BDL_API_KEY},
                params={'dates[]': p['game_date'], 'per_page': 100},
                timeout=15
            )
            data = r2.json().get('data', [])
            match = next(
                (g for g in data if g.get('status') == 'Final' and (
                    (p['home_team'].endswith(g['home_team']['name']) or g['home_team']['full_name'] == p['home_team'])
                    and (p['away_team'].endswith(g['visitor_team']['name']) or g['visitor_team']['full_name'] == p['away_team'])
                )),
                None
            )
            if not match:
                continue
            home_score = match['home_team_score']
            away_score = match['visitor_team_score']
            update = {
                'home_score': home_score,
                'away_score': away_score,
                'home_win': home_score > away_score,
                'total_points': home_score + away_score,
                'result_logged_at': datetime.now(timezone.utc).isoformat(),
            }
            r3 = requests.patch(
                f'{SUPABASE_URL}/rest/v1/nba_game_results?game_id=eq.{p["game_id"]}',
                headers={**HEADERS, 'Content-Type': 'application/json',
                         'Prefer': 'return=minimal'},
                json=update, timeout=15
            )
            if r3.status_code in (200, 204):
                print(f'  ✅ {p["away_team"]} @ {p["home_team"]} — {away_score}-{home_score}')
        except Exception as e:
            print(f'  ⚠️ {p["game_id"]}: {e}')

    # Phase 2: grade nba_game_picks from the scores we just filled in.
    resolve_picks()


def resolve_picks():
    """Grade nba_game_picks rows using nba_game_results scores.

    Pick types:
      - ml          → win when picked side won outright
      - ats         → win when picked side covered market_spread (home perspective)
      - total       → win when total over/under hit market_total
      - star_out_skip → never graded (informational row)
    """
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/nba_game_picks?result=is.null&select=*',
        headers=HEADERS, timeout=15
    )
    pending = r.json() if r.status_code == 200 else []
    if not pending:
        print('No NBA picks to grade')
        return
    print(f'Grading {len(pending)} pending NBA picks...')

    # Pull scored games once, key by game_id
    r2 = requests.get(
        f'{SUPABASE_URL}/rest/v1/nba_game_results?home_score=not.is.null&select=game_id,home_score,away_score',
        headers=HEADERS, timeout=15
    )
    scores = {row['game_id']: row for row in (r2.json() if r2.status_code == 200 else [])}

    graded = 0
    for p in pending:
        gid = p['game_id']
        if gid not in scores:
            continue  # game not scored yet
        if p['pick_type'] == 'star_out_skip':
            continue  # informational, no grade
        s = scores[gid]
        hs, as_ = s['home_score'], s['away_score']
        margin = hs - as_  # home perspective
        total_pts = hs + as_
        result = None
        final_value = None

        if p['pick_type'] == 'ml':
            if p['pick_side'] == 'home':
                result = 'Win' if margin > 0 else 'Loss'
            elif p['pick_side'] == 'away':
                result = 'Win' if margin < 0 else 'Loss'
            final_value = margin
        elif p['pick_type'] == 'ats':
            spread = p.get('market_spread')
            if spread is None:
                continue
            # margin > -spread → home covered. Push when equal.
            cover_margin = margin + spread  # home covers when this > 0
            if abs(cover_margin) < 1e-6:
                result = 'Push'
            elif p['pick_side'] == 'home':
                result = 'Win' if cover_margin > 0 else 'Loss'
            else:
                result = 'Win' if cover_margin < 0 else 'Loss'
            final_value = cover_margin
        elif p['pick_type'] == 'total':
            total_line = p.get('market_total')
            if total_line is None:
                continue
            delta = total_pts - total_line
            if abs(delta) < 1e-6:
                result = 'Push'
            elif p['pick_side'] == 'over':
                result = 'Win' if delta > 0 else 'Loss'
            else:
                result = 'Win' if delta < 0 else 'Loss'
            final_value = delta

        if result is None:
            continue
        update = {
            'result': result,
            'final_value': final_value,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        }
        # Compose URL filter — pick_side may be NULL for some rows but ml/ats/total always have it.
        url = (f'{SUPABASE_URL}/rest/v1/nba_game_picks'
               f'?game_id=eq.{gid}&pick_type=eq.{p["pick_type"]}'
               f'&pick_side=eq.{p["pick_side"]}')
        r3 = requests.patch(
            url, headers={**HEADERS, 'Content-Type': 'application/json',
                          'Prefer': 'return=minimal'},
            json=update, timeout=15
        )
        if r3.status_code in (200, 204):
            graded += 1
            print(f'  ✅ {p["away_team"]} @ {p["home_team"]} — [{p["tier"]}] {p["pick_label"]} → {result}')
        else:
            print(f'  ❌ grade failed {gid} {p["pick_type"]}/{p["pick_side"]}: {r3.status_code}')

    print(f'Graded {graded} picks')


def _gate(mode):
    if not is_in_season("NBA"):
        print(f"[OFFSEASON]{season_status('NBA')} — skipping nba_pick_logger.py ({mode})")
        return False
    # log_picks uses Odds API only; --resolve uses BDL. Gate BDL on key presence.
    if mode == 'resolve' and not BDL_API_KEY:
        print("[OFFSEASON]BDL_API_KEY not set — skipping nba_pick_logger.py --resolve")
        return False
    return True


if __name__ == '__main__':
    if '--resolve' in sys.argv:
        if _gate('resolve'):
            resolve_results()
    else:
        if _gate('log'):
            log_picks()
