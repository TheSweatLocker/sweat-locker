"""
Build HR Watch candidates server-side and store in Supabase.

Runs once at 2pm ET (after lineups confirmed) to avoid 170 API calls per app load.
App just queries mlb_hr_watch table — 1 query, instant.

Table schema (create in Supabase):
  CREATE TABLE mlb_hr_watch (
    id BIGSERIAL PRIMARY KEY,
    game_date DATE NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT NOT NULL,
    home_team TEXT NOT NULL,
    matchup TEXT,
    score INT,
    hr INT,
    pa INT,
    hr_rate NUMERIC,
    ba NUMERIC,
    opp_pitcher TEXT,
    opp_xera NUMERIC,
    venue TEXT,
    park_factor INT,
    temp INT,
    wind_speed INT,
    wind_dir TEXT,
    wind_out BOOLEAN,
    opp_hard_hit NUMERIC,
    opp_barrel NUMERIC,
    contact_score INT,
    power_score INT,
    env_score INT,
    hr_bonus INT,
    opp_score INT,
    is_fallback BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX idx_hr_watch_date ON mlb_hr_watch(game_date DESC, score DESC);
"""
import os
import time
import unicodedata
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
}


def get_today_et():
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')


# Jerry per-batter HR allocator. Imported lazily so a jerry_model issue
# never breaks HR Watch — if the import fails, we still ship the legacy
# score; jerry_hr_contribution just stays NULL for that run.
try:
    from jerry_model import compute_batter_hr_contribution
    _JERRY_AVAILABLE = True
except Exception as _e:
    print(f'  ⚠️  jerry_model import failed: {_e} — Jerry HR contribution will be NULL')
    _JERRY_AVAILABLE = False
    def compute_batter_hr_contribution(**kwargs):
        return {'jerry_hr_contribution': None, 'jerry_allocated_pa': None, 'jerry_signals': None}


# Park HR factors — diverge meaningfully from park run factors.
# Sources: FanGraphs / Statcast park HR factor data (3-year averages).
# Higher = HRs more frequent at this venue. League average = 100.
PARK_HR_FACTOR = {
    'Coors Field':                123,  # extreme HR park (altitude)
    'Great American Ball Park':   118,
    'Yankee Stadium':             115,  # short porch (LH bias not modeled here)
    'Citizens Bank Park':         112,
    'Wrigley Field':              108,  # wind-dependent but HR-friendly when out
    'Globe Life Field':           107,
    'Camden Yards':               104,  # post-2022 LF wall changes
    'Rogers Centre':              104,
    'Truist Park':                103,
    'Chase Field':                102,
    'Comerica Park':              102,
    'American Family Field':      102,
    'Target Field':               101,
    'Citi Field':                 100,
    'Angel Stadium':              100,
    'Nationals Park':             100,
    'Fenway Park':                 99,  # high run factor but Green Monster suppresses HR
    'PNC Park':                    98,
    'Kauffman Stadium':            98,
    'Daikin Park':                 97,  # Astros (renamed from Minute Maid)
    'Minute Maid Park':            97,
    'Progressive Field':           96,
    'Busch Stadium':               96,
    'loanDepot Park':              94,  # deep gaps + retractable roof
    'Sutter Health Park':          93,  # A's temp Sacramento home
    'T-Mobile Park':               92,  # marine layer suppresses HR
    'George M. Steinbrenner Field':92,  # Rays temp Tampa home
    'Dodger Stadium':              91,
    'Oracle Park':                 88,  # cold + deep gaps
    'Petco Park':                  88,  # spacious + marine layer
}


def park_hr_factor(venue):
    """Return park HR factor (100 = league avg). Falls back to 100."""
    if not venue:
        return 100
    return PARK_HR_FACTOR.get(venue, 100)


