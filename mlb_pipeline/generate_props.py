"""
Pipeline-driven prop generator for MLB.

Scores batter Hits O/U 0.5 and pitcher Ks O/U based on pipeline matchup data.
No EV scanning — conviction comes from proprietary signal alignment.

All signals read directly from mlb_game_context (populated upstream by
game_context.py, team_stats.py, pitcher_stats.py, savant_enrichment.py).

Writes top N props by conviction to mlb_pipeline_props table.
"""
import os
import re
import sys
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}

# TOP_N now scales with slate (computed in run()). Floor 8, ceiling 25.
# Tier cutoffs retuned from 94-game audit (2026-04-30):
#   - hits_over LEAN 63.2% / STRONG 63.0% — flat differentiation, lift STRONG to 72
#   - ks_over LEAN 53.8% / STRONG 53.8% / PRIME 66.7% (n=3) — drop K LEAN entirely,
#     low-conviction Ks barely break even.
K_CUTOFF = 65       # Ks Over — below 65 doesn't beat coin flip
HITS_CUTOFF = 55    # Hits Over — floor still profitable
# Unders bet on rarer outcomes — require higher conviction floors.
K_UNDER_CUTOFF = 65       # Ks Under — fading aspirational K lines
HITS_UNDER_CUTOFF = 70    # Hits Under (0-fer) — needs strong evidence


def _f(v):
    try: return float(v)
    except: return None


def _i(v):
    try: return int(float(v))
    except: return None


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def fetch_todays_games():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{gd}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []


def parse_pitcher_k_pct_from_context(pitcher_context, pitcher_name):
    """Parse season K% from the 'pitcher_context' field format:
    'Name1 (RHP): xERA X, K% Y%, ... | Name2 (RHP): xERA Z, K% W%, ...'"""
    if not pitcher_context or not pitcher_name:
        return None
    last = pitcher_name.split()[-1]
    for segment in pitcher_context.split('|'):
        if last.lower() in segment.lower():
            m = re.search(r'K%\s+([\d.]+)', segment)
            if m:
                try: return float(m.group(1))
                except: return None
    return None


def tier_for(conviction, prop_type=None):
    """Tier thresholds calibrated per prop_type from 94-game audit.
    Hits beat 60% across the board, so STRONG must clear 72 to actually mean
    something different from LEAN. Ks barely beat coin flip at conviction <70,
    so we lift the K bar and skip K LEAN entirely. Unders need higher floors
    because they bet on the rarer outcome (0-fer hitter, K-line fade).
    """
    if prop_type == 'ks_over':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        return 'SKIP'
    if prop_type == 'ks_under':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        return 'SKIP'
    if prop_type == 'hits_under':
        # 0-fer is a long shot — only PRIME/STRONG, no LEAN noise
        if conviction >= 85: return 'PRIME'
        if conviction >= 75: return 'STRONG'
        return 'SKIP'
    # Default / hits_over
    if conviction >= 82: return 'PRIME'
    if conviction >= 72: return 'STRONG'
    if conviction >= 55: return 'LEAN'
    return 'SKIP'


_PITCHER_BUCKET_CACHE = {}
_BATTER_L7_CACHE = {}
_BATTER_ID_CACHE = {}


def _lookup_player_id(player_name):
    """Resolve MLB Stats API personId for a player name (cached)."""
    if not player_name:
        return None
    if player_name in _BATTER_ID_CACHE:
        return _BATTER_ID_CACHE[player_name]
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=8,
        )
        people = r.json().get("people", []) if r.status_code == 200 else []
        pid = people[0]["id"] if people else None
    except Exception:
        pid = None
    _BATTER_ID_CACHE[player_name] = pid
    return pid


