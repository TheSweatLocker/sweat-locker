"""NFL prop-projection generator — Phase 3 Week 1 launch target.

For every player prop offered on today's + upcoming NFL slate:
  1. Pull the market line from Odds API (americanfootball_nfl props markets)
  2. Look up player's L4 + season averages from nfl_player_stats
  3. Apply opponent defensive adjustment from nfl_team_stats
  4. Compare projected vs line → OVER/UNDER pick with tier gate
  5. Upsert to nfl_props with structured signals blob for Jerry

Prop markets covered (v1, matches PROP_MARKETS.NFL in app):
  - player_pass_yds      → passing_yards
  - player_rush_yds      → rushing_yards
  - player_reception_yds → receiving_yards
  - player_receptions    → receptions
  - player_anytime_td    → any TD (v1.1 — needs snap counts / red-zone target share)

v1 blend formula:
  projected = 0.60 * l4_avg + 0.35 * season_avg + 0.05 * league_baseline
    then multiplied by opp_adj:
      opp_adj = 1 + (opp_rank_pct - 0.5) * 0.15
    (i.e. worst-defense opponent → +7.5%, best-defense opponent → -7.5%)

Tier thresholds (calibrate post-Week-4 with live audit):
  edge >= line * 0.15 → PRIME       (or exactly 15pct)
  edge >= line * 0.10 → STRONG
  edge >= line * 0.06 → LIGHT
  else → skip

Sign conventions:
  edge = projected - line              (OVER lens)
  For UNDER: use pick_side=UNDER when edge < 0 with same absolute threshold.

Anytime-TD scaffold present but returns 0 picks in v1 (needs red-zone
target share which is not yet computed server-side; ship v1.1).

Usage:
    python nfl_generate_props.py               # today + upcoming week
    python nfl_generate_props.py --dry-run
    python nfl_generate_props.py --player 'Patrick Mahomes'   # test one player
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ODDS_API_BASE = 'https://api.the-odds-api.com/v4/sports'


# ─────────────────────────────────────────────────────────────
# Prop registry — maps Odds API market key → player_stats column
# ─────────────────────────────────────────────────────────────
PROP_CONFIG = {
    'player_pass_yds': {
        'col': 'passing_yards',
        'position': 'QB',
        'league_baseline': 235.0,     # league avg per game
        'opp_col': 'def_pass_def',    # higher pass_def = tougher for QB
        'label': 'Pass Yds',
        'fantasy_col': 'proj_pass_yds',  # 2026-08-09: Sleeper/ESPN projection field
    },
    'player_rush_yds': {
        'col': 'rushing_yards',
        'position': 'RB',
        'league_baseline': 55.0,
        'opp_col': 'def_sacks',       # loose proxy — sackier defenses stop the run too
        'label': 'Rush Yds',
        'fantasy_col': 'proj_rush_yds',
    },
    'player_reception_yds': {
        'col': 'receiving_yards',
        'position': None,             # WR/TE/RB — filter looser
        'league_baseline': 42.0,
        'opp_col': 'def_pass_def',
        'label': 'Rec Yds',
        'fantasy_col': 'proj_rec_yds',
    },
    'player_receptions': {
        'col': 'receptions',
        'position': None,
        'league_baseline': 3.2,
        'opp_col': 'def_pass_def',
        'label': 'Receptions',
        'fantasy_col': 'proj_receptions',
    },
    # 2026-08-23 TD family markets. Historical data columns may not fully
    # exist yet — signal-emit path handles missing L4 gracefully via
    # league_baseline. When TD-specific historical cols land, per-family
    # nuance can be added.
    'player_pass_tds': {
        'col': 'passing_tds',
        'position': 'QB',
        'league_baseline': 1.4,     # league avg pass TDs per game
        'opp_col': 'def_pass_def',
        'label': 'Pass TDs',
        'fantasy_col': 'proj_pass_tds',
    },
    'player_anytime_td': {
        'col': 'anytime_td',
        'position': None,
        'league_baseline': 0.35,    # league avg (RBs + WRs mixed)
        'opp_col': 'def_pass_def',  # loose proxy — no red-zone-specific data yet
        'label': 'Anytime TD',
        'fantasy_col': 'proj_anytime_td',
    },
    'player_rush_tds': {
        'col': 'rushing_tds',
        'position': 'RB',
        'league_baseline': 0.3,
        'opp_col': 'def_sacks',
        'label': 'Rush TDs',
        'fantasy_col': 'proj_rush_tds',
    },
    'player_pass_attempts': {
        'col': 'pass_attempts',
        'position': 'QB',
        'league_baseline': 32.0,
        'opp_col': 'def_pass_def',
        'label': 'Pass Att',
        'fantasy_col': 'proj_pass_attempts',
    },
    'player_pass_completions': {
        'col': 'pass_completions',
        'position': 'QB',
        'league_baseline': 21.0,
        'opp_col': 'def_pass_def',
        'label': 'Completions',
        'fantasy_col': 'proj_pass_completions',
    },
    'player_pass_interceptions': {
        'col': 'interceptions',
        'position': 'QB',
        'league_baseline': 0.75,
        'opp_col': 'def_pass_def',   # high pass_def = more INTs
        'label': 'Interceptions',
        'fantasy_col': 'proj_pass_ints',
    },
    'player_rush_attempts': {
        'col': 'rush_attempts',
        'position': 'RB',
        'league_baseline': 13.0,
        'opp_col': 'def_sacks',
        'label': 'Rush Att',
        'fantasy_col': 'proj_rush_attempts',
    },
}


# 2026-08-09 Phase 3 Panel-informed props: cache per-week projections
_FANTASY_PROJ_CACHE = {}


def load_fantasy_projections(season: int, week: int) -> dict:
    """Return {(name_lower, position): {stat_field: avg_value}} — ensemble
    averaged across Sleeper + ESPN if both present. Cached per (season, week)."""
    key = (season, week)
    if key in _FANTASY_PROJ_CACHE:
        return _FANTASY_PROJ_CACHE[key]
    r = requests.get(f'{SB}/rest/v1/nfl_player_projections', headers=H_READ,
        params={'season': f'eq.{season}', 'week': f'eq.{week}',
                'select': '*'}, timeout=15)
    rows = r.json() if isinstance(r.json(), list) else []
    if not rows:
        _FANTASY_PROJ_CACHE[key] = {}
        return {}
    # Group by player name → merge across sources
    import re, unicodedata as ud
    def _key(name):
        n = ud.normalize('NFKD', name or '').encode('ascii','ignore').decode('ascii')
        n = re.sub(r'\b(jr|iii|ii|iv|sr)\b\.?', '', n.lower())
        n = re.sub(r'[^a-z0-9\s]', '', n).strip()
        return re.sub(r'\s+', ' ', n)
    by_player = {}
    stat_fields = ['proj_pass_yds','proj_rush_yds','proj_rec_yds','proj_receptions',
                    'proj_pass_tds','proj_rush_tds','proj_rec_tds']
    for row in rows:
        k = (_key(row.get('player_name')), row.get('position'))
        if not k[0]: continue
        slot = by_player.setdefault(k, {'_n': 0, **{f: [] for f in stat_fields},
                                          'team': row.get('team')})
        slot['_n'] += 1
        for f in stat_fields:
            v = row.get(f)
            if v is not None:
                try: slot[f].append(float(v))
                except: pass
    # Reduce lists → averages
    out = {}
    for k, slot in by_player.items():
        merged = {'team': slot['team'], '_n': slot['_n']}
        for f in stat_fields:
            vals = slot[f]
            if vals:
                merged[f] = round(sum(vals) / len(vals), 2)
        out[k] = merged
    _FANTASY_PROJ_CACHE[key] = out
    return out


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_alias_map() -> dict:
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_aliases?select=canonical_name,odds_api_name,full_name',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    aliases = {}
    for row in r.json():
        canonical = row.get('canonical_name')
        for field in ('odds_api_name', 'full_name'):
            n = row.get(field)
            if n and canonical: aliases[n] = canonical
    return aliases


def load_opponent_defense(season: int) -> dict:
    """Map team → defensive stat dict for opponent-adjustment."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_stats?season=eq.{season}&season_type=eq.REG&select=team,def_sacks,def_ints,def_pass_def',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    return {row['team']: row for row in r.json()}


