"""NFL sharp-fade rule engine (2026-08-09).

Adaptation of `sharp_fade_rules.py` (MLB) for NFL. Uses OddsCrowd
divergence data via `oddscrowd_snapshot` on `nfl_game_context`.

Rules ported + NFL-specific:

  MODELS_OPPOSE_SHARP_TOTAL   All models point one way, sharp opposite
  MODELS_OPPOSE_SHARP_ML      Both models oppose sharp ML
  MODELS_OPPOSE_SHARP_SPREAD  Both models oppose sharp spread
  SHARP_ON_ROAD_TEAM          Sharp on AWAY ML (loses historically)
  SHARP_ON_AWAY_FAV           Sharp on AWAY favorite (harshest fade)
  SHARP_LIGHT_JUICE           Sharp on -140 to +150 pick'em spot
  SHARP_OPPOSES_CONFLUENCE    Sharp direction ≠ signal_confluence_net
  SHARP_ON_HEAVY_HOME_FAV     Sharp on home fav worse than -300 (rare-but-brutal)

Rule stats accumulate in jerry_cache['nfl_sharp_fade_rules_stats'] via
nightly nfl_sharp_fade_rules_stats.py (analog of MLB script). Until we
have that sample, use MLB-derived defaults + n_min gates.

Same interface as MLB `compute_fade_context()` — plug-compatible.
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


# Per-rule metadata. Defaults derived from MLB findings; will re-tune once
# NFL sample accumulates via nfl_sharp_fade_rules_stats.py.
RULE_META = {
    'MODELS_OPPOSE_SHARP_TOTAL':   {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'MODELS_OPPOSE_SHARP_ML':      {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'MODELS_OPPOSE_SHARP_SPREAD':  {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_ROAD_TEAM':          {'n_min': 12, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_AWAY_FAV':           {'n_min': 6,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_LIGHT_JUICE':           {'n_min': 15, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_OPPOSES_CONFLUENCE':    {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'ACTIVE'},
    'SHARP_ON_HEAVY_HOME_FAV':     {'n_min': 5,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
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
    """When both matchup + Panel oppose sharp side."""
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
        # NFL: positive proj_spread = home wins (nflverse convention)
        if model_spread is None: return None
        if model_spread > 0.5: return 'HOME'
        if model_spread < -0.5: return 'AWAY'
        return None

    if pick_market == 'total':
        line = ctx.get('close_total')
        models = {
            'matchup': _t_side(ctx.get('projected_total'), line),
            'panel': _t_side(ctx.get('panel_pred_total'), line),
        }
    elif pick_market in ('ml', 'spread'):
        models = {
            'matchup': _s_side(ctx.get('projected_spread')),
        }
        # Panel spread derived from panel_pred_home_pts − panel_pred_away_pts
        ph = ctx.get('panel_pred_home_pts'); pa = ctx.get('panel_pred_away_pts')
        if ph is not None and pa is not None:
            models['panel'] = _s_side(float(ph) - float(pa))
    else:
        return None

    filled = {m: s for m, s in models.items() if s}
    if not filled: return None
    oppose = sum(1 for s in filled.values() if s != sharp_side)
    if oppose >= 2 and all(s != sharp_side for s in filled.values()):
        rule = 'MODELS_OPPOSE_SHARP_' + ('TOTAL' if pick_market=='total' else pick_market.upper())
        return {'rule': rule, 'severity': 'STRONG',
                'reason': f'Both {list(filled.keys())} oppose sharp on {sharp_side}. Fade edge in similar spots per MLB analog (~70% fade win).'}
    return None


def rule_sharp_light_juice(ctx, pick_market, pick_side):
    """Sharp on light-juice ML (-140 to +150)."""
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
                'reason': f'Sharp on {sharp_side} at {sharp_price:+.0f} (light-juice pick-em band). MLB analog hit 27-33% (fade edge).'}
    return None


def rule_sharp_opposes_confluence(ctx, pick_market, pick_side):
    """Sharp direction opposite signal_confluence_net."""
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
                'reason': f'Sharp {sharp_side} but confluence net={cn:+d} → {conf_side}. Historical fade edge 87.5% (MLB analog).'}
    return None


def rule_sharp_on_road_team(ctx, pick_market, pick_side):
    """Sharp on AWAY team ML — historically loses in MLB."""
    if pick_market != 'ml' or pick_side != 'AWAY': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'AWAY': return None
    return {'rule': 'SHARP_ON_ROAD_TEAM', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY (road) team ML at +{div}pp. MLB analog: 21% hit — road-team sharp buys lose 79%.'}


def rule_sharp_on_away_fav(ctx, pick_market, pick_side):
    """Sharp on AWAY favorite ML — harshest fade pattern in MLB."""
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
            'reason': f'Sharp on AWAY FAVORITE at {a_ml:+.0f}. MLB analog 14% hit — 86% fade.'}


def rule_sharp_on_heavy_home_fav(ctx, pick_market, pick_side):
    """NFL-specific: sharp on home fav priced worse than -300."""
    if pick_market != 'ml' or pick_side != 'HOME': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'HOME': return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml')
    if h_ml is None: return None
    if float(h_ml) > -300: return None   # not heavy enough
    return {'rule': 'SHARP_ON_HEAVY_HOME_FAV', 'severity': 'MEDIUM',
            'reason': f'Sharp on HOME fav priced {h_ml:+.0f} (worse than -300). Historical NFL trap zone.'}


ALL_RULES: list[Callable] = [
    rule_models_oppose_sharp,
    rule_sharp_light_juice,
    rule_sharp_opposes_confluence,
    rule_sharp_on_road_team,
    rule_sharp_on_away_fav,
    rule_sharp_on_heavy_home_fav,
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
                         params={'cache_key': 'eq.nfl_sharp_fade_rules_stats',
                                 'game_id': 'eq.GLOBAL_RULES_NFL',
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
    """Same shape as MLB compute_fade_context. Cap policy:
    1 ACTIVE rule → CAP_TO_LEAN. 2+ ACTIVE rules → CAP_TO_READ."""
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
    demo = {
        'oddscrowd_snapshot': {'ml': {'pick':'AWAY','div':22,'money':78,'bets':56},
                                'total': {'pick':'UNDER','div':18,'money':66,'bets':48}},
        'close_home_ml': -140, 'close_away_ml': +120,
        'close_total': 45.5, 'close_spread': 3.5,
        'projected_total': 48.5, 'panel_pred_total': 47.2,
        'projected_spread': 4.5, 'panel_pred_home_pts': 26, 'panel_pred_away_pts': 21,
        'signal_confluence_net': 3,
    }
    print(json.dumps(compute_fade_context(demo, 'ml', 'AWAY'), indent=2))