def fetch_batter_l7(player_name, season=2026):
    """Last-7-games hitting recency for a batter.
    Returns dict with games, hits_per_game, got_hit_rate, hitless_streak.
    Returns None on lookup failure."""
    if not player_name:
        return None
    if player_name in _BATTER_L7_CACHE:
        return _BATTER_L7_CACHE[player_name]
    pid = _lookup_player_id(player_name)
    if not pid:
        _BATTER_L7_CACHE[player_name] = None
        return None
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
            params={"stats": "gameLog", "group": "hitting", "season": season},
            timeout=10,
        )
        splits = r.json().get("stats", []) if r.status_code == 200 else []
        games = splits[0].get("splits", []) if splits else []
    except Exception:
        games = []
    if not games:
        _BATTER_L7_CACHE[player_name] = None
        return None
    # Newest first, keep games where batter actually had an AB or PA
    games.sort(key=lambda g: g.get("date", ""), reverse=True)
    played = [g for g in games if int(g.get("stat", {}).get("atBats", 0) or 0) > 0]
    last7 = played[:7]
    if len(last7) < 3:
        _BATTER_L7_CACHE[player_name] = None
        return None
    total_h = sum(int(g["stat"].get("hits", 0) or 0) for g in last7)
    total_ab = sum(int(g["stat"].get("atBats", 0) or 0) for g in last7)
    got_hit = sum(1 for g in last7 if int(g["stat"].get("hits", 0) or 0) >= 1)
    # Hitless streak from most recent backwards
    streak = 0
    for g in last7:
        if int(g["stat"].get("hits", 0) or 0) == 0:
            streak += 1
        else:
            break
    out = {
        "games": len(last7),
        "avg": round(total_h / total_ab, 3) if total_ab else None,
        "got_hit_rate": got_hit / len(last7),
        "got_hit_count": got_hit,
        "hitless_streak": streak,
    }
    _BATTER_L7_CACHE[player_name] = out
    return out


def fetch_pitcher_buckets(pitcher_name):
    """Lookup early/mid-inning K% from mlb_pitcher_stats. Cached per run."""
    if not pitcher_name:
        return None
    if pitcher_name in _PITCHER_BUCKET_CACHE:
        return _PITCHER_BUCKET_CACHE[pitcher_name]
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats",
            params={
                'player_name': f'eq.{pitcher_name}',
                'select': 'innings_1_3_k_pct,innings_1_3_ip,innings_4_6_k_pct,innings_4_6_ip',
                'limit': '1',
            },
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        out = rows[0] if rows else None
    except Exception:
        out = None
    _PITCHER_BUCKET_CACHE[pitcher_name] = out
    return out


