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
import unicodedata
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv


def _norm_name(s):
    """Accent-fold + lowercase a name so 'Cristopher Sánchez' (MLB Stats API)
    matches 'Cristopher Sanchez' (Odds API). 2026-06-03 fix — Sánchez had 4
    SKIP-tier props tonight because the book_map lookup was exact-lowercase
    against an accented MLB-side name, missing the unaccented Odds API key.
    NFD decomposes accented chars into base + combining mark, then we drop
    any code point in the Unicode "Mn" (Mark, Nonspacing) category."""
    if not s:
        return ''
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(s).lower())
        if unicodedata.category(c) != 'Mn'
    ).strip()

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# Reconfigure stdout for UTF-8 on Windows (cp1252 default crashes on emoji
# in print statements when run locally; cron runs on Linux so no-op there).
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
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

# 2026-06-21 RECAL — fresh 90d audit (n=48 graded outs_under, n=43 graded
# outs_over) found the threshold structure backwards from where the edge
# actually lives:
#   outs_under: 44-4 (92%) across ALL tiers — even SKIP tier (31 picks
#     below the publish cutoff) hit 30-1 (97%). The scorer was suppressing
#     the loudest prop cohort in the library. CUTOFF dropped 70→50 so the
#     LEAN band actually publishes, and per-tier thresholds shifted in
#     tier_for() so PRIME tier becomes reachable.
#   outs_over: 2-41 (5%) across all tiers — destroyed. Even PRIME-tier
#     outs_over was 0-2. CUTOFF raised 65→78 to suppress all but the
#     loudest setups; better to publish nothing than to publish a 5% trap.
OUTS_CUTOFF = 78        # was 65 — outs_over has been a 5% fade
OUTS_UNDER_CUTOFF = 50  # was 70 — outs_under has been a 92% smash, surface it
ER_CUTOFF = 65
ER_UNDER_CUTOFF = 70
# New 2026-05-11 — Pitcher Walks Allowed Over/Under. Walks are higher-variance
# than Ks. Scoring scale tops ~56-60 for strong cases (base 30 + conservative
# increments), so cutoff is 55. No audit data yet — recalibrate at n=20+.
BB_CUTOFF = 48  # lowered from 55 (2026-05-13) — BB OVER props were systemically under-surfacing
BB_UNDER_CUTOFF = 48  # lowered from 55 (2026-05-13) — BB props were systemically under-surfacing
# New 2026-05-11 — Pitcher Hits Allowed Over/Under. Same projection-first
# design as BB props, line typically 5.5. Cutoff 55, recalibrate at n=20+.
HA_CUTOFF = 55
HA_UNDER_CUTOFF = 55


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


def detect_scratched_starters(games, gd):
    """Compare each game's stored home_pitcher / away_pitcher to MLB Stats
    API's current probable pitchers, flagging any mismatches.

    2026-06-01 trigger — Chase Burns was listed as the morning probable
    for CIN @ KC and props were scored against his K-artist profile (PRIME
    91 K-Over, PRIME 72 HA-Under). By 2pm CT he was scratched and replaced.
    The props pipeline kept publishing Burns-anchored props because nothing
    re-checked the probable pitcher. KC hitter unders (Loftin, Marte) were
    also calibrated against Burns; with a different starter their thesis
    weakens.

    Returns: set of (game_id, scratched_pitcher_name, replacement_name) tuples.
    Caller (run) iterates and demotes affected props to SKIP.

    Fails open (returns empty set) on any error so a transient MLB Stats
    API hiccup doesn't kill the pipeline. The logging still surfaces in
    cron output so the failure is visible.
    """
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": gd, "hydrate": "probablePitcher"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  ⚠️  Probable-pitcher refetch returned {r.status_code} — skipping scratch detection")
            return set()
        schedule = r.json()
    except Exception as e:
        print(f"  ⚠️  Probable-pitcher refetch failed: {type(e).__name__}: {e} — skipping scratch detection")
        return set()

    # Map MLB schedule by (home_team, away_team) -> (home_probable, away_probable)
    current_starters = {}
    for d in schedule.get("dates", []):
        for sg in d.get("games", []):
            teams = sg.get("teams") or {}
            home_team = (teams.get("home") or {}).get("team", {}).get("name", "")
            away_team = (teams.get("away") or {}).get("team", {}).get("name", "")
            home_pp = ((teams.get("home") or {}).get("probablePitcher") or {}).get("fullName")
            away_pp = ((teams.get("away") or {}).get("probablePitcher") or {}).get("fullName")
            if home_team and away_team:
                current_starters[(home_team, away_team)] = (home_pp, away_pp)

    scratched = set()
    for g in games:
        key = (g.get('home_team'), g.get('away_team'))
        current = current_starters.get(key)
        if not current:
            continue
        cur_home_pp, cur_away_pp = current
        db_home = g.get('home_pitcher')
        db_away = g.get('away_pitcher')
        gid = g.get('game_id')
        if cur_home_pp and db_home and cur_home_pp.strip() != db_home.strip():
            scratched.add((gid, db_home, cur_home_pp))
            print(f"  🚨 STARTER CHANGE: {g.get('away_team')} @ {g.get('home_team')} — home pitcher {db_home} → {cur_home_pp}")
        if cur_away_pp and db_away and cur_away_pp.strip() != db_away.strip():
            scratched.add((gid, db_away, cur_away_pp))
            print(f"  🚨 STARTER CHANGE: {g.get('away_team')} @ {g.get('home_team')} — away pitcher {db_away} → {cur_away_pp}")
    return scratched


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


# ─────────────────────────────────────────────────────────────────────────
# LIVE CALIBRATION FILTER (2026-07-12)
# ─────────────────────────────────────────────────────────────────────────
# Reads prop_edge_calibration table, demotes KILL-bucket (tier,prop_type)
# picks to SKIP after tier_for() assigns. Table is refreshed nightly by
# prop_edge_calibrator.py from a rolling 30-day window.
#
# Filter is post-tier: tier_for computes the base tier from conviction, then
# apply_calibration_filter demotes to SKIP if that bucket has < 45% historical
# hit rate on n >= 10 samples. Fails open (no filter) if table unreachable.
_CALIBRATION_CACHE = None
_CALIBRATION_MIN_SAMPLE_FOR_DEMOTE = 10  # don't demote on thin samples


def _load_calibration():
    """Fetch most recent prop_edge_calibration rows. Cached per-run."""
    global _CALIBRATION_CACHE
    if _CALIBRATION_CACHE is not None:
        return _CALIBRATION_CACHE
    _CALIBRATION_CACHE = {}
    try:
        headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/prop_edge_calibration"
            f"?order=computed_at.desc"
            f"&select=tier,prop_type,category,hit_rate,sample_size,computed_at"
            f"&limit=200",
            headers=headers, timeout=10,
        )
        if r.status_code != 200:
            print(f"  ⚠️  Calibration table unreachable ({r.status_code}) — no bucket filter applied")
            return _CALIBRATION_CACHE
        rows = r.json() if isinstance(r.json(), list) else []
        if not rows:
            print(f"  ⚠️  Calibration table empty — no bucket filter applied")
            return _CALIBRATION_CACHE
        latest = rows[0].get('computed_at')
        for row in rows:
            if row.get('computed_at') != latest:
                continue
            key = (row['tier'], row['prop_type'])
            _CALIBRATION_CACHE[key] = {
                'category': row['category'],
                'hit_rate': float(row['hit_rate']),
                'sample_size': int(row['sample_size']),
            }
        print(f"  ✓ Loaded prop calibration: {len(_CALIBRATION_CACHE)} buckets from {latest}")
    except Exception as e:
        print(f"  ⚠️  Calibration load exception: {e} — no bucket filter applied")
    return _CALIBRATION_CACHE


def apply_calibration_filter(tier, prop_type):
    """Post-tier filter: demote KILL-bucket (tier, prop_type) picks to SKIP.
    Requires sample_size >= _CALIBRATION_MIN_SAMPLE_FOR_DEMOTE so we don't
    fade on noise. NEUTRAL and KEEP buckets pass through untouched — KEEP
    promotion is a separate follow-up (surface flag) rather than a tier bump.
    """
    if tier == 'SKIP':
        return tier
    cal = _load_calibration()
    entry = cal.get((tier, prop_type))
    if entry and entry['category'] == 'KILL' and entry['sample_size'] >= _CALIBRATION_MIN_SAMPLE_FOR_DEMOTE:
        return 'SKIP'
    return tier


def _tier_for_raw(conviction, prop_type=None):
    """Raw conviction → tier assignment. Wrapped by tier_for() which applies
    the live calibration filter. Kept separate so tests + audits can inspect
    the pre-filter tier if needed.
    """
    # 2026-06-03: restored LEAN tier for K props (55-69 conviction). The
    # original audit said "Ks barely beat coin flip <70" but that was on
    # n=23 and the side-effect was hiding half the K slate when the prop
    # exists with a real book line. UX cost (users see less surface area)
    # exceeded the audit benefit (suppressing a marginal cohort).
    # The 55-69 LEAN band surfaces as informational/projection-only in the
    # app — STRONG and PRIME tiers still gate card-grade picks.
    if prop_type == 'ks_over':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        if conviction >= 55: return 'LEAN'
        return 'SKIP'
    if prop_type == 'ks_under':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        if conviction >= 55: return 'LEAN'
        return 'SKIP'
    if prop_type == 'hits_under':
        # 0-fer is a long shot — only PRIME/STRONG, no LEAN noise
        if conviction >= 85: return 'PRIME'
        if conviction >= 75: return 'STRONG'
        return 'SKIP'
    # 2026-06-21 RECAL on n=48 outs_under / n=43 outs_over graded picks
    # over 90d. outs_under has been a 92% smash across every tier the
    # scorer surfaced (and 97% on the SKIP tier we never published) —
    # thresholds shifted down so STRONG/PRIME become reachable from the
    # actual signal range the scorer produces (base 30, max realistic ~70).
    # outs_over stayed on the conservative Ks ladder since it's been a
    # 5% trap across the same window.
    if prop_type == 'outs_under':
        if conviction >= 65: return 'PRIME'
        if conviction >= 55: return 'STRONG'
        if conviction >= 50: return 'LEAN'
        return 'SKIP'
    # 2026-06-22 ER threshold tightening. 90d audit found:
    #   er_over STRONG (conv 70-81): 48% (n=27) — sub-baseline
    #   er_under STRONG (conv 70-81): 46% (n=13) — sub-baseline
    # PRIME tiers stay loud (er_over 64% / er_under 73%) so the existing
    # PRIME 82+ floor is correct. The STRONG band is where the loss is
    # — raise the STRONG floor from 70 to 76 to filter out the middling
    # cases that hit at coinflip.
    if prop_type == 'outs_over':
        if conviction >= 82: return 'PRIME'
        if conviction >= 70: return 'STRONG'
        return 'SKIP'
    if prop_type in ('er_over', 'er_under'):
        if conviction >= 82: return 'PRIME'
        if conviction >= 76: return 'STRONG'  # was 70 — removed losing middle band
        return 'SKIP'
    # 2026-06-22 ha_under STRONG/LEAN extinction. 90d audit:
    #   PRIME ha_under: 58% (n=62) — real edge, keep
    #   STRONG ha_under: 47% (n=38) — LOSING money
    #   LEAN ha_under: 38% (n=26) — clear FADE
    #   SKIP ha_under: 50% (n=36) — better than STRONG/LEAN!
    # The tier ladder is INVERTED. PRIME is the only band with edge.
    # Collapse STRONG + LEAN into SKIP so only PRIME publishes.
    if prop_type == 'ha_under':
        if conviction >= 70: return 'PRIME'
        return 'SKIP'  # STRONG (47%) + LEAN (38%) tiers were systematically losing
    if prop_type in ('bb_over', 'bb_under', 'ha_over'):
        # New 2026-05-11 — walks + hits-allowed are higher-variance pitcher
        # props. Scoring tops ~60 for strong cases so thresholds scaled down.
        if conviction >= 70: return 'PRIME'
        if conviction >= 55: return 'STRONG'
        return 'SKIP'
    # Default / hits_over
    if conviction >= 82: return 'PRIME'
    if conviction >= 72: return 'STRONG'
    if conviction >= 55: return 'LEAN'
    return 'SKIP'


def tier_for(conviction, prop_type=None):
    """Public tier_for(): raw tier from conviction, then live-calibration
    KILL-bucket filter demoting to SKIP. 2026-07-12 addition — see
    apply_calibration_filter() docstring for demote rules.
    """
    return apply_calibration_filter(_tier_for_raw(conviction, prop_type), prop_type)


_PITCHER_BUCKET_CACHE = {}
_BATTER_L7_CACHE = {}
_BATTER_ID_CACHE = {}
_BATTER_QUALITY_CACHE = None  # one-time leaderboard fetch, keyed by name lower


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


def fetch_batter_quality(player_name, season=2026):
    """Per-batter Statcast quality-of-contact lookup.
    Returns dict {barrel_pct, hard_hit_pct} or None.

    Used by hits Over/Under scoring as a luck-vs-skill differentiator:
    a batter on a 0-fer streak with high barrel% is hitting the ball hard
    but unlucky (due for regression) — fade the hits-UNDER, boost the
    hits-OVER. A batter with no barrels and a cold streak is genuinely
    slumping — the hits-UNDER fade is real.

    Hits Savant's batter leaderboard once per process and caches the full
    table. Same source build_hr_watch.py uses (single CSV fetch)."""
    global _BATTER_QUALITY_CACHE
    if _BATTER_QUALITY_CACHE is None:
        try:
            import io
            import pandas as pd
            url = (
                f"https://baseballsavant.mlb.com/leaderboard/statcast"
                f"?type=batter&year={season}&position=&team=&min=q&csv=true"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }, timeout=30)
            if r.status_code != 200:
                _BATTER_QUALITY_CACHE = {}
                return None
            df = pd.read_csv(io.StringIO(r.text))
            lookup = {}
            for _, row in df.iterrows():
                nm = str(row.get("last_name, first_name", "") or "")
                if "," not in nm:
                    continue
                last, first = [p.strip() for p in nm.split(",", 1)]
                key = f"{first} {last}".lower()
                # Savant renamed these fields sometime after 5/11/2026 —
                # legacy `barrel_batted_rate`/`hard_hit_percent` silently
                # returned None for 12 days. Use new + legacy fallback.
                try:
                    raw_b = row.get("brl_percent")
                    if raw_b is None or (isinstance(raw_b, float) and raw_b != raw_b):
                        raw_b = row.get("barrel_batted_rate")
                    barrel = float(raw_b) if raw_b is not None and str(raw_b) not in ('', 'nan') else None
                except (TypeError, ValueError):
                    barrel = None
                try:
                    raw_h = row.get("ev95percent")
                    if raw_h is None or (isinstance(raw_h, float) and raw_h != raw_h):
                        raw_h = row.get("hard_hit_percent")
                    hard = float(raw_h) if raw_h is not None and str(raw_h) not in ('', 'nan') else None
                except (TypeError, ValueError):
                    hard = None
                lookup[key] = {"barrel_pct": barrel, "hard_hit_pct": hard}
            _BATTER_QUALITY_CACHE = lookup
            with_barrel = sum(1 for v in lookup.values() if v.get("barrel_pct") is not None)
            print(f"  Loaded Statcast barrel%/hard hit% for {len(lookup)} batters ({with_barrel} with non-null barrel%)")
            # 5/24 schema-break catch: if barrel_pct is None for the whole
            # leaderboard, Savant probably renamed the column again.
            if len(lookup) > 0 and with_barrel == 0:
                print("  ⚠️  Savant returned 0 batters with barrel_pct — column rename suspected, signals will be dead")
        except Exception as e:
            print(f"  ⚠️  Savant batter quality fetch failed: {e}")
            _BATTER_QUALITY_CACHE = {}
    return _BATTER_QUALITY_CACHE.get((player_name or '').lower())


_PITCHER_PROJ_CACHE = None  # loaded from data/pitcher_class_projections.json


# ── Bayesian blend of L7 rolling vs season baseline (2026-08-06) ────
# Root cause of Buehler BB over 2.5 miscalibration: score_pitcher_bb_over
# used l7_rolling.avg_bb DIRECTLY (3.0 walks/start for Buehler) while the
# game-context field projected 2.1 — a 40% divergence Jerry then had to
# pick from and always picked the aggressive one that justified the pick.
#
# The 3.0 came from 7 short starts (avg 4.5 IP). Season baseline computed
# from classes was 2.2. Proper Bayesian blend with a 20-start prior gives
# 2.4 — closer to the game-context number, less trap-prone.
#
# Prior weight of 20 starts = "we trust the season baseline about as much
# as 20 recent starts". Small enough that a real change in form (10-12
# starts of new data) shifts the blend materially, large enough that a
# 7-start hot streak doesn't stampede the projection.
_SEASON_PRIOR_N_STARTS = 20


def _season_baseline_from_classes(proj: dict, stat_key: str):
    """Weighted mean of a stat across pitcher_projections.classes buckets.

    stat_key: 'avg_bb' | 'avg_ks' | 'avg_hits' | 'avg_er' | 'avg_outs'
    Returns (baseline_value, total_n_starts) or (None, 0) if unavailable.
    Classes are opponent-strength buckets (91_100 = league-avg offense,
    101_110 = above-avg offense, etc.). Weighting by n gives season avg.
    """
    if not proj: return None, 0
    classes = proj.get('classes') or {}
    if not classes: return None, 0
    total_n, total_val = 0, 0.0
    for _label, bucket in classes.items():
        if not isinstance(bucket, dict): continue
        n = bucket.get('n')
        v = bucket.get(stat_key)
        try: n = int(n); v = float(v)
        except (TypeError, ValueError): continue
        if n <= 0: continue
        total_n += n
        total_val += v * n
    if total_n == 0: return None, 0
    return total_val / total_n, total_n


def _bayes_blend_l7_season(l7_val, l7_n, season_val, prior_n: int = _SEASON_PRIOR_N_STARTS):
    """Bayesian-style weighted blend: (l7 × l7_n + season × prior_n) / (l7_n + prior_n).

    Returns blended value. If either input is None, returns the other.
    Both None → None. Handles Buehler-style hot-streak inflation without
    overriding real form changes once l7_n climbs above prior_n.
    """
    try:
        l7_val = float(l7_val) if l7_val is not None else None
        l7_n = int(l7_n) if l7_n is not None else 0
        season_val = float(season_val) if season_val is not None else None
    except (TypeError, ValueError):
        return None
    if l7_val is None and season_val is None: return None
    if l7_val is None: return season_val
    if season_val is None: return l7_val
    if l7_n <= 0: return season_val
    return (l7_val * l7_n + season_val * prior_n) / (l7_n + prior_n)


def get_pitcher_projection(name):
    """Load the pitcher class-projection JSON cache (built by
    compute_pitcher_class_projections.py) and return this pitcher's entry
    (with l7_rolling + classes) keyed by lowercased name. Returns None if
    not found or cache missing.

    Falls back to Supabase `pitcher_projections` table when the JSON misses
    (e.g., late-confirmed starter not yet in this morning's JSON build).
    Painter ER-over miss on 5/13 highlighted this gap — JSON had 29 entries,
    Painter wasn't one of them, but his row was in Supabase from the more
    recent compute step. Supabase fallback closes the loop."""
    global _PITCHER_PROJ_CACHE, _PITCHER_PROJ_SB_MISSES
    if _PITCHER_PROJ_CACHE is None:
        try:
            import json
            from pathlib import Path
            cache_path = Path(__file__).parent / 'data' / 'pitcher_class_projections.json'
            if cache_path.exists():
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                _PITCHER_PROJ_CACHE = {v['name'].lower(): v for v in data.values() if v.get('name')}
            else:
                _PITCHER_PROJ_CACHE = {}
        except Exception:
            _PITCHER_PROJ_CACHE = {}
    try:
        _PITCHER_PROJ_SB_MISSES  # type: ignore[name-defined]
    except NameError:
        _PITCHER_PROJ_SB_MISSES = set()  # cache misses we've already tried Supabase for

    key = (name or '').lower()
    hit = _PITCHER_PROJ_CACHE.get(key)
    if hit is not None:
        return hit
    # Supabase fallback — only attempted once per pitcher per run
    if not name or key in _PITCHER_PROJ_SB_MISSES:
        return None
    _PITCHER_PROJ_SB_MISSES.add(key)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        import urllib.parse, urllib.request, json as _json
        qs = urllib.parse.urlencode({
            'select': 'pitcher_name,l7_rolling,classes',
            'pitcher_name': f'eq.{name}',
        })
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/pitcher_projections?{qs}",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        )
        rows = _json.load(urllib.request.urlopen(req, timeout=8))
        if rows:
            row = rows[0]
            entry = {
                'name': row.get('pitcher_name'),
                'l7_rolling': row.get('l7_rolling') or {},
                'classes': row.get('classes') or {},
            }
            _PITCHER_PROJ_CACHE[key] = entry  # cache for the rest of this run
            return entry
    except Exception:
        pass
    return None


