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

def get_mlb_game_times(date_str):
    """Fetch game commence times from MLB Stats API for matching teams"""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": date_str},
            timeout=15
        )
        times = {}
        for d in r.json().get("dates", []):
            for g in d.get("games", []):
                home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                game_time = g.get("gameDate")  # ISO format UTC
                if home and game_time:
                    times[home] = game_time
        return times
    except Exception as e:
        print(f"MLB game time fetch error: {e}")
        return {}

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

    # NRFI signal — 88-94 sweet spot is highest conviction (77% hit rate)
    nrfi = ctx.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        score += 30    # prime sweet spot
    elif 88 <= nrfi <= 89:
        score += 22    # edge of sweet spot
    elif nrfi >= 95:
        score += 10    # historically volatile — reduced boost
    elif nrfi >= 75:
        score += 15
    elif nrfi >= 70:
        score += 10

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

    # Spread delta — retuned 2026-04-24 after sign-bug fix.
    # OLD (buggy 2x-inflated): 4.0+ HIGH, 3.0+ STRONG, 2.0+ LEAN
    # NEW (corrected): 1.5+ HIGH, 1.0+ STRONG, 0.5+ LEAN — same hit-rate buckets, real magnitudes
    spread_delta = abs(float(ctx.get('spread_delta') or 0))
    if spread_delta >= 1.5:
        score += 18    # massive market disagreement (was old 4.0)
    elif spread_delta >= 1.0:
        score += 12    # proven 60-70% threshold (was old 3.0)
    elif spread_delta >= 0.5:
        score += 4     # marginal lean (was old 2.0)

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
    """Score an NBA game for Play of the Day candidacy — playoff-enhanced"""
    score = 25  # base

    home_team = game.get('home_team', '')
    away_team = game.get('away_team', '')

    home_data = next((t for t in nba_teams if home_team.endswith(t.get('team', '').split(' ')[-1])), None)
    away_data = next((t for t in nba_teams if away_team.endswith(t.get('team', '').split(' ')[-1])), None)

    if not home_data or not away_data:
        return score, None, None

    home_net = float(home_data.get('net_rating') or 0)
    away_net = float(away_data.get('net_rating') or 0)
    home_def = float(home_data.get('defensive_rating') or 112)
    away_def = float(away_data.get('defensive_rating') or 112)
    home_pace = float(home_data.get('pace') or 100)
    away_pace = float(away_data.get('pace') or 100)

    # Net rating gap — strongest NBA predictor
    net_gap = abs(home_net - away_net)
    if net_gap >= 10:
        score += 25
    elif net_gap >= 8:
        score += 20
    elif net_gap >= 5:
        score += 12
    elif net_gap >= 3:
        score += 6

    # Defensive rating mismatch
    def_gap = abs(home_def - away_def)
    if def_gap >= 6:
        score += 12
    elif def_gap >= 4:
        score += 8

    # Home/away record edge
    home_record = home_data.get('home_record', '')
    away_record = away_data.get('away_record', '')
    home_wpct = 0.5
    away_wpct = 0.5
    try:
        hw, hl = map(int, home_record.split('-'))
        aw, al = map(int, away_record.split('-'))
        home_wpct = hw / (hw + hl) if (hw + hl) > 0 else 0.5
        away_wpct = aw / (aw + al) if (aw + al) > 0 else 0.5
        if home_wpct - away_wpct >= 0.25:
            score += 12
        elif home_wpct - away_wpct >= 0.15:
            score += 6
    except:
        pass

    # Playoff boost — April 19+ is playoffs, matchups are more predictable
    is_playoff = datetime.now(timezone.utc).month >= 4 and datetime.now(timezone.utc).day >= 19
    if is_playoff:
        score += 10  # baseline playoff boost — matchups more predictable
        # Home court is stronger in playoffs (65% vs 57% regular season)
        if home_wpct >= 0.65:
            score += 8
        # Both elite defenses = under lean signal
        if home_def <= 110 and away_def <= 110:
            score += 8

    # Determine lean
    lean = None
    lean_type = None
    # Pace-based total lean
    avg_pace = (home_pace + away_pace) / 2
    if home_def <= 110 and away_def <= 110 and avg_pace < 100:
        lean = 'Under'
        lean_type = 'total'
    elif home_def >= 115 and away_def >= 115 and avg_pace > 101:
        lean = 'Over'
        lean_type = 'total'
    # Side lean — better team at home with strong record
    elif net_gap >= 5 and home_net > away_net and home_wpct >= 0.6:
        lean = home_team.split(' ')[-1]
        lean_type = 'ml'
    elif net_gap >= 5 and away_net > home_net:
        lean = away_team.split(' ')[-1]
        lean_type = 'ml'

    return min(100, score), lean, lean_type