def strip_accents(s):
    if not s:
        return s
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def fetch_savant_batter_quality(year=2026):
    """Pull season-long Statcast quality-of-contact + xHR/xSLG for all
    qualified batters in one CSV call. Returns {name_lower: {...stats}}.

    Why this matters for HR Watch: raw HR/PA over small samples is high-
    variance — Bayesian regression mitigates but doesn't replace the
    underlying *process* signal. xHR (HR over expected based on launch
    angle + exit velo) and barrel% are sticky over small samples and
    predictive of future HR rate. A batter at 12% barrel rate / .250
    xSLG-over-SLG is a HR threat regardless of whether his HR/PA caught
    up yet."""
    try:
        import io
        import pandas as pd
        url = (
            f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
            f"?type=batter&year={year}&position=&team=&min=q&csv=true"
        )
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)
        if r.status_code != 200:
            print(f"  ⚠️ Savant batter quality fetch returned {r.status_code} — skipping xHR signal")
            return {}
        df = pd.read_csv(io.StringIO(r.text))

        # Pull a second CSV for barrel% / hard_hit% (different endpoint)
        contact_url = (
            f"https://baseballsavant.mlb.com/leaderboard/statcast"
            f"?type=batter&year={year}&position=&team=&min=q&csv=true"
        )
        contact = {}
        try:
            cr = requests.get(contact_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }, timeout=30)
            if cr.status_code == 200:
                cdf = pd.read_csv(io.StringIO(cr.text))
                for _, row in cdf.iterrows():
                    nm = str(row.get("last_name, first_name", "") or "")
                    if "," in nm:
                        last, first = [p.strip() for p in nm.split(",", 1)]
                        key = f"{first} {last}".lower()
                        contact[key] = {
                            "barrel_pct": float(row.get("barrel_batted_rate") or 0) or None,
                            "hard_hit_pct": float(row.get("hard_hit_percent") or 0) or None,
                        }
        except Exception:
            pass

        out = {}
        for _, row in df.iterrows():
            nm = str(row.get("last_name, first_name", "") or "")
            if "," not in nm:
                continue
            last, first = [p.strip() for p in nm.split(",", 1)]
            key = f"{first} {last}".lower()
            stats = {}
            for col in ("est_ba", "est_slg", "est_woba"):
                v = row.get(col)
                if v is not None and str(v) != "nan":
                    try:
                        stats[col] = float(v)
                    except Exception:
                        pass
            # xHR isn't a direct column — we approximate using
            # est_slg minus actual slg (positive = batter hits harder than
            # results suggest). Same logic Savant uses for "xHR over HR".
            actual_slg = row.get("slg")
            if actual_slg is not None and "est_slg" in stats:
                try:
                    stats["xslg_diff"] = round(stats["est_slg"] - float(actual_slg), 3)
                except Exception:
                    pass
            stats.update(contact.get(key, {}))
            if stats:
                out[key] = stats
        print(f"  Built Savant batter quality lookup for {len(out)} batters")
        return out
    except Exception as e:
        print(f"  ⚠️ Savant batter quality fetch failed: {e}")
        return {}


# Module-level cache — populated once per pipeline run via run() below.
_SAVANT_BATTER_CACHE = {}


def get_batter_quality(name):
    """Look up a batter in the Savant cache. Tries lowercase exact match
    then last-name match."""
    if not name or not _SAVANT_BATTER_CACHE:
        return {}
    key = strip_accents(name).lower().strip()
    if key in _SAVANT_BATTER_CACHE:
        return _SAVANT_BATTER_CACHE[key]
    # Last-name fallback for accent / diacritic mismatches we missed
    last = key.split(" ")[-1]
    for k, v in _SAVANT_BATTER_CACHE.items():
        if k.endswith(" " + last):
            return v
    return {}