def _apply_l5_signal(g, side, metric, line, direction, conviction, signals):
    """Apply L5 confirm/fade signal to a prop scorer. Returns the updated
    conviction; mutates `signals` in place.

    metric: one of 'outs', 'ks', 'bb', 'hits', 'er'

    2026-06-22 — per-prop l5_confirm gating after Phase 2 backtest.
    Signal-level audit showed l5_confirm as a -6.7pt fade across most
    prop types, but TIER-level backtest (_backtest_l5_confirm_removal.py)
    found that removing the conviction bonus only IMPROVES per-tier hit
    rates for two prop_types. For all others, removal either demoted
    winners (ks_over) or made no measurable difference.

    Backtest verdicts (90d, n=40-646 per type):
      VALIDATED REMOVAL (l5_confirm bonus → 0):
        bb_over    — PRIME 63%→65%, STRONG 56%→59% (+2.2/+2.6pt)
        outs_under — PRIME 89%→91% (n=9→44, growth from rightful promotion)

      KEEP (no validated improvement):
        ks_over, ks_under, ha_under, ha_over, bb_under,
        outs_over, er_over, er_under, hits_over, hits_under

    Original logic (added 2026-06-07 after 6/7 card where Baz outs O17.5 +
    Flaherty hits O4.5 + Cameron ER U2.5 all had loud L5 actuals):
      - L5 avg strongly confirms direction (>=0.7 vs line on same side):  +12
      - L5 avg modestly confirms (>=0.3 vs line on same side):            +6
      - L5 avg disagrees (>=0.5 vs line on OTHER side):                   -8
      - Bonus: 4-of-5 or 5-of-5 streak on the bet direction:              +4
      - No data → no change

    L5 is a CO-SIGNATURE check, not a primary signal.
    """
    try:
        from pitcher_l5_lookup import get_l5, streak_count
    except ImportError:
        return conviction
    l5_payload = get_l5(g.get('game_date'), g.get('game_id'))
    if not l5_payload:
        return conviction
    side_l5 = l5_payload.get(side)
    if not side_l5:
        return conviction
    avg = (side_l5.get('avg') or {}).get(metric)
    if avg is None or line is None:
        return conviction
    margin = avg - line if direction == 'over' else line - avg
    streak_hits, streak_total = streak_count(side_l5, metric, line, direction)
    streak_label = f' ({streak_hits}-of-{streak_total} L5)' if streak_total else ''
    # Per-prop gate: l5_confirm bonus zero'd for prop types where the
    # tier-level backtest didn't show improvement. Narrative still surfaces
    # so the user sees the L5 number — only the conviction-point inflation
    # is removed.
    l5_confirm_pays_conviction = (metric, direction) not in {
        ('bb', 'over'),    # validated removal: bb_over PRIME 63→65%
        ('outs', 'under'), # validated removal: outs_under PRIME 89→91%
    }
    if margin >= 0.7:
        signals['l5_confirm'] = f'L5 avg {avg} ({direction} {line}, {margin:+.1f}){streak_label}'
        if l5_confirm_pays_conviction:
            conviction += 12
            if streak_total and streak_hits >= 4:
                conviction += 4  # streak bonus when 4/5+ on the same side
    elif margin >= 0.3:
        signals['l5_confirm'] = f'L5 avg {avg} ({direction} {line}, {margin:+.1f}){streak_label}'
        if l5_confirm_pays_conviction:
            conviction += 6
    elif margin <= -0.5:
        signals['l5_fade'] = f'⚠ L5 avg {avg} opposes (going {direction} {line}, gap {margin:+.1f}){streak_label}'
        conviction -= 8
    return conviction


def score_pitcher_bb_over(g, side):
    """Score a starter's Total Walks Allowed OVER prop. Markets typically
    post O/U 1.5 (sometimes 2.5). We project from L7 rolling avg_bb when
    available (the strongest recency signal), then adjust for opp lineup
    patience, first-inning control, days rest.

    The projection (`_projected_bb` in signals) is the headline number the
    app surfaces — 'expected ~2.1 walks' — so the user can shop their book."""
    pitcher = g.get(f'{side}_pitcher')
    if not pitcher:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    # Opener filter
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None

    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    if not l7 or l7.get('avg_bb') is None:
        return None  # no projection basis = no walks prop
    if l7.get('avg_ip', 0) < 3.0:
        return None  # opener / short-relief profile

    # 2026-08-06 Bayesian blend: raw L7 avg was inflating projections on
    # small-sample hot streaks (Buehler 7 short starts → 3.0 BB/start when
    # season baseline was 2.2). Blend with season baseline from classes.
    l7_avg_bb = l7['avg_bb']
    l7_n = l7.get('n_starts') or 7
    season_bb, season_n = _season_baseline_from_classes(proj, 'avg_bb')
    proj_bb = _bayes_blend_l7_season(l7_avg_bb, l7_n, season_bb) or l7_avg_bb

    opp_side = 'away' if side == 'home' else 'home'
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22  # patient-vs-aggressive proxy (lower K = more contact-y)
    first_inn_whip = _f(g.get(f'{side}_first_inning_whip'))
    days_rest = _f(g.get(f'{side}_days_rest'))

    signals = {}
    signals['_projected_bb'] = round(proj_bb, 1)
    signals['_projected_bb_l7_raw'] = round(l7_avg_bb, 1)
    if season_bb is not None:
        signals['_projected_bb_season'] = round(season_bb, 2)
    conviction = 30

    # Primary: how far is blended walk rate above the 1.5 line?
    # Thresholds widened 2026-05-13 — pre-patch only Schultz-style outliers
    # (L7 BB ≥3.0) cleared the +28 bonus; middling walks-prone arms like
    # McCullers (2.86) and Bradish (2.71) got stuck at slim-edge tier and
    # never cleared cutoff 55. New tiers: 0.7/0.4/0.15 (was 1.0/0.5/0.2).
    over_margin = proj_bb - 1.5
    if over_margin >= 0.7:
        conviction += 28
        signals['last7_walks'] = f'last 7 starts avg {l7_avg_bb:.1f} BB/start · blended proj {proj_bb:.1f} — {over_margin:+.1f} vs 1.5 line'
    elif over_margin >= 0.4:
        conviction += 18
        signals['last7_walks'] = f'last 7 starts avg {l7_avg_bb:.1f} BB/start · blended proj {proj_bb:.1f} — {over_margin:+.1f} vs 1.5 line'
    elif over_margin >= 0.15:
        conviction += 8
        signals['last7_walks'] = f'last 7 starts avg {l7_avg_bb:.1f} BB/start · blended proj {proj_bb:.1f} — slim edge over 1.5'
    else:
        return None  # not enough walk volume to bet the over

    # BB/9 layer — corroborates the per-start average (raw L7, informational)
    bb9 = l7.get('bb_per_9')
    if bb9 is not None:
        if bb9 >= 4.0:
            conviction += 10
            signals['bb_rate'] = f'{bb9:.1f} BB/9 over last 7 starts — elevated walk rate'
        elif bb9 <= 2.0:
            conviction -= 8

    # First-inning control — wild starts inflate walk totals
    if first_inn_whip is not None and first_inn_whip >= 1.6:
        conviction += 6
        signals['wild_start'] = f'1st-inn WHIP {first_inn_whip:.2f} — shaky control early'

    # Opp lineup patience proxy — lower team K% ≈ more pitches seen ≈ more walk chances
    if opp_k_pct <= 20:
        conviction += 5
        signals['patient_opp'] = f'Opp K% {opp_k_pct:.1f}% — works counts'
    elif opp_k_pct >= 27:
        conviction -= 4  # aggressive lineup, fewer walks drawn

    # Rust — extra rest can mean shakier command first time back
    if days_rest is not None and days_rest >= 7:
        conviction += 3
        signals['rest_rust'] = f'{int(days_rest)} days rest — possible command rust'

    conviction = max(0, min(100, conviction))
    # Suggested line: 1.5 unless the projection is well above 2.5
    suggested_line = 2.5 if proj_bb >= 3.0 else 1.5
    conviction = _apply_l5_signal(g, side, 'bb', suggested_line, 'over', conviction, signals)
    conviction = max(0, min(100, conviction))

    # 2026-07-27 cohort audit: bb_over PRIME hit 29% (n=7 7d), STRONG 50%
    # (n=16 7d), LEAN 25% (n=4 7d). No tier has an edge — every level is
    # coin flip or worse. Cap conviction at 65 so tier never reaches
    # STRONG (70+) or PRIME (82+). Keeps LEAN available for elite-walk
    # matchups (BB/9 >= 4.5, first-inn WHIP >= 1.8) where a small L5-lift
    # signal might genuinely fire, but prevents systemic false-PRIMEs.
    # Recalibrate at n=25+ per tier.
    if conviction > 65:
        signals['_cohort_cap'] = 'bb_over cohort weak (PRIME 29% / STRONG 50% 7d) — capped at LEAN'
        conviction = 65

    return {'conviction': conviction, 'signals': signals, 'prop_line': suggested_line}


def score_pitcher_bb_under(g, side):
    """Score a starter's Total Walks Allowed UNDER prop — bet on elite
    control. Hits when L7 walk rate is comfortably below the line and the
    pitcher has a low BB/9 with no recent command wobble."""
    pitcher = g.get(f'{side}_pitcher')
    if not pitcher:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None

    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    if not l7 or l7.get('avg_bb') is None:
        return None
    if l7.get('avg_ip', 0) < 4.0:
        return None  # need a real innings load to bet UNDER on walks

    # 2026-08-06 Bayesian blend — symmetric to bb_over. A 7-start "elite
    # control" streak from a career-average command guy shouldn't drive
    # PRIME UNDER; blend to season baseline to catch true low-BB arms only.
    l7_avg_bb = l7['avg_bb']
    l7_n = l7.get('n_starts') or 7
    season_bb, _ = _season_baseline_from_classes(proj, 'avg_bb')
    proj_bb = _bayes_blend_l7_season(l7_avg_bb, l7_n, season_bb) or l7_avg_bb

    opp_side = 'away' if side == 'home' else 'home'
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22
    first_inn_whip = _f(g.get(f'{side}_first_inning_whip'))

    signals = {}
    signals['_projected_bb'] = round(proj_bb, 1)
    signals['_projected_bb_l7_raw'] = round(l7_avg_bb, 1)
    if season_bb is not None:
        signals['_projected_bb_season'] = round(season_bb, 2)
    conviction = 30

    # Primary: how far is blended walk rate below the 1.5 line?
    # Thresholds widened 2026-05-13 — pre-patch most pitchers projecting
    # 1.1-1.4 BB/start (clean BB-Unders) got stuck at +6 and never cleared
    # cutoff 55. New tiers: 0.4/0.2/0.1 (was 0.7/0.4/0.2). This surfaces
    # the natural cohort of low-walk arms (Lodolo 1.14, Gray 1.29, Ohtani
    # 1.29, B. Miller 1.29, Imanaga 1.57, Messick 1.57) which all clearly
    # project under the standard 1.5 BB line.
    under_margin = 1.5 - proj_bb
    if under_margin >= 0.4:
        conviction += 28
        signals['last7_control'] = f'last 7 starts avg {l7_avg_bb:.1f} BB/start · blended proj {proj_bb:.1f} — elite control, {under_margin:.1f} under 1.5'
    elif under_margin >= 0.2:
        conviction += 18
        signals['last7_control'] = f'last 7 starts avg {l7_avg_bb:.1f} BB/start · blended proj {proj_bb:.1f} — {under_margin:.1f} under 1.5'
    elif under_margin >= 0.05:
        conviction += 8
        signals['last7_control'] = f'last 7 starts avg {l7_avg_bb:.1f} BB/start · blended proj {proj_bb:.1f} — slim edge under 1.5'
    else:
        return None  # walks too high to bet the under

    bb9 = l7.get('bb_per_9')
    if bb9 is not None:
        if bb9 <= 2.0:
            conviction += 12
            signals['bb_rate'] = f'{bb9:.1f} BB/9 over last 7 starts — elite command'
        elif bb9 >= 4.0:
            conviction -= 10

    # Clean first-inning control corroborates
    if first_inn_whip is not None and first_inn_whip <= 1.0:
        conviction += 6
        signals['clean_start'] = f'1st-inn WHIP {first_inn_whip:.2f} — pounds the zone'

    # Aggressive opp lineup helps the under (chase more, walk less)
    if opp_k_pct >= 25:
        conviction += 5
        signals['aggressive_opp'] = f'Opp K% {opp_k_pct:.1f}% — aggressive, low walk draw'
    elif opp_k_pct <= 18:
        conviction -= 5  # patient lineup, more walk risk

    conviction = max(0, min(100, conviction))
    suggested_line = 1.5  # under-1.5 is the standard book line
    conviction = _apply_l5_signal(g, side, 'bb', suggested_line, 'under', conviction, signals)
    conviction = max(0, min(100, conviction))

    return {'conviction': conviction, 'signals': signals, 'prop_line': suggested_line}


def _blended_projected_hits(l7, g, side):
    """Blended hits projection — replaces raw L7 mean.

    2026-07-21 fix: previous formula was `proj_h = l7['avg_hits']` (raw mean of
    last 7 dated starts). This ignored regression to season mean, opp quality,
    and park factor. Result: 5-for-5 HA UNDER losses on 7/19-7/20 (Yesavage/
    Griffin/Holmes/Bibee all "hot streak" pitchers whose L7 mean got baked in
    as truth, then Regression 101 destroyed the picks). See
    [[project_hits_allowed_calibration_719]] for the audit chain.

    New formula:
      season_h  = xERA-derived BAA proxy × proj_ip × 4.3 BF/IP
      w_l7      = 0.30..0.55 based on L7 sample size AND divergence from season
      proj      = w_l7 * l7_h + (1 - w_l7) * season_h
      proj    *= (1 + (opp_wrc - 100) * 0.004)     # opp lineup multiplier
      proj    *= (1 + (park - 100) * 0.005)         # park factor multiplier
    """
    l7_h = l7.get('avg_hits')
    if l7_h is None:
        return None
    n = l7.get('n_starts') or 0
    proj_ip = l7.get('avg_ip') or 5.5

    # Season-anchored baseline.
    # 2026-08-15: prefer xba_allowed (Statcast expected BA) — strips luck
    # noise vs raw baa_allowed. baa fallback when xba missing (rookies).
    # xERA-derived proxy is last resort.
    pitcher_name = g.get(f'{side}_pitcher')
    contact = fetch_pitcher_contact_quality(pitcher_name) or {}
    xba = contact.get('xba_allowed')
    baa = contact.get('baa_allowed')
    xera = _f(g.get(f'{side}_sp_xera'))
    if xba is not None and 0.15 <= float(xba) <= 0.35:
        season_h = float(xba) * proj_ip * 4.3
    elif baa is not None and 0.15 <= float(baa) <= 0.35:
        season_h = float(baa) * proj_ip * 4.3
    elif xera is not None and 2.0 <= xera <= 8.0:
        season_baa = 0.045 * float(xera) + 0.055
        season_h = season_baa * proj_ip * 4.3
    else:
        # league starter baseline — 8.5 H/9
        season_h = proj_ip * 8.5 / 9.0

    # Shrinkage: heavier season weight when L7 sample thin OR L7 diverges hard
    # from season baseline (regression-risk gate).
    divergence = abs(l7_h - season_h)
    if n < 4:
        w_l7 = 0.30
    elif divergence >= 2.0:
        w_l7 = 0.35  # hot/cold streak — trust season more
    elif n >= 6:
        w_l7 = 0.55
    else:
        w_l7 = 0.45

    proj = w_l7 * l7_h + (1 - w_l7) * season_h

    # Opp lineup multiplier (~10 wRC+ pts ≈ 4% H swing)
    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    proj *= 1.0 + (opp_wrc - 100) * 0.004

    # Park multiplier (~50% of park_run swing is hits)
    park = _f(g.get('park_run_factor')) or 100
    proj *= 1.0 + (park - 100) * 0.005

    return round(proj, 1)


def score_pitcher_ha_over(g, side):
    """Score a starter's Hits Allowed OVER prop. Markets typically post O/U
    5.5 (sometimes 4.5 / 6.5). Projects from L7 rolling avg_hits, adjusts for
    opp lineup quality, park factor, recent hard-contact trend (L3 ERA)."""
    pitcher = g.get(f'{side}_pitcher')
    if not pitcher:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None
    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    if not l7 or l7.get('avg_hits') is None:
        return None
    if l7.get('avg_ip', 0) < 4.0:
        return None  # short profile — hits-over unreliable
    proj_h = _blended_projected_hits(l7, g, side)
    if proj_h is None:
        return None

    # 2026-08-08 EARLY-PULL ADJUSTMENT — ha_over went 0-5 on 8/7 because
    # bad-xERA pitchers projected for 5+ hits kept getting pulled at
    # 3 IP (giving up only 2-3 hits). The projected_hits number came
    # from a full-outing L7 average that assumed 5+ IP. If the pitcher
    # has been getting pulled early (last_ip low or projected_outs low
    # in game_context), scale hits down proportionally.
    #
    # Scale factor: expected_outs / typical_starter_outs (18).
    # If proj_outs = 12 (4 IP), scale = 0.67 → 5 hits becomes 3.3 hits.
    # Correlates with outs_under prop (which went 5-2 same night).
    proj_outs = _f(g.get(f'{side}_pitcher_projected_outs'))
    if proj_outs is not None and proj_outs > 0 and proj_outs < 18:
        pull_scale = proj_outs / 18.0
        proj_h_raw = proj_h
        proj_h = proj_h * pull_scale

    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))

    signals = {}
    signals['_projected_hits'] = round(proj_h, 1)
    if proj_outs is not None and proj_outs < 18:
        signals['_projected_hits_pre_pull_adjust'] = round(proj_h_raw, 1)
        signals['_projected_outs_used'] = proj_outs
        signals['_pull_scale'] = round(proj_outs/18.0, 2)
    conviction = 30
    LINE = 5.5
    over_margin = proj_h - LINE
    if over_margin >= 1.5:
        conviction += 28
        signals['l7_hits'] = f'L7 avg {proj_h:.1f} H/start — {over_margin:+.1f} vs 5.5 line'
    elif over_margin >= 0.7:
        conviction += 16
        signals['l7_hits'] = f'L7 avg {proj_h:.1f} H/start — {over_margin:+.1f} vs 5.5 line'
    elif over_margin >= 0.3:
        conviction += 6
        signals['l7_hits'] = f'L7 avg {proj_h:.1f} H/start — slim edge over 5.5'
    else:
        return None

    h9 = l7.get('hits_per_9')
    if h9 is not None:
        if h9 >= 10.0:
            conviction += 10
            signals['hits_rate'] = f'{h9:.1f} H/9 L7 — getting squared up'
        elif h9 <= 7.0:
            conviction -= 8

    if opp_wrc >= 110:
        conviction += 6
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — quality lineup'
    elif opp_wrc <= 90:
        conviction -= 5

    if l3_era is not None and l3_era >= 5.0:
        conviction += 5
        signals['l3_hard'] = f'L3 ERA {l3_era:.2f} — hit hard lately'

    # 2026-08-23 GAP CLOSE — park was silently influencing conviction but
    # never emitted a signal key. Coverage chip showed "park missing" on
    # Rockies pitchers because signals dict had no park entry despite the
    # math using park_run_factor. Now emits signal so card + coverage chip
    # actually reflect what the model is using.
    if park_run >= 115:
        conviction += 6
        signals['park'] = f'Extreme hitter park (run factor {park_run:.0f}) — Coors-class HA boost'
    elif park_run >= 108:
        conviction += 4
        signals['park'] = f'Hitter-friendly park ({park_run:.0f}) — HA over lean'
    elif park_run <= 92:
        conviction -= 3
        signals['park'] = f'Pitcher-friendly park ({park_run:.0f}) — HA over headwind'

    # 2026-08-23 GAP CLOSE — weather signal for HA. Hot temp (ball carries),
    # cool temp (dead ball). ctx.temperature is present but was never read.
    _temp = _f(g.get('temperature'))
    if _temp is not None:
        if _temp >= 88:
            conviction += 4
            signals['weather'] = f'Hot game-time {_temp:.0f}°F — ball carries, more hits'
        elif _temp <= 55:
            conviction -= 3
            signals['weather'] = f'Cold {_temp:.0f}°F — dead ball, hits suppressed'

    # 2026-08-23 GAP CLOSE — opposing bullpen quality. When a pitcher gets
    # pulled early (see pull_scale above), the OPP BP takes over. Weak
    # pen extends the hits count. Strong pen limits late damage.
    _opp_bp = _f(g.get(f'{opp_side}_bullpen_era'))
    if _opp_bp is not None:
        # For HA_OVER: we want to KNOW OUR OWN team's bullpen (comes in after
        # our pitcher), not opp's. But since HA is on OUR pitcher, use our
        # own team's bullpen (side=side, not opp_side).
        pass
    _own_bp = _f(g.get(f'{side}_bullpen_era'))
    if _own_bp is not None and _own_bp >= 5.0:
        conviction += 4
        signals['weak_pen'] = f'Own bullpen ERA {_own_bp:.2f} — late-inning damage adds HA'

    # vs-team BAA mastery — added 2026-05-24. When a pitcher has been
    # historically pummeled by this opponent (BAA >= .290), boost HA-over.
    # When historically dominant (BAA <= .215), fade.
    #
    # 2026-05-27 INCIDENT: gate added on minimum sample (≥15 IP). Matz @ BAL
    # was showing as .200 BAA "mastery" on only 9.7 IP (last 2 seasons) —
    # full career sample (38.3 IP) is actually .276 BAA / 4.23 ERA, neutral.
    # We publicly cited the small-sample number as a top play and it burned
    # us. The 15-IP gate eliminates that whole class of hot-streak false
    # positive.
    vt_baa = _f(g.get(f'{side}_pitcher_vs_team_avg'))
    vt_ip = _f(g.get(f'{side}_pitcher_vs_team_ip')) or 0
    if vt_baa is not None and vt_ip >= 15:
        if vt_baa >= 0.290:
            conviction += 10
            signals['vs_team_baa_anti'] = f'Career vs opp: {vt_baa:.3f} BAA on {vt_ip:.0f} IP — historically hit hard'
        elif vt_baa <= 0.215:
            conviction -= 6
            signals['vs_team_baa'] = f'Career vs opp: {vt_baa:.3f} BAA on {vt_ip:.0f} IP — opp has trouble making contact'

    conviction = max(0, min(100, conviction))
    suggested_line = 6.5 if proj_h >= 7.0 else 5.5
    conviction = _apply_l5_signal(g, side, 'hits', suggested_line, 'over', conviction, signals)
    conviction = max(0, min(100, conviction))
    return {'conviction': conviction, 'signals': signals, 'prop_line': suggested_line}


