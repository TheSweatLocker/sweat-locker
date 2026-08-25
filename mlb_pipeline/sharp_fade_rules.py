"""Sharp money fade rule engine (2026-08-09).

Each rule takes a game context + pick, returns None or a trigger dict.
Composed by compute_fade_context() → returns full audit for a game.

Rules (with source data + policy):

  R1  MODELS_OPPOSE_SHARP        n≥6/10   fade 70-100%   cap ACTIVE
  R2  SHARP_LIGHT_JUICE          n≥20     sharp hit 30%  cap ACTIVE (fav only)
  R3  SHARP_OVER_HIGH_TOTAL      n≥5      fade 80%       cap LOG (small n)
  R4  SHARP_OPPOSES_CONFLUENCE   n≥8      fade 87.5%     cap ACTIVE
  R5  LEAN_TIER_SHARP_PILE_IN    n≥13     fade 77%       cap ACTIVE
  R6  SHARP_ON_ROAD_TEAM         n≥14     fade 79%       cap ACTIVE
  R7  SHARP_ON_AWAY_FAV          n≥7      fade 86%       cap ACTIVE  (subset R6)
  R8  NON_DIV_SHARP_FADE         n≥23     fade 70%       amplifier   (log)

Each rule has cap_mode: 'ACTIVE' | 'LOG' | 'DISABLED'
  - ACTIVE: applies cap-to-LEAN (or READ for 2+ triggers)
  - LOG:    prints warning but does not gate
  - DISABLED: recency kill switch tripped, skipped

Rule metadata (n_min, lifetime_hit_pct, recent_7d_hit_pct) reads from
jerry_cache['sharp_divergence_stats'] and jerry_cache['sharp_fade_rules_stats'].

Recency kill: if recent-7d hit% for a rule >= 55, rule auto-DISABLED
until digest confirms edge returns.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path
from typing import Optional, Callable

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL'); KEY = os.environ.get('SUPABASE_KEY')
_H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else None


# --------- rule definitions --------- #

# Per-rule metadata: (min_n, lifetime_hit_pct_ceiling, recent_kill_pct, cap_mode_default)
RULE_META = {
    'MODELS_OPPOSE_SHARP_TOTAL':   {'n_min': 5,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'MODELS_OPPOSE_SHARP_ML':      {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_LIGHT_JUICE':           {'n_min': 15, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_OVER_HIGH_TOTAL':       {'n_min': 5,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_OPPOSES_CONFLUENCE':    {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'LEAN_TIER_SHARP_PILE_IN':     {'n_min': 10, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_ROAD_TEAM':          {'n_min': 12, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_AWAY_FAV':           {'n_min': 6,  'life_ceiling': 40.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'NON_DIV_SHARP_FADE':          {'n_min': 20, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
}


# --------- rule triggers --------- #

def _has_sharp(ctx, mkt, min_div=10):
    """Return (pick, div) if there's a POSITIVE sharp divergence >= min_div.
    Negative div = public > money (public-leaning), NOT sharp signal. Rules
    fire on sharp-piled-in patterns; public patterns are handled separately
    via the bucket flag (public_10+ boost)."""
    oc = ctx.get('oddscrowd_snapshot')
    if isinstance(oc, str):
        try: oc = json.loads(oc)
        except: return None
    if not isinstance(oc, dict): return None
    b = oc.get(mkt) or {}
    pick, div = b.get('pick'), b.get('div')
    if pick is None or div is None or div == -1: return None
    if div < min_div: return None   # positive only — sharp>public
    return (pick, div)


def rule_models_oppose_sharp(ctx, pick_market, pick_side):
    """R1: all 2+ models oppose sharp side."""
    sh = _has_sharp(ctx, pick_market)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None  # only applies when pick == sharp

    def _mk_side_total(v, line):
        if v is None or line is None: return None
        if v > line + 0.5: return 'OVER'
        if v < line - 0.5: return 'UNDER'
        return None
    def _mk_side_ml(spread, close):
        if spread is None or close is None: return None
        d = float(spread) + float(close)
        if d > 0.5: return 'HOME'
        if d < -0.5: return 'AWAY'
        return None

    if pick_market == 'total':
        line = ctx.get('close_total')
        models = {
            'jerry': _mk_side_total(ctx.get('jerry_pred_total'), line),
            'v4':    _mk_side_total(ctx.get('model_pred_total'), line),
            'panel': _mk_side_total(ctx.get('panel_implied_total'), line),
        }
    else:
        close = ctx.get('close_spread')
        models = {
            'jerry': _mk_side_ml(ctx.get('jerry_pred_spread'), close),
            'v4':    _mk_side_ml(ctx.get('model_pred_spread'), close),
        }
    filled = {m: s for m, s in models.items() if s}
    if not filled: return None
    agree = sum(1 for s in filled.values() if s == sharp_side)
    oppose = sum(1 for s in filled.values() if s != sharp_side)
    if oppose >= 2 and agree == 0:
        rule = 'MODELS_OPPOSE_SHARP_' + pick_market.upper()
        return {'rule': rule, 'severity': 'STRONG',
                'reason': f'{oppose}/{len(filled)} models oppose sharp on {sharp_side}; '
                          f'models: {filled}. Fade edge 70-100% (n small).'}
    return None


def rule_sharp_light_juice(ctx, pick_market, pick_side):
    """R2: sharp on light-juice fav/dog (price -140 to +150)."""
    if pick_market != 'ml': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    h_ml = ctx.get('home_ml_close') or ctx.get('home_ml_odds')
    a_ml = ctx.get('away_ml_close') or ctx.get('away_ml_odds')
    if h_ml is None or a_ml is None: return None
    sharp_price = float(h_ml) if sharp_side == 'HOME' else float(a_ml)
    if -140 < sharp_price < 150:
        return {'rule': 'SHARP_LIGHT_JUICE', 'severity': 'MEDIUM',
                'reason': f'Sharp on {sharp_side} at price {sharp_price:+.0f} (light-juice band -140 to +150). '
                          f'Historical hit rate 27-33% (fade edge).'}
    return None


def rule_sharp_over_high_total(ctx, pick_market, pick_side):
    """R3: sharp on OVER when close_total >= 9.0."""
    if pick_market != 'total' or pick_side != 'OVER': return None
    sh = _has_sharp(ctx, 'total')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'OVER': return None
    line = ctx.get('close_total')
    if line is None or float(line) < 9.0: return None
    return {'rule': 'SHARP_OVER_HIGH_TOTAL', 'severity': 'MEDIUM',
            'reason': f'Sharp on OVER at line {line} (>=9). Historical 20% hit on this pattern (n=5, log-only).'}


def rule_sharp_opposes_confluence(ctx, pick_market, pick_side):
    """R4: sharp side opposite direction of signal_confluence_net."""
    sh = _has_sharp(ctx, pick_market)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    cn = ctx.get('signal_confluence_net') or ctx.get('signal_confluence_v2_net')
    if cn is None or abs(cn) < 2: return None
    if pick_market == 'ml':
        conf_side = 'HOME' if cn > 0 else 'AWAY'
    else:
        conf_side = 'OVER' if cn > 0 else 'UNDER'
    if conf_side != sharp_side:
        return {'rule': 'SHARP_OPPOSES_CONFLUENCE', 'severity': 'STRONG',
                'reason': f'Sharp on {sharp_side} but cohort confluence net={cn:+d} points {conf_side}. '
                          f'Historical fade edge 87.5% when directions disagree.'}
    return None


def rule_lean_tier_sharp_pile_in(ctx, pick_market, pick_side):
    """R5: primary_play tier is LEAN and sharp piled in on that side."""
    pp = ctx.get('primary_play') or {}
    if isinstance(pp, str):
        try: pp = json.loads(pp)
        except: pp = {}
    tier = (pp.get('tier') or '').upper()
    if tier != 'LEAN': return None
    sh = _has_sharp(ctx, pick_market)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    # 2026-08-09: only fire on POSITIVE div (sharp piled in, not public).
    # Negative div = public bet MORE than sharp, opposite pattern.
    if div < 10: return None
    return {'rule': 'LEAN_TIER_SHARP_PILE_IN', 'severity': 'MEDIUM',
            'reason': f'Primary tier is LEAN and sharp piled +{div}pp on {sharp_side}. '
                      f'Sharp hit 23% in this pattern (n=13, fade edge).'}


def rule_sharp_on_road_team(ctx, pick_market, pick_side):
    """R6: sharp on AWAY team ML at div ≥10."""
    if pick_market != 'ml' or pick_side != 'AWAY': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'AWAY': return None
    return {'rule': 'SHARP_ON_ROAD_TEAM', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY (road) team ML at +{div}pp div. '
                      f'Historical 21% hit — road-team sharp buys lose 79% (n=14).'}


def rule_sharp_on_away_fav(ctx, pick_market, pick_side):
    """R7: sharp on AWAY team ML when AWAY is the favorite."""
    if pick_market != 'ml' or pick_side != 'AWAY': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'AWAY': return None
    h_ml = ctx.get('home_ml_close') or ctx.get('home_ml_odds')
    a_ml = ctx.get('away_ml_close') or ctx.get('away_ml_odds')
    if h_ml is None or a_ml is None: return None
    if float(a_ml) >= float(h_ml): return None  # AWAY is not the fav
    return {'rule': 'SHARP_ON_AWAY_FAV', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY FAVORITE at +{div}pp div (away ML {a_ml:+.0f}). '
                      f'Historical 14% hit — road-fav sharp buys lose 86% (n=7).'}


def rule_non_div_sharp_fade(ctx, pick_market, pick_side):
    """R8: non-divisional game amplifier (log only)."""
    home = ctx.get('home_team') or ''
    away = ctx.get('away_team') or ''
    DIV_PAIRS = [
        ('Braves','Marlins','Mets','Phillies','Nationals'),
        ('Cubs','Reds','Brewers','Pirates','Cardinals'),
        ('Diamondbacks','Rockies','Dodgers','Padres','Giants'),
        ('Orioles','Red Sox','Yankees','Rays','Blue Jays'),
        ('Guardians','White Sox','Tigers','Royals','Twins'),
        ('Astros','Angels','Athletics','Mariners','Rangers'),
    ]
    def _short(t): return t.split()[-1] if t else ''
    hs, as_ = _short(home), _short(away)
    is_div = any(hs in d and as_ in d for d in DIV_PAIRS)
    if is_div: return None
    sh = _has_sharp(ctx, pick_market)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    return {'rule': 'NON_DIV_SHARP_FADE', 'severity': 'WEAK',
            'reason': f'Non-divisional game — sharp fade edge amplified. '
                      f'Non-div sharp hit 30% vs 46% divisional (n=23 vs 13).'}


ALL_RULES: list[Callable] = [
    rule_models_oppose_sharp,
    rule_sharp_light_juice,
    rule_sharp_over_high_total,
    rule_sharp_opposes_confluence,
    rule_lean_tier_sharp_pile_in,
    rule_sharp_on_road_team,
    rule_sharp_on_away_fav,
    rule_non_div_sharp_fade,
]


# --------- rule-mode lookup with recency kill switch --------- #

_STATS_CACHE = {'data': None, 'fetched': 0}


def _load_rule_stats() -> dict:
    """Fetch jerry_cache['sharp_fade_rules_stats'] (per-rule lifetime + recent
    hit rates). Cached 15 min. Missing keys = default mode from RULE_META."""
    import time
    now = time.time()
    if _STATS_CACHE['data'] and now - _STATS_CACHE['fetched'] < 900:
        return _STATS_CACHE['data']
    if not _H:
        _STATS_CACHE['data'] = {}
        return {}
    try:
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=_H,
                         params={'cache_key': 'eq.sharp_fade_rules_stats',
                                 'game_id': 'eq.GLOBAL_RULES', 'select': 'data'},
                         timeout=10)
        rows = r.json() if isinstance(r.json(), list) else []
        data = rows[0]['data'] if rows else {}
    except Exception:
        data = {}
    _STATS_CACHE['data'] = data
    _STATS_CACHE['fetched'] = now
    return data


def _get_rule_mode(rule_name: str) -> str:
    """Return current mode for a rule: ACTIVE | LOG | DISABLED.

    2026-08-09: prefer auto-calibrated mode from nightly calibrate() run
    (uses actual market baseline). Falls back to hardcoded RULE_META
    thresholds when auto-calibration hasn't run yet.
    """
    stats = _load_rule_stats().get(rule_name) or {}
    # Auto-calibrated mode takes priority
    ac_mode = stats.get('auto_calibrated_mode')
    if ac_mode in ('ACTIVE', 'LOG', 'DISABLED'):
        return ac_mode

    # Fallback to hardcoded thresholds
    meta = RULE_META.get(rule_name)
    if not meta: return 'LOG'
    n = stats.get('n', 0)
    recent_hit = stats.get('recent_hit_pct')
    lifetime_hit = stats.get('lifetime_hit_pct')

    mode = meta['default_mode']
    if recent_hit is not None and recent_hit >= meta['kill']:
        return 'DISABLED'
    if n < meta['n_min']:
        return 'LOG'
    if lifetime_hit is not None and lifetime_hit >= meta['life_ceiling']:
        return 'LOG'
    return mode


# --------- main entry point --------- #

def compute_fade_context(ctx: dict, pick_market: str, pick_side: str) -> dict:
    """Run all rules against a game context + pick. Returns:

    {
      'triggers': [{rule, severity, reason, mode}, ...],
      'active_count': int,   # rules in ACTIVE mode that fired
      'cap_directive': None | 'CAP_TO_LEAN_55' | 'CAP_TO_READ_49',
    }

    Cap policy: 1 ACTIVE rule → CAP_TO_LEAN. 2+ ACTIVE rules → CAP_TO_READ.
    """
    triggers = []
    for rule_fn in ALL_RULES:
        try:
            t = rule_fn(ctx, pick_market, pick_side)
        except Exception:
            continue
        if not t: continue
        mode = _get_rule_mode(t['rule'])
        t['mode'] = mode
        triggers.append(t)
    active = [t for t in triggers if t.get('mode') == 'ACTIVE']
    cap = None
    if len(active) >= 2: cap = 'CAP_TO_READ_49'
    elif len(active) == 1: cap = 'CAP_TO_LEAN_55'
    return {
        'triggers': triggers,
        'active_count': len(active),
        'cap_directive': cap,
    }


if __name__ == '__main__':
    # Smoke test
    ctx_bos = {
        'oddscrowd_snapshot': {'ml': {'pick':'HOME','div':14,'money':87,'bets':73}},
        'close_spread': -1.5,
        'jerry_pred_spread': 0.5,  # HOME
        'model_pred_spread': 1.2,  # HOME
        'home_ml_close': -160, 'away_ml_close': +140,
        'home_team': 'Boston Red Sox', 'away_team': 'Athletics',
        'signal_confluence_net': 3,  # HOME
        'primary_play': {'tier': 'STRONG', 'type': 'ml'},
    }
    r = compute_fade_context(ctx_bos, 'ml', 'HOME')
    import json as _j
    print(_j.dumps(r, indent=2))