def fetch_batter_stats(name):
    """Fetch season hitting stats + bat side + last-7 game pace from MLB Stats API.
    Adds slg + iso (process signals more stable than HR/PA in small samples).
    Adds bat_side (L/R/S) for platoon scoring.
    Adds last_7_hr / last_7_pa to penalize cold streaks (season totals miss recent slumps)."""
    if not name:
        return None
    try:
        search_name = strip_accents(name)
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/people/search',
            params={'names': search_name, 'sportId': 1, 'active': True},
            timeout=10
        )
        people = r.json().get('people', [])
        if not people:
            return None
        person = people[0]
        pid = person['id']
        bat_side = person.get('batSide', {}).get('code')  # 'L' / 'R' / 'S'

        sr = requests.get(
            f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
            params={'stats': 'season', 'group': 'hitting', 'season': 2026, 'sportId': 1},
            timeout=10
        )
        splits = sr.json().get('stats', [{}])[0].get('splits', [])
        if not splits:
            return None
        # Filter to MLB-only splits (sportId=1 is the param but some responses still
        # include MiLB rows — defensively pick the MLB split if multiple exist).
        mlb_split = next(
            (sp for sp in splits if sp.get('sport', {}).get('id') == 1
             or sp.get('league', {}).get('sport', {}).get('id') == 1),
            splits[0]
        )
        s = mlb_split.get('stat', {})
        ba = float(s.get('avg', 0) or 0)
        slg = float(s.get('slg', 0) or 0)
        iso = max(0.0, slg - ba)

        # Last-N games pace — gameLog stats sorted by date desc.
        # 2026-06-27: expanded to also track L5 + L15 multi-HR games for
        # hot-streak detection (Caglianone case: 6 HR in 5 games + 3 multi-HR
        # games since 6/19 was not flagged by the L7-only signal).
        last_5_hr = 0
        last_5_pa = 0
        last_7_hr = 0
        last_7_pa = 0
        last_15_hr = 0
        last_15_multi_hr_games = 0  # games with 2+ HR in last 15
        try:
            gl = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'gameLog', 'group': 'hitting', 'season': 2026, 'sportId': 1},
                timeout=10
            )
            gsplits = gl.json().get('stats', [{}])[0].get('splits', [])
            # Filter to MLB games only — guards against MiLB rehab/option stints
            mlb_games = [g for g in gsplits
                         if g.get('sport', {}).get('id') == 1
                         or g.get('league', {}).get('sport', {}).get('id') == 1]
            # If response was already MLB-only, mlb_games may be empty due to
            # missing nested fields; fall back to all splits.
            source = mlb_games or gsplits
            recent_15 = source[-15:]
            recent_7 = source[-7:]
            recent_5 = source[-5:]
            for g in recent_15:
                gs = g.get('stat', {})
                hr_g = int(gs.get('homeRuns', 0) or 0)
                last_15_hr += hr_g
                if hr_g >= 2:
                    last_15_multi_hr_games += 1
            for g in recent_7:
                gs = g.get('stat', {})
                last_7_hr += int(gs.get('homeRuns', 0) or 0)
                last_7_pa += int(gs.get('plateAppearances', 0) or 0)
            for g in recent_5:
                gs = g.get('stat', {})
                last_5_hr += int(gs.get('homeRuns', 0) or 0)
                last_5_pa += int(gs.get('plateAppearances', 0) or 0)
        except Exception:
            pass

        # Savant quality of contact (barrel%, xSLG, xHR signal)
        sav = get_batter_quality(name)

        return {
            'name': name,
            'pa': int(s.get('plateAppearances', 0) or 0),
            'hr': int(s.get('homeRuns', 0) or 0),
            'ba': ba,
            'slg': slg,
            'iso': round(iso, 3),
            'bat_side': bat_side,
            'last_5_hr': last_5_hr,
            'last_5_pa': last_5_pa,
            'last_7_hr': last_7_hr,
            'last_7_pa': last_7_pa,
            'last_15_hr': last_15_hr,
            'last_15_multi_hr_games': last_15_multi_hr_games,
            # Savant signals — keys may be missing if not in the leaderboard
            'savant_barrel_pct': sav.get('barrel_pct'),
            'savant_hard_hit_pct': sav.get('hard_hit_pct'),
            'savant_xslg_diff': sav.get('xslg_diff'),  # xSLG - SLG, positive = batter hits harder than results
            'savant_est_slg': sav.get('est_slg'),
        }
    except Exception as e:
        print(f'  Error fetching {name}: {e}')
        return None


def get_pitcher_contact(pitcher_name):
    """Fetch pitcher contact profile from mlb_pitcher_stats.

    Now also returns fb_pct (HR risk proxy — flyball pitchers give up
    more HRs than groundball pitchers for the same xERA) and throws
    (handedness for platoon scoring)."""
    if not pitcher_name:
        return None
    try:
        last_name = pitcher_name.split()[-1]
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_pitcher_stats'
            f'?player_name=ilike.*{requests.utils.quote(last_name)}*'
            f'&select=player_name,hard_hit_pct_allowed,barrel_pct,fb_pct,gb_pct,throws'
            f'&limit=1',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        data = r.json()
        if data and isinstance(data, list) and data:
            return data[0]
    except:
        pass
    return None


def get_team_fallback(team_name):
    """Fallback to cached team HR threats when lineup missing"""
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_team_hr_threats'
            f'?team=eq.{requests.utils.quote(team_name)}&select=top_hitters',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        data = r.json()
        if data and isinstance(data, list) and data:
            return [h['name'] for h in data[0].get('top_hitters', [])[:5]]
    except:
        pass
    return []