def score_pitcher_ha_under(g, side):
    """Score a starter's Hits Allowed UNDER prop — bet on a pitcher limiting
    contact. Hits when L7 hit rate is comfortably below 5.5 and the matchup
    (soft lineup, pitcher park) supports it."""
    pitcher = g.get(f'{side}_pitcher')
    if not pitcher:
        return None
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None
    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    if not l7 or l7.get('avg_hits') is None:
        return None
    if l7.get('avg_ip', 0) < 4.5:
        return None  # need real innings load for hits-under
    proj_h = _blended_projected_hits(l7, g, side)
    if proj_h is None:
        return None

    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))

    signals = {}
    signals['_projected_hits'] = round(proj_h, 1)
    conviction = 30
    LINE = 5.5
    under_margin = LINE - proj_h
    if under_margin >= 1.5:
        conviction += 28
        signals['l7_limit'] = f'L7 avg {proj_h:.1f} H/start — {under_margin:.1f} under 5.5'
    elif under_margin >= 0.7:
        conviction += 16
        signals['l7_limit'] = f'L7 avg {proj_h:.1f} H/start — {under_margin:.1f} under 5.5'
    elif under_margin >= 0.3:
        conviction += 6
        signals['l7_limit'] = f'L7 avg {proj_h:.1f} H/start — slim edge under 5.5'
    else:
        return None

    h9 = l7.get('hits_per_9')
    if h9 is not None:
        if h9 <= 7.0:
            conviction += 12
            signals['hits_rate'] = f'{h9:.1f} H/9 L7 — limits hard contact'
        elif h9 >= 10.0:
            conviction -= 10

    if opp_wrc <= 90:
        conviction += 6
        signals['opp_weak'] = f'Opp wRC+ {opp_wrc:.0f} — soft lineup'
    elif opp_wrc >= 115:
        conviction -= 6

    if l3_era is not None and l3_era <= 2.5:
        conviction += 5
        signals['l3_locked'] = f'L3 ERA {l3_era:.2f} — locked in'

    # 2026-08-23 GAP CLOSE — same silent-park-influence bug as ha_over,
    # inverse direction. Emit signal so coverage chip + card show the park
    # factor the model is using.
    if park_run <= 88:
        conviction += 6
        signals['park'] = f'Extreme pitcher park ({park_run:.0f}) — HA-under boost'
    elif park_run <= 92:
        conviction += 4
        signals['park'] = f'Pitcher-friendly park ({park_run:.0f}) — HA-under lean'
    elif park_run >= 108:
        conviction -= 3
        signals['park'] = f'Hitter-friendly park ({park_run:.0f}) — HA-under headwind'

    # 2026-08-23 GAP CLOSE — weather (inverse of ha_over)
    _temp = _f(g.get('temperature'))
    if _temp is not None:
        if _temp <= 55:
            conviction += 4
            signals['weather'] = f'Cold {_temp:.0f}°F — dead ball, hits suppressed'
        elif _temp >= 88:
            conviction -= 3
            signals['weather'] = f'Hot {_temp:.0f}°F — ball carries, HA-under headwind'

    # 2026-08-23 GAP CLOSE — own bullpen elite means late-inning damage minimal
    _own_bp = _f(g.get(f'{side}_bullpen_era'))
    if _own_bp is not None and _own_bp <= 3.50:
        conviction += 4
        signals['strong_pen'] = f'Own bullpen ERA {_own_bp:.2f} — late innings shut down'

    # vs-team BAA mastery — added 2026-05-24 per project_mastery_split_by_prop_type.
    # Previously score_pitcher_ha_under only used vs_team_era which is the
    # wrong dimension for hits-allowed props. BAA captures opponent-specific
    # contact suppression directly. Schlittler 5/20 false positive was the
    # trigger — career vs PHI .145 BAA / 4.50 ERA shouldn't have fired the
    # ER-under mastery vote (ERA matched xera) but DOES support hits-under.
    vt_baa = _f(g.get(f'{side}_pitcher_vs_team_avg'))
    vt_ip_under = _f(g.get(f'{side}_pitcher_vs_team_ip')) or 0
    if vt_baa is not None and vt_ip_under >= 15:
        if vt_baa <= 0.215:
            conviction += 10
            signals['vs_team_baa'] = f'Career vs opp: {vt_baa:.3f} BAA on {vt_ip_under:.0f} IP — contact-suppression mastery'
        elif vt_baa >= 0.290:
            conviction -= 6
            signals['vs_team_baa_anti'] = f'Career vs opp: {vt_baa:.3f} BAA on {vt_ip_under:.0f} IP — gets hit hard'

    conviction = max(0, min(100, conviction))
    suggested_line = 5.5
    conviction = _apply_l5_signal(g, side, 'hits', suggested_line, 'under', conviction, signals)
    conviction = max(0, min(100, conviction))
    return {'conviction': conviction, 'signals': signals, 'prop_line': suggested_line}


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
                "cache_key": f"eq.projected_lineup_v2_{team_name.replace(' ', '_')}",
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
                "cache_key": f"projected_lineup_v2_{team_name.replace(' ', '_')}",
                "game_id": f"projected_lineup_v2_{team_name.replace(' ', '_')}",
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
        # 2026-08-18 BUG FIX: previously matched on `last = team_name.split()[-1].lower()`
        # which collided on 'sox' between "Boston Red Sox" and "Chicago White Sox".
        # Result: fetch_projected_lineup('Boston Red Sox') could return the White Sox
        # lineup (Meidroth showed up as a BOS "leadoff" prop when he plays for CWS).
        # Fix: prefer FULL team name match; fall back to teamName (nickname); last
        # resort is last-word match ONLY if it doesn't collide with another team.
        tn_lc = team_name.lower()
        # 1. Full name exact match
        team = next(
            (t for t in teams if (t.get("name") or "").lower() == tn_lc),
            None,
        )
        # 2. teamName (nickname, e.g. "Red Sox") exact match
        if not team:
            nickname = team_name.split()[-2:] if len(team_name.split()) > 2 else team_name.split()[-1:]
            nickname_str = ' '.join(nickname).lower()
            team = next(
                (t for t in teams if (t.get("teamName") or "").lower() == nickname_str),
                None,
            )
        # 3. Fallback: last-word only if no ambiguity (single match)
        if not team:
            last = team_name.split()[-1].lower()
            hits = [t for t in teams
                    if last == (t.get("teamName") or "").lower()
                    or last in (t.get("name") or "").lower().split()]
            team = hits[0] if len(hits) == 1 else None
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
        # 2026-08-18 BUG FIX: also fix side-picking to use exact team match
        # (was `if last in t_name.lower()` — same 'sox' collision).
        for side in ("home", "away"):
            t = box.get("teams", {}).get(side, {})
            t_name = t.get("team", {}).get("name", "")
            if t_name and t_name.lower() == team_name.lower():
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


_PITCHER_CONTACT_CACHE = {}

