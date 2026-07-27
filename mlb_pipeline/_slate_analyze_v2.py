"""Slate analysis v2 — separates WINS_ML from COVERS_SPREAD.

The v1 script had ONE 'side' column that answered "who covers the
spread" but was labeled ambiguously enough that ML picks were built
off it. That burned a public DET ML rec on 7/27 where 6/6 lens agreed
'DET' but actually all lens had BAL winning narrowly (DET covers +1.5,
doesn't win ML).

Fix: every lens now produces TWO reads per game:
  - _ml_side:   sign of predicted margin (positive = home wins outright)
  - _rl_side:   sign of predicted margin + close_spread (positive = home covers)

Confluence counts and ladder are computed for each separately. Never
conflate the two again.

Usage: python _slate_analyze_v2.py <slate_YYYYMMDD.json>
"""
import json
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
from collections import Counter
from pathlib import Path


def _f(v):
    try: return float(v) if v is not None else None
    except: return None


def ml_side(margin):
    """Predicted ML winner. Positive margin = home wins outright."""
    if margin is None: return None
    return 'H' if margin > 0 else 'A'


def rl_side(margin, close_sp):
    """Predicted runline cover. margin + close_sp > 0 = home covers.

    Convention: close_sp negative = home fav laying, close_sp positive =
    home dog getting. Adding them tells you if home beats the spread:
      home fav -1.5 needs margin > 1.5 → margin + (-1.5) > 0
      home dog +1.5 needs margin > -1.5 → margin + 1.5 > 0
    """
    if margin is None or close_sp is None: return None
    return 'H' if (margin + close_sp) > 0 else 'A'


def mc_ml_side(mc):
    """MC's ML pick — based on p_home_win vs p_away_win."""
    if not mc: return None
    hw = mc.get('mc_p_home_win'); aw = mc.get('mc_p_away_win')
    if hw is None or aw is None: return None
    return 'H' if hw > aw else 'A'


def mc_rl_side(mc):
    """MC's RL pick — based on p_home_covers vs p_away_covers."""
    if not mc: return None
    hc = mc.get('mc_p_home_covers'); ac = mc.get('mc_p_away_covers')
    if hc is None or ac is None: return None
    return 'H' if hc > ac else 'A'


def total_side(t, close_t):
    if t is None or close_t is None: return None
    return 'O' if t > close_t else 'U'


def mc_total(mc, close_t):
    if not mc or close_t is None: return None
    ov = mc.get('mc_p_over'); mt = mc.get('mc_mean_total')
    # Prefer mean_total vs line (matches other lenses); fall back to p_over probability
    if mt is not None: return 'O' if mt > close_t else 'U'
    if ov is not None: return 'O' if ov > 0.5 else 'U'
    return None


def analyze_game(g):
    sp = _f(g.get('close_spread'))
    tot = _f(g.get('close_total'))
    mc = g.get('mc_probs') or {}

    # Get each lens's predicted margin (positive = home wins by that much)
    lens_margins = {
        'panel': _f(g.get('panel_margin')),
        'jerry': _f(g.get('jerry_spread')),
        'v3':    _f(g.get('v3_spread')),
        'v4':    _f(g.get('v4_spread')),
        'mc':    _f(mc.get('mc_expected_margin')),
    }
    # ML picks (who wins outright)
    ml_picks = {name: ml_side(m) for name, m in lens_margins.items()}
    # RL picks (who covers spread)
    rl_picks = {name: rl_side(m, sp) for name, m in lens_margins.items()}
    # MC pick has dedicated probability fields — use those where available for higher fidelity
    if mc:
        ml_picks['mc'] = mc_ml_side(mc) or ml_picks['mc']
        rl_picks['mc'] = mc_rl_side(mc) or rl_picks['mc']
    # Confluence net direction (from signal_confluence_net)
    conf = g.get('conf_net') or 0
    conf_side = 'H' if conf > 0 else 'A' if conf < 0 else None

    # Total picks
    lens_totals = {
        'panel': _f(g.get('panel_total')),
        'jerry': _f(g.get('jerry_total')),
        'v3':    _f(g.get('v3_total')),
        'v4':    _f(g.get('v4_total')),
    }
    total_picks = {name: total_side(t, tot) for name, t in lens_totals.items()}
    total_picks['mc'] = mc_total(mc, tot)

    # Aggregate: ML side (5 lens + confluence = 6 votes)
    ml_votes = [v for v in list(ml_picks.values()) + [conf_side] if v]
    rl_votes = [v for v in list(rl_picks.values()) + [conf_side] if v]
    total_votes = [v for v in total_picks.values() if v]

    ml_c = Counter(ml_votes)
    rl_c = Counter(rl_votes)
    tot_c = Counter(total_votes)

    ml_lead = list(ml_c.most_common(1)[0]) if ml_c else ['-', 0]
    rl_lead = list(rl_c.most_common(1)[0]) if rl_c else ['-', 0]
    tot_lead = list(tot_c.most_common(1)[0]) if tot_c else ['-', 0]

    g['_ml_picks'] = ml_picks
    g['_ml_picks']['conf'] = conf_side
    g['_rl_picks'] = rl_picks
    g['_rl_picks']['conf'] = conf_side
    g['_total_picks'] = total_picks
    g['_ml_lead'] = ml_lead
    g['_rl_lead'] = rl_lead
    g['_total_lead'] = tot_lead
    g['_lens_margins'] = lens_margins
    return g


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\gomez\AppData\Local\Temp\claude\c--Users-gomez-SweatShop\785f60eb-2896-47d0-b93c-5a98f036e862\scratchpad\slate_727.json'
    with open(path) as f:
        d = json.load(f)
    for g in d['games']:
        analyze_game(g)
    with open(path, 'w') as f:
        json.dump(d, f, default=str, indent=2)

    ranked = sorted(d['games'], key=lambda g: -(g['_ml_lead'][1] + g['_rl_lead'][1] + g['_total_lead'][1]))
    print('== ML vs RL confluence — where the two DIVERGE is the key check ==\n')
    for g in ranked:
        away = g['away']; home = g['home']
        ml_side, ml_n = g['_ml_lead']
        rl_side, rl_n = g['_rl_lead']
        tot_side, tot_n = g['_total_lead']
        div_flag = ' ⚠ DIVERGE' if ml_side != rl_side and ml_side != '-' and rl_side != '-' else ''
        print(f'{away[:22]:22s} @ {home[:22]:22s}  ML:{ml_side}({ml_n}/6)  RL:{rl_side}({rl_n}/6)  TOT:{tot_side}({tot_n}/5){div_flag}')


if __name__ == '__main__':
    main()