def score_batter(stats, opp_xera, opp_contact, park_factor, hr_park, temp, wind_speed, wind_dir):
    """HR Watch scoring with stronger small-sample regression + recency, platoon, FB-rate signals.

    BAYESIAN REGRESSION on HR rate (added 2026-05-02, prior strengthened
    2026-05-04): raw HR/PA over small samples wildly inflates power_score
    for hot small-sample hitters (e.g. Rushing 7 HR / 52 PA = 13.5%).
    PRIOR_PA bumped 200 → 400 because Rushing kept ranking #1 even after
    his sample grew — at 200 prior, mid-sample hot hitters still beat
    full-season legitimate threats. 400 forces meaningful evidence.

    NEW SIGNALS (2026-05-04):
      - FB-rate score: flyball pitchers (fb_pct ≥ .40) give up more HRs
        than groundballers at the same xERA. Direct HR-risk signal.
      - L7 cold-streak penalty: 0 HR over last 25+ PA = -10. Catches
        slumps that season totals miss.
      - Platoon score: opp-handed matchup gets +5, same-handed -3.
        Switch-hitters always opp-handed (treated as +5).
    """
    # Bumped PA threshold from 15 to 40 — anything under is small-sample noise.
    if not stats or stats['pa'] < 40:
        return None

    raw_hr_rate = stats['hr'] / stats['pa'] if stats['pa'] > 0 else 0
    if raw_hr_rate < 0.02:
        return None

    # Bayesian-regress BOTH HR rate AND ISO. Stronger PRIOR_PA (400) so
    # mid-sample hot hitters can't dominate full-season legit threats.
    PRIOR_HR_RATE = 0.03
    PRIOR_ISO     = 0.155
    PRIOR_PA      = 400
    hr_rate = (stats['hr'] + PRIOR_HR_RATE * PRIOR_PA) / (stats['pa'] + PRIOR_PA)
    raw_iso = stats.get('iso', PRIOR_ISO)
    iso = (raw_iso * stats['pa'] + PRIOR_ISO * PRIOR_PA) / (stats['pa'] + PRIOR_PA)

    # Power score blends regressed HR rate + regressed ISO.
    power_from_rate = hr_rate * 250
    power_from_iso  = iso * 150
    power_score = round(power_from_rate + power_from_iso)
    hr_bonus = 10 if stats['hr'] >= 4 else 0
    opp_score = 15 if opp_xera and opp_xera > 4.5 else (5 if opp_xera and opp_xera > 3.5 else 0)

    contact_score = 0
    fb_score = 0
    pitcher_throws = None
    if opp_contact:
        hard_hit = float(opp_contact.get('hard_hit_pct_allowed') or 0)
        barrel = float(opp_contact.get('barrel_pct') or 0)
        if hard_hit >= 42: contact_score += 12
        elif hard_hit >= 38: contact_score += 6
        elif 0 < hard_hit <= 30: contact_score -= 5
        if barrel >= 10: contact_score += 10
        elif barrel >= 7: contact_score += 5

        # FB-rate HR risk — flyball pitchers allow far more HRs.
        # mlb_pitcher_stats.fb_pct is sometimes stored as fraction (0.35) and
        # sometimes as percent (35.0) depending on data source path. Normalize.
        fb_pct = opp_contact.get('fb_pct')
        if fb_pct is not None:
            try:
                fb = float(fb_pct)
                if fb > 1.0: fb = fb / 100.0  # normalize percent → fraction
                if fb >= 0.40: fb_score += 8     # extreme flyball pitcher
                elif fb >= 0.35: fb_score += 4
                elif 0 < fb <= 0.30: fb_score -= 5  # heavy GB pitcher suppresses HR
            except:
                pass

        pitcher_throws = (opp_contact.get('throws') or '').upper() or None

    env_score = 0
    # Park HR factor — replaces park_run_factor for HR-specific scoring.
    # Coors 123 / Petco 88 spans much wider than run factor.
    if hr_park >= 115: env_score += 18      # extreme HR park
    elif hr_park >= 108: env_score += 12    # plus HR park
    elif hr_park >= 103: env_score += 6
    elif hr_park <= 92:  env_score -= 8     # pitcher park penalty
    elif hr_park <= 95:  env_score -= 4
    if temp >= 80: env_score += 10
    elif temp >= 70: env_score += 5
    wind_out = wind_speed > 10 and any(d in (wind_dir or '').upper() for d in ['S', 'SW', 'SE', 'OUT'])
    if wind_out: env_score += 12

    # Platoon score — switch hitters always count as opp-handed.
    bat_side = (stats.get('bat_side') or '').upper()
    platoon_score = 0
    if bat_side and pitcher_throws:
        if bat_side == 'S':
            platoon_score = 5
        elif bat_side != pitcher_throws:
            platoon_score = 5
        else:
            platoon_score = -3

    # Recency penalty + hot-streak boost. Need a meaningful recent sample
    # (≥25 PA) to call cold; hot-streak boost uses L5 HR count gated by L5 PA.
    #
    # 2026-06-27 — added tiered hot-streak boost. Prior signal was only +5
    # for "heating up" (≥2 HR in 20+ L7 PA), which under-rated cases like
    # Caglianone (6 HR in 5 games, 3 multi-HR games since 6/19) who never
    # surfaced on HR Watch despite being one of the hottest power hitters in
    # baseball. New tiered logic:
    #   L5 HR ≥ 5 → +22  (Caglianone band)
    #   L5 HR = 4 → +18
    #   L5 HR = 3 → +12
    #   L15 multi-HR games ≥ 3 → +14 (independent compounding streak signal)
    #   L15 multi-HR games = 2 → +8
    # Net effect: a 6-in-5 hitter now gets +22 instead of +5 (+17pp swing).
    last_5_hr = int(stats.get('last_5_hr') or 0)
    last_5_pa = int(stats.get('last_5_pa') or 0)
    last_7_pa = int(stats.get('last_7_pa') or 0)
    last_7_hr = int(stats.get('last_7_hr') or 0)
    last_15_multi_hr = int(stats.get('last_15_multi_hr_games') or 0)
    recency_score = 0

    # Cold streak — unchanged
    if last_7_pa >= 25 and last_7_hr == 0:
        recency_score = -10

    # Hot streak — L5 HR tier (requires meaningful PA so we don't reward
    # 5-HR-in-5-PA edge cases from a pinch-hit run)
    if last_5_pa >= 15:
        if last_5_hr >= 5:
            recency_score = max(recency_score, 22)
        elif last_5_hr >= 4:
            recency_score = max(recency_score, 18)
        elif last_5_hr >= 3:
            recency_score = max(recency_score, 12)
    # Legacy "heating up" — preserves prior +5 for ≥2 HR in L7 (20+ PA) when
    # the L5 tiers above don't trigger (e.g., 2 HR L7 but only 1 in L5)
    if recency_score < 5 and last_7_hr >= 2 and last_7_pa >= 20:
        recency_score = max(recency_score, 5)

    # Multi-HR games streak — compounds additively
    if last_15_multi_hr >= 3:
        recency_score += 14
    elif last_15_multi_hr == 2:
        recency_score += 8

    # Savant quality-of-contact (added 2026-05-05) — barrel% and xSLG-SLG
    # diff are sticky predictors of HR rate that survive small samples.
    # A 12% barrel rate is HR-threat tier regardless of whether the season
    # HR/PA caught up yet.
    savant_score = 0
    barrel = stats.get('savant_barrel_pct')
    if barrel is not None:
        try:
            b = float(barrel)
            if b >= 14:    savant_score += 14   # elite — top 5% of MLB
            elif b >= 11:  savant_score += 9    # plus barrel rate
            elif b >= 8:   savant_score += 4
            elif b <= 4:   savant_score -= 6    # contact hitter, low HR upside
        except Exception:
            pass
    # xSLG-SLG diff — when batter is hitting harder than results show,
    # they're due for HR regression UPWARD (positive diff). Negative diff
    # means season HR rate may overstate true power.
    xslg_diff = stats.get('savant_xslg_diff')
    if xslg_diff is not None:
        try:
            xd = float(xslg_diff)
            if xd >= 0.040:    savant_score += 6   # under-performing power, due
            elif xd >= 0.020:  savant_score += 3
            elif xd <= -0.040: savant_score -= 6   # over-performing, regression risk
        except Exception:
            pass

    # Batter's own hard-hit % — added 2026-05-15. Was being pulled in
    # build_savant_lookup() but never scored. Hard-hit (95+mph) % is a
    # sticky power indicator independent of barrel% — elite barrel guys
    # who also crush hard contact rate are the highest-HR-upside profile.
    hh_pct = stats.get('savant_hard_hit_pct')
    if hh_pct is not None:
        try:
            hh = float(hh_pct)
            if hh >= 50:   savant_score += 6   # elite hard contact (top ~5%)
            elif hh >= 45: savant_score += 4
            elif hh >= 40: savant_score += 2
            elif hh <= 30: savant_score -= 3   # weak contact, low HR upside
        except Exception:
            pass

    # "Due for HR" interaction signal — added 2026-05-15. Catches the spot
    # where a batter LOOKS cold on the surface (0 HR in 25+ recent PA) but
    # Statcast says he's been hitting balls hard with bad luck (positive
    # xSLG_diff + plus barrel%). This is the most fan-shareable HR Watch
    # angle ("0-for-12 but his xSLG is .520 — turning into homers fast").
    # Boost is intentionally additive to the existing cold-streak penalty
    # so a "due" hitter recovers the -10 and ends up with net positive lean.
    try:
        is_cold_surface = last_7_pa >= 25 and last_7_hr == 0
        statcast_due = (
            xslg_diff is not None and float(xslg_diff) >= 0.040
            and barrel is not None and float(barrel) >= 9
        )
        if is_cold_surface and statcast_due:
            savant_score += 12   # net flips cold-penalty from -10 to +2 boost
            # Mark for display/narrative — gets surfaced in the row metadata
            stats['_due_signal'] = True
    except Exception:
        pass

    total_score = (power_score + hr_bonus + opp_score + contact_score
                   + fb_score + env_score + platoon_score + recency_score
                   + savant_score)

    return {
        'hr_rate': round(raw_hr_rate, 4),  # display raw, scoring uses regressed
        'hr_rate_regressed': round(hr_rate, 4),
        'iso': round(raw_iso, 3),
        'iso_regressed': round(iso, 3),
        'score': total_score,
        'power_score': power_score,
        'hr_bonus': hr_bonus,
        'opp_score': opp_score,
        'contact_score': contact_score,
        'fb_score': fb_score,
        'env_score': env_score,
        'platoon_score': platoon_score,
        'recency_score': recency_score,
        'savant_score': savant_score,
        'wind_out': wind_out,
    }