def fetch_pitcher_contact_quality(pitcher_name):
    """Lookup xba_allowed + baa_allowed from mlb_pitcher_stats. Cached per run.

    Used by _blended_projected_hits (2026-08-15 wire-up). xba_allowed is
    Statcast expected batting average — strips luck/BABIP noise from raw
    baa_allowed. Prefer xba when present; fall back to baa when missing
    (rookies, low-BF relievers)."""
    if not pitcher_name:
        return None
    if pitcher_name in _PITCHER_CONTACT_CACHE:
        return _PITCHER_CONTACT_CACHE[pitcher_name]
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats",
            params={
                'player_name': f'eq.{pitcher_name}',
                'select': 'xba_allowed,baa_allowed',
                'limit': '1',
            },
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        out = rows[0] if rows else None
    except Exception:
        out = None
    _PITCHER_CONTACT_CACHE[pitcher_name] = out
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

    # L3 K% REGRESSION gate (added 2026-07-25 after Miller 99-conv 0K disaster).
    # Miller had season K% 29.5 but L3 K% 21.8 (down 7.7pts). We had this
    # data but only used it for HOT streaks — regressions were ignored.
    # Divergence ≥5pts = downgrade, ≥8pts = suppress.
    # See project_miller_k_prop_postmortem_725.
    if l3_k is not None and pitcher_k_pct is not None:
        l3_delta = l3_k - pitcher_k_pct
        if l3_delta <= -8:
            conviction -= 30
            signals['l3_regression_severe'] = (
                f'L3 K% {l3_k:.1f}% down {abs(l3_delta):.1f}pts vs season '
                f'{pitcher_k_pct:.1f}% — SEVERE regression, K-over trap'
            )
        elif l3_delta <= -5:
            conviction -= 18
            signals['l3_regression'] = (
                f'L3 K% {l3_k:.1f}% down {abs(l3_delta):.1f}pts vs season '
                f'{pitcher_k_pct:.1f}% — recency regression'
            )

    # Whiff-rate credibility gate (added 2026-07-25, defensive version).
    # Data quality issue: mlb_pitcher_stats.whiff_rate has DEFAULT VALUE
    # 10.0 stored for many pitchers (Scherzer 10.0, Kershaw 0.103 — mixed
    # decimal + percent + defaults). Only apply gate when value is
    # UNAMBIGUOUSLY real: not exactly 10.0 (default marker) AND is a
    # reasonable percent.
    #
    # See project_miller_k_prop_postmortem_725. Miller's actual whiff
    # was likely much lower than his season K% implied — divergence is
    # the real signal, not raw whiff.
    try:
        import urllib.parse as _urlp, urllib.request as _urlr
        _q = _urlp.quote(pitcher)
        _r = _urlr.urlopen(_urlr.Request(
            f"{os.environ['SUPABASE_URL']}/rest/v1/mlb_pitcher_stats"
            f"?player_name=eq.{_q}&season=eq.2026&select=whiff_rate&limit=1",
            headers={'apikey': os.environ['SUPABASE_KEY'],
                     'Authorization': f'Bearer {os.environ["SUPABASE_KEY"]}'},
        ), timeout=5)
        import json as _json
        _rows = _json.loads(_r.read())
        _whiff_raw = _rows[0]['whiff_rate'] if _rows and _rows[0].get('whiff_rate') is not None else None
        # Normalize: values < 1 are decimals (0.28 = 28%), values 1-50 are already %
        if _whiff_raw is not None:
            _wf = float(_whiff_raw)
            _whiff = _wf * 100 if _wf < 1 else _wf
        else:
            _whiff = None
    except Exception:
        _whiff = None
    # Skip if value is the 10.0 default (unreliable) or obviously bad
    if _whiff is not None and abs(_whiff - 10.0) < 0.01:
        _whiff = None  # default value, don't trust
    if _whiff is not None and (_whiff < 5 or _whiff > 50):
        _whiff = None  # implausible
    if _whiff is not None:
        if _whiff < 13.0:
            conviction -= 20
            signals['whiff_gate_severe'] = (
                f'whiff_rate {_whiff:.1f}% too low — K-over trap regardless of season'
            )
        elif _whiff < 18.0:
            conviction -= 10
            signals['whiff_gate'] = (
                f'whiff_rate {_whiff:.1f}% below elite — K-over risky'
            )
        elif _whiff >= 28.0:
            conviction += 5
            signals['whiff_elite'] = f'whiff_rate {_whiff:.1f}% — elite miss-bat rate'

    # Opposing offense quality
    if opp_wrc >= 120:
        conviction -= 15
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — elite offense grinds ABs'
    elif opp_wrc <= 85:
        conviction += 8
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — weak lineup'

    # Opposing team swing-and-miss tendency (season)
    if opp_k_pct >= 26:
        conviction += 10
        signals['opp_k_rate'] = f'Opp K% {opp_k_pct:.1f}% — whiff-prone'
    elif opp_k_pct <= 18:
        conviction -= 6

    # Opp recency: a hot, contact-trending lineup suppresses K-overs more than
    # the season K% suggests. Without per-team L5 K% stored we proxy via
    # offense_drift (L10 R/G vs season). Hot + already-low-K opp = downgrade.
    # Added 2026-05-13 after Cease PRIME 100 vs Tampa highlighted the gap —
    # Tampa season K% 21 but 9-1 L10 with .313 wOBA (contact-hot, not whiffing).
    opp_drift = _f(g.get(f'{opp_side}_offense_drift'))
    if opp_drift is not None and opp_drift >= 0.5 and opp_k_pct <= 22:
        conviction -= 6
        signals['opp_contact_hot'] = f'Opp on a heater (+{opp_drift:.1f} R/G L10) — contact-trending, not whiffing'
    elif opp_drift is not None and opp_drift <= -1.0 and opp_k_pct <= 22:
        conviction += 4
        signals['opp_contact_cold'] = f'Opp cold ({opp_drift:.1f} R/G L10) — chasing, more whiffs likely'

    # K-rate mastery dimension vs this opponent (added 2026-05-25, see
    # project_mastery_split_by_prop_type). ERA-vs-team is the wrong axis for
    # K props — Schlittler 5/20 had 4.50 ERA vs PHI but K-rate mastery, and
    # the old scorer missed it. K/9-vs-team needs ≥10 IP sample to trust.
    vt_k9 = _f(g.get(f'{side}_pitcher_vs_team_k_per_9'))
    vt_ip = _f(g.get(f'{side}_pitcher_vs_team_ip')) or 0
    if vt_k9 is not None and vt_ip >= 10:
        if vt_k9 >= 11.0:
            conviction += 12
            signals['vs_team_k'] = f'Career vs opp: {vt_k9:.1f} K/9 ({vt_ip:.0f} IP) — K dominance'
        elif vt_k9 >= 9.5:
            conviction += 6
            signals['vs_team_k'] = f'Career vs opp: {vt_k9:.1f} K/9 ({vt_ip:.0f} IP) — above-avg vs this team'
        elif vt_k9 <= 6.0:
            conviction -= 8
            signals['vs_team_k_anti'] = f'Career vs opp: {vt_k9:.1f} K/9 ({vt_ip:.0f} IP) — they put the ball in play'

    # 1st-inning fragility compound signal — when a starter's L3 form is bad
    # AND his season 1st-inn ERA is elevated, the K-over upside is capped
    # because he gets pulled before reaching K volume. Stacks with the L3-ERA
    # penalty above (cumulative is intentional — short outings really do
    # suppress K totals more than the linear model captures).
    if (l3_era is not None and l3_era >= 6.0
            and first_inn_era is not None and first_inn_era >= 4.5):
        conviction -= 5
        signals['short_outing_risk'] = f'L3 ERA {l3_era:.2f} + 1st-inn ERA {first_inn_era:.1f} — short start risk caps K volume'

    # Catcher framing behind the plate helps the pitcher. Audit: `framing`
    # signal hits 72.0% (n=25) on K-over picks — strong standalone, so worth
    # an elite tier (≥4 runs) for extreme cases. Added 2026-05-23.
    if framing is not None and framing >= 4:
        conviction += 12
        signals['framing'] = f'Catcher +{framing:.1f} framing runs — elite zone-stealer'
    elif framing is not None and framing >= 2:
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

    # Catcher framing — SHADOW SIGNAL (2026-05-28). Logged but NOT applied
    # to conviction until we backtest n>=20 K-Over PRIMEs across catchers
    # with framing scores ≥3 vs ≤-3 to confirm the expected lift.
    # Hypothesis: pitchers with elite-framing catchers (+3 framing runs/season)
    # get more strike calls on borderline pitches → more strikeouts. Inverse
    # for poor framers (-3 or worse). Magnitude calibrated from public
    # research (~2-3% extra K rate per +5 framing).
    #
    # Side mapping: own pitcher uses OWN team's catcher framing. Tonight's
    # 5/28 ATL@BOS reads: away_catcher_framing=+3.4 (ATL — Sale's catcher),
    # home_catcher_framing=-3.5 (BOS — Tolle's catcher). If active, Sale's
    # K-Over conviction would gain +4 from elite framing, Tolle's would
    # lose 4 from poor framing.
    own_framing = _f(g.get(f'{side}_catcher_framing'))
    if own_framing is not None:
        # Shadow conviction delta — logged for backtest, not added to conviction.
        if   own_framing >=  5.0: shadow_delta =  7
        elif own_framing >=  3.0: shadow_delta =  4
        elif own_framing <= -5.0: shadow_delta = -7
        elif own_framing <= -3.0: shadow_delta = -4
        else: shadow_delta = 0
        signals['_shadow_framing_value'] = round(own_framing, 1)
        signals['_shadow_framing_delta'] = shadow_delta
        # _shadow_conviction_if_applied = what conviction would be IF we
        # consumed this signal. Lets backtests join shadow vs live cleanly.
        signals['_shadow_conviction_if_applied'] = max(0, min(100, conviction + shadow_delta))

    conviction = max(0, min(100, conviction))

    # Realistic K projection from L7 actual avg (or season fallback).
    # Compute FIRST so suggested_line uses it directly — the old approach
    # used a separate `est_ks = k_pct × 5IP × 4BF` formula that assumed
    # every starter goes 5 IP. For workhorses like Sale (real L7 avg ~8 Ks
    # over 6.5-7 IP), the old formula gave "Over 5.1" while the displayed
    # projection said 8.0 — a -400-juice trap that violated user trust.
    # Now suggested_line is derived from projected_ks so what we recommend
    # matches what we project.
    proj = get_pitcher_projection(pitcher)
    l7_rolling = (proj or {}).get('l7_rolling') if proj else {}
    l7_k = (l7_rolling or {}).get('avg_k')
    # 2026-06-05 K_PROJECTION_SHIFT: subtract 0.3 from raw L7 K projection.
    # Lifetime backtest n=127 graded K props: applying shift -0.3 improves
    # OVER picks 66.7%→72.0% AND UNDER picks 70.2%→71.9% (3.5pt total lift).
    # The raw L7 average modestly over-projects in modern MLB — likely a
    # bullpen-leverage effect (starters get hooked before reaching peak K).
    # 2026-08-07 Bayesian blend: on top of the -0.3 shift, blend L7 with
    # season baseline from classes so short-sample streaks don't drive
    # inflated K lines (same fix pattern as bb 9a42c7cd).
    K_PROJECTION_SHIFT = 0.3
    if l7_k is not None:
        l7_n = (l7_rolling or {}).get('n_starts') or 7
        season_k, _ = _season_baseline_from_classes(proj, 'avg_k')
        blended_k = _bayes_blend_l7_season(l7_k, l7_n, season_k) or float(l7_k)
        signals['_projected_ks'] = round(blended_k - K_PROJECTION_SHIFT, 1)
        signals['_projected_ks_l7_raw'] = round(float(l7_k), 1)
        if season_k is not None:
            signals['_projected_ks_season'] = round(season_k, 2)
    elif pitcher_k_pct is not None:
        signals['_projected_ks'] = round(pitcher_k_pct / 100 * 22 - K_PROJECTION_SHIFT, 1)

    # Catcher framing bonus (NEW 2026-08-15). Elite framer behind starter
    # steals ~5-8 called strikes per game vs replacement → ~+0.8 K/game.
    # Computed by mlb_advanced_metrics.py from savant framing_runs; graceful
    # skip when framing data missing (catcher not yet named, etc.).
    framing_bonus_raw = g.get(f'{side}_framing_k_bonus')
    try:
        framing_bonus = float(framing_bonus_raw) if framing_bonus_raw is not None else None
    except (TypeError, ValueError):
        framing_bonus = None
    if framing_bonus is not None and signals.get('_projected_ks') is not None:
        signals['_projected_ks_pre_framing'] = signals['_projected_ks']
        signals['_projected_ks'] = round(signals['_projected_ks'] + framing_bonus, 1)
        signals['_framing_k_bonus'] = round(framing_bonus, 2)

    # Suggested line — aim for ~1.5 K cushion below projection so the line
    # we surface is a CLEAR Over edge (not 0.5-juiced). Snap to X.5 because
    # books only post X.5 K lines. Bounds 3.5-7.5 match book distribution.
    import math
    projected_ks_val = signals.get('_projected_ks')
    if projected_ks_val is not None:
        # floor(proj - 1.5) + 0.5  → Sale 8.0 → 6.5  ·  Martin 6.0 → 4.5
        suggested_line = max(3.5, min(7.5, math.floor(projected_ks_val - 1.5) + 0.5))
    else:
        # Fallback path — fully conservative when projection missing
        raw_k = pitcher_k_pct if pitcher_k_pct is not None else 22
        k_pct_for_line = min(raw_k, 28)
        est_ks = (k_pct_for_line / 100) * (5.0 * 4.0)
        suggested_line = max(3.5, min(6.5, round(est_ks - 0.5, 1)))

    # Snapshot bullpen state for the audit cohort (added 2026-05-10).
    # mlb_game_context is transient, so without snapshotting at pick time
    # the K-Over × bullpen correlation cohort can't read history.
    own_pen = _i(g.get(f'{side}_bp_relievers_3d'))
    if own_pen is not None:
        signals['_starter_pen_relievers_3d'] = own_pen

    conviction = _apply_l5_signal(g, side, 'ks', suggested_line, 'over', conviction, signals)
    conviction = max(0, min(100, conviction))

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

    # Opp recency boost — a low-K lineup that's also hot lately is reinforcing
    # the K-Under thesis (they're making good contact AND in form). Inverse of
    # the K-Over patch added the same day. Without true L5 K% we proxy via
    # offense_drift.
    opp_drift = _f(g.get(f'{opp_side}_offense_drift'))
    if opp_drift is not None and opp_drift >= 0.5 and opp_k_pct <= 22:
        conviction += 5
        signals['opp_contact_hot'] = f'Opp on a heater (+{opp_drift:.1f} R/G L10) — contact-trending, K-Under reinforced'
    elif opp_drift is not None and opp_drift <= -1.0 and opp_k_pct <= 22:
        conviction -= 4  # cold contact lineup more likely to chase = some whiffs after all

    # K-rate mastery dimension vs this opponent — INVERSE of K-Over scorer.
    # Added 2026-05-25 (see project_mastery_split_by_prop_type). When the
    # pitcher has historically dominated this lineup K-wise, fade the under.
    # When this lineup has historically put the ball in play against him,
    # reinforce the under.
    vt_k9 = _f(g.get(f'{side}_pitcher_vs_team_k_per_9'))
    vt_ip = _f(g.get(f'{side}_pitcher_vs_team_ip')) or 0
    if vt_k9 is not None and vt_ip >= 10:
        if vt_k9 >= 11.0:
            conviction -= 12
            signals['vs_team_k_anti'] = f'Career vs opp: {vt_k9:.1f} K/9 ({vt_ip:.0f} IP) — K dominance, fade caution'
        elif vt_k9 >= 9.5:
            conviction -= 6
            signals['vs_team_k_anti'] = f'Career vs opp: {vt_k9:.1f} K/9 ({vt_ip:.0f} IP) — above-avg K rate vs this team'
        elif vt_k9 <= 6.0:
            conviction += 8
            signals['vs_team_k'] = f'Career vs opp: {vt_k9:.1f} K/9 ({vt_ip:.0f} IP) — they put it in play, under reinforced'

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

    # Bad framing catcher = lost called strikes. Elite-bad tier added
    # 2026-05-23 to match the K-over scorer symmetry.
    if framing is not None and framing <= -4:
        conviction += 12
        signals['framing'] = f'Catcher {framing:.1f} framing — elite zone-loss, K-Under boosted'
    elif framing is not None and framing <= -2:
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

    # K-Under PRIME gate (added 2026-05-07).
    # Audit shows k_under_prime hits 55.6% (5-4) while k_under_strong hits
    # 85.7% (6-1) — PRIME is over-confident on small sample. Multi-signal
    # gate required to clear PRIME tier:
    #   - pitcher season K% ≤ 22 (low-K pitcher, fade is appropriate)
    #   - opposing lineup K% ≤ 22 (contact lineup supports the fade)
    #   - L3 K% < season K% (declining form, not just consistently low)
    # If any missing or above threshold, cap at STRONG (conviction 81).
    # Hypothesis from project_under_props_calibration.md (4/30) — confirmed
    # by 5/7 audit landing K-Under PRIME at coin-flip territory.
    prime_gate_pass = (
        pitcher_k_pct is not None and pitcher_k_pct <= 22
        and opp_k_pct is not None and opp_k_pct <= 22
        and l3_k is not None and pitcher_k_pct is not None and l3_k < pitcher_k_pct
    )
    if not prime_gate_pass and conviction >= 82:
        conviction = 81  # cap at STRONG
        signals['prime_gate'] = (
            'PRIME tier capped — multi-signal gate not met '
            '(k_under_prime audit 55.6%, n=9)'
        )

    conviction = max(0, min(100, conviction))

    # Projection from L7 actual avg (or season fallback). Compute FIRST so
    # suggested_line can use it directly — same unification as K-Over scorer.
    # Apply K_PROJECTION_SHIFT for the same reason as K-Over (see comment
    # at score_pitcher_ks).
    # 2026-08-07 Bayesian blend added — symmetric to K-Over. A hot 7-start
    # K streak on a career-avg strikeout guy shouldn't drive PRIME UNDER
    # (nor short-sample cold streak drive PRIME OVER on the sister scorer).
    proj = get_pitcher_projection(pitcher)
    l7_rolling = (proj or {}).get('l7_rolling') if proj else {}
    l7_k = (l7_rolling or {}).get('avg_k')
    K_PROJECTION_SHIFT = 0.3
    if l7_k is not None:
        l7_n = (l7_rolling or {}).get('n_starts') or 7
        season_k, _ = _season_baseline_from_classes(proj, 'avg_k')
        blended_k = _bayes_blend_l7_season(l7_k, l7_n, season_k) or float(l7_k)
        signals['_projected_ks'] = round(blended_k - K_PROJECTION_SHIFT, 1)
        signals['_projected_ks_l7_raw'] = round(float(l7_k), 1)
        if season_k is not None:
            signals['_projected_ks_season'] = round(season_k, 2)
    elif pitcher_k_pct is not None:
        signals['_projected_ks'] = round(pitcher_k_pct / 100 * 18 - K_PROJECTION_SHIFT, 1)

    # Catcher framing bonus (NEW 2026-08-15). Elite framer raises
    # expected Ks, which for UNDER scoring means the projected total
    # goes UP → suggested UNDER line goes UP → book UNDER at old line
    # becomes less attractive. Symmetric to K-Over path.
    framing_bonus_raw = g.get(f'{side}_framing_k_bonus')
    try:
        framing_bonus = float(framing_bonus_raw) if framing_bonus_raw is not None else None
    except (TypeError, ValueError):
        framing_bonus = None
    if framing_bonus is not None and signals.get('_projected_ks') is not None:
        signals['_projected_ks_pre_framing'] = signals['_projected_ks']
        signals['_projected_ks'] = round(signals['_projected_ks'] + framing_bonus, 1)
        signals['_framing_k_bonus'] = round(framing_bonus, 2)

    # Suggested Under line — aim for ~1.5 K cushion ABOVE projection so the
    # line we surface is a CLEAR Under edge (book line above projection by
    # enough that fade has real value). Snap to X.5. Bounds 3.5-7.5.
    # Old formula used a 4.5-IP assumption that under-projected workhorses
    # and over-projected short-leash guys.
    import math
    projected_ks_val = signals.get('_projected_ks')
    if projected_ks_val is not None:
        # ceil(proj + 1.5) - 0.5  →  Corbin 3.3 → 4.5  ·  Bassitt 4.1 → 5.5
        suggested_line = max(3.5, min(7.5, math.ceil(projected_ks_val + 1.5) - 0.5))
    else:
        raw_k = pitcher_k_pct if pitcher_k_pct is not None else 20
        est_ks = (raw_k / 100) * (4.5 * 4.0)
        suggested_line = max(4.0, min(7.0, round(est_ks + 1.0, 0) - 0.5))

    conviction = _apply_l5_signal(g, side, 'ks', suggested_line, 'under', conviction, signals)
    conviction = max(0, min(100, conviction))

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

    # Last outing pitch count + IP — recent durability proof.
    # Softened 2026-05-13: 85-pitch threshold (was 90) so efficient deep starts
    # like Fried 86p/6.33 IP register. Also pull L7 avg_ip from pitcher_projections
    # — a starter with 6+ IP L7 avg is durable regardless of a single short last
    # outing (rain, blowout, etc.). Fixes Fried-style outs-over misses.
    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    l7_ip = (l7 or {}).get('avg_ip') if l7 else None
    if last_ip is not None and last_ip >= 6.0 and (last_pitches or 0) >= 85:
        conviction += 10
        signals['durable'] = f'Last outing: {last_pitches:.0f}p / {last_ip:.1f} IP — stretched out'
    elif l7_ip is not None and float(l7_ip) >= 6.0:
        conviction += 8
        signals['l7_durable'] = f'L7 avg {l7_ip:.1f} IP/start — sustained deep starter'
    elif last_ip is not None and last_ip <= 4.5:
        conviction -= 8
        signals['short_last'] = f'Last outing: {last_ip:.1f} IP — short leash'

    # L3 form — gated 2026-05-23. Standalone `l3` signal hits 43.5% on n=23
    # per audit_signal_attribution scan; lifting it from a noise predictor
    # to a confirming signal. Only boost when xERA also confirms (genuinely
    # good arm, not just a lucky 3-start stretch). Same fix applied to ER
    # scorer below.
    if l3_era is not None:
        l3_confirmed = xera is not None and xera <= 3.75
        if l3_era <= 2.5 and l3_confirmed:
            conviction += 8
            signals['l3'] = f'L3 ERA {l3_era:.2f} — locked in (xERA {xera:.2f} confirms)'
        elif l3_era <= 2.5:
            # Soft mention only, no conviction boost
            signals['l3_unconfirmed'] = f'L3 ERA {l3_era:.2f} — recent good starts (xERA {xera:.2f} unconfirmed)'
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

    # 2026-08-23 GAP CLOSE — park was silently influencing conviction on
    # outs scoring but never emitting a signal (same class of bug as HA
    # scoring before its fix). Now emits so coverage chip + card reflect
    # what the model is using.
    if park_run >= 115:
        conviction -= 7
        signals['park'] = f'Extreme hitter park ({park_run:.0f}) — high pitch count risk shortens outing'
    elif park_run >= 110:
        conviction -= 5
        signals['park'] = f'Hitter-friendly park ({park_run:.0f}) — more pitches, shorter outing'
    elif park_run <= 88:
        conviction += 6
        signals['park'] = f'Extreme pitcher park ({park_run:.0f}) — efficient innings, deeper outing'
    elif park_run <= 92:
        conviction += 4
        signals['park'] = f'Pitcher-friendly park ({park_run:.0f}) — efficient innings'

    # 2026-08-23 GAP CLOSE — days_rest signal (silent before)
    if days_rest is not None:
        if days_rest >= 6:
            conviction += 3
            signals['fatigue'] = f'{int(days_rest)} days rest — fresh arm, depth advantage'
        elif days_rest <= 3:
            conviction -= 3
            signals['fatigue'] = f'{int(days_rest)} days rest — short rest, quicker hook likely'

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

    # Pull projected_outs (set by patch_projected_ks.py from L7 actual avg_ip)
    # so the suggested line tracks real recent durability instead of a fixed
    # xERA-tier formula. Falls back to old tier formula when projection missing.
    projected_outs = _f(g.get(f'{side}_pitcher_projected_outs'))
    if projected_outs is not None:
        signals['_projected_outs'] = round(projected_outs, 1)
        # Over scorer: target line ~2 outs BELOW projection for a clear Over edge.
        # Snap to standard book grid (books post 14.5 / 15.5 / 16.5 / 17.5).
        target = projected_outs - 2.0
        if target >= 17.5:
            suggested_line = 17.5
        elif target >= 16.5:
            suggested_line = 16.5
        elif target >= 15.5:
            suggested_line = 15.5
        else:
            suggested_line = 14.5
    else:
        # Legacy tier formula — only fires when projection is unavailable.
        if xera <= 3.0 and last_ip is not None and last_ip >= 6.0:
            suggested_line = 17.5
        elif xera <= 3.75:
            suggested_line = 16.5
        elif xera <= 4.5:
            suggested_line = 15.5
        else:
            suggested_line = 14.5

    conviction = _apply_l5_signal(g, side, 'outs', suggested_line, 'over', conviction, signals)
    conviction = max(0, min(100, conviction))

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
    # Openers SKIPPED (2026-05-19 patch): the math says they cash Under
    # trivially, but books don't post outs lines on openers — the prop is
    # unbettable. Today's Rojas case: pipeline surfaced him at 91.7% audit
    # cohort, user couldn't find the line on any sportsbook. Better to not
    # surface than to surface an unbettable lock.
    if last_ip is not None and last_ip <= 2.0 and (last_pitches or 0) <= 40:
        return None  # opener — books don't post outs line
    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))

    signals = {}
    conviction = 30

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

    # Pull projected_outs (L7 actual avg_ip × 3) to drive the Under line
    # toward what books actually post. 5/28 trigger: Flaherty xERA 5.04
    # got the old "shaky pitcher" formula's 14.5 line, but the actual
    # book posted 16.5. With projected_outs of 11.6 and a book line of
    # 16.5, the real Under cushion was 4.9 outs (a huge edge we wrote up
    # as a modest 2.9-out Under). Now we snap to the book grid with a
    # 4-out cushion target so the suggested line tracks reality.
    projected_outs = _f(g.get(f'{side}_pitcher_projected_outs'))
    if projected_outs is not None:
        signals['_projected_outs'] = round(projected_outs, 1)
    if last_ip is not None and last_ip <= 2.0:
        # Opener case — books rarely post outs lines for openers but when
        # they do it's typically 12.5 or 13.5. Keep the existing handling.
        suggested_line = 12.5
    elif projected_outs is not None:
        # Under scorer: target line ~4 outs ABOVE projection, snapped to the
        # nearest X.5 (books only post 14.5 / 15.5 / 16.5 / 17.5). 5/28
        # Flaherty trigger: projected 12.6, target 16.6 — strict floor-to-
        # bracket bumped to 17.5 when the actual book line was 16.5. Round
        # to nearest 0.5 (16.6 → 16.5) instead and clip to bracket bounds.
        target = projected_outs + 4.0
        rounded = round(target * 2) / 2
        # Force X.5 (drop any .0 to .5 below — books don't post X.0)
        if rounded == int(rounded):
            rounded -= 0.5
        suggested_line = max(14.5, min(17.5, rounded))
    else:
        # Legacy formula fallback when projection unavailable.
        if xera >= 5.0:
            suggested_line = 15.5  # bumped from 14.5 to match book reality
        else:
            suggested_line = 16.5  # bumped from 15.5 to match book reality

    conviction = _apply_l5_signal(g, side, 'outs', suggested_line, 'under', conviction, signals)
    conviction = max(0, min(100, conviction))

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

    # L3 form — gated 2026-05-23. Audit pair `l3 + xera_high` hit 50% on
    # n=10 vs xera_high alone at 79.3%. Adding L3 boost on top of an
    # already-bad xERA was poisoning the pick. Now: drop L3 boost from
    # +14 → +6 when xera_high is already firing (don't double-count "bad
    # pitcher" via two signals). Standalone L3 with average xera also
    # demoted since standalone L3 underperforms (43.5% on n=23).
    if l3_era is not None:
        if l3_era >= 6.0:
            if xera >= 5.0:
                # xera_high already fired — soften the L3 add to avoid
                # double-counting. Pair audit: pair 50% vs xera-only 79.3%.
                conviction += 6
                signals['l3'] = f'L3 ERA {l3_era:.2f} (xera already flagged — softened)'
            elif xera <= 4.0:
                # Decent season xERA with bad L3 — likely variance, soft add only
                conviction += 6
                signals['l3'] = f'L3 ERA {l3_era:.2f} — recent regression (xera {xera:.2f} suggests bounce-back risk)'
            else:
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

    # L7 rolling avg ER — direct signal the model previously ignored. Added
    # 2026-05-13 after 5/13 surfaced Woods Richardson (L7 4.0 ER) / McCullers
    # (L7 4.0 ER) projecting clean ER-overs that didn't clear the xERA-anchored
    # scorer. L7 captures what xERA misses (mechanical issues, command slumps).
    # 2026-08-07 Bayesian blend added — same fix pattern as bb 9a42c7cd.
    # Raw L7 avg_er inflated projections when a hot-streak spike (Buehler
    # 7 short starts) drove PRIME OVERs off unregressed numbers.
    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    l7_er = (l7 or {}).get('avg_er') if l7 else None
    if l7_er is not None:
        l7_n = (l7 or {}).get('n_starts') or 7
        season_er, _ = _season_baseline_from_classes(proj, 'avg_er')
        blended_er = _bayes_blend_l7_season(l7_er, l7_n, season_er) or float(l7_er)
        signals['_projected_er'] = round(blended_er, 1)
        signals['_projected_er_l7_raw'] = round(float(l7_er), 1)
        if season_er is not None:
            signals['_projected_er_season'] = round(season_er, 2)
        # Gate on BLENDED value — was gating on raw l7_er and inflating
        # conviction on small-sample hot streaks
        if blended_er >= 3.5:
            conviction += 12
            signals['last7_er'] = f'last 7 starts avg {l7_er:.1f} ER/start · blended proj {blended_er:.1f} — getting tagged'
        elif blended_er <= 1.5:
            conviction -= 8

    # Vs-team ERA history override — when a starter has historically been
    # torched by this opponent (≥7.00 ERA on n≥5 IP), prioritize that over
    # the model's L3 / xERA read. Added 2026-05-13 after Sonny Gray (career
    # 17.18 ERA vs PHI on 3.7 IP) cleared no standard gate but is a clear
    # matchup-history ER-over candidate.
    vs_team_era = _f(g.get(f'{side}_pitcher_vs_team_era'))
    vs_team_ip_er = _f(g.get(f'{side}_pitcher_vs_team_ip')) or 0
    # 2026-05-27 INCIDENT: 15-IP gate added. Was firing on 5-IP samples
    # which is one bad/good start away from being noise. Mastery-tier
    # boosts need a real career sample, not a 1-game hot/cold streak.
    if vs_team_era is not None and vs_team_ip_er >= 15 and vs_team_era >= 7.0:
        conviction += 14
        signals['vs_team'] = f'Career vs opp: {vs_team_era:.2f} ERA on {vs_team_ip_er:.0f} IP — historically pummeled'
    elif vs_team_era is not None and vs_team_ip_er >= 15 and vs_team_era <= 2.5:
        conviction -= 8

    conviction = max(0, min(100, conviction))

    # Suggested line: most common is 2.5. Use 1.5 only for elite arms (rare
    # — book usually doesn't price a 1.5 ER Over with edge for the bettor).
    if xera <= 3.0:
        suggested_line = 1.5
    elif xera <= 4.0:
        suggested_line = 2.5
    else:
        suggested_line = 2.5  # most-common market line — keep simple

    # 2026-06-09 ER OVER calibration audit (post project_may17_pitcher_prop_cohorts
    # surfaced ER OVER PRIME at 25% / 1-3 yellow-flag). Root cause: scorer was
    # stacking signals (xera_high + opp_offense + park) into PRIME tier even
    # when the projected ER (from L7 avg) was MATERIALLY below the suggested
    # line. Recommending Over 2.5 when L7 avg is 1.8 ER per start is asking
    # the pitcher to exceed his recent norm — that's a coinflip at best.
    #
    # Gate: compare _projected_er against the suggested line.
    #   - projection ≥ line by 0.5+: boost (recent form supports the OVER)
    #   - projection at or near line: no adjustment
    #   - projection ≤ line by 0.5+: FADE (recent form contradicts the OVER)
    #   - projection ≤ line by 1.0+: HARD FADE (projection screams UNDER)
    proj_er = signals.get('_projected_er')
    if proj_er is not None:
        try:
            gap = float(proj_er) - float(suggested_line)
            if gap >= 0.5:
                conviction += 10
                signals['proj_supports'] = f'L7 avg {proj_er:.1f} ER ≥ line {suggested_line} — recent form supports'
            elif gap <= -1.0:
                conviction -= 20
                signals['proj_fade_hard'] = f'L7 avg {proj_er:.1f} ER vs line {suggested_line} — projection points UNDER, scorer over-promoted'
            elif gap <= -0.5:
                conviction -= 12
                signals['proj_fade'] = f'L7 avg {proj_er:.1f} ER below line {suggested_line} — recent form contradicts'
        except (TypeError, ValueError):
            pass

    # Outs/IP context — if pitcher projects for <15 outs (5 IP), there's less
    # opportunity for 2.5+ ER to cluster. Soft fade when projected outs are
    # low. Conversely, 21+ outs (7 IP, 3x through order) amplifies ER risk.
    projected_outs = _f(g.get(f'{side}_pitcher_projected_outs'))
    if projected_outs is not None:
        if projected_outs <= 12 and suggested_line >= 2.5:
            # ≤4 IP projection vs 2.5 line — pitcher barely sees the lineup twice
            conviction -= 8
            signals['short_start'] = f'Projects ~{projected_outs/3:.1f} IP — limited ER window'
        elif projected_outs >= 21 and xera >= 4.0:
            # 7+ IP vs already shaky starter — 3rd-time-through amplifies risk
            conviction += 5
            signals['deep_into_order'] = f'Projects ~{projected_outs/3:.1f} IP — 3rd time through risk'

    conviction = _apply_l5_signal(g, side, 'er', suggested_line, 'over', conviction, signals)
    conviction = max(0, min(100, conviction))

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
    projected_outs = _f(g.get(f'{side}_pitcher_projected_outs'))

    signals = {}
    conviction = 30

    # Numeric projection used by the book-line recalibration step. Prefer L7
    # avg_er (matches over-side projection source for symmetry), fall back to
    # xERA × projected_IP / 9 when L7 sample isn't available. Stored as
    # `_projected_er` to mirror _projected_ks / _projected_bb / _projected_hits.
    # 2026-08-07 Bayesian blend added — symmetric to ER-Over. Cold-streak
    # ER dips shouldn't drive PRIME UNDER on a career-average arm.
    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    _l7_er = (l7 or {}).get('avg_er') if l7 else None
    if _l7_er is not None:
        l7_n = (l7 or {}).get('n_starts') or 7
        season_er, _ = _season_baseline_from_classes(proj, 'avg_er')
        blended_er = _bayes_blend_l7_season(_l7_er, l7_n, season_er) or float(_l7_er)
        signals['_projected_er'] = round(blended_er, 1)
        signals['_projected_er_l7_raw'] = round(float(_l7_er), 1)
        if season_er is not None:
            signals['_projected_er_season'] = round(season_er, 2)
    elif projected_outs is not None and xera is not None:
        # ER = xERA × IP / 9 = xERA × outs / 27
        signals['_projected_er'] = round(float(xera) * float(projected_outs) / 27.0, 1)

    # xERA — primary signal (smoothed band 2026-05-17 — added 3.3 middle bucket
    # to avoid the 3.0 cliff that left Ryan 3.27 underscored on 5/15).
    if xera <= 2.5:
        conviction += 25
        signals['xera_elite'] = f'Elite xERA {xera:.2f} — ace'
    elif xera <= 3.0:
        conviction += 18
        signals['xera'] = f'Strong xERA {xera:.2f}'
    elif xera <= 3.3:
        conviction += 13
        signals['xera'] = f'Sharp xERA {xera:.2f}'
    elif xera <= 3.5:
        conviction += 10
    elif xera >= 4.5:
        conviction -= 15

    # L3 form — added 2.5 and 3.0 bands 2026-05-17 to remove the 2.0 cliff.
    if l3_era is not None:
        if l3_era <= 2.0:
            conviction += 12
            signals['l3_hot'] = f'L3 ERA {l3_era:.2f} — locked in'
        elif l3_era <= 2.5:
            conviction += 7
            signals['l3_sharp'] = f'L3 ERA {l3_era:.2f} — sharp'
        elif l3_era <= 3.0:
            conviction += 3
        elif l3_era >= 5.5:
            conviction -= 10

    # Pitcher career mastery vs current opp lineup (added 2026-05-17 — was
    # entirely missing from ER UNDER scorer; cost Ryan's 5/15 prop a +10
    # bump despite 2.38 ERA / 12.3 IP vs MIL career).
    #
    # 2026-05-25: loosened the relative-gap threshold (xera-1.0 → xera-0.8)
    # AND added an absolute-large-sample path. Ryan's 5/15 case re-recompute:
    # vt_era 2.38 vs xera 3.27 = 0.89 below xera, juuust shy of the old 1.0
    # cliff. With IP sample of 12.3, mastery is real signal. New rule fires
    # on either relative gap ≥0.8 OR (vt_era ≤2.5 AND IP ≥10).
    vt_era = _f(g.get(f'{side}_pitcher_vs_team_era'))
    vt_ip = _f(g.get(f'{side}_pitcher_vs_team_ip')) or 0
    # 2026-05-27 INCIDENT: minimum IP raised from 10 → 15 for absolute
    # mastery, and relative mastery now also gated on ≥15 IP. The 10-IP
    # floor was still admitting 2-3 start hot streaks as "mastery" — Matz
    # @ BAL was the public-facing failure (9.7 IP / .200 BAA in DB vs
    # 38.3 IP / .276 BAA full career).
    if vt_era is not None and xera is not None and vt_ip >= 15:
        relative_mastery = vt_era <= 2.5 and (vt_era < xera - 0.8)
        absolute_mastery = vt_era <= 2.5
        if relative_mastery or absolute_mastery:
            conviction += 10
            signals['vs_team'] = f'Career vs opp: {vt_era:.2f} ERA on {vt_ip:.0f} IP — mastery'
        elif vt_era >= 6.0 and (vt_era > xera + 1.5):
            conviction -= 10  # anti-mastery signal goes the other way
            signals['vs_team_anti'] = f'Career vs opp: {vt_era:.2f} ERA — gets tagged'

    # NRFI score assist (added 2026-05-17, threshold lowered 5/25) — if game
    # projects to scoreless 1st, that supports ER UNDER thesis. Lowered floor
    # from 75 to 73 so Ryan's NRFI 74 (5/15 case) catches the assist.
    nrfi = _f(g.get('nrfi_score'))
    if nrfi is not None:
        if nrfi >= 73:
            conviction += 4
            signals['nrfi_assist'] = f'NRFI {int(nrfi)} — clean 1st projects'
        elif nrfi <= 40:
            conviction -= 4

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
    # Smoothed bands 2026-05-17 — added the -0.5 mid-tier to capture clean
    # splits like Ryan's -0.67 home advantage.
    split = pitcher_split_delta(g, side)
    if split is not None:
        if split <= -1.0:
            conviction += 7
            signals['split'] = f'In favored split ({split:+.2f} ERA vs season)'
        elif split <= -0.5:
            conviction += 4
            signals['split'] = f'Favored split lean ({split:+.2f} ERA vs season)'
        elif split >= 1.0:
            conviction -= 5

    conviction = max(0, min(100, conviction))

    # Most common Under line is 2.5
    suggested_line = 2.5
    if xera <= 2.5:
        suggested_line = 1.5  # only for true aces

    conviction = _apply_l5_signal(g, side, 'er', suggested_line, 'under', conviction, signals)
    conviction = max(0, min(100, conviction))

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

    # Team offense_heat — L10 R/G vs season delta. Catches the Angels-hot
    # case (season wRC+ 99 but L10 cooking) and the inverse: good-season
    # offense currently slumping. Wired 2026-05-23.
    team_drift = _f(g.get(f'{side}_offense_drift'))
    if team_drift is not None:
        if team_drift >= 1.0:
            conviction += 6
            signals['team_heat'] = f'🔥 Team L10 +{team_drift:.1f} R/G vs season — hot bats'
        elif team_drift >= 0.5:
            conviction += 3
            signals['team_heat'] = f'Team L10 +{team_drift:.1f} R/G — trending warm'
        elif team_drift <= -1.0:
            conviction -= 6
            signals['team_cold'] = f'❄️  Team L10 {team_drift:.1f} R/G vs season — cold bats'
        elif team_drift <= -0.5:
            conviction -= 3
            signals['team_cold'] = f'Team L10 {team_drift:.1f} R/G — trending cool'

    # L14 OPS-proxy heat (2026-05-23, threshold retuned same-day).
    # Cleaner version of team_drift: OPS strips BABIP cluster luck.
    # Raised from ±15 → ±25 after the initial threshold flagged 23 of 30
    # teams (noise-fitting, not signal). ±10 stays as narrative-only.
    # Also added L7 confirmation: full-conviction signal requires L7
    # OPS to be on the same side of league avg as L14 (both above or
    # both below). When L7 disagrees, demote to narrative-mention only —
    # the team is regressing or rebounding, not in a clean trend.
    team_wrc_l14 = _f(g.get(f'{side}_wrc_proxy_l14'))
    team_ops_l7 = _f(g.get(f'{side}_ops_last7'))
    season_wrc = _f(g.get(f'{side}_wrc_plus')) or 100
    if team_wrc_l14 is not None:
        l14_delta = team_wrc_l14 - season_wrc
        l7_confirms = True
        if team_ops_l7 is not None:
            l7_delta_ops = team_ops_l7 - 0.720  # league-avg OPS
            l7_confirms = (l14_delta > 0 and l7_delta_ops > 0) or \
                          (l14_delta < 0 and l7_delta_ops < 0)
        if l14_delta >= 25 and l7_confirms:
            conviction += 5
            signals['l14_heat'] = f'🔥 L14 wRC+ {team_wrc_l14:.0f} vs season {season_wrc:.0f} (+{l14_delta:.0f}) — quality contact up, L7 confirms'
        elif l14_delta <= -25 and l7_confirms:
            conviction -= 5
            signals['l14_cold'] = f'❄️  L14 wRC+ {team_wrc_l14:.0f} vs season {season_wrc:.0f} ({l14_delta:.0f}) — quality contact down, L7 confirms'
        elif abs(l14_delta) >= 25 and not l7_confirms:
            # Real L14 magnitude but L7 disagrees — team is reversing
            direction = 'cooling' if l14_delta > 0 else 'rebounding'
            signals['l14_reversing'] = f'L14 wRC+ {team_wrc_l14:.0f} ({l14_delta:+.0f}) but L7 OPS {team_ops_l7:.3f} {direction} — no conviction adj'
        elif abs(l14_delta) >= 10:
            # Mid-magnitude — mention only, no conviction adj
            tag = 'l14_warming' if l14_delta > 0 else 'l14_cooling'
            signals[tag] = f'L14 wRC+ {team_wrc_l14:.0f} ({l14_delta:+.0f}) — modest drift, narrative only'

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
        elif opp_quality >= 3.50:
            # 2026-08-23 Wave 3a: middle-zone mild boost. Prior code left
            # 2.75-4.25 silent — batter Over Hits vs mediocre starter got
            # no pitcher-form signal. Parallel to the fade added on the
            # UNDER side for the same middle zone (score_batter_hits_under).
            conviction += 3
            signals['opp_starter_mid'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — average form, mild lift'
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

    # 2026-07-21 BvP (Batter-vs-Pitcher) career splits — per-batter level.
    # Team-level opp_vs_team_baa gives an aggregate view; BvP surfaces the
    # specific matchup edge (e.g. Freeman .386 / 1.128 OPS on 57 AB vs
    # Wheeler = BATTER_MASTERY). Requires >= 6 lifetime ABs to trust.
    # Data from MLB Stats API vsPlayer endpoint (public, no auth).
    try:
        from batter_vs_pitcher import get_bvp_line, classify_bvp
        batter_id = _lookup_player_id(batter)
        opp_pitcher_id = _lookup_player_id(opp_pitcher)
        if batter_id and opp_pitcher_id:
            bvp = get_bvp_line(batter_id, opp_pitcher_id)
            if bvp:
                cls = classify_bvp(bvp)
                ab_str = f"{bvp['ab']} AB"
                if cls == 'BATTER_MASTERY':
                    conviction += 8
                    signals['bvp_mastery'] = (
                        f"BvP: {bvp['avg']:.3f}/{bvp['ops']:.3f} OPS on {ab_str} — batter owns pitcher"
                    )
                elif cls == 'BATTER_TROUBLE':
                    conviction -= 8
                    signals['bvp_trouble'] = (
                        f"BvP: {bvp['avg']:.3f}/{bvp['ops']:.3f} OPS on {ab_str} — pitcher owns batter"
                    )
                # NEUTRAL — no conviction adjustment, but log for transparency
                elif cls == 'NEUTRAL' and bvp['ab'] >= 8:
                    signals['bvp_neutral'] = f"BvP: {bvp['avg']:.3f}/{bvp['ops']:.3f} on {ab_str}"
    except Exception:
        pass  # fail silently — BvP is a lift, not a required signal

    # Opp pitcher's career BAA vs this team — mastery dimension specific to
    # hits props (added 2026-05-24 per project_mastery_split_by_prop_type).
    # When opp pitcher has historically held this lineup to low BAA, hits
    # are harder to come by → fade hits_over. Inverse: when this lineup
    # has historically tagged the opp pitcher (BAA >= .290), boost.
    opp_vs_team_baa = _f(g.get(f'{opp_side}_pitcher_vs_team_avg'))
    opp_vs_team_ip = _f(g.get(f'{opp_side}_pitcher_vs_team_ip')) or 0
    # 2026-05-27 INCIDENT: 15-IP gate added. Was firing on any sample size.
    if opp_vs_team_baa is not None and opp_vs_team_ip >= 15:
        if opp_vs_team_baa <= 0.215:
            conviction -= 8
            signals['opp_vs_team_baa'] = f'Opp pitcher career vs this team: {opp_vs_team_baa:.3f} BAA on {opp_vs_team_ip:.0f} IP — historical mastery'
        elif opp_vs_team_baa >= 0.290:
            conviction += 8
            signals['opp_vs_team_baa_anti'] = f'Opp pitcher career vs this team: {opp_vs_team_baa:.3f} BAA on {opp_vs_team_ip:.0f} IP — gets tagged'

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

    # Barrel% regression catcher (added 2026-05-11). Inverse of the
    # hits-UNDER barrel detector: a cold batter with high barrel% is
    # hitting it hard but unlucky — boost the OVER as a regression play.
    quality = fetch_batter_quality(batter)
    if quality and quality.get('barrel_pct') is not None and l7:
        barrel = quality['barrel_pct']
        l7_rate = l7.get('got_hit_rate', 1.0)
        if barrel >= 10.0 and l7_rate <= 0.40:
            conviction += 6
            signals['barrel_due'] = (
                f'Barrel% {barrel:.1f}% elite despite L7 cold — due for regression'
            )
        elif barrel >= 8.0 and l7_rate <= 0.50:
            conviction += 3
            signals['barrel_underlying'] = f'Barrel% {barrel:.1f}% above avg — underlying quality contact'

    # Offense drift fade gate (added 2026-05-07).
    # Catches the "good season offense, cold bats currently" trap. Twins 5/6
    # are the canonical case — 105 wRC+ season-long but in a cold L10 stretch,
    # then went 2-runs-flat in a 15-2 loss with 4 hits-OVER PRIMEs on the
    # lineup that all missed.
    #
    # Drift = L10 R/G - season R/G. When drift <= -1.0 R/G, the lineup is
    # currently cold relative to its baseline. Apply a hard -15 conviction
    # penalty so PRIME-tier picks downgrade to STRONG/LEAN. Doesn't kill the
    # play — just stops PRIME on a cold lineup.
    drift = _f(g.get(f'{side}_offense_drift'))
    if drift is not None and drift <= -1.0:
        conviction -= 15
        signals['offense_drift_cold'] = (
            f'Team L10 R/G drift {drift:+.1f} vs season — cold bats fade'
        )

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
    # 2026-05-30 REWEIGHT per 324-prop cohort audit: opp_starter PRESENT
    # audits 58.5% vs 67.6% ABSENT (delta -9.0pt). The "elite opposing
    # pitcher" signal that the entire scorer was anchored on is actually
    # SLIGHTLY anti-predictive — the props ship better without it because
    # the genuinely-cold-bat cases (which DO predict) tend to suppress
    # the opp_quality requirement. Cutting weights ~45%: keep the signal
    # as a directional filter (still need ≤3.50 quality opp to play) but
    # don't let it stack conviction the way it was.
    opp_quality = opp_xera if opp_xera is not None else opp_l3
    opp_quality_label = 'xERA' if opp_xera is not None else 'L3 ERA'
    if opp_quality is None:
        return None  # no pitcher signal = no fade
    if opp_quality <= 2.75:
        conviction += 12  # was +22 — audit said over-weighted
        signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — ace'
    elif opp_quality <= 3.50:
        conviction += 6   # was +12
        signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — quality arm'
    elif opp_quality >= 5.0:
        return None  # bad opposing pitcher = wrong side
    else:
        # 2026-08-23 Wave 3a: MIDDLE-ZONE FADE. Prior code left xERA/L3 ERA
        # 3.50-5.00 as silent — no boost, no fade — so batter Under Hits
        # picks against mediocre pitchers shipped at the same base conviction
        # as if the pitcher had no downside. Empirically these are lift
        # regressions: mediocre opp starter -> batter still makes contact.
        # Mild fade signal + narrative so downstream (Jerry, refit) sees it.
        conviction -= 4
        signals['opp_starter_mediocre'] = (
            f'Opp starter {opp_quality:.2f} {opp_quality_label} — mediocre form, '
            f'fade caution')

    # K-heavy opp starter — REWEIGHTED 2026-05-30. opp_k_artist (k_pct≥30)
    # audits at 58.1% PRESENT vs 60.6% ABSENT (delta -2.6pt). Not as bad
    # as opp_starter but still doesn't earn the +15 it had. Cut to +5.
    if opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 30:
        conviction += 5   # was +15 — audit -2.6pt delta
        signals['opp_k_artist'] = f'Opp K% {opp_pitcher_k_pct:.1f}% — strikeout artist'
    elif opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 26:
        conviction += 3   # was +8
        signals['opp_k_heavy'] = f'Opp K% {opp_pitcher_k_pct:.1f}% — high whiff'
    elif opp_pitcher_k_pct is not None and opp_pitcher_k_pct <= 18:
        conviction -= 4   # was -8

    # Hot opp form
    if opp_l3 is not None and opp_l3 <= 2.0:
        conviction += 8
        signals['opp_form_hot'] = f'Opp L3 ERA {opp_l3:.2f} — locked in'
    elif opp_l3 is not None and opp_l3 >= 5.5:
        conviction -= 8

    # Opp pitcher's career BAA vs this team — INVERSE of hits_over scorer.
    # Low BAA = pitcher has owned this lineup → BOOST hits_under.
    # 2026-05-30 REWEIGHT: opp_vs_team_baa PRESENT audits 52.0% vs 60.2%
    # ABSENT (delta -8.2pt on n=25). Cut from ±8 to ±3 — still keep as
    # narrative signal but stop letting it stack conviction the way it
    # did. May need full removal if v2 reweight audit confirms; for now
    # neutering is safer than pulling entirely (n=25 is small enough
    # that the delta could partly be noise).
    opp_vs_team_baa = _f(g.get(f'{opp_side}_pitcher_vs_team_avg'))
    opp_vs_team_ip2 = _f(g.get(f'{opp_side}_pitcher_vs_team_ip')) or 0
    if opp_vs_team_baa is not None and opp_vs_team_ip2 >= 15:
        if opp_vs_team_baa <= 0.215:
            conviction += 3  # was +8 — audit -8.2pt delta when present
            signals['opp_vs_team_baa'] = f'Opp pitcher career vs this team: {opp_vs_team_baa:.3f} BAA on {opp_vs_team_ip2:.0f} IP — mastery (weight reduced per 5/30 audit)'
        elif opp_vs_team_baa >= 0.290:
            conviction -= 3  # was -8
            signals['opp_vs_team_baa_anti'] = f'Opp pitcher career vs this team: {opp_vs_team_baa:.3f} BAA on {opp_vs_team_ip2:.0f} IP — gets tagged, fade caution'

    # Weak team offense
    if team_wrc <= 85:
        conviction += 12
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — weak'
    elif team_wrc <= 95:
        conviction += 6
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — below avg'
    elif team_wrc >= 115:
        conviction -= 12

    # Team offense_heat (L10 R/G vs season) — inverted for hits-UNDER.
    # 2026-05-30 REWEIGHT: team_cold PRESENT audits 68.7% vs 57.2% ABSENT
    # (delta +11.5pt on n=67) — the 2nd-best single signal in the entire
    # scorer. Boost the conviction add to reflect that signal strength.
    team_drift = _f(g.get(f'{side}_offense_drift'))
    if team_drift is not None:
        if team_drift <= -1.0:
            conviction += 10  # was +6 — audit said +11.5pt edge
            signals['team_cold'] = f'❄️  Team L10 {team_drift:.1f} R/G vs season — cold bats'
        elif team_drift <= -0.5:
            conviction += 5   # was +3
            signals['team_cold'] = f'Team L10 {team_drift:.1f} R/G — trending cool'
        elif team_drift >= 1.0:
            conviction -= 6
            signals['team_heat'] = f'🔥 Team L10 +{team_drift:.1f} R/G vs season — hot bats, fade caution'
        elif team_drift >= 0.5:
            conviction -= 3
            signals['team_heat'] = f'Team L10 +{team_drift:.1f} R/G — trending warm, fade caution'

    # L14 OPS-proxy heat (inverted for under). Threshold raised ±15 → ±25
    # + L7 confirmation gate same as hits_OVER scorer. When L7 disagrees
    # with L14, the team is reversing (cooling off or rebounding) — fading
    # is the better play than stacking, so no conviction add either way.
    team_wrc_l14 = _f(g.get(f'{side}_wrc_proxy_l14'))
    team_ops_l7 = _f(g.get(f'{side}_ops_last7'))
    season_wrc = _f(g.get(f'{side}_wrc_plus')) or 100
    if team_wrc_l14 is not None:
        l14_delta = team_wrc_l14 - season_wrc
        l7_confirms = True
        if team_ops_l7 is not None:
            l7_delta_ops = team_ops_l7 - 0.720
            l7_confirms = (l14_delta > 0 and l7_delta_ops > 0) or \
                          (l14_delta < 0 and l7_delta_ops < 0)
        if l14_delta <= -25 and l7_confirms:
            conviction += 5
            signals['l14_cold'] = f'❄️  L14 wRC+ {team_wrc_l14:.0f} vs season {season_wrc:.0f} ({l14_delta:.0f}) — quality contact down, L7 confirms'
        elif l14_delta >= 25 and l7_confirms:
            conviction -= 5
            signals['l14_heat'] = f'🔥 L14 wRC+ {team_wrc_l14:.0f} vs season {season_wrc:.0f} (+{l14_delta:.0f}) — hot bats, fade caution'
        elif abs(l14_delta) >= 25 and not l7_confirms:
            direction = 'cooling' if l14_delta > 0 else 'rebounding'
            signals['l14_reversing'] = f'L14 wRC+ {team_wrc_l14:.0f} ({l14_delta:+.0f}) but L7 OPS {team_ops_l7:.3f} {direction} — no conviction adj'
        elif abs(l14_delta) >= 10:
            tag = 'l14_warming' if l14_delta > 0 else 'l14_cooling'
            signals[tag] = f'L14 wRC+ {team_wrc_l14:.0f} ({l14_delta:+.0f}) — modest drift, narrative only'

    # Pitcher park
    # 2026-06-09 REWEIGHT (7-day STRONG audit): park-as-fade-signal hit
    # 12% (1-7) on hits_under. Pitcher park doesn't suppress individual
    # hits at random the way it does totals. Neutralizing to small penalty
    # so it stays in narrative without inflating conviction.
    if park is not None:
        if park <= 93:
            conviction -= 3  # was +8 — audit showed 12% hit rate when fired
            signals['park'] = f'Park factor {park} — pitcher park (weight reduced per 6/9 audit)'
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

    # L7 cold + active hitless streak — REWEIGHTED 2026-06-09 after 7-day
    # STRONG hits_under audit (41% hit rate over 41 plays). The cold-streak
    # signals were the worst offenders: hitless_streak ≥3 was firing on
    # losses 58% of the time (15-11), l7_cold 56% (20-16), l7_avg_cold
    # 58% (22-16), l7_cool 100% (4-0). The 5/30 audit that boosted these
    # was on a different sample regime; current data shows cold batters
    # regress UP, not down further. Inverted to small penalty so the
    # signals stay narrative without driving conviction up.
    l7 = fetch_batter_l7(batter)
    if l7:
        rate = l7['got_hit_rate']
        n = l7['games']
        avg = l7.get('avg')
        streak = l7.get('hitless_streak', 0)
        if rate <= 0.35:
            conviction -= 3  # was +14 — 56% loss rate in 6/1-6/8 audit
            signals['l7_cold'] = f'Only {l7["got_hit_count"]} of last {n} games w/ a hit (weight reduced per 6/9 audit — regression risk)'
        elif rate <= 0.50:
            conviction -= 3  # was +7 — 100% loss rate (4-0) in audit
            signals['l7_cool'] = f'Hits in {l7["got_hit_count"]} of last {n} ({rate*100:.0f}%) (weight reduced per 6/9 audit)'
        elif rate >= 0.80:
            conviction -= 12  # recent form opposes the fade (unchanged — this is fade-direction signal that works)
        if avg is not None and avg <= 0.180:
            conviction -= 3  # was +6 — 58% loss rate in audit
            signals['l7_avg_cold'] = f'L7 BA .{int(avg*1000):03d} (weight reduced per 6/9 audit)'
        elif avg is not None and avg >= 0.330:
            conviction -= 6
        # hitless_streak — was the 5/30 audit's strongest signal (+14).
        # Current 7d audit shows it firing on losses 58% of the time
        # (15-11 over 26 trials). Reversion to mean is winning.
        if streak >= 3:
            conviction -= 3  # was +14 — most reversed signal
            signals['hitless_streak'] = f'{streak} straight games w/o a hit (weight reduced per 6/9 audit — regression candidate)'
        elif streak >= 2:
            conviction += 0  # was +7 — neutral now

    # Barrel% slump detector (added 2026-05-11). Audit identified
    # hits-UNDER PRIME at 55.3% — half those misses are likely "unlucky
    # cold" batters (high barrel%, low BABIP — quality contact but no hits)
    # who are due for regression. Fade the fade signal when this fires.
    quality = fetch_batter_quality(batter)
    if quality and quality.get('barrel_pct') is not None and l7:
        barrel = quality['barrel_pct']
        l7_rate = l7.get('got_hit_rate', 1.0)
        # MLB avg barrel% is ~7.5%. Threshold ≥9% = above-average contact quality.
        # When cold L7 (rate ≤0.40) AND barreling = unlucky, due for regression.
        if barrel >= 9.0 and l7_rate <= 0.40:
            conviction -= 6
            signals['barrel_regression'] = (
                f'Barrel% {barrel:.1f}% (above avg) despite L7 cold — '
                'quality contact, due for regression'
            )
        elif barrel <= 4.0 and l7_rate <= 0.40:
            # Genuinely cold — no hard contact + no hits = real slump
            conviction += 4
            signals['barrel_genuinely_cold'] = f'Barrel% {barrel:.1f}% confirms cold (no quality contact)'

    # Anti-correlation gate (added 2026-05-23 from audit_signal_attribution).
    # When a cold-individual signal (hitless_streak / l7_cold / l7_cool) fires
    # AND the opp-team-offense signal also fires (i.e. "we're betting under on
    # cold batter facing weak team-context"), the pick loses badly:
    #   team_offense + hitless_streak -> 42.9% (vs 66.9% / 64.7% alone)
    #   l7_cold + team_offense        -> 44.0% (vs 66.9% / 61.2% alone)
    # Translation: weak team-context masks that the individual is due to
    # regress to mean. Two signals "saying yes" produces fewer wins than
    # either alone — classic anti-correlation. Strip conviction here so the
    # combined pick lands at LEAN instead of getting stacked into PRIME.
    has_team_offense_signal = 'team_offense' in signals
    individual_cold_signals = ('hitless_streak', 'l7_cold', 'l7_cool')
    has_individual_cold = any(s in signals for s in individual_cold_signals)
    if has_team_offense_signal and has_individual_cold:
        conviction -= 9
        signals['anticorr_team_individual'] = (
            'Anti-correlation gate: team-context + individual-cold combo '
            'audits 43-44% (n=14-25) per 5/23 attribution scan'
        )

    conviction = max(0, min(100, conviction))

    # PRIME multi-signal gate — 2026-05-30 REWRITE based on 324-prop audit.
    #
    # AUDIT FINDINGS:
    # - PRIME 59.8% vs STRONG 58.4% on n=127+166 — gate barely differentiates
    # - prime_gate flag itself audits at 55.2% PRESENT vs 60.0% ABSENT
    #   (delta -4.8pt) — the gating predicate was anti-predictive
    # - Strongest single signals: hitless_streak (69.0%), team_cold (68.7%),
    #   l14_cold (65.5%), park (65.2%), l7_avg_cold (62.1%), l7_cold (62.6%)
    # - Strongest 2-signal pairs: hitless_streak + l14_cold (84.6% n=13),
    #   hitless_streak + l7_avg_cold (75.0% n=84), hitless_streak +
    #   team_cold (80.0% n=15)
    #
    # New PRIME requires:
    #   - elite opp (xERA ≤ 3.0)  — directional filter, not signal weight
    #   AND hitless_streak ≥ 3 (the only single signal that hits 65%+ alone)
    #   AND one of (team_cold drift ≤ -1.0, l14_cold, park ≤ 93)
    #     — the companion winners that pair with hitless_streak at 80%+
    #
    # opp_k_artist and bottom_and_cold are REMOVED as PRIME predicates
    # because they audit at 58.1% and ~59.5% respectively (not PRIME-grade).
    if conviction >= 85:
        gate_ace = opp_quality is not None and opp_quality <= 3.0
        active_ice = l7 and l7.get('hitless_streak', 0) >= 3
        # Companion-signal gate — must pair with hitless_streak. Audit
        # showed pairs at 75-84% but bare hitless_streak alone at 56.1%
        # in PRIME (the over-promotion that was killing PRIME hit rate).
        has_team_cold = team_drift is not None and team_drift <= -1.0
        has_l14_cold = 'l14_cold' in signals
        has_pitcher_park = park is not None and park <= 93
        companion_winner = has_team_cold or has_l14_cold or has_pitcher_park

        if not (gate_ace and active_ice and companion_winner):
            conviction = 84  # cap at STRONG
            signals['prime_gate'] = (
                f'PRIME capped — new 5/30 gate not met '
                f'(elite_opp={bool(gate_ace)}, hitless_streak≥3={bool(active_ice)}, '
                f'companion_winner={bool(companion_winner)} '
                f'[team_cold={has_team_cold}, l14_cold={has_l14_cold}, '
                f'pitcher_park={has_pitcher_park}]). '
                'PRIME requires hitless_streak + winning companion per 324-prop audit.'
            )

    # ── Fix #3: game-level cohort gate (2026-06-09) ──
    # If the game's TOTAL has a v3_tot_over_lean or LOCK over-direction
    # cohort match, this game projects to score runs. Hits_under in a
    # high-scoring game has structurally worse hit rate (more PAs per
    # batter, more runs scored = more hits distributed). Penalize.
    try:
        from cohort_signals import evaluate_game_for_play as _cohort_eval
        over_matches = _cohort_eval(g, 'v3_tot', 'over') or []
        # Look for any LOCK or STRONG_EDGE on the OVER side
        for m in over_matches:
            if m.get('tier') in ('LOCK', 'STRONG_EDGE'):
                conviction -= 10
                signals['game_over_lean_fade'] = (
                    f'Game projecting OVER (cohort: {m.get("matches_if_raw")} '
                    f'{m.get("shrunken_pct")}%) — hits_under unreliable in high-scoring spots '
                    '(2026-06-09 wire)'
                )
                break
    except Exception:
        pass

    # ── Fix #1: tier cap (2026-06-09) ──
    # 7d audit showed STRONG hits_under at 41% (random baseline for
    # 0.5-hit unders ~ 32-42%). The 75-84 conviction band specifically
    # is the worst — higher signal stacks underperform due to over-fitting.
    # Cap conviction at 74 unless multi-signal stack genuinely qualifies
    # by hitting the 5/30 PRIME gate criteria (which only fires when
    # conviction was ≥85 pre-cap).
    if conviction >= 75 and conviction < 85:
        signals['hits_under_strong_cap'] = (
            f'STRONG tier capped — conviction band below the qualifying '
            f'threshold. Need conviction ≥85 to qualify for STRONG '
            '(2026-06-09 wire)'
        )
        conviction = 74  # drops to LIGHT_LEAN ceiling

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': 0.5,
    }


