"""NRFI v2 scorer — load learned weights from models/nrfi_v2_weights.json
and score any game context dict.

Trained 2026-06-06 on n=448 graded games with full feature coverage.
Lifetime backtest (`_nrfi_yrfi_reweight_606.py`):
  Top 10% by NEW score: 65.9% (vs 40.9% for old nrfi_score)
  Top 20% by NEW score: 70.5% (vs 51.1%)
  60-69 band: 69.3% on n=75 — primary surface band

Usage:
    from nrfi_v2_scorer import score_nrfi, score_yrfi
    ctx = fetch_game_context(...)
    score = score_nrfi(ctx)  # returns int 0-100
"""
import os
import json
import math

_MODEL_CACHE = None


def _load():
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    path = os.path.join(os.path.dirname(__file__), "models", "nrfi_v2_weights.json")
    with open(path) as fp:
        _MODEL_CACHE = json.load(fp)
    return _MODEL_CACHE


def _sigmoid(z):
    if z < -500: return 0.0
    if z > 500: return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _gather_features(ctx, away_offense=None, home_offense=None):
    """Pull the 26 features the model needs from a mlb_game_context-style
    dict. Caller can pass team-offense rows if they have them (richer);
    otherwise we impute to feature means."""
    m = _load()
    feat_names = m["feature_names"]
    means = m["feature_means"]
    stds = m["feature_stds"]

    raw = {
        'home_fi_era': _f(ctx.get('home_first_inning_era')),
        'away_fi_era': _f(ctx.get('away_first_inning_era')),
        'home_fi_ip':  _f(ctx.get('home_first_inning_ip')),
        'away_fi_ip':  _f(ctx.get('away_first_inning_ip')),
        'home_fi_whip': _f(ctx.get('home_first_inning_whip')),
        'away_fi_whip': _f(ctx.get('away_first_inning_whip')),
        'home_xera':   _f(ctx.get('home_sp_xera')),
        'away_xera':   _f(ctx.get('away_sp_xera')),
        'home_l3_era': _f(ctx.get('home_pitcher_last_3_era')),
        'away_l3_era': _f(ctx.get('away_pitcher_last_3_era')),
        'home_sp_k':   _f(ctx.get('home_sp_k_pct')),
        'away_sp_k':   _f(ctx.get('away_sp_k_pct')),
        'home_wrc':    _f(ctx.get('home_wrc_plus')),
        'away_wrc':    _f(ctx.get('away_wrc_plus')),
        'home_team_k': _f(ctx.get('home_team_k_pct')),
        'away_team_k': _f(ctx.get('away_team_k_pct')),
        'park': _f(ctx.get('park_run_factor')),
        'temp': _f(ctx.get('temperature')),
    }
    # Inning-1 offense splits (from team_offense rows)
    ho = home_offense or {}
    ao = away_offense or {}
    raw.update({
        'home_off_inn1_ops':  _f(ho.get('inning_1_ops')),
        'away_off_inn1_ops':  _f(ao.get('inning_1_ops')),
        'home_off_inn1_rpg':  _f(ho.get('inning_1_runs_per_game')),
        'away_off_inn1_rpg':  _f(ao.get('inning_1_runs_per_game')),
        'home_off_inn1_wrc':  _f(ho.get('inning_1_wrc_plus')),
        'away_off_inn1_wrc':  _f(ao.get('inning_1_wrc_plus')),
        'home_off_inn1_k':    _f(ho.get('inning_1_k_pct')),
        'away_off_inn1_k':    _f(ao.get('inning_1_k_pct')),
    })

    # Impute missing with training-time mean
    vec = []
    for i, fn in enumerate(feat_names):
        v = raw.get(fn)
        if v is None:
            v = means[i]
        # Scale: (x - mean) / std
        sd = stds[i] if stds[i] != 0 else 1.0
        vec.append((v - means[i]) / sd)
    return vec


def _score_with(coef, intercept, vec):
    z = intercept + sum(c * v for c, v in zip(coef, vec))
    p = _sigmoid(z)
    return int(round(p * 100))


def score_nrfi(ctx, home_offense=None, away_offense=None):
    """Score a game's NRFI probability (0-100). Higher = more likely NRFI."""
    m = _load()
    vec = _gather_features(ctx, home_offense=home_offense, away_offense=away_offense)
    return _score_with(m["nrfi_weights"], m["nrfi_intercept"], vec)


def score_yrfi(ctx, home_offense=None, away_offense=None):
    """Score a game's YRFI probability (0-100). Higher = more likely run in 1st."""
    m = _load()
    vec = _gather_features(ctx, home_offense=home_offense, away_offense=away_offense)
    return _score_with(m["yrfi_weights"], m["yrfi_intercept"], vec)


def tier_for_nrfi(score):
    """Map v2 NRFI score to a publishable tier per backtest bands.
    60-69 = STRONG (69.3% n=75)
    70+   = PRIME (66.7% n=21)
    <60   = SKIP
    """
    if score >= 70: return "PRIME"
    if score >= 60: return "STRONG"
    return "SKIP"


if __name__ == "__main__":
    # CLI: score today's 6/6 slate for visual sanity check
    import sys, io, urllib.request
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    from dotenv import load_dotenv
    load_dotenv('.env'); load_dotenv('mlb_pipeline/.env')
    URL = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
    H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    def get(p):
        with urllib.request.urlopen(urllib.request.Request(URL + p, headers=H), timeout=30) as r:
            return json.loads(r.read())
    games = get('/rest/v1/mlb_game_context?game_date=eq.2026-06-06&select=*')
    offense = get('/rest/v1/mlb_team_offense?season=eq.2026&select=team,inning_1_ops,inning_1_runs_per_game,inning_1_k_pct,inning_1_bb_pct,inning_1_hr_per_game,inning_1_wrc_plus')
    off_by = {o['team']: o for o in offense}
    print(f"{'MATCHUP':>45s}  OLD  NEW  TIER")
    print("-" * 80)
    for g in sorted(games, key=lambda x: -(x.get('nrfi_score') or 0)):
        away_o = off_by.get(g.get('away_team'))
        home_o = off_by.get(g.get('home_team'))
        new = score_nrfi(g, home_offense=home_o, away_offense=away_o)
        old = g.get('nrfi_score') or 0
        tier = tier_for_nrfi(new)
        flag = ' ⭐' if tier in ('PRIME', 'STRONG') else ''
        m = f"{g.get('away_team','?')[:18]} @ {g.get('home_team','?')[:18]}"
        print(f'  {m:>43s}  {old:>3}  {new:>3}  {tier:>6}{flag}')