_V2_BUCKET_CACHE = {}


def _v2_total_edge(ctx):
    """v2 Total OVER Edge — fires when projection_v2 model_total ≥ market+1.5.
    Backtest: 56-34 (62.2%) n=90 over 2807 games. Only durable v2 signal.

    Returns lean label if qualified, otherwise None. Lazy import + module-level
    caches keep play_of_day startup cost minimal.
    """
    close_total = ctx.get('close_total') or ctx.get('open_total')
    if close_total is None:
        return None
    try:
        import projection_v2 as v2
    except Exception:
        return None

    # Lazy-load shared lookup tables once per process (~700 pitchers, 30 teams)
    cache = _V2_BUCKET_CACHE
    if 'pitchers' not in cache:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?select=player_name,innings_1_3_era,innings_1_3_ip,innings_4_6_era,innings_7_9_era&limit=2000",
                headers=HEADERS, timeout=15,
            )
            cache['pitchers'] = {p['player_name']: p for p in (r.json() or [])}
        except Exception:
            cache['pitchers'] = {}
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_team_offense?select=team,innings_1_3_runs_per_game,innings_4_6_runs_per_game,innings_7_9_runs_per_game,last10_runs_per_game",
                headers=HEADERS, timeout=15,
            )
            cache['teams'] = {t['team']: t for t in (r.json() or [])}
        except Exception:
            cache['teams'] = {}
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_bullpen_stats?select=team,pitching_1_3_era,pitching_4_6_era,pitching_7_9_era",
                headers=HEADERS, timeout=15,
            )
            cache['bullpens'] = {b['team']: b for b in (r.json() or [])}
        except Exception:
            cache['bullpens'] = {}

    # Build merged ctx for v2 (mlb_game_context fields + bucket lookups)
    enriched = dict(ctx)
    home_p = ctx.get('home_pitcher')
    away_p = ctx.get('away_pitcher')
    if home_p and home_p in cache.get('pitchers', {}):
        pb = cache['pitchers'][home_p]
        enriched['home_innings_1_3_era'] = pb.get('innings_1_3_era')
        enriched['home_innings_4_6_era'] = pb.get('innings_4_6_era')
        enriched['home_innings_7_9_era'] = pb.get('innings_7_9_era')
        enriched['home_sp_ip'] = pb.get('innings_1_3_ip', 0) or 0
    if away_p and away_p in cache.get('pitchers', {}):
        pb = cache['pitchers'][away_p]
        enriched['away_innings_1_3_era'] = pb.get('innings_1_3_era')
        enriched['away_innings_4_6_era'] = pb.get('innings_4_6_era')
        enriched['away_innings_7_9_era'] = pb.get('innings_7_9_era')
        enriched['away_sp_ip'] = pb.get('innings_1_3_ip', 0) or 0
    home_team = ctx.get('home_team')
    away_team = ctx.get('away_team')
    if home_team in cache.get('teams', {}):
        tb = cache['teams'][home_team]
        enriched['home_innings_1_3_runs_per_game'] = tb.get('innings_1_3_runs_per_game')
        enriched['home_innings_4_6_runs_per_game'] = tb.get('innings_4_6_runs_per_game')
        enriched['home_innings_7_9_runs_per_game'] = tb.get('innings_7_9_runs_per_game')
        enriched['home_last10_runs_per_game'] = tb.get('last10_runs_per_game')
    if away_team in cache.get('teams', {}):
        tb = cache['teams'][away_team]
        enriched['away_innings_1_3_runs_per_game'] = tb.get('innings_1_3_runs_per_game')
        enriched['away_innings_4_6_runs_per_game'] = tb.get('innings_4_6_runs_per_game')
        enriched['away_innings_7_9_runs_per_game'] = tb.get('innings_7_9_runs_per_game')
        enriched['away_last10_runs_per_game'] = tb.get('last10_runs_per_game')

    try:
        proj = v2.project_game(enriched)
    except Exception:
        return None

    delta = proj.model_total - float(close_total)
    if delta >= 1.5 and proj.confidence >= 0.7:
        return (
            f"Over {close_total} (v2 edge — model {proj.model_total:.1f} vs market {close_total}, +{delta:.1f} runs)",
            'total',
            False,
            proj,
        )
    # UNDER edge added 2026-05-07. Symmetric threshold to OVER (|delta| >= 1.5,
    # confidence >= 0.7). Audit cohort total_edge_under_1_5_to_3 is currently
    # 2-0 (small sample) — flag as informational lean, not PRIME.
    if delta <= -1.5 and proj.confidence >= 0.7:
        return (
            f"Under {close_total} (v2 edge — model {proj.model_total:.1f} vs market {close_total}, {delta:.1f} runs)",
            'total',
            False,
            proj,
        )
    return None


