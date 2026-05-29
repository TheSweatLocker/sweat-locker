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

def sweat_tier_for(score, ctx=None):
    """Tier thresholds aligned with app/index.tsx getSweatTier (line ~2497).
    Both server and app now use the same 4-tier system: PRIME/STRONG/LIGHT_LEAN/PASS.

    Playability gate (2026-05-18): a game cannot reach PRIME tier without a
    qualifying primary_play. Before this gate, games with high confluence +
    big xERA gap but no actionable lean (spread_delta below ML threshold)
    were ranking PRIME on the home-screen sweat card with no specific bet
    to display — confusing UX. Cap at STRONG when there's no primary_play."""
    if score is None:
        return None
    if score >= 80:
        # PRIME requires an actionable lean — otherwise the card shows a
        # giant "high data interest" tile with no specific bet.
        if ctx is not None and not ctx.get('primary_play'):
            return 'STRONG'
        return 'PRIME'
    if score >= 65:
        return 'STRONG'
    if score >= 50:
        return 'LIGHT_LEAN'
    return 'PASS'


def write_sweat_score(ctx, score, tier, breakdown=None):
    """Write the score + tier back to mlb_game_context so the app reads the
    same number the server computed. Eliminates the client/server drift that
    made PRIME (68+) effectively invisible in-app (client formula was topping
    out ~65 even when the server said 72).

    2026-05-25: also writes sweat_breakdown (JSONB with contributions+evidence)
    when supplied — feeds the WHY THIS SCORE UI block to achieve parity with
    NBA. Requires 20260525_sweat_breakdown.sql migration to have been applied;
    until then the breakdown field is silently dropped by PostgREST.

    2026-05-29: cap the displayed sweat_score at 79 when primary_play is None.
    Trigger — 5/29 SF@COL showed sweat 93 but rendered as STRONG (tier already
    capped from PRIME because no primary_play met thresholds). User-confusing:
    the 93 number suggested PRIME, the tier said STRONG. Cap aligns the two:
    sweat ≥ 80 only when an actionable PRIME bet exists.
    Raw composite score preserved in breakdown.sweat_score_raw for audit.
    """
    game_id = ctx.get('game_id')
    if not game_id:
        return
    # Cap score to 79 when no primary_play. Tier should already be capped
    # to STRONG via sweat_tier_for, but the score itself wasn't aligned.
    displayed_score = int(score)
    if displayed_score >= 80 and not ctx.get('primary_play'):
        if isinstance(breakdown, dict):
            breakdown.setdefault('sweat_score_raw', displayed_score)
            breakdown.setdefault('cap_reason', 'no_primary_play')
        displayed_score = 79
    payload = {'sweat_score': displayed_score, 'sweat_tier': tier}
    if breakdown is not None:
        payload['sweat_breakdown'] = breakdown
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_id=eq.{game_id}&game_date=eq.{ctx.get('game_date')}",
            headers={**HEADERS, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
            json=payload,
            timeout=10,
        )
        # If the breakdown column doesn't exist yet (migration not applied),
        # retry without it so sweat_score/tier still land.
        if r.status_code == 400 and breakdown is not None:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_id=eq.{game_id}&game_date=eq.{ctx.get('game_date')}",
                headers={**HEADERS, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                json={'sweat_score': int(score), 'sweat_tier': tier},
                timeout=10,
            )
    except Exception as e:
        print(f"  ⚠️ sweat_score writeback failed for {game_id}: {e}")