def wipe_todays_props(skip_live_game_ids=None, max_stale_hours: int = 6):
    """Prune STALE prop rows for today; preserve fresh ones so downstream
    decisions (prop_playbook_decisions) don't get orphaned mid-day.

    2026-08-21 — replaced blanket DELETE with staleness-based prune. The
    old behavior wiped every non-live prop for today and let the upsert
    rewrite from scratch. That worked in isolation but stranded any
    `prop_playbook_decisions` row whose source prop got wiped between
    morning scoring and evening publish (Cameron outs_over 17.5 case:
    STRONG BACK decision scored at 10:43 UTC survived, but its source
    prop got wiped, so the 5:23 PM signal port never reached it).

    New behavior: only prune props whose `last_attached_at` is older than
    `max_stale_hours` (default 6) or NULL. The next `attach_book_lines`
    pass restamps `last_attached_at=now()` for every fresh generation,
    so props re-touched this run are kept. Anything not re-touched for
    6+ hours is genuinely stale (line pulled, batter scratched, market
    dropped) and safe to remove.

    2026-05-31 — `skip_live_game_ids` preserved. Games already started
    at cron time don't have fresh pre-game markets; the PM cron was
    wiping morning book-attached props (Misiorowski K Over ✓book STRONG
    75) and re-publishing them at the internal line with inflated PRIME
    conviction. Live games still exempt from prune here.
    """
    from datetime import datetime, timezone, timedelta
    gd = today_et()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_stale_hours)).isoformat()
    # Build filter: game_date=today AND last_attached_at IS NULL or older than cutoff
    base = (f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props"
            f"?game_date=eq.{gd}"
            f"&or=(last_attached_at.is.null,last_attached_at.lt.{cutoff})")
    if skip_live_game_ids:
        ids_csv = ','.join(f'"{gid}"' for gid in skip_live_game_ids)
        url = f"{base}&game_id=not.in.({ids_csv})"
    else:
        url = base
    try:
        r = requests.delete(url, headers=HEADERS, timeout=15)
        # Response body is empty w/ Prefer=return=minimal; we only care about status
        if r.status_code not in (200, 204):
            print(f"  ! prune_stale_props returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  ! prune_stale_props error: {e}")