def _rl_alt_for_juiced_chalk(ctx):
    """When confluence is PRIME (+4 net) but ML is chalk-juiced (≤-180),
    suggest the run line -1.5 alt instead of the unattractive ML.

    The Cubs 5/7 case was the trigger — model PRIME +8 with reverse-mastery
    on Lowder, but Cubs ML at -207 made the ML EV nearly nil. Cubs RL -1.5
    was the actual play (model projected +2.68 margin). This function
    surfaces that scenario as a POTD candidate.

    Threshold logic:
      - confluence_net ≥ +4 (PRIME)
      - |projected_spread| ≥ 1.5 (RL cover plausible per model)
      - home_ml ≤ -180 OR away_ml ≤ -180 on the favored side
      - direction matches confluence model_pick

    Audit cohort spread_delta_ge2 hits 55% historically — RL covers correlate
    with that signal. Confluence PRIME tier hits 70.6% on direction.
    Combined effect should be net positive at the better RL price (+130-150).
    """
    confluence_net = ctx.get('signal_confluence_net') or 0
    projected_spread = ctx.get('projected_spread')
    home_ml = ctx.get('home_ml_close') or ctx.get('home_ml_open')
    away_ml = ctx.get('away_ml_close') or ctx.get('away_ml_open')
    if confluence_net is None or projected_spread is None:
        return None
    try:
        cn = int(confluence_net)
        ps = float(projected_spread)
    except (TypeError, ValueError):
        return None
    if cn < 4 or abs(ps) < 1.5:
        return None
    # Determine favored side per model
    home_favored = ps > 0
    fav_ml = home_ml if home_favored else away_ml
    fav_team = ctx.get('home_team') if home_favored else ctx.get('away_team')
    if fav_ml is None:
        return None
    try:
        fav_ml_i = int(fav_ml)
    except (TypeError, ValueError):
        return None
    # Only fires when ML is chalk-juiced (eats most of the model edge)
    if fav_ml_i > -180:
        return None
    return (
        f"{fav_team} -1.5 (chalk ML alt — model projects +{abs(ps):.1f} margin, ML {fav_ml_i:+d} too juiced)",
        'runline',
        False,
    )