def score_mlb_game(ctx, game_props=None, track=None):
    """Score an MLB game's overall sweat heat.

    Rewritten 2026-05-16. Previous formula clustered most games at 42-45 PASS
    because base 30 + small bonuses couldn't stack high enough on a single
    NRFI / xERA signal. New formula adds confluence, 1st-inn extremes, mastery,
    and PRIME-prop-stack bonuses so a game's heat reflects what the app
    actually surfaces.

    Target distribution per slate:
      ~1-3 PRIME  (≥80) — POTD + stack-alert games
      ~3-6 STRONG (65-79) — confluence + DOD + multi-PRIME-prop games
      ~4-7 LIGHT_LEAN (50-64) — single-PRIME-prop or one-signal games
      ~1-3 PASS   (<50) — no edges

    2026-05-25: added optional `track` parameter for the WHY THIS SCORE
    UI parity work. If a dict is passed (with empty 'contributions' and
    'evidence' lists), the scorer appends signal entries inline as it
    scores. Backward-compatible default (None) means no tracking.
    """
    score = 30  # base
    # Contributions/evidence tracking — local closures avoid threading lists
    # through every branch. When track is None, the helpers are no-ops.
    def _contrib(emoji, label, points, detail=None):
        if track is not None and points > 0:
            entry = {'emoji': emoji, 'label': label, 'points': points}
            if detail:
                entry['detail'] = detail
            track.setdefault('contributions', []).append(entry)

    def _evidence(emoji, label, detail=None):
        if track is not None:
            entry = {'emoji': emoji, 'label': label}
            if detail:
                entry['detail'] = detail
            track.setdefault('evidence', []).append(entry)

    # ---- NRFI band (audit-calibrated) ----
    nrfi = ctx.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        score += 30    # PRIME band, audit 71.4% n=28
        _contrib('⚾', 'NRFI sweet spot', 30, f'Score {int(nrfi)}/100 — audit 71.4% (n=28)')
    elif 88 <= nrfi <= 89:
        score += 22
        _contrib('⚾', 'NRFI edge tier', 22, f'Score {int(nrfi)}/100')
    elif nrfi >= 95:
        score += 12    # volatile/trap zone, audit 47.8% — still some heat
        _contrib('⚠️', 'NRFI volatile (95+)', 12, f'Score {int(nrfi)}/100 — coin-flip cohort')
    elif 80 <= nrfi <= 89:
        score += 14    # lean band
        _contrib('⚾', 'NRFI lean band', 14, f'Score {int(nrfi)}/100')
    elif 70 <= nrfi <= 79:
        score += 10    # lean band, audit 56.7%
        _contrib('⚾', 'NRFI lean', 10, f'Score {int(nrfi)}/100 — audit 56.7%')
    elif nrfi <= 30:
        _h1 = float(ctx.get('home_first_inning_era') or 4.5)
        _a1 = float(ctx.get('away_first_inning_era') or 4.5)
        _max_fi = max(_h1, _a1)
        if 6.0 <= _max_fi < 8.0:
            score += 14    # YRFI sweet-spot fragility (audit ~63%)
            _contrib('🔥', 'YRFI sweet spot', 14, f'NRFI {int(nrfi)} + 1st-inn ERA {_max_fi:.1f} (audit ~63%)')
        else:
            score += 4     # small-sample noise — don't drive sweat to PRIME
    elif nrfi <= 40:
        score += 8
        _contrib('🔥', 'YRFI lean', 8, f'NRFI score {int(nrfi)}/100')

    # ---- Pitcher xERA mismatch ----
    home_xera = float(ctx.get('home_sp_xera') or 4.5)
    away_xera = float(ctx.get('away_sp_xera') or 4.5)
    xera_gap = abs(home_xera - away_xera)
    if xera_gap >= 2.0:
        score += 14
        _contrib('⚖️', 'Major xERA gap', 14, f'{abs(home_xera-away_xera):.2f}-run pitcher mismatch')
    elif xera_gap >= 1.5:
        score += 9
        _contrib('⚖️', 'xERA gap', 9, f'{xera_gap:.2f}-run pitcher mismatch')
    elif xera_gap >= 1.0:
        score += 6
        _contrib('⚖️', 'xERA gap', 6, f'{xera_gap:.2f}-run pitcher mismatch')
    elif xera_gap >= 0.5:
        score += 3

    # ---- Both pitchers elite (ace duel) ----
    if home_xera <= 3.0 and away_xera <= 3.0:
        score += 10
        _contrib('🎯', 'Ace duel', 10, f'Both starters ≤3.00 xERA')
    elif home_xera <= 3.5 and away_xera <= 3.5:
        score += 5
        _contrib('🎯', 'Quality matchup', 5, 'Both starters ≤3.50 xERA')

    # ---- 1st-inning extremes (NRFI lock or YRFI fade) ----
    h1 = float(ctx.get('home_first_inning_era') or 4.5)
    a1 = float(ctx.get('away_first_inning_era') or 4.5)
    # Extreme fragility (≥8 ERA) is small-sample noise per 5/18 audit
    # — fragile starter bonus only applies in 6.0-7.9 sweet spot
    if 6.0 <= max(h1, a1) < 8.0:
        score += 8     # fragile starter sweet spot
    elif 8.0 <= max(h1, a1):
        score += 2     # high but noisy
    elif h1 >= 6.0 or a1 >= 6.0:
        score += 5
    if h1 <= 1.5 and a1 <= 1.5:
        score += 6     # mutual NRFI lock
    elif h1 <= 1.5 or a1 <= 1.5:
        score += 3

    # ---- Signal confluence (strongest single side indicator) ----
    conf_net = ctx.get('signal_confluence_net')
    try:
        conf_mag = abs(int(conf_net)) if conf_net is not None else 0
    except (TypeError, ValueError):
        conf_mag = 0
    if conf_mag >= 5:
        score += 14    # PRIME confluence — multi-signal stacking
        _contrib('🎯', 'PRIME confluence', 14, f'{conf_mag} independent signals align')
    elif conf_mag >= 4:
        score += 10
        _contrib('🎯', 'Strong confluence', 10, f'{conf_mag} signals on one side')
    elif conf_mag >= 3:
        score += 6
        _contrib('🎯', 'Confluence edge', 6, f'{conf_mag} signals on one side')
    elif conf_mag >= 2:
        score += 3

    # ---- Spread delta (market disagreement) ----
    # Audit (2026-05-21, see project_spread_delta_trap_zone) found the U-shape:
    #   <1.0:    43-50% (coinflip / noise)
    #   1.0-1.5: 55-58% (sweet spot — small edge cashes)
    #   1.5-2.0: 40-43% (TRAP — model's pick LOSES more than wins)
    #   ≥2.0:    55-58% (real conviction)
    # Pre-2026-05-25 scoring rewarded the trap zone with +9 points — almost
    # as much as the genuine ≥2.0 conviction band. New scoring matches the
    # cohort curve: trap zone gets ZERO, 1.0-1.5 sweet spot gets bumped up.
    spread_delta = abs(float(ctx.get('spread_delta') or 0))
    if spread_delta >= 2.0:
        score += 13   # genuine conviction (55-58%)
        _contrib('📊', 'Market disagreement', 13, f'{spread_delta:.1f}-run spread delta — model fades market')
    elif spread_delta >= 1.5:
        score += 0    # trap zone — no boost
    elif spread_delta >= 1.0:
        score += 8    # sweet spot (55-58%) — bumped from 6 to recognize edge
        _contrib('📊', 'Spread delta edge', 8, f'{spread_delta:.1f}-run model edge vs market')
    elif spread_delta >= 0.5:
        score += 3

    # ---- Total model vs market disagreement ----
    proj_total = float(ctx.get('projected_total') or 0)
    close_total = float(ctx.get('close_total') or ctx.get('open_total') or 0)
    if proj_total > 0 and close_total > 0:
        total_delta = abs(proj_total - close_total)
        if total_delta >= 2.0:
            score += 9
        elif total_delta >= 1.5:
            score += 6
        elif total_delta >= 1.0:
            score += 4

    # ---- K gap ----
    home_k_gap = abs(float(ctx.get('home_k_gap') or 0))
    away_k_gap = abs(float(ctx.get('away_k_gap') or 0))
    k_gap = max(home_k_gap, away_k_gap)
    if k_gap >= 12:
        score += 6
    elif k_gap >= 8:
        score += 3

    # ---- Pitcher mastery / anti-mastery vs current opp ----
    for vt_key in ('home_pitcher_vs_team_era', 'away_pitcher_vs_team_era'):
        v = ctx.get(vt_key)
        if v is None:
            continue
        try:
            vt = float(v)
            side = 'Home' if vt_key.startswith('home') else 'Away'
            if vt <= 2.5:
                score += 5
                _contrib('⚾', f'{side} pitcher mastery vs opp', 5, f'{vt:.2f} ERA career vs this team')
            elif vt >= 7.0:
                score += 5
                _contrib('🚨', f'{side} pitcher tagged by opp', 5, f'{vt:.2f} ERA career vs this team')
            elif vt <= 3.0:
                score += 3
                _evidence('⚾', f'{side} pitcher edge vs opp', f'{vt:.2f} ERA career vs this team')
            elif vt >= 6.0:
                score += 3
                _evidence('🚨', f'{side} pitcher struggles vs opp', f'{vt:.2f} ERA career vs this team')
        except (TypeError, ValueError):
            pass

    # ---- Park + weather extremes ----
    park = float(ctx.get('park_run_factor') or 100)
    if park >= 110:
        score += 4
        _evidence('🏟', 'Hitter-friendly park', f'Park factor {park:.0f}')
    elif park <= 92:
        score += 4
        _evidence('🏟', 'Pitcher-friendly park', f'Park factor {park:.0f}')
    temp = float(ctx.get('temperature') or 70)
    if temp <= 45:
        score += 3
        _evidence('❄️', 'Cold weather', f'{int(temp)}°F suppresses scoring')
    wind = float(ctx.get('wind_speed') or 0)
    if wind >= 18:
        score += 3
        _evidence('💨', 'High wind', f'{int(wind)} mph affecting flight')

    # ---- Prop stack (game contains high-conviction props) ----
    game_props = game_props or []
    prime_props = [p for p in game_props if p.get('tier') == 'PRIME']
    strong_props = [p for p in game_props if p.get('tier') == 'STRONG']
    if len(prime_props) >= 4:
        score += 20
        _contrib('🔥', 'PRIME prop stack', 20, f'{len(prime_props)} PRIME props in this game')
    elif len(prime_props) >= 2:
        score += 11
        _contrib('🔥', 'Multiple PRIME props', 11, f'{len(prime_props)} PRIME props in this game')
    elif len(prime_props) == 1:
        score += 6
        _contrib('🔥', 'PRIME prop available', 6, '1 PRIME prop in this game')
    elif len(strong_props) >= 3:
        score += 5
        _contrib('💪', 'STRONG prop cluster', 5, f'{len(strong_props)} STRONG props')

    # ---- Sort contributions by points (biggest drivers first) ----
    if track is not None and track.get('contributions'):
        track['contributions'].sort(key=lambda c: -c.get('points', 0))
        # Cap at top 6 to keep the UI section focused
        track['contributions'] = track['contributions'][:6]
    if track is not None and track.get('evidence'):
        # Cap evidence at 5 items
        track['evidence'] = track['evidence'][:5]

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