def score_pitcher_ks(g, side):
    """Score a starter's Ks O/U prop. side = 'home' or 'away'."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None

    # Opener / small-sample filter — don't project Ks for relievers going 1 inning
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None  # opener / bullpen arm — not a starter prop

    opp_side = 'away' if side == 'home' else 'home'
    k_gap = _f(g.get(f'{side}_k_gap'))  # pre-computed: pitcher K% - opp team K% vs hand
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    l3_k = _f(g.get(f'{side}_pitcher_last_3_k_pct'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))
    # Pitcher's catcher's framing = own side's catcher
    framing = _f(g.get(f'{side}_catcher_framing'))
    throws = g.get(f'{side}_throws') or 'R'
    parsed_k_pct = parse_pitcher_k_pct_from_context(g.get('pitcher_context'), pitcher)
    # Sanitize: MLB K% caps around ~40% (historical max ~42% for elite short-stint relievers).
    # Values above 40 are small-sample noise — fall back to a conservative default.
    pitcher_k_pct = parsed_k_pct if parsed_k_pct is not None and 5 <= parsed_k_pct <= 40 else None
    ump_note = (g.get('umpire_note') or '').lower()

    signals = {}
    conviction = 30

    # K rate gap vs opposing lineup (already computed upstream)
    if k_gap is not None:
        if k_gap >= 8:
            conviction += 22
            signals['k_gap'] = f'{pitcher.split()[-1]} K% vs lineup: +{k_gap:.1f}pt advantage'
        elif k_gap >= 4:
            conviction += 12
            signals['k_gap'] = f'+{k_gap:.1f}pt K rate advantage vs lineup'
        elif k_gap <= -5:
            conviction -= 10
            signals['k_gap_neg'] = f'{k_gap:.1f}pt K rate disadvantage'

    # xERA tier
    if xera <= 3.0:
        conviction += 15
        signals['xera'] = f'Elite xERA {xera:.2f}'
    elif xera <= 3.75:
        conviction += 8
        signals['xera'] = f'Above-avg xERA {xera:.2f}'
    elif xera >= 5.0:
        conviction -= 8

    # Absolute season K% signal (high K pitcher trending)
    if pitcher_k_pct is not None and pitcher_k_pct >= 28:
        conviction += 10
        signals['k_artist'] = f'Season K% {pitcher_k_pct:.1f}% — strikeout artist'
    elif pitcher_k_pct is not None and pitcher_k_pct <= 17:
        conviction -= 8

    # L3 form — hot streak on K rate
    if l3_k is not None and pitcher_k_pct is not None and l3_k - pitcher_k_pct >= 3:
        conviction += 8
        signals['form_hot'] = f'L3 K% {l3_k:.1f}% vs season {pitcher_k_pct:.1f}% — heater'
    elif l3_era is not None and l3_era >= 6.0:
        conviction -= 8
        signals['form_cold'] = f'L3 ERA {l3_era:.2f} — struggling'

    # Opposing offense quality
    if opp_wrc >= 120:
        conviction -= 15
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — elite offense grinds ABs'
    elif opp_wrc <= 85:
        conviction += 8
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — weak lineup'

    # Opposing team swing-and-miss tendency
    if opp_k_pct >= 26:
        conviction += 10
        signals['opp_k_rate'] = f'Opp K% {opp_k_pct:.1f}% — whiff-prone'
    elif opp_k_pct <= 18:
        conviction -= 6

    # Catcher framing behind the plate helps the pitcher
    if framing is not None and framing >= 2:
        conviction += 8
        signals['framing'] = f'Catcher +{framing:.1f} framing runs — expands zone'
    elif framing is not None and framing <= -2:
        conviction -= 5

    # 1st inning trouble hurts K volume (eats pitches early)
    if first_inn_era is not None and first_inn_era >= 5.0:
        conviction -= 10
        signals['slow_start'] = f'1st inn ERA {first_inn_era:.1f} — eats pitches early'

    # Umpire
    if 'k-friendly' in ump_note:
        conviction += 8
        signals['umpire'] = 'K-friendly umpire'

    # Inning-bucket K% — front-loaded K guys hit Over on lower lines because
    # they bank K count before getting pulled. Especially powerful when the
    # season-long K% looks pedestrian but bucket 1-3 is elite (early-game
    # stuff plays up before the lineup adjusts).
    buckets = fetch_pitcher_buckets(pitcher)
    if buckets:
        b13 = _f(buckets.get('innings_1_3_k_pct'))
        b13_ip = _f(buckets.get('innings_1_3_ip')) or 0
        b46 = _f(buckets.get('innings_4_6_k_pct'))
        # Need ≥10 IP in bucket 1-3 to trust the rate
        if b13 is not None and b13_ip >= 10:
            if b13 >= 32:
                conviction += 12
                signals['bucket_k'] = f'1st-3rd K% {b13:.0f}% — front-loads strikeouts'
            elif b13 >= 28:
                conviction += 7
                signals['bucket_k'] = f'1st-3rd K% {b13:.0f}% — early-K leaning'
            elif b13 <= 16:
                conviction -= 6
                signals['bucket_k'] = f'1st-3rd K% {b13:.0f}% — slow K start'
            # Bonus when middle innings sustain (no bucket 4-6 collapse)
            if b46 is not None and b13 >= 26 and b46 >= 24:
                conviction += 4
                signals['bucket_sustain'] = f'4th-6th K% {b46:.0f}% — sustains through order'

    conviction = max(0, min(100, conviction))

    # Suggested line: conservative projection with realistic caps.
    # Books rarely post pitcher K lines above 7.5 even for elite arms —
    # matching that distribution keeps our suggested line credible.
    # Small-sample noise cap: if K% > 30, use 28 as ceiling for projection
    # (prevents rookie/tiny-sample pitchers from getting 8+ K lines).
    raw_k = pitcher_k_pct if pitcher_k_pct is not None else 22
    k_pct_for_line = min(raw_k, 28)  # cap small-sample spikes
    typical_ip = 5.0  # realistic average starter IP (not aspirational)
    est_ks = (k_pct_for_line / 100) * (typical_ip * 4.0)  # 4.0 BF/IP for quality starts
    # Tier-based line caps — mirrors book distribution
    if raw_k >= 32:
        line_cap = 7.0  # elite K guys max out around 7.0 on books
    elif raw_k >= 28:
        line_cap = 6.5
    elif raw_k >= 24:
        line_cap = 5.5
    else:
        line_cap = 5.0
    suggested_line = max(3.5, min(line_cap, round(est_ks - 0.5, 1)))

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
        'throws': throws,
    }


def score_pitcher_ks_under(g, side):
    """Score a Ks Under prop — fading aspirational K lines on contact-heavy
    matchups. Inverts the K Over signal stack: high opp wRC+ (grinding ABs),
    low opp K% (puts ball in play), modest pitcher K%, slow-start ERA, soft
    bucket 1-3 K%, K-suppressing ump."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None

    # Don't rate Unders for openers/relievers — book lines for them are already low
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None

    opp_side = 'away' if side == 'home' else 'home'
    k_gap = _f(g.get(f'{side}_k_gap'))
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    l3_k = _f(g.get(f'{side}_pitcher_last_3_k_pct'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))
    framing = _f(g.get(f'{side}_catcher_framing'))
    parsed_k_pct = parse_pitcher_k_pct_from_context(g.get('pitcher_context'), pitcher)
    pitcher_k_pct = parsed_k_pct if parsed_k_pct is not None and 5 <= parsed_k_pct <= 40 else None
    ump_note = (g.get('umpire_note') or '').lower()

    signals = {}
    conviction = 30

    # Don't fade strikeout artists — they tend to clear even high lines
    if pitcher_k_pct is not None and pitcher_k_pct >= 30:
        return None  # not a fade candidate
    if pitcher_k_pct is not None and pitcher_k_pct >= 26:
        conviction -= 12

    # K gap inverted — pitcher LESS K-prone than the opp lineup
    if k_gap is not None:
        if k_gap <= -5:
            conviction += 18
            signals['k_gap_neg'] = f'Pitcher K% {k_gap:.1f}pt below opp lineup'
        elif k_gap <= -2:
            conviction += 10
            signals['k_gap_neg'] = f'Pitcher K% {k_gap:.1f}pt below opp lineup'
        elif k_gap >= 6:
            conviction -= 10  # pitcher actually has the K edge — don't fade

    # Opp wRC+ — elite contact lineup grinds ABs and puts ball in play
    if opp_wrc >= 115:
        conviction += 15
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — grinds ABs, puts ball in play'
    elif opp_wrc >= 105:
        conviction += 7
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — quality ABs'
    elif opp_wrc <= 85:
        conviction -= 8  # weak lineup more likely to whiff

    # Opp K% — contact lineup
    if opp_k_pct <= 18:
        conviction += 14
        signals['opp_contact'] = f'Opp K% {opp_k_pct:.1f}% — contact lineup'
    elif opp_k_pct <= 21:
        conviction += 7
        signals['opp_contact'] = f'Opp K% {opp_k_pct:.1f}% — below-avg whiff rate'
    elif opp_k_pct >= 26:
        conviction -= 10  # whiff-prone offense supports the Over

    # Pitcher absolute K% — modest K guys are the right fade target
    if pitcher_k_pct is not None and pitcher_k_pct <= 18:
        conviction += 12
        signals['low_k_pitcher'] = f'Season K% {pitcher_k_pct:.1f}% — pitch-to-contact'
    elif pitcher_k_pct is not None and pitcher_k_pct <= 22:
        conviction += 6

    # L3 form — K rate fading
    if l3_k is not None and pitcher_k_pct is not None and pitcher_k_pct - l3_k >= 3:
        conviction += 8
        signals['form_cold'] = f'L3 K% {l3_k:.1f}% vs season {pitcher_k_pct:.1f}% — fading'

    # Bad framing catcher = lost called strikes
    if framing is not None and framing <= -2:
        conviction += 8
        signals['framing'] = f'Catcher {framing:.1f} framing runs — costs called strikes'
    elif framing is not None and framing >= 3:
        conviction -= 5

    # Slow first inning = pitcher gets pulled early (caps K count)
    if first_inn_era is not None and first_inn_era >= 5.5:
        conviction += 10
        signals['short_outing'] = f'1st inn ERA {first_inn_era:.1f} — gets pulled early'

    # L3 ERA blowout = book may already lower line; but if not, supports short outing
    if l3_era is not None and l3_era >= 6.0:
        conviction += 5
        signals['form_struggling'] = f'L3 ERA {l3_era:.2f} — short leash'

    # Inning bucket — slow K starter who fades through the order
    buckets = fetch_pitcher_buckets(pitcher)
    if buckets:
        b13 = _f(buckets.get('innings_1_3_k_pct'))
        b13_ip = _f(buckets.get('innings_1_3_ip')) or 0
        if b13 is not None and b13_ip >= 10 and b13 <= 18:
            conviction += 10
            signals['bucket_k'] = f'1st-3rd K% {b13:.0f}% — slow K accumulation'

    # K-friendly ump opposes the fade
    if 'k-friendly' in ump_note:
        conviction -= 8
    elif 'pitcher-friendly' in ump_note and 'k' not in ump_note:
        # pitcher-friendly but not specifically K-friendly = bigger zone, not more Ks
        pass

    conviction = max(0, min(100, conviction))

    # Suggested Under line — project conservative Ks then add cushion to the
    # line we'd take Under. Books often hang lines 1-1.5 above projection on
    # mid-tier arms, so we suggest a line slightly above our projection that
    # still has fade value.
    raw_k = pitcher_k_pct if pitcher_k_pct is not None else 20
    typical_ip = 4.5  # short-leash assumption when we're fading
    est_ks = (raw_k / 100) * (typical_ip * 4.0)
    # Suggest the next half-line above projection (where book likely sits)
    suggested_line = max(4.0, min(7.0, round(est_ks + 1.0, 0) - 0.5))

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
    }


