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

# New 2026-05-05 — Total Outs and Earned Runs props. Floor at 65 until we
# have audit data. Recalibrate once n=20+ resolved per cohort.
OUTS_CUTOFF = 65
OUTS_UNDER_CUTOFF = 70
ER_CUTOFF = 65
ER_UNDER_CUTOFF = 70


def _f(v):
    try: return float(v)
    except: return None


def _i(v):
    try: return int(float(v))
    except: return None


def pitcher_split_delta(g, side):
    """Returns (split_era - season_era) for the pitcher tonight.
    Positive = pitcher is in WORSE split today (fade-favorable).
    Negative = pitcher is in BETTER split today (Over-K / Under-ER favorable).

    Side='home' means pitcher pitches at home → use home_era split.
    Side='away' means pitcher is on the road → use away_era split.
    Returns None when split data is missing."""
    if side == 'home':
        split_era = _f(g.get('home_pitcher_home_era'))
    else:
        split_era = _f(g.get('away_pitcher_away_era'))
    season_era = _f(g.get(f'{side}_sp_era'))
    if split_era is None or season_era is None:
        return None
    return split_era - season_era


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
    if prop_type in ('outs_over', 'outs_under', 'er_over', 'er_under'):
        # New 2026-05-05 — no audit data yet, mirror Ks tier thresholds
        # since outs/ER are similarly pitcher-driven props. Recalibrate
        # once n=20+ resolved props per cohort.
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
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


_PROJECTED_LINEUP_CACHE = {}


_TEAMS_LOOKUP_CACHE = None  # MLB API teams list cached once per process


def _get_with_retry(url, params=None, timeout=10, retries=2):
    """GET with exponential-backoff retries on timeout/network errors.
    MLB Stats API occasionally hiccups under load — retrying recovers
    instead of silently dropping the call (which was today's bug — 0 hits
    props in the morning pipeline because per-team lineup fetches all
    timed out simultaneously)."""
    import time
    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
        except requests.exceptions.Timeout as e:
            last_exc = e
        except Exception as e:
            last_exc = e
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))  # 0.5s, 1.0s
    return None


def _read_lineup_cache(team_name):
    """Read persistent projected-lineup cache from jerry_cache (24hr TTL).
    Survives process boundaries so multiple cron fires don't re-fetch."""
    if not team_name:
        return None
    try:
        from datetime import datetime, timezone, timedelta
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache",
            params={
                "cache_key": f"eq.projected_lineup_{team_name.replace(' ', '_')}",
                "select": "data,fetched_at",
            },
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=5,
        )
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            return None
        fetched = rows[0].get("fetched_at")
        if fetched:
            ft = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - ft) > timedelta(hours=24):
                return None
        names = (rows[0].get("data") or {}).get("lineup")
        return names if isinstance(names, list) and names else None
    except Exception:
        return None


def _write_lineup_cache(team_name, names):
    if not team_name or not names:
        return
    try:
        from datetime import datetime, timezone
        requests.post(
            f"{SUPABASE_URL}/rest/v1/jerry_cache",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "cache_key"},
            json={
                "cache_key": f"projected_lineup_{team_name.replace(' ', '_')}",
                "game_id": f"projected_lineup_{team_name.replace(' ', '_')}",
                "sport": "MLB",
                "narrative": f"Projected lineup for {team_name}",
                "data": {"lineup": names, "team": team_name},
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=5,
        )
    except Exception:
        pass