_UMP_CACHE = {}


def _get_ump_total_signal(umpire_name, lean_side):
    """Look up ump's over_rate and return cohort-based signal for total picks.

    Audit cohort (2026-05-10) showed:
      total_over × ump_over_hostile (over_rate ≤ 0.45): 20% OVER (n=15) — strong fade
      total_over × ump_over_friendly (over_rate ≥ 0.55): 56% OVER (n=114) — confirm

    Returns dict: {'action': 'suppress'|'confirm'|'neutral', 'note': str, 'over_rate': float}
    """
    if not umpire_name:
        return {'action': 'neutral', 'note': '', 'over_rate': None}
    if 'ump_map' not in _UMP_CACHE:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_umpires?select=ump_name,over_rate,games_sampled",
                headers=HEADERS, timeout=10,
            )
            rows = r.json() or []
            _UMP_CACHE['ump_map'] = {row['ump_name'].lower(): row for row in rows if row.get('ump_name')}
        except Exception:
            _UMP_CACHE['ump_map'] = {}
    ump = _UMP_CACHE['ump_map'].get((umpire_name or '').strip().lower())
    if not ump or ump.get('over_rate') is None:
        return {'action': 'neutral', 'note': '', 'over_rate': None}
    try:
        over_rate = float(ump['over_rate'])
        n = ump.get('games_sampled') or 0
    except (TypeError, ValueError):
        return {'action': 'neutral', 'note': '', 'over_rate': None}
    # Require minimum sample to act on ump signal
    if n < 30:
        return {'action': 'neutral', 'note': '', 'over_rate': over_rate}
    # OVER lean: ump under-friendly fades it, ump over-friendly confirms
    if lean_side == 'over':
        if over_rate <= 0.45:
            return {'action': 'suppress', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} under-friendly ({over_rate:.2f} over rate, audit: 20% OVER hits) — suppressed'}
        if over_rate >= 0.55:
            return {'action': 'confirm', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} over-friendly ({over_rate:.2f}) confirms'}
    # UNDER lean: ump over-friendly fades it, ump under-friendly confirms
    elif lean_side == 'under':
        if over_rate >= 0.55:
            return {'action': 'suppress', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} over-friendly ({over_rate:.2f}) — UNDER suppressed'}
        if over_rate <= 0.45:
            return {'action': 'confirm', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} under-friendly ({over_rate:.2f}) confirms UNDER'}
    return {'action': 'neutral', 'note': '', 'over_rate': over_rate}


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
        # Umpire-aware filter (added 2026-05-10): suppress OVER picks when
        # ump cohort strongly opposes (under-friendly with n≥30), append
        # confirmation note when ump supports.
        ump_sig = _get_ump_total_signal(ctx.get('umpire'), 'over')
        if ump_sig['action'] == 'suppress':
            print(f"  🚫 v2 OVER suppressed by ump filter: {ump_sig['note']}")
            return None
        suffix = f" • {ump_sig['note']}" if ump_sig['action'] == 'confirm' else ''
        return (
            f"Over {close_total} (v2 edge — model {proj.model_total:.1f} vs market {close_total}, +{delta:.1f} runs){suffix}",
            'total',
            False,
            proj,
        )
    if delta <= -1.5 and proj.confidence >= 0.7:
        ump_sig = _get_ump_total_signal(ctx.get('umpire'), 'under')
        if ump_sig['action'] == 'suppress':
            print(f"  🚫 v2 UNDER suppressed by ump filter: {ump_sig['note']}")
            return None
        suffix = f" • {ump_sig['note']}" if ump_sig['action'] == 'confirm' else ''
        return (
            f"Under {close_total} (v2 edge — model {proj.model_total:.1f} vs market {close_total}, {delta:.1f} runs){suffix}",
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

    Threshold logic (updated 2026-05-21 — see project_spread_delta_trap_zone):
      - confluence_net ≥ +4 (PRIME)
      - |projected_spread| ≥ 2.0 (cohort cliff — 1.5-2.0 is trap zone at 40%)
      - home_ml ≤ -180 OR away_ml ≤ -180 on the favored side
      - direction matches confluence model_pick

    Audit cohort spread_delta_ge2 hits 55% historically — RL covers correlate
    with that signal. Confluence PRIME tier hits 70.6% on direction.
    Combined effect should be net positive at the better RL price (+130-150).
    """
    confluence_net = ctx.get('signal_confluence_net') or 0
    # Prefer v4 (model_pred_spread) over v3 (projected_spread). Sign
    # convention: POSITIVE = home favored (opposite of close_spread).
    # Mirrors the v4-aware fix in game_context.compute_primary_play
    # (2026-05-20 audit). Falls back to v3 when v4 is suppressed.
    projected_spread = ctx.get('model_pred_spread') or ctx.get('projected_spread')
    home_ml = ctx.get('home_ml_close') or ctx.get('home_ml_open')
    away_ml = ctx.get('away_ml_close') or ctx.get('away_ml_open')
    if confluence_net is None or projected_spread is None:
        return None
    try:
        cn = int(confluence_net)
        ps = float(projected_spread)
    except (TypeError, ValueError):
        return None
    if cn < 4 or abs(ps) < 2.0:
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

    # 4. Total lean — prefer v4 (model_pred_total) edge against the line.
    # 2026-05-20 audit: prior path only read v3-derived over_lean which
    # missed v4-driven edges entirely. Conservative threshold ≥2.5 matches
    # compute_primary_play (v4-OVER cohort audit pending). Falls back to
    # v3 over_lean when v4 is suppressed.
    ct = ctx.get('close_total') or ctx.get('open_total')
    v4_total = ctx.get('model_pred_total')
    if v4_total is not None and ct is not None:
        try:
            v4_delta = float(v4_total) - float(ct)
        except (TypeError, ValueError):
            v4_delta = None
        if v4_delta is not None and abs(v4_delta) >= 2.5:
            side = 'Over' if v4_delta > 0 else 'Under'
            return f"{side} {ct}", 'total', False
    over_lean = ctx.get('over_lean')
    if over_lean is not None and ct:
        side = 'Over' if over_lean else 'Under'
        return f"{side} {ct}", 'total', False

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

    # LATE-SLATE DEFER (added 2026-04-29, narrowed 2026-05-19):
    # If no game starts before 4pm ET on this date, defer the POTD *selection*
    # to the 2pm pipeline run. Per-game sweat scores still get written so the
    # app's home-screen MLB tab has fresh tier data in the morning.
    # Before 5/19: this used to `return` early, leaving every game's
    # sweat_score = null and forcing the app into client-side fallback
    # (showed weird 40s-50s scores from outdated formula).
    defer_potd = False
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
                # 4pm ET cutoff — anything later = weeknight slate, defer POTD
                cutoff_hour = 16
                if earliest.hour >= cutoff_hour:
                    print(f"⏰ Late-slate detected — earliest game {earliest.strftime('%H:%M')} ET, deferring POTD selection to 2pm run (sweat scores still being written)")
                    defer_potd = True
        except Exception as e:
            print(f"  late-slate detect failed (continuing normally): {e}")

    # Get NBA data
    nba_teams = get_nba_teams()
    nba_games = get_nba_games()
    print(f"NBA games: {len(nba_games)}, teams: {len(nba_teams)}")

    # Pre-fetch today's pipeline props so score_mlb_game can factor in
    # stack alerts + PRIME prop count (added 2026-05-16 — sweat_score now
    # reflects what the app actually surfaces).
    props_by_game = {}
    try:
        pr = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{today}&select=game_id,tier,conviction",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=15,
        )
        for p in (pr.json() or []):
            gid = p.get('game_id')
            if gid:
                props_by_game.setdefault(gid, []).append(p)
        print(f"  Loaded props for {len(props_by_game)} games (sweat-score stack signal)")
    except Exception as e:
        print(f"  ⚠️ prop fetch for sweat-score failed: {e}")

    # Score all candidates
    candidates = []

    for ctx in mlb_games:
        gid = ctx.get('game_id')
        # Track contributions/evidence for the WHY THIS SCORE UI block
        # (2026-05-25 — Stage 2 of the game-detail parity work).
        track = {'contributions': [], 'evidence': []}
        game_score = score_mlb_game(ctx, game_props=props_by_game.get(gid, []), track=track)
        # Write the score + tier back to mlb_game_context so the app reads
        # the server-authoritative value (instead of computing its own with
        # a different formula that systematically under-reports PRIME).
        breakdown = None
        if track['contributions'] or track['evidence']:
            breakdown = {'contributions': track['contributions'], 'evidence': track['evidence']}
        write_sweat_score(ctx, game_score, sweat_tier_for(game_score, ctx), breakdown=breakdown)
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
            # Downstream cohort math expects close_spread; fall back to open
            # so manual noon runs (close_* still null) don't break candidate
            # eval. Same fallback pattern used in compute_primary_play.
            'close_spread': ctx.get('close_spread') or ctx.get('open_spread'),
            'open_spread': ctx.get('open_spread'),
            'home_ml_odds': ctx.get('home_ml_odds') or ctx.get('home_ml_close') or ctx.get('home_ml_open'),
            'away_ml_odds': ctx.get('away_ml_odds') or ctx.get('away_ml_close') or ctx.get('away_ml_open'),
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

    # Late-slate POTD defer — sweat scores are already written above; just
    # skip the POTD lock/selection logic until the 2pm run.
    if defer_potd:
        print(f"  ✓ Wrote sweat scores for {len(candidates)} games. POTD selection deferred to 2pm run.")
        return

    # ── AUDIT-DRIVEN POTD SELECTION (2026-05-19 rewrite) ──
    # Replaces hardcoded Tier 1/1.5/2 ordering. For each candidate, look up
    # the live 30d hit rate from mlb_tier_calibration. Only candidates from
    # cohorts that ACTUALLY audit above the break-even threshold (and have
    # enough sample size to trust) make the pool. Top by audit rate wins.
    #
    # Why: tier-based selection was hardcoding the OVER v2 Edge tier above
    # NRFI 88-89 edge based on a backtest claim of 62.2% — live POTD
    # outcomes on that bucket showed 50% (n=6), basically coin flip. The
    # self-calibrating version drops anything below MIN_AUDIT_RATE and
    # promotes whatever's actually hitting in production right now.
    #
    # When NO candidate clears the bar → no-play day (honest content beats
    # forced picks). Same skip-day design as before.
    MIN_AUDIT_RATE = 0.58  # need to beat -135 ML break-even to lock as POTD
    MIN_SAMPLE_SIZE = 10   # below this, can't trust the rate

    _cohort_cache = {}
    def _cohort_rate(cohort_key):
        """Pull latest 30d hit rate for the cohort from mlb_tier_calibration.
        Cached per-run. Returns (rate, n) or (None, 0) if cohort isn't
        calibrated yet."""
        if not cohort_key:
            return (None, 0)
        if cohort_key in _cohort_cache:
            return _cohort_cache[cohort_key]
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration",
                params={
                    'tier': f'eq.{cohort_key}',
                    'window_label': 'eq.30d',
                    'select': 'hit_rate,total,computed_date',
                    'order': 'computed_date.desc',
                    'limit': '1',
                },
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
                timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []
            if rows:
                result = (float(rows[0].get('hit_rate') or 0), int(rows[0].get('total') or 0))
                _cohort_cache[cohort_key] = result
                return result
        except Exception as e:
            print(f"  ⚠️ cohort rate lookup failed for {cohort_key}: {e}")
        _cohort_cache[cohort_key] = (None, 0)
        return _cohort_cache[cohort_key]

    def _derive_cohort(c):
        """Map a candidate to its calibration cohort key. Returns None if
        the candidate doesn't have a calibrated cohort (will be filtered)."""
        if c.get('is_nrfi'):
            n = c.get('nrfi_score') or 0
            if 90 <= n <= 94: return 'nrfi_prime_90_94'
            if 88 <= n <= 89: return 'nrfi_dead_80_89'
            if 70 <= n <= 79: return 'nrfi_lean_70_79'
            if n >= 95:        return 'nrfi_volatile_95plus'
            if n <= 25:        return 'yrfi_lean_le40'
            return None
        if c.get('lean_bet') == 'ml':
            # Check the more-specific autofade cohort first. Per audit
            # 2026-05-21 (see project_spread_delta_trap_zone), when the model
            # picks a DOG with high conviction (cn>=2 + market disagrees),
            # the autofade_dog_high_conv cohort hits 58-65% live. That beats
            # confluence_prime_ge4 in real outcomes and deserves promotion.
            # If the candidate doesn't fit autofade_dog_high_conv, fall back
            # to confluence_prime_ge4 (existing strict gate).
            try:
                from auto_fade import cohort_for_pick
                ml_cohort = cohort_for_pick(
                    c.get('projected_spread'),
                    c.get('close_spread'),
                    c.get('signal_confluence_net'),
                    home_ml=c.get('home_ml_odds'),
                    away_ml=c.get('away_ml_odds'),
                )
                if ml_cohort == 'ml_dog_high_conv':
                    return 'autofade_dog_high_conv'
            except Exception:
                pass
            return 'confluence_prime_ge4'
        # v2 Total OVER/UNDER edge — no calibrated cohort yet. Spread_delta_ge2
        # is the closest proxy but it audits in the low-50s, won't clear
        # MIN_AUDIT_RATE. Dropping these from POTD eligibility until a
        # dedicated cohort accumulates.
        return None

    # Build audit-validated candidate pool
    audit_pool = []
    audit_log = []
    for c in candidates:
        if c.get('sport') != 'MLB':
            continue  # NBA + other sports handled below
        cohort = _derive_cohort(c)
        if not cohort:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']}: no calibrated cohort")
            continue
        rate, n = _cohort_rate(cohort)
        if rate is None:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): no calibration data")
            continue
        if n < MIN_SAMPLE_SIZE:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): n={n} below min {MIN_SAMPLE_SIZE}")
            continue
        if rate < MIN_AUDIT_RATE:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): audit {rate*100:.1f}% below threshold {MIN_AUDIT_RATE*100:.0f}%")
            continue
        c['_cohort'] = cohort
        c['_audit_rate'] = rate
        c['_audit_n'] = n
        audit_pool.append(c)

    # Sort: highest audit rate first, then in-game signal strength (sweat score)
    audit_pool.sort(key=lambda c: (-c['_audit_rate'], -c.get('score', 0)))

    if audit_log:
        print("AUDIT-DRIVEN POTD FILTER:")
        for line in audit_log:
            print(line)

    pick = None
    confidence = 'standard'
    best_overall = candidates[0] if candidates else None

    if audit_pool:
        pick = audit_pool[0]
        # Tier label from audit rate (used by app for visual styling)
        r = pick['_audit_rate']
        if r >= 0.68: confidence = 'elite'
        elif r >= 0.62: confidence = 'high'
        else: confidence = 'solid'
        print(f"🔒 AUDIT-DRIVEN PICK: {pick['away_team']} @ {pick['home_team']} — "
              f"cohort {pick['_cohort']} hit {pick['_audit_rate']*100:.1f}% (n={pick['_audit_n']})")
        if len(audit_pool) > 1:
            print(f"   Runners-up:")
            for c in audit_pool[1:4]:
                print(f"     {c['away_team']} @ {c['home_team']} ({c['_cohort']}: {c['_audit_rate']*100:.1f}% n={c['_audit_n']})")

    # NBA fallback path (separate cohort calibration not yet wired into
    # mlb_tier_calibration — preserve the legacy score-based gate).
    if not pick and best_overall and best_overall.get('sport') == 'NBA':
        if best_overall['score'] >= 75:
            pick = best_overall
            confidence = 'high'
            print(f"🔒 NBA HIGH CONVICTION: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")
        elif best_overall['score'] >= 65:
            pick = best_overall
            confidence = 'solid'
            print(f"✅ NBA pick: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")

    # VALUE TIER FALLBACK (2026-05-23): when no audit-qualified cohort fires,
    # surface the model's strongest model-supported lean rather than skipping.
    # Prior design ("no-play day" preserves trust) was correct in principle but
    # produced empty POTD slots that propagated as placeholder rows in the
    # sweat card top_8 — worse UX than a clearly-labeled sub-audit pick.
    #
    # Rules for value pick:
    #   - Must have a computed lean_display (build_lean returned a side)
    #   - Sort by composite of |signal_confluence_net| + score
    #   - Confidence tag 'value' so app can style it softer than audit-locked
    #   - Narrative explicitly labels it Model Lean, not audit-qualified
    if not pick:
        value_pool = [
            c for c in candidates
            if c.get('sport') == 'MLB'
            and c.get('lean_display')
            and (c.get('score') or 0) >= 50
        ]
        # Composite rank: confluence magnitude (most predictive single signal)
        # tie-broken by sweat score
        value_pool.sort(key=lambda c: (
            -abs(c.get('signal_confluence_net') or 0),
            -(c.get('score') or 0),
        ))
        if value_pool:
            pick = value_pool[0]
            confidence = 'value'
            conf_net = pick.get('signal_confluence_net') or 0
            print(f"📌 VALUE POTD (sub-audit fallback): {pick['away_team']} @ {pick['home_team']} — "
                  f"{pick.get('lean_display')} | confluence={conf_net:+d} | sweat={pick.get('score')}")

    if not pick:
        print("🚫 No model-supported lean anywhere on the board — no POTD posted today.")
        try:
            requests.post(
                f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "cache_key": f"best_bet_{today}",
                    "game_id": f"best_bet_{today}",
                    "sport": "none",
                    "narrative": (
                        "No play on the board today. The slate didn't generate any "
                        "leans the model has conviction on. Bucket angles + Dawg of "
                        "the Day are still in the app."
                    ),
                    "data": {"noPlay": True, "reason": "no_model_supported_lean"},
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
    # 'elite' = sweet-spot NRFI 90-94 — 70.0% lifetime, 68.8% L30d (n=30) per 5/18 DB audit.
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

    TIER_RANK = {'elite': 0, 'high': 1, 'solid': 2, 'standard': 3, 'value': 4}
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

    # Also log to history — was silently swallowed pre 2026-05-25; turned
    # loud after 5/25 incident (home tab showed NRFI, receipts showed v2 OVER
    # — both writes timestamped within 250ms of each other in same run yet
    # ended up with different picks). Most likely cause: two concurrent
    # play_of_day invocations racing on the two upserts. Loud failure surfaces
    # this in cron logs next time.
    expected_game = f"{pick['away_team']} @ {pick['home_team']}"
    expected_lean = pick.get('lean_display')
    try:
        hr = requests.post(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?on_conflict=bet_date",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "bet_date": today,
                "sport": pick['sport'],
                "game": expected_game,
                "lean": expected_lean,
                "sweat_score": pick['score'],
                "result": "Pending",
            },
            timeout=15,
        )
        if hr.status_code not in (200, 201, 204):
            print(f"  ⚠️ history write returned {hr.status_code}: {hr.text[:200]}")
    except Exception as e:
        print(f"  ⚠️ history write FAILED: {e}")

    # Consistency check: read back both surfaces and warn if they diverge.
    # When this fires, the next POTD render to users will show mismatched
    # values across Home and Receipts tabs.
    try:
        jc_back = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?game_id=eq.best_bet_{today}&select=data",
            headers=HEADERS, timeout=10,
        ).json()
        hist_back = requests.get(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?bet_date=eq.{today}&select=game,lean",
            headers=HEADERS, timeout=10,
        ).json()
        jc_lean = (jc_back[0].get('data') or {}).get('leanDisplay') if jc_back else None
        jc_game_d = (jc_back[0].get('data') or {}).get('game') if jc_back else {}
        jc_game = f"{jc_game_d.get('away_team')} @ {jc_game_d.get('home_team')}" if jc_game_d else None
        h_game = hist_back[0].get('game') if hist_back else None
        h_lean = hist_back[0].get('lean') if hist_back else None
        if (jc_game and h_game and jc_game != h_game) or (jc_lean and h_lean and jc_lean != h_lean):
            print(f"  ⚠️ POTD DIVERGENCE DETECTED post-write:")
            print(f"     jerry_cache: game={jc_game!r}  lean={jc_lean!r}")
            print(f"     history:     game={h_game!r}  lean={h_lean!r}")
            print(f"     expected:    game={expected_game!r}  lean={expected_lean!r}")
            print(f"     Likely cause: concurrent play_of_day invocation overwrote one surface.")
    except Exception as e:
        print(f"  ⚠️ post-write consistency check failed: {e}")

if __name__ == '__main__':
    run()
