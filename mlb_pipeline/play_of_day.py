"""
Play of the Day — runs after game_context.py in the pipeline.
Scans all games across MLB and NBA, picks the single best play,
and stores it in jerry_cache for the app to read.
"""
import requests
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def get_today_et():
    """Get today's date in ET"""
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')

def get_mlb_games():
    """Fetch today's MLB game context from Supabase"""
    today = get_today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{today}&select=*",
        headers=HEADERS
    )
    data = r.json()
    if isinstance(data, list):
        return data
    return []

def get_nba_teams():
    """Fetch NBA team stats from Supabase"""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nba_team_stats?season=eq.2025-26&select=*",
        headers=HEADERS
    )
    data = r.json()
    if isinstance(data, list):
        return data
    return []

def get_nba_games():
    """Fetch today's NBA games from Odds API"""
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/basketball_nba/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals,h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings"
            },
            timeout=15
        )
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0)
        today_end = now.replace(hour=23, minute=59, second=59)
        games = []
        for g in r.json():
            t = datetime.fromisoformat(g['commence_time'].replace('Z', '+00:00'))
            if today_start <= t <= today_end:
                games.append(g)
        return games
    except Exception as e:
        print(f"NBA games fetch error: {e}")
        return []

def score_mlb_game(ctx):
    """Score an MLB game for Play of the Day candidacy"""
    score = 30  # base

    # NRFI signal — strongest indicator
    nrfi = ctx.get('nrfi_score') or 0
    if nrfi >= 88:
        score += 25
    elif nrfi >= 75:
        score += 18
    elif nrfi >= 70:
        score += 12

    # Pitcher quality — xERA gap
    home_xera = float(ctx.get('home_sp_xera') or 4.5)
    away_xera = float(ctx.get('away_sp_xera') or 4.5)
    xera_gap = abs(home_xera - away_xera)
    if xera_gap >= 2.0:
        score += 15
    elif xera_gap >= 1.0:
        score += 8

    # Both pitchers elite
    if home_xera <= 3.0 and away_xera <= 3.0:
        score += 10

    # Spread delta
    spread_delta = abs(float(ctx.get('spread_delta') or 0))
    if spread_delta >= 2.0:
        score += 10
    elif spread_delta >= 1.0:
        score += 5

    # Total delta
    proj_total = float(ctx.get('projected_total') or 0)
    close_total = float(ctx.get('close_total') or ctx.get('open_total') or 0)
    if proj_total > 0 and close_total > 0:
        total_delta = abs(proj_total - close_total)
        if total_delta >= 2.0:
            score += 12
        elif total_delta >= 1.0:
            score += 6

    # K gap signal
    home_k_gap = abs(float(ctx.get('home_k_gap') or 0))
    away_k_gap = abs(float(ctx.get('away_k_gap') or 0))
    if home_k_gap >= 10 or away_k_gap >= 10:
        score += 8

    # Park + weather
    park = float(ctx.get('park_run_factor') or 100)
    if park >= 108 or park <= 93:
        score += 5

    temp = float(ctx.get('temperature') or 70)
    if temp <= 45:
        score += 3  # cold = pitcher advantage = more predictable

    return min(100, score)

