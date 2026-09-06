"""NFL game context pipeline — server-side analog of mlb_game_context /
ncaab_game_context.

Pulls today's + upcoming NFL games from the Odds API, joins nfl_team_stats,
computes EPA-based projections + signal confluence + sweat score +
primary_play, and upserts to nfl_game_context.

Model (v1 — Week 1 baseline, iterates once we have live 2026 data):
  home_off_rating = (pass_epa + rush_epa) / games         (from nfl_team_stats)
  power_diff       = home_off_rating - away_off_rating
  projected_spread = power_diff * K_PTS + HOME_FIELD      (positive = home fav)
  projected_total  = base_total + weather/roof adjustments

Cohort tags computed inline (mirrors nfl_cohort_backfill logic).

Sign convention: nflverse standard (close_spread > 0 = home favored).
nfl_odds_pull.py flips Odds API native at write time to match. This
script assumes nflverse convention throughout.

Usage:
    python nfl_game_context.py             # today + next 7 days
    python nfl_game_context.py --dry-run

Required env: SUPABASE_URL, SUPABASE_KEY, ODDS_API_KEY
"""
import argparse
import os
import sys
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

# Calibration constants (revisit after Week 4 with live 2026 data)
K_PTS = 0.15               # EPA-diff → spread points scaling
HOME_FIELD_PTS = 2.2       # league-avg HFA in modern era
BASE_TOTAL = 44.5          # 2022-2025 league-avg total
DOME_ADJ = 1.5             # dome → +1.5 total
COLD_ADJ = -2.0            # temp <= 35°F → -2.0 total
WIND_ADJ = -1.5            # wind >= 15 → -1.5 total

# From nfl_cohort_backfill
NFL_DIVISION = {
    'BUF':'AFC East','MIA':'AFC East','NE':'AFC East','NYJ':'AFC East',
    'BAL':'AFC North','CIN':'AFC North','CLE':'AFC North','PIT':'AFC North',
    'HOU':'AFC South','IND':'AFC South','JAX':'AFC South','TEN':'AFC South',
    'DEN':'AFC West','KC':'AFC West','LV':'AFC West','LAC':'AFC West',
    'DAL':'NFC East','NYG':'NFC East','PHI':'NFC East','WAS':'NFC East',
    'CHI':'NFC North','DET':'NFC North','GB':'NFC North','MIN':'NFC North',
    'ATL':'NFC South','CAR':'NFC South','NO':'NFC South','TB':'NFC South',
    'ARI':'NFC West','LA':'NFC West','SF':'NFC West','SEA':'NFC West',
}

WEST_COAST = {'LA', 'LAC', 'SF', 'SEA'}


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


