"""Deterministic prop-synth template renderer (2026-08-22).

Companion to `generate_prop_jerry_synthesis.py`. Where the LLM path
produces rich prose for PRIME/STRONG props (~60 Claude calls/night),
this template path produces the same output shape for below-gate props
(LEAN + smaller tiers) using ONLY structured data from the ensemble:

    prop.tier, prop.conviction, prop.direction, prop.prop_line
    prop.signals (dict of signal_key → contribution)
    prop_playbook_decisions.playbook_sources (list of chip dicts)

Zero Claude calls. Zero hallucination risk. Every claim in the prose
is provably true because it comes from the signals dict itself. Runs
in milliseconds per prop.

Output shape matches LLM synth so downstream consumers (`prop_jerry_reads`
readers, `apply_refit_verdict_override`, `apply_fade_type_discipline`,
grader) don't need branching logic:

    {
        'short_read': str,   # 40-60 word English narration
        'verdict':    'BACK' | 'FADE' | 'PASS',
        'conviction': int (0-100),
        'source':     'template',  # marker distinguishing from LLM path
    }

Usage:
    from render_prop_template import render_prop_template
    result = render_prop_template(prop, playbook_decision)
    # write result to prop_jerry_reads with source='template'

Design intent per user directive 2026-08-22:
    "Jerry synthesis on games; models/signals as reason for O/U back
     on props across all sports."
"""
from __future__ import annotations

_MARKET_LABEL = {
    'ks':    'strikeouts',
    'outs':  'outs recorded',
    'er':    'earned runs',
    'ha':    'hits allowed',
    'bb':    'walks allowed',
    'hits':  'hits',
    'tb':    'total bases',
    'rbi':   'RBIs',
    'runs':  'runs',
    'hr':    'home runs',
    'sb':    'stolen bases',
    'pts':   'points',
    'reb':   'rebounds',
    'ast':   'assists',
    'threes':'3-pointers',
    'sog':   'shots on goal',
    'saves': 'saves',
    'passing_yds': 'passing yards',
    'rushing_yds': 'rushing yards',
    'receiving_yds': 'receiving yards',
    'rec':   'receptions',
}


def _readable_prop_type(prop_type: str) -> str:
    """'outs_over' → 'outs recorded'."""
    if not prop_type:
        return 'this prop'
    base = prop_type.rsplit('_', 1)[0] if prop_type.endswith(('_over', '_under')) else prop_type
    return _MARKET_LABEL.get(base, base.replace('_', ' '))


def _verdict_from_tier_conviction(tier: str, conviction: int, side: str = '') -> str:
    """Map ensemble output to Jerry verdict enum.

    Legacy LLM path outputs BACK/FADE/PASS. We mirror it deterministically:
    - PRIME/STRONG + high conviction → BACK
    - Explicit fade side from calibration → FADE
    - LEAN with moderate conviction → BACK (light)
    - Below LEAN or low conviction → PASS
    Downstream apply_refit_verdict_override can still flip.
    """
    tier = (tier or '').upper()
    side = (side or '').upper()
    conv = int(conviction or 0)

    if side == 'FADE':
        return 'FADE'
    if tier == 'PRIME' and conv >= 70:
        return 'BACK'
    if tier == 'STRONG' and conv >= 60:
        return 'BACK'
    if tier == 'LEAN' and conv >= 55:
        return 'BACK'
    return 'PASS'


def _top_signals(sources: list, side_filter: str | None = None, top_n: int = 3) -> list:
    """Return top-N sources by |contribution|, optionally filtered by BACK/FADE."""
    if not sources:
        return []
    if side_filter:
        filtered = [s for s in sources if (s.get('side') or '').upper() == side_filter.upper()]
    else:
        filtered = list(sources)
    return sorted(filtered, key=lambda s: -abs(float(s.get('contribution') or 0)))[:top_n]


def _signal_prose(source: dict) -> str:
    """Extract the human-readable chip text from a signal source row.

    Falls back through prose → signal_key humanized if prose missing.
    """
    prose = source.get('prose') or source.get('display_prose')
    if prose:
        return prose.strip()
    key = source.get('signal_key') or ''
    return key.replace('_', ' ')


def _implied_prob_pct(odds) -> str:
    """American odds → implied probability percentage string."""
    try:
        o = int(odds)
    except (TypeError, ValueError):
        return ''
    if o >= 0:
        p = 100.0 / (o + 100)
    else:
        p = -o / (-o + 100.0)
    return f'{round(p * 100)}%'


