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

    2026-08-22 v2: verdict = tier verbatim. Do NOT re-threshold against
    conviction — the juice-trap gate already down-scores conv from 55 to
    52 which used to fall through to PASS while the DB tier stayed LEAN.
    Result: card showed tier=LEAN AND verdict=PASS AND refit=100 — three
    contradictory labels. Now: tier is the authority, period. If tier
    got demoted, DB tier is demoted; verdict follows.
    """
    tier = (tier or '').upper(); side = (side or '').upper()
    if side == 'FADE': return 'FADE'
    if tier in ('PRIME', 'STRONG', 'LEAN'): return tier
    return 'PASS'


def _clean_prose(s: str) -> str:
    """Prose scrubber — kill jargon, sentence-case, humanize.

    Input signal prose comes from playbook_sources / signal registry and
    is stat-notation dense: "career BAA vs opp >= .270 (15+ IP) — gets hit".
    Output: "Gets hit by this lineup historically (.270 career BAA)".

    Rules:
      - Strip parenthetical sample thresholds "(15+ IP)", "(2+ starts)"
      - Convert ">= .270" / "<= 3.0" comparison syntax to a stat
      - Capitalize first character
      - No trailing em-dash phrase (redundant with the stat)
    """
    import re
    if not s: return s
    s = str(s).strip()
    # Drop sample-size parentheticals
    s = re.sub(r'\s*\((?:\d+\+?\s*(?:IP|starts|PA|AB|games|min|snaps|shots|K|BB))\)\s*', ' ', s)
    # Comparison shorthand — "K% <= 20" → "K% 20% or lower"; ">= X" → "X or higher"
    s = re.sub(r'>=\s*(\.\d+|\d+(?:\.\d+)?)', r'\1+', s)
    s = re.sub(r'<=\s*(\.\d+|\d+(?:\.\d+)?)', r'\1 or lower', s)
    # Common jargon substitutions
    s = re.sub(r'\bBAA\b', 'batting avg', s)
    s = re.sub(r'\bopp lineup\b', 'opposing lineup', s)
    s = re.sub(r'\bwRC\+\b', 'wRC+', s)  # keep — well-known metric
    s = re.sub(r'\bxERA\b', 'xERA', s)   # keep — well-known metric
    s = re.sub(r'\bATS\b', 'ATS', s)     # keep
    # Collapse double spaces from prior substitutions
    s = re.sub(r'\s+', ' ', s).strip()
    # Sentence case: uppercase first letter without touching acronyms
    if s and s[0].isalpha() and s[0].islower():
        s = s[0].upper() + s[1:]
    # Strip trailing period + em-dash noise
    s = s.rstrip('.').strip()
    return s


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


def _categorize_signals(sources: list, prop_signals: dict, prop_direction: str = '',
                         playbook_side: str = '') -> dict:
    """Group signals into FOR / AGAINST the CARD's shown direction.

    2026-08-22 v2: previously always labeled "FAVORING <prop_direction>"
    regardless of which side the playbook actually supported. Example:
    prop=Weathers HA UNDER, playbook_side=OVER (FADE the under), signals
    with positive contribution supported OVER — but card labeled them
    "FAVORING UNDER" (wrong).

    Now: contribution sign is relative to playbook_side. Card is oriented
    to prop_direction (what the user sees the line on). If playbook_side
    disagrees with prop_direction, we flip the label — positive
    contribution supporting playbook_side is actually AGAINST the card's
    shown direction.
    """
    pd = (prop_direction or '').upper()
    ps = (playbook_side or '').upper()
    # Flip is on when playbook fades the shown direction. Two encodings:
    #   ps='FADE' — playbook_decisions convention (most common)
    #   ps='OVER'/'UNDER' with pd opposite — legacy encoding
    flip = (ps == 'FADE') or (
        pd in ('OVER', 'UNDER') and ps in ('OVER', 'UNDER') and pd != ps
    )
    for_side, against_side = [], []
    for s in (sources or []):
        contrib = float(s.get('contribution') or 0)
        prose = _clean_prose(s.get('prose') or s.get('signal_key', ''))
        supports_shown = (contrib >= 0) ^ flip  # XOR: flip inverts
        (for_side if supports_shown else against_side).append((abs(contrib), prose))
    # Fallback for props without playbook chips
    if not sources and prop_signals:
        for k, v in prop_signals.items():
            if k.startswith('_'): continue
            prose = _clean_prose(str(v) if isinstance(v, str) and len(str(v)) > len(k) else f'{k.replace("_", " ")}')
            for_side.append((0.5, prose))
    for_side.sort(key=lambda x: -x[0])
    against_side.sort(key=lambda x: -x[0])
    return {'positive': [p for _, p in for_side[:5]], 'negative': [p for _, p in against_side[:3]]}


def _coverage_from_ctx(checklist: list, ctx: dict, prop_signals: dict, sources: list) -> tuple[int, list]:
    """Coverage-check every checklist item against RAW DATA in the ctx row.

    2026-08-22: the old check looked for fired signal keys, which
    under-reports coverage — a K-neutral umpire never triggers a 'umpire'
    signal even though the data was fetched and evaluated. That produced
    false gap warnings on 4/5 items for a typical MLB pitcher prop.

    New rule: coverage = raw data reachable, not signal fired. Check ctx
    row for the field feeding each checklist item. Fall back to signal
    key presence when ctx wasn't provided.
    """
    ctx = ctx or {}
    sig_keys = set(k for k in (prop_signals or {}).keys() if not k.startswith('_'))
    if isinstance(sources, list):
        for s in sources:
            sk = s.get('signal_key') if isinstance(s, dict) else None
            if sk: sig_keys.add(sk)
    # ctx field(s) that satisfy each checklist item — "data was available"
    ctx_check = {
        'opp_k_pct':          ['home_team_k_pct', 'away_team_k_pct'],
        'opp_k_pct_vs_hand':  ['home_ops_vs_opp_hand', 'away_ops_vs_opp_hand',
                               'home_wrc_vs_opp_hand', 'away_wrc_vs_opp_hand'],
        'opp_wrc':            ['home_wrc_plus', 'away_wrc_plus'],
        'opp_wrc_recent':     ['home_last10_runs_per_game', 'away_last10_runs_per_game'],
        'opp_starter_xera':   ['home_sp_xera', 'away_sp_xera'],
        'opp_starter_form':   ['home_pitcher_last_3_era', 'away_pitcher_last_3_era'],
        'opp_starter_hr_rate':['home_pitcher_hr_per_9', 'away_pitcher_hr_per_9'],
        'opp_bb_pct':         ['home_team_bb_pct', 'away_team_bb_pct'],
        'park':               ['park_run_factor', 'venue'],
        'weather':            ['temperature', 'wind_speed', 'wind_direction'],
        'umpire_k':           ['umpire', 'umpire_note'],
        'catcher_framing':    ['home_catcher_framing', 'away_catcher_framing'],
        'lineup_slot':        ['home_lineup', 'away_lineup', 'lineup_confirmed'],
        'platoon':            ['home_platoon_advantage', 'away_platoon_advantage'],
        'bvp':                ['bvp_mastery'],  # per-batter, not usually on ctx
        'vs_team_history':    ['home_pitcher_vs_team_era', 'away_pitcher_vs_team_era'],
        'fatigue':            ['home_days_rest', 'away_days_rest',
                               'home_pitcher_last_outing_pitches',
                               'away_pitcher_last_outing_pitches'],
        'bullpen_taxed':      ['home_bp_relievers_3d', 'away_bp_relievers_3d'],
        'pitcher_1st_inn_era':['home_first_inning_era', 'away_first_inning_era'],
        # Non-MLB fields — ctx keys vary by sport but same detection pattern
        'pace':               ['home_pace', 'away_pace'],
        'minutes_projected':  ['minutes_proj'],
        'usage':              ['usage_rate'],
        'b2b':                ['back_to_back'],
        'target_share':       ['target_share'],
        'game_script':        ['projected_spread', 'close_spread'],
        'shot_volume':        ['fga_projected'],
        'teammate_scoring':   ['teammate_scoring'],
        'line_projection':    ['line_proj'],
        'toi':                ['toi_projected'],
        'team_defense':       ['team_defense_rating'],
        'team_run_env':       ['home_runs_per_game', 'away_runs_per_game'],
        'barrel_rate':        ['home_team_barrel_pct', 'away_team_barrel_pct'],
        'opp_def_rating':     ['opp_def_rating'],
        'opp_rebound_rate':   ['opp_rebound_rate'],
        'opp_3p_defense':     ['opp_3p_defense'],
        'opp_pass_def':       ['opp_pass_def'],
        'opp_run_def':        ['opp_run_def'],
        'opp_goalie_sv':      ['opp_goalie_sv'],
        'opp_shots_per60':    ['opp_shots_per60'],
    }
    # Legacy fuzzy match on signal keys — used when ctx not provided
    sig_check = {
        'opp_k_pct':          ['opp_k_rate', 'opp_k_pct', 'opp_hand_k', 'opp_k_heavy', 'opp_k_artist'],
        'opp_k_pct_vs_hand':  ['opp_hand_k', 'opp_k_vs_hand'],
        'opp_wrc':            ['opp_wrc', 'opp_offense'],
        'opp_wrc_recent':     ['opp_l14', 'opp_recent', 'opp_contact_hot', 'opp_contact_cold'],
        'opp_starter_xera':   ['opp_starter', 'opp_xera', 'xera'],
        'opp_starter_form':   ['opp_form'],
        'park':               ['park'],
        'weather':            ['weather', 'temp', 'wind'],
        'umpire_k':           ['umpire', 'ump'],
        'catcher_framing':    ['framing'],
        'lineup_slot':        ['lineup_spot', 'lineup_slot'],
        'platoon':            ['platoon'],
        'bvp':                ['bvp'],
        'vs_team_history':    ['vs_team', 'pitcher_vs_team'],
        'fatigue':            ['fatigue', 'days_rest', 'last_outing', 'short_outing'],
        'bullpen_taxed':      ['bullpen_taxed', 'bp_taxed', 'opp_bullpen'],
        'pitcher_1st_inn_era':['first_inn', 'slow_start'],
    }
    missing = []
    for req in checklist:
        covered = False
        if ctx:
            for f in ctx_check.get(req, [req]):
                v = ctx.get(f)
                if v is not None and v != '':
                    covered = True; break
        if not covered:
            for kw in sig_check.get(req, [req]):
                if any(kw in k for k in sig_keys):
                    covered = True; break
        if not covered:
            missing.append(req)
    covered_n = len(checklist) - len(missing)
    return covered_n, missing


def render_prop_template(prop: dict, playbook_decision: Optional[dict] = None,
                          ctx: Optional[dict] = None) -> dict:
    """Render a full analytical prop card.

    Args:
        prop: prop row (dict). Must include prop_type, direction, prop_line,
              tier, conviction, refit_conviction, signals dict.
        playbook_decision: optional prop_playbook_decisions row (rich chips).
        ctx: optional mlb_game_context row for this prop's game_id. When
             provided, coverage check inspects raw data availability rather
             than only whether signals fired — kills false "missing" flags
             when data was fetched but the reading was neutral.

    Returns dict with:
        short_read: multi-line str (the full card body, plain text)
        verdict:    'PRIME' | 'STRONG' | 'LEAN' | 'PASS' | 'FADE'
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

    # Signal categorization — pass direction + playbook_side so FAVORING/
    # AGAINST labels correctly reflect what the model supports vs the
    # card's shown direction.
    sources = (playbook_decision or {}).get('playbook_sources') if playbook_decision else None
    if not isinstance(sources, list): sources = []
    prop_signals = prop.get('signals') if isinstance(prop.get('signals'), dict) else {}
    cats = _categorize_signals(sources, prop_signals, direction, pb_side)

    # Recent form — from signals._stat_last10 (backfill_prop_lookback populates)
    stat_rows = prop_signals.get('_stat_last10') or []
    avg_l5 = prop_signals.get('_stat_avg_l5')
    avg_l10 = prop_signals.get('_stat_avg_l10')
    avg_season = prop_signals.get('_stat_avg_season')

    # Coverage audit — every prop family declares a checklist in _STAT_META.
    # When ctx is provided, coverage = raw data availability (correct — a
    # neutral umpire is still covered). When ctx is absent, fall back to
    # signal-key fuzzy match (over-flags but better than nothing).
    checklist = meta.get('relevant_ctx', []) or []
    covered, missing = _coverage_from_ctx(checklist, ctx or {}, prop_signals, sources)
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

    # Signal breakdown — headers reflect model stance:
    #   verdict = FADE  → "Why fade the OVER" (opposite of shown)
    #   verdict = play  → "Why UNDER works" (same as shown)
    # 'positive' and 'negative' lists are always oriented to the card's
    # shown direction. When verdict is FADE, the model actually likes the
    # OPPOSITE side, so what looks "against the UNDER" is really the
    # model's supporting case — swap the labels.
    dir_label = direction.upper()
    opp_label = 'OVER' if direction == 'under' else 'UNDER'
    if verdict == 'FADE':
        # positive-for-shown-direction becomes "why the FADE risks" and
        # negative-for-shown-direction becomes "why FADE" (supports opposite)
        why_header, risk_header = f'Why FADE (take the {opp_label})', f'Why the {dir_label} could still hit'
        why_list, risk_list = cats['negative'], cats['positive']
    else:
        why_header, risk_header = f'Why {dir_label}', f'Why {dir_label} risks'
        why_list, risk_list = cats['positive'], cats['negative']
    if why_list:
        lines.append(why_header)
        for p in why_list:
            lines.append(f'  · {p[:110]}')
    if risk_list:
        lines.append('')
        lines.append(risk_header)
        for n in risk_list:
            lines.append(f'  · {n[:110]}')

    # No footer duplicating the tier/score — app-side chip + big number
    # already display these. Duplicating them here created 3-way mismatch
    # confusion (tier/verdict/refit all different).

    short_read = '\n'.join(lines).rstrip()

    # ─── STRUCTURED SECTIONS (2026-08-22 v3) ────────────────────────
    # App-side renderer parses this instead of plaintext short_read so
    # each section can be styled (coverage pill color-graded, recent-form
    # as a proper table, favoring/risk as colored chip lists) and each
    # section collapsible independently. Falls back to short_read when
    # rendering in legacy consumers that don't know about sections.
    sections = {
        'header': {
            'player': player,
            'stat_label': label,
            'direction': direction.upper(),
            'line': line,
            'odds': side_odds,
            'implied_pct': implied,
            'avg_l5': avg_l5,
            'avg_l10': avg_l10,
            'avg_season': avg_season,
        },
        'coverage': {
            'covered': covered,
            'total': len(checklist),
            'pct': coverage_pct,
            'missing': missing,
            # severity → app can color-grade the pill
            #   'full' = green ✅ (0 missing)
            #   'partial' = yellow ⚠️ (1-2 missing, correctable)
            #   'sparse' = red 🚨 (3+ missing, structural gap)
            'severity': 'full' if not missing else ('partial' if len(missing) <= 2 else 'sparse'),
        } if checklist else None,
        'recent_form': {
            'rows': stat_rows[:10] if stat_rows else [],
            'over_count': sum(1 for r in (stat_rows or [])[:10] if float(r.get('value', 0) or 0) >= line_f),
            'under_count': sum(1 for r in (stat_rows or [])[:10] if float(r.get('value', 0) or 0) < line_f),
            'line': line_f,
            'direction': direction,
        } if stat_rows else None,
        'reasoning': {
            # verdict-relative headers so the app doesn't have to derive them
            'why_header': why_header,
            'why_bullets': why_list,
            'risk_header': risk_header,
            'risk_bullets': risk_list,
            'is_fade': verdict == 'FADE',
        },
        'verdict': verdict,
        'conviction_display': int(float(refit_conv)) if refit_conv is not None else conviction,
    }

    return {
        'short_read': short_read,      # backward-compat plaintext
        'verdict': verdict,
        'conviction': conviction,
        'source': 'template',
        'sections': sections,           # structured payload for styled render
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
    print()
    print('=' * 60)
    print('WITH CTX (real-pipeline coverage):')
    print('=' * 60)
    demo_ctx = {
        'game_id': 'abc', 'home_team': 'MIL', 'away_team': 'ATL',
        'home_team_k_pct': 24.0, 'away_team_k_pct': 22.5,
        'home_wrc_plus': 106, 'home_ops_vs_opp_hand': 0.712,
        'home_sp_xera': 2.7, 'home_pitcher_last_3_era': 3.1,
        'umpire': 'Angel Hernandez', 'umpire_note': 'K-friendly',
        'home_catcher_framing': 3.5, 'temperature': 78, 'wind_speed': 8,
        'wind_direction': 'out to right', 'park_run_factor': 97,
    }
    print(render_prop_template(demo_prop, ctx=demo_ctx)['short_read'])