def score_nba_game(game, nba_teams):
    """Score an NBA game for Play of the Day candidacy"""
    score = 25  # base

    home_team = game.get('home_team', '')
    away_team = game.get('away_team', '')

    home_data = next((t for t in nba_teams if home_team.endswith(t.get('team', '').split(' ')[-1])), None)
    away_data = next((t for t in nba_teams if away_team.endswith(t.get('team', '').split(' ')[-1])), None)

    if not home_data or not away_data:
        return score

    # Net rating gap
    home_net = float(home_data.get('net_rating') or 0)
    away_net = float(away_data.get('net_rating') or 0)
    net_gap = abs(home_net - away_net)
    if net_gap >= 8:
        score += 20
    elif net_gap >= 5:
        score += 12
    elif net_gap >= 3:
        score += 6

    # Defensive rating mismatch
    home_def = float(home_data.get('defensive_rating') or 112)
    away_def = float(away_data.get('defensive_rating') or 112)
    def_gap = abs(home_def - away_def)
    if def_gap >= 5:
        score += 10

    # Home/away record edge
    home_record = home_data.get('home_record', '')
    away_record = away_data.get('away_record', '')
    # Parse W-L from "28-13" format
    try:
        hw, hl = map(int, home_record.split('-'))
        aw, al = map(int, away_record.split('-'))
        home_wpct = hw / (hw + hl) if (hw + hl) > 0 else 0.5
        away_wpct = aw / (aw + al) if (aw + al) > 0 else 0.5
        if home_wpct - away_wpct >= 0.2:
            score += 10
    except:
        pass

    return min(100, score)

def build_lean(ctx):
    """Determine the lean for an MLB game"""
    # NRFI first
    nrfi = ctx.get('nrfi_score') or 0
    if nrfi >= 75:
        return f"NRFI — Score {nrfi}/100", 'nrfi', True

    # Spread lean
    spread_lean = ctx.get('spread_lean')
    if spread_lean:
        team = ctx.get('home_team') if spread_lean == 'home' else ctx.get('away_team')
        return team, 'ml', False

    # Total lean
    over_lean = ctx.get('over_lean')
    if over_lean is not None:
        total = ctx.get('close_total') or ctx.get('open_total') or ''
        side = 'Over' if over_lean else 'Under'
        return f"{side} {total}", 'total', False

    return None, None, False