def attach_book_odds(candidates):
    """Phase 2-style attach for batter_home_runs market — same pattern as
    pitcher_strikeouts / pitcher_walks in generate_props.py.

    Fetches events for today, then queries the batter_home_runs market for
    each event. Builds a {batter_name_lower: (line, over_odds_int, source)}
    map and stamps each candidate with book_odds + book_source. Bestknown
    line for HR is typically over 0.5, so we only need the over price (e.g.
    +450). Falls open (no attach) when ODDS_API_KEY missing or events
    endpoint fails — candidates keep projected_hr_prob but no book column.
    """
    api_key = os.environ.get('ODDS_API_KEY')
    if not api_key:
        print('  ⚠️  ODDS_API_KEY missing — HR Watch book odds attach skipped')
        return
    try:
        now_utc = datetime.now(timezone.utc)
        events_r = requests.get(
            'https://api.the-odds-api.com/v4/sports/baseball_mlb/events',
            params={'apiKey': api_key,
                    'commenceTimeFrom': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')},
            timeout=15,
        )
        if events_r.status_code != 200:
            print(f'  ⚠️  HR market events fetch returned {events_r.status_code}')
            return
        events = events_r.json() or []
    except Exception as e:
        print(f'  ⚠️  HR market events fetch failed: {e}')
        return

    book_map = {}  # batter_lower → (over_odds_int, source)
    for ev in events:
        ev_id = ev.get('id')
        if not ev_id:
            continue
        try:
            odds_r = requests.get(
                f'https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{ev_id}/odds',
                params={'apiKey': api_key, 'regions': 'us,us2',
                        'markets': 'batter_home_runs', 'oddsFormat': 'american'},
                timeout=15,
            )
            if odds_r.status_code != 200:
                continue
            ev_data = odds_r.json() or {}
        except Exception:
            continue

        # Aggregate prices across books. Selection priority (2026-07-30 fix):
        #   1. Hard Rock Bet (user's book) — matches what user sees in-app
        #   2. Best price for user (highest American odds — most $ back per bet)
        #   3. Median across all books (safe fallback if HRB unavailable AND
        #      best-price outlier looks stale/suspicious)
        # Prior behavior picked the MEDIAN, which meant book_source rotated
        # through whatever book landed in the middle (usually williamhill_us).
        # Users compared "app says +3000 (3.2%)" vs "HRB says +2500 (4.0%)"
        # and thought our data was broken. Fixed by prefering HRB when
        # available, else best-price.
        HRB_KEYS = {'hardrockbet', 'hard_rock_bet', 'hardrock', 'hard_rock',
                    'hardrockbet_us', 'hardrockbet_oh'}  # base + Ohio variant; add state variants as they appear
        by_batter = {}  # name_lower → list of (over_odds_int, src)
        for bk in ev_data.get('bookmakers', []) or []:
            src = bk.get('key', '?')
            for mkt in bk.get('markets', []) or []:
                if mkt.get('key') != 'batter_home_runs':
                    continue
                for outcome in mkt.get('outcomes', []) or []:
                    name = (outcome.get('description') or outcome.get('name') or '').strip().lower()
                    price = outcome.get('price')
                    side = (outcome.get('name') or '').lower()
                    if 'over' not in side or price is None:
                        continue
                    by_batter.setdefault(name, []).append((int(price), src))
        for name, prices in by_batter.items():
            if not prices:
                continue
            # 1. Prefer HRB
            hrb = next((p for p in prices if p[1].lower() in HRB_KEYS), None)
            if hrb:
                book_map[name] = hrb
                continue
            # 2. Best price for user = max American odds (higher = better payout)
            best = max(prices, key=lambda x: x[0])
            book_map[name] = best

    matched = 0
    rejected_sanity = 0
    for c in candidates:
        name = (c.get('player_name') or '').strip().lower()
        hit = book_map.get(name)
        if not hit:
            continue
        odds, src = hit[0], hit[1]
        # Sanity gate (2026-07-30): drop prices that diverge insanely from model.
        # CJ Abrams case: model 17.9%, book +18000 implied 0.55% → ratio 32x.
        # Any book row where model_prob / implied_prob > 5 is data noise, not
        # a real price. Falls open (null book_odds) rather than showing garbage.
        p_model = c.get('projected_hr_prob') or 0
        implied = 100.0 / (odds + 100) if odds >= 0 else -odds / (-odds + 100.0)
        if p_model > 0 and implied > 0 and (p_model / implied) > 5.0:
            rejected_sanity += 1
            continue
        c['book_odds'] = odds
        c['book_source'] = src
        matched += 1
    print(f'  📖 HR book odds attached: {matched}/{len(candidates)} candidates'
          f' (rejected {rejected_sanity} on sanity gate)')


def run():
    today = get_today_et()
    print(f'Building HR Watch for {today}')

    # Bootstrap Savant batter quality cache once per run (single CSV pull
    # instead of per-batter API hits). Used by score_batter for xHR + barrel
    # signals.
    global _SAVANT_BATTER_CACHE
    _SAVANT_BATTER_CACHE = fetch_savant_batter_quality(year=2026)

    # Clear today's previous entries
    requests.delete(
        f'{SUPABASE_URL}/rest/v1/mlb_hr_watch?game_date=eq.{today}',
        headers=HEADERS
    )

    # Get today's game contexts
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{today}&select=*',
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    )
    games = r.json()
    print(f'Games found: {len(games)}')

    candidates = []
    batters_seen = set()  # dedupe if same player appears in multiple games somehow

    for ctx in games:
        home_team = ctx.get('home_team')
        away_team = ctx.get('away_team')
        venue = ctx.get('venue')
        park_factor = float(ctx.get('park_run_factor') or 100)
        hr_park = park_hr_factor(venue)
        temp = float(ctx.get('temperature') or 70)
        wind_speed = float(ctx.get('wind_speed') or 0)
        wind_dir = ctx.get('wind_direction') or ''

        home_xera = float(ctx.get('home_sp_xera') or 4.25) if ctx.get('home_sp_xera') else None
        away_xera = float(ctx.get('away_sp_xera') or 4.25) if ctx.get('away_sp_xera') else None

        # Cache pitcher contact profiles once per game
        home_contact = get_pitcher_contact(ctx.get('home_pitcher'))
        away_contact = get_pitcher_contact(ctx.get('away_pitcher'))

        for side_name, team, opp_xera, opp_contact, opp_pitcher, lineup_str in [
            ('home', home_team, away_xera, away_contact, ctx.get('away_pitcher'), ctx.get('home_lineup')),
            ('away', away_team, home_xera, home_contact, ctx.get('home_pitcher'), ctx.get('away_lineup')),
        ]:
            batters = []
            is_fallback = False

            if lineup_str:
                batters = [b.strip() for b in lineup_str.split(',') if b.strip()][:5]
            else:
                batters = get_team_fallback(team)[:5]
                is_fallback = True

            if not batters:
                continue

            for spot_idx, batter_name in enumerate(batters):
                if len(batter_name) < 3:
                    continue
                key = f'{team}:{batter_name}'
                if key in batters_seen:
                    continue
                batters_seen.add(key)

                stats = fetch_batter_stats(batter_name)
                if not stats:
                    continue

                scoring = score_batter(stats, opp_xera, opp_contact, park_factor, hr_park, temp, wind_speed, wind_dir)
                if not scoring or scoring['score'] < 20:
                    continue

                # Jerry per-batter HR contribution (added 2026-06-01). Shadow
                # mode — runs alongside the legacy score, stored in
                # jerry_hr_contribution so the audit can compare hit rates
                # and we can blend in if it shows lift. Uses the same
                # Bayesian regression prior + situational machinery as
                # score_batter, but multiplicative instead of additive so
                # each factor's contribution is auditable.
                lineup_spot = spot_idx + 1  # 1-indexed
                pitcher_throws = (opp_contact.get('throws') if opp_contact else None)
                pitcher_fb_pct = (opp_contact.get('fb_pct') if opp_contact else None)
                jerry_result = compute_batter_hr_contribution(
                    season_hr=stats['hr'],
                    season_pa=stats['pa'],
                    bat_side=stats.get('bat_side'),
                    lineup_spot=lineup_spot,
                    park_hr_factor=hr_park,
                    temperature=temp,
                    wind_speed=wind_speed,
                    wind_direction=wind_dir,
                    opp_pitcher_xera=opp_xera,
                    opp_pitcher_fb_pct=pitcher_fb_pct,
                    opp_pitcher_throws=pitcher_throws,
                    barrel_pct=stats.get('savant_barrel_pct'),
                    hard_hit_pct=stats.get('savant_hard_hit_pct'),
                )

                # Projected HR probability (added 2026-06-01).
                # P(>=1 HR in game) = 1 - (1 - p_pa)^expected_pa
                # expected_pa ~ 4.2 for a starter (top-half of order avg).
                # Use regressed hr rate so small-sample hot hitters don't
                # inflate the probability; matches what the score uses.
                regressed_rate = scoring.get('hr_rate_regressed') or 0
                expected_pa = 4.2
                projected_hr_prob = round(1 - (1 - regressed_rate) ** expected_pa, 4) if regressed_rate > 0 else 0

                candidates.append({
                    'game_date': today,
                    'player_name': stats['name'],
                    'team': team,
                    'home_team': home_team,
                    'matchup': f'{away_team} @ {home_team}',
                    'score': scoring['score'],
                    'hr': stats['hr'],
                    'pa': stats['pa'],
                    'hr_rate': scoring['hr_rate'],
                    'ba': stats['ba'],
                    'opp_pitcher': opp_pitcher,
                    'opp_xera': opp_xera,
                    'venue': venue,
                    'park_factor': int(park_factor),
                    'temp': int(temp),
                    'wind_speed': int(wind_speed),
                    'wind_dir': wind_dir,
                    'wind_out': scoring['wind_out'],
                    'opp_hard_hit': float(opp_contact.get('hard_hit_pct_allowed')) if opp_contact and opp_contact.get('hard_hit_pct_allowed') else None,
                    'opp_barrel': float(opp_contact.get('barrel_pct')) if opp_contact and opp_contact.get('barrel_pct') else None,
                    # Core scoring components (already persisted)
                    'contact_score': scoring['contact_score'],
                    'power_score': scoring['power_score'],
                    'env_score': scoring['env_score'],
                    'hr_bonus': scoring['hr_bonus'],
                    'opp_score': scoring['opp_score'],
                    # Full signal stack — added 2026-06-01 so the app can render
                    # a transparent breakdown ("Schwarber 62 = power 28 + barrel
                    # 14 + park 18 + wind 12"). Previously computed but dropped
                    # on upload, which made the score opaque.
                    'fb_score': scoring.get('fb_score', 0),
                    'platoon_score': scoring.get('platoon_score', 0),
                    'recency_score': scoring.get('recency_score', 0),
                    'savant_score': scoring.get('savant_score', 0),
                    # Projection (added 2026-06-01)
                    'projected_hr_prob': projected_hr_prob,
                    # "Due for HR" interaction flag — surfaced when batter looks
                    # cold but Statcast says he's been squaring it up. Set by
                    # score_batter via the stats dict side-channel.
                    'due_signal': bool(stats.get('_due_signal')),
                    # Jerry per-batter HR contribution (Phase 1 shadow mode,
                    # added 2026-06-01). See compute_batter_hr_contribution
                    # in jerry_model.py for the formula.
                    'jerry_hr_contribution': jerry_result.get('jerry_hr_contribution'),
                    'jerry_allocated_pa': jerry_result.get('jerry_allocated_pa'),
                    'jerry_signals': jerry_result.get('jerry_signals'),
                    'is_fallback': is_fallback,
                })

                time.sleep(0.1)  # be polite to MLB API

    # 2026-06-20 ranking rescore. Audit (_audit_hr_components over n=480 / 60d)
    # found that current `score` was non-predictive in the 70-89 band
    # (-2.8 to -4.7pt lift over baseline) — the band most surfaced rows live in.
    # The two signals that DO carry lift:
    #   jerry_hr_contribution 0.3+ : 34.3% hit rate (+15.7pt lift, n=35)
    #   projected_hr_prob 15%+     : 22.7% hit rate (+4.1pt lift, n=119)
    # Composite ranking weights both well above raw score so guys with
    # loud Jerry allocation + plus projection rise to the top regardless of
    # how the legacy `score` shakes out. Score is still rendered in the app
    # for transparency; this only changes which 15 candidates we surface.
    def _rank(c):
        jerry = float(c.get('jerry_hr_contribution') or 0)
        proj = float(c.get('projected_hr_prob') or 0)
        s = float(c.get('score') or 0)
        return -(jerry * 300 + proj * 150 + s * 0.3)
    candidates.sort(key=_rank)
    top_n = candidates[:15]  # store more than displayed so app can filter/sort

    # Phase 2-style book odds attach (added 2026-06-01). Same pattern as
    # generate_props.py for ks/bb/ha — fetch from Odds API batter_home_runs
    # market and stamp each candidate with book_odds + book_source. App
    # uses this for the "model 23% vs implied 13% → +EV" value chip.
    attach_book_odds(top_n)

    print(f'\nTop candidates: {len(top_n)}')
    for c in top_n[:5]:
        print(f'  {c["player_name"]} ({c["team"]}) — {c["score"]} | {c["hr"]} HR/{c["pa"]} PA')

    # Batch upload
    if top_n:
        # Normalize keys across the batch — PostgREST rejects mixed schemas
        # with "All object keys must match" when a column is on some rows
        # but not others. Same fix pattern as generate_props.py upsert_props
        # (5/29 bug). 6/3 trigger: attach_book_odds attached on 14/15 rows
        # tonight (1 batter didn't have a batter_home_runs market), so 14
        # rows had `book_odds`+`book_source` and 1 didn't — whole batch
        # failed, 0 candidates landed. Union keys, backfill missing as None.
        all_keys = set()
        for c in top_n:
            all_keys.update(c.keys())
        for c in top_n:
            for k in all_keys:
                if k not in c:
                    c[k] = None

        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/mlb_hr_watch',
            headers={**HEADERS, 'Prefer': 'return=minimal'},
            json=top_n
        )
        if r.status_code in (200, 201, 204):
            print(f'\n✅ Stored {len(top_n)} candidates')
        else:
            print(f'\n❌ Upload failed {r.status_code}: {r.text[:200]}')


if __name__ == '__main__':
    run()