def score_batter_hits(g, batter, side, lineup_position=None):
    """Score a batter's Hits Over 0.5 prop. side = 'home' or 'away' (batter's side).
    lineup_position: 1-indexed spot in the confirmed lineup (1-9)."""
    opp_side = 'away' if side == 'home' else 'home'

    team_wrc_vs_hand = _f(g.get(f'{side}_wrc_vs_opp_hand'))
    team_wrc_season = _f(g.get(f'{side}_wrc_plus')) or 100
    team_wrc = team_wrc_vs_hand if team_wrc_vs_hand is not None else team_wrc_season
    team_ops_vs_hand = _f(g.get(f'{side}_ops_vs_opp_hand'))
    opp_xera = _f(g.get(f'{opp_side}_sp_xera'))
    opp_l3 = _f(g.get(f'{opp_side}_pitcher_last_3_era'))
    opp_bp = _f(g.get(f'{opp_side}_bullpen_era'))
    opp_throws = g.get(f'{opp_side}_throws') or 'R'
    opp_pitcher = g.get(f'{opp_side}_pitcher') or 'opposing SP'
    opp_pitcher_k_pct = parse_pitcher_k_pct_from_context(g.get('pitcher_context'), opp_pitcher)
    park = _i(g.get('park_run_factor'))
    temp = _i(g.get('temperature'))
    wind_speed = _i(g.get('wind_speed'))
    wind_dir = (g.get('wind_direction') or '').upper()
    ump_note = (g.get('umpire_note') or '').lower()

    signals = {}
    conviction = 30

    # Team platoon-adjusted offense
    if team_wrc >= 115:
        conviction += 15
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — elite'
    elif team_wrc >= 105:
        conviction += 8
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — above avg'
    elif team_wrc <= 85:
        conviction -= 10
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — weak'

    # Opposing starter quality — fall back to L3 ERA when xERA is null (early season, suspicious values capped upstream)
    opp_quality = opp_xera if opp_xera is not None else opp_l3
    opp_quality_label = 'xERA' if opp_xera is not None else 'L3 ERA'
    if opp_quality is not None:
        if opp_quality >= 5.0:
            conviction += 18
            signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — very soft'
        elif opp_quality >= 4.25:
            conviction += 10
            signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — below avg'
        elif opp_quality <= 2.75:
            conviction -= 12
            signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — elite arm'

    # K-heavy starter punishes hit props
    if opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 28:
        conviction -= 8
        signals['opp_k_heavy'] = f'Opp K% {opp_pitcher_k_pct:.1f}% — strikeout artist'

    # L3 opposing pitcher form drift
    if opp_l3 is not None and opp_l3 >= 5.5:
        conviction += 8
        signals['opp_form'] = f'Opp L3 ERA {opp_l3:.2f} — trending wrong way'
    elif opp_l3 is not None and opp_l3 <= 2.5 and opp_xera is not None and opp_xera <= 3.5:
        conviction -= 6
        signals['opp_form_hot'] = f'Opp L3 ERA {opp_l3:.2f} — locked in'

    # Opposing bullpen — matters for hits 2+ and late-game
    if opp_bp is not None:
        if opp_bp >= 4.5:
            conviction += 8
            signals['opp_bullpen'] = f'Opp BP ERA {opp_bp:.2f} — soft pen'
        elif opp_bp <= 3.0:
            conviction -= 5

    # Park factor
    if park is not None:
        if park >= 108:
            conviction += 10
            signals['park'] = f'Park factor {park} — hitter friendly'
        elif park >= 103:
            conviction += 5
            signals['park'] = f'Park factor {park} — slight hitter tilt'
        elif park <= 93:
            conviction -= 8
            signals['park'] = f'Park factor {park} — pitcher park'

    # Wind blowing out
    if wind_speed and wind_dir:
        if wind_speed >= 10 and wind_dir in ('S', 'SW', 'SSW', 'SSE', 'SE'):
            conviction += 5
            signals['wind'] = f'Wind {wind_speed}mph {wind_dir} — blowing out'
        elif wind_speed >= 12 and wind_dir in ('N', 'NW', 'NNW', 'NNE', 'NE'):
            conviction -= 5

    # Hot weather
    if temp is not None and temp >= 80:
        conviction += 3

    # K-friendly ump hurts contact-reliant hitters
    if 'k-friendly' in ump_note:
        conviction -= 5

    # Lineup position bonus — top of order sees more PAs = higher hit probability
    if lineup_position is not None:
        if lineup_position <= 2:
            conviction += 6
            signals['lineup_spot'] = f'Hitting {lineup_position} — leadoff/2-hole (4-5 PAs)'
        elif lineup_position <= 5:
            conviction += 3
            signals['lineup_spot'] = f'Hitting {lineup_position} — heart of order (4+ PAs)'
        elif lineup_position >= 8:
            conviction -= 4
            signals['lineup_spot'] = f'Hitting {lineup_position} — bottom of order (3-4 PAs)'

    # L7 recency — got-a-hit rate is the most direct signal for hits 0.5
    l7 = fetch_batter_l7(batter)
    if l7:
        rate = l7['got_hit_rate']
        n = l7['games']
        avg = l7.get('avg')
        streak = l7.get('hitless_streak', 0)
        if rate >= 0.85:
            conviction += 12
            signals['l7_hot'] = f'Hits in {l7["got_hit_count"]} of last {n} ({rate*100:.0f}%)'
        elif rate >= 0.70:
            conviction += 7
            signals['l7_warm'] = f'Hits in {l7["got_hit_count"]} of last {n} ({rate*100:.0f}%)'
        elif rate <= 0.40:
            conviction -= 10
            signals['l7_cold'] = f'Only {l7["got_hit_count"]} of last {n} games with a hit'
        # L7 batting avg layer (caps recency-spike noise)
        if avg is not None and avg >= 0.350:
            conviction += 5
            signals['l7_avg_hot'] = f'L7 BA .{int(avg*1000):03d}'
        elif avg is not None and avg <= 0.180:
            conviction -= 5
        # Active hitless streak penalty (3+ games without a hit)
        if streak >= 3:
            conviction -= 6
            signals['hitless_streak'] = f'{streak} straight games w/o a hit'

    conviction = max(0, min(100, conviction))
    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': 0.5,
    }


