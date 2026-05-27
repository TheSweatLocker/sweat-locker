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

# New 2026-05-05 — Total Outs and Earned Runs props. Floor at 65 until we
# have audit data. Recalibrate once n=20+ resolved per cohort.
OUTS_CUTOFF = 65
OUTS_UNDER_CUTOFF = 70
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
    if prop_type in ('bb_over', 'bb_under', 'ha_over', 'ha_under'):
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
    proj_bb = l7['avg_bb']
    if l7.get('avg_ip', 0) < 3.0:
        return None  # opener / short-relief profile

    opp_side = 'away' if side == 'home' else 'home'
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22  # patient-vs-aggressive proxy (lower K = more contact-y)
    first_inn_whip = _f(g.get(f'{side}_first_inning_whip'))
    days_rest = _f(g.get(f'{side}_days_rest'))

    signals = {}
    signals['_projected_bb'] = round(proj_bb, 1)
    conviction = 30

    # Primary: how far is L7 walk rate above the 1.5 line?
    # Thresholds widened 2026-05-13 — pre-patch only Schultz-style outliers
    # (L7 BB ≥3.0) cleared the +28 bonus; middling walks-prone arms like
    # McCullers (2.86) and Bradish (2.71) got stuck at slim-edge tier and
    # never cleared cutoff 55. New tiers: 0.7/0.4/0.15 (was 1.0/0.5/0.2).
    over_margin = proj_bb - 1.5
    if over_margin >= 0.7:
        conviction += 28
        signals['l7_walks'] = f'L7 avg {proj_bb:.1f} BB/start — {over_margin:+.1f} vs 1.5 line'
    elif over_margin >= 0.4:
        conviction += 18
        signals['l7_walks'] = f'L7 avg {proj_bb:.1f} BB/start — {over_margin:+.1f} vs 1.5 line'
    elif over_margin >= 0.15:
        conviction += 8
        signals['l7_walks'] = f'L7 avg {proj_bb:.1f} BB/start — slim edge over 1.5'
    else:
        return None  # not enough walk volume to bet the over

    # BB/9 layer — corroborates the per-start average
    bb9 = l7.get('bb_per_9')
    if bb9 is not None:
        if bb9 >= 4.0:
            conviction += 10
            signals['bb_rate'] = f'{bb9:.1f} BB/9 L7 — elevated walk rate'
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
    proj_bb = l7['avg_bb']
    if l7.get('avg_ip', 0) < 4.0:
        return None  # need a real innings load to bet UNDER on walks

    opp_side = 'away' if side == 'home' else 'home'
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22
    first_inn_whip = _f(g.get(f'{side}_first_inning_whip'))

    signals = {}
    signals['_projected_bb'] = round(proj_bb, 1)
    conviction = 30

    # Primary: how far is L7 walk rate below the 1.5 line?
    # Thresholds widened 2026-05-13 — pre-patch most pitchers projecting
    # 1.1-1.4 BB/start (clean BB-Unders) got stuck at +6 and never cleared
    # cutoff 55. New tiers: 0.4/0.2/0.1 (was 0.7/0.4/0.2). This surfaces
    # the natural cohort of low-walk arms (Lodolo 1.14, Gray 1.29, Ohtani
    # 1.29, B. Miller 1.29, Imanaga 1.57, Messick 1.57) which all clearly
    # project under the standard 1.5 BB line.
    under_margin = 1.5 - proj_bb
    if under_margin >= 0.4:
        conviction += 28
        signals['l7_control'] = f'L7 avg {proj_bb:.1f} BB/start — elite control, {under_margin:.1f} under 1.5'
    elif under_margin >= 0.2:
        conviction += 18
        signals['l7_control'] = f'L7 avg {proj_bb:.1f} BB/start — {under_margin:.1f} under 1.5'
    elif under_margin >= 0.05:
        conviction += 8
        signals['l7_control'] = f'L7 avg {proj_bb:.1f} BB/start — slim edge under 1.5'
    else:
        return None  # walks too high to bet the under

    bb9 = l7.get('bb_per_9')
    if bb9 is not None:
        if bb9 <= 2.0:
            conviction += 12
            signals['bb_rate'] = f'{bb9:.1f} BB/9 L7 — elite command'
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

    return {'conviction': conviction, 'signals': signals, 'prop_line': suggested_line}


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
    proj_h = l7['avg_hits']
    if l7.get('avg_ip', 0) < 4.0:
        return None  # short profile — hits-over unreliable

    opp_side = 'away' if side == 'home' else 'home'
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    park_run = _f(g.get('park_run_factor')) or 100
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))

    signals = {}
    signals['_projected_hits'] = round(proj_h, 1)
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

    if park_run >= 108:
        conviction += 4
    elif park_run <= 92:
        conviction -= 3

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
    proj_h = l7['avg_hits']
    if l7.get('avg_ip', 0) < 4.5:
        return None  # need real innings load for hits-under

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

    if park_run <= 92:
        conviction += 4
    elif park_run >= 108:
        conviction -= 3

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

    # Realistic K projection (uncapped, for app display) — book lines vary
    # widely in juice, so we surface the model's actual point estimate rather
    # than a juiced "Over X.X" framing. Prefer the L7 rolling avg_k from the
    # pitcher class-projection cache (more current + survives K%-parse failures
    # like Skenes/Sánchez where the pitcher_context name match misses). Fall
    # back to season K% × 22 BF (~5.5 IP) if no L7 data.
    proj = get_pitcher_projection(pitcher)
    l7_k = (proj or {}).get('l7_rolling', {}).get('avg_k') if proj else None
    if l7_k is not None:
        signals['_projected_ks'] = round(float(l7_k), 1)
    elif pitcher_k_pct is not None:
        signals['_projected_ks'] = round(pitcher_k_pct / 100 * 22, 1)

    # Snapshot bullpen state for the audit cohort (added 2026-05-10).
    # mlb_game_context is transient, so without snapshotting at pick time
    # the K-Over × bullpen correlation cohort can't read history.
    own_pen = _i(g.get(f'{side}_bp_relievers_3d'))
    if own_pen is not None:
        signals['_starter_pen_relievers_3d'] = own_pen

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

    # Suggested Under line — project conservative Ks then add cushion to the
    # line we'd take Under. Books often hang lines 1-1.5 above projection on
    # mid-tier arms, so we suggest a line slightly above our projection that
    # still has fade value.
    raw_k = pitcher_k_pct if pitcher_k_pct is not None else 20
    typical_ip = 4.5  # short-leash assumption when we're fading
    est_ks = (raw_k / 100) * (typical_ip * 4.0)
    # Suggest the next half-line above projection (where book likely sits)
    suggested_line = max(4.0, min(7.0, round(est_ks + 1.0, 0) - 0.5))

    # Realistic K projection for app display — prefer L7 rolling avg_k from
    # the class-projection cache (most current; survives K%-parse failures),
    # fall back to season K% × 18 BF (short-leash fade assumption).
    proj = get_pitcher_projection(pitcher)
    l7_k = (proj or {}).get('l7_rolling', {}).get('avg_k') if proj else None
    if l7_k is not None:
        signals['_projected_ks'] = round(float(l7_k), 1)
    elif pitcher_k_pct is not None:
        signals['_projected_ks'] = round(pitcher_k_pct / 100 * 18, 1)

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
    proj = get_pitcher_projection(pitcher)
    l7 = (proj or {}).get('l7_rolling') if proj else None
    l7_er = (l7 or {}).get('avg_er') if l7 else None
    if l7_er is not None:
        if float(l7_er) >= 3.5:
            conviction += 12
            signals['l7_er'] = f'L7 avg {l7_er:.1f} ER/start — getting tagged'
        elif float(l7_er) <= 1.5:
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

    # Opp pitcher's career BAA vs this team — INVERSE of hits_over scorer.
    # Low BAA = pitcher has owned this lineup → BOOST hits_under.
    # High BAA = pitcher gets tagged by this lineup → FADE hits_under.
    # Added 2026-05-24 per project_mastery_split_by_prop_type.
    opp_vs_team_baa = _f(g.get(f'{opp_side}_pitcher_vs_team_avg'))
    opp_vs_team_ip2 = _f(g.get(f'{opp_side}_pitcher_vs_team_ip')) or 0
    # 2026-05-27 INCIDENT: 15-IP gate added across all mastery signals.
    if opp_vs_team_baa is not None and opp_vs_team_ip2 >= 15:
        if opp_vs_team_baa <= 0.215:
            conviction += 8
            signals['opp_vs_team_baa'] = f'Opp pitcher career vs this team: {opp_vs_team_baa:.3f} BAA on {opp_vs_team_ip2:.0f} IP — mastery, under reinforced'
        elif opp_vs_team_baa >= 0.290:
            conviction -= 8
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

    # Team offense_heat (L10 R/G vs season) — inverted for hits-UNDER. Hot
    # team = more contact = harder to land the 0-fer; fade conviction.
    # Cold team = more outs in the offense = boost conviction. Same data
    # path as the hits_OVER scorer (wired 2026-05-23).
    team_drift = _f(g.get(f'{side}_offense_drift'))
    if team_drift is not None:
        if team_drift <= -1.0:
            conviction += 6
            signals['team_cold'] = f'❄️  Team L10 {team_drift:.1f} R/G vs season — cold bats'
        elif team_drift <= -0.5:
            conviction += 3
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

    # PRIME multi-signal gate (added 2026-05-11, retuned 2026-05-23).
    #
    # 2026-05-11 audit: hits_under PRIME hit only 55.3% vs STRONG 67.4%.
    # Added gate requiring elite_opp + ONE individual factor (lineup_pos,
    # L7_cold, or hitless_streak).
    #
    # 2026-05-23 RE-audit (n=97): PRIME still at 55.7% vs STRONG 58.0%.
    # Original gate wasn't tight enough — "bottom of order" alone isn't
    # signal (gets promoted on team stacks even when the batter is hot).
    # New gate per [[project_may17_hits_under_audit]] memo: opp_k_artist
    # (k_pct ≥30) is the strongest standalone predictor. Require it as
    # the headline signal; lineup_position becomes a tie-breaker only.
    #
    # Required for PRIME (conviction ≥85):
    #   - elite opp (xERA ≤ 3.0)
    #   AND one of:
    #     - opp_k_artist (opp_pitcher_k_pct ≥ 30)  ← strongest standalone
    #     - hitless_streak ≥ 3  (real bat ice — not just team-cold proxy)
    #     - lineup_pos ≥7 AND L7 cold (≤30% games w/ hit)  ← both, not either
    if conviction >= 85:
        gate_ace = opp_quality is not None and opp_quality <= 3.0
        opp_k_artist = (
            opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 30
        )
        active_ice = l7 and l7.get('hitless_streak', 0) >= 3
        bottom_and_cold = (
            (lineup_position is not None and lineup_position >= 7)
            and (l7 and l7.get('got_hit_rate', 1.0) <= 0.30)
        )
        gate_individual = opp_k_artist or active_ice or bottom_and_cold

        # 2026-05-23 hitless_streak-only cap: scanner found hitless_streak
        # hits 56.1% in PRIME (n=41) vs 77.8% in STRONG (n=27) — a 21.7pt
        # over-promotion gap. When active_ice is the ONLY individual gate
        # passer (no opp_k_artist, no bottom_and_cold), cap at STRONG. Lets
        # the signal carry its own picks at the tier it actually works at.
        hitless_only = active_ice and not (opp_k_artist or bottom_and_cold)
        if gate_ace and gate_individual and hitless_only:
            conviction = 84
            signals['hitless_streak_cap'] = (
                'Capped at STRONG — hitless_streak alone audits 77.8% at '
                'STRONG (n=27) but 56.1% at PRIME (n=41). Better tier match.'
            )

        if not (gate_ace and gate_individual):
            conviction = 84  # cap at STRONG
            signals['prime_gate'] = (
                f'PRIME capped — gate not met (elite_opp={bool(gate_ace)}, '
                f'opp_k_artist={bool(opp_k_artist)}, '
                f'active_ice={bool(active_ice)}, '
                f'bottom_and_cold={bool(bottom_and_cold)}). '
                'hits_under PRIME 30d 55.7% — STRONG outperforms (58.0%, n=119)'
            )

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
    for p in top:
        sigs = p.get('signals') or {}
        ptype = p.get('prop_type', '')
        line = p.get('prop_line')
        player = p.get('player_name', '')
        proj_ks = sigs.get('_projected_ks')
        proj_bb = sigs.get('_projected_bb')
        proj_ha = sigs.get('_projected_hits')
        label = None
        if ptype == 'ks_over' and proj_ks is not None and line is not None:
            label = f'{player} Over {line} Ks  ·  proj {proj_ks}'
        elif ptype == 'ks_under' and proj_ks is not None and line is not None:
            label = f'{player} Under {line} Ks  ·  proj {proj_ks}'
        elif ptype == 'bb_over' and proj_bb is not None and line is not None:
            label = f'{player} Over {line} BB  ·  proj {proj_bb}'
        elif ptype == 'bb_under' and proj_bb is not None and line is not None:
            label = f'{player} Under {line} BB  ·  proj {proj_bb}'
        elif ptype == 'ha_over' and proj_ha is not None and line is not None:
            label = f'{player} Over {line} Hits Allowed  ·  proj {proj_ha}'
        elif ptype == 'ha_under' and proj_ha is not None and line is not None:
            label = f'{player} Under {line} Hits Allowed  ·  proj {proj_ha}'
        elif ptype == 'outs_over' and line is not None:
            label = f'{player} Over {line} Outs'
        elif ptype == 'outs_under' and line is not None:
            label = f'{player} Under {line} Outs'
        elif ptype == 'er_over' and line is not None:
            label = f'{player} Over {line} ER'
        elif ptype == 'er_under' and line is not None:
            label = f'{player} Under {line} ER'
        elif ptype == 'hits_over':
            label = f'{player} Over 0.5 Hits'
        elif ptype == 'hits_under':
            label = f'{player} Under 0.5 Hits (0-fer)'
        if label:
            sigs['_display_label'] = label
            p['signals'] = sigs

    wipe_todays_props()
    saved = upsert_props(top)
    print(f"\n✅ Stored {saved} top props (of {len(all_props)} passing threshold)")
    for p in top[:8]:
        print(f"  [{p['conviction']}] {p['player_name']} {p['prop_type']} {p['prop_line']} ({p['tier']}) — {p['matchup']}")
        for k, v in p['signals'].items():
            print(f"      · {v}")

if __name__ == "__main__":
    run()
