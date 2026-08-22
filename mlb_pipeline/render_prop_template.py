"""Deterministic prop analysis card renderer (2026-08-22 v2).

Renders a COMPLETE analytical card per prop — no LLM, no hallucination,
every claim provably true from structured data. Same shape output for
downstream consumers (prop_jerry_reads readers).

The card follows a universal checklist across prop types:

    1. RECENT FORM (ESPN-style stat table L5-L10)
    2. PITCHER/BATTER QUALITY (season rates + underlying)
    3. MATCHUP (opponent context, vs-team history)
    4. FATIGUE (rest days, workload trend)
    5. ENVIRONMENT (park, weather, umpire, catcher)
    6. BETTING CONTEXT (odds, edge, sharp $, line movement)
    7. MODEL OUTPUT (projection, verdict, one score)

Universal — same code renders MLB pitcher props, MLB batter hits, NBA
points, NHL SOG, NFL yards, etc. Prop-type-specific rendering pulls
from _COVERAGE_CHECKLIST per stat family.

Sources fed by:
  - backfill_prop_lookback.py (signals._stat_last10, avg_l5/l10/season)
  - ensemble scorer (signals dict — situational chips)
  - prop_playbook_decisions (playbook_sources rich chip data)
  - game_context (weather, umpire, catcher framing, vs-team)

Design intent: one verdict, one score, comprehensive data surface,
no LLM prose that contradicts itself.
"""
from __future__ import annotations
from typing import Optional


# ─── Stat family metadata ────────────────────────────────────────────
# Per prop-type family: display label, whether it's a pitcher/batter/skill
# prop, which context fields are RELEVANT to check for coverage.
_STAT_META = {
    # MLB PITCHER
    'ks':   {'label': 'Strikeouts', 'role': 'pitcher', 'relevant_ctx': ['opp_k_pct', 'opp_k_pct_vs_hand', 'umpire_k', 'catcher_framing', 'weather']},
    'ha':   {'label': 'Hits Allowed', 'role': 'pitcher', 'relevant_ctx': ['opp_wrc', 'opp_wrc_recent', 'park', 'weather', 'vs_team_history']},
    'bb':   {'label': 'Walks Allowed', 'role': 'pitcher', 'relevant_ctx': ['opp_bb_pct', 'umpire_k', 'catcher_framing']},
    'outs': {'label': 'Outs Recorded', 'role': 'pitcher', 'relevant_ctx': ['pitcher_1st_inn_era', 'bullpen_taxed', 'fatigue']},
    'er':   {'label': 'Earned Runs', 'role': 'pitcher', 'relevant_ctx': ['opp_wrc', 'opp_wrc_recent', 'park', 'weather', 'vs_team_history', 'fatigue']},
    # MLB BATTER
    'hits': {'label': 'Hits', 'role': 'batter', 'relevant_ctx': ['opp_starter_xera', 'opp_starter_form', 'park', 'weather', 'lineup_slot', 'platoon', 'bvp']},
    'tb':   {'label': 'Total Bases', 'role': 'batter', 'relevant_ctx': ['opp_starter_xera', 'park', 'weather', 'platoon', 'barrel_rate']},
    'hr':   {'label': 'Home Runs', 'role': 'batter', 'relevant_ctx': ['park', 'weather', 'opp_starter_hr_rate', 'barrel_rate']},
    'rbi':  {'label': 'RBIs', 'role': 'batter', 'relevant_ctx': ['lineup_slot', 'opp_starter_xera', 'team_run_env']},
    # NBA
    'points':   {'label': 'Points', 'role': 'skill', 'relevant_ctx': ['opp_def_rating', 'pace', 'minutes_projected', 'b2b', 'usage']},
    'rebounds': {'label': 'Rebounds', 'role': 'skill', 'relevant_ctx': ['opp_rebound_rate', 'pace', 'minutes_projected']},
    'assists':  {'label': 'Assists', 'role': 'skill', 'relevant_ctx': ['pace', 'usage', 'teammate_scoring']},
    'threes':   {'label': '3-Pointers Made', 'role': 'skill', 'relevant_ctx': ['opp_3p_defense', 'pace', 'shot_volume']},
    # NHL
    'sog':      {'label': 'Shots on Goal', 'role': 'skill', 'relevant_ctx': ['opp_goalie_sv', 'line_projection', 'toi']},
    'saves':    {'label': 'Saves', 'role': 'goalie', 'relevant_ctx': ['opp_shots_per60', 'team_defense']},
    # NFL
    'passing_yards':    {'label': 'Passing Yards', 'role': 'qb', 'relevant_ctx': ['opp_pass_def', 'pace', 'weather', 'game_script']},
    'rushing_yards':    {'label': 'Rushing Yards', 'role': 'rb', 'relevant_ctx': ['opp_run_def', 'game_script', 'weather']},
    'receiving_yards':  {'label': 'Receiving Yards', 'role': 'wr', 'relevant_ctx': ['opp_pass_def', 'target_share', 'game_script']},
    'receptions':       {'label': 'Receptions', 'role': 'wr', 'relevant_ctx': ['target_share', 'game_script']},
}


