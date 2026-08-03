"""NFL player stat projection layer (Sprint 1 Day 2 · 2026-08-03).

Transparent multiplier-based projections for QB / WR / TE / RB player props.
100% free stack: nfl_data_py for game logs + PBP-derived defense metrics.

Approach mirrors MLB architecture:
  baseline = L5-game average for this stat
  final    = baseline × opp_D_mult × pace_mult × weather_mult × home_mult × injury_mult

Every multiplier is deterministic and traceable. Output includes an `inputs`
dict so downstream (Jerry synth, validator) can audit exactly how the number
was computed — matches the "no hallucinations" architecture from 2026-08-03.

Coverage this file:
  QB — pass_yds, pass_tds, ints, pass_attempts, rush_yds
  WR/TE — rec_yds, receptions (Day 4)
  RB — rush_yds, rush_attempts (Day 5)

Caches nfl_data_py pulls to disk (nfl_data_py handles its own caching too);
weekly data refresh cost is <10s once warm.

Usage:
    from project_nfl_stats import project_qb
    proj = project_qb('Patrick Mahomes', 'BUF', 'AWAY', {'wind_mph': 15, 'temp_f': 42})
    # proj = {'pass_yds': {'value': 257, 'inputs': {...}}, ...}
"""
from __future__ import annotations
import os, sys, warnings, functools, time
from typing import Optional
from datetime import datetime, timezone

warnings.filterwarnings('ignore')

# nfl_data_py is optional at import — projection functions raise if missing at call time
try:
    import nfl_data_py as nfl
    import pandas as pd
    NFL_AVAILABLE = True
except ImportError:
    NFL_AVAILABLE = False

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# ── Cache the season-long weekly pull so we don't refetch per player ──
@functools.lru_cache(maxsize=4)
def _weekly_cache(season: int) -> 'pd.DataFrame | None':
    """Returns the full weekly game log for a season (all positions).
    Returns None if the season's data isn't published yet (nfl_data_py 404).
    Callers must handle None (means: try previous season)."""
    if not NFL_AVAILABLE:
        raise RuntimeError('nfl_data_py not installed — cannot project')
    try:
        return nfl.import_weekly_data([season])
    except Exception as e:
        # Most common: HTTPError 404 for future/incomplete seasons
        return None


def _completed_seasons(now_year: int) -> list[int]:
    """Return list of most-recent COMPLETED seasons (newest first).

    NFL regular season completes late Jan/early Feb of following year.
    So during Aug-Dec 2026, 2025 season is still in progress → not yet
    published cleanly in nfl_data_py. Use 2024 + 2023 as baseline.
    """
    # Conservative: only count season Y as 'complete' after Feb Y+1
    # In Aug 2026, latest complete season is 2024.
    return [now_year - 2, now_year - 3]  # e.g. [2024, 2023]


@functools.lru_cache(maxsize=4)
def _team_defense_cache(season: int) -> dict:
    """Returns {team_abbr: {'pass_yds_allowed_per_gm': .., 'rush_yds_allowed_per_gm': .., 'rec_yds_allowed_per_gm': ..}}."""
    if not NFL_AVAILABLE: return {}
    w = _weekly_cache(season)
    # Aggregate: for each opponent_team, sum stats they allowed
    grp = w.groupby('opponent_team').agg(
        pass_yds_allowed=('passing_yards', 'sum'),
        rush_yds_allowed=('rushing_yards', 'sum'),
        rec_yds_allowed=('receiving_yards', 'sum'),
        games=('week', 'nunique'),
    ).reset_index()
    # Convert to per-game
    out = {}
    for _, row in grp.iterrows():
        games = max(row['games'], 1)
        out[row['opponent_team']] = {
            'pass_yds_allowed_per_gm': round(row['pass_yds_allowed'] / games, 1),
            'rush_yds_allowed_per_gm': round(row['rush_yds_allowed'] / games, 1),
            'rec_yds_allowed_per_gm': round(row['rec_yds_allowed'] / games, 1),
            'games': int(games),
        }
    return out


def _player_last_n_games(player_name: str, n: int = 5,
                          seasons: Optional[list] = None) -> 'pd.DataFrame | None':
    """Returns last N game rows for a player (most recent first).
    Searches provided seasons in reverse order until N games found."""
    if not NFL_AVAILABLE: return None
    if seasons is None:
        seasons = _completed_seasons(datetime.now(timezone.utc).year)

    rows = []
    for season in seasons:
        w = _weekly_cache(season)
        if w is None: continue
        m = w[w['player_display_name'] == player_name]
        if len(m):
            m = m.assign(_sort_key=m['season'] * 100 + m['week']).sort_values('_sort_key', ascending=False)
            rows.append(m)
        if sum(len(r) for r in rows) >= n:
            break
    if not rows: return None
    all_rows = pd.concat(rows).head(n)
    return all_rows if len(all_rows) else None