def fetch_projected_lineup(team_name, season=2026):
    """Pull the team's MOST RECENT confirmed batting order from MLB Stats
    API box score. Used as a fallback when today's lineup hasn't been
    posted yet (props can still surface in the morning with a PROJECTED
    tag, refreshed to CONFIRMED once today's lineup lands).

    Hardened 2026-05-03: retry on MLB API timeouts + persistent Supabase
    cache (24hr TTL) so morning pipeline doesn't return 0 hits props
    when the API hiccups (today's bug). Cache lives across runs.

    Returns list of full names in batting order (1-9), or [] on failure.
    """
    if not team_name:
        return []
    if team_name in _PROJECTED_LINEUP_CACHE:
        return _PROJECTED_LINEUP_CACHE[team_name]

    # Try persistent cache first (24hr TTL — yesterday's lineup is fine
    # for today's projection; gets refreshed once today's confirms)
    cached = _read_lineup_cache(team_name)
    if cached:
        _PROJECTED_LINEUP_CACHE[team_name] = cached
        return cached

    # Live MLB API path with retries
    global _TEAMS_LOOKUP_CACHE
    try:
        if _TEAMS_LOOKUP_CACHE is None:
            tr = _get_with_retry(
                "https://statsapi.mlb.com/api/v1/teams",
                params={"sportId": 1},
                timeout=10,
            )
            _TEAMS_LOOKUP_CACHE = tr.json().get("teams", []) if tr else []
        teams = _TEAMS_LOOKUP_CACHE
        last = team_name.split()[-1].lower()
        team = next(
            (t for t in teams if last in (t.get("name") or "").lower() or last == (t.get("teamName") or "").lower()),
            None,
        )
        if not team:
            _PROJECTED_LINEUP_CACHE[team_name] = []
            return []
        team_id = team["id"]

        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
        start = end - timedelta(days=7)
        sr = _get_with_retry(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "teamId": team_id, "startDate": start.isoformat(), "endDate": end.isoformat()},
            timeout=10,
        )
        games = []
        for d in (sr.json().get("dates", []) if sr else []):
            for g in d.get("games", []):
                if g.get("status", {}).get("abstractGameState") == "Final":
                    games.append(g)
        if not games:
            _PROJECTED_LINEUP_CACHE[team_name] = []
            return []
        games.sort(key=lambda g: g.get("gameDate", ""), reverse=True)
        last_game_pk = games[0].get("gamePk")
        bx = _get_with_retry(
            f"https://statsapi.mlb.com/api/v1/game/{last_game_pk}/boxscore",
            timeout=12,
        )
        box = bx.json() if bx else {}
        for side in ("home", "away"):
            t = box.get("teams", {}).get(side, {})
            t_name = t.get("team", {}).get("name", "")
            if last in t_name.lower():
                batter_ids = t.get("batters", [])[:9]
                players = t.get("players", {}) or {}
                names = []
                for pid in batter_ids:
                    p = players.get(f"ID{pid}", {})
                    fn = (p.get("person") or {}).get("fullName")
                    if fn:
                        names.append(fn)
                _PROJECTED_LINEUP_CACHE[team_name] = names
                if names:
                    _write_lineup_cache(team_name, names)
                return names
    except Exception:
        pass
    _PROJECTED_LINEUP_CACHE[team_name] = []
    return []


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

    # Home/away split — pitcher in favorable split = more Ks expected
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split <= -1.0:
            conviction += 5
            signals['split'] = f'In favored split ({split:+.2f} ERA vs season)'
        elif split >= 1.0:
            conviction -= 5
            signals['split_neg'] = f'In worse split ({split:+.2f} ERA vs season)'

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

    # Home/away split — pitcher in WORSE split = fewer Ks → boost K Under
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split >= 1.0:
            conviction += 5
            signals['split'] = f'In worse split ({split:+.2f} ERA vs season)'
        elif split <= -1.0:
            conviction -= 5

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


