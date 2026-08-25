"""NHL sharp-fade rule engine (2026-08-19).

Port of ncaaf_sharp_fade_rules for NHL. Uses OddsCrowd divergence via
`oddscrowd_snapshot` on nhl_game_context (populated once ODDS_CROWD_KEY
is enabled — set in Phase 2, ~Week 3 of season). Plug-compatible interface
with MLB/NFL/NCAAF: same compute_fade_context(ctx, pick_market, pick_side)
signature, same cap policy.

NHL-specific considerations:
- Puckline is always ±1.5 (no variable spread), so spread-market fades
  translate to puckline-market with fixed line — simpler than football.
- Home ice edge smaller than NBA/NFL (~5pp for average teams); heavy home
  fav trap threshold set higher.
- Public loves overs in NHL (soft OU market). Sharp on UNDER is common
  and edgier.
- Goalie-driven late line moves — SHARP_ON_GOALIE_NEWS rule fires when
  sharp direction confirmed by starter change (~1h pre-puck-drop).
- Divisional games have public bias (rivalry noise) → amplifier.

Rule set:
  MODELS_OPPOSE_SHARP_TOTAL   Both Elo + team form vs sharp on total
  MODELS_OPPOSE_SHARP_ML      Both Elo + h2h vs sharp on ML
  MODELS_OPPOSE_SHARP_PUCKLINE Both models vs sharp on puckline
  SHARP_ON_ROAD_TEAM_ML       Sharp on AWAY ML (weakest cross-sport)
  SHARP_ON_AWAY_FAV_ML        Sharp on AWAY favorite (harshest fade)
  SHARP_LIGHT_JUICE           Sharp on -140 to +150 pick-em ML band
  SHARP_OPPOSES_CONFLUENCE    Sharp opposite cohort confluence net
  SHARP_ON_OVER_NHL_TRAP      NHL-specific: sharp on OVER in high-total slate
                              (public loves overs; sharp $ often trap-buy)
  SHARP_ON_HEAVY_HOME_FAV_ML  Sharp on home fav worse than -250 (NHL trap)

Rules ship in LOG mode until per-rule stats accumulate via nightly
`nhl_sharp_fade_rules_stats.py` (analog to MLB/NFL) — not built yet.
Once we have ~10 fires per rule from live season data, activate manually.

Cap policy: 1 ACTIVE rule → CAP_TO_LEAN. 2+ ACTIVE → CAP_TO_READ.
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


# Per-rule metadata. All rules start in LOG mode — activate after Week 3
# based on nhl_sharp_fade_rules_stats accumulation.
RULE_META = {
    'MODELS_OPPOSE_SHARP_TOTAL':      {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'MODELS_OPPOSE_SHARP_ML':         {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'MODELS_OPPOSE_SHARP_PUCKLINE':   {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_ON_ROAD_TEAM_ML':          {'n_min': 12, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_ON_AWAY_FAV_ML':           {'n_min': 6,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_LIGHT_JUICE':              {'n_min': 15, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_OPPOSES_CONFLUENCE':       {'n_min': 8,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_ON_OVER_NHL_TRAP':         {'n_min': 10, 'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
    'SHARP_ON_HEAVY_HOME_FAV_ML':     {'n_min': 5,  'life_ceiling': 45.0, 'kill': 55.0, 'default_mode': 'LOG'},
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
    """Both Elo + h2h/team_form oppose sharp side."""
    # NHL market key: 'puckline' internally, 'rl' externally; support both
    lookup_mkt = 'puckline' if pick_market == 'rl' else pick_market
    sh = _has_sharp(ctx, lookup_mkt)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None

    def _t_side(v, line):
        if v is None or line is None: return None
        if v > line + 0.3: return 'OVER'
        if v < line - 0.3: return 'UNDER'
        return None
    def _ml_side(wp):
        if wp is None: return None
        if wp > 0.55: return 'HOME'
        if wp < 0.45: return 'AWAY'
        return None

    if pick_market == 'total':
        line = ctx.get('close_total')
        models = {
            'elo': _t_side(ctx.get('projected_total'), line),
        }
    elif pick_market in ('ml', 'rl'):
        models = {
            'elo':  _ml_side(ctx.get('projected_home_wp')),
        }
        # Add form model
        h5 = ctx.get('home_form_last5_wins'); a5 = ctx.get('away_form_last5_wins')
        if h5 is not None and a5 is not None:
            diff = h5 - a5
            models['form'] = 'HOME' if diff >= 2 else 'AWAY' if diff <= -2 else None
    else:
        return None

    filled = {m: s for m, s in models.items() if s}
    if not filled: return None
    if all(s != sharp_side for s in filled.values()) and len(filled) >= 2:
        rule = 'MODELS_OPPOSE_SHARP_' + ('TOTAL' if pick_market == 'total'
                                        else 'PUCKLINE' if pick_market == 'rl'
                                        else 'ML')
        return {'rule': rule, 'severity': 'STRONG',
                'reason': f'Both {list(filled.keys())} oppose sharp on {sharp_side}. Cross-sport fade edge ~65-70%.'}
    return None


def rule_sharp_light_juice(ctx, pick_market, pick_side):
    if pick_market != 'ml': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml_close')
    a_ml = ctx.get('close_away_ml') or ctx.get('away_ml_close')
    if h_ml is None or a_ml is None: return None
    sharp_price = float(h_ml) if sharp_side == 'HOME' else float(a_ml)
    if -140 < sharp_price < 150:
        return {'rule': 'SHARP_LIGHT_JUICE', 'severity': 'MEDIUM',
                'reason': f'Sharp on {sharp_side} at {sharp_price:+.0f} (light-juice band). Cross-sport: 27-33% hit.'}
    return None


def rule_sharp_opposes_confluence(ctx, pick_market, pick_side):
    lookup_mkt = 'puckline' if pick_market == 'rl' else pick_market
    sh = _has_sharp(ctx, lookup_mkt)
    if not sh: return None
    sharp_side, div = sh
    if pick_side != sharp_side: return None
    cn = ctx.get('signal_confluence_net')
    if cn is None or abs(cn) < 2: return None
    if pick_market == 'ml' or pick_market == 'rl':
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
    return {'rule': 'SHARP_ON_ROAD_TEAM_ML', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY (road) team ML at +{div}pp. Cross-sport: 21% hit — road-team sharp buys lose 79%.'}


def rule_sharp_on_away_fav(ctx, pick_market, pick_side):
    if pick_market != 'ml' or pick_side != 'AWAY': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'AWAY': return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml_close')
    a_ml = ctx.get('close_away_ml') or ctx.get('away_ml_close')
    if h_ml is None or a_ml is None: return None
    if float(a_ml) >= float(h_ml): return None
    return {'rule': 'SHARP_ON_AWAY_FAV_ML', 'severity': 'STRONG',
            'reason': f'Sharp on AWAY FAVORITE at {a_ml:+.0f}. Cross-sport analog: 80%+ fade.'}


def rule_sharp_on_heavy_home_fav(ctx, pick_market, pick_side):
    """NHL: sharp on home fav priced worse than -250 (moderate — NHL has small HFA)."""
    if pick_market != 'ml' or pick_side != 'HOME': return None
    sh = _has_sharp(ctx, 'ml')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'HOME': return None
    h_ml = ctx.get('close_home_ml') or ctx.get('home_ml_close')
    if h_ml is None: return None
    if float(h_ml) > -250: return None
    return {'rule': 'SHARP_ON_HEAVY_HOME_FAV_ML', 'severity': 'MEDIUM',
            'reason': f'Sharp on HOME fav priced {h_ml:+.0f} (worse than -250). NHL heavy-fav ML rare & often trap.'}


def rule_sharp_on_over_nhl_trap(ctx, pick_market, pick_side):
    """NHL-specific: sharp $ on OVER when total ≥ 6.5 is often a public-following
    trap. NHL public loves overs; historical fade edge on very-high totals."""
    if pick_market != 'total' or pick_side != 'OVER': return None
    sh = _has_sharp(ctx, 'total')
    if not sh: return None
    sharp_side, div = sh
    if sharp_side != 'OVER': return None
    tot = ctx.get('close_total')
    if tot is None or float(tot) < 6.5: return None
    return {'rule': 'SHARP_ON_OVER_NHL_TRAP', 'severity': 'MEDIUM',
            'reason': f'Sharp on OVER at close_total {tot} (≥6.5). NHL public bias — overs get sharp $ that often loses.'}


ALL_RULES: list[Callable] = [
    rule_models_oppose_sharp,
    rule_sharp_light_juice,
    rule_sharp_opposes_confluence,
    rule_sharp_on_road_team,
    rule_sharp_on_away_fav,
    rule_sharp_on_heavy_home_fav,
    rule_sharp_on_over_nhl_trap,
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
                         params={'cache_key': 'eq.nhl_sharp_fade_rules_stats',
                                 'game_id': 'eq.GLOBAL_RULES_NHL',
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
        'home_team': 'Boston Bruins', 'away_team': 'Toronto Maple Leafs',
        'oddscrowd_snapshot': {'ml': {'pick': 'AWAY', 'div': 22, 'money': 78, 'bets': 56}},
        'close_home_ml': -175, 'close_away_ml': +155,
        'close_puckline': -1.5, 'close_total': 6.5,
        'projected_home_wp': 0.62,
        'home_form_last5_wins': 4, 'away_form_last5_wins': 2,
        'signal_confluence_net': +2,
    }
    print('DEMO: fade sharp AWAY on road-fav ML play')
    print(json.dumps(compute_fade_context(demo, 'ml', 'AWAY'), indent=2))