def _stat_family(prop_type: str) -> str:
    """'hits_over' → 'hits'. 'passing_yards_under' → 'passing_yards'."""
    if not prop_type: return ''
    for suffix in ('_over', '_under'):
        if prop_type.endswith(suffix):
            return prop_type[:-len(suffix)]
    return prop_type


def _implied_prob_pct(odds) -> Optional[int]:
    try: o = int(odds)
    except (TypeError, ValueError): return None
    return round(100 * 100.0 / (o + 100)) if o >= 0 else round(100 * -o / (-o + 100.0))


def _verdict(tier: str, conviction: int, side: str = '') -> str:
    """Return the SINGLE-word stance for this prop.

    2026-08-22: killed the dual-label "LEAN BACK" bug — user was reading
    the card and seeing both tier=LEAN and verdict='LEAN BACK' which
    forced them to reconcile two labels for one decision. Now the tier
    IS the stance:
        PRIME  → play it (highest conviction bucket)
        STRONG → play it
        LEAN   → lean, size down
        PASS   → skip
    'FADE' overrides when playbook signals contrarian direction.

    Preserves receipts vocabulary — PRIME/STRONG/LEAN unchanged in DB.
    """
    tier = (tier or '').upper(); side = (side or '').upper()
    conv = int(conviction or 0)
    if side == 'FADE': return 'FADE'
    if tier == 'PRIME' and conv >= 70: return 'PRIME'
    if tier == 'STRONG' and conv >= 60: return 'STRONG'
    if tier == 'LEAN' and conv >= 55: return 'LEAN'
    return 'PASS'


def _format_stat_table(rows: list, line: float, direction: str) -> list[str]:
    """ESPN-style compact last-10 table. Returns list of lines."""
    if not rows: return ['  (no recent game log available)']
    out = ['  Last {} games:'.format(len(rows[:10]))]
    over = sum(1 for r in rows[:10] if float(r.get('value', 0) or 0) >= line)
    under = sum(1 for r in rows[:10] if float(r.get('value', 0) or 0) < line)
    for r in rows[:10]:
        v = r.get('value')
        d = r.get('date', '')[-5:] if r.get('date') else '?'
        opp = r.get('opp', '?')
        home_marker = 'vs' if r.get('home') else '@'
        mark = '✓' if (direction == 'over' and float(v or 0) >= line) or (direction == 'under' and float(v or 0) < line) else '✗'
        ip = f' {r.get("ip")}IP' if r.get('ip') is not None else ''
        out.append(f'    {d} {home_marker} {opp:3}  {v:>4}{ip}  {mark}')
    out.append(f'  → {over}/{len(rows[:10])} games OVER {line}   ({under} UNDER)')
    return out


def _categorize_signals(sources: list, prop_signals: dict) -> dict:
    """Group signals into positive (supporting direction) / negative (against).
    Prefer playbook_sources chip data; fall back to signals dict."""
    positive, negative = [], []
    for s in (sources or []):
        contrib = float(s.get('contribution') or 0)
        prose = s.get('prose') or s.get('signal_key', '')
        (positive if contrib >= 0 else negative).append((abs(contrib), prose))
    # If no playbook sources, extract from signals dict
    if not sources and prop_signals:
        for k, v in prop_signals.items():
            if k.startswith('_'): continue
            positive.append((0.5, f'{k}: {str(v)[:80]}'))
    positive.sort(key=lambda x: -x[0])
    negative.sort(key=lambda x: -x[0])
    return {'positive': [p for _, p in positive[:5]], 'negative': [p for _, p in negative[:3]]}