def score_pitcher_outs(g, side):
    """Score a starter's Total Outs Over prop. side = 'home' or 'away'.
    Markets typically post 13.5 / 14.5 / 15.5 / 16.5 / 17.5 outs. We score
    Over when the pitcher profile says he goes deep (low xERA, durable last
    outing, weak opp lineup, manager won't quick-hook)."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    # Opener filter — relievers don't go 14+ outs
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None

    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    own_bp_era = _f(g.get(f'{side}_bullpen_era'))
    park_run = _f(g.get('park_run_factor')) or 100
    days_rest = _f(g.get(f'{side}_days_rest'))
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))

    signals = {}
    conviction = 35  # baseline — Over outs needs decent quality to hit

    # xERA — primary durability signal
    if xera <= 3.0:
        conviction += 18
        signals['xera'] = f'Elite xERA {xera:.2f} — goes deep'
    elif xera <= 3.75:
        conviction += 10
        signals['xera'] = f'Above-avg xERA {xera:.2f}'
    elif xera >= 5.0:
        conviction -= 14
        signals['xera_high'] = f'xERA {xera:.2f} — likely short outing'

    # Last outing pitch count + IP — recent durability proof
    if last_ip is not None and last_ip >= 6.0 and (last_pitches or 0) >= 90:
        conviction += 10
        signals['durable'] = f'Last outing: {last_pitches:.0f}p / {last_ip:.1f} IP — stretched out'
    elif last_ip is not None and last_ip <= 4.5:
        conviction -= 8
        signals['short_last'] = f'Last outing: {last_ip:.1f} IP — short leash'

    # L3 form
    if l3_era is not None:
        if l3_era <= 2.5:
            conviction += 8
            signals['l3'] = f'L3 ERA {l3_era:.2f} — locked in'
        elif l3_era >= 6.0:
            conviction -= 10
            signals['l3_bad'] = f'L3 ERA {l3_era:.2f} — short hook risk'

    # Opp offense quality — strong lineups force quicker exits
    if opp_wrc >= 115:
        conviction -= 12
        signals['opp_wrc'] = f'Opp wRC+ {opp_wrc:.0f} — grinds, forces high pitch count'
    elif opp_wrc <= 90:
        conviction += 8
        signals['opp_weak'] = f'Opp wRC+ {opp_wrc:.0f} — soft lineup, longer outing'

    # Bullpen workload — gassed pen = manager keeps starter in (good for Over outs)
    own_bp_used = _f(g.get(f'{side}_bp_relievers_3d')) or 0
    if own_bp_used >= 11:
        conviction += 7
        signals['pen_gassed'] = f'Own pen used {int(own_bp_used)} relievers L3d — manager rides starter'

    # Park — high-run parks force more pitches
    if park_run >= 110:
        conviction -= 5
    elif park_run <= 92:
        conviction += 4

    # Days rest — extra rest helps depth
    if days_rest is not None and days_rest >= 6:
        conviction += 3

    # 1st-inning trouble bleeds into pitch count
    if first_inn_era is not None and first_inn_era >= 5.0:
        conviction -= 7
        signals['slow_start'] = f'1st inn ERA {first_inn_era:.1f} — burns pitches early'

    # Home/away split — favorable split → pitcher goes deeper
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split <= -1.0:
            conviction += 5
            signals['split'] = f'In favored split ({split:+.2f} ERA vs season)'
        elif split >= 1.0:
            conviction -= 5

    conviction = max(0, min(100, conviction))

    # Suggested line: most starters target 5-6 IP = 15-18 outs.
    # Elite + healthy + weak opp → 17.5; mediocre → 14.5.
    if xera <= 3.0 and last_ip is not None and last_ip >= 6.0:
        suggested_line = 17.5
    elif xera <= 3.75:
        suggested_line = 16.5
    elif xera <= 4.5:
        suggested_line = 15.5
    else:
        suggested_line = 14.5

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
    }


def score_pitcher_outs_under(g, side):
    """Score a starter's Total Outs Under prop — fade durability. Hits when
    the pitcher is shaky, opp lineup is strong, or the manager has a quick
    hook (gassed bullpen rested). Mirrors score_pitcher_outs in inputs."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    # Openers ARE candidates for Under (they only go 1-3 IP) — don't skip them here
    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))

    signals = {}
    conviction = 30

    # Mark obvious openers — should hit Under almost trivially
    if last_ip is not None and last_ip <= 2.0 and (last_pitches or 0) <= 40:
        conviction += 35
        signals['opener'] = f'Last outing {last_ip:.1f} IP / {last_pitches or 0:.0f}p — opener'

    # xERA — bad pitchers go shorter
    if xera >= 5.0:
        conviction += 16
        signals['xera_high'] = f'xERA {xera:.2f} — short outing risk'
    elif xera >= 4.25:
        conviction += 8
    elif xera <= 3.0:
        conviction -= 12

    # Recent struggles
    if l3_era is not None and l3_era >= 6.0:
        conviction += 12
        signals['l3_bad'] = f'L3 ERA {l3_era:.2f} — getting hooked early'
    elif l3_era is not None and l3_era <= 2.5:
        conviction -= 8

    # 1st-inning trouble
    if first_inn_era is not None and first_inn_era >= 5.0:
        conviction += 10
        signals['slow_start'] = f'1st inn ERA {first_inn_era:.1f} — burns pitches'

    # Strong opp lineup forces quick exit
    if opp_wrc >= 115:
        conviction += 12
        signals['opp_wrc'] = f'Opp wRC+ {opp_wrc:.0f} — grinds early'
    elif opp_wrc <= 88:
        conviction -= 10

    # High-run park
    if park_run >= 110:
        conviction += 6
        signals['park'] = f'Park factor {park_run:.0f} — runs come, pen called'

    # Last outing short — momentum signal
    if last_ip is not None and 2.0 < last_ip <= 4.5:
        conviction += 8
        signals['short_last'] = f'Last outing {last_ip:.1f} IP — fragile'

    # Home/away split — worse split = quicker hook
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split >= 1.0:
            conviction += 5
            signals['split'] = f'In worse split ({split:+.2f} ERA vs season)'
        elif split <= -1.0:
            conviction -= 5

    conviction = max(0, min(100, conviction))

    # Suggested line: market rarely posts under 12.5; pick the line where
    # the pitcher's projection is most likely to be on the wrong side.
    if last_ip is not None and last_ip <= 2.0:
        suggested_line = 12.5  # opener - very low line
    elif xera >= 5.0:
        suggested_line = 14.5
    else:
        suggested_line = 15.5

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
    }