def render_prop_template(prop: dict, playbook_decision: dict | None = None) -> dict:
    """Render a deterministic Jerry-shaped synthesis for one prop.

    Inputs:
        prop: row from *_pipeline_props (needs tier, conviction, direction,
              prop_line, player_name, prop_type, signals, odds cols)
        playbook_decision: matching prop_playbook_decisions row, or None.
                          If provided, playbook_sources is the source-of-truth
                          for signal chips.

    Returns dict shaped exactly like LLM synth output.
    """
    player = prop.get('player_name') or 'This player'
    prop_type = prop.get('prop_type') or ''
    direction = (prop.get('direction') or '').lower()
    line = prop.get('prop_line')
    tier = (prop.get('tier') or 'LEAN').upper()
    conviction = int(float(prop.get('conviction') or 0))
    readable_prop = _readable_prop_type(prop_type)

    # Prefer playbook_sources when available (richer chip data). Fall back
    # to signals dict entries reformatted as pseudo-sources.
    sources = []
    if playbook_decision and isinstance(playbook_decision.get('playbook_sources'), list):
        sources = playbook_decision['playbook_sources']
    else:
        # Convert signals dict into pseudo-source list where value is contribution
        raw_sig = prop.get('signals') or {}
        if isinstance(raw_sig, dict):
            for k, v in raw_sig.items():
                if k.startswith('_') or v is None:
                    continue
                try:
                    contrib = float(v)
                except (TypeError, ValueError):
                    continue
                sources.append({
                    'signal_key': k,
                    'contribution': contrib,
                    'side': 'BACK' if contrib >= 0 else 'FADE',
                    'prose': k.replace('_', ' '),
                })

    verdict = _verdict_from_tier_conviction(tier, conviction,
                                             side=prop.get('side') or (playbook_decision or {}).get('playbook_side', ''))

    # Compute BACK/FADE signal groups
    back_signals = _top_signals(sources, side_filter='BACK', top_n=3)
    fade_signals = _top_signals(sources, side_filter='FADE', top_n=2)
    n_back = len([s for s in sources if (s.get('side') or '').upper() == 'BACK'])
    n_fade = len([s for s in sources if (s.get('side') or '').upper() == 'FADE'])

    # Odds/implied probability
    side_odds = prop.get('book_over_odds') if direction == 'over' else prop.get('book_under_odds')
    implied = _implied_prob_pct(side_odds)

    # ─── Compose the short read ─────────────────────────────────────
    parts = []
    line_txt = f'{direction.title()} {line}' if line is not None else direction.title()

    if verdict == 'BACK' and back_signals:
        # "3 signals BACK Player Over 5.5 outs: opp K% 28%, L10 hit rate 7/10, low park total."
        chip_prose = ', '.join(_signal_prose(s) for s in back_signals)
        lead = f'{n_back} signal{"s" if n_back != 1 else ""} back {player} {line_txt} {readable_prop}: {chip_prose}.'
        parts.append(lead)
        if fade_signals:
            fade_prose = ', '.join(_signal_prose(s) for s in fade_signals)
            parts.append(f'Counter: {fade_prose}.')
    elif verdict == 'FADE' and fade_signals:
        chip_prose = ', '.join(_signal_prose(s) for s in fade_signals)
        parts.append(f'Fade {player} {line_txt} {readable_prop}: {chip_prose}.')
        if back_signals:
            back_prose = _signal_prose(back_signals[0])
            parts.append(f'Only support: {back_prose}.')
    elif verdict == 'PASS':
        if back_signals and fade_signals:
            parts.append(f'{player} {line_txt} {readable_prop}: {n_back} back / {n_fade} fade — no edge.')
        elif sources:
            parts.append(f'{player} {line_txt} {readable_prop}: mixed signals, no clean edge.')
        else:
            parts.append(f'{player} {line_txt} {readable_prop}: insufficient signal support.')
    else:
        # Verdict has weight but no supporting sources — describe from tier only
        parts.append(f'{player} {line_txt} {readable_prop}: {tier} tier from ensemble ({conviction} conviction).')

    # Tier + conviction footer
    footer_bits = [f'{tier} tier', f'conviction {conviction}']
    if implied:
        footer_bits.append(f'market implies {implied}')
    parts.append('· '.join(footer_bits) + '.')

    short_read = ' '.join(parts).strip()

    return {
        'short_read': short_read,
        'verdict': verdict,
        'conviction': conviction,
        'source': 'template',
    }


if __name__ == '__main__':
    # Quick sanity render
    demo_prop = {
        'player_name': 'Willi Castro',
        'prop_type': 'hits_over',
        'direction': 'over',
        'prop_line': 0.5,
        'tier': 'PRIME',
        'conviction': 84,
        'book_over_odds': -140,
        'signals': {'opp_k_low': 0.12, 'l10_hit_rate': 0.7, 'park_factor_low': -0.05},
    }
    demo_pb = {
        'playbook_sources': [
            {'signal_key': 'opp_k_low', 'side': 'BACK', 'contribution': 0.85,
             'prose': 'Opp starter K% 18% (well below 22% avg)'},
            {'signal_key': 'l10_hit_rate', 'side': 'BACK', 'contribution': 0.72,
             'prose': '7/10 last 10 games'},
            {'signal_key': 'park_factor', 'side': 'FADE', 'contribution': -0.18,
             'prose': 'Coors depressor factor'},
        ]
    }
    print(render_prop_template(demo_prop, demo_pb))