def run():
    today = get_today_et()
    print(f"Play of the Day — scanning {today}")

    # Check if today's pick already exists — first pick of the day locks
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?game_id=eq.best_bet_{today}&select=data",
            headers=HEADERS
        )
        existing = r.json()
        if existing and len(existing) > 0 and existing[0].get('data', {}).get('pipelineGenerated'):
            print(f"✅ Today's pick already locked — skipping regeneration")
            return
    except:
        pass

    # Get all MLB games with context
    mlb_games = get_mlb_games()
    print(f"MLB games: {len(mlb_games)}")

    # Get NBA data
    nba_teams = get_nba_teams()
    nba_games = get_nba_games()
    print(f"NBA games: {len(nba_games)}, teams: {len(nba_teams)}")

    # Score all candidates
    candidates = []

    for ctx in mlb_games:
        game_score = score_mlb_game(ctx)
        lean_display, lean_bet, is_nrfi = build_lean(ctx)
        candidates.append({
            'sport': 'MLB',
            'home_team': ctx.get('home_team'),
            'away_team': ctx.get('away_team'),
            'score': game_score,
            'nrfi_score': ctx.get('nrfi_score'),
            'is_nrfi': is_nrfi,
            'lean_display': lean_display,
            'lean_bet': lean_bet,
            'home_pitcher': ctx.get('home_pitcher'),
            'away_pitcher': ctx.get('away_pitcher'),
            'home_sp_xera': ctx.get('home_sp_xera'),
            'away_sp_xera': ctx.get('away_sp_xera'),
            'projected_total': ctx.get('projected_total'),
            'spread_delta': ctx.get('spread_delta'),
            'venue': ctx.get('venue'),
            'temperature': ctx.get('temperature'),
        })

    for game in nba_games:
        game_score = score_nba_game(game, nba_teams)
        home_data = next((t for t in nba_teams if game['home_team'].endswith(t.get('team', '').split(' ')[-1])), None)
        away_data = next((t for t in nba_teams if game['away_team'].endswith(t.get('team', '').split(' ')[-1])), None)
        home_net = float(home_data.get('net_rating', 0)) if home_data else 0
        away_net = float(away_data.get('net_rating', 0)) if away_data else 0
        fav = game['home_team'] if home_net > away_net else game['away_team']
        candidates.append({
            'sport': 'NBA',
            'home_team': game.get('home_team'),
            'away_team': game.get('away_team'),
            'score': game_score,
            'nrfi_score': None,
            'is_nrfi': False,
            'lean_display': fav.split(' ')[-1] if game_score >= 50 else None,
            'lean_bet': 'ml',
            'commence_time': game.get('commence_time'),
        })

    if not candidates:
        print("No games found — storing noGames")
        requests.post(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "cache_key": f"best_bet_{today}",
                "game_id": f"best_bet_{today}",
                "sport": "none",
                "narrative": "No games on the slate today.",
                "data": {"noGames": True},
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    # Sort by score — NRFI 75+ gets priority unless another game scores 80+
    candidates.sort(key=lambda c: c['score'], reverse=True)

    # Find the strongest NRFI — sort by NRFI score, not game score
    nrfi_candidates = [c for c in candidates if c.get('is_nrfi') and (c.get('nrfi_score') or 0) >= 75]
    nrfi_candidates.sort(key=lambda c: c.get('nrfi_score', 0), reverse=True)
    best_nrfi = nrfi_candidates[0] if nrfi_candidates else None
    best_overall = candidates[0]

    # Highest NRFI score wins unless a non-NRFI game scores 80+
    if best_nrfi and (best_overall['score'] < 80 or best_overall.get('is_nrfi')):
        pick = best_nrfi
        print(f"🔒 NRFI pick: {pick['away_team']} @ {pick['home_team']} — NRFI {pick['nrfi_score']}")
    else:
        pick = best_overall
        print(f"🎯 Top pick: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']} ({pick['sport']})")

    # Print all candidates
    for c in candidates[:5]:
        nrfi_str = f" | NRFI {c['nrfi_score']}" if c.get('nrfi_score') else ''
        print(f"  {c['sport']} {c['away_team']} @ {c['home_team']} — Score {c['score']}{nrfi_str} | Lean: {c.get('lean_display') or 'none'}")

    # Build the result — app will generate Jerry narrative on first load
    result = {
        'game': {
            'home_team': pick['home_team'],
            'away_team': pick['away_team'],
            'commence_time': pick.get('commence_time'),
        },
        'sport': pick['sport'],
        'score': {'total': pick['score'], 'isNRFI': pick.get('is_nrfi', False), 'nrfiScore': pick.get('nrfi_score')},
        'leanDisplay': pick.get('lean_display') or f"{pick['away_team']} @ {pick['home_team']}",
        'generatedAt': today,
        'pipelineGenerated': True,
        # Include context for Jerry narrative generation
        'context': {
            'home_pitcher': pick.get('home_pitcher'),
            'away_pitcher': pick.get('away_pitcher'),
            'home_sp_xera': pick.get('home_sp_xera'),
            'away_sp_xera': pick.get('away_sp_xera'),
            'projected_total': pick.get('projected_total'),
            'spread_delta': pick.get('spread_delta'),
            'nrfi_score': pick.get('nrfi_score'),
            'venue': pick.get('venue'),
            'temperature': pick.get('temperature'),
        },
    }

    # Store in jerry_cache
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={
            "cache_key": f"best_bet_{today}",
            "game_id": f"best_bet_{today}",
            "sport": pick['sport'],
            "narrative": f"Play of the Day: {pick['away_team']} @ {pick['home_team']} | {pick.get('lean_display', '')}",
            "data": result,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if r.status_code in [200, 201, 204]:
        print(f"✅ Play of the Day stored: {pick['sport']} {pick['away_team']} @ {pick['home_team']} | Lean: {pick.get('lean_display')}")
    else:
        print(f"❌ Cache store failed: {r.status_code} {r.text[:200]}")

    # Also log to history
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?on_conflict=bet_date",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "bet_date": today,
                "sport": pick['sport'],
                "game": f"{pick['away_team']} @ {pick['home_team']}",
                "lean": pick.get('lean_display'),
                "sweat_score": pick['score'],
                "result": "Pending",
            }
        )
    except:
        pass

if __name__ == '__main__':
    run()