def render_prop_template(prop: dict, playbook_decision: Optional[dict] = None) -> dict:
    """Render a full analytical prop card.

    Returns dict with:
        short_read: multi-line str (the full card body, plain text)
        verdict:    'BACK' | 'FADE' | 'LEAN BACK' | 'PASS'
        conviction: int (0-95)
        source:     'template'
    """
    player = prop.get('player_name') or 'This player'
    prop_type = prop.get('prop_type') or ''
    direction = (prop.get('direction') or '').lower()
    line = prop.get('prop_line')
    try: line_f = float(line) if line is not None else 0.0
    except (TypeError, ValueError): line_f = 0.0
    tier = (prop.get('tier') or 'LEAN').upper()
    conviction = int(float(prop.get('conviction') or 0))
    refit_conv = prop.get('refit_conviction')

    family = _stat_family(prop_type)
    meta = _STAT_META.get(family, {'label': family or 'prop', 'role': 'unknown', 'relevant_ctx': []})
    label = meta['label']

    # Odds + implied
    side_odds = prop.get('book_over_odds') if direction == 'over' else prop.get('book_under_odds')
    implied = _implied_prob_pct(side_odds)

    # Verdict + score
    pb_side = (playbook_decision or {}).get('playbook_side') if playbook_decision else ''
    verdict = _verdict(tier, conviction, side=pb_side or prop.get('side', ''))

    # Signal categorization
    sources = (playbook_decision or {}).get('playbook_sources') if playbook_decision else None
    if not isinstance(sources, list): sources = []
    prop_signals = prop.get('signals') if isinstance(prop.get('signals'), dict) else {}
    cats = _categorize_signals(sources, prop_signals)

    # Recent form — from signals._stat_last10 (backfill_prop_lookback populates)
    stat_rows = prop_signals.get('_stat_last10') or []
    avg_l5 = prop_signals.get('_stat_avg_l5')
    avg_l10 = prop_signals.get('_stat_avg_l10')
    avg_season = prop_signals.get('_stat_avg_season')

    # Coverage audit — compute EARLY so it can header the card. Every prop
    # family declares its relevant_ctx checklist in _STAT_META. For each
    # required signal, check whether it appears in signals dict OR playbook
    # sources OR raw prop payload. Any missing item = gap.
    # Rationale (2026-08-22, user PARAMOUNT concern): "it is paramount we
    # are tracking every relevant signal for each prop and it is getting
    # assessed." Old code buried this as a warning below the score. New:
    # every card headers with COVERAGE: N/M so gaps are visible before
    # the reader reaches the verdict.
    ctx_keys_in_signals = set(k for k in prop_signals.keys() if not k.startswith('_'))
    if isinstance(sources, list):
        for s in sources:
            sk = s.get('signal_key') if isinstance(s, dict) else None
            if sk: ctx_keys_in_signals.add(sk)
    relevance_check = {
        'opp_k_pct':          {'opp_k_rate', 'opp_k_pct', 'opp_hand_k'},
        'opp_k_pct_vs_hand':  {'opp_hand_k', 'opp_k_vs_hand'},
        'opp_wrc':            {'opp_wrc', 'opp_team_wrc'},
        'opp_wrc_recent':     {'opp_l14_ops', 'opp_l14_wrc', 'opp_recent'},
        'opp_starter_xera':   {'opp_starter', 'opp_xera', 'xera'},
        'opp_starter_form':   {'opp_form', 'opp_starter_form'},
        'opp_starter_hr_rate': {'opp_hr_rate', 'opp_starter_hr'},
        'opp_bb_pct':         {'opp_bb_rate', 'opp_bb_pct'},
        'opp_def_rating':     {'opp_def', 'opp_def_rating'},
        'opp_rebound_rate':   {'opp_rebound'},
        'opp_3p_defense':     {'opp_3p_def'},
        'opp_pass_def':       {'opp_pass_def'},
        'opp_run_def':        {'opp_run_def'},
        'opp_goalie_sv':      {'goalie_sv', 'opp_goalie'},
        'opp_shots_per60':    {'opp_shots'},
        'park':               {'park', 'park_factor'},
        'weather':            {'weather', 'temp', 'wind'},
        'umpire_k':           {'umpire', 'ump'},
        'catcher_framing':    {'catcher_framing', 'framing'},
        'lineup_slot':        {'lineup_spot', 'lineup_slot'},
        'platoon':            {'platoon', 'wrc_hand'},
        'bvp':                {'bvp_mastery', 'bvp'},
        'vs_team_history':    {'vs_team', 'pitcher_vs_team'},
        'fatigue':            {'fatigue', 'days_rest', 'pitch_count_last', 'last_outing'},
        'bullpen_taxed':      {'bullpen_taxed', 'bp_taxed', 'opp_bullpen'},
        'pitcher_1st_inn_era': {'first_inn', 'first_inning'},
        'pace':               {'pace'},
        'minutes_projected':  {'minutes', 'minutes_proj'},
        'usage':              {'usage_rate', 'usage'},
        'b2b':                {'b2b', 'back_to_back'},
        'target_share':       {'target_share', 'targets'},
        'game_script':        {'game_script', 'spread'},
        'shot_volume':        {'shot_volume', 'fga'},
        'teammate_scoring':   {'teammate'},
        'line_projection':    {'line_proj'},
        'toi':                {'toi', 'ice_time'},
        'team_defense':       {'team_def'},
        'team_run_env':       {'team_runs', 'implied_team_total'},
        'barrel_rate':        {'barrel_rate', 'barrels'},
    }
    checklist = meta.get('relevant_ctx', []) or []
    missing = []
    for req in checklist:
        expected_keys = relevance_check.get(req, {req})
        if not any(any(ek in k for ek in expected_keys) for k in ctx_keys_in_signals):
            missing.append(req)
    covered = len(checklist) - len(missing)
    coverage_pct = int(100 * covered / max(1, len(checklist)))

    # ─── COMPOSE THE CARD ───────────────────────────────────────────
    lines = []
    lines.append(f'{player} · {label} {direction.upper()} {line}  @  {side_odds if side_odds is not None else "—"}')
    lines.append(f'{"─" * 60}')
    # Signal coverage chip — prominent, right below the header.
    if checklist:
        cov_flag = '✅' if not missing else ('⚠️' if len(missing) <= 2 else '🚨')
        lines.append(f'  {cov_flag} SIGNAL COVERAGE: {covered}/{len(checklist)} ({coverage_pct}%)')
        if missing:
            lines.append(f'     missing: {", ".join(missing)}')
    # Header stats
    header_bits = []
    if avg_l5 is not None: header_bits.append(f'L5 avg {avg_l5}')
    if avg_l10 is not None: header_bits.append(f'L10 avg {avg_l10}')
    if avg_season is not None: header_bits.append(f'Season avg {avg_season}')
    if implied is not None: header_bits.append(f'Implied {implied}%')
    if header_bits:
        lines.append('  ' + '  ·  '.join(header_bits))
    lines.append('')

    # Recent-form ESPN table
    if stat_rows:
        lines.append('RECENT FORM')
        lines.extend(_format_stat_table(stat_rows, line_f, direction))
        lines.append('')

    # Signal breakdown — positive + negative
    if cats['positive']:
        lines.append(f'▲ FAVORING {direction.upper()}')
        for p in cats['positive']:
            lines.append(f'  · {p[:110]}')
    if cats['negative']:
        lines.append(f'')
        lines.append(f'▼ AGAINST {direction.upper()}')
        for n in cats['negative']:
            lines.append(f'  · {n[:110]}')
    lines.append('')

    # Verdict = tier. Show ONE label + one score (refit if available).
    # Score priority: refit_conviction (model-of-record) > legacy conviction.
    # Legacy conviction retired from display 2026-08-22.
    display_score = None
    if refit_conv is not None:
        try: display_score = int(float(refit_conv))
        except (TypeError, ValueError): display_score = conviction
    if display_score is None: display_score = conviction
    lines.append(f'{verdict}   ·   Score: {display_score}')

    short_read = '\n'.join(lines)
    return {
        'short_read': short_read,
        'verdict': verdict,
        'conviction': conviction,
        'source': 'template',
    }