# ── Multipliers ──

def _opp_d_multiplier(opp_team: str, stat_key: str, season: Optional[int] = None) -> tuple[float, str]:
    """Returns (multiplier, note). Rank opp defense against this stat; tighter D → smaller multiplier."""
    if season is None:
        # Use latest completed season for defense baseline
        season = _completed_seasons(datetime.now(timezone.utc).year)[0]
    d_map = _team_defense_cache(season)
    if opp_team not in d_map:
        return 1.0, f'opp_D_data_missing_for_{opp_team}'

    # Rank against league (30ish teams)
    all_teams = sorted(d_map.items(), key=lambda x: x[1].get(stat_key, 0))
    ranks = {team: i + 1 for i, (team, _) in enumerate(all_teams)}
    rank = ranks.get(opp_team, 16)
    total = len(ranks)

    # Multiplier scale: top-5 D = 0.90, bottom-5 = 1.10, linear in between
    if rank <= 5:
        mult = 0.90; label = f'top-5 D vs {stat_key} (rank {rank})'
    elif rank <= 10:
        mult = 0.95; label = f'top-10 D vs {stat_key} (rank {rank})'
    elif rank >= total - 4:
        mult = 1.10; label = f'bottom-5 D vs {stat_key} (rank {rank})'
    elif rank >= total - 9:
        mult = 1.05; label = f'bottom-10 D vs {stat_key} (rank {rank})'
    else:
        mult = 1.00; label = f'mid-tier D vs {stat_key} (rank {rank})'
    return mult, label


def _weather_multiplier(stat_key: str, weather: dict) -> tuple[float, str]:
    """Weather adjustment for pass-heavy stats. Rush stats less sensitive."""
    if not weather: return 1.0, 'no_weather_data'
    wind = weather.get('wind_mph', 0) or 0
    temp = weather.get('temp_f', 60) or 60
    is_dome = weather.get('is_dome', False)

    if is_dome: return 1.0, 'dome (weather neutral)'

    if stat_key in ('pass_yds', 'pass_tds', 'rec_yds'):
        if wind >= 20:
            return 0.85, f'strong wind {wind}mph — passing suppressed'
        if wind >= 15:
            return 0.93, f'moderate wind {wind}mph'
        if temp <= 20:
            return 0.90, f'cold {temp}°F — passing suppressed'
        if temp <= 32:
            return 0.95, f'freezing {temp}°F'
    return 1.0, 'weather neutral'


def _pace_multiplier(opp_pace_plays_per_gm: Optional[float]) -> tuple[float, str]:
    """Faster opp pace → more plays → more stats. League avg ~62 plays/gm."""
    if not opp_pace_plays_per_gm: return 1.0, 'pace_neutral'
    if opp_pace_plays_per_gm >= 66:
        return 1.05, f'fast pace ({opp_pace_plays_per_gm:.0f} plays/gm)'
    if opp_pace_plays_per_gm <= 58:
        return 0.95, f'slow pace ({opp_pace_plays_per_gm:.0f} plays/gm)'
    return 1.0, f'avg pace ({opp_pace_plays_per_gm:.0f} plays/gm)'


def _injury_multiplier(injury_notes: Optional[dict]) -> tuple[float, str]:
    """Missing top receivers hurt QB projections. Missing OL hurts running.
    injury_notes format: {'top_wr_out': bool, 'ol_starters_out': int, ...}"""
    if not injury_notes: return 1.0, 'no_injury_context'
    mult = 1.0; notes = []
    if injury_notes.get('top_wr_out'):
        mult *= 0.92; notes.append('top WR out')
    if injury_notes.get('top_te_out'):
        mult *= 0.96; notes.append('top TE out')
    ol = injury_notes.get('ol_starters_out') or 0
    if ol >= 2:
        mult *= 0.94; notes.append(f'{ol} OL starters out')
    return round(mult, 3), (', '.join(notes) if notes else 'healthy')


# ── QB projection functions ──