def opp_rank(opp_map: dict, opp_team: str, opp_col: str) -> Optional[float]:
    """Return 0.0 (best defense) → 1.0 (worst) rank as fraction. None if data missing."""
    if opp_team not in opp_map: return None
    values = sorted(
        [(t, (row.get(opp_col) or 0)) for t, row in opp_map.items()],
        key=lambda x: -x[1],   # higher = better defense for this stat
    )
    for i, (t, _) in enumerate(values):
        if t == opp_team:
            return i / max(1, len(values) - 1)   # 0.0 = best, 1.0 = worst
    return None


def player_rolling(player_id: str, prop_col: str, current_season: int) -> tuple:
    """Return (l4_avg, season_avg, games_played) for a player-prop.
    Reads from nfl_player_stats — pulls most recent 20 rows, computes L4 + season."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_player_stats'
        f'?player_id=eq.{player_id}'
        f'&order=season.desc,week.desc'
        f'&limit=20'
        f'&select=season,week,{prop_col}',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        return None, None, 0
    rows = r.json() or []
    if not rows:
        return None, None, 0
    vals = [_f(r.get(prop_col)) for r in rows if r.get(prop_col) is not None]
    if not vals:
        return None, None, 0

    l4 = vals[:4]
    l4_avg = round(sum(l4) / len(l4), 2) if l4 else None
    # Season avg = current season only
    season_vals = [_f(r.get(prop_col)) for r in rows
                   if r.get('season') == current_season and r.get(prop_col) is not None]
    season_avg = round(sum(season_vals) / len(season_vals), 2) if season_vals else None
    return l4_avg, season_avg, len(vals)


def project(l4_avg: Optional[float], season_avg: Optional[float],
            league_baseline: float, opp_rank_pct: Optional[float],
            fantasy_proj_stat: Optional[float] = None) -> Optional[float]:
    """Blend model — 2026-08-09 Phase 3 upgrade adds fantasy_proj_stat lens.

    OLD (still fallback): 0.60 L4 + 0.35 season + 0.05 baseline
    NEW (when fantasy_proj_stat present): 0.50 fantasy_ensemble + 0.30 L4 + 0.15 season + 0.05 baseline

    The `fantasy_proj_stat` param is the ensemble-averaged projection for
    this stat from `nfl_player_projections` (Sleeper + ESPN). It's the
    freshest signal (injury-aware, week-specific) so gets the largest
    weight when available.
    """
    if l4_avg is None and season_avg is None and fantasy_proj_stat is None:
        return None
    l4 = l4_avg if l4_avg is not None else (season_avg or league_baseline)
    season = season_avg if season_avg is not None else (l4_avg or league_baseline)
    if fantasy_proj_stat is not None:
        base = (0.50 * fantasy_proj_stat
                + 0.30 * l4
                + 0.15 * season
                + 0.05 * league_baseline)
    else:
        base = 0.60 * l4 + 0.35 * season + 0.05 * league_baseline
    if opp_rank_pct is not None:
        opp_adj = 1.0 + (opp_rank_pct - 0.5) * 0.15
        base *= opp_adj
    return round(base, 2)


def tier_from_edge_pct(edge_pct: float) -> Optional[str]:
    """Edge as fraction of line: 0.15 = 15% above/below → PRIME."""
    a = abs(edge_pct)
    if a >= 0.15: return 'PRIME'
    if a >= 0.10: return 'STRONG'
    if a >= 0.06: return 'LIGHT'
    return None


def conviction_from_edge_pct(edge_pct: float) -> int:
    a = abs(edge_pct)
    return min(100, max(30, int(50 + a * 300)))


# ─────────────────────────────────────────────────────────────
# Odds API
# ─────────────────────────────────────────────────────────────
def fetch_events(sport_key: str = 'americanfootball_nfl') -> list:
    if not ODDS_KEY: return []
    r = requests.get(
        f'{ODDS_API_BASE}/{sport_key}/events'
        f'?apiKey={ODDS_KEY}',
        timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ events {sport_key}: {r.status_code}')
        return []
    return r.json()


_PROP_FETCH_ERRS: dict = {}

def fetch_event_props(event_id: str, sport_key: str) -> dict:
    """Player-prop markets require the per-event endpoint (Odds API v4).
    Uses paid quota — 1 call per event × per market batch."""
    markets = ','.join(PROP_CONFIG.keys())
    r = requests.get(
        f'{ODDS_API_BASE}/{sport_key}/events/{event_id}/odds'
        f'?apiKey={ODDS_KEY}&regions=us&markets={markets}&oddsFormat=american',
        timeout=15,
    )
    if r.status_code != 200:
        # 2026-08-31: log distinct error bodies. Silent {} return let
        # player_ints (invalid market key) 422 the whole request without
        # surfacing — nfl_props stayed empty for weeks before diagnosed.
        key = (r.status_code, r.text[:80])
        _PROP_FETCH_ERRS[key] = _PROP_FETCH_ERRS.get(key, 0) + 1
        return {}
    return r.json()


def player_id_lookup(name: str, position: Optional[str] = None) -> Optional[dict]:
    """Fuzzy lookup player_id + team from nfl_player_stats latest season.
    Returns {player_id, player_name, team, position} or None."""
    # Odds API uses common name (Patrick Mahomes), nflverse uses same.
    # Filter by position when we know it, order by season desc so we get most recent team.
    q = f'{SB}/rest/v1/nfl_player_stats?player_name=ilike.{name}&order=season.desc,week.desc&limit=1&select=player_id,player_name,team,position'
    if position:
        q += f'&position=eq.{position}'
    r = requests.get(q, headers=H_READ, timeout=15)
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None


# ─────────────────────────────────────────────────────────────
# Row build + orchestration
# ─────────────────────────────────────────────────────────────
def fetch_nfl_player_recent(player_id: int, stat_col: str, season: int,
                             n: int = 10) -> list[dict]:
    """Return last-N per-week rows for a player's stat.

    2026-08-23: NFL parallel to MLB fetch_mlb_player_recent_rows. Uses
    nfl_player_stats (per-week per-player) as source. Rows returned
    newest-first: {season, week, opponent, value, home_away?}.
    Falls back to prior season if current has <3 rows (Week 1 case).
    """
    if not player_id or not stat_col: return []
    def _pull(sn):
        try:
            r = requests.get(f'{SB}/rest/v1/nfl_player_stats',
                             headers=H_READ,
                             params={'player_id': f'eq.{player_id}',
                                     'season': f'eq.{sn}',
                                     'season_type': 'eq.REG',
                                     'select': f'season,week,opponent_team,{stat_col}',
                                     'order': 'week.desc', 'limit': str(n)},
                             timeout=10)
            if r.status_code != 200: return []
            return r.json() or []
        except Exception: return []
    rows = _pull(season)
    if len(rows) < 3 and season > 2020:
        rows = rows + _pull(season - 1)
    # Normalize shape
    out = []
    for row in rows[:n]:
        v = row.get(stat_col)
        if v is None: continue
        try: val = float(v)
        except (TypeError, ValueError): continue
        out.append({
            'season': row.get('season'),
            'week': row.get('week'),
            'opp': row.get('opponent_team'),
            'value': val,
        })
    return out


def fetch_nfl_defense_recent_allowed(opp_team: str, stat_col: str, season: int,
                                       weeks_back: int = 5) -> float | None:
    """Aggregate per-week yards/TDs allowed by opp_team over last N weeks.

    2026-08-23: nfl_team_defense_stats only has SEASON averages — can't
    see recent form. This aggregates from nfl_player_stats by summing
    the stat across all opposing players who played opp_team in the
    last N weeks. Returns avg per-game allowed. None if no data.
    """
    if not opp_team or not stat_col: return None
    try:
        r = requests.get(f'{SB}/rest/v1/nfl_player_stats',
                         headers=H_READ,
                         params={'opponent_team': f'eq.{opp_team}',
                                 'season': f'eq.{season}',
                                 'season_type': 'eq.REG',
                                 'select': f'week,{stat_col}',
                                 'order': 'week.desc', 'limit': '200'},
                         timeout=15)
        if r.status_code != 200: return None
        rows = r.json() or []
    except Exception:
        return None
    # Group by week, sum
    by_week: dict = {}
    for row in rows:
        wk = row.get('week')
        v = row.get(stat_col)
        if wk is None or v is None: continue
        try: by_week[wk] = by_week.get(wk, 0) + float(v)
        except (TypeError, ValueError): continue
    if not by_week: return None
    # Take most-recent weeks_back weeks
    recent_weeks = sorted(by_week.keys(), reverse=True)[:weeks_back]
    if not recent_weeks: return None
    total = sum(by_week[w] for w in recent_weeks)
    return round(total / len(recent_weeks), 1)


def fetch_nfl_player_usage(player_id: int, season: int) -> dict:
    """L4 target_share + air_yards_share + wopr for a pass-catcher.

    Signals whether a WR/TE/RB is genuinely a focal target or a
    peripheral option. High target_share (>=22%) is a real edge for
    receptions + rec_yds props.
    """
    if not player_id: return {}
    try:
        r = requests.get(f'{SB}/rest/v1/nfl_player_stats',
                         headers=H_READ,
                         params={'player_id': f'eq.{player_id}',
                                 'season': f'eq.{season}',
                                 'season_type': 'eq.REG',
                                 'select': 'week,target_share,air_yards_share,wopr,targets',
                                 'order': 'week.desc', 'limit': '4'},
                         timeout=10)
        rows = r.json() if r.status_code == 200 else []
    except Exception:
        return {}
    if not rows: return {}
    def _avg(field):
        vals = [float(row[field]) for row in rows if row.get(field) is not None]
        return round(sum(vals)/len(vals), 3) if vals else None
    return {
        'l4_target_share': _avg('target_share'),
        'l4_air_yards_share': _avg('air_yards_share'),
        'l4_wopr': _avg('wopr'),
        'l4_targets': _avg('targets'),
    }


def compute_nfl_l10_signals(recent_rows: list, line: float, side: str) -> tuple[dict, int]:
    """Compute L10 hit-rate signals from recent player rows.

    Mirror of MLB `l5_confirm`/`l10_hot` pattern that empirically shows
    up in signal tracker at ~58-83% hit rate. Track record vs prop line
    IS a signal in itself.
    """
    sig, bonus = {}, 0
    if not recent_rows: return sig, 0
    is_over = (side or '').upper() == 'OVER'
    values = [r['value'] for r in recent_rows]
    n = len(values)

    # L5 hit count vs line
    l5 = values[:5]
    if l5:
        l5_hits = sum(1 for v in l5 if (v >= line if is_over else v < line))
        l5_pct = l5_hits / len(l5)
        if l5_hits >= 4:  # 4-of-5 or 5-of-5
            bonus += 6
            avg = round(sum(l5)/len(l5), 1)
            sig['l5_confirm'] = f'L5 avg {avg} — {l5_hits}-of-{len(l5)} {"OVER" if is_over else "UNDER"} {line}'
        elif l5_hits <= 1:  # 0-of-5 or 1-of-5 — cold streak on this direction
            bonus -= 5
            sig['l5_cold'] = f'L5 only {l5_hits}-of-{len(l5)} on {"OVER" if is_over else "UNDER"} {line} — fade risk'

    # L10 hit count — bigger sample
    l10 = values[:10]
    if len(l10) >= 8:
        l10_hits = sum(1 for v in l10 if (v >= line if is_over else v < line))
        l10_pct = l10_hits / len(l10)
        if l10_hits >= 8:  # elite streak
            bonus += 5
            avg = round(sum(l10)/len(l10), 1)
            sig['l10_hot'] = f'L10 {l10_hits}-of-{len(l10)} on {"OVER" if is_over else "UNDER"} {line} — dominant trend'
        elif l10_hits <= 2:
            bonus -= 4
            sig['l10_cold'] = f'L10 only {l10_hits}-of-{len(l10)} on this direction — trend against'

    # Store raw rows for chart rendering (mirror MLB _stat_last10)
    sig['_stat_last10'] = recent_rows
    sig['_stat_avg_l5'] = round(sum(l5)/len(l5), 2) if l5 else None
    sig['_stat_avg_l10'] = round(sum(l10)/len(l10), 2) if l10 else None
    sig['_line'] = line
    sig['_direction'] = side.lower() if side else None
    return sig, bonus


def _emit_nfl_ctx_signals(prop_family: str, side: str, ctx: dict, player_team: str,
                           home_team: str, away_team: str) -> tuple[dict, int]:
    """Emit context-based signals for an NFL prop.

    Sport-parity with MLB signal-emit pattern. Returns (signals_dict,
    conviction_bonus). Each fired signal is a rule that ctx-data-value +
    threshold produced a meaningful edge.

    2026-08-23: introduced. Prior NFL scoring only used L4/season/fantasy/
    opp_pct — coverage chip always at ~30% and cards had almost no
    "why" bullets. This closes that gap so NFL cards match MLB depth
    when Week 1 hits.
    """
    sig, bonus = {}, 0
    if not isinstance(ctx, dict): return sig, 0

    is_pass_family = prop_family in ('pass_yds', 'pass_tds', 'pass_attempts',
                                      'pass_completions', 'ints',
                                      'reception_yds', 'receptions')
    is_rush_family = prop_family in ('rush_yds', 'rush_tds', 'rush_attempts')
    is_over = (side or '').upper() == 'OVER'
    player_is_home = player_team == home_team

    # 1) WEATHER (outdoor games — roof open/None; skip if dome/closed)
    roof = str(ctx.get('roof') or '').lower()
    is_outdoor = roof not in ('dome', 'closed')
    if is_outdoor:
        wind = _f(ctx.get('wind'))
        temp = _f(ctx.get('temp'))
        # High wind (>=15 mph) crushes pass yards + receptions + attempts
        if wind is not None and wind >= 15 and is_pass_family:
            if is_over:
                bonus -= 6
                sig['weather_wind'] = f'{int(wind)}mph wind — passing suppressed, fade OVER'
            else:
                bonus += 6
                sig['weather_wind'] = f'{int(wind)}mph wind — passing suppressed, favors UNDER'
        # Cold (<=32°F) also suppresses passing + boosts rushing (usually)
        if temp is not None and temp <= 32:
            if is_pass_family:
                bonus += (6 if not is_over else -4)
                sig['weather_cold'] = f'{int(temp)}°F — cold suppresses passing'
            if is_rush_family and is_over:
                bonus += 3
                sig['weather_cold'] = f'{int(temp)}°F — teams lean rushing in cold'
        # Warm dry conditions — slight over lean for pass
        if wind is not None and wind <= 5 and is_pass_family and is_over:
            bonus += 3
            sig['weather_calm'] = f'Calm conditions ({int(wind or 0)}mph) — passing environment'

    # 2) SHORT WEEK (Thursday games — one side has rest disadvantage)
    home_rest = _f(ctx.get('home_rest')); away_rest = _f(ctx.get('away_rest'))
    player_rest = home_rest if player_is_home else away_rest
    if player_rest is not None and player_rest <= 4:
        bonus -= 4 if is_over else 2
        sig['short_week'] = f'Player team on {int(player_rest)} days rest — short week, hits volume'

    # 3) GAME SCRIPT — spread + total combine to imply how the game flows
    spread = _f(ctx.get('close_spread'))
    total = _f(ctx.get('close_total'))
    if spread is not None and total is not None:
        # Player team's implied total = (total ± spread) / 2
        player_favored_by = -spread if player_is_home else spread
        player_implied_total = (total + player_favored_by) / 2
        # High implied total → more scoring plays → more props hit OVER
        if player_implied_total >= 25:
            if is_over:
                bonus += 4
                sig['implied_high'] = f'Player team implied {player_implied_total:.1f} pts — high-scoring script'
        elif player_implied_total <= 17:
            if is_over:
                bonus -= 4
                sig['implied_low'] = f'Player team implied {player_implied_total:.1f} pts — low-scoring script'
            else:
                bonus += 3
                sig['implied_low'] = f'Player team implied {player_implied_total:.1f} pts — supports UNDER'

        # Game script — big underdogs pass more (garbage time),
        # big favorites run more (clock control)
        if player_favored_by >= 7 and is_rush_family and is_over:
            bonus += 4
            sig['game_script_run'] = f'Player team favored by {player_favored_by:.1f} — clock-control rushing'
        elif player_favored_by <= -7 and is_pass_family and is_over:
            bonus += 4
            sig['game_script_pass'] = f'Player team dog by {-player_favored_by:.1f} — pass-heavy game script'

    # 4) QB VS OPPONENT HISTORY (pass_yds + pass_tds only)
    if prop_family in ('pass_yds', 'pass_tds'):
        qb_col = 'home_qb_vs_team_recent_pass_yds_avg' if player_is_home \
                 else 'away_qb_vs_team_recent_pass_yds_avg'
        qb_avg = _f(ctx.get(qb_col))
        if qb_avg is not None and qb_avg > 0:
            if prop_family == 'pass_yds':
                # Compare QB's recent avg vs the prop line implicitly (bonus only when strong signal)
                if qb_avg >= 280 and is_over:
                    bonus += 5
                    sig['qb_vs_team'] = f'QB avg {qb_avg:.0f} pass yds vs opp recently — dominant matchup'
                elif qb_avg <= 200 and not is_over:
                    bonus += 5
                    sig['qb_vs_team'] = f'QB avg {qb_avg:.0f} pass yds vs opp recently — struggles here'

    # 5) INJURY OUTS on offense — signals sig off
    inj = ctx.get('panel_injury_outs')
    if isinstance(inj, list) and len(inj) >= 3:
        # Many injuries on offense — depressed volume for skill players
        bonus -= 3 if is_over else 2
        sig['injury_load'] = f'{len(inj)} key players OUT — depressed offense'

    return sig, bonus


def build_prop_row(event: dict, market: dict, outcome: dict, opp_map: dict,
                   aliases: dict, season: int, ctx: dict | None = None) -> Optional[dict]:
    """Build one nfl_props row from an Odds API prop outcome."""
    market_key = market.get('key')
    cfg = PROP_CONFIG.get(market_key)
    if not cfg: return None

    player_name = outcome.get('description')
    line = _f(outcome.get('point'))
    side = (outcome.get('name') or '').upper()   # 'Over' | 'Under'
    odds = _i(outcome.get('price'))
    if not player_name or line is None or side not in ('OVER', 'UNDER'):
        return None

    # Player lookup
    player = player_id_lookup(player_name, position=cfg['position'])
    if not player:
        return None
    player_id = player['player_id']
    player_team = player['team']
    position = player.get('position')

    # Opponent = whichever team isn't the player's
    home_raw = event.get('home_team'); away_raw = event.get('away_team')
    home_canon = aliases.get(home_raw); away_canon = aliases.get(away_raw)
    if not home_canon or not away_canon: return None
    opp_team = away_canon if player_team == home_canon else home_canon

    l4, season_avg, gp = player_rolling(player_id, cfg['col'], season)
    # 2026-08-09 Phase 3: fantasy-informed projection lookup.
    # week is derived per-event; keep simple fallback (season=season, week=1).
    fantasy_proj_stat = None
    try:
        # Derive week from commence_time — same logic as nfl_game_context._nfl_week
        commence = event.get('commence_time') or ''
        from datetime import datetime as _dt, date as _date
        dt = _dt.fromisoformat(commence.replace('Z','+00:00')) if commence else None
        if dt:
            year = dt.year
            sept1 = _date(year, 9, 1)
            first_thu = (3 - sept1.weekday()) % 7
            wk1_start = _date(year, 9, 1 + first_thu)
            days = (dt.date() - wk1_start).days
            wk = max(1, min(18, days // 7 + 1)) if days >= 0 else 1
        else:
            wk = 1
        fp_map = load_fantasy_projections(season, wk)
        import re, unicodedata as ud
        def _nk(name):
            n = ud.normalize('NFKD', name or '').encode('ascii','ignore').decode('ascii')
            n = re.sub(r'\b(jr|iii|ii|iv|sr)\b\.?', '', n.lower())
            n = re.sub(r'[^a-z0-9\s]', '', n).strip()
            return re.sub(r'\s+', ' ', n)
        pkey = (_nk(player['player_name']), position)
        pdata = fp_map.get(pkey)
        if pdata:
            fantasy_proj_stat = pdata.get(cfg.get('fantasy_col'))
    except Exception:
        pass
    if l4 is None and season_avg is None and fantasy_proj_stat is None:
        return None
    opp_pct = opp_rank(opp_map, opp_team, cfg['opp_col'])
    proj = project(l4, season_avg, cfg['league_baseline'], opp_pct,
                    fantasy_proj_stat=fantasy_proj_stat)
    if proj is None: return None

    edge = round(proj - line, 2)
    edge_pct = edge / line if line > 0 else 0
    # For UNDER picks: flip sign — model says lower → UNDER edge
    directional_edge = edge_pct if side == 'OVER' else -edge_pct
    if directional_edge <= 0:
        # Wrong-side pick — skip
        return None

    tier = tier_from_edge_pct(directional_edge)
    if not tier: return None
    conv = conviction_from_edge_pct(directional_edge)

    # 2026-09-02 EARLY-SEASON TIER CAP (Q2 discussion decision).
    # NFL Weeks 1-3 have no live calibration data — playbook can't
    # meaningfully differentiate PRIME from STRONG. Cap PRIME → STRONG
    # during Weeks 1-3 until sample accumulates. Auto-expires Week 3-4.
    # Same discipline for NCAAF (see project_nfl_ncaaf_week1_readiness_820).
    _gd = (event.get('commence_time') or '')[:10]
    if _gd and _gd < '2026-09-22':  # 2026 NFL Week 3 ends Sept 22
        if tier == 'PRIME':
            tier = 'STRONG'
            conv = min(conv, 72)  # cap conviction at STRONG floor

    # 2026-08-23 NFL SIGNAL-EMIT WIRE-UP. Prior scoring only tracked
    # L4/season/fantasy/opp_pct as metadata — no context signals fired.
    # Now emits weather / short_week / game_script / qb_vs_team / injury
    # signals from ctx and adjusts conviction. Mirror of MLB pattern.
    prop_family = market_key.replace('player_', '')
    ctx_sigs, ctx_bonus = _emit_nfl_ctx_signals(
        prop_family=prop_family, side=side, ctx=ctx or {},
        player_team=player_team, home_team=home_canon, away_team=away_canon,
    )

    # 2026-08-23 NFL L10 HIT-RATE + CHART DATA. User asked: track record
    # of hits over the line IS a signal in itself. Mirror MLB _stat_last10
    # pattern. Fetches player's last 10 weeks in this stat, computes
    # L5/L10 hit counts vs the prop line, emits l5_confirm/l10_hot/etc
    # signals + stores raw rows for chart render.
    recent_rows = fetch_nfl_player_recent(player_id, cfg['col'], season, n=10)
    l10_sigs, l10_bonus = compute_nfl_l10_signals(recent_rows, line, side)

    # 2026-08-23 DEFENSE L5 RECENT-FORM SIGNAL. Opp def is currently a
    # season-average percentile — no recent-form view. This aggregates
    # opponents' yards allowed to opp_team over last 5 weeks. Emits
    # def_recent_soft / def_recent_stout signals when yielded avg
    # sits meaningfully above/below league baseline.
    def_bonus = 0
    def_recent_avg = fetch_nfl_defense_recent_allowed(
        opp_team, cfg['col'], season, weeks_back=5)
    if def_recent_avg is not None:
        baseline = cfg['league_baseline']
        # Loose delta thresholds — "soft" if opp allows 15%+ over baseline
        # (over-friendly) or "stout" if 15%- under baseline (under-friendly)
        pct_delta = (def_recent_avg - baseline) / baseline if baseline else 0
        if pct_delta >= 0.15:
            if side.upper() == 'OVER':
                def_bonus += 5
                l10_sigs['def_recent_soft'] = (
                    f'Opp {opp_team} allowed {def_recent_avg:.1f} {cfg["label"]}/game L5 '
                    f'(+{int(pct_delta*100)}% vs baseline) — soft matchup'
                )
        elif pct_delta <= -0.15:
            if side.upper() == 'UNDER':
                def_bonus += 5
                l10_sigs['def_recent_stout'] = (
                    f'Opp {opp_team} allowed {def_recent_avg:.1f} {cfg["label"]}/game L5 '
                    f'({int(pct_delta*100)}% vs baseline) — stout matchup'
                )

    # 2026-08-23 TARGET SHARE SIGNAL for pass-catcher props. Available
    # directly in nfl_player_stats. High target share = genuinely focal
    # WR/TE — meaningful edge for receptions + rec_yds overs.
    usage_bonus = 0
    if prop_family in ('reception_yds', 'receptions', 'anytime_td'):
        usage = fetch_nfl_player_usage(player_id, season)
        ts = usage.get('l4_target_share')
        if ts is not None:
            if ts >= 0.25 and side.upper() == 'OVER':
                usage_bonus += 5
                l10_sigs['target_share_elite'] = (
                    f'L4 target share {ts:.1%} — elite alpha, high volume'
                )
            elif ts >= 0.22 and side.upper() == 'OVER':
                usage_bonus += 3
                l10_sigs['target_share_high'] = (
                    f'L4 target share {ts:.1%} — genuine focal option'
                )
            elif ts <= 0.10 and side.upper() == 'OVER':
                usage_bonus -= 4
                l10_sigs['target_share_low'] = (
                    f'L4 target share {ts:.1%} — peripheral, low volume'
                )
        # Also store raw usage metrics for downstream display
        for k, v in usage.items():
            if v is not None:
                l10_sigs[f'_{k}'] = v

    total_bonus = ctx_bonus + l10_bonus + def_bonus + usage_bonus
    if total_bonus:
        conv = max(0, min(100, conv + total_bonus))
        # Re-tier if bonus significantly changed conviction (may promote LEAN→STRONG etc)
        # Keep original tier if bonus doesn't clear next threshold — no unearned lifts

    signals = {
        'l4': l4, 'season_avg': season_avg,
        'league_baseline': cfg['league_baseline'],
        'opp_pct': opp_pct, 'opp_col': cfg['opp_col'],
        'edge_pct': round(directional_edge * 100, 1),
        'games_used': gp,
        'label': cfg['label'],
    }
    signals.update(ctx_sigs)   # merge fired ctx signals
    signals.update(l10_sigs)   # merge L10 hit-rate + chart data

    return {
        'game_id': event.get('id'),
        'game_date': (event.get('commence_time') or '')[:10],
        'season': season,
        'week': None,   # nflverse week schedule join (Phase 3.1)
        'home_team': home_canon,
        'away_team': away_canon,
        'player_id': player_id,
        'player_name': player['player_name'],
        'team': player_team,
        'opponent_team': opp_team,
        'position': position,
        'prop_type': market_key.replace('player_', ''),
        'pick_side': side,
        'pick_line': line,
        'odds_american': odds,
        'projected': proj,
        'edge': edge,
        'l4_avg': l4,
        'season_avg': season_avg,
        'opp_rank': int(round(opp_pct * 32)) if opp_pct is not None else None,
        'tier': tier,
        'conviction': conv,
        'signals': signals,
    }


def _to_pipeline_props_shape(row: dict) -> dict:
    """Map nfl_props row shape → nfl_pipeline_props schema (2026-08-22 bridge).

    nfl_props uses pick_side + pick_line + bare prop_type ('pass_yds').
    nfl_pipeline_props (canonical cross-sport table) uses direction +
    prop_line + suffixed prop_type ('pass_yds_over'). Cross-sport tooling
    (prop_ensemble_scorer, backfill_prop_lookback, snapshot_pick_lock,
    grade_prop_jerry_reads) reads nfl_pipeline_props exclusively — without
    this bridge NFL props are invisible to the ensemble framework.

    Bridge is dual-write, not a table rename — nfl_weekly_card.py and
    resolve_nfl_results.py still read from nfl_props unchanged.
    """
    direction = (row.get('pick_side') or '').lower()
    if direction not in ('over', 'under'):
        return None
    prop_type_base = row.get('prop_type') or ''
    return {
        'game_date': row.get('game_date'),
        'game_id': row.get('game_id'),
        'week': row.get('week'),
        'season_phase': 'regular',   # NFL prop pipeline currently regular-season only
        'player_name': row.get('player_name'),
        'player_team': row.get('team'),
        'position': row.get('position'),
        'opp_team': row.get('opponent_team'),
        'home_away': 'HOME' if row.get('team') == row.get('home_team') else 'AWAY',
        'matchup': f"{row.get('away_team','')} @ {row.get('home_team','')}",
        'prop_type': f'{prop_type_base}_{direction}',
        'prop_line': row.get('pick_line'),
        'direction': direction,
        'conviction': row.get('conviction', 0),
        'tier': row.get('tier'),
        'signals': row.get('signals') or {},
        'book_line': row.get('pick_line'),
        'book_over_odds': row.get('odds_american') if direction == 'over' else None,
        'book_under_odds': row.get('odds_american') if direction == 'under' else None,
    }


def upsert_props(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            print(f"  [DRY] {r['player_name']:22} {r['prop_type']:16} "
                  f"{r['pick_side']:5} {r['pick_line']:>5.1f}  "
                  f"proj={r['projected']:>5.1f} edge={r['edge']:+.1f}  "
                  f"tier={r['tier']:<6} conv={r['conviction']}")
        return len(rows)
    # Bridge (2026-08-22): dual-write to nfl_pipeline_props so cross-sport
    # tooling (ensemble_scorer, backfill_prop_lookback, etc.) can see NFL
    # props. Fire-and-log — nfl_props write is authoritative.
    try:
        bridge_rows = [b for b in (_to_pipeline_props_shape(r) for r in rows) if b]
        if bridge_rows:
            br = requests.post(
                f'{SB}/rest/v1/nfl_pipeline_props?on_conflict=game_date,game_id,player_name,prop_type,direction,prop_line',
                headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                json=bridge_rows, timeout=30,
            )
            if br.status_code in (200, 201, 204):
                print(f'  ↔ bridge: {len(bridge_rows)} rows mirrored to nfl_pipeline_props')
            else:
                print(f'  ⚠ bridge failed (non-fatal): {br.status_code} {br.text[:150]}')
    except Exception as _e:
        print(f'  ⚠ bridge write raised (non-fatal): {_e}')

    r = requests.post(
        f'{SB}/rest/v1/nfl_props?on_conflict=game_id,player_name,prop_type,pick_side',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def _load_nfl_ctx_by_game(game_dates: list) -> dict:
    """Batch-fetch nfl_game_context for the given dates, keyed by game_id.

    2026-08-23: added so build_prop_row can emit context-based signals
    (weather, rest, game_script, opp_def_tier) — mirrors MLB signal-emit
    pattern. Prior NFL scoring only used L4/season/fantasy proj +
    opp_pct — no per-context signals. Result: cards had almost no
    "why" bullets, coverage chip always at ~30%.
    """
    if not game_dates: return {}
    from urllib.parse import quote
    all_ctx = {}
    for gd in set(game_dates):
        try:
            r = requests.get(f'{SB}/rest/v1/nfl_game_context',
                             headers=H_READ,
                             params={'game_date': f'eq.{gd}',
                                     'select': 'game_id,home_team,away_team,temp,wind,roof,'
                                               'home_rest,away_rest,close_spread,close_total,'
                                               'projected_total,projected_spread,'
                                               'panel_pred_total,panel_pred_home_pts,panel_pred_away_pts,'
                                               'home_off_rating,away_off_rating,'
                                               'panel_injury_outs,'
                                               'home_qb_vs_team_recent_pass_yds_avg,'
                                               'away_qb_vs_team_recent_pass_yds_avg,'
                                               'home_qb_vs_team_recent_pass_td_avg,'
                                               'away_qb_vs_team_recent_pass_td_avg'},
                             timeout=15)
            if r.status_code == 200:
                for row in (r.json() or []):
                    all_ctx[row.get('game_id')] = row
        except Exception:
            pass
    return all_ctx


def run(dry_run: bool = False, single_player: Optional[str] = None) -> None:
    print(f'=== NFL props generator · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort')
        return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ nfl_team_aliases empty')
        return

    season = _et_now().year if _et_now().month >= 9 else _et_now().year - 1
    opp_map = load_opponent_defense(season)
    print(f'  aliases={len(aliases)}  opp_map={len(opp_map)} teams  season={season}')

    events = fetch_events('americanfootball_nfl') + fetch_events('americanfootball_nfl_preseason')
    print(f'  events with props potentially available: {len(events)}')

    # 2026-08-23 batch-load ctx so build_prop_row can emit signals
    game_dates_seen = sorted(set(
        (e.get('commence_time') or '')[:10] for e in events if e.get('commence_time')
    ))
    ctx_by_game = _load_nfl_ctx_by_game(game_dates_seen)
    print(f'  ctx loaded for {len(ctx_by_game)} games')

    all_rows = []
    events_with_props = 0
    for evt in events:
        eid = evt.get('id')
        if not eid: continue
        # NOTE: fetch_event_props hits paid Odds API quota. Cap at reasonable
        # per-run — in production we'd only pull events kicking off in next 14 days.
        # 2026-08-31: bumped 168h → 336h. Prior 7-day window silently skipped
        # every Week 1 game (Aug 31 → Sept 10 = 240h out) so nfl_props stayed
        # empty. 14-day window covers Week 1 + Week 2 Thu without exploding quota.
        commence = evt.get('commence_time', '')
        try:
            dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
            hrs_out = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hrs_out > 336 or hrs_out < -1:  # only next 14 days
                continue
        except Exception:
            continue

        event_data = fetch_event_props(eid, 'americanfootball_nfl')
        markets_seen = 0
        for book in (event_data.get('bookmakers') or []):
            for market in (book.get('markets') or []):
                if market.get('key') not in PROP_CONFIG:
                    continue
                markets_seen += 1
                for outcome in market.get('outcomes') or []:
                    if single_player and single_player.lower() not in (outcome.get('description') or '').lower():
                        continue
                    row = build_prop_row(evt, market, outcome, opp_map, aliases, season,
                                          ctx=ctx_by_game.get(evt.get('id')))
                    if row:
                        all_rows.append(row)
            if markets_seen: break   # first book that has props is enough
        if markets_seen:
            events_with_props += 1

    print(f'  events with props pulled: {events_with_props}')
    print(f'  prop picks generated: {len(all_rows)}')
    if _PROP_FETCH_ERRS:
        print(f'  ⚠ Odds API prop fetch errors ({sum(_PROP_FETCH_ERRS.values())} total):')
        for (code, body), n in sorted(_PROP_FETCH_ERRS.items(), key=lambda x: -x[1]):
            print(f'      · {code} × {n}: {body}')

    # Dedup on (game_id, player_name, prop_type, pick_side) — keep highest conv
    dedup: dict = {}
    for r in all_rows:
        key = (r['game_id'], r['player_name'], r['prop_type'], r['pick_side'])
        if key not in dedup or (dedup[key]['conviction'] or 0) < (r['conviction'] or 0):
            dedup[key] = r
    rows = list(dedup.values())

    written = upsert_props(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_props')

    by_tier = defaultdict(int)
    for r in rows:
        by_tier[r['tier']] += 1
    print(f'  by tier: {dict(by_tier)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--player', default=None, help='Test one player (partial match)')
    args = ap.parse_args()
    run(dry_run=args.dry_run, single_player=args.player)


if __name__ == '__main__':
    main()