def load_team_stats(season: int) -> dict:
    """Return dict[team_abbrev → stats row] for the given season."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_stats?season=eq.{season}&season_type=eq.REG&select=*',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    return {row['team']: row for row in r.json()}


def load_team_def_stats(season: int) -> dict:
    """2026-08-09 Phase 2: return dict[team_abbrev → def_stats row].
    Falls back to prior season if current is empty (preseason case)."""
    for candidate in (season, season - 1):
        r = requests.get(
            f'{SB}/rest/v1/nfl_team_defense_stats?season=eq.{candidate}&season_type=eq.REG&select=*',
            headers=H_READ, timeout=15,
        )
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return {row['team']: row for row in rows}
    return {}


# Minimum games/team avg before we trust current-season stats over prior year.
# Under this threshold we fall back to prior season (regressed to league mean).
# 4 games ≈ Week 5 — matches the point where cohort samples are meaningful.
MIN_GAMES_PER_TEAM_AVG = 4.0


def _avg_games(stats_dict: dict) -> float:
    if not stats_dict: return 0.0
    games = [(row.get('games') or 0) for row in stats_dict.values()]
    return sum(games) / max(1, len(games))


def _league_mean_per_game(stats_dict: dict) -> dict:
    """Compute league-mean per-game rates for regression-to-mean blending."""
    if not stats_dict: return {}
    totals = {'pass_epa': 0.0, 'rush_epa': 0.0, 'pass_cpoe': 0.0, 'games': 0.0}
    n_cpoe = 0
    for row in stats_dict.values():
        g = row.get('games') or 0
        if g < 1: continue
        totals['pass_epa'] += (row.get('pass_epa') or 0) / g
        totals['rush_epa'] += (row.get('rush_epa') or 0) / g
        totals['games']    += 1
        cp = row.get('pass_cpoe')
        if cp is not None:
            totals['pass_cpoe'] += cp
            n_cpoe += 1
    n = totals['games'] or 1
    return {
        'pass_epa_per_g': totals['pass_epa'] / n,
        'rush_epa_per_g': totals['rush_epa'] / n,
        'pass_cpoe':      totals['pass_cpoe'] / max(1, n_cpoe),
    }


def _regress_to_mean(stats_dict: dict, shrink: float = 0.4) -> dict:
    """Blend prior-season stats toward league mean.
    shrink=0.4 means 60% prior-season signal, 40% pulled toward mean.
    Handles roster turnover (new QB, coach change) without discarding
    the prior-year signal entirely. Preserves the 'games' count so the
    downstream per-game divides still work.
    """
    if not stats_dict: return {}
    mean = _league_mean_per_game(stats_dict)
    out = {}
    for team, row in stats_dict.items():
        g = row.get('games') or 0
        if g < 1:
            out[team] = row
            continue
        new = dict(row)
        # Blend per-game rates, then re-scale back to season totals via games count
        team_pe_pg = (row.get('pass_epa') or 0) / g
        team_re_pg = (row.get('rush_epa') or 0) / g
        blend_pe_pg = (1 - shrink) * team_pe_pg + shrink * mean.get('pass_epa_per_g', 0)
        blend_re_pg = (1 - shrink) * team_re_pg + shrink * mean.get('rush_epa_per_g', 0)
        new['pass_epa'] = blend_pe_pg * g
        new['rush_epa'] = blend_re_pg * g
        cp = row.get('pass_cpoe')
        if cp is not None:
            new['pass_cpoe'] = (1 - shrink) * cp + shrink * mean.get('pass_cpoe', 0)
        out[team] = new
    return out


def get_qb_vs_team_stats(team_abbr: str, opponent_abbr: str) -> dict:
    """Look up starting QB for `team_abbr` and their career+recent stats vs
    `opponent_abbr` defense from nfl_qb_vs_team table.

    Strategy: query nfl_qb_vs_team for opponent_team = opp, filter to QBs
    whose recent games (via nfl_player_stats or last_faced_date) indicate
    they're the current starter for `team_abbr`. Simplification: pick QB
    with highest career_starts vs this opp among those recently active
    (most-recent last_faced_date within 2 seasons).

    Returns dict of qb_vs_team fields (empty if no data).

    2026-08-21: shipped with nfl_qb_vs_team backfill (1425 rows).
    """
    if not team_abbr or not opponent_abbr:
        return {}
    try:
        # Query nfl_player_stats for the most-recent QB start for this team
        # to determine current starting QB
        r = requests.get(
            f'{SB}/rest/v1/nfl_player_stats',
            headers=H_READ,
            params={
                'recent_team': f'eq.{team_abbr}',
                'position': 'eq.QB',
                'select': 'player_id,player_display_name,season,week,attempts',
                'order': 'season.desc,week.desc',
                'limit': '10',
            }, timeout=10,
        )
        recent = r.json() if r.status_code == 200 else []
        # Filter to actual starts (>= 15 attempts) and get most-recent QB
        starters = [row for row in recent if isinstance(row, dict)
                    and (row.get('attempts') or 0) >= 15]
        if not starters:
            return {}
        qb_id = starters[0].get('player_id')
        qb_name = starters[0].get('player_display_name')
        if not qb_id:
            return {}

        # Now fetch this QB's stats vs the specific opponent
        vs = requests.get(
            f'{SB}/rest/v1/nfl_qb_vs_team',
            headers=H_READ,
            params={
                'qb_id': f'eq.{qb_id}',
                'opponent_team': f'eq.{opponent_abbr}',
                'select': 'career_starts,career_qb_rating,career_yds_per_att,career_cmp_pct,career_td_int_ratio,recent_n_starts,recent_pass_yds_avg,recent_pass_td_avg,recent_int_avg,recent_qb_rating,qb_name',
            }, timeout=10,
        )
        rows = vs.json() if vs.status_code == 200 else []
        if not rows:
            # QB has never faced this opponent — return name only
            return {'qb_id': qb_id, 'qb_name': qb_name}
        row = rows[0]
        return {
            'qb_id': qb_id,
            'qb_name': qb_name or row.get('qb_name'),
            'career_starts': row.get('career_starts'),
            'career_qb_rating': row.get('career_qb_rating'),
            'career_yds_per_att': row.get('career_yds_per_att'),
            'career_cmp_pct': row.get('career_cmp_pct'),
            'career_td_int_ratio': row.get('career_td_int_ratio'),
            'recent_n_starts': row.get('recent_n_starts'),
            'recent_pass_yds_avg': row.get('recent_pass_yds_avg'),
            'recent_pass_td_avg': row.get('recent_pass_td_avg'),
            'recent_int_avg': row.get('recent_int_avg'),
            'recent_qb_rating': row.get('recent_qb_rating'),
        }
    except Exception as e:
        print(f'  ⚠ get_qb_vs_team_stats({team_abbr} vs {opponent_abbr}) failed: {e}')
        return {}


_RANK_CACHE: dict = {}

def _rank_teams_by(field: str, team_stats: dict, is_defense: bool = False,
                   per_game: bool = True, higher_is_better: bool = True) -> dict:
    """Return {team_abbrev → 1-based rank} across all teams by field.
    Cached across calls in a run (2×32 teams sorted once)."""
    key = (field, id(team_stats), higher_is_better)
    if key in _RANK_CACHE: return _RANK_CACHE[key]
    scores = []
    for team, s in team_stats.items():
        if not s: continue
        raw = s.get(field)
        if raw is None: continue
        try:
            v = float(raw)
            if per_game:
                g = float(s.get('games') or 0)
                if g <= 0: continue
                v = v / g
            scores.append((team, v))
        except (TypeError, ValueError):
            continue
    scores.sort(key=lambda x: -x[1] if higher_is_better else x[1])
    ranks = {team: i + 1 for i, (team, _) in enumerate(scores)}
    _RANK_CACHE[key] = ranks
    return ranks


def _build_team_summary(team: str, off_stats: dict, def_stats: dict,
                        all_off: dict, all_def: dict) -> dict:
    """Casual-friendly per-team summary for game-card render.

    Blob shape (all values nullable when sample missing):
      { pts_pg, pts_allowed_pg,
        rush_yds_pg, rush_yds_allowed_pg,
        pass_yds_pg, pass_yds_allowed_pg,
        sacks_pg, turnovers_forced_pg,
        rank_rush_off, rank_rush_def, rank_pass_off, rank_pass_def,
        rank_scoring_off, rank_scoring_def,
        season_source, games_sample }

    Offense fields come from nfl_team_stats (season totals ÷ games).
    Defense fields come from nfl_team_defense_stats (already per-game).
    Ranks computed across all 32 teams. Lower rank = better (1st = best).
    """
    if not off_stats:
        return {'error': 'no offense stats for team'}
    games = off_stats.get('games') or 0
    def _pg(field):
        v = off_stats.get(field)
        if v is None or not games: return None
        try: return round(float(v) / float(games), 1)
        except (TypeError, ValueError): return None

    # Scoring: pass_tds + rush_tds + ~1.05 XP each = rough proxy. Uses
    # actual scored fields when available; falls back to TD*6.9 estimate.
    pts_pg = None
    total_tds = ((off_stats.get('pass_tds') or 0)
                 + (off_stats.get('rush_tds') or 0))
    fg_made = off_stats.get('fg_made') or 0
    if games and (total_tds or fg_made):
        pts_pg = round((total_tds * 6.9 + fg_made * 3) / games, 1)

    summary = {
        'pts_pg':               pts_pg,
        'pts_allowed_pg':       def_stats.get('def_ppg') if def_stats else None,
        'rush_yds_pg':          _pg('rush_yards'),
        'rush_yds_allowed_pg':  def_stats.get('def_rush_ypg') if def_stats else None,
        'pass_yds_pg':          _pg('pass_yards'),
        'pass_yds_allowed_pg':  def_stats.get('def_pass_ypg') if def_stats else None,
        'sacks_pg':             _pg('def_sacks'),
        'turnovers_forced_pg':  round(((_pg('def_ints') or 0)
                                       + (_pg('def_fumbles_forced') or 0)), 1),
        'season_source':        off_stats.get('season'),
        'games_sample':         games,
    }

    # Ranks (1-based, lower = better). Skip when field missing.
    off_yds_ranks = _rank_teams_by('rush_yards', all_off, higher_is_better=True)
    if team in off_yds_ranks: summary['rank_rush_off'] = off_yds_ranks[team]
    pass_ranks = _rank_teams_by('pass_yards', all_off, higher_is_better=True)
    if team in pass_ranks: summary['rank_pass_off'] = pass_ranks[team]
    # Defense: lower yds allowed = better rank
    if all_def:
        def_rush_ranks = _rank_teams_by('def_rush_ypg', all_def,
                                        per_game=False, higher_is_better=False)
        if team in def_rush_ranks: summary['rank_rush_def'] = def_rush_ranks[team]
        def_pass_ranks = _rank_teams_by('def_pass_ypg', all_def,
                                        per_game=False, higher_is_better=False)
        if team in def_pass_ranks: summary['rank_pass_def'] = def_pass_ranks[team]
        def_scoring_ranks = _rank_teams_by('def_ppg', all_def,
                                           per_game=False, higher_is_better=False)
        if team in def_scoring_ranks: summary['rank_scoring_def'] = def_scoring_ranks[team]
    scoring_ranks = _rank_teams_by('pass_tds', all_off, higher_is_better=True)
    if team in scoring_ranks: summary['rank_scoring_off'] = scoring_ranks[team]

    return summary


def load_team_stats_with_fallback(current_season: int) -> tuple:
    """Return (stats_dict, source_label). Falls back to prior-season
    regressed-to-mean when current-year sample is too thin (Weeks 1-3).
    """
    current = load_team_stats(current_season)
    if _avg_games(current) >= MIN_GAMES_PER_TEAM_AVG:
        return current, 'current'
    prior = load_team_stats(current_season - 1)
    if _avg_games(prior) >= MIN_GAMES_PER_TEAM_AVG:
        return _regress_to_mean(prior, shrink=0.4), 'prior_season_regressed'
    # Neither season is populated — return whatever we have but flag it
    return current or prior, 'none'


def compute_off_rating(stats: dict) -> Optional[float]:
    """EPA per game (pass + rush). Higher = better offense.
    League-avg ≈ 0 by definition of EPA; strong teams 30-50, weak -20 to -40.
    """
    if not stats: return None
    games = stats.get('games') or 0
    if games < 1: return None
    pe = stats.get('pass_epa') or 0
    re = stats.get('rush_epa') or 0
    return round(float(pe + re) / float(games), 3)


def compute_projections(home_stats: dict, away_stats: dict, roof: str,
                        temp: Optional[int], wind: Optional[int],
                        home_def: Optional[dict] = None,
                        away_def: Optional[dict] = None) -> dict:
    """EPA-based projected spread + total with venue + matchup adjustments.

    2026-08-09 Phase 2: added home_def/away_def params. When defensive
    stats are available, project total per-matchup instead of flat
    BASE_TOTAL for every game. Each team's projected points is:

        team_pts = LEAGUE_AVG_PPG × (own_off_index) × (opp_def_index)

    where indices normalize to 1.0 at league average. A top offense
    (index 1.15) vs a bottom defense (index 1.12) projects ~29 points
    (25 × 1.15 × 1.12 × pace_adj). Falls back to legacy flat model when
    defensive stats are missing (preserves behavior for missing seasons).
    """
    h_rate = compute_off_rating(home_stats)
    a_rate = compute_off_rating(away_stats)

    out = {
        'home_off_rating': h_rate,
        'away_off_rating': a_rate,
        'power_diff': None,
        'projected_spread': None,
        'projected_total': None,
        'model_pred_home_points': None,
        'model_pred_away_points': None,
    }
    if h_rate is None or a_rate is None:
        return out

    power_diff = round(h_rate - a_rate, 2)
    projected_spread = round(power_diff * K_PTS + HOME_FIELD_PTS, 2)

    # Venue adjustments (weather/dome apply to both sides equally)
    venue_adj = 0.0
    if roof and roof.lower() in ('dome', 'closed'): venue_adj += DOME_ADJ
    if temp is not None and int(temp) <= 35: venue_adj += COLD_ADJ
    if wind is not None and int(wind) >= 15: venue_adj += WIND_ADJ

    # Matchup-adjusted per-team projection (when defense data available)
    LEAGUE_AVG_PPG = 22.25   # BASE_TOTAL/2 = each team gets half of the 44.5
    LEAGUE_AVG_PASS_EPA = 0.10   # rough NFL average pass_epa per play
    LEAGUE_AVG_RUSH_EPA = -0.05  # rush is slightly negative EPA on average
    LEAGUE_AVG_DEF_PPG = 22.25   # points allowed on average

    if home_def and away_def:
        # Build off-index (1.0 = league avg, >1 = above avg)
        # Tightened 2026-08-09 evening: ±0.15 max (not ±0.5) so extreme teams
        # don't compound to absurd totals (DET@CIN was projecting 85).
        def _off_idx(stats):
            g = float(stats.get('games') or 0) or 1
            pe = float(stats.get('pass_epa') or 0) / g
            re = float(stats.get('rush_epa') or 0) / g
            combined = pe + re
            # ±0.15 max deviation from 1.0
            raw = 1.0 + 1.5 * (combined - (LEAGUE_AVG_PASS_EPA + LEAGUE_AVG_RUSH_EPA))
            return max(0.85, min(1.15, raw))
        def _def_idx(def_stats):
            # Def index: >1 means bad defense (allows more). Tighter cap 0.88-1.12
            ppg = float(def_stats.get('def_ppg') or LEAGUE_AVG_DEF_PPG)
            raw = ppg / LEAGUE_AVG_DEF_PPG
            return max(0.88, min(1.12, raw))

        h_off = _off_idx(home_stats); a_off = _off_idx(away_stats)
        h_def = _def_idx(home_def);   a_def = _def_idx(away_def)

        # 70% matchup-driven, 30% anchored to BASE_TOTAL — prevents runaway
        # projections while still preserving matchup information
        raw_home = LEAGUE_AVG_PPG * h_off * a_def
        raw_away = LEAGUE_AVG_PPG * a_off * h_def
        anchor_home = LEAGUE_AVG_PPG
        anchor_away = LEAGUE_AVG_PPG
        home_pts = 0.7 * raw_home + 0.3 * anchor_home
        away_pts = 0.7 * raw_away + 0.3 * anchor_away

        total = home_pts + away_pts + venue_adj
        # HFA bump on home side only
        home_pts += HOME_FIELD_PTS * 0.5   # ~1.1 pts to home
        away_pts -= HOME_FIELD_PTS * 0.5
    else:
        # Legacy fallback: flat BASE_TOTAL + venue
        total = BASE_TOTAL + venue_adj
        fav_share = 0.50 + min(0.10, abs(power_diff) * 0.005)
        if power_diff >= 0:
            home_pts = total * fav_share
            away_pts = total * (1 - fav_share)
        else:
            away_pts = total * fav_share
            home_pts = total * (1 - fav_share)

    out.update({
        'power_diff': power_diff,
        'projected_spread': projected_spread,
        'projected_total': round(total, 2),
        'model_pred_home_points': round(home_pts, 1),
        'model_pred_away_points': round(away_pts, 1),
    })
    return out


def compute_confluence(home_stats: dict, away_stats: dict,
                       roof: str, temp: Optional[int], wind: Optional[int]) -> tuple:
    """Count signals leaning home vs away. Returns (net, breakdown_dict).
    Positive = home lean."""
    breakdown = {}

    # 1. Offensive EPA rating
    h_off = compute_off_rating(home_stats)
    a_off = compute_off_rating(away_stats)
    if h_off is not None and a_off is not None:
        if h_off - a_off >= 5:
            breakdown['off_epa'] = 'home'
        elif a_off - h_off >= 5:
            breakdown['off_epa'] = 'away'

    # 2. Defensive splash plays (sacks + INTs — signal for pressure defense)
    for stats, side in [(home_stats, 'home'), (away_stats, 'away')]:
        splash = (stats.get('def_sacks') or 0) + (stats.get('def_ints') or 0) * 1.5
        stats['_splash_score'] = splash
    if home_stats and away_stats:
        h_splash = home_stats.get('_splash_score') or 0
        a_splash = away_stats.get('_splash_score') or 0
        if h_splash - a_splash >= 10:
            breakdown['def_splash'] = 'home'
        elif a_splash - h_splash >= 10:
            breakdown['def_splash'] = 'away'

    # 3. CPOE — passing efficiency (home QB)
    h_cpoe = _f((home_stats or {}).get('pass_cpoe'))
    a_cpoe = _f((away_stats or {}).get('pass_cpoe'))
    if h_cpoe is not None and a_cpoe is not None:
        if h_cpoe - a_cpoe >= 2:
            breakdown['cpoe'] = 'home'
        elif a_cpoe - h_cpoe >= 2:
            breakdown['cpoe'] = 'away'

    # 4. Rush EPA (some teams win with ground game)
    h_re = _f((home_stats or {}).get('rush_epa'))
    a_re = _f((away_stats or {}).get('rush_epa'))
    if h_re is not None and a_re is not None:
        # normalize by games — otherwise late-season teams overweight
        hg = (home_stats or {}).get('games') or 1
        ag = (away_stats or {}).get('games') or 1
        h_rpg = h_re / hg
        a_rpg = a_re / ag
        if h_rpg - a_rpg >= 3:
            breakdown['rush_epa'] = 'home'
        elif a_rpg - h_rpg >= 3:
            breakdown['rush_epa'] = 'away'

    # 5. Home-field default (always +1 for home in NFL, cohort-audited)
    breakdown['hfa'] = 'home'

    h = sum(1 for v in breakdown.values() if v == 'home')
    a = sum(1 for v in breakdown.values() if v == 'away')
    return h - a, breakdown


def compute_cohort_tags(row: dict) -> list:
    """Mirror nfl_cohort_backfill.compute_cohorts_for_game but for pre-game."""
    tags = []
    home = row.get('home_team'); away = row.get('away_team')
    spread = _f(row.get('close_spread'))
    total = _f(row.get('close_total'))
    roof = (row.get('roof') or '').lower()
    temp = _i(row.get('temp'))
    wind = _i(row.get('wind'))

    if spread is not None and spread <= -7.0:
        tags.append('nfl_heavy_home_dog')
    if roof in ('outdoors', 'open') and (
        (temp is not None and temp <= 40) or (wind is not None and wind >= 12)
    ):
        tags.append('nfl_outdoor_under')
    if home and away and NFL_DIVISION.get(home) == NFL_DIVISION.get(away) and NFL_DIVISION.get(home):
        tags.append('nfl_div_home_cover')
    if roof in ('dome', 'closed') and total is not None and total >= 47:
        tags.append('nfl_dome_over')
    if spread is not None and spread > 0:
        tags.append('nfl_home_fav')
    return tags


def compute_sweat_score(proj_spread, close_spread, conf_net, proj_total, close_total) -> int:
    """Universal 0-100 sweat score. Matches MLB/NCAAB banding
    (PRIME 80+, STRONG 65+, LIGHT 50+, PASS <50)."""
    score = 45  # base
    if proj_spread is not None and close_spread is not None:
        edge = abs(proj_spread - close_spread)   # both nflverse convention, so subtract
        if edge >= 5.0: score += 25
        elif edge >= 3.5: score += 18
        elif edge >= 2.0: score += 12
        elif edge >= 1.0: score += 6

    abs_conf = abs(conf_net)
    if abs_conf >= 4: score += 15
    elif abs_conf >= 3: score += 10
    elif abs_conf >= 2: score += 5

    if proj_total is not None and close_total is not None:
        te = abs(proj_total - close_total)
        if te >= 6: score += 8
        elif te >= 4: score += 5
        elif te >= 2.5: score += 3

    return min(100, max(0, score))


def sweat_tier(score):
    if score >= 80: return 'PRIME'
    if score >= 65: return 'STRONG'
    if score >= 50: return 'LIGHT_LEAN'
    return 'PASS'


def compute_primary_play(ctx: dict) -> Optional[dict]:
    """Analog of ncaab_game_context.compute_primary_play. Spread/total/ML only.
    Tier gates align with audit (nfl_heavy_home_dog is the only pre-Week-1
    audit-validated PRIME cohort).

    Early-season discipline: when stats_source='prior_season_regressed',
    non-cohort plays cap at LEAN. The heavy_home_dog cohort is exempt
    because it's Vegas-driven, not EPA-driven (audit-validated on
    2022-2025 data using only close_spread + home/away).
    """
    stats_source = ctx.get('stats_source') or 'current'
    # Preseason = no primary_play at all. Starters play limited series;
    # historical hit rates on preseason picks are pure noise.
    if stats_source == 'preseason':
        return None
    stats_stale  = stats_source != 'current'
    conf = ctx.get('signal_confluence_net') or 0
    proj_spread = ctx.get('projected_spread')
    close_spread = ctx.get('close_spread')
    home_team = ctx.get('home_team') or 'Home'
    away_team = ctx.get('away_team') or 'Away'
    proj_total = ctx.get('projected_total')
    close_total = ctx.get('close_total')

    spread_edge = None
    if proj_spread is not None and close_spread is not None:
        spread_edge = round(float(proj_spread) - float(close_spread), 2)
    abs_edge = abs(spread_edge) if spread_edge is not None else 0.0
    fav = home_team if (proj_spread is not None and float(proj_spread) > 0) else away_team

    total_edge = None
    if proj_total is not None and close_total is not None:
        total_edge = round(float(proj_total) - float(close_total), 2)

    # 2026-08-09 Phase 2: Panel projection as second lens.
    # panel_pred_total from nfl_panel_projection (fantasy-aggregated).
    # If Panel + EPA-matchup agree on direction, we boost. If disagree, we
    # downgrade or fade. Same pattern as MLB Jerry + V4 + Panel.
    panel_total = ctx.get('panel_pred_total')
    panel_total_edge = None
    if panel_total is not None and close_total is not None:
        panel_total_edge = round(float(panel_total) - float(close_total), 2)
    panel_home_pts = ctx.get('panel_pred_home_pts')
    panel_away_pts = ctx.get('panel_pred_away_pts')
    panel_spread = None
    if panel_home_pts is not None and panel_away_pts is not None:
        # Positive = home projected to win by X (nflverse convention)
        panel_spread = round(float(panel_home_pts) - float(panel_away_pts), 2)

    tags = ctx.get('cohort_tags') or []

    # 2026-08-13 · MC lens integration (5-model coverage rollout).
    # Read the Monte Carlo simulator output written by nfl_mc_simulator.py.
    # Same shape as MLB's mc_probabilities so downstream lens-count logic
    # can treat MC as the fifth NFL lens.
    mc_probs = ctx.get('mc_probabilities') or {}
    mc_p_home = mc_probs.get('mc_p_home') if isinstance(mc_probs, dict) else None
    mc_expected_margin = mc_probs.get('mc_expected_margin') if isinstance(mc_probs, dict) else None
    mc_confidence_high = mc_probs.get('mc_confidence_high') if isinstance(mc_probs, dict) else False

    # 2026-08-13 · V3 lens (situational-regression). Written by
    # nfl_v3_regression.py; represents rest/weather/division adjustments
    # to the base Matchup-EPA projection.
    v3_spread = ctx.get('v3_spread')
    v3_total = ctx.get('v3_total')

    # 2026-08-13 · V4 lens (XGBoost). Trained on 2020-2024 nflverse, inference
    # via nfl_v4_inference.py. Represents the data-driven "everything else"
    # signal — learns interactions Matchup-EPA/V3/MC don't model directly.
    v4_spread = ctx.get('v4_spread')
    v4_total = ctx.get('v4_total')
    v4_confidence = ctx.get('v4_confidence')

    # 1a. MC HIGH-CONF headline — fires when MC sim shows a decisive edge
    # AND at least one other lens (EPA model or Panel) agrees on direction.
    # Analog of the MLB MC HIGH-CONF chip; NFL threshold set to 70% (vs
    # MLB's 80%) because NFL has more variance per game (16 vs 162 games
    # of season signal → wider prior).
    if mc_p_home is not None and mc_confidence_high:
        mc_side = 'HOME' if mc_p_home >= 0.5 else 'AWAY'
        mc_pct = mc_p_home if mc_side == 'HOME' else (1 - mc_p_home)
        if mc_pct >= 0.70:
            # 2026-08-13: with V3 + V4 both shipping tonight, NFL's spread-
            # producing lens count is now 5 (MC + Matchup-EPA + Panel + V3 + V4).
            # Jerry LLM is a 6th synthesis lens but doesn't produce a spread
            # projection directly; not counted in agreement.
            supporting = 1  # MC counts itself
            if proj_spread is not None:
                model_side = 'HOME' if float(proj_spread) > 0 else 'AWAY'
                if model_side == mc_side: supporting += 1
            if panel_spread is not None:
                panel_side = 'HOME' if panel_spread > 0 else 'AWAY'
                if panel_side == mc_side: supporting += 1
            if v3_spread is not None:
                v3_side = 'HOME' if float(v3_spread) > 0 else 'AWAY'
                if v3_side == mc_side: supporting += 1
            if v4_spread is not None:
                v4_side = 'HOME' if float(v4_spread) > 0 else 'AWAY'
                if v4_side == mc_side: supporting += 1
            # Only fire if AT LEAST ONE other lens agrees (2/5 minimum).
            # Lone-MC picks were 4-6 (40%) historical per MLB audit — same
            # caution applies here.
            if supporting >= 2:
                team = home_team if mc_side == 'HOME' else away_team
                # PRIME needs 4+ lens agreement (of 5 spread-producing lens),
                # STRONG at 2-3. Stale stats caps at STRONG regardless.
                tier = 'PRIME' if (supporting >= 4 and not stats_stale) else 'STRONG'
                return {
                    'type': 'ml',
                    'tier': tier,
                    'label': f'{team} ML',
                    'sub': f'MC HIGH-CONF: {mc_pct*100:.0f}% win prob (10k sim) · '
                           f'{supporting}/5 lens confirm · margin {mc_expected_margin:+.1f}',
                    'signal_floor': 85 if tier == 'PRIME' else 75,
                    'audit_note': 'MC HIGH-CONF chip · NFL 5-model rollout',
                }

        # 2026-08-13 · ANTI-CONSENSUS FADE (NFL) — analog of MLB's rule.
        # 2026-08-14 · GATED THROUGH Session C's LOG-ONLY discipline.
        # Registry mode='shadow' → runner.fire() logs but returns False
        # so the caller (this function) skips returning the primary_play,
        # letting the downstream cohort/edge branches take over.
        # After ~15 shadow fires with graded outcomes, backtest_rules.py
        # auto-promotes to active if 30d hit_rate beats baseline+3pp.
        def _side_of(spread_val: Optional[float]) -> Optional[str]:
            if spread_val is None: return None
            try: return 'H' if float(spread_val) > 0 else 'A'
            except (TypeError, ValueError): return None
        mc_side_char = 'H' if (mc_p_home or 0) >= 0.5 else 'A'
        lens_sides = {
            'mc':    mc_side_char,
            'epa':   _side_of(proj_spread),
            'panel': _side_of(panel_spread),
            'v3':    _side_of(v3_spread),
            'v4':    _side_of(v4_spread),
        }
        # Only run if ALL 5 lens produced a side
        if all(v is not None for v in lens_sides.values()):
            from collections import Counter
            side_counts = Counter(lens_sides.values())
            # Fires only when 2-3 pattern (not 1-4 or 4-1; needs meaningful split)
            if side_counts.most_common(1)[0][1] == 3:
                minority_side = side_counts.most_common()[-1][0]
                majority_side = side_counts.most_common(1)[0][0]
                mc_in_minority = lens_sides['mc'] == minority_side
                # Pattern: MC in minority (2 lens) with 3 dissenting = fade signal
                if mc_in_minority:
                    fade_team = home_team if majority_side == 'H' else away_team
                    proposal = {
                        'type': 'ml',
                        'tier': 'LEAN',
                        'label': f'{fade_team} ML',
                        'sub': f'Anti-consensus: 3 of 5 lens on {fade_team}, MC in minority. '
                               f'Historical MLB pattern ~68-74% inverse (NFL calibration pending).',
                        'signal_floor': 62,
                        'audit_note': 'NFL anti-consensus fade · rule_registry-gated',
                    }
                    # Rule registry gate — see LOG-ONLY discipline (Session C)
                    try:
                        from rule_registry import RuleRunner
                        runner = RuleRunner('NFL_ANTI_CONSENSUS_FADE')
                        game_id = ctx.get('game_id')
                        if runner.fire(
                            sport='NFL',
                            game_date=str(ctx.get('game_date')) if ctx.get('game_date') else None,
                            game_id=game_id,
                            target_table='nfl_game_context',
                            target_id=str(game_id) if game_id else 'unknown',
                            proposed_action=f'primary_play=LEAN {fade_team} ML',
                            before_state={'primary_play': None},
                            after_state={'primary_play_type': 'ml', 'tier': 'LEAN',
                                         'label': proposal['label']},
                            context={'lens_sides': lens_sides,
                                     'majority': majority_side,
                                     'minority': minority_side},
                        ):
                            return proposal
                        # shadow mode → log logged, don't return; fall through
                    except Exception:
                        # if the registry client fails, preserve pre-Session-C
                        # behavior (return proposal) — fail-open
                        return proposal

    # 1. HEAVY HOME DOG cohort PRIME override — 63.1% audit hit rate
    if 'nfl_heavy_home_dog' in tags:
        return {
            'type': 'spread',
            'tier': 'PRIME',
            'label': f'{home_team} +{abs(float(close_spread)):.1f}',
            'sub': f'nfl_heavy_home_dog cohort — 63.1% lifetime (n=65)',
            'signal_floor': 85,
        }

    # 2. STRONG spread — big model-vs-market gap + confluence agrees
    if abs_edge >= 3.5 and abs(conf) >= 2:
        tier = 'LEAN' if stats_stale else 'STRONG'
        floor = 60 if stats_stale else 72
        sub = f'Model {proj_spread:+.1f} vs market {close_spread:+.1f} (edge {abs_edge:.1f}, conf {conf:+d})'
        # 2026-08-09 Phase 2: Panel spread confirmation
        if panel_spread is not None:
            panel_side = 'HOME' if panel_spread > 0 else 'AWAY'
            model_side = 'HOME' if proj_spread > 0 else 'AWAY'
            if panel_side == model_side:
                # Panel agrees — boost tier one notch
                if tier == 'STRONG': tier = 'PRIME'
                elif tier == 'LEAN': tier = 'STRONG'
                floor += 8
                sub += f' · Panel confirms ({panel_spread:+.1f})'
            else:
                # Panel disagrees — downgrade
                if tier == 'STRONG': tier = 'LEAN'
                floor -= 12
                sub += f' · Panel disagrees ({panel_spread:+.1f}) — downgrade'
        if stats_stale:
            sub += ' · prior-season data, LEAN cap'
        return {
            'type': 'spread',
            'tier': tier,
            'label': f'{fav} spread {"lean" if stats_stale else "cover"}',
            'sub': sub,
            'signal_floor': floor,
        }

    # 3. STRONG total — model disagreement ≥ 4
    if total_edge is not None and abs(total_edge) >= 4.0:
        side = 'Over' if total_edge > 0 else 'Under'
        tier = 'LEAN' if stats_stale else 'STRONG'
        floor = 58 if stats_stale else 70
        sub = f'Model projects {proj_total:.1f} vs market {close_total} ({total_edge:+.1f})'
        # Panel total confirmation
        if panel_total_edge is not None:
            panel_side = 'Over' if panel_total_edge > 0 else 'Under'
            if panel_side == side:
                if tier == 'STRONG': tier = 'PRIME'
                elif tier == 'LEAN': tier = 'STRONG'
                floor += 8
                sub += f' · Panel confirms ({panel_total:.1f})'
            else:
                if tier == 'STRONG': tier = 'LEAN'
                floor -= 12
                sub += f' · Panel disagrees ({panel_total:.1f}) — downgrade'
        if stats_stale:
            sub += ' · prior-season data, LEAN cap'
        return {
            'type': 'total',
            'tier': tier,
            'label': f'{side} {close_total}',
            'sub': sub,
            'signal_floor': floor,
        }

    # 3b. Panel-only STRONG total: if EPA-matchup edge is small but Panel
    # disagrees loudly with market (>=4 pt gap), publish Panel LEAN.
    # This surfaces games where fantasy-aggregated projections see
    # something EPA doesn't. Only when Panel confidence >=0.6 (enough
    # players contributing).
    panel_conf = ctx.get('panel_confidence') or 0
    if (panel_total_edge is not None
            and abs(panel_total_edge) >= 4.0
            and panel_conf >= 0.6
            and not stats_stale):
        side = 'Over' if panel_total_edge > 0 else 'Under'
        return {
            'type': 'total',
            'tier': 'LEAN',
            'label': f'{side} {close_total}',
            'sub': f'Panel-driven ({panel_total:.1f} vs {close_total}, conf {panel_conf:.2f}) — EPA model quiet',
            'signal_floor': 58,
        }

    # 3c. ML lane (2026-08-09 Phase 2). Emits ML when:
    #   - Model + Panel both agree on same side by >= 3-pt spread margin
    #   - Model ML side is a live dog (+100 to +200 range) OR light-fav (-140 to +100)
    #   - Skips heavy fav (worse than -200) — per feedback_heavy_fav_ml_trap_803
    home_ml = ctx.get('close_home_ml') or ctx.get('home_ml')
    away_ml = ctx.get('close_away_ml') or ctx.get('away_ml')
    model_side = None
    if proj_spread is not None:
        # nflverse convention: positive = home fav, so home wins ML if proj_spread > 0
        model_side = 'HOME' if proj_spread > 0 else 'AWAY'
    panel_side = None
    if panel_spread is not None:
        panel_side = 'HOME' if panel_spread > 0 else 'AWAY'
    if (model_side and panel_side and model_side == panel_side
            and abs(proj_spread or 0) >= 3.0
            and home_ml and away_ml and not stats_stale):
        ml_price = home_ml if model_side == 'HOME' else away_ml
        try:
            price = float(ml_price)
        except (TypeError, ValueError):
            price = None
        if price is not None and -200 <= price <= 200:
            # Fair-price zone — publish ML
            team = home_team if model_side == 'HOME' else away_team
            tier = 'LEAN'
            floor = 58
            if abs(proj_spread) >= 5.0 and abs(conf) >= 2:
                tier = 'STRONG'; floor = 68
            return {
                'type': 'ml',
                'tier': tier,
                'label': f'{team} ML ({price:+.0f})',
                'sub': f'Model spread {proj_spread:+.1f} + Panel spread {panel_spread:+.1f} both {model_side} · fair price {price:+.0f}',
                'signal_floor': floor,
            }
        elif price is not None and price < -200:
            # Heavy-fav trap zone — skip ML, no play
            pass

    # 4. LIGHT spread lean — but cap at LEAN when stale (case 2/3 already did;
    # otherwise a weaker signal could sneak into lock_of_week while the
    # stronger STRONG-tier signals got capped).
    if abs_edge >= 2.0:
        tier = 'LEAN' if stats_stale else 'LIGHT'
        return {
            'type': 'spread',
            'tier': tier,
            'label': f'{fav} spread lean',
            'sub': f'Edge {abs_edge:.1f}' + (' · prior-season data, LEAN cap' if stats_stale else ''),
            'signal_floor': 60,
        }

    return None


# ─── Odds API + row build ────────────────────────────────────────────────

def fetch_odds_events(sport_key: str = 'americanfootball_nfl') -> list:
    if not ODDS_KEY: return []
    url = (f'{ODDS_API_BASE}/{sport_key}/odds/'
           f'?apiKey={ODDS_KEY}&regions=us&markets=spreads,totals,h2h&oddsFormat=american')
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ Odds API {sport_key}: {r.status_code}')
        return []
    return r.json()


def _pick_book(event: dict, market_key: str) -> dict:
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] == market_key:
                return {'book': book['title'], 'outcomes': m['outcomes']}
    return {}


def build_row(event: dict, aliases: dict, team_stats: dict, stats_source: str = 'current',
               team_def_stats: Optional[dict] = None) -> Optional[dict]:
    home_raw = event.get('home_team'); away_raw = event.get('away_team')
    home = aliases.get(home_raw); away = aliases.get(away_raw)
    if not home or not away:
        return None

    commence = event.get('commence_time', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
    except Exception:
        dt = _et_now()
        game_date = dt.date().isoformat()

    # 2026-08-09: derive NFL week from kickoff date. NFL Week 1 begins the
    # first Thursday of September. Preseason weeks 1-3 typically start
    # in mid-August. We compute both so Panel lookup works.
    def _nfl_week(kickoff_dt, season):
        # 2026-09-06 fix — hardcode Week 1 opener Thursday per season.
        # Prior logic used "first Thursday of September" which was off by
        # a week for 2026 (opener actually Sept 10, not Sept 3 first-Thu).
        # NFL schedules the season opener based on TV/venue constraints,
        # not calendar arithmetic — hardcoding matches published schedule.
        # Add new seasons as they land; unknown seasons fall back to
        # the old first-Thursday-of-September heuristic.
        from datetime import date as _date
        _WEEK1_OPENER = {
            2022: _date(2022, 9, 8),
            2023: _date(2023, 9, 7),
            2024: _date(2024, 9, 5),
            2025: _date(2025, 9, 4),
            2026: _date(2026, 9, 10),   # SF @ LA (verified via ESPN scoreboard)
        }
        wk1_start = _WEEK1_OPENER.get(season)
        if wk1_start is None:
            sept1 = _date(season, 9, 1)
            first_thu_offset = (3 - sept1.weekday()) % 7  # 3 = Thursday
            wk1_start = _date(season, 9, 1 + first_thu_offset)
        days_since_wk1 = (kickoff_dt.date() - wk1_start).days
        if days_since_wk1 < 0:
            # Preseason — approximate weeks (Aug is preseason wk 1-3)
            aug_days = (kickoff_dt.date() - _date(season, 8, 1)).days
            if aug_days < 0: return None
            return max(1, aug_days // 7 + 1)
        return max(1, min(18, days_since_wk1 // 7 + 1))

    row = {
        'game_id': event.get('id'),
        'game_date': game_date,
        'season': dt.year,
        'season_type': 'PRE' if stats_source == 'preseason' else 'REG',
        'week': _nfl_week(dt, dt.year),
        'home_team': home,
        'away_team': away,
        'kickoff_utc': commence,
        'div_game': NFL_DIVISION.get(home) == NFL_DIVISION.get(away) and bool(NFL_DIVISION.get(home)),
    }

    # Odds API spreads (native negative-fav) → nflverse convention (positive-fav)
    sp = _pick_book(event, 'spreads')
    for o in sp.get('outcomes') or []:
        if o['name'] == home_raw:
            pt = _f(o.get('point'))
            row['close_spread'] = -pt if pt is not None else None
        # Away spread price not in nfl_game_context schema; captured
        # in nfl_odds_pull → nfl_game_results if we ever need it.

    tot = _pick_book(event, 'totals')
    for o in tot.get('outcomes') or []:
        if o['name'] == 'Over':
            row['close_total'] = _f(o.get('point'))

    ml = _pick_book(event, 'h2h')
    for o in ml.get('outcomes') or []:
        if o['name'] == home_raw:
            row['close_home_ml'] = _i(o.get('price'))
        elif o['name'] == away_raw:
            row['close_away_ml'] = _i(o.get('price'))

    # Mirror close → open on first pull
    row.setdefault('open_spread', row.get('close_spread'))
    row.setdefault('open_total', row.get('close_total'))

    # Model
    home_stats = team_stats.get(home) or {}
    away_stats = team_stats.get(away) or {}
    # 2026-08-09 Phase 2: pull defense stats for matchup-adjusted projection
    tds = team_def_stats or {}
    home_def = tds.get(home) or {}
    away_def = tds.get(away) or {}
    proj = compute_projections(
        home_stats, away_stats,
        roof=row.get('roof'), temp=row.get('temp'), wind=row.get('wind'),
        home_def=home_def, away_def=away_def,
    )
    row.update(proj)

    # 2026-08-09 Phase 2: Panel projection (aggregate fantasy → team pts).
    # Independent of EPA model_pred — treated as second lens for confluence.
    # Silently no-ops if projections table empty / player projections not
    # yet pulled for this season+week. season+week come from the event.
    try:
        from nfl_panel_projection import compute_panel_projection
        # Derive week from kickoff_utc or fall back to row's week
        season = row.get('season')
        week = row.get('week')
        if season and week:
            panel = compute_panel_projection(
                home, away, int(season), int(week),
                season_type='reg' if row.get('season_type','REG').lower() in ('reg','regular') else 'pre',
                is_division_game=bool(row.get('div_game')),
            )
            if not panel.get('error'):
                row['panel_pred_home_pts'] = panel.get('home_pts')
                row['panel_pred_away_pts'] = panel.get('away_pts')
                row['panel_pred_total'] = panel.get('total')
                row['panel_confidence'] = panel.get('confidence')
                row['panel_source'] = 'sleeper'
                row['panel_players_used'] = (panel.get('home_players_used', 0)
                                              + panel.get('away_players_used', 0))
                row['panel_injury_outs'] = (panel.get('home_injury_outs', 0)
                                              + panel.get('away_injury_outs', 0))
    except Exception as e:
        # Never block context build on Panel failure
        print(f'  ⚠ panel projection failed for {away}@{home}: {e}')

    conf_net, breakdown = compute_confluence(
        home_stats, away_stats,
        row.get('roof'), row.get('temp'), row.get('wind'),
    )
    row['signal_confluence_net'] = conf_net
    row['signal_confluence_breakdown'] = breakdown

    # 2026-08-28: expose per-team defense stats on the row so downstream
    # signals (nfl_def_matchup_pass_top, _bottom, _rush_top, _ppg_extreme_low)
    # can read ctx.home_def_pass_ypg / ctx.away_def_rush_ypg / etc.
    # home_def / away_def already loaded above for compute_projections.
    row['home_def_ppg']              = home_def.get('def_ppg')
    row['home_def_ypg']              = home_def.get('def_ypg')
    row['home_def_pass_ypg']         = home_def.get('def_pass_ypg')
    row['home_def_rush_ypg']         = home_def.get('def_rush_ypg')
    row['home_def_pass_epa_allowed'] = home_def.get('def_pass_epa_allowed')
    row['home_def_rush_epa_allowed'] = home_def.get('def_rush_epa_allowed')
    row['away_def_ppg']              = away_def.get('def_ppg')
    row['away_def_ypg']              = away_def.get('def_ypg')
    row['away_def_pass_ypg']         = away_def.get('def_pass_ypg')
    row['away_def_rush_ypg']         = away_def.get('def_rush_ypg')
    row['away_def_pass_epa_allowed'] = away_def.get('def_pass_epa_allowed')
    row['away_def_rush_epa_allowed'] = away_def.get('def_rush_epa_allowed')

    # 2026-08-28: expose full offense + defense + discipline stats on
    # the row for the game-detail team-stats section. All derived from
    # already-populated nfl_team_stats (nflverse pull). Per-game math.
    def _per_game(stats: dict, field: str):
        n = stats.get('games') or 0
        v = stats.get(field)
        if v is None or not n: return None
        try: return round(float(v) / float(n), 2)
        except (TypeError, ValueError): return None
    # Penalty tendencies
    row['home_penalties_pg']       = _per_game(home_stats, 'penalties')
    row['home_penalty_yds_pg']     = _per_game(home_stats, 'penalty_yards')
    row['away_penalties_pg']       = _per_game(away_stats, 'penalties')
    row['away_penalty_yds_pg']     = _per_game(away_stats, 'penalty_yards')
    # Offense passing volume + efficiency
    row['home_pass_yds_pg']        = _per_game(home_stats, 'pass_yards')
    row['home_pass_tds_pg']        = _per_game(home_stats, 'pass_tds')
    row['home_pass_ints_pg']       = _per_game(home_stats, 'pass_ints')
    row['home_pass_cpoe']          = home_stats.get('pass_cpoe')
    row['home_pass_epa_pg']        = _per_game(home_stats, 'pass_epa')
    row['away_pass_yds_pg']        = _per_game(away_stats, 'pass_yards')
    row['away_pass_tds_pg']        = _per_game(away_stats, 'pass_tds')
    row['away_pass_ints_pg']       = _per_game(away_stats, 'pass_ints')
    row['away_pass_cpoe']          = away_stats.get('pass_cpoe')
    row['away_pass_epa_pg']        = _per_game(away_stats, 'pass_epa')
    # Offense rushing volume + efficiency
    row['home_rush_yds_pg']        = _per_game(home_stats, 'rush_yards')
    row['home_rush_tds_pg']        = _per_game(home_stats, 'rush_tds')
    row['home_rush_epa_pg']        = _per_game(home_stats, 'rush_epa')
    row['home_rush_first_downs_pg'] = _per_game(home_stats, 'rush_first_downs')
    row['away_rush_yds_pg']        = _per_game(away_stats, 'rush_yards')
    row['away_rush_tds_pg']        = _per_game(away_stats, 'rush_tds')
    row['away_rush_epa_pg']        = _per_game(away_stats, 'rush_epa')
    row['away_rush_first_downs_pg'] = _per_game(away_stats, 'rush_first_downs')
    # Defense event counts per game (from own-team nfl_team_stats def_* fields)
    row['home_def_sacks_pg']       = _per_game(home_stats, 'def_sacks')
    row['home_def_ints_pg']        = _per_game(home_stats, 'def_ints')
    row['home_def_fumbles_pg']     = _per_game(home_stats, 'def_fumbles_forced')
    row['home_def_tds_pg']         = _per_game(home_stats, 'def_tds')
    row['away_def_sacks_pg']       = _per_game(away_stats, 'def_sacks')
    row['away_def_ints_pg']        = _per_game(away_stats, 'def_ints')
    row['away_def_fumbles_pg']     = _per_game(away_stats, 'def_fumbles_forced')
    row['away_def_tds_pg']         = _per_game(away_stats, 'def_tds')
    # QB pressure allowed (own O-line)
    row['home_sacks_suffered_pg']  = _per_game(home_stats, 'sacks_suffered')
    row['away_sacks_suffered_pg']  = _per_game(away_stats, 'sacks_suffered')

    # 2026-08-31: Casual-friendly team-stats summary for game-card render.
    # Aggregates the flat per-game fields above into a compact JSONB with
    # league ranks — so app can render "Iowa State averages 189 rush yds/g
    # (14th)" without hitting nfl_team_stats a second time. team_stats +
    # team_def_stats already in scope; ranks computed across all teams
    # once and cached on the closure so we don't re-sort per game.
    row['home_team_stats_summary'] = _build_team_summary(
        home, home_stats, home_def, team_stats, tds,
    )
    row['away_team_stats_summary'] = _build_team_summary(
        away, away_stats, away_def, team_stats, tds,
    )

    # 2026-08-21: Join qb_vs_team stats from nfl_qb_vs_team backfill (1425 rows
    # 2021-2025 seasons). Powers nfl_qb_owns_defense_career + related signals.
    # Fields nullable if no career data or migration not yet applied.
    try:
        home_qb = get_qb_vs_team_stats(home, away)
        if home_qb:
            row['home_qb_id'] = home_qb.get('qb_id')
            row['home_qb_name'] = home_qb.get('qb_name')
            row['home_qb_vs_team_career_starts'] = home_qb.get('career_starts')
            row['home_qb_vs_team_career_qb_rating'] = home_qb.get('career_qb_rating')
            row['home_qb_vs_team_career_yds_per_att'] = home_qb.get('career_yds_per_att')
            row['home_qb_vs_team_career_cmp_pct'] = home_qb.get('career_cmp_pct')
            row['home_qb_vs_team_career_td_int_ratio'] = home_qb.get('career_td_int_ratio')
            row['home_qb_vs_team_recent_n_starts'] = home_qb.get('recent_n_starts')
            row['home_qb_vs_team_recent_pass_yds_avg'] = home_qb.get('recent_pass_yds_avg')
            row['home_qb_vs_team_recent_pass_td_avg'] = home_qb.get('recent_pass_td_avg')
            row['home_qb_vs_team_recent_int_avg'] = home_qb.get('recent_int_avg')
            row['home_qb_vs_team_recent_qb_rating'] = home_qb.get('recent_qb_rating')
        away_qb = get_qb_vs_team_stats(away, home)
        if away_qb:
            row['away_qb_id'] = away_qb.get('qb_id')
            row['away_qb_name'] = away_qb.get('qb_name')
            row['away_qb_vs_team_career_starts'] = away_qb.get('career_starts')
            row['away_qb_vs_team_career_qb_rating'] = away_qb.get('career_qb_rating')
            row['away_qb_vs_team_career_yds_per_att'] = away_qb.get('career_yds_per_att')
            row['away_qb_vs_team_career_cmp_pct'] = away_qb.get('career_cmp_pct')
            row['away_qb_vs_team_career_td_int_ratio'] = away_qb.get('career_td_int_ratio')
            row['away_qb_vs_team_recent_n_starts'] = away_qb.get('recent_n_starts')
            row['away_qb_vs_team_recent_pass_yds_avg'] = away_qb.get('recent_pass_yds_avg')
            row['away_qb_vs_team_recent_pass_td_avg'] = away_qb.get('recent_pass_td_avg')
            row['away_qb_vs_team_recent_int_avg'] = away_qb.get('recent_int_avg')
            row['away_qb_vs_team_recent_qb_rating'] = away_qb.get('recent_qb_rating')
    except Exception as e:
        print(f'  ⚠ QB vs team join failed for {away} @ {home}: {e}')

    row['cohort_tags'] = compute_cohort_tags(row)
    row['stats_source'] = stats_source

    score = compute_sweat_score(
        row.get('projected_spread'), row.get('close_spread'), conf_net,
        row.get('projected_total'), row.get('close_total'),
    )
    # 2026-08-14 PRESEASON DISCIPLINE: primary_play is already gated to
    # None for preseason (compute_primary_play line 446), but the sweat
    # SCORE + TIER were still being emitted using the normal formula.
    # Result: PRIME 90 badges on preseason games with no actionable play,
    # which is confusing UX. Cap preseason at LIGHT_LEAN tier + 60 score
    # to match the "read but don't act" discipline this phase requires.
    if stats_source == 'preseason':
        score = min(score, 60)
    row['sweat_score'] = score
    row['sweat_tier'] = sweat_tier(score)

    # 2026-08-16 CUTOVER: ensemble_scorer v2 authority (per-sport, NFL).
    # Same pattern as mlb_game_context — ensemble first, legacy fallback.
    ensemble_pp = None
    try:
        from ensemble_scorer import score_game as _ensemble_score
        from game_context import _compose_ensemble_sub
        from defensive_gates import apply_all_defensive_gates, reroute_ml_if_trapped
        decision = _ensemble_score('NFL', row)
        if decision is not None:
            # 2026-09-01: juice-trap ML reroute BEFORE picking top. Same
            # pattern as NCAAF (ncaaf_game_context.py). NFL Week 1 has
            # heavy chalk games (BAL -14 → ML -700+) that never should
            # ship as ML picks — swap to spread/total when either has a
            # real score. Threshold: -300 heavy_fav, +400 long_dog per
            # _JUICE_TRAP_*_BY_SPORT['NFL'].
            decision = reroute_ml_if_trapped(decision, row, sport='NFL')
            top = decision.top()
            if top.pick is not None:
                # 2026-08-31: recommended_stake for unified sizing across sports.
                # See game_context.compute_recommended_stake for rule.
                from game_context import compute_recommended_stake as _rs
                _rec_stake = _rs(top, mc_dissented=False)
                _reroute = getattr(top, '_ml_reroute', None)
                _audit = (f'ensemble_scorer v2 · NFL · {len(top.contributions)} sources · '
                          f'score={top.score:.2f} margin={top.margin:+.2f}')
                if _reroute:
                    _audit += f' · ML-reroute({_reroute["orig_market"]}→{top.market} @ {_reroute["orig_ml_price"]})'
                ensemble_pp = {
                    'type': top.market,
                    'tier': top.tier,
                    'label': top.display_label,
                    'side': top.side,
                    'line': top.line,
                    'conviction': top.conviction,
                    'score': round(top.score, 2),
                    'sub': _compose_ensemble_sub(top),
                    'recommended_stake': _rec_stake,
                    'audit_note': _audit,
                    '_engine': 'ensemble_v2',
                    '_ensemble_sources': [
                        {'signal_key': c.signal_key, 'class': c.signal_class,
                         'side': c.side, 'weight': round(c.weight, 2),
                         'n': c.n, 'contribution': round(c.contribution, 2),
                         'hit_rate': (round(c.hit_rate, 3) if c.hit_rate is not None else None),
                         'prose': c.display_prose}
                        for c in top.contributions[:8]
                    ],
                    '_ensemble_all_markets': {
                        'ml':    {'pick': decision.ml.pick, 'label': decision.ml.display_label,
                                  'tier': decision.ml.tier, 'conviction': decision.ml.conviction},
                        'rl':    {'pick': decision.rl.pick, 'label': decision.rl.display_label,
                                  'tier': decision.rl.tier, 'conviction': decision.rl.conviction},
                        'total': {'pick': decision.total.pick, 'label': decision.total.display_label,
                                  'tier': decision.total.tier, 'conviction': decision.total.conviction},
                    },
                }
                if _reroute:
                    ensemble_pp['_ml_reroute'] = _reroute
                # 2026-09-01: apply defensive gates (OC flip → MC dissent →
                # juice-trap demote → publish gate). Juice-trap floor is
                # -300 for NFL. Reroute already handled ML→spread swap;
                # gate catches leftover cases where reroute couldn't find
                # a clearing alt and ML still shipped.
                ensemble_pp = apply_all_defensive_gates(ensemble_pp, row, sport='NFL')
    except Exception:
        pass

    if ensemble_pp is not None:
        row['primary_play'] = ensemble_pp
    else:
        row['primary_play'] = compute_primary_play(row)
        if isinstance(row['primary_play'], dict):
            row['primary_play']['_engine'] = 'legacy_nfl_compute_primary_play'

    return row


def upsert_context(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            pp = r.get('primary_play') or {}
            print(f"  [DRY] {r['game_id'][:12]}... {r['away_team']} @ {r['home_team']}  "
                  f"sp={r.get('close_spread'):+.1f} proj={r.get('projected_spread'):+.1f}  "
                  f"conf={r.get('signal_confluence_net'):+d}  ss={r['sweat_score']} {r['sweat_tier']}"
                  + (f"  → {pp.get('tier')} {pp.get('label')}" if pp else ''))
        return len(rows)
    # 2026-08-28: normalize batch keys — PostgREST returns
    # PGRST102 "All object keys must match" when different rows in
    # the same batch have different key sets. This happens naturally
    # here: preseason rows have some enrichment fields regular-season
    # rows don't (or vice versa depending on ESPN/pull availability).
    # Union keys, fill missing with None.
    all_keys = set()
    for row in rows: all_keys.update(row.keys())
    for row in rows:
        for k in all_keys:
            if k not in row: row[k] = None
    r = requests.post(
        f'{SB}/rest/v1/nfl_game_context?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    # 2026-09-03 PGRST204 STRIP-RETRY: mirrors game_context.py MLB
    # pattern. If DB schema is missing a column we're writing (e.g.
    # away_def_fumbles_pg discovered 9/3 blocking ALL NFL writes),
    # parse the error, strip the column, retry. Loop up to 8x so a
    # migration lag doesn't kill the whole pipeline. Prior behavior:
    # single missing column silently zeroed the entire NFL context
    # slate, LR override never fired, users saw stale Week 1 picks.
    if r.status_code == 400:
        import re as _re
        stripped = []
        # 2026-09-03 raised from 8 → 60. Initial 8 was mirrored from
        # game_context.py MLB pattern but NFL has 44 missing columns
        # (schema catchup migration 20260903a pending user apply). 8
        # iterations left 36 columns still blocking. 60 gives headroom.
        for _attempt in range(60):
            try:
                err = r.json(); msg = err.get('message', '') if isinstance(err, dict) else ''
            except (ValueError, AttributeError):
                msg = r.text
            m = _re.search(r"'([a-z_0-9]+)'", msg)
            if not m or r.status_code != 400: break
            col = m.group(1)
            if not any(col in row for row in rows): break
            for row in rows:
                row.pop(col, None)
            stripped.append(col)
            r = requests.post(
                f'{SB}/rest/v1/nfl_game_context?on_conflict=game_id',
                headers=H_WRITE, json=rows, timeout=30,
            )
            if r.status_code in (200, 201, 204): break
        if stripped and r.status_code in (200, 201, 204):
            print(f"  ⚠ nfl_game_context: stripped missing cols ({', '.join(stripped[:5])}) — schema lag, apply pending migrations")
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    # 2026-08-23 Wave 1b (multi-sport): snapshot primary_play per successful
    # publish. Prior state: only MLB snapshotted (see mlb_pipeline/snapshot_
    # writer.py). NFL/NCAAF/NCAAB/NBA silently never populated the append-
    # only audit trail — as their seasons open, users would get zero snapshot
    # history for signal-impact audits. Best-effort — never blocks the upsert.
    try:
        from snapshot_writer import write_primary_play_snapshot
        for row in rows:
            write_primary_play_snapshot(SB, H_WRITE, 'NFL', row)
    except Exception:
        pass
    return len(rows)


def _enrich_nfl_rest_days(rows: list) -> None:
    """Populate ctx.home_rest / ctx.away_rest per game (days since last game).
    2026-08-22: closes 5 dead signals (nfl_rest_advantage_home, _away,
    nfl_home_rest_edge_2plus, _away, nfl_short_week_thursday_fade).
    Also sets ctx.dome / ctx.roof from event weather field (finding #18)."""
    from datetime import date, timedelta
    # Build team -> [game_date] map from THIS slate's rows + a lookback query
    # against nfl_game_results for prior weeks (last 30 days).
    team_dates: dict = {}
    for r in rows:
        d = r.get('game_date')
        if isinstance(d, str):
            try: d = date.fromisoformat(d)
            except ValueError: d = None
        if not d: continue
        for team in (r.get('home_team'), r.get('away_team')):
            if team:
                team_dates.setdefault(team, set()).add(d)

    # Pull last 30 days of NFL results for prior-game dates
    try:
        earliest = min((min(v) for v in team_dates.values() if v), default=None)
        if earliest:
            lookback = (earliest - timedelta(days=30)).isoformat()
            r = requests.get(f'{SB}/rest/v1/nfl_game_results', headers=SB_READ,
                             params={'game_date': f'gte.{lookback}',
                                     'select': 'game_date,home_team,away_team'}, timeout=15)
            if r.status_code == 200:
                for h in r.json() or []:
                    d = h.get('game_date')
                    if isinstance(d, str):
                        try: d = date.fromisoformat(d)
                        except ValueError: continue
                    for team in (h.get('home_team'), h.get('away_team')):
                        if team:
                            team_dates.setdefault(team, set()).add(d)
    except Exception:
        pass  # non-fatal — proceed with just current-slate dates

    for r in rows:
        d = r.get('game_date')
        if isinstance(d, str):
            try: d = date.fromisoformat(d)
            except ValueError: continue
        for prefix, team in (('home', r.get('home_team')),
                              ('away', r.get('away_team'))):
            if not team or not d: continue
            prior = sorted(x for x in team_dates.get(team, set()) if x < d)
            r[f'{prefix}_rest'] = (d - prior[-1]).days if prior else None
        # ctx.roof / ctx.dome — signal #18 reads ctx.roof; keep both
        # populated so old + new signals work.
        if r.get('dome') is True:
            r['roof'] = 'dome'


def run(dry_run: bool = False) -> None:
    print(f'=== NFL game context · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort')
        return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ nfl_team_aliases empty — run nfl_seed_aliases.py')
        return

    season = _et_now().year if _et_now().month >= 9 else _et_now().year - 1
    team_stats, stats_source = load_team_stats_with_fallback(season)
    if stats_source == 'prior_season_regressed':
        print(f'  ⚠ current season {season} thin — falling back to {season-1} regressed to mean (LEAN cap on non-cohort plays)')
    elif stats_source == 'none':
        print(f'  ⚠ neither {season} nor {season-1} has usable team stats — cohort/market signal only')
    print(f'  aliases={len(aliases)}  team_stats={len(team_stats)} teams  source={stats_source}')

    # 2026-08-09 Phase 2: also load defensive stats for matchup adjustment.
    team_def_stats = load_team_def_stats(season)
    print(f'  team_def_stats={len(team_def_stats)} teams')

    events = []
    for sk in ('americanfootball_nfl', 'americanfootball_nfl_preseason'):
        e = fetch_odds_events(sk)
        # Tag each event with its phase so build_row can flag preseason.
        # Preseason gets stats_source='preseason' — no primary_play emitted
        # (starters play limited series; picks are noise).
        phase = 'preseason' if 'preseason' in sk else 'regular'
        for ev in e:
            ev['_sweat_phase'] = phase
        events.extend(e)
    print(f'  Odds API events: {len(events)}')

    rows = []
    skipped = 0
    for e in events:
        row_source = 'preseason' if e.get('_sweat_phase') == 'preseason' else stats_source
        r = build_row(e, aliases, team_stats, stats_source=row_source, team_def_stats=team_def_stats)
        if r is None:
            skipped += 1
            continue
        rows.append(r)
    if skipped:
        print(f'  ⚠ skipped {skipped} events with unmapped teams')

    # 2026-08-22 REST-DAYS ENRICHMENT (silent-bug audit finding #17):
    # 5 signals were reading ctx.home_rest / ctx.away_rest / ctx.roof / etc.
    # that nfl_game_context never wrote. Adds home_rest / away_rest computed
    # from prior game_date per team + roof/dome tag (mirror of nhl pattern
    # in nhl_game_context.py:242-262). Non-fatal — historical query miss
    # leaves rest_days = None (existing behavior).
    try:
        _enrich_nfl_rest_days(rows)
    except Exception as _e:
        print(f'  ⚠ rest-days enrichment failed (non-fatal): {_e}')

    written = upsert_context(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_game_context')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    # 2026-08-22: season gate. Matches the same gate on generate_nfl_game_reads.py
    # (ee4b69e2). Was previously only guarded there but the context builder
    # was still running year-round — 9 rows from prior preseason runs sitting
    # in nfl_game_context confirm the drift. Off-season top-exit avoids Odds
    # API calls + player/stats pulls that produce zero-value rows.
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NFL')
    except ImportError:
        pass
    main()
