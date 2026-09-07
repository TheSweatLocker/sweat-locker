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
    # NFL — long-name keys retained for backward compat; short-name keys
    # (pass_yds/rush_yds/etc) added 2026-09-07 to match what
    # nfl_generate_props.py actually writes (prop_type='pass_yds_over'
    # → _stat_family() strips '_over' → 'pass_yds'). Prior _STAT_META
    # only had long-name keys → NFL props fell into unknown-role
    # fallback → coverage checklist empty → sections.coverage = null
    # → app never rendered the coverage pill for NFL. Adding short-name
    # variants makes NFL props hit the real metadata path.
    'passing_yards':    {'label': 'Passing Yards', 'role': 'qb', 'relevant_ctx': ['opp_pass_def', 'pace', 'weather', 'game_script']},
    'pass_yds':         {'label': 'Passing Yards', 'role': 'qb', 'relevant_ctx': ['opp_pass_def', 'pace', 'weather', 'game_script']},
    'pass_tds':         {'label': 'Passing TDs', 'role': 'qb', 'relevant_ctx': ['opp_pass_def', 'game_script', 'implied_high']},
    'pass_attempts':    {'label': 'Pass Attempts', 'role': 'qb', 'relevant_ctx': ['game_script_pass', 'weather']},
    'pass_completions': {'label': 'Completions', 'role': 'qb', 'relevant_ctx': ['opp_pass_def', 'weather']},
    'pass_interceptions': {'label': 'Interceptions', 'role': 'qb', 'relevant_ctx': ['opp_pass_def']},
    'interceptions':    {'label': 'Interceptions', 'role': 'qb', 'relevant_ctx': ['opp_pass_def']},
    'rushing_yards':    {'label': 'Rushing Yards', 'role': 'rb', 'relevant_ctx': ['opp_run_def', 'game_script', 'weather']},
    'rush_yds':         {'label': 'Rushing Yards', 'role': 'rb', 'relevant_ctx': ['opp_run_def', 'game_script', 'weather']},
    'rush_tds':         {'label': 'Rushing TDs', 'role': 'rb', 'relevant_ctx': ['opp_run_def', 'game_script', 'implied_high']},
    'rush_attempts':    {'label': 'Rush Attempts', 'role': 'rb', 'relevant_ctx': ['game_script_run', 'weather']},
    'receiving_yards':  {'label': 'Receiving Yards', 'role': 'wr', 'relevant_ctx': ['opp_pass_def', 'target_share', 'game_script']},
    'reception_yds':    {'label': 'Receiving Yards', 'role': 'wr', 'relevant_ctx': ['opp_pass_def', 'target_share', 'game_script']},
    'receptions':       {'label': 'Receptions', 'role': 'wr', 'relevant_ctx': ['target_share', 'game_script']},
    'anytime_td':       {'label': 'Anytime TD', 'role': 'skill', 'relevant_ctx': ['opp_pass_def', 'implied_high', 'game_script']},
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

    2026-08-23 v3: PURE TIER SYSTEM. Verdict = tier verbatim, no
    exceptions. Playbook is shadow-mode (not proven against legacy yet)
    so its BACK/FADE opinion should NOT override the user-facing verdict.
    If playbook disagrees with the shown direction, that appears as a
    context bullet in the "risks" section — not as the primary label.
    Fixes the confusion where users saw tier=LEAN pill next to
    verdict=FADE and had to reconcile two conflicting labels.

    Only PRIME/STRONG/LEAN/PASS. `side` param retained for API compat
    but not used to override.
    """
    tier = (tier or '').upper()
    if tier in ('PRIME', 'STRONG', 'LEAN'): return tier
    return 'PASS'


def _clean_prose(s) -> str:
    """Prose scrubber — kill jargon, sentence-case, humanize.

    Input signal prose comes from playbook_sources / signal registry and
    is stat-notation dense: "career BAA vs opp >= .270 (15+ IP) — gets hit".
    Output: "Gets hit by this lineup historically (.270 career BAA)".

    Rules:
      - Strip parenthetical sample thresholds "(15+ IP)", "(2+ starts)"
      - Convert ">= .270" / "<= 3.0" comparison syntax to a stat
      - Capitalize first character
      - No trailing em-dash phrase (redundant with the stat)

    2026-08-23 dict-safe: when a signal's prose is a dict instead of a str
    (yesterday's hits_over PRIME "Opposing starter is soft" signal source
    emitted `{'park': 'Park factor 118…', 'l7_hot': '...'}` which stringified
    as raw Python dict on 3 cards). Now joins dict values with " · " so the
    downstream card renders "Park factor 118 · Hits in 6 of last 7 (86%)"
    instead of the literal dict repr.
    """
    import re
    if not s: return ''
    # Handle non-str prose defensively (dict, list, tuple)
    if isinstance(s, dict):
        # Prefer values (usually the human-readable sentences) over keys
        parts = [str(v).strip() for v in s.values() if v]
        s = ' · '.join(p for p in parts if p) or ''
    elif isinstance(s, (list, tuple)):
        s = ' · '.join(str(x).strip() for x in s if x)
    if not s: return ''
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

    2026-08-23 v3 — PURE TIER SYSTEM. The flip-orientation on playbook
    FADE was removed. Now: signals with positive contribution ALWAYS
    support the card's shown direction; negative always against. This
    keeps semantics consistent regardless of what the shadow-mode
    playbook thinks. Playbook's disagreement (when it fades) becomes
    a separate context bullet in the render, not a signal-orientation
    override.

    Rationale: playbook is shadow-mode / not primary. It should not
    silently reinterpret the model's fired signals from the user's
    perspective. If playbook disagrees, that's noted as context, but
    the primary "why UNDER" bullets stay grounded in the direction
    the CARD is showing.

    prop_direction + playbook_side retained for API compat but not
    used for flipping.
    """
    for_side, against_side = [], []
    for s in (sources or []):
        contrib = float(s.get('contribution') or 0)
        prose = _clean_prose(s.get('prose') or s.get('signal_key', ''))
        (for_side if contrib >= 0 else against_side).append((abs(contrib), prose))

    # 2026-09-07 v2 — prose-first fallback when no playbook sources.
    # Prior version rendered raw key names ("L4", "Rec Yds", "Def_pass_def")
    # even when signals like l5_confirm and l10_hot had rich prose values
    # ("L5 avg 76.8 — 5-of-5 OVER 56.5"). The isinstance+len check DID try
    # to prefer prose but numeric signals (l4: 4.25) and short keys fell
    # into the key-name path, then sort collapsed everything to iteration
    # order — result: prose signals often bumped out of top-5 by useless
    # key-name bullets.
    #
    # New logic: two passes.
    #   Pass 1 — collect only PROSE signals (string values with '—' or ':'
    #            or length >> key). These are the humanized model-generated
    #            insights we always want to prioritize.
    #   Pass 2 — if we still have <5 bullets, humanize the raw key + value
    #            into a "Key: value" chip. Better than raw uppercase key
    #            name alone but only used to pad thin coverage.
    if not sources and prop_signals:
        # 2026-09-07 skip internal-metadata + duplicate-of-header signals.
        # These are useful for scoring/debugging but read as noise when
        # padded into user-facing bullets:
        #   label / opp_col — duplicates or raw column names
        #   opp_pct with null value — chip renders "Opp Pct" alone
        #   league_baseline — internal baseline, not signal
        #   games_used — sample size, shown elsewhere
        #   direction / _direction / _line — internal metadata
        _SKIP_KEYS = {'label', 'opp_col', 'league_baseline', 'games_used',
                      'direction', 'season_avg', 'implied_high', 'implied_low'}
        prose_bullets = []
        keyname_bullets = []
        for k, v in prop_signals.items():
            if k.startswith('_'): continue
            if k in _SKIP_KEYS: continue
            v_str = str(v) if v is not None else ''
            # Skip when the value is null / empty / "None"
            if not v_str or v_str.lower() == 'none': continue
            # Prose signal: string value that's clearly humanized narrative
            is_prose = isinstance(v, str) and (
                '—' in v_str or
                (':' in v_str and len(v_str) > len(k) + 3) or
                len(v_str) > 25
            )
            if is_prose:
                prose_bullets.append(_clean_prose(v_str))
            else:
                # Format as "Key: value" chip — cleaner than raw uppercase key
                pretty_key = k.replace('_', ' ').title()
                # Suffix percentage-looking values
                fmt_val = f'{v_str}%' if k.endswith('_pct') and not v_str.endswith('%') else v_str
                keyname_bullets.append(f'{pretty_key}: {fmt_val}')
        # Prose wins first; pad with humanized key-name chips only if <5.
        for p in prose_bullets:
            for_side.append((1.0, p))
        for p in keyname_bullets:
            for_side.append((0.3, p))

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

    # 2026-08-23 v3 — PURE TIER SYSTEM. Headers always match the shown
    # direction. Playbook's shadow-mode BACK/FADE opinion no longer
    # flips headers or verdict — it appears (optionally) as a context
    # bullet in the risks section.
    dir_label = direction.upper()
    # 2026-08-23 rename: this section shows PLAYBOOK's per-signal contribution
    # view for the pick. App's "WHY WE BACK THIS" already surfaces the raw
    # generate_props signals. Prior header "Why UNDER" made the two sections
    # look duplicative even though they're different perspectives. Renaming
    # to "PLAYBOOK CONFIRMS" so users read them as two views (raw signals +
    # model-weighted confirmation) rather than one section repeating itself.
    # Kept `Why UNDER risks` for the risk block (correct label — that IS
    # what risks the direction).
    why_header, risk_header = 'PLAYBOOK CONFIRMS', f'Why {dir_label} risks'
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
    # Shadow-playbook disagreement note (context only, not verdict).
    # If playbook flags FADE on this direction, surface the fact as a
    # small caution — users get transparency without playbook overriding
    # the primary tier verdict.
    if (pb_side or '').upper() == 'FADE':
        lines.append('')
        lines.append(f'⚠ Shadow playbook fades this direction (calibrating vs legacy — not primary signal)')

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
            'is_fade': False,  # 2026-08-23 v3: PURE TIER SYSTEM — verdict never FADE
            'shadow_playbook_fades': (pb_side or '').upper() == 'FADE',  # context flag for app
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