def project_qb(player_name: str, opp_team: str, home_away: str = 'HOME',
                weather: Optional[dict] = None,
                injuries: Optional[dict] = None,
                opp_pace: Optional[float] = None) -> dict:
    """Project full QB stat line for a matchup. Returns dict keyed by prop family.

    Each value has shape {value, inputs}. Inputs is fully-transparent
    breakdown of the multiplier chain — feeds Jerry synth + audit validator.
    """
    l5 = _player_last_n_games(player_name, n=5)
    if l5 is None or len(l5) == 0:
        return {'error': f'no_game_log_for_{player_name}'}

    # Baselines from L5
    l5_pass_yds = float(l5['passing_yards'].mean())
    l5_pass_tds = float(l5['passing_tds'].mean())
    l5_ints = float(l5['interceptions'].mean())
    l5_attempts = float(l5['attempts'].mean())
    l5_rush_yds = float(l5['rushing_yards'].mean()) if 'rushing_yards' in l5.columns else 0.0
    n_games = len(l5)

    # Multipliers by stat family
    m_pass, m_pass_note = _opp_d_multiplier(opp_team, 'pass_yds_allowed_per_gm')
    m_rush, m_rush_note = _opp_d_multiplier(opp_team, 'rush_yds_allowed_per_gm')
    m_weather_pass, w_pass_note = _weather_multiplier('pass_yds', weather or {})
    m_weather_rush, w_rush_note = _weather_multiplier('rush_yds', weather or {})
    m_pace, pace_note = _pace_multiplier(opp_pace)
    m_home = 1.02 if home_away == 'HOME' else 0.98
    home_note = 'home boost' if home_away == 'HOME' else 'road penalty'
    m_inj, inj_note = _injury_multiplier(injuries)

    def _pack(baseline, family_mult, weather_mult):
        value = round(baseline * family_mult * weather_mult * m_pace * m_home * m_inj, 1)
        return {
            'value': value,
            'inputs': {
                'L5_avg': round(baseline, 1),
                'L5_games': n_games,
                'opp_D_mult': family_mult,
                'weather_mult': weather_mult,
                'pace_mult': m_pace,
                'home_mult': m_home,
                'injury_mult': m_inj,
                'notes': [pace_note, home_note, inj_note],
            }
        }

    return {
        'pass_yds': {**_pack(l5_pass_yds, m_pass, m_weather_pass),
                     'inputs': {**_pack(l5_pass_yds, m_pass, m_weather_pass)['inputs'],
                                'opp_D_note': m_pass_note, 'weather_note': w_pass_note}},
        'pass_tds': {**_pack(l5_pass_tds, m_pass, m_weather_pass),
                     'inputs': {**_pack(l5_pass_tds, m_pass, m_weather_pass)['inputs'],
                                'opp_D_note': m_pass_note, 'weather_note': w_pass_note}},
        'ints':     {**_pack(l5_ints, 1.0, 1.0),   # INTs less sensitive to defense strength
                     'inputs': {**_pack(l5_ints, 1.0, 1.0)['inputs'],
                                'opp_D_note': 'INTs use raw L5 baseline'}},
        'pass_attempts': {**_pack(l5_attempts, 1.0, m_weather_pass),
                          'inputs': {**_pack(l5_attempts, 1.0, m_weather_pass)['inputs'],
                                     'opp_D_note': 'attempts driven by game script not defense',
                                     'weather_note': w_pass_note}},
        'rush_yds': {**_pack(l5_rush_yds, m_rush, m_weather_rush),
                     'inputs': {**_pack(l5_rush_yds, m_rush, m_weather_rush)['inputs'],
                                'opp_D_note': m_rush_note, 'weather_note': w_rush_note}},
        'meta': {
            'player': player_name,
            'opp_team': opp_team,
            'home_away': home_away,
            'l5_games_used': n_games,
            'l5_seasons_pulled': list(sorted(l5['season'].unique().tolist(), reverse=True)),
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }
    }


# ── WR/TE projection functions (Day 4 → shipped Day 2 for velocity) ──