def score_batter_hits_under(g, batter, side, lineup_position=None):
    """Score Hits Under 0.5 — batter goes 0-fer. Inverts hits scorer:
    elite opp pitcher, K-heavy starter, weak team offense, bottom of order,
    K-friendly ump, pitcher park, cold L7 with active hitless streak."""
    opp_side = 'away' if side == 'home' else 'home'

    team_wrc_vs_hand = _f(g.get(f'{side}_wrc_vs_opp_hand'))
    team_wrc_season = _f(g.get(f'{side}_wrc_plus')) or 100
    team_wrc = team_wrc_vs_hand if team_wrc_vs_hand is not None else team_wrc_season
    opp_xera = _f(g.get(f'{opp_side}_sp_xera'))
    opp_l3 = _f(g.get(f'{opp_side}_pitcher_last_3_era'))
    opp_throws = g.get(f'{opp_side}_throws') or 'R'
    opp_pitcher = g.get(f'{opp_side}_pitcher') or 'opposing SP'
    opp_pitcher_k_pct = parse_pitcher_k_pct_from_context(g.get('pitcher_context'), opp_pitcher)
    park = _i(g.get('park_run_factor'))
    ump_note = (g.get('umpire_note') or '').lower()

    signals = {}
    conviction = 30

    # Need an elite opp pitcher to bet on a 0-fer — opener filter
    last_ip = _f(g.get(f'{opp_side}_last_ip'))
    if last_ip is not None and last_ip <= 1.5:
        return None  # opener — bullpen game spreads ABs across many arms, dilutes fade

    # Elite opposing pitcher
    opp_quality = opp_xera if opp_xera is not None else opp_l3
    opp_quality_label = 'xERA' if opp_xera is not None else 'L3 ERA'
    if opp_quality is None:
        return None  # no pitcher signal = no fade
    if opp_quality <= 2.75:
        conviction += 22
        signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — ace'
    elif opp_quality <= 3.50:
        conviction += 12
        signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — quality arm'
    elif opp_quality >= 5.0:
        return None  # bad opposing pitcher = wrong side

    # K-heavy opp starter
    if opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 30:
        conviction += 15
        signals['opp_k_artist'] = f'Opp K% {opp_pitcher_k_pct:.1f}% — strikeout artist'
    elif opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 26:
        conviction += 8
        signals['opp_k_heavy'] = f'Opp K% {opp_pitcher_k_pct:.1f}% — high whiff'
    elif opp_pitcher_k_pct is not None and opp_pitcher_k_pct <= 18:
        conviction -= 8

    # Hot opp form
    if opp_l3 is not None and opp_l3 <= 2.0:
        conviction += 8
        signals['opp_form_hot'] = f'Opp L3 ERA {opp_l3:.2f} — locked in'
    elif opp_l3 is not None and opp_l3 >= 5.5:
        conviction -= 8

    # Weak team offense
    if team_wrc <= 85:
        conviction += 12
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — weak'
    elif team_wrc <= 95:
        conviction += 6
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — below avg'
    elif team_wrc >= 115:
        conviction -= 12

    # Pitcher park
    if park is not None:
        if park <= 93:
            conviction += 8
            signals['park'] = f'Park factor {park} — pitcher park'
        elif park <= 98:
            conviction += 3
        elif park >= 108:
            conviction -= 8

    # K-friendly ump aids the fade (more punchouts, fewer balls in play)
    if 'k-friendly' in ump_note:
        conviction += 5
        signals['umpire'] = 'K-friendly umpire'

    # Lineup position — bottom of order = fewer PAs = better fade
    if lineup_position is not None:
        if lineup_position >= 8:
            conviction += 8
            signals['lineup_spot'] = f'Hitting {lineup_position} — only 3 PAs'
        elif lineup_position == 7:
            conviction += 4
        elif lineup_position <= 2:
            conviction -= 8  # 4-5 PAs gives too many chances
        elif lineup_position <= 5:
            conviction -= 4

    # L7 cold + active hitless streak = the strongest fade signal
    l7 = fetch_batter_l7(batter)
    if l7:
        rate = l7['got_hit_rate']
        n = l7['games']
        avg = l7.get('avg')
        streak = l7.get('hitless_streak', 0)
        if rate <= 0.35:
            conviction += 14
            signals['l7_cold'] = f'Only {l7["got_hit_count"]} of last {n} games w/ a hit'
        elif rate <= 0.50:
            conviction += 7
            signals['l7_cool'] = f'Hits in {l7["got_hit_count"]} of last {n} ({rate*100:.0f}%)'
        elif rate >= 0.80:
            conviction -= 12  # recent form opposes the fade
        if avg is not None and avg <= 0.180:
            conviction += 6
            signals['l7_avg_cold'] = f'L7 BA .{int(avg*1000):03d}'
        elif avg is not None and avg >= 0.330:
            conviction -= 6
        if streak >= 3:
            conviction += 8
            signals['hitless_streak'] = f'{streak} straight games w/o a hit'
        elif streak >= 2:
            conviction += 4

    conviction = max(0, min(100, conviction))
    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': 0.5,
    }