# Phase 2 (2026-05-29) — book-line recalibration maps. Each pitcher prop
# type maps to (a) the Odds API market key, (b) the signal key holding the
# numeric projection used to compute edge, (c) edge → conviction-multiplier
# bands tuned to the natural variance of that prop type, and (d) the
# conviction range where a SKIP-tier prop with positive edge gets promoted
# to LEAN. Phase 1 shipped K's only; Phase 2 extends to BB / HA / Outs / ER
# using the same edge-band recipe scaled per prop.
PROP_MARKET_MAP = {
    'ks_over': 'pitcher_strikeouts',     'ks_under': 'pitcher_strikeouts',
    'bb_over': 'pitcher_walks',          'bb_under': 'pitcher_walks',
    'ha_over': 'pitcher_hits_allowed',   'ha_under': 'pitcher_hits_allowed',
    'outs_over': 'pitcher_outs',         'outs_under': 'pitcher_outs',
    'er_over': 'pitcher_earned_runs',    'er_under': 'pitcher_earned_runs',
}
PROP_PROJ_KEY = {
    'ks_over': '_projected_ks',     'ks_under': '_projected_ks',
    'bb_over': '_projected_bb',     'bb_under': '_projected_bb',
    'ha_over': '_projected_hits',   'ha_under': '_projected_hits',
    'outs_over': '_projected_outs', 'outs_under': '_projected_outs',
    'er_over': '_projected_er',     'er_under': '_projected_er',
}
# Edge bands per prop group — (edge_threshold, multiplier, label). Walked
# top-down: first row whose threshold is met wins. Anything below the
# smallest row falls through to NO-EDGE multiplier 0.30. Scales reflect
# the natural unit-variance of each market — K's swing 1-2 per start, BB
# swings 0.3-0.5, ER swings 0.3-1.0.
EDGE_BANDS = {
    'ks':   [(1.5, 1.00, 'real edge'), (1.0, 0.90, 'moderate edge'), (0.5, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'ha':   [(1.5, 1.00, 'real edge'), (1.0, 0.90, 'moderate edge'), (0.5, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'outs': [(3.0, 1.00, 'real edge'), (2.0, 0.90, 'moderate edge'), (1.0, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'bb':   [(0.5, 1.00, 'real edge'), (0.3, 0.90, 'moderate edge'), (0.1, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'er':   [(1.0, 1.00, 'real edge'), (0.5, 0.90, 'moderate edge'), (0.3, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
}
# LEAN promotion ranges. After recal-tier comes back SKIP, if edge > 0
# AND conviction lands in this range, promote SKIP → LEAN. K/Outs/ER use
# 55-69 (between STRONG and the higher SKIP floor). BB/HA use 40-54
# because their tier_for thresholds run lower (PRIME 70 / STRONG 55).
LEAN_PROMOTION_RANGES = {
    'ks_over': (55, 70), 'ks_under': (55, 70),
    'outs_over': (55, 70), 'outs_under': (55, 70),
    'er_over': (55, 70), 'er_under': (55, 70),
    'bb_over': (40, 55), 'bb_under': (40, 55),
    'ha_over': (40, 55), 'ha_under': (40, 55),
}
_PROP_UNIT = {'ks': 'K', 'bb': 'BB', 'ha': 'H', 'outs': 'outs', 'er': 'ER'}
_PROP_GROUP_LABEL = {'ks': 'Ks', 'bb': 'BB', 'ha': 'Hits Allowed', 'outs': 'Outs', 'er': 'ER'}


def fetch_book_lines_for_market(date_str, market):
    """Fetch sportsbook lines per pitcher for ANY pitcher-prop market.

    market: Odds API market key — 'pitcher_strikeouts', 'pitcher_walks',
    'pitcher_hits_allowed', 'pitcher_outs', or 'pitcher_earned_runs'.

    Returns {pitcher_name_lower: {'line': float, 'over': int, 'under': int,
                                   'source': str, 'n_books': int}}.

    Architecture: book lines are REFERENCE DATA. The pipeline's projections
    + signal-based scoring stay the source of truth; the book line is what
    gets *recalibrated against* downstream so we don't ship convictions
    calibrated to internal suggested lines that don't match the bettable
    market. Same recipe as Phase 1 K-only (5/28), generalized 5/29 to all
    pitcher prop types.

    Returns empty dict when ODDS_API_KEY missing or any call fails — never
    raises. Downstream consumers should treat None book_line as 'unavailable'.
    """
    ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
    book_map = {}
    if not ODDS_API_KEY:
        print(f"  ⚠️ ODDS_API_KEY missing — skipping book line fetch ({market})")
        return book_map

    # Retry-with-backoff on transient failures (added 2026-05-29 after the
    # 5/29 6am cron hit a transient 401 and silently swallowed it — book
    # lines were NULL on all K props for the day until manual re-run).
    # Three attempts with backoff: 0s, 2s, 6s. Loud logging when each retry
    # fires + when the final attempt fails so the cron log surfaces it.
    import time as _time
    events = None
    for attempt in range(3):
        try:
            now_utc = datetime.now(timezone.utc)
            events_r = requests.get(
                "https://api.the-odds-api.com/v4/sports/baseball_mlb/events",
                params={
                    'apiKey': ODDS_API_KEY,
                    'commenceTimeFrom': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
                },
                timeout=15,
            )
            if events_r.status_code == 200:
                events = events_r.json() or []
                break
            print(f"  ⚠️ events fetch attempt {attempt+1}/3 → status {events_r.status_code}: {events_r.text[:160]}")
        except Exception as e:
            print(f"  ⚠️ events fetch attempt {attempt+1}/3 → {type(e).__name__}: {e}")
        if attempt < 2:
            _time.sleep([0, 2, 6][attempt + 1])
    if events is None:
        print("  🚨 BOOK LINES: events endpoint failed after 3 attempts — props will have NULL book_line for this run")
        return book_map
    try:
        for ev in events:
            ev_id = ev.get('id')
            if not ev_id:
                continue
            try:
                odds_r = requests.get(
                    f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{ev_id}/odds",
                    params={
                        'apiKey': ODDS_API_KEY,
                        # 2026-06-03: was 'us' only — missed Fliff, theScore Bet,
                        # Hard Rock Bet, Hard Rock Bet (OH) which Odds API
                        # classifies under us2. Critical for pitcher_walks
                        # specifically — main us books often don't post BB
                        # markets but us2 books do. Williams BB U 1.5 was
                        # SKIP'd 6/3 because of this.
                        'regions': 'us,us2',
                        'markets': market,
                        'oddsFormat': 'american',
                    },
                    timeout=15,
                )
                if odds_r.status_code != 200:
                    continue
                data = odds_r.json()
                # Each bookmaker has its own line; collect across books and
                # take the median (Odds API returns multiple — DraftKings,
                # FanDuel, etc.). For each pitcher we want ONE line.
                pitcher_books = {}  # name → list of (line, over, under, src)
                for bm in data.get('bookmakers', []):
                    book_src = bm.get('title') or bm.get('key')
                    for mkt in bm.get('markets', []):
                        if mkt.get('key') != market:
                            continue
                        # Pair Over/Under by pitcher (description = pitcher name)
                        by_pitcher = {}  # pitcher_name → {Over: (line, odds), Under: (line, odds)}
                        for o in mkt.get('outcomes', []):
                            pname = o.get('description', '').strip()
                            side = (o.get('name') or '').strip()
                            point = o.get('point')
                            price = o.get('price')
                            if not (pname and side in ('Over', 'Under') and point is not None and price is not None):
                                continue
                            by_pitcher.setdefault(pname, {})[side] = (float(point), int(price))
                        for pname, sides in by_pitcher.items():
                            if 'Over' not in sides or 'Under' not in sides:
                                continue
                            over_line, over_odds = sides['Over']
                            under_line, under_odds = sides['Under']
                            # Lines should match O/U pair; if not, skip
                            if over_line != under_line:
                                continue
                            pitcher_books.setdefault(pname, []).append((over_line, over_odds, under_odds, book_src))
                # Median across books per pitcher
                for pname, entries in pitcher_books.items():
                    if not entries:
                        continue
                    lines = sorted(e[0] for e in entries)
                    median_line = lines[len(lines) // 2]
                    # Use the entry matching the median for odds
                    match = next((e for e in entries if e[0] == median_line), entries[0])
                    # Accent-fold the key so MLB-side names with diacritics
                    # (e.g., "Cristopher Sánchez") still hit even when Odds API
                    # stores the plain ASCII spelling.
                    book_map[_norm_name(pname)] = {
                        'line': match[0],
                        'over': match[1],
                        'under': match[2],
                        'source': match[3],
                        'n_books': len(entries),
                    }
            except Exception as e:
                continue
        print(f"  📖 Loaded book lines for {market}: {len(book_map)} pitchers")
    except Exception as e:
        print(f"  ⚠️ book line fetch failed: {e}")
    return book_map


def fetch_book_lines_for_ks(date_str):
    """Backwards-compatible alias preserved for any external caller."""
    return fetch_book_lines_for_market(date_str, 'pitcher_strikeouts')


def recalibrate_props_with_book_lines(props):
    """Replace prop_line with the actual book line and recalibrate conviction
    on the REAL edge at that line. Generalized 2026-05-29 from the K-only
    Phase 1 to cover all pitcher prop markets (K, BB, HA, Outs, ER).

    The signal-based scorer measures cohort quality ("this matchup favors
    Under") but doesn't know whether the book has already priced that signal
    in. When the book line is much tighter than our internal suggested line,
    the 'edge' collapses — the book captured the same signal we did.

    Phase 1 triggers (K's, 2026-05-28) — Paddack/Pallante/Lorenzen/etc K
    props were tagged STRONG 80+ with zero or negative edge at the book line.
    Phase 2 triggers (HA/ER, 2026-05-29) — Meyer U5.5 HA PRIME 91 → U4.5
    book = 0.9 hits cushion (STRONG), Lorenzen O2.5 ER PRIME 86 → O3.5 +110
    book = +0.5 ER edge (LEAN), Rodón U5.5 HA PRIME 80 → U4.5 book likely
    a SKIP.

    Recalibration: edge → multiplier → tier_for(). LEAN promotion floor
    when recalc lands at SKIP but edge > 0 (thin but legitimate at the
    actual bettable price). Trace fields `_internal_suggested_line`,
    `_book_line`, `_edge_at_book`, `_pre_recal_*`, `_recal_multiplier`,
    and `book_recalibration` retained so the 2-week backtest can grade:
    "Of all props recalibration demoted, what was the hit rate at the
    bettable book line?" If high, multipliers tune up.
    """
    for p in props:
        ptype = p.get('prop_type')
        if ptype not in PROP_PROJ_KEY:
            continue
        book_line = p.get('book_line')
        if book_line is None:
            continue
        sigs = p.get('signals') or {}
        proj = sigs.get(PROP_PROJ_KEY[ptype])
        if proj is None:
            continue
        try:
            proj_f = float(proj)
            book_f = float(book_line)
        except (TypeError, ValueError):
            continue

        # Edge for THIS direction. Positive = good for bettor at the book line.
        is_under = ptype.endswith('_under')
        edge = (book_f - proj_f) if is_under else (proj_f - book_f)

        # Pick conviction multiplier from prop-type-specific edge band
        group = ptype.split('_')[0]  # ks / bb / ha / outs / er
        bands = EDGE_BANDS.get(group)
        if not bands:
            continue
        mult, note = 0.30, 'NO EDGE (book priced our signal in)'
        for thr, m, lbl in bands:
            if edge >= thr:
                mult, note = m, lbl
                break

        old_conv = int(p.get('conviction', 0))
        old_line = p.get('prop_line')
        old_tier = p.get('tier')
        new_conv = max(0, int(round(old_conv * mult)))

        # Trace fields so user / audit can see the recalibration.
        # _pre_recal_* preserve what the scorer WOULD have published before
        # the book-line gate. Powers the 2-week backtest: "Of all props the
        # recalibration demoted, what would the hit rate have been at the
        # pre-recal tier?" If that number is high, multipliers are too
        # aggressive and we tune up.
        sigs['_internal_suggested_line'] = old_line
        sigs['_book_line'] = book_f
        sigs['_edge_at_book'] = round(edge, 2)
        sigs['_pre_recal_tier'] = old_tier
        sigs['_pre_recal_conviction'] = old_conv
        sigs['_recal_multiplier'] = mult
        unit = _PROP_UNIT.get(group, '')
        market_label = _PROP_GROUP_LABEL.get(group, group.title())
        direction = 'Under' if is_under else 'Over'
        sigs['book_recalibration'] = (
            f"Book {market_label} {direction} {book_f} vs proj {proj_f} "
            f"= {edge:+.1f} {unit} edge — {note}. "
            f"Conviction {old_conv} → {new_conv} (×{mult:.2f})."
        )
        p['signals'] = sigs

        # Replace the displayed line with the actual book line
        p['prop_line'] = book_f
        p['conviction'] = new_conv
        # Tier auto-re-derives from conviction
        recalc_tier = tier_for(new_conv, ptype)
        # LEAN promotion floor (added 2026-05-29). When recalc lands at
        # SKIP but the edge is positive AND conviction sits in the prop-
        # type's promotion range, surface as LEAN — thin but legitimate
        # edge at the actual book line. The positive-edge gate prevents
        # reintroducing the old thin plays that had no real edge.
        lean_lo, lean_hi = LEAN_PROMOTION_RANGES.get(ptype, (None, None))
        if recalc_tier == 'SKIP' and edge > 0.0 and lean_lo is not None and lean_lo <= new_conv < lean_hi:
            recalc_tier = 'LEAN'
            sigs['book_recalibration'] += " · Promoted to LEAN — thin but positive edge at book."
            p['signals'] = sigs
        p['tier'] = recalc_tier


def recalibrate_k_props_with_book_lines(props):
    """Backwards-compatible alias — runs the generalized recalibration which
    now covers K + BB + HA + Outs + ER markets."""
    return recalibrate_props_with_book_lines(props)


PRESERVE_STALE_MAX_HOURS = 6  # how long an old attach is trusted on fresh-attach failure


def _snapshot_existing_book_lines(date_str):
    """Read currently-stored book_line + book_*_odds + last_attached_at off
    today's props BEFORE the wipe. Returns dict keyed
    (game_id, normalized_player_name, prop_type) → {line, over, under,
    source, attached_at}. Used by attach_book_lines to preserve recently-
    confirmed lines when a fresh Odds API call returns empty for that
    pitcher/market.

    Safe-when-column-missing: if last_attached_at column doesn't exist
    yet, retries without it and stamps attached_at=None (snapshot still
    preserves the line, but the recency gate falls open — preserve only
    when the original DB row had a value). Idempotent on re-run.
    """
    if not date_str:
        return {}
    select = "game_id,player_name,prop_type,book_line,book_over_odds,book_under_odds,book_source,last_attached_at"
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props"
            f"?game_date=eq.{date_str}&book_line=not.is.null&select={select}",
            headers=HEADERS, timeout=10,
        )
        if r.status_code == 400:
            # last_attached_at column probably not migrated yet — retry without
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props"
                f"?game_date=eq.{date_str}&book_line=not.is.null"
                f"&select=game_id,player_name,prop_type,book_line,book_over_odds,book_under_odds,book_source",
                headers=HEADERS, timeout=10,
            )
        if r.status_code != 200:
            return {}
        snap = {}
        for row in r.json() or []:
            key = (row.get('game_id'), _norm_name(row.get('player_name')), row.get('prop_type'))
            snap[key] = {
                'line': row.get('book_line'),
                'over': row.get('book_over_odds'),
                'under': row.get('book_under_odds'),
                'source': row.get('book_source'),
                'attached_at': row.get('last_attached_at'),
            }
        return snap
    except Exception as e:
        print(f"  ⚠️ snapshot existing book lines failed (continuing without preserve): {e}")
        return {}


def _preserve_is_fresh(attached_at_iso):
    """Is the previous attach recent enough to trust on a fresh-call failure?

    Returns True when last_attached_at is within PRESERVE_STALE_MAX_HOURS.
    When the column wasn't yet migrated (attached_at None on the row) we
    fall through to True for backward compat — preserve anything we had,
    because the alternative is dropping the line and going SKIP."""
    if not attached_at_iso:
        return True  # no timestamp on file → assume legacy row from this cron pass
    try:
        from datetime import datetime as _dt, timezone as _tz
        t = _dt.fromisoformat(str(attached_at_iso).replace('Z', '+00:00'))
        age_hours = (_dt.now(_tz.utc) - t).total_seconds() / 3600
        return age_hours <= PRESERVE_STALE_MAX_HOURS
    except Exception:
        return True


def attach_book_lines(props):
    """Attach book_line / book_over_odds / book_under_odds / book_source to
    ALL pitcher props (K, BB, HA, Outs, ER) in-place. Reference data only —
    `recalibrate_props_with_book_lines` runs immediately after to update
    prop_line + conviction based on the real edge.

    Phase 2 (2026-05-29) generalized from K-only. Fetches each Odds API
    market once per cron (events endpoint cached across markets via the
    underlying retry path).

    2026-06-03 preserve-on-failure: before the fresh-attach loop, snapshot
    {line, odds, source, last_attached_at} from currently-stored rows. When
    a fresh API call returns empty for a pitcher/market AND the snapshot
    has a value attached within PRESERVE_STALE_MAX_HOURS (6h), restore the
    snapshot line. This stops the recurring "line vanished" pattern when
    Odds API throttles, a market hasn't posted yet, or a region scope bug
    excludes some books (us2 incident, 6/3). Stamps last_attached_at='now'
    on every successful attach (fresh OR preserved)."""
    needed_markets = {}  # market → set of pitcher names
    for p in props:
        market = PROP_MARKET_MAP.get(p.get('prop_type'))
        if not market:
            continue
        name = (p.get('player_name') or '').strip()
        if name:
            needed_markets.setdefault(market, set()).add(name)
    if not needed_markets:
        return
    date_str = today_et()
    # 2026-06-03 preserve snapshot: pull currently-stored book lines BEFORE
    # we hit the API so we have a fallback for any pitcher/market the fresh
    # call misses.
    snapshot = _snapshot_existing_book_lines(date_str)
    from datetime import datetime as _dt, timezone as _tz
    now_iso = _dt.now(_tz.utc).isoformat()

    for market, pitchers in needed_markets.items():
        book_map = fetch_book_lines_for_market(date_str, market)
        fresh = 0
        preserved = 0
        for p in props:
            if PROP_MARKET_MAP.get(p.get('prop_type')) != market:
                continue
            # Accent-fold the lookup so Sánchez → sanchez matches the book_map
            # key we built the same way above.
            name = _norm_name(p.get('player_name'))
            bk = (book_map or {}).get(name)
            if bk:
                # Fresh attach succeeded — use the new line + stamp now.
                p['book_line'] = bk['line']
                p['book_over_odds'] = bk['over']
                p['book_under_odds'] = bk['under']
                p['book_source'] = f"{bk['source']} (median of {bk['n_books']})"
                p['last_attached_at'] = now_iso
                fresh += 1
                continue
            # Fresh attach missed — try snapshot.
            key = (p.get('game_id'), name, p.get('prop_type'))
            snap = snapshot.get(key)
            if not snap or snap.get('line') is None:
                continue
            if not _preserve_is_fresh(snap.get('attached_at')):
                continue
            # Preserve: restore the prior line + odds, keep prior timestamp.
            p['book_line'] = snap['line']
            p['book_over_odds'] = snap['over']
            p['book_under_odds'] = snap['under']
            p['book_source'] = (snap.get('source') or '') + ' [preserved]'
            p['last_attached_at'] = snap.get('attached_at')
            preserved += 1
        status = f"fresh {fresh}"
        if preserved:
            status += f" + preserved {preserved}"
        print(f"  📖 {market}: {status} (of {len(pitchers)} pitchers in scope)")


def upsert_props(props):
    """Upsert prop rows. Falls back to stripping the lineup_state field if
    Supabase rejects it (column doesn't exist yet — user needs to run:
      ALTER TABLE mlb_pipeline_props ADD COLUMN lineup_state TEXT;
    Once added, the field flows through and the app can render PROJECTED
    vs CONFIRMED tags on hits props.)"""
    if not props:
        return 0
    # 2026-08-22 DEDUP GUARD: same (game_date, player_name, prop_type, direction,
    # prop_line) was landing in the DB 2-4× today (Andrew Painter er_over 2.5
    # appeared 4× as PRIME c=94, Willi Castro / Cole Carrigg hits_over 0.5
    # appeared 2×). Root cause: multiple call sites emit rows for the same
    # prop from different code paths and no on_conflict resolution was set
    # on the POST. Fix here (defense-in-depth) collapses dupes in-Python
    # before write, keeping the row with the highest conviction (or first
    # populated L10 if conviction is tied) so downstream sees exactly one
    # authoritative row per prop.
    _dedup_seen = {}
    for p in props:
        key = (p.get('game_date'), (p.get('player_name') or '').lower(),
               p.get('prop_type'), p.get('direction'),
               p.get('prop_line'))
        if None in key[:4]:
            # missing critical field — keep unique via id path (rare)
            continue
        existing = _dedup_seen.get(key)
        if existing is None:
            _dedup_seen[key] = p
            continue
        # Prefer higher conviction; break ties on L10 presence, then last write.
        cur_conv = float(p.get('conviction') or 0)
        exist_conv = float(existing.get('conviction') or 0)
        if cur_conv > exist_conv:
            _dedup_seen[key] = p
        elif cur_conv == exist_conv:
            cur_l10 = p.get('player_l10_hit_count')
            exist_l10 = existing.get('player_l10_hit_count')
            if cur_l10 is not None and exist_l10 is None:
                _dedup_seen[key] = p
    _before_dedup = len(props)
    props = list(_dedup_seen.values()) + [p for p in props if None in (
        p.get('game_date'), (p.get('player_name') or '').lower(),
        p.get('prop_type'), p.get('direction'))]
    if _before_dedup != len(props):
        print(f"  🧹 dedup collapsed {_before_dedup - len(props)} duplicate props "
              f"({_before_dedup} → {len(props)})")

    # Normalize keys across the batch — PostgREST rejects mixed schemas with
    # "All object keys must match" when a column is on some records but not
    # others. Pitcher props pick up book_line / book_*_odds via attach_book_lines
    # but BATTER props (hits_over/under) never do — bug surfaced 5/29 when
    # the whole batch failed and 0 rows landed. Walk all rows, union the keys,
    # then backfill missing keys as None on each record.
    all_keys = set()
    for p in props:
        all_keys.update(p.keys())
    for p in props:
        for k in all_keys:
            if k not in p:
                p[k] = None
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
    #   (5/28) book_line / book_over_odds / book_under_odds / book_source
    #          per 20260528_book_lines_on_props.sql migration
    optional_cols = ('lineup_state', 'stack_alert',
                     'book_line', 'book_over_odds', 'book_under_odds', 'book_source',
                     'last_attached_at')
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
                # 2026-05-23: er_OVER STRONG cohort hits 50% on n=12 — coin
                # flip. Demote STRONG → LEAN unless L3 ERA is in extreme
                # regression territory (≥8.0) where the bad-pitcher thesis
                # is loud enough to be real, not just an above-avg pitcher
                # with one bad start.
                er_tier = tier_for(er_over['conviction'], 'er_over')
                l3_era_val = _f(g.get(f'{side}_pitcher_last_3_era'))
                if er_tier == 'STRONG' and (l3_era_val is None or l3_era_val < 8.0):
                    er_tier = 'LEAN'
                    er_over['signals']['er_strong_demoted'] = (
                        'STRONG demoted → LEAN: er_OVER STRONG audits 50% on n=12. '
                        f'L3 ERA {l3_era_val} not in regression territory (≥8.0 required).'
                    )
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
                    'tier': er_tier,
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

            # Walks Allowed O/U (added 2026-05-11) — projection-first prop
            # using L7 rolling avg_bb. _projected_bb in signals is the
            # headline number the app surfaces.
            bb_over = score_pitcher_bb_over(g, side)
            if bb_over and bb_over['conviction'] >= BB_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'bb_over',
                    'prop_line': bb_over['prop_line'],
                    'direction': 'over',
                    'conviction': bb_over['conviction'],
                    'tier': tier_for(bb_over['conviction'], 'bb_over'),
                    'signals': bb_over['signals'],
                    'lineup_state': 'confirmed',
                })
            bb_under = score_pitcher_bb_under(g, side)
            if bb_under and bb_under['conviction'] >= BB_UNDER_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'bb_under',
                    'prop_line': bb_under['prop_line'],
                    'direction': 'under',
                    'conviction': bb_under['conviction'],
                    'tier': tier_for(bb_under['conviction'], 'bb_under'),
                    'signals': bb_under['signals'],
                    'lineup_state': 'confirmed',
                })

            # Hits Allowed O/U (added 2026-05-11) — projection-first from L7 avg_hits
            ha_over = score_pitcher_ha_over(g, side)
            if ha_over and ha_over['conviction'] >= HA_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'ha_over',
                    'prop_line': ha_over['prop_line'],
                    'direction': 'over',
                    'conviction': ha_over['conviction'],
                    'tier': tier_for(ha_over['conviction'], 'ha_over'),
                    'signals': ha_over['signals'],
                    'lineup_state': 'confirmed',
                })
            ha_under = score_pitcher_ha_under(g, side)
            if ha_under and ha_under['conviction'] >= HA_UNDER_CUTOFF:
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': pitcher,
                    'player_team': g.get(f'{side}_team'),
                    'matchup': matchup,
                    'prop_type': 'ha_under',
                    'prop_line': ha_under['prop_line'],
                    'direction': 'under',
                    'conviction': ha_under['conviction'],
                    'tier': tier_for(ha_under['conviction'], 'ha_under'),
                    'signals': ha_under['signals'],
                    'lineup_state': 'confirmed',
                })

        # Batter Hits props — confirmed lineup preferred, projected as fallback.
        # Hybrid lineup approach (added 2026-05-02): if today's lineup not yet
        # posted by MLB, fall back to the team's most recent batting order from
        # box score. Mark the prop with lineup_state=projected so the app can
        # show a PROJECTED tag (and the next pipeline run with confirmed
        # lineups overwrites the row with state=confirmed).
        # 2026-08-21 FIX: per-side fallback. Prior logic used a game-level
        # `lineup_confirmed` flag to decide confirmed-vs-projected for BOTH
        # sides. In practice the upstream lineup writer sometimes sets the
        # flag=True while only one side's lineup string is populated
        # (Washington/Pittsburgh/NY Mets away lineups empty on 8/21). Result:
        # confirmed branch reads '' → 0 batters → skips → no batter props
        # for half the slate. Fixed volume collapse from ~20/g to ~3/g.
        # Now: check EACH side's lineup string independently; fall back to
        # projected when a side is empty even if game-level flag is True.
        confirmed_game = bool(g.get('lineup_confirmed'))
        for side, lineup_field in (('home', 'home_lineup'), ('away', 'away_lineup')):
            team_name = g.get(f'{side}_team')
            lineup_str = g.get(lineup_field) or ''
            batters = [b.strip() for b in lineup_str.split(',') if b.strip()][:9]
            if batters:
                lineup_state = 'confirmed' if confirmed_game else 'projected'
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
    # Tier-aware top-N (2026-05-31 fix): conviction numbers are calibrated
    # PER prop type — a hits_over at 77 STRONG and a bb_under at 76 PRIME
    # are not directly comparable. The old top_n slice sorted by raw
    # conviction and let STRONG hits-overs squeeze out PRIME pitcher props.
    # Bryce Miller bb_under PRIME 76 ✓book got dropped from PM publish on
    # 5/31 because 27 hits props at conviction >=77 filled the top 30.
    # Guarantee: every PRIME/STRONG prop passes regardless of slice index;
    # the top_n cap applies only to LEAN (and unrated SKIP-w/-trace) tier
    # rows used as filler at the bottom.
    guaranteed = [p for p in capped if p.get('tier') in ('PRIME', 'STRONG')]
    filler = [p for p in capped if p.get('tier') not in ('PRIME', 'STRONG')]
    remaining_slots = max(0, top_n - len(guaranteed))
    top = guaranteed + filler[:remaining_slots]

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

    # Server-composed display label (added 2026-05-26 per
    # feedback_backside_dictates_app_renders). The app was deciding K-prop
    # label format ("Cease Over 7.5 Ks · proj 8.6" vs "8.6 expected Ks (over)"
    # vs raw "ks_over"), with the sweat-card and Daily-Degen surfaces using
    # different formats — same data, different render. That caused the
    # Luzardo 5/25 incident: app showed "6.3 expected Ks (over)" but the
    # book line was 6.5, defaulting users into a -EV Over bet.
    #
    # Single source of truth: server composes _display_label, app reads it
    # verbatim. Future label changes = pure backend, no TestFlight push.
    # Display-label composition moved BELOW attach_book_lines /
    # recalibrate_props_with_book_lines (2026-06-01) so labels use the
    # actual book line, not the internal scorer's suggested line. Pre-fix
    # bug: server wrote `_display_label = "Joe Ryan Under 1.5 BB · proj 1.0"`
    # using the internal 1.5, then recalibrate replaced prop_line with the
    # book's 2.5, leaving the label stale. App showed wrong line. See
    # the actual composition after attach_book_lines below.

    # Attach real sportsbook lines for ALL pitcher prop markets (K / BB /
    # HA / Outs / ER) + recalibrate against the real edge at those lines.
    # Phase 1 (K-only, 5/28): user audit found 4/6 K-Under props tagged
    # STRONG had zero or negative edge at the actual book line.
    # Phase 2 (5/29): same problem confirmed in HA/ER — Meyer U5.5 PRIME 91
    # vs book U4.5, Lorenzen O2.5 PRIME 86 vs book O3.5 +110, Rodón U5.5
    # PRIME 80 vs book U4.5. Recalibration replaces prop_line with book_line
    # and adjusts conviction down when the real edge is thin or negative.
    attach_book_lines(top)
    recalibrate_props_with_book_lines(top)

    # Display-label composition — runs AFTER recalibrate so labels use the
    # actual book line. Uniform format across all prop types:
    #
    #   "{player} {Over|Under} {line} {market}  ·  proj {proj}"
    #
    # Beta-user 6/1 complaint: prop labels were inconsistent — "6.7 expected
    # K (over)" hid the line entirely (was the line 5.5 or 6.5?), while
    # "Over 5.5 Hits Allowed · proj 7.1" showed both clearly. Uniformity
    # restored here. App reads _display_label verbatim; Daily Degen builder
    # mirrors this format from the same fields.
    for p in top:
        sigs = p.get('signals') or {}
        ptype = p.get('prop_type', '')
        line = p.get('prop_line')
        player = p.get('player_name', '')
        proj_ks = sigs.get('_projected_ks')
        proj_bb = sigs.get('_projected_bb')
        proj_ha = sigs.get('_projected_hits')
        proj_outs = sigs.get('_projected_outs')
        proj_er = sigs.get('_projected_er')
        label = None
        if ptype == 'ks_over' and line is not None:
            label = f'{player} Over {line} Ks  ·  proj {proj_ks}' if proj_ks is not None else f'{player} Over {line} Ks'
        elif ptype == 'ks_under' and line is not None:
            label = f'{player} Under {line} Ks  ·  proj {proj_ks}' if proj_ks is not None else f'{player} Under {line} Ks'
        elif ptype == 'bb_over' and line is not None:
            label = f'{player} Over {line} BB  ·  proj {proj_bb}' if proj_bb is not None else f'{player} Over {line} BB'
        elif ptype == 'bb_under' and line is not None:
            label = f'{player} Under {line} BB  ·  proj {proj_bb}' if proj_bb is not None else f'{player} Under {line} BB'
        elif ptype == 'ha_over' and line is not None:
            label = f'{player} Over {line} Hits Allowed  ·  proj {proj_ha}' if proj_ha is not None else f'{player} Over {line} Hits Allowed'
        elif ptype == 'ha_under' and line is not None:
            label = f'{player} Under {line} Hits Allowed  ·  proj {proj_ha}' if proj_ha is not None else f'{player} Under {line} Hits Allowed'
        elif ptype == 'outs_over' and line is not None:
            label = f'{player} Over {line} Outs  ·  proj {proj_outs}' if proj_outs is not None else f'{player} Over {line} Outs'
        elif ptype == 'outs_under' and line is not None:
            label = f'{player} Under {line} Outs  ·  proj {proj_outs}' if proj_outs is not None else f'{player} Under {line} Outs'
        elif ptype == 'er_over' and line is not None:
            label = f'{player} Over {line} ER  ·  proj {proj_er}' if proj_er is not None else f'{player} Over {line} ER'
        elif ptype == 'er_under' and line is not None:
            label = f'{player} Under {line} ER  ·  proj {proj_er}' if proj_er is not None else f'{player} Under {line} ER'
        elif ptype == 'hits_over':
            label = f'{player} Over 0.5 Hits'
        elif ptype == 'hits_under':
            label = f'{player} Under 0.5 Hits (0-fer)'
        if label:
            sigs['_display_label'] = label
            p['signals'] = sigs

    # Suppress pitcher props that failed book attach (2026-06-01 fix).
    # The internal scorer suggests its own lines for K/BB/HA/Outs/ER props
    # (e.g. Freeland outs_under @ 17.5 when books may have 15.5). When
    # Phase 2 attach succeeds, prop_line gets replaced with the book line
    # and conviction is recalibrated against the real edge. When attach
    # FAILS (Odds API doesn't list this pitcher's market, transient outage,
    # late-confirmed starter), the internal-line tier publishes as if it
    # were book-verified — beta users see "Freeland over 17.5 outs PRIME"
    # in the app and shop their book only to find it doesn't exist or has
    # a much tighter line. Trust killer.
    #
    # Fix: demote any pitcher prop with book_line=None to SKIP tier, mark
    # the reason, and keep in DB for backtest visibility (same pattern as
    # recalibrated-to-SKIP rows). App filters tier IN (PRIME, STRONG, LEAN)
    # so these stay silent in UI. Hitter props (hits_over/under) at line
    # 0.5 are line-agnostic and don't need book attach, so they're exempt.
    for p in top:
        if p.get('prop_type') not in PROP_MARKET_MAP:
            continue  # not a pitcher prop, no book line needed
        if p.get('book_line') is not None:
            continue  # attach worked, all good
        # Pitcher prop with no book line — suppress
        sigs = p.get('signals') or {}
        sigs.setdefault('_internal_only', True)
        sigs.setdefault('_suppression_reason',
                        f'Phase 2 attach failed: no book line found for {p.get("prop_type")} '
                        f'on {p.get("player_name")}. Internal scorer line was {p.get("prop_line")}; '
                        f'unsafe to publish without book verification.')
        sigs.setdefault('_pre_attach_tier', p.get('tier'))
        sigs.setdefault('_pre_attach_conviction', p.get('conviction'))
        p['signals'] = sigs
        p['tier'] = 'SKIP'

    # Lineup re-confirmation — detect mid-day starter scratches and
    # invalidate affected props. 2026-06-01 trigger: Chase Burns PRIME 91
    # K-Over published in the AM cron, scratched by 2pm. Without this
    # check the morning props stayed in DB as PRIME-tier dead bets and
    # KC hitter unders (Loftin, Marte) stayed calibrated against a pitcher
    # who wasn't even pitching. See detect_scratched_starters docstring.
    #
    # Also handles the lingering-stale-props case: when DB has caught up
    # to MLB Stats API (both show Richardson now) BUT the old starter's
    # props are still in mlb_pipeline_props from a prior cron, compare
    # against all pitcher_name values currently published — anything that
    # doesn't match either current starter for its game gets deleted.
    try:
        scratched = detect_scratched_starters(games, gd)
    except Exception as e:
        print(f"  ⚠️  Scratch detection unexpected failure: {type(e).__name__}: {e}")
        scratched = set()

    # Cleanup stale published props from earlier crons whose pitchers no
    # longer match the current home/away. This catches the "Burns props
    # still in DB even though game_context updated to Richardson" case.
    try:
        valid_pitcher_names = set()
        valid_game_pitchers = {}  # game_id -> set of currently valid pitcher names
        for g in games:
            valid_for_this_game = set()
            for sp in (g.get('home_pitcher'), g.get('away_pitcher')):
                if sp:
                    valid_pitcher_names.add(sp.strip().lower())
                    valid_for_this_game.add(sp.strip().lower())
            valid_game_pitchers[g.get('game_id')] = valid_for_this_game

        # Pull currently-published pitcher props for today (in DB but not in
        # our upsert payload yet — these are from earlier crons).
        from urllib.parse import urlencode
        qs = urlencode({
            'game_date': f'eq.{gd}',
            'prop_type': f'in.({",".join(PROP_MARKET_MAP.keys())})',
            'tier': 'neq.COVERAGE',   # sweep_prop_coverage.py owns these; do not delete
            'select': 'player_name,game_id,prop_type',
        })
        r = requests.get(f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?{qs}",
                         headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
                         timeout=15)
        if r.status_code == 200:
            existing = r.json() or []
            stale_to_delete = []
            for row in existing:
                gid = row.get('game_id')
                pname = (row.get('player_name') or '').strip().lower()
                if gid in valid_game_pitchers and pname and pname not in valid_game_pitchers[gid]:
                    stale_to_delete.append(row)
            if stale_to_delete:
                # Delete each stale row by (player_name, prop_type, game_date) match.
                # Safer than bulk-IN because some books reuse pitcher names.
                deleted = 0
                for row in stale_to_delete:
                    del_qs = urlencode({
                        'game_date': f'eq.{gd}',
                        'game_id': f'eq.{row.get("game_id")}',
                        'player_name': f'eq.{row.get("player_name")}',
                        'prop_type': f'eq.{row.get("prop_type")}',
                    })
                    dr = requests.delete(
                        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?{del_qs}",
                        headers=HEADERS, timeout=10,
                    )
                    if dr.status_code in (200, 204):
                        deleted += 1
                if deleted:
                    print(f"  🧹 Cleaned up {deleted} stale published props from prior-cron scratched starters")
    except Exception as e:
        print(f"  ⚠️  Stale-props cleanup failed: {type(e).__name__}: {e}")

    if scratched:
        pitcher_invalidated = 0
        hitter_flagged = 0
        scratched_game_ids = {gid for gid, _old, _new in scratched}
        scratched_old_names = {old.strip().lower() for _gid, old, _new in scratched}
        for p in top:
            ptype = p.get('prop_type')
            player = (p.get('player_name') or '').strip().lower()
            gid = p.get('game_id')
            # Pitcher props anchored to the scratched starter — kill them outright
            if ptype in PROP_MARKET_MAP and player in scratched_old_names:
                sigs = p.get('signals') or {}
                old_match = next(((o, n) for _gid, o, n in scratched
                                 if _gid == gid and o.strip().lower() == player), None)
                if old_match:
                    sigs.setdefault('_suppression_reason',
                                    f'{old_match[0]} scratched — replaced by {old_match[1]}. Prop invalid.')
                    sigs.setdefault('_pre_scratch_tier', p.get('tier'))
                    p['signals'] = sigs
                    p['tier'] = 'SKIP'
                    pitcher_invalidated += 1
            # Hitter props in scratched games — flag and demote one tier.
            # Their scoring depended on the old starter's K-artist / xERA /
            # mastery profile which may no longer apply. Don't outright skip
            # (the broader game-level signals like team_cold, lineup_spot,
            # hitless_streak still hold), but demote to acknowledge the
            # uncertainty until we can re-score with the new starter.
            elif ptype in ('hits_over', 'hits_under') and gid in scratched_game_ids:
                sigs = p.get('signals') or {}
                sigs.setdefault('_starter_scratch_flag',
                                'Game has a scratched starter — opp_starter signals in this score may be stale')
                # Demote one tier (PRIME → STRONG, STRONG → LEAN, LEAN → SKIP)
                tier = p.get('tier')
                if tier == 'PRIME': p['tier'] = 'STRONG'
                elif tier == 'STRONG': p['tier'] = 'LEAN'
                elif tier == 'LEAN': p['tier'] = 'SKIP'
                p['signals'] = sigs
                hitter_flagged += 1
        print(f"  🔄 Lineup scratch: invalidated {pitcher_invalidated} pitcher props, demoted {hitter_flagged} hitter props in affected games")

    # Re-sort by conviction and re-tier-filter so demoted props drop off
    # the published list — EXCEPT recalibrated props that landed at SKIP:
    # those get kept so the backtest can query what got demoted and grade
    # hypothetical outcomes. App / sweat card filter by tier IN (PRIME,
    # STRONG, LEAN) for display so SKIP'd props stay silent in the UI.
    # The _pre_recal_tier and _pre_attach_tier trace fields make them
    # auditable.
    def _keep(p):
        if p.get('tier') in ('PRIME', 'STRONG', 'LEAN'):
            return True
        # Any recalibrated pitcher prop with trace — keep for backtest
        sigs = p.get('signals') or {}
        if p.get('prop_type') in PROP_MARKET_MAP and (
            sigs.get('_pre_recal_tier') or sigs.get('_pre_attach_tier')
        ):
            return True
        return False
    top = [p for p in top if _keep(p)]
    top.sort(key=lambda p: -(p.get('conviction') or 0))

    # Live-game preservation (2026-05-31). Games already underway by cron
    # time don't have fresh pre-game markets at the Odds API; if we wipe
    # their AM rows and re-publish, the book line drops, recalibration is
    # skipped, and conviction inflates against a stale internal line.
    # Filter out live games from the wipe (preserves AM state) AND from
    # the upsert payload (don't overwrite the same rows with worse data).
    # mlb_game_context doesn't store first-pitch time, so pull from MLB
    # Stats API and match by home team name (same pattern play_of_day uses).
    live_game_ids = set()
    try:
        sched_r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": gd},
            timeout=10,
        )
        sched_times = {}
        if sched_r.status_code == 200:
            for d in sched_r.json().get("dates", []):
                for sg in d.get("games", []):
                    home_name = (sg.get("teams") or {}).get("home", {}).get("team", {}).get("name", "")
                    ts = sg.get("gameDate")
                    if home_name and ts:
                        sched_times[home_name] = ts
        now_utc = datetime.now(timezone.utc)
        for g in games:
            ts = sched_times.get(g.get('home_team'))
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if t <= now_utc:
                    live_game_ids.add(g.get('game_id'))
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print(f"  ⚠️ live-game detect failed (continuing without preservation): {e}")
    if live_game_ids:
        before = len(top)
        top = [p for p in top if p.get('game_id') not in live_game_ids]
        print(f"  🔒 Live-game preserve: {len(live_game_ids)} game(s) past first pitch — kept AM props, dropped {before - len(top)} re-scored rows for those games from upsert")

    # 2026-08-22 DEDUP: one card per (player_name, stat_family). Previously
    # generate_props emitted BOTH over and under variants for the same
    # pitcher/batter/stat when both scored above the tier threshold. User
    # correctly flagged: 15 games × 30 pitchers should yield ~30 HA cards,
    # not 84. The app "matchup edges" counter on index.tsx:13351 was
    # inflated by both-sides duplicates × multiple book_source rows.
    #
    # Dedup rule: for each (player, stat_family), keep the side with the
    # higher conviction. Tie → prefer the tier ordering PRIME > STRONG > LEAN.
    # Note: STAT_FAMILY strips _over/_under suffix using the same helper
    # render_prop_template uses.
    _TIER_ORD = {'PRIME': 3, 'STRONG': 2, 'LEAN': 1, 'SKIP': 0, 'PASS': 0}
    def _stat_fam(pt: str) -> str:
        if not pt: return ''
        for suf in ('_over', '_under'):
            if pt.endswith(suf): return pt[:-len(suf)]
        return pt
    def _dedup_key(p):
        return ((p.get('player_name') or '').lower(),
                _stat_fam(p.get('prop_type') or ''))
    def _rank(p):
        # higher tuple wins the (player, stat) matchup
        return (_TIER_ORD.get((p.get('tier') or '').upper(), 0),
                float(p.get('conviction') or 0),
                float(p.get('refit_conviction') or 0))
    _before_dedup = len(top)
    _by_matchup = {}
    for p in top:
        k = _dedup_key(p)
        if k not in _by_matchup or _rank(p) > _rank(_by_matchup[k]):
            _by_matchup[k] = p
    top = list(_by_matchup.values())
    if _before_dedup != len(top):
        print(f"  🎯 Prop dedup: {_before_dedup} → {len(top)} rows "
              f"(one card per (player, stat) — winning direction kept)")

    wipe_todays_props(skip_live_game_ids=live_game_ids or None)
    saved = upsert_props(top)
    print(f"\n✅ Stored {saved} top props (of {len(all_props)} passing threshold)")
    for p in top[:8]:
        print(f"  [{p['conviction']}] {p['player_name']} {p['prop_type']} {p['prop_line']} ({p['tier']}) — {p['matchup']}")
        for k, v in p['signals'].items():
            print(f"      · {v}")

if __name__ == "__main__":
    run()