def project_receiver(player_name: str, opp_team: str, home_away: str = 'HOME',
                      weather: Optional[dict] = None,
                      injuries: Optional[dict] = None,
                      opp_pace: Optional[float] = None) -> dict:
    """Project WR/TE stat line: rec_yds, receptions, anytime_td (via TD rate).

    Anytime-TD projection uses L5 TD rate as probability, NOT a raw yardage
    multiplier — so the output value is a decimal probability the player
    scores at least one TD (usable directly for +price implied comparison).
    """
    l5 = _player_last_n_games(player_name, n=5)
    if l5 is None or len(l5) == 0:
        return {'error': f'no_game_log_for_{player_name}'}

    l5_rec_yds = float(l5['receiving_yards'].mean()) if 'receiving_yards' in l5.columns else 0.0
    l5_receptions = float(l5['receptions'].mean()) if 'receptions' in l5.columns else 0.0
    l5_targets = float(l5['targets'].mean()) if 'targets' in l5.columns else 0.0
    l5_rec_tds = float(l5['receiving_tds'].mean()) if 'receiving_tds' in l5.columns else 0.0
    l5_rush_tds = float(l5['rushing_tds'].mean()) if 'rushing_tds' in l5.columns else 0.0
    n_games = len(l5)

    # TD prob = 1 - P(no TD) where per-game rate ≈ L5 avg TDs per game
    # Poisson approximation: P(≥1 TD) = 1 - exp(-λ)
    import math
    combined_td_rate = l5_rec_tds + l5_rush_tds
    p_td = round(1.0 - math.exp(-combined_td_rate), 3) if combined_td_rate > 0 else 0.0

    m_rec, m_rec_note = _opp_d_multiplier(opp_team, 'rec_yds_allowed_per_gm')
    m_weather, w_note = _weather_multiplier('rec_yds', weather or {})
    m_pace, pace_note = _pace_multiplier(opp_pace)
    m_home = 1.02 if home_away == 'HOME' else 0.98
    home_note = 'home boost' if home_away == 'HOME' else 'road penalty'
    m_inj, inj_note = _injury_multiplier(injuries)

    def _pack_receiver(baseline, family_mult, weather_mult):
        value = round(baseline * family_mult * weather_mult * m_pace * m_home * m_inj, 1)
        return {'value': value,
                'inputs': {'L5_avg': round(baseline, 1), 'L5_games': n_games,
                           'opp_D_mult': family_mult, 'weather_mult': weather_mult,
                           'pace_mult': m_pace, 'home_mult': m_home, 'injury_mult': m_inj,
                           'notes': [pace_note, home_note, inj_note]}}

    return {
        'rec_yds': {**_pack_receiver(l5_rec_yds, m_rec, m_weather),
                    'inputs': {**_pack_receiver(l5_rec_yds, m_rec, m_weather)['inputs'],
                               'opp_D_note': m_rec_note, 'weather_note': w_note}},
        'receptions': {**_pack_receiver(l5_receptions, m_rec, m_weather),
                       'inputs': {**_pack_receiver(l5_receptions, m_rec, m_weather)['inputs'],
                                  'opp_D_note': m_rec_note, 'weather_note': w_note}},
        'targets': {**_pack_receiver(l5_targets, m_rec, 1.0),
                    'inputs': {**_pack_receiver(l5_targets, m_rec, 1.0)['inputs'],
                               'opp_D_note': m_rec_note}},
        'anytime_td_prob': {'value': p_td,
                             'inputs': {'L5_rec_tds_per_gm': round(l5_rec_tds, 3),
                                        'L5_rush_tds_per_gm': round(l5_rush_tds, 3),
                                        'combined_lambda': round(combined_td_rate, 3),
                                        'formula': '1 - exp(-lambda) (Poisson)'}},
        'meta': {'player': player_name, 'opp_team': opp_team, 'home_away': home_away,
                 'l5_games_used': n_games,
                 'l5_seasons_pulled': list(sorted(l5['season'].unique().tolist(), reverse=True)),
                 'generated_at': datetime.now(timezone.utc).isoformat()},
    }


# ── RB projection functions ──

