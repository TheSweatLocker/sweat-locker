"""NCAAF sharp-fade rule engine (2026-08-09).

Adaptation of nfl_sharp_fade_rules for NCAAF. Uses OddsCrowd divergence
via `oddscrowd_snapshot` on `ncaaf_game_context`.

NCAAF-specific considerations:
- Larger public bias toward NAME brands (Ohio State, Alabama, etc.) —
  sharp fade on heavy-public favorites more effective than NFL
- Wider spread ranges (3.5 to 45+) so juice trap band different from NFL
- Divisional games more common — most conference games are "rivalries"
  in some sense, so div-game amplifier applies broadly

Rule set (mirrors NFL + NCAAF-specific additions):

  MODELS_OPPOSE_SHARP_TOTAL   All models vs sharp on total
  MODELS_OPPOSE_SHARP_ML      Both models vs sharp on ML
  MODELS_OPPOSE_SHARP_SPREAD  Both models vs sharp on spread
  SHARP_ON_ROAD_TEAM          Sharp on AWAY team ML
  SHARP_ON_AWAY_FAV           Sharp on AWAY favorite (harshest fade in MLB)
  SHARP_LIGHT_JUICE           Sharp on -140 to +150 pick-em price
  SHARP_OPPOSES_CONFLUENCE    Sharp opposite cohort confluence net
  SHARP_ON_HEAVY_HOME_FAV     Sharp on home fav worse than -400 (NCAAF-specific: bigger favs)
  SHARP_ON_BRAND_NAME_FAV     NCAAF-specific: sharp on major P4 program at -300+ = public trap

Same interface as NFL/MLB `compute_fade_context()` — plug-compatible.
Cap policy: 1 ACTIVE rule → CAP_TO_LEAN. 2+ ACTIVE → CAP_TO_READ.

Rules start in LOG mode until per-rule stats accumulate via nightly
`ncaaf_sharp_fade_rules_stats.py` (analog to MLB/NFL).
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


# Per-rule metadata. Defaults from MLB/NFL analogs; retunes via nightly stats.
RULE_META = {
    'MODELS_OPPOSE_SHARP_TOTAL':   {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'MODELS_OPPOSE_SHARP_ML':      {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'MODELS_OPPOSE_SHARP_SPREAD':  {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_ROAD_TEAM':          {'n_min': 12, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_AWAY_FAV':           {'n_min': 6,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_LIGHT_JUICE':           {'n_min': 15, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_OPPOSES_CONFLUENCE':    {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_HEAVY_HOME_FAV':     {'n_min': 5,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_ON_BRAND_NAME_FAV':     {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
}


# NCAAF "brand name" programs — public traps when they're heavy favs
BRAND_NAME_PROGRAMS = {
    'Alabama','Georgia','Ohio State','Michigan','Texas','Notre Dame','USC',
    'Oregon','Penn State','LSU','Florida','Oklahoma','Auburn','Tennessee',
    'Clemson','Florida State','Miami','Wisconsin','Iowa','Nebraska',
}


def _has_sharp(ctx, mkt, min_div=10):
    """Return (pick, div) if positive sharp divergence >= min_div, else None."""
    oc = ctx.get('oddscrowd_snapshot')
    if isinstance(oc, str):
        try: oc = json.loads(oc)
        except: return None
    if not isinstance(oc, dict): return None
    b = oc.get(mkt) or {}
    pick, div = b.get('pick'), b.get('div')
    if pick is None or div is None or div == -1: return None
    if div < min_div: return None
    return (pick, div)


def rule_models_oppose_sharp(ctx, pick_market, pick_side):
    """When both models (matchup EPA + SP+/returning) oppose sharp."""
    sh = _has_sharp(ctx, pick_market)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None

    def _t_side(v, line):
        if v is None or line is None: return None
        if v > line + 0.5: return 'OVER'
        if v < line - 0.5: return 'UNDER'
        return None
    def _s_side(model_spread):
        # NCAAF CFBD convention: positive close_spread = home is DOG.
        # For direction: model_spread > 0 means model predicts home wins by X (home fav)
        # Cross-reference with close_spread direction happens in caller
        if model_spread is None: return None
        if model_spread > 0.5: return 'HOME'
        if model_spread < -0.5: return 'AWAY'
        return None

    if pick_market == 'total':
        line = ctx.get('close_total')
        models = {
            'matchup': _t_side(ctx.get('projected_total'), line),
            'sp_plus': _t_side(ctx.get('sp_plus_pred_total'), line),
        }
    elif pick_market in ('ml', 'spread'):
        models = {
            'matchup': _s_side(ctx.get('projected_spread')),
            'sp_plus': _s_side(ctx.get('sp_plus_pred_spread')),
        }
    else:
        return None

    filled = {m: s for m, s in models.items() if s}
    if not filled: return None
    if all(s != sharp_side for s in filled.values()) and len(filled) >= 2:
        rule = 'MODELS_OPPOSE_SHARP_' + ('TOTAL' if pick_market=='total' else pick_market.upper())
        return {'rule': rule, 'severity': 'STRONG',
                'reason': f'Both {list(filled.keys())} oppose sharp on {sharp_side}. Cross-sport fade edge ~70%.'}
    return None


def rule_sharp_light_juice(ctx, pick_market, pick_side):
    if pick_market != 'ml': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml')
    a_ml = ctx.get('close_away_ml') or ctx.get('away_ml')
    if h_ml is None or a_ml is None: return None
    sharp_price = float(h_ml) if sharp_side == 'HOME' else float(a_ml)
    if -140 < sharp_price < 150:
        return {'rule': 'SHARP_LIGHT_JUICE', 'severity': 'MEDIUM',
                'reason': f'Sharp on {sharp_side} at {sharp_price:+.0f} (light-juice band). Cross-sport analog: 27-33% hit.'}
    return None


def rule_sharp_opposes_confluence(ctx, pick_market, pick_side):
    sh = _has_sharp(ctx, pick_market)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    cn = ctx.get('signal_confluence_net')
    if cn is None or abs(cn) < 2: return None
    if pick_market == 'ml':
        conf_side = 'HOME' if cn > 0 else 'AWAY'
    else:
        conf_side = 'OVER' if cn > 0 else 'UNDER'
    if conf_side != sharp_side:
        return {'rule': 'SHARP_OPPOSES_CONFLUENCE', 'severity': 'STRONG',
                'reason': f'Sharp {sharp_side} but confluence net={cn:+d} → {conf_side}.'}
    return None


def rule_sharp_on_road_team(ctx, pick_market, pick_side):
    if pick_market != 'ml' or pick_side != 'AWAY': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'AWAY': return None
    return {'rule': 'SHARP_ON_ROAD_TEAM', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY (road) team ML at +{div}pp. MLB analog: 21% hit — road-team sharp buys lose 79%.'}


def rule_sharp_on_away_fav(ctx, pick_market, pick_side):
    if pick_market != 'ml' or pick_side != 'AWAY': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'AWAY': return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml')
    a_ml = ctx.get('close_away_ml') or ctx.get('away_ml')
    if h_ml is None or a_ml is None: return None
    if float(a_ml) >= float(h_ml): return None
    return {'rule': 'SHARP_ON_AWAY_FAV', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY FAVORITE at {a_ml:+.0f}. MLB analog: 86% fade.'}


def rule_sharp_on_heavy_home_fav(ctx, pick_market, pick_side):
    """NCAAF: sharp on home fav priced worse than -400 (bigger threshold than NFL)."""
    if pick_market != 'ml' or pick_side != 'HOME': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'HOME': return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml')
    if h_ml is None: return None
    if float(h_ml) > -400: return None
    return {'rule': 'SHARP_ON_HEAVY_HOME_FAV', 'severity': 'MEDIUM',
            'reason': f'Sharp on HOME fav priced {h_ml:+.0f} (worse than -400). NCAAF heavy-fav trap zone.'}


def rule_sharp_on_brand_name_fav(ctx, pick_market, pick_side):
    """NCAAF-specific: sharp on brand-name P4 program as heavy fav (public trap).
    Only fires when the sharp-preferred side is a brand-name program AND priced -300 or worse."""
    if pick_market != 'ml': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    team = ctx.get('home_team') if sharp_side == 'HOME' else ctx.get('away_team')
    if not team or not any(bn in team for bn in BRAND_NAME_PROGRAMS): return None
    ml = ctx.get('close_home_ml') if sharp_side == 'HOME' else ctx.get('close_away_ml')
    if ml is None: return None
    try:
        if float(ml) > -300: return None
    except: return None
    return {'rule': 'SHARP_ON_BRAND_NAME_FAV', 'severity': 'MEDIUM',
            'reason': f'Sharp on brand-name {team} at {ml:+.0f} (heavy fav). NCAAF-specific: public loves marquee names, fade edge likely.'}


ALL_RULES: list[Callable] = [
    rule_models_oppose_sharp,
    rule_sharp_light_juice,
    rule_sharp_opposes_confluence,
    rule_sharp_on_road_team,
    rule_sharp_on_away_fav,
    rule_sharp_on_heavy_home_fav,
    rule_sharp_on_brand_name_fav,
]


_STATS_CACHE = {'data': None, 'fetched': 0}


def _load_rule_stats() -> dict:
    now = time.time()
    if _STATS_CACHE['data'] and now - _STATS_CACHE['fetched'] < 900:
        return _STATS_CACHE['data']
    if not _H:
        _STATS_CACHE['data'] = {}
        return {}
    try:
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=_H,
                         params={'cache_key': 'eq.ncaaf_sharp_fade_rules_stats',
                                 'game_id': 'eq.GLOBAL_RULES_NCAAF',
                                 'select': 'data'}, timeout=10)
        rows = r.json() if isinstance(r.json(), list) else []
        data = rows[0]['data'] if rows else {}
    except Exception:
        data = {}
    _STATS_CACHE['data'] = data
    _STATS_CACHE['fetched'] = now
    return data


def _get_rule_mode(rule_name: str) -> str:
    stats = _load_rule_stats().get(rule_name) or {}
    ac_mode = stats.get('auto_calibrated_mode')
    if ac_mode in ('ACTIVE', 'LOG', 'DISABLED'):
        return ac_mode
    meta = RULE_META.get(rule_name)
    if not meta: return 'LOG'
    n = stats.get('n', 0)
    if n < meta['n_min']: return 'LOG'
    lifetime = stats.get('lifetime_hit_pct')
    if lifetime is not None and lifetime >= meta['life_ceiling']: return 'LOG'
    return meta['default_mode']


def compute_fade_context(ctx: dict, pick_market: str, pick_side: str) -> dict:
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
    demo = {
        'home_team': 'Ohio State', 'away_team': 'Ball State',
        'oddscrowd_snapshot': {'ml': {'pick':'HOME','div':22,'money':92,'bets':70}},
        'close_home_ml': -3500, 'close_away_ml': +1400,
        'close_spread': +50.5,
        'projected_spread': -35, 'sp_plus_pred_spread': -33,
        'signal_confluence_net': -3,
    }
    print(json.dumps(compute_fade_context(demo, 'ml', 'HOME'), indent=2))