def wipe_todays_props():
    gd = today_et()
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{gd}",
        headers=HEADERS,
        timeout=15
    )


def upsert_props(props):
    if not props:
        return 0
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
        headers=HEADERS,
        json=props,
        timeout=20
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return 0
    return len(props)


def run():
    gd = today_et()
    print(f"=== Pipeline prop generator {gd} ===")

    # No overwrite guard — each cron wipes + regenerates so afternoon run
    # with confirmed lineups REPLACES morning run's K-only output.
    # Idempotent: running multiple times just produces the best-available
    # picks based on current lineup confirmation state.

    games = fetch_todays_games()
    if not games:
        print("No games today in mlb_game_context")
        return

    print(f"Scoring props across {len(games)} games...")
    all_props = []

    for g in games:
        game_id = g.get('game_id')
        game_date = g.get('game_date')
        home_team = g.get('home_team')
        away_team = g.get('away_team')
        matchup = f"{away_team} @ {home_team}"

        # Pitcher K props — score Over and Under, only one will clear cutoff
        for side in ('home', 'away'):
            pitcher = g.get(f'{side}_pitcher')
            if not pitcher:
                continue
            over = score_pitcher_ks(g, side)
            if over and over['conviction'] >= K_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'ks_over',
                    'prop_line': over['prop_line'],
                    'direction': 'over',
                    'conviction': over['conviction'],
                    'tier': tier_for(over['conviction'], 'ks_over'),
                    'signals': over['signals'],
                })
            under = score_pitcher_ks_under(g, side)
            if under and under['conviction'] >= K_UNDER_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'ks_under',
                    'prop_line': under['prop_line'],
                    'direction': 'under',
                    'conviction': under['conviction'],
                    'tier': tier_for(under['conviction'], 'ks_under'),
                    'signals': under['signals'],
                })

        # Batter Hits props — requires confirmed lineup
        if not g.get('lineup_confirmed'):
            continue
        for side, lineup_field in (('home', 'home_lineup'), ('away', 'away_lineup')):
            lineup_str = g.get(lineup_field) or ''
            batters = [b.strip() for b in lineup_str.split(',') if b.strip()][:9]
            team_name = g.get(f'{side}_team')
            for idx, batter in enumerate(batters):
                lineup_position = idx + 1
                over = score_batter_hits(g, batter, side, lineup_position)
                if over and over['conviction'] >= HITS_CUTOFF:
                    all_props.append({
                        'game_date': game_date,
                        'game_id': game_id,
                        'player_name': batter,
                        'player_team': team_name,
                        'matchup': matchup,
                        'prop_type': 'hits_over',
                        'prop_line': 0.5,
                        'direction': 'over',
                        'conviction': over['conviction'],
                        'tier': tier_for(over['conviction'], 'hits_over'),
                        'signals': over['signals'],
                    })
                under = score_batter_hits_under(g, batter, side, lineup_position)
                if under and under['conviction'] >= HITS_UNDER_CUTOFF:
                    all_props.append({
                        'game_date': game_date,
                        'game_id': game_id,
                        'player_name': batter,
                        'player_team': team_name,
                        'matchup': matchup,
                        'prop_type': 'hits_under',
                        'prop_line': 0.5,
                        'direction': 'under',
                        'conviction': under['conviction'],
                        'tier': tier_for(under['conviction'], 'hits_under'),
                        'signals': under['signals'],
                    })

    all_props.sort(key=lambda p: p['conviction'], reverse=True)

    # Cap per game so one juicy matchup doesn't flood the board.
    # Hits props (over+under combined) capped at 3 per game.
    # Slate-scaling TOP_N: floor 8 (small slate), ceil 25 (full slate).
    top_n = min(25, max(8, len(games) + 5))
    hits_per_game = {}
    capped = []
    for p in all_props:
        if p['prop_type'] in ('hits_over', 'hits_under'):
            key = p['game_id']
            hits_per_game[key] = hits_per_game.get(key, 0) + 1
            if hits_per_game[key] > 3:
                continue
        capped.append(p)
    top = capped[:top_n]

    wipe_todays_props()
    saved = upsert_props(top)
    print(f"\n✅ Stored {saved} top props (of {len(all_props)} passing threshold)")
    for p in top[:8]:
        print(f"  [{p['conviction']}] {p['player_name']} {p['prop_type']} {p['prop_line']} ({p['tier']}) — {p['matchup']}")
        for k, v in p['signals'].items():
            print(f"      · {v}")

if __name__ == "__main__":
    run()