def project_rb(player_name: str, opp_team: str, home_away: str = 'HOME',
                weather: Optional[dict] = None,
                injuries: Optional[dict] = None,
                opp_pace: Optional[float] = None) -> dict:
    """Project RB stat line: rush_yds, carries, anytime_td, rec_yds/receptions (pass-catching RBs)."""
    l5 = _player_last_n_games(player_name, n=5)
    if l5 is None or len(l5) == 0:
        return {'error': f'no_game_log_for_{player_name}'}

    l5_rush_yds = float(l5['rushing_yards'].mean()) if 'rushing_yards' in l5.columns else 0.0
    l5_carries = float(l5['carries'].mean()) if 'carries' in l5.columns else 0.0
    l5_rec_yds = float(l5['receiving_yards'].mean()) if 'receiving_yards' in l5.columns else 0.0
    l5_receptions = float(l5['receptions'].mean()) if 'receptions' in l5.columns else 0.0
    l5_rush_tds = float(l5['rushing_tds'].mean()) if 'rushing_tds' in l5.columns else 0.0
    l5_rec_tds = float(l5['receiving_tds'].mean()) if 'receiving_tds' in l5.columns else 0.0
    n_games = len(l5)

    import math
    combined_td_rate = l5_rush_tds + l5_rec_tds
    p_td = round(1.0 - math.exp(-combined_td_rate), 3) if combined_td_rate > 0 else 0.0

    m_rush, m_rush_note = _opp_d_multiplier(opp_team, 'rush_yds_allowed_per_gm')
    m_rec, m_rec_note = _opp_d_multiplier(opp_team, 'rec_yds_allowed_per_gm')
    m_weather_rush, w_rush_note = _weather_multiplier('rush_yds', weather or {})
    m_weather_rec, w_rec_note = _weather_multiplier('rec_yds', weather or {})
    m_pace, pace_note = _pace_multiplier(opp_pace)
    m_home = 1.02 if home_away == 'HOME' else 0.98
    home_note = 'home boost' if home_away == 'HOME' else 'road penalty'
    m_inj, inj_note = _injury_multiplier(injuries)

    def _pack_rb(baseline, family_mult, weather_mult):
        value = round(baseline * family_mult * weather_mult * m_pace * m_home * m_inj, 1)
        return {'value': value,
                'inputs': {'L5_avg': round(baseline, 1), 'L5_games': n_games,
                           'opp_D_mult': family_mult, 'weather_mult': weather_mult,
                           'pace_mult': m_pace, 'home_mult': m_home, 'injury_mult': m_inj,
                           'notes': [pace_note, home_note, inj_note]}}

    return {
        'rush_yds': {**_pack_rb(l5_rush_yds, m_rush, m_weather_rush),
                     'inputs': {**_pack_rb(l5_rush_yds, m_rush, m_weather_rush)['inputs'],
                                'opp_D_note': m_rush_note, 'weather_note': w_rush_note}},
        'carries': {**_pack_rb(l5_carries, 1.0, 1.0),
                    'inputs': {**_pack_rb(l5_carries, 1.0, 1.0)['inputs'],
                               'opp_D_note': 'carries volume driven by game script'}},
        'rec_yds': {**_pack_rb(l5_rec_yds, m_rec, m_weather_rec),
                    'inputs': {**_pack_rb(l5_rec_yds, m_rec, m_weather_rec)['inputs'],
                               'opp_D_note': m_rec_note, 'weather_note': w_rec_note}},
        'receptions': {**_pack_rb(l5_receptions, m_rec, m_weather_rec),
                       'inputs': {**_pack_rb(l5_receptions, m_rec, m_weather_rec)['inputs'],
                                  'opp_D_note': m_rec_note, 'weather_note': w_rec_note}},
        'anytime_td_prob': {'value': p_td,
                             'inputs': {'L5_rush_tds_per_gm': round(l5_rush_tds, 3),
                                        'L5_rec_tds_per_gm': round(l5_rec_tds, 3),
                                        'combined_lambda': round(combined_td_rate, 3),
                                        'formula': '1 - exp(-lambda) (Poisson)'}},
        'meta': {'player': player_name, 'opp_team': opp_team, 'home_away': home_away,
                 'l5_games_used': n_games,
                 'l5_seasons_pulled': list(sorted(l5['season'].unique().tolist(), reverse=True)),
                 'generated_at': datetime.now(timezone.utc).isoformat()},
    }


if __name__ == '__main__':
    # Smoke test: QB, WR, RB projections
    print('=== NFL projection smoke test ===')
    t0 = time.time()
    proj_qb = project_qb('Patrick Mahomes', 'BUF', home_away='AWAY',
                          weather={'wind_mph': 15, 'temp_f': 42, 'is_dome': False},
                          opp_pace=62)
    proj_wr = project_receiver('Jaxon Smith-Njigba', 'NE', home_away='HOME',
                                weather={'wind_mph': 8, 'temp_f': 68, 'is_dome': False})
    proj_rb = project_rb('Christian McCaffrey', 'ARI', home_away='HOME',
                          weather={'is_dome': False})
    dt = time.time() - t0
    print(f'\n3-player projection time: {dt*1000:.0f}ms')
    for name, proj in [('QB Mahomes @ BUF (15mph wind, cold)', proj_qb),
                        ('WR Smith-Njigba vs NE (home)', proj_wr),
                        ('RB McCaffrey vs ARI (home)', proj_rb)]:
        print(f'\n{name}')
        if 'error' in proj:
            print(f'  ERROR: {proj["error"]}'); continue
        for k, v in proj.items():
            if k == 'meta': continue
            if isinstance(v, dict) and 'value' in v:
                print(f'  {k:<18} → {v["value"]}  (L5={v["inputs"].get("L5_avg","?")}, opp={v["inputs"].get("opp_D_mult","?")})')

