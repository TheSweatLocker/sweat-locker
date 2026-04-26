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

def build_lean(ctx):
    """Determine the lean for an MLB game.

    Priority order (BUG #1 FIX 2026-04-26): PRIME confluence ML beats EDGE NRFI.
    Yesterday's Braves @ Phillies had NRFI 89 + PRIME confluence +5 — old code
    returned NRFI first, blocking the stronger ML pick from Tier 1 HIGH CONVICTION
    selection. Now PRIME confluence ML (>= +4) checked FIRST since multi-signal
    alignment (~71% backtest) is stronger than NRFI 88-89 dead-edge band (~47%).
    """
    nrfi = ctx.get('nrfi_score') or 0
    projected_spread = ctx.get('projected_spread')
    confluence_net = ctx.get('signal_confluence_net')

    # Helper: try ML lean via auto-fade, return (label, type, is_nrfi) or None if suppressed
    def _try_ml_lean(min_confluence):
        if projected_spread is None or confluence_net is None:
            return None
        if int(confluence_net) < min_confluence:
            return None
        try:
            from auto_fade import adjust_pick
            res = adjust_pick(
                projected_spread, ctx.get('close_spread'), confluence_net,
                ctx.get('home_team'), ctx.get('away_team'),
                home_ml=ctx.get('home_ml_odds'), away_ml=ctx.get('away_ml_odds')
            )
            if res['action'] == 'SUPPRESS':
                return None
            if res['action'] in ('SURFACE', 'FADE'):
                fav_team = res['pick_team']
                tier_tag = 'PRIME' if int(confluence_net) >= 4 else 'STRONG'
                tag_extra = ' [auto-fade]' if res['action'] == 'FADE' else ''
                return (f"{fav_team} ML ({tier_tag} {int(confluence_net):+d} signals){tag_extra}", 'ml', False)
        except Exception:
            # Fallback if auto_fade unavailable
            fav_team = ctx.get('home_team') if float(projected_spread) > 0 else ctx.get('away_team')
            tier_tag = 'PRIME' if int(confluence_net) >= 4 else 'STRONG'
            return (f"{fav_team} ML ({tier_tag} {int(confluence_net):+d} signals)", 'ml', False)
        return None

    # PRIORITY 1: PRIME NRFI sweet spot (90-94) — audit 78.9% hit rate (n=19)
    if 90 <= nrfi <= 94:
        return f"NRFI — Score {nrfi}/100 (sweet spot)", 'nrfi', True

    # PRIORITY 2: PRIME confluence ML (>=+4) — backtest 71% (n=7, smaller sample)
    prime_ml = _try_ml_lean(4)
    if prime_ml:
        return prime_ml

    # PRIORITY 3: EDGE NRFI 88-89 — borderline tier, ~47% audit hit rate
    if 88 <= nrfi <= 89:
        return f"NRFI — Score {nrfi}/100 (edge tier)", 'nrfi', True

    # PRIORITY 4: STRONG confluence ML (+2 or +3) — ~55% backtest
    strong_ml = _try_ml_lean(2)
    if strong_ml:
        return strong_ml

    # Total lean
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

    # Get NBA data
    nba_teams = get_nba_teams()
    nba_games = get_nba_games()
    print(f"NBA games: {len(nba_games)}, teams: {len(nba_teams)}")

    # Get MLB game times to populate commence_time
    mlb_times = get_mlb_game_times(today)

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

    # NRFI candidates — only 88-94 range (77% hit rate proven sweet spot)
    # 95+ excluded from NRFI POTD — historically volatile (38.5%)
    sweet_spot = [c for c in candidates if c.get('is_nrfi') and 90 <= (c.get('nrfi_score') or 0) <= 94]
    edge_nrfi = [c for c in candidates if c.get('is_nrfi') and 88 <= (c.get('nrfi_score') or 0) <= 89]

    # ML lean candidates — games with 2+ run spread delta
    ml_candidates = [c for c in candidates if c.get('lean_bet') == 'ml' and c.get('sport') == 'MLB']

    best_overall = candidates[0]

    pick = None
    confidence = 'standard'

    # Tier 1 — high conviction = SIGNAL CONFLUENCE PRIME tier + AUTO-FADE filter.
    # Backtest: net confluence >= +4 hit 71% (n=7) over April 10-23 — true PRIME tier.
    # Auto-fade additionally filters out cohorts in losing buckets or uncalibrated.
    def _has_pitcher_data(c):
        return c.get('home_sp_xera') is not None and c.get('away_sp_xera') is not None
    def _passes_auto_fade(c):
        try:
            from auto_fade import adjust_pick
            res = adjust_pick(
                c.get('projected_spread'), c.get('close_spread'),
                c.get('signal_confluence_net'),
                c.get('home_team'), c.get('away_team'),
                home_ml=c.get('home_ml_odds'), away_ml=c.get('away_ml_odds'),
            )
            return res['action'] != 'SUPPRESS'
        except Exception:
            return True  # fail-open if auto_fade unavailable
    ml_high_conviction = [
        c for c in ml_candidates
        if c.get('signal_confluence_net') is not None
        and int(c['signal_confluence_net']) >= 4
        and _has_pitcher_data(c)
        and _passes_auto_fade(c)
    ]
    if ml_high_conviction:
        # Sort by confluence net (descending) — most signals stacking wins
        ml_high_conviction.sort(key=lambda c: int(c.get('signal_confluence_net') or 0), reverse=True)
        pick = ml_high_conviction[0]
        confidence = 'high'
        print(f"🔒 ML HIGH CONVICTION (PRIME confluence): {pick['away_team']} @ {pick['home_team']} — net {int(pick.get('signal_confluence_net') or 0):+d} signals, delta {float(pick.get('spread_delta') or 0):+.1f}")
    elif sweet_spot:
        sweet_spot.sort(key=lambda c: c.get('nrfi_score', 0), reverse=True)
        pick = sweet_spot[0]
        confidence = 'high'
        print(f"🔒 SWEET SPOT pick: {pick['away_team']} @ {pick['home_team']} — NRFI {pick['nrfi_score']}")
    elif best_overall.get('sport') == 'NBA' and best_overall['score'] >= 75:
        pick = best_overall
        confidence = 'high'
        print(f"🔒 NBA HIGH CONVICTION: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")

    # Tier 2 — solid
    # NRFI 88-89, or ML lean with big spread delta, or NBA 65+
    if not pick:
        if edge_nrfi:
            edge_nrfi.sort(key=lambda c: c.get('nrfi_score', 0), reverse=True)
            pick = edge_nrfi[0]
            confidence = 'solid'
            print(f"✅ NRFI pick: {pick['away_team']} @ {pick['home_team']} — NRFI {pick['nrfi_score']}")
        elif ml_candidates:
            ml_candidates.sort(key=lambda c: c['score'], reverse=True)
            pick = ml_candidates[0]
            confidence = 'solid'
            print(f"✅ ML lean pick: {pick['away_team']} @ {pick['home_team']} — {pick.get('lean_display')}")
        elif best_overall.get('sport') == 'NBA' and best_overall['score'] >= 65:
            pick = best_overall
            confidence = 'solid'
            print(f"✅ NBA pick: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")

    # Tier 3 — best available (always fires on a full slate)
    if not pick:
        pick = best_overall
        confidence = 'standard'
        print(f"🎯 Best available: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']} ({pick['sport']})")

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
    TIER_RANK = {'high': 1, 'solid': 2, 'standard': 3}
    if existing_pick and et_hour >= 14:
        existing_score = existing_pick.get('score', {}).get('total', 0) or 0
        existing_confidence = existing_pick.get('confidence', 'standard')
        new_score = pick.get('score', 0) or 0
        existing_tier = TIER_RANK.get(existing_confidence, 3)
        new_tier = TIER_RANK.get(confidence, 3)
        if new_tier < existing_tier:
            # Strictly higher tier (lower rank number) — override regardless of score
            print(f"🔄 TIER UPGRADE OVERRIDE — new pick {confidence} (tier {new_tier}) beats locked {existing_confidence} (tier {existing_tier}) regardless of score")
        elif new_tier > existing_tier:
            # Strictly lower tier — never override even if score is higher
            print(f"🔒 Keeping locked pick — new pick {confidence} is strictly lower tier than locked {existing_confidence}")
            return
        else:
            # Same tier — score delta rule applies
            if new_score < existing_score + SCORE_OVERRIDE_THRESHOLD:
                print(f"🔒 Keeping locked pick — new score {new_score} doesn't beat locked {existing_score} + {SCORE_OVERRIDE_THRESHOLD} (same tier)")
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