def build_lean(ctx):
    """Determine the lean for an MLB game.

    ML LEANS REMOVED 2026-05-01 pending projection_v2 rebuild.

    Backtest finding (391 games, 30 days): every ML selection rule we tested
    (PRIME confluence, magnitude ≥3.0, magnitude ≥2.0, xERA gap ≥2.5) hit
    below 50% on POTD picks. Root cause: the projection layer itself uses
    only 4 inputs (xERA, wRC+, bullpen, park) when the schema has 70+. The
    ML formula doesn't beat market efficiency — no filter on top of it will.

    PRIORITIES (post-rebuild 2026-05-01):
      1. NRFI 90-94 PRIME (audited 75% n=16)
      2. v2 Total Over Edge (audited 62.2% n=90 at delta ≥1.5)
      3. NRFI 88-89 edge tier (coin flip but kept as secondary)
      4. v1 over_lean fallback (xERA gap rule etc.)
    """
    nrfi = ctx.get('nrfi_score') or 0

    # 1. PRIME NRFI sweet spot (90-94) — audited 75% over 30d (n=16)
    if 90 <= nrfi <= 94:
        return f"NRFI — Score {nrfi}/100 (sweet spot)", 'nrfi', True

    # 2. v2 Total OVER/UNDER Edge — model_total vs market + 1.5
    v2_pick = _v2_total_edge(ctx)
    if v2_pick is not None:
        # Drop the proj object before returning to keep tuple shape consistent
        return v2_pick[0], v2_pick[1], v2_pick[2]

    # 3. RL alt for juiced chalk (added 2026-05-07 after Cubs 5/7 lesson —
    # PRIME confluence with chalk ML at -200+ surfaces nothing under old logic
    # because ML EV is too thin; RL -1.5 at +130-150 is the actual play when
    # model projects 1.5+ run margin).
    rl_pick = _rl_alt_for_juiced_chalk(ctx)
    if rl_pick is not None:
        return rl_pick

    # 4. NRFI 88-89 edge tier — coin flip historically (3-3) but kept as
    # secondary when no PRIME tier game exists
    if 88 <= nrfi <= 89:
        return f"NRFI — Score {nrfi}/100 (edge tier)", 'nrfi', True

    # 4. Total lean — projected total vs market line (post-rebuild, evaluate
    # whether to keep this branch in POTD or move to props-only display)
    over_lean = ctx.get('over_lean')
    if over_lean is not None:
        total = ctx.get('close_total') or ctx.get('open_total') or ''
        side = 'Over' if over_lean else 'Under'
        return f"{side} {total}", 'total', False

    return None, None, False