def score_pitcher_er(g, side):
    """Score a starter's Earned Runs Allowed Over prop. Most lines: 1.5-3.5.
    Hits when the pitcher gives up runs (bad xERA, soft contact profile,
    HR-friendly env, strong opp). Common Over angle for starters with
    elevated xERA in hitter parks."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    # Openers don't pitch enough innings to give up 2.5+ ER cleanly — skip
    if last_ip is not None and last_ip <= 1.5 and (last_pitches or 0) <= 35:
        return None

    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    park_hr = _f(g.get('park_hr_factor')) or 100
    temp = _f(g.get('temperature')) or 70
    wind_speed = _f(g.get('wind_speed')) or 0
    wind_dir = (g.get('wind_direction') or '').upper()
    framing = _f(g.get(f'{side}_catcher_framing'))
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))

    signals = {}
    conviction = 30

    # xERA is the headline signal for ER
    if xera >= 5.0:
        conviction += 22
        signals['xera_high'] = f'xERA {xera:.2f} — bleeds runs'
    elif xera >= 4.25:
        conviction += 12
        signals['xera_avg'] = f'xERA {xera:.2f} — above avg run risk'
    elif xera <= 3.0:
        conviction -= 18

    # L3 form
    if l3_era is not None:
        if l3_era >= 6.0:
            conviction += 14
            signals['l3'] = f'L3 ERA {l3_era:.2f} — getting tagged'
        elif l3_era <= 2.5:
            conviction -= 12

    # 1st-inning ERA — ER often happens early
    if first_inn_era is not None and first_inn_era >= 5.0:
        conviction += 8
        signals['1st_inn'] = f'1st inn ERA {first_inn_era:.1f} — opens shaky'

    # Opp lineup quality
    if opp_wrc >= 115:
        conviction += 14
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — top tier offense'
    elif opp_wrc <= 88:
        conviction -= 10

    # Park run factor
    if park_run >= 110:
        conviction += 8
        signals['park'] = f'Park factor {park_run:.0f} — hitter friendly'
    elif park_run <= 92:
        conviction -= 6

    # Park HR + warm wind out — combined HR risk lifts ER
    wind_out = wind_speed > 10 and any(d in wind_dir for d in ('S', 'SW', 'SE', 'OUT'))
    if park_hr >= 108 and (temp >= 75 or wind_out):
        conviction += 8
        signals['env'] = f'Park HR {park_hr:.0f}, {int(temp)}°F{" / wind out" if wind_out else ""} — HR-friendly'

    # Catcher framing — bad framing = more pitches in zone, more contact
    if framing is not None and framing <= -2:
        conviction += 6
        signals['framing_bad'] = f'Catcher {framing:.1f} framing — squeezed strikes'
    elif framing is not None and framing >= 2:
        conviction -= 5

    # Home/away split — worse split = more ER expected
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split >= 1.0:
            conviction += 5
            signals['split'] = f'In worse split ({split:+.2f} ERA vs season)'
        elif split <= -1.0:
            conviction -= 5

    conviction = max(0, min(100, conviction))

    # Suggested line: most common is 2.5. Use 1.5 only for elite arms (rare
    # — book usually doesn't price a 1.5 ER Over with edge for the bettor).
    if xera <= 3.0:
        suggested_line = 1.5
    elif xera <= 4.0:
        suggested_line = 2.5
    else:
        suggested_line = 2.5  # most-common market line — keep simple

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
    }


def score_pitcher_er_under(g, side):
    """Score a starter's Earned Runs Allowed Under prop — fade run scoring.
    Cleanest setups: ace pitcher (low xERA), pitcher park, weak opp lineup,
    cold weather + wind in. Common 2.5 ER Under PRIME play vs offensively
    weak teams."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches or 0) <= 35:
        return None  # opener

    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    park_hr = _f(g.get('park_hr_factor')) or 100
    temp = _f(g.get('temperature')) or 70
    wind_speed = _f(g.get('wind_speed')) or 0
    wind_dir = (g.get('wind_direction') or '').upper()
    framing = _f(g.get(f'{side}_catcher_framing'))
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))

    signals = {}
    conviction = 30

    # xERA — primary signal
    if xera <= 2.5:
        conviction += 25
        signals['xera_elite'] = f'Elite xERA {xera:.2f} — ace'
    elif xera <= 3.0:
        conviction += 18
        signals['xera'] = f'Strong xERA {xera:.2f}'
    elif xera <= 3.5:
        conviction += 10
    elif xera >= 4.5:
        conviction -= 15

    # L3 form
    if l3_era is not None:
        if l3_era <= 2.0:
            conviction += 10
            signals['l3_hot'] = f'L3 ERA {l3_era:.2f} — locked in'
        elif l3_era >= 5.5:
            conviction -= 10

    # Opp lineup weakness
    if opp_wrc <= 90:
        conviction += 14
        signals['opp_weak'] = f'Opp wRC+ {opp_wrc:.0f} — soft lineup'
    elif opp_wrc >= 115:
        conviction -= 12

    # Pitcher park
    if park_run <= 95:
        conviction += 8
        signals['park'] = f'Park factor {park_run:.0f} — pitcher friendly'
    elif park_run >= 108:
        conviction -= 8

    # Cold + wind in suppresses runs
    wind_in = wind_speed > 8 and any(d in wind_dir for d in ('N', 'NW', 'NE', 'IN'))
    if park_hr <= 95 and (temp <= 60 or wind_in):
        conviction += 8
        signals['env'] = f'Park HR {park_hr:.0f}, {int(temp)}°F{" / wind in" if wind_in else ""} — run suppressing'

    # Catcher framing helps zone control = fewer balls = less traffic
    if framing is not None and framing >= 2:
        conviction += 6
        signals['framing'] = f'Catcher +{framing:.1f} framing — expands zone'

    # Home/away split — favorable split = lower ER risk
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split <= -1.0:
            conviction += 5
            signals['split'] = f'In favored split ({split:+.2f} ERA vs season)'
        elif split >= 1.0:
            conviction -= 5

    conviction = max(0, min(100, conviction))

    # Most common Under line is 2.5
    suggested_line = 2.5
    if xera <= 2.5:
        suggested_line = 1.5  # only for true aces

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
    """Upsert prop rows. Falls back to stripping the lineup_state field if
    Supabase rejects it (column doesn't exist yet — user needs to run:
      ALTER TABLE mlb_pipeline_props ADD COLUMN lineup_state TEXT;
    Once added, the field flows through and the app can render PROJECTED
    vs CONFIRMED tags on hits props.)"""
    if not props:
        return 0
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
        headers=HEADERS,
        json=props,
        timeout=20
    )
    if r.status_code in (200, 201, 204):
        return len(props)
    # Schema-mismatch fallback — strip optional fields and retry once.
    # New columns the user needs to add:
    #   ALTER TABLE mlb_pipeline_props ADD COLUMN lineup_state TEXT;
    #   ALTER TABLE mlb_pipeline_props ADD COLUMN stack_alert BOOLEAN DEFAULT FALSE;
    optional_cols = ('lineup_state', 'stack_alert')
    if r.status_code == 400 and any(c in (r.text or '') for c in optional_cols):
        missing = [c for c in optional_cols if c in (r.text or '')]
        print(f"  ⚠️ optional columns missing ({', '.join(missing)}) — stripping and retrying. Run:")
        for c in missing:
            if c == 'lineup_state':
                print(f"      ALTER TABLE mlb_pipeline_props ADD COLUMN lineup_state TEXT;")
            elif c == 'stack_alert':
                print(f"      ALTER TABLE mlb_pipeline_props ADD COLUMN stack_alert BOOLEAN DEFAULT FALSE;")
        for p in props:
            for c in optional_cols:
                p.pop(c, None)
        r2 = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
            headers=HEADERS,
            json=props,
            timeout=20,
        )
        if r2.status_code in (200, 201, 204):
            return len(props)
        print(f"  ⚠️ retry also failed {r2.status_code}: {r2.text[:300]}")
        return 0
    print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
    return 0


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
                    'lineup_state': 'confirmed',
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
                    'lineup_state': 'confirmed',
                })

            # Total Outs O/U (new 2026-05-05) — score both sides, only the
            # winner clears cutoff. Same opener-skip applies to Over but not
            # Under (openers naturally bust Over and confirm Under).
            outs_over = score_pitcher_outs(g, side)
            if outs_over and outs_over['conviction'] >= OUTS_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'outs_over',
                    'prop_line': outs_over['prop_line'],
                    'direction': 'over',
                    'conviction': outs_over['conviction'],
                    'tier': tier_for(outs_over['conviction'], 'outs_over'),
                    'signals': outs_over['signals'],
                    'lineup_state': 'confirmed',
                })
            outs_under = score_pitcher_outs_under(g, side)
            if outs_under and outs_under['conviction'] >= OUTS_UNDER_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'outs_under',
                    'prop_line': outs_under['prop_line'],
                    'direction': 'under',
                    'conviction': outs_under['conviction'],
                    'tier': tier_for(outs_under['conviction'], 'outs_under'),
                    'signals': outs_under['signals'],
                    'lineup_state': 'confirmed',
                })

            # Earned Runs O/U
            er_over = score_pitcher_er(g, side)
            if er_over and er_over['conviction'] >= ER_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'er_over',
                    'prop_line': er_over['prop_line'],
                    'direction': 'over',
                    'conviction': er_over['conviction'],
                    'tier': tier_for(er_over['conviction'], 'er_over'),
                    'signals': er_over['signals'],
                    'lineup_state': 'confirmed',
                })
            er_under = score_pitcher_er_under(g, side)
            if er_under and er_under['conviction'] >= ER_UNDER_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'er_under',
                    'prop_line': er_under['prop_line'],
                    'direction': 'under',
                    'conviction': er_under['conviction'],
                    'tier': tier_for(er_under['conviction'], 'er_under'),
                    'signals': er_under['signals'],
                    'lineup_state': 'confirmed',
                })

        # Batter Hits props — confirmed lineup preferred, projected as fallback.
        # Hybrid lineup approach (added 2026-05-02): if today's lineup not yet
        # posted by MLB, fall back to the team's most recent batting order from
        # box score. Mark the prop with lineup_state=projected so the app can
        # show a PROJECTED tag (and the next pipeline run with confirmed
        # lineups overwrites the row with state=confirmed).
        confirmed = bool(g.get('lineup_confirmed'))
        for side, lineup_field in (('home', 'home_lineup'), ('away', 'away_lineup')):
            team_name = g.get(f'{side}_team')
            if confirmed:
                lineup_str = g.get(lineup_field) or ''
                batters = [b.strip() for b in lineup_str.split(',') if b.strip()][:9]
                lineup_state = 'confirmed'
            else:
                batters = fetch_projected_lineup(team_name)
                lineup_state = 'projected'
            if not batters:
                continue
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
                        'lineup_state': lineup_state,
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
                        'lineup_state': lineup_state,
                    })

    all_props.sort(key=lambda p: p['conviction'], reverse=True)

    # Cap per game so one juicy matchup doesn't flood the board.
    # STACK ALERT (added 2026-05-03): when 4+ batters in the same game score
    # PRIME tier (≥82 conviction), the matchup itself is the play (everyone
    # is teeing off vs a disaster starter — Cubs/Kelly was the trigger case
    # where Happ + Suzuki + Bregman all PRIME but silently capped). For
    # those games, lift the cap to 6 so all the stack picks surface and
    # tag them with stack_alert=True so the app/social can render a
    # "Stack Alert" badge.
    PRIME_THRESHOLD = 82
    prime_per_game = {}
    game_matchups = {}
    for p in all_props:
        if p['prop_type'] in ('hits_over', 'hits_under') and p['conviction'] >= PRIME_THRESHOLD:
            prime_per_game[p['game_id']] = prime_per_game.get(p['game_id'], 0) + 1
            game_matchups[p['game_id']] = p.get('matchup', p['game_id'])
    stack_games = {gid for gid, n in prime_per_game.items() if n >= 4}
    if stack_games:
        for gid in stack_games:
            print(f"  🔥 STACK ALERT: {game_matchups.get(gid)} has {prime_per_game[gid]} PRIME hits picks — lifting cap to 6")
    if prime_per_game:
        print(f"  PRIME hits-pick counts per game: {dict((game_matchups[g], n) for g, n in sorted(prime_per_game.items(), key=lambda x: -x[1]))}")
    # Set stack_alert on EVERY prop so the upsert batch has a uniform shape.
    # Default False; True only for hits picks in stack matchups.
    for p in all_props:
        p['stack_alert'] = (
            p['game_id'] in stack_games
            and p['prop_type'] in ('hits_over', 'hits_under')
        )

    # Bumped cap from min(25, len(games)+5) → min(40, len(games)*2) so a
    # 12-game slate has 24 slots instead of 17. Tighter cap was suppressing
    # late-game props (West Coast 9-10pm starters got cut before their
    # confirmed lineups even landed).
    top_n = min(40, max(12, len(games) * 2))
    hits_per_game = {}
    capped = []
    for p in all_props:
        if p['prop_type'] in ('hits_over', 'hits_under'):
            key = p['game_id']
            hits_per_game[key] = hits_per_game.get(key, 0) + 1
            # Stack Alert games get an expanded cap of 6 so all the PRIME
            # picks against the same matchup surface together as a stack.
            cap = 6 if key in stack_games else 3
            if hits_per_game[key] > cap:
                continue
        capped.append(p)
    top = capped[:top_n]

    # Per-game floor: every scheduled game gets at least one prop on the
    # board. Without this, late-night West Coast games show zero picks
    # because their PRIMEs get squeezed out by early-slate confluence.
    # Take the highest-conviction prop from each missing game (must clear
    # conviction ≥ 60 — don't force garbage onto the board just to fill).
    represented_games = {p['game_id'] for p in top}
    all_game_ids = {p['game_id'] for p in all_props}
    missing_games = all_game_ids - represented_games
    if missing_games:
        cut_props = [p for p in capped if p not in top]
        # capped maintains conviction-desc order, so first match per game = best
        added = 0
        for p in cut_props:
            if p['game_id'] in missing_games and p['conviction'] >= 60:
                top.append(p)
                missing_games.discard(p['game_id'])
                added += 1
                if not missing_games:
                    break
        if added:
            print(f"  📋 Per-game floor: added {added} prop(s) to surface late-slate games")

    wipe_todays_props()
    saved = upsert_props(top)
    print(f"\n✅ Stored {saved} top props (of {len(all_props)} passing threshold)")
    for p in top[:8]:
        print(f"  [{p['conviction']}] {p['player_name']} {p['prop_type']} {p['prop_line']} ({p['tier']}) — {p['matchup']}")
        for k, v in p['signals'].items():
            print(f"      · {v}")

if __name__ == "__main__":
    run()
