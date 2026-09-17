"""Cross-sport automated pattern miner.

Discovers new (model x model x public split x team form) combinations that
show real edge on graded history, seeds them into signal_sources so the
ensemble picks them up on next cron.

Runs per-sport using a shared engine. Each sport plugs in:
  - ctx table name
  - results table name
  - list of extractor functions (feature name -> value / direction)
  - market grader (given actual score, close_line, close_spread, return
    which side won each market)

Filter: n>=15, |hit_rate - 0.5| >= 10pp. Auto-seed as signal_source when
threshold met. Refit_signal_registry grades later; ANTI_VALIDATED get
auto-faded per existing fade-flip mechanism.

CLI:
  python discover_patterns.py --sport MLB              # mine only MLB
  python discover_patterns.py --sport MLB --dry-run    # show findings, don't seed
  python discover_patterns.py --all                    # every sport
  python discover_patterns.py --sport MLB --days 90    # 90-day lookback

Rerun weekly (add to cron).
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path
from typing import Callable

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MIN_SAMPLE = 15
MIN_EDGE_PP = 10  # absolute distance from 50%


# ═══════════════════════════════════════════════════════════════════════
# SPORT PLUGINS
# ═══════════════════════════════════════════════════════════════════════
# Each sport provides:
#   ctx_table, results_table, ctx_select, extractors, market_grader,
#   markets (list of markets in play for this sport)

def _f(v):
    try: return float(v) if v is not None else None
    except (ValueError, TypeError): return None


def _mlb_extractors(g: dict) -> dict:
    """Extract normalized feature values from an MLB ctx row.
    Returns dict of feature_name -> str direction ('over'/'under'/'HOME'/'AWAY') or None."""
    out = {}
    ct = _f(g.get('close_total'))
    cs = _f(g.get('close_spread'))
    mc = g.get('mc_probabilities') or {}
    if not isinstance(mc, dict): mc = {}
    oc = g.get('oddscrowd_snapshot') or {}
    if not isinstance(oc, dict): oc = {}

    # Total-direction features
    if ct is not None:
        for name, key in (('jerry_total', 'jerry_pred_total'),
                          ('panel_total', 'panel_implied_total'),
                          ('proj_total', 'projected_total'),
                          ('model_total', 'model_pred_total')):
            v = _f(g.get(key))
            if v is not None and abs(v - ct) >= 0.5:
                out[name] = 'over' if v > ct else 'under'
        if mc.get('mc_mean_total') is not None:
            mc_t = _f(mc['mc_mean_total'])
            if mc_t is not None and abs(mc_t - ct) >= 0.5:
                out['mc_total'] = 'over' if mc_t > ct else 'under'
        # OC total money
        oc_t = oc.get('total') or {}
        if oc_t.get('pick'):
            oc_m = _f(oc_t.get('money')) or 0
            if oc_m >= 65:
                out['oc_total_heavy'] = str(oc_t.get('pick', '')).lower()

    # ML direction features
    for name, key in (('jerry_spread', 'jerry_pred_spread'),
                       ('proj_spread', 'projected_spread'),
                       ('model_spread', 'model_pred_spread')):
        v = _f(g.get(key))
        if v is not None and cs is not None:
            delta = v + cs
            if abs(delta) >= 0.5:
                out[name.replace('spread', 'ml')] = 'HOME' if delta > 0 else 'AWAY'
    if mc.get('mc_p_home_win') is not None:
        p = _f(mc['mc_p_home_win']) or 0
        if p >= 0.55: out['mc_ml'] = 'HOME'
        elif p <= 0.45: out['mc_ml'] = 'AWAY'
    oc_ml = oc.get('ml') or {}
    if oc_ml.get('pick'):
        m = _f(oc_ml.get('money')) or 0
        if m >= 65:
            out['oc_ml_heavy'] = str(oc_ml.get('pick', '')).upper()

    # Team form (split-adjusted)
    try:
        h_ou_o = _f(g.get('home_ou_l10_at_home_overs')) or 0
        h_ou_u = _f(g.get('home_ou_l10_at_home_unders')) or 0
        if h_ou_o + h_ou_u >= 5:
            h_u_pct = h_ou_u / (h_ou_o + h_ou_u)
            if h_u_pct >= 0.6: out['home_form_total'] = 'under'
            elif h_u_pct <= 0.4: out['home_form_total'] = 'over'
        a_ou_o = _f(g.get('away_ou_l10_on_road_overs')) or 0
        a_ou_u = _f(g.get('away_ou_l10_on_road_unders')) or 0
        if a_ou_o + a_ou_u >= 5:
            a_u_pct = a_ou_u / (a_ou_o + a_ou_u)
            if a_u_pct >= 0.6: out['away_form_total'] = 'under'
            elif a_u_pct <= 0.4: out['away_form_total'] = 'over'
    except (TypeError, ZeroDivisionError):
        pass

    return out


def _mlb_grade(res: dict, ct, cs) -> dict:
    """Return actual result per market."""
    hs, as_ = res['home_score'], res['away_score']
    grades = {}
    if ct is not None:
        t = hs + as_
        if t != ct:
            grades['total'] = 'over' if t > ct else 'under'
    if hs != as_:
        grades['ml'] = 'HOME' if hs > as_ else 'AWAY'
    if cs is not None:
        adj = (hs - as_) + cs
        if adj != 0:
            grades['rl'] = 'HOME' if adj > 0 else 'AWAY'
    return grades


def _football_extractors(g: dict, sport: str = None) -> dict:
    """Shared extractor for NFL + NCAAF (both have same column shape).
    Total-direction from projected/panel/sp_plus/model + ml direction from
    spread models. OU / ATS split-adj tendencies.

    2026-09-15 FIXED per [[project_close_spread_sign_bug_914]]:
    Per-field sign registry normalizes all spread-shaped fields to
    "home_margin" (positive = home wins by X). Prior code did `v + cs`
    which double-counted signs when both v and cs used the same
    convention (NFL projected_spread + NFL close_spread both positive =
    home fav → false HOME signal on every NFL game where they agreed).

    Normalization by field × sport:
      close_spread NFL:      +cs = home wins by cs                 → cs
      close_spread NCAAF:    -cs (negative=home fav flipped)       → -cs
      projected_spread NCAAF: +v  (already home margin)            → v
      projected_spread NFL:   +v  (positive=home fav = home margin)→ v
      sp_plus_pred_spread:    +v  (already home margin)            → v
      model_pred_home - away: +v  (already home margin)            → v

    Then: delta = model_home_margin - market_home_margin
      HOME lean iff delta > +0.5 (model thinks home wins by MORE)
      AWAY lean iff delta < -0.5

    Requires `sport` arg from caller (SPORT_CONFIG below now passes it).
    """
    out = {}
    ct = _f(g.get('close_total'))
    cs = _f(g.get('close_spread'))
    # Sport-aware home_margin from close_spread. NFL stores POSITIVE = home fav;
    # every other sport (MLB, NCAAF, NBA, NHL, NCAAB) stores NEGATIVE = home fav.
    if cs is None:
        market_hm = None
    elif sport == 'NFL':
        market_hm = cs
    else:
        market_hm = -cs
    mc = g.get('mc_probabilities') or {}
    if not isinstance(mc, dict): mc = {}
    oc = g.get('oddscrowd_snapshot') or {}
    if not isinstance(oc, dict): oc = {}

    if ct is not None:
        for name, key in (('proj_total', 'projected_total'),
                          ('panel_total', 'panel_pred_total'),   # NFL only, absent NCAAF
                          ('sp_plus_total', 'sp_plus_pred_total')):  # NCAAF only
            v = _f(g.get(key))
            if v is not None and abs(v - ct) >= 0.5:
                out[name] = 'over' if v > ct else 'under'
        # Model_pred_home + away can be summed for a total projection
        mph = _f(g.get('model_pred_home_points'))
        mpa = _f(g.get('model_pred_away_points'))
        if mph is not None and mpa is not None:
            mt = mph + mpa
            if abs(mt - ct) >= 0.5:
                out['model_total'] = 'over' if mt > ct else 'under'
        if mc.get('mc_mean_total') is not None:
            mt2 = _f(mc['mc_mean_total'])
            if mt2 is not None and abs(mt2 - ct) >= 0.5:
                out['mc_total'] = 'over' if mt2 > ct else 'under'
        oc_t = oc.get('total') or {}
        if oc_t.get('pick'):
            oc_m = _f(oc_t.get('money')) or 0
            if oc_m >= 65:
                out['oc_total_heavy'] = str(oc_t.get('pick', '')).lower()

    if market_hm is not None:
        # 2026-09-15 (per project_close_spread_sign_bug_914): normalized
        # deltas. Each of these model fields ALREADY stores home_margin
        # (positive = home wins by X). Compare against market_hm (also
        # normalized above). Positive delta → model thinks home wins by
        # MORE than market → HOME lean.
        for name, key in (('proj_ml', 'projected_spread'),
                          ('sp_plus_ml', 'sp_plus_pred_spread')):
            v = _f(g.get(key))
            if v is not None:
                delta = v - market_hm
                if abs(delta) >= 0.5:
                    out[name] = 'HOME' if delta > 0 else 'AWAY'
        # From model pred points — home_margin = mph - mpa, positive = home wins
        mph = _f(g.get('model_pred_home_points'))
        mpa = _f(g.get('model_pred_away_points'))
        if mph is not None and mpa is not None:
            model_margin = mph - mpa
            delta = model_margin - market_hm
            if abs(delta) >= 0.5:
                out['model_ml'] = 'HOME' if delta > 0 else 'AWAY'
        if mc.get('mc_p_home_win') is not None:
            p = _f(mc['mc_p_home_win']) or 0
            if p >= 0.55: out['mc_ml'] = 'HOME'
            elif p <= 0.45: out['mc_ml'] = 'AWAY'
    oc_ml = oc.get('ml') or {}
    if oc_ml.get('pick'):
        m = _f(oc_ml.get('money')) or 0
        if m >= 65:
            out['oc_ml_heavy'] = str(oc_ml.get('pick', '')).upper()

    # Team form (split-adjusted OU + ATS)
    try:
        h_o = _f(g.get('home_ou_l10_at_home_overs')) or 0
        h_u = _f(g.get('home_ou_l10_at_home_unders')) or 0
        if h_o + h_u >= 4:
            h_u_pct = h_u / (h_o + h_u)
            if h_u_pct >= 0.6: out['home_form_total'] = 'under'
            elif h_u_pct <= 0.4: out['home_form_total'] = 'over'
        a_o = _f(g.get('away_ou_l10_on_road_overs')) or 0
        a_u = _f(g.get('away_ou_l10_on_road_unders')) or 0
        if a_o + a_u >= 4:
            a_u_pct = a_u / (a_o + a_u)
            if a_u_pct >= 0.6: out['away_form_total'] = 'under'
            elif a_u_pct <= 0.4: out['away_form_total'] = 'over'
    except (TypeError, ZeroDivisionError):
        pass
    return out


def _football_grade(res, ct, cs):
    return _mlb_grade(res, ct, cs)   # same math (home_score + away_score)


def _basketball_extractors(g: dict) -> dict:
    """NBA / NCAAB — same shape as football but without panel/sp+, keeps
    projected_total/projected_spread + model_pred_*_points + mc + oc.
    """
    out = {}
    ct = _f(g.get('close_total'))
    cs = _f(g.get('close_spread'))
    mc = g.get('mc_probabilities') or {}
    if not isinstance(mc, dict): mc = {}
    oc = g.get('oddscrowd_snapshot') or {}
    if not isinstance(oc, dict): oc = {}

    if ct is not None:
        v = _f(g.get('projected_total'))
        if v is not None and abs(v - ct) >= 0.5:
            out['proj_total'] = 'over' if v > ct else 'under'
        mph = _f(g.get('model_pred_home_points'))
        mpa = _f(g.get('model_pred_away_points'))
        if mph is not None and mpa is not None:
            mt = mph + mpa
            if abs(mt - ct) >= 0.5:
                out['model_total'] = 'over' if mt > ct else 'under'
        if mc.get('mc_mean_total') is not None:
            m2 = _f(mc['mc_mean_total'])
            if m2 is not None and abs(m2 - ct) >= 0.5:
                out['mc_total'] = 'over' if m2 > ct else 'under'
        oc_t = oc.get('total') or {}
        if oc_t.get('pick'):
            m = _f(oc_t.get('money')) or 0
            if m >= 65:
                out['oc_total_heavy'] = str(oc_t.get('pick', '')).lower()

    if cs is not None:
        v = _f(g.get('projected_spread'))
        if v is not None:
            delta = v + cs
            if abs(delta) >= 0.5:
                out['proj_ml'] = 'HOME' if delta > 0 else 'AWAY'
        mph = _f(g.get('model_pred_home_points'))
        mpa = _f(g.get('model_pred_away_points'))
        if mph is not None and mpa is not None:
            model_margin = mph - mpa
            delta = model_margin + cs
            if abs(delta) >= 0.5:
                out['model_ml'] = 'HOME' if delta > 0 else 'AWAY'
        if mc.get('mc_p_home_win') is not None:
            p = _f(mc['mc_p_home_win']) or 0
            if p >= 0.55: out['mc_ml'] = 'HOME'
            elif p <= 0.45: out['mc_ml'] = 'AWAY'
    oc_ml = oc.get('ml') or {}
    if oc_ml.get('pick'):
        m = _f(oc_ml.get('money')) or 0
        if m >= 65:
            out['oc_ml_heavy'] = str(oc_ml.get('pick', '')).upper()
    return out


def _hockey_extractors(g: dict) -> dict:
    """NHL — no total_points field on results, so total market omitted for grading.
    ML + puck line (RL equivalent) supported."""
    out = {}
    cs = _f(g.get('close_spread'))
    mc = g.get('mc_probabilities') or {}
    if not isinstance(mc, dict): mc = {}
    if cs is not None:
        v = _f(g.get('projected_spread'))
        if v is not None:
            delta = v + cs
            if abs(delta) >= 0.5:
                out['proj_ml'] = 'HOME' if delta > 0 else 'AWAY'
        if mc.get('mc_p_home_win') is not None:
            p = _f(mc['mc_p_home_win']) or 0
            if p >= 0.55: out['mc_ml'] = 'HOME'
            elif p <= 0.45: out['mc_ml'] = 'AWAY'
    return out


SPORT_PLUGINS = {
    'MLB': {
        'ctx_table': 'mlb_game_context',
        'results_table': 'mlb_game_results',
        'ctx_select': ('game_id,close_total,close_spread,'
                       'jerry_pred_total,panel_implied_total,projected_total,model_pred_total,'
                       'jerry_pred_spread,projected_spread,model_pred_spread,'
                       'mc_probabilities,oddscrowd_snapshot,'
                       'home_ou_l10_at_home_overs,home_ou_l10_at_home_unders,'
                       'away_ou_l10_on_road_overs,away_ou_l10_on_road_unders'),
        'extractors': _mlb_extractors,
        'grade': _mlb_grade,
        'markets': ['total', 'ml', 'rl'],
        'market_features': {
            'total': ['jerry_total', 'panel_total', 'proj_total', 'model_total',
                      'mc_total', 'oc_total_heavy', 'home_form_total', 'away_form_total'],
            'ml': ['jerry_ml', 'proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
            'rl': ['jerry_ml', 'proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
        },
    },
    'NFL': {
        'ctx_table': 'nfl_game_context',
        'results_table': 'nfl_game_results',
        'ctx_select': ('game_id,close_total,close_spread,'
                       'projected_total,panel_pred_total,'
                       'projected_spread,'
                       'model_pred_home_points,model_pred_away_points,'
                       'mc_probabilities,oddscrowd_snapshot,'
                       'home_ou_l10_at_home_overs,home_ou_l10_at_home_unders,'
                       'away_ou_l10_on_road_overs,away_ou_l10_on_road_unders'),
        'extractors': _football_extractors,
        'grade': _football_grade,
        'markets': ['total', 'ml', 'rl'],
        'market_features': {
            'total': ['proj_total', 'panel_total', 'model_total', 'mc_total',
                      'oc_total_heavy', 'home_form_total', 'away_form_total'],
            'ml': ['proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
            'rl': ['proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
        },
    },
    'NCAAF': {
        'ctx_table': 'ncaaf_game_context',
        'results_table': 'ncaaf_game_results',
        'ctx_select': ('game_id,close_total,close_spread,'
                       'projected_total,sp_plus_pred_total,'
                       'projected_spread,sp_plus_pred_spread,'
                       'model_pred_home_points,model_pred_away_points,'
                       'mc_probabilities,oddscrowd_snapshot,'
                       'home_ou_l10_at_home_overs,home_ou_l10_at_home_unders,'
                       'away_ou_l10_on_road_overs,away_ou_l10_on_road_unders'),
        'extractors': _football_extractors,
        'grade': _football_grade,
        'markets': ['total', 'ml', 'rl'],
        'market_features': {
            'total': ['proj_total', 'sp_plus_total', 'model_total', 'mc_total',
                      'oc_total_heavy', 'home_form_total', 'away_form_total'],
            'ml': ['proj_ml', 'sp_plus_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
            'rl': ['proj_ml', 'sp_plus_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
        },
    },
    'NBA': {
        'ctx_table': 'nba_game_context',
        'results_table': 'nba_game_results',
        'ctx_select': ('game_id,close_total,close_spread,'
                       'projected_total,projected_spread,'
                       'model_pred_home_points,model_pred_away_points,'
                       'mc_probabilities,oddscrowd_snapshot'),
        'extractors': _basketball_extractors,
        'grade': _football_grade,   # same math
        'markets': ['total', 'ml', 'rl'],
        'market_features': {
            'total': ['proj_total', 'model_total', 'mc_total', 'oc_total_heavy'],
            'ml': ['proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
            'rl': ['proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
        },
    },
    'NCAAB': {
        'ctx_table': 'ncaab_game_context',
        'results_table': 'ncaab_game_results',
        'ctx_select': ('game_id,close_total,close_spread,'
                       'projected_total,projected_spread,'
                       'model_pred_home_points,model_pred_away_points,'
                       'mc_probabilities,oddscrowd_snapshot'),
        'extractors': _basketball_extractors,
        'grade': _football_grade,
        'markets': ['total', 'ml', 'rl'],
        'market_features': {
            'total': ['proj_total', 'model_total', 'mc_total', 'oc_total_heavy'],
            'ml': ['proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
            'rl': ['proj_ml', 'model_ml', 'mc_ml', 'oc_ml_heavy'],
        },
    },
    'NHL': {
        'ctx_table': 'nhl_game_context',
        'results_table': 'nhl_game_results',
        'ctx_select': ('game_id,close_spread,'
                       'projected_spread,'
                       'mc_probabilities'),
        'extractors': _hockey_extractors,
        'grade': _football_grade,   # ML + RL only
        'markets': ['ml', 'rl'],
        'market_features': {
            'ml': ['proj_ml', 'mc_ml'],
            'rl': ['proj_ml', 'mc_ml'],
        },
    },
    # UFC deferred — different market structure (fight/method/round).
}


def fetch_ctx(sport: str, days: int):
    plugin = SPORT_PLUGINS[sport]
    start = (date.today() - timedelta(days=days)).isoformat()
    # 2026-09-16 PAGINATION (project_postgrest_truncation_audit_912).
    # MLB ctx with days=365 = ~2500 rows, silently truncated to 1000.
    # discover_patterns pattern-mines against a partial slate → false
    # "signal not seen enough" verdicts on real edges.
    return _paged_get(f'{plugin["ctx_table"]}',
                       {'game_date': f'gte.{start}', 'select': plugin['ctx_select']})


def fetch_results(sport: str, days: int):
    plugin = SPORT_PLUGINS[sport]
    start = (date.today() - timedelta(days=days)).isoformat()
    data = _paged_get(f'{plugin["results_table"]}',
                       {'game_date': f'gte.{start}',
                        'select': 'game_id,home_score,away_score'})
    return {x['game_id']: x for x in data if x.get('home_score') is not None}


def _paged_get(table: str, params: dict, page: int = 1000):
    """Range-header pagination — fetches all rows for a query."""
    out = []
    offset = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}',
                         headers={**H_READ, 'Range': f'{offset}-{offset+page-1}',
                                  'Range-Unit': 'items'},
                         params=params, timeout=45)
        if r.status_code not in (200, 206): break
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        out += chunk
        if len(chunk) < page: break
        offset += page
    return out


def enumerate_combos(features: list, arity_range=(2, 3)):
    """Return list of tuples of feature names, pairs + triples."""
    combos = []
    for a in range(arity_range[0], arity_range[1] + 1):
        for c in combinations(features, a):
            combos.append(c)
    return combos


def mine_sport(sport: str, days: int = 60, dry_run: bool = False):
    """Discover patterns for one sport, optionally seed as signal_sources."""
    plugin = SPORT_PLUGINS[sport]
    print(f'\n=== Mining {sport} · last {days}d ===')
    ctx_rows = fetch_ctx(sport, days)
    results = fetch_results(sport, days)
    print(f'  {len(ctx_rows)} ctx rows · {len(results)} graded games')

    if not ctx_rows:
        print('  (no ctx data — skipping)')
        return []

    # Extract features per game once
    # 2026-09-15: pass `sport` kwarg to extractors that accept it (per
    # project_close_spread_sign_bug_914 — _football_extractors needs
    # sport to normalize close_spread signs correctly).
    import inspect as _inspect
    _ext_fn = plugin['extractors']
    _ext_accepts_sport = 'sport' in _inspect.signature(_ext_fn).parameters
    game_features = {}
    for g in ctx_rows:
        gid = g.get('game_id')
        if not gid or gid not in results: continue
        feats = _ext_fn(g, sport=sport) if _ext_accepts_sport else _ext_fn(g)
        game_features[gid] = feats

    # Compute grades per game
    game_grades = {}
    for g in ctx_rows:
        gid = g.get('game_id')
        if not gid or gid not in results: continue
        ct = _f(g.get('close_total'))
        cs = _f(g.get('close_spread'))
        game_grades[gid] = plugin['grade'](results[gid], ct, cs)

    # Per-market: enumerate feature combos, filter to combos where all
    # features fire in the same direction, grade
    all_findings = []
    for market in plugin['markets']:
        market_features = plugin['market_features'][market]
        combos = enumerate_combos(market_features, arity_range=(2, 3))
        market_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'sides': defaultdict(int)})

        for gid, feats in game_features.items():
            grade = game_grades.get(gid, {}).get(market)
            if not grade: continue

            for combo in combos:
                # Check every feature in combo fired
                values = [feats.get(f) for f in combo]
                if any(v is None for v in values): continue
                # Same direction across combo?
                if len(set(values)) != 1: continue
                direction = values[0]

                key = (combo, direction)
                if grade == direction:
                    market_stats[key]['w'] += 1
                else:
                    market_stats[key]['l'] += 1

        # Filter
        for (combo, direction), stats in market_stats.items():
            n = stats['w'] + stats['l']
            if n < MIN_SAMPLE: continue
            pct = 100 * stats['w'] / n
            if abs(pct - 50) < MIN_EDGE_PP: continue
            all_findings.append({
                'sport': sport,
                'market': market,
                'combo': combo,
                'direction': direction,
                'wins': stats['w'],
                'losses': stats['l'],
                'n': n,
                'hit_rate': round(pct, 1),
                'is_fade': pct < 50,
            })

    # Report + optionally seed
    all_findings.sort(key=lambda x: -x['n'])
    print(f'  {len(all_findings)} combos meeting n>={MIN_SAMPLE} |edge|>={MIN_EDGE_PP}pp')
    for f in all_findings[:30]:
        combo_str = '+'.join(f['combo'])
        fade_flag = ' [FADE]' if f['is_fade'] else ''
        print(f"  {f['market']:5} {combo_str:60} {f['direction']:5} "
              f"{f['wins']}-{f['losses']} ({f['hit_rate']}%) n={f['n']}{fade_flag}")

    if not dry_run and all_findings:
        seeded = _seed_findings(sport, all_findings)
        print(f'  ✓ seeded {seeded} new signal_sources rows')

    return all_findings


def _seed_findings(sport: str, findings: list) -> int:
    """Upsert findings as signal_sources rows via idempotent naming."""
    now = date.today().isoformat()
    payloads = []
    for f in findings:
        combo_slug = '_'.join(f['combo'])
        dir_slug = f['direction'].lower()
        suffix = '_fade' if f['is_fade'] else ''
        signal_key = f"discovered_{f['market']}_{combo_slug}_{dir_slug}{suffix}"
        # Side_expr: normal → direction; fade → opposite
        if f['is_fade']:
            flip = {'over':'UNDER','under':'OVER','HOME':'AWAY_ML','AWAY':'HOME_ML'}
            # for RL append _RL
            base_side = flip.get(f['direction'])
            if f['market'] == 'rl':
                base_side = {'HOME':'AWAY_RL','AWAY':'HOME_RL'}.get(f['direction'])
            side = base_side
            hr_final = 100 - f['hit_rate']  # fade rate
        else:
            side_map = {'over':'OVER','under':'UNDER','HOME':'HOME_ML','AWAY':'AWAY_ML'}
            side = side_map.get(f['direction'])
            if f['market'] == 'rl':
                side = {'HOME':'HOME_RL','AWAY':'AWAY_RL'}.get(f['direction'])
            hr_final = f['hit_rate']
        if not side: continue

        payloads.append({
            'signal_key': signal_key,
            'sport': sport,
            'class': 'model',   # keep in model class so class-balance cap applies
            'market_scope': f['market'],
            'condition_expr': 'False',  # discovered signals are informational
                                        # only until wired into ctx extractors
            'side_expr': f'"{side}"',
            'strength_expr': '0.5',
            'hit_rate_pct': hr_final,
            'sample_n': f['n'],
            'display_prose_template': f"discovered pattern {'+'.join(f['combo'])} agree {f['direction']}",
            'description': (f'Auto-discovered pattern: {"+".join(f["combo"])} '
                           f'agree {f["direction"]} on {f["market"].upper()}. '
                           f'{f["wins"]}-{f["losses"]} ({f["hit_rate"]}%) '
                           f'{60}d {sport}. Auto-seeded {now}.'),
            'enabled': False,   # start disabled until validated by user review
        })

    if not payloads: return 0
    r = requests.post(f'{SB}/rest/v1/signal_sources'
                       '?on_conflict=signal_key,sport,market_scope',
                       headers=H_WRITE, json=payloads, timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  ✗ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payloads)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None, choices=list(SPORT_PLUGINS.keys()))
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--all', action='store_true')
    args = ap.parse_args()

    if args.all:
        for sport in SPORT_PLUGINS:
            mine_sport(sport, days=args.days, dry_run=args.dry_run)
    elif args.sport:
        mine_sport(args.sport, days=args.days, dry_run=args.dry_run)
    else:
        print('specify --sport <SPORT> or --all')
        sys.exit(1)


if __name__ == '__main__':
    main()