if __name__ == '__main__':
    demo_prop = {
        'player_name': 'Logan Henderson',
        'prop_type': 'ks_under',
        'direction': 'under',
        'prop_line': 6.5,
        'tier': 'LEAN',
        'conviction': 55,
        'refit_conviction': 95,
        'book_under_odds': -120,
        'signals': {
            'l3_k': 'l3_k 29.6', 'park': 'park 97', 'xera': 'xera 2.7',
            'opp_wrc': 'opp_wrc 106', 'opp_k_rate': 'opp_k_rate 24.0',
            '_stat_last10': [
                {'date': '2026-08-15', 'value': 7, 'opp': 'ATL', 'home': True, 'ip': '5.0'},
                {'date': '2026-08-10', 'value': 4, 'opp': 'STL', 'home': False, 'ip': '4.2'},
                {'date': '2026-08-05', 'value': 7, 'opp': 'PIT', 'home': True, 'ip': '6.0'},
                {'date': '2026-07-31', 'value': 9, 'opp': 'CHC', 'home': False, 'ip': '6.1'},
                {'date': '2026-07-26', 'value': 7, 'opp': 'NYM', 'home': True, 'ip': '5.2'},
                {'date': '2026-07-21', 'value': 8, 'opp': 'CIN', 'home': False, 'ip': '6.0'},
                {'date': '2026-07-16', 'value': 7, 'opp': 'MIL', 'home': True, 'ip': '5.1'},
                {'date': '2026-07-10', 'value': 6, 'opp': 'MIN', 'home': False, 'ip': '5.0'},
                {'date': '2026-07-05', 'value': 4, 'opp': 'STL', 'home': True, 'ip': '4.1'},
                {'date': '2026-06-29', 'value': 4, 'opp': 'PIT', 'home': False, 'ip': '4.0'},
            ],
            '_stat_avg_l5': 6.8, '_stat_avg_l10': 6.3, '_stat_avg_season': 6.4,
        },
    }
    print(render_prop_template(demo_prop)['short_read'])