def run():
    today = get_today_et()
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    et_hour = et_now.hour
    print(f"Play of the Day — scanning {today} (ET hour: {et_hour})")

    # POTD lock strategy (hybrid):
    # - Pre-8am: always regenerate (stale overnight data)
    # - 8am-11am: always regenerate (early morning data still settling)
    # - 11am+ lock: pick locks so early-game users see a pick
    # - 2pm run override: if new pick has Sweat Score 20+ higher than locked pick,
    #                     overwrite (only trigger when 2pm data changes things materially)
    SCORE_OVERRIDE_THRESHOLD = 20  # 20-point score delta to override locked pick
    existing_pick = None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?game_id=eq.best_bet_{today}&select=data",
            headers=HEADERS
        )
        existing = r.json()
        if existing and len(existing) > 0 and existing[0].get('data', {}).get('pipelineGenerated'):
            existing_pick = existing[0]['data']
            existing_score = existing_pick.get('score', {}).get('total', 0) or 0

            # Manual-override lock — set when POTD is hand-picked (e.g. user
            # already posted to social before pipeline shifted). Wins against
            # all auto-regeneration paths regardless of ET hour.
            if existing_pick.get('manualOverride'):
                print(f"🔒 manualOverride=true — POTD hand-locked, skipping all regeneration")
                return

            if et_hour < 11:
                print(f"⏰ Pre-11am ET ({et_hour}h) — regenerating with fresh data")
                existing_pick = None  # clear so we overwrite
            elif et_hour < 14:
                print(f"✅ Today's pick locked (11am-2pm window) — skipping regeneration")
                return
            else:
                # 2pm+ run: allow override only if new pick beats locked score significantly
                print(f"🔄 2pm+ run — will override locked pick only if new Sweat Score > {existing_score} + {SCORE_OVERRIDE_THRESHOLD}")
    except:
        pass

    # Get all MLB games with context
    mlb_games = get_mlb_games()
    print(f"MLB games: {len(mlb_games)}")

    # Get MLB game times to populate commence_time (also used below for late-slate detection)
    mlb_times = get_mlb_game_times(today)

    # LATE-SLATE DEFER (added 2026-04-29):
    # If no game starts before 4pm ET on this date, defer POTD generation to the
    # 2pm pipeline run. Avoids locking a stale 8am pick on weeknight slates that
    # don't need an early-locked POTD. Weekend slates with 1pm games still get
    # POTD locked at 8am as before.
    if et_hour < 14 and mlb_times:
        try:
            earliest = None
            for ts in mlb_times.values():
                if not ts:
                    continue
                t = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                t_et = t - timedelta(hours=4)
                if earliest is None or t_et < earliest:
                    earliest = t_et
            if earliest is not None:
                # 4pm ET cutoff — anything later = weeknight slate, defer
                cutoff_hour = 16
                if earliest.hour >= cutoff_hour:
                    print(f"⏰ Late-slate detected — earliest game {earliest.strftime('%H:%M')} ET, deferring POTD to 2pm run for fresher data")
                    return
        except Exception as e:
            print(f"  late-slate detect failed (continuing normally): {e}")

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
            'commence_time': mlb_times.get(ctx.get('home_team')),
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
            'projected_spread': ctx.get('projected_spread'),
            'spread_delta': ctx.get('spread_delta'),
            'signal_confluence_net': ctx.get('signal_confluence_net'),
            'signal_confluence_support': ctx.get('signal_confluence_support'),
            'signal_confluence_breakdown': ctx.get('signal_confluence_breakdown'),
            'close_spread': ctx.get('close_spread'),
            'open_spread': ctx.get('open_spread'),
            'home_ml_odds': ctx.get('home_ml_odds'),
            'away_ml_odds': ctx.get('away_ml_odds'),
            'venue': ctx.get('venue'),
            'temperature': ctx.get('temperature'),
        })

    for game in nba_games:
        game_score, nba_lean, nba_lean_type = score_nba_game(game, nba_teams)
        candidates.append({
            'sport': 'NBA',
            'home_team': game.get('home_team'),
            'away_team': game.get('away_team'),
            'score': game_score,
            'nrfi_score': None,
            'is_nrfi': False,
            'lean_display': nba_lean,
            'lean_bet': nba_lean_type or 'ml',
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

    # Sort by score
    candidates.sort(key=lambda c: c['score'], reverse=True)

    # NRFI candidates — only 88-94 range (75% hit rate proven sweet spot)
    sweet_spot = [c for c in candidates if c.get('is_nrfi') and 90 <= (c.get('nrfi_score') or 0) <= 94]
    edge_nrfi = [c for c in candidates if c.get('is_nrfi') and 88 <= (c.get('nrfi_score') or 0) <= 89]

    # v2 Total OVER Edge candidates — backtest 56-34 (62.2%) at delta ≥1.5
    v2_total_edge = [
        c for c in candidates
        if c.get('lean_bet') == 'total'
        and c.get('sport') == 'MLB'
        and 'v2 edge' in (c.get('lean_display') or '').lower()
    ]

    best_overall = candidates[0]

    pick = None
    confidence = 'standard'

    # ML POTD ELIGIBILITY REMOVED 2026-05-01 pending projection_v2 ML rebuild.
    # NBA POTDs still fire (separate model).

    # Tier 1 — sweet spot NRFI (90-94) audited 75% n=16, OR NBA high conviction
    if sweet_spot:
        sweet_spot.sort(key=lambda c: c.get('nrfi_score', 0), reverse=True)
        pick = sweet_spot[0]
        confidence = 'elite'
        print(f"🔒 SWEET SPOT pick: {pick['away_team']} @ {pick['home_team']} — NRFI {pick['nrfi_score']}")
    elif best_overall.get('sport') == 'NBA' and best_overall['score'] >= 75:
        pick = best_overall
        confidence = 'high'
        print(f"🔒 NBA HIGH CONVICTION: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")

    # Tier 1.5 — v2 Total OVER Edge (audited 62.2% n=90 at ≥1.5 run delta)
    if not pick and v2_total_edge:
        v2_total_edge.sort(key=lambda c: c['score'], reverse=True)
        pick = v2_total_edge[0]
        confidence = 'high'
        print(f"🔒 v2 TOTAL OVER EDGE: {pick['away_team']} @ {pick['home_team']} — {pick.get('lean_display')}")

    # Tier 2 — NRFI 88-89 edge OR NBA solid
    if not pick:
        if edge_nrfi:
            edge_nrfi.sort(key=lambda c: c.get('nrfi_score', 0), reverse=True)
            pick = edge_nrfi[0]
            confidence = 'solid'
            print(f"✅ NRFI pick: {pick['away_team']} @ {pick['home_team']} — NRFI {pick['nrfi_score']}")
        elif best_overall.get('sport') == 'NBA' and best_overall['score'] >= 65:
            pick = best_overall
            confidence = 'solid'
            print(f"✅ NBA pick: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")

    # NO TIER 3 FALLBACK — if no NRFI sweet/edge tier or NBA solid, post no
    # POTD. "No play" is honest content; forced picks erode trust faster than
    # silence does.
    if not pick:
        print("🚫 No PRIME tier play on the board — no POTD posted today.")
        # Write a no-play marker so app shows transparent "no lock" message
        try:
            requests.post(
                f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "cache_key": f"best_bet_{today}",
                    "game_id": f"best_bet_{today}",
                    "sport": "none",
                    "narrative": "No PRIME tier play on the board today. The Sweat Locker model only locks NRFI 90-94 sweet-spot games (75% audited). When that doesn't show, we don't force a pick — bucket angles + Dawg of the Day are still in the app.",
                    "data": {"noPlay": True, "reason": "no_prime_tier"},
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as e:
            print(f"  no-play marker write failed: {e}")
        return

    # Print all candidates
    for c in candidates[:5]:
        nrfi_str = f" | NRFI {c['nrfi_score']}" if c.get('nrfi_score') else ''
        print(f"  {c['sport']} {c['away_team']} @ {c['home_team']} — Score {c['score']}{nrfi_str} | Lean: {c.get('lean_display') or 'none'}")

    # 2pm override gate (BUG #2 FIX 2026-04-26): TIER hierarchy beats numeric score.
    # Yesterday: locked Tier 2 (NRFI edge 89, score 78), afternoon found Tier 1
    # (PRIME confluence ML, score 71). Old code blocked override based on score alone,
    # keeping the WEAKER tier locked. Now: a strictly higher tier overrides regardless
    # of score; same-tier override still requires +20 score delta.
    # Tier ranking: 'high' = 1 (HIGH CONVICTION), 'solid' = 2 (NRFI/ML lean/NBA solid),
    #               'standard' = 3 (best available)
    # 'elite' = sweet-spot NRFI 90-94 — proven 78.9% over 352 audited games.
    # 'high' = PRIME confluence ML / NBA high conviction.
    # 'solid' = NRFI edge / ML lean / NBA solid.
    # 'standard' = best available fallback.
    def _new_pick_cohort_healthy(c):
        """Return False if the candidate's auto_fade cohort has a 7d hit
        rate below 0.50 with n>=8. Used as a stickiness gate when the 2pm
        run wants to override the 8am locked pick — don't flip to a cold
        cohort even if its score outranks. NRFI/NBA picks (no spread_delta
        cohort) are always considered healthy."""
        try:
            from auto_fade import adjust_pick, CALIBRATION
            res = adjust_pick(
                c.get('projected_spread'), c.get('close_spread'),
                c.get('signal_confluence_net'),
                c.get('home_team'), c.get('away_team'),
                home_ml=c.get('home_ml_odds'), away_ml=c.get('away_ml_odds'),
            )
            cohort = res.get('cohort')
            if not cohort or cohort not in CALIBRATION:
                return True  # NRFI / NBA / unmapped pick — no cohort gate
            cal = CALIBRATION[cohort]
            n_7d = cal.get('n_7d') or 0
            hit_7d = cal.get('hit_rate_7d')
            if hit_7d is None or n_7d < 8:
                return True  # not enough recent sample to judge
            return hit_7d >= 0.50
        except Exception:
            return True  # fail-open

    TIER_RANK = {'elite': 0, 'high': 1, 'solid': 2, 'standard': 3}
    if existing_pick and et_hour >= 14:
        existing_score = existing_pick.get('score', {}).get('total', 0) or 0
        existing_confidence = existing_pick.get('confidence', 'standard')
        new_score = pick.get('score', 0) or 0
        existing_tier = TIER_RANK.get(existing_confidence, 3)
        new_tier = TIER_RANK.get(confidence, 3)
        # Cohort-health gate for ANY override (tier upgrade or same-tier).
        # Pulls 7d hit rate of the new pick's cohort from mlb_tier_calibration
        # — if cohort is in a 7d slump (rate < 0.50, n>=8), refuse to flip
        # away from the locked morning pick. Stops the 2pm cron from swapping
        # an already-decent pick to a freshly-eligible cold cohort.
        new_cohort_healthy = _new_pick_cohort_healthy(pick)
        if new_tier < existing_tier:
            if not new_cohort_healthy:
                print(f"🔒 Keeping locked pick — new {confidence} pick's cohort in 7d slump (would have upgraded tier)")
                return
            print(f"🔄 TIER UPGRADE OVERRIDE — new pick {confidence} (tier {new_tier}) beats locked {existing_confidence} (tier {existing_tier})")
        elif new_tier > existing_tier:
            print(f"🔒 Keeping locked pick — new pick {confidence} is strictly lower tier than locked {existing_confidence}")
            return
        else:
            if new_score < existing_score + SCORE_OVERRIDE_THRESHOLD:
                print(f"🔒 Keeping locked pick — new score {new_score} doesn't beat locked {existing_score} + {SCORE_OVERRIDE_THRESHOLD} (same tier)")
                return
            if not new_cohort_healthy:
                print(f"🔒 Keeping locked pick — same-tier override blocked, new pick's cohort in 7d slump")
                return
            print(f"🔄 OVERRIDE — same tier, new score {new_score} beats locked {existing_score} + {SCORE_OVERRIDE_THRESHOLD}")

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
        'confidence': confidence,  # high, solid, standard
        # Include context for Jerry narrative generation
        'context': {
            'home_pitcher': pick.get('home_pitcher'),
            'away_pitcher': pick.get('away_pitcher'),
            'home_sp_xera': pick.get('home_sp_xera'),
            'away_sp_xera': pick.get('away_sp_xera'),
            'projected_total': pick.get('projected_total'),
            'spread_delta': pick.get('spread_delta'),
            'projected_spread': pick.get('projected_spread'),
            'lean_bet': pick.get('lean_bet'),
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
