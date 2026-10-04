#!/usr/bin/env python3
"""Resolve a prop row to its game-context row.

WHY THIS EXISTS (2026-10-03)
----------------------------
Every `*_pipeline_props` table keys `game_id` to The Odds API event id — a
32-char hash, because the odds API is where a prop comes from. The context and
results tables key on whatever their own upstream uses, and those disagree:

    mlb_pipeline_props   '1632ced04d65127cf6172d3a2c2e2d16'   odds-API hash
    mlb_game_results     '1632ced04d65127cf6172d3a2c2e2d16'   same -> joins
    nfl_game_results     '20270110_LAC_DEN'                   does not join
    nhl_game_results     '2026020073'        NHL numeric       does not join
    nba_game_results     '401909101'         ESPN id           does not join

So MLB is the only sport where a prop can find its own game by id. For NHL a
`game_id` join matched **0 of 2,132** rows. Both the live scorer
(prop_ensemble_scorer: `ctxs.get(prop['game_id']) or {}`) and the tier grader
silently received an empty context and every ctx-dependent signal evaluated to
"did not fire" rather than "could not be evaluated".

Cost of that: four NHL prop signals — `nhl_prop_facing_elite_goalie`,
`nhl_prop_facing_weak_goalie`, `nhl_prop_b2b_fade`,
`nhl_prop_pp_specialist_hot_pk` — sat at `sample_n: 0` in signal_registry on
5,889 graded props. Goalie quality and special teams, the two things that most
obviously matter in hockey props, had never fired once, and the registry
presented that as evidence rather than as plumbing.

THE FALLBACK KEY
----------------
`(game_date, team_abbrev)`. A team plays at most once per day, so it is 1:1 —
verified on 13,174 NHL props: 13,106 matched (99.5%), 0 ambiguous, and the
only misses are the 68 props whose player identity never resolved, so they
carry no team at all. The context rows it finds carry every input the signals
need (goalie SV%, back-to-back, PK%) on 100% of matches.

`game_id` is still tried FIRST, so MLB keeps its exact join and this changes
nothing there.

NOT A SUBSTITUTE FOR A SNAPSHOT. This resolves against whatever the context
table holds *now*. Context is a working set, not an archive — `mlb_game_context`
is pruned to 14 days (game_context.py) and holds 131 rows across a 6-month
season. The durable fix is to snapshot the signal inputs onto the prop row at
generation, the way `jerry_reads.input_snapshot` already does for game reads.
Until then, historical grading reaches only as far back as context survives.
"""
from __future__ import annotations

# Prop-side fields that may name the player's team, best first.
_PROP_TEAM_FIELDS = ('team_abbrev', 'player_team', 'team')
# Context-side fields naming the two teams.
_CTX_TEAM_FIELDS = ('home_team_abbrev', 'away_team_abbrev', 'home_team', 'away_team')


def build_ctx_index(ctx_rows) -> dict:
    """-> {'by_id': {...}, 'by_team_date': {(date, team): row}}.

    A context row is registered under BOTH of its teams, so a prop resolves
    whichever side its player is on.
    """
    by_id, by_team_date = {}, {}
    for c in (ctx_rows or []):
        gid = c.get('game_id')
        if gid is not None:
            by_id[str(gid)] = c
        gd = c.get('game_date')
        if not gd:
            continue
        for f in _CTX_TEAM_FIELDS:
            v = c.get(f)
            if v:
                by_team_date[(str(gd), str(v).upper())] = c
    return {'by_id': by_id, 'by_team_date': by_team_date}


def derive_side_fields(ctx_row: dict, prop: dict) -> dict:
    """Turn every home_*/away_* context field into own_*/opp_* for THIS prop.

    WHY. The NHL prop signals were written as:

        p.get('player_team') == ctx.home_team and ctx.away_goalie_sv_pct >= 0.920

    which can never be true, for two independent reasons that both fail
    SILENTLY. NHL props have no `player_team` column at all (it is
    `team_abbrev`), so the left side is None; and `ctx.home_team` is
    'Columbus Blue Jackets' while the prop carries 'CBJ'. `None == 'Columbus
    Blue Jackets'` is simply False and `and` short-circuits before the float
    comparison, so nothing raises and the signal reports "did not fire".

    Asking a signal to re-derive which side a player is on, in an expression,
    against whichever vocabulary each table happens to use, is the bug. The
    resolver already knows the side — so hand the expression `opp_goalie_sv_pct`
    and let it say what it means.

    Generic on purpose: any `home_x`/`away_x` pair becomes `own_x`/`opp_x`, so
    this serves MLB park factors and NFL pace the same way it serves NHL
    goalies. Matching accepts either an abbrev or a full name on either side.
    """
    if not ctx_row or not prop:
        return {}
    team = None
    for f in _PROP_TEAM_FIELDS:
        if prop.get(f):
            team = str(prop[f]).upper()
            break
    if not team:
        return {}
    home_names = {str(ctx_row.get(f)).upper() for f in ('home_team_abbrev', 'home_team')
                  if ctx_row.get(f)}
    away_names = {str(ctx_row.get(f)).upper() for f in ('away_team_abbrev', 'away_team')
                  if ctx_row.get(f)}
    if team in home_names:
        own, opp = 'home_', 'away_'
    elif team in away_names:
        own, opp = 'away_', 'home_'
    else:
        # Prop resolved to this game but its team matches neither side. Emit
        # nothing rather than guess a side — a wrong side is worse than absent.
        return {}
    out = {'own_is_home': own == 'home_'}
    for k, v in ctx_row.items():
        if k.startswith(own):
            out['own_' + k[len(own):]] = v
        elif k.startswith(opp):
            out['opp_' + k[len(opp):]] = v
    return out


def resolve_ctx(index: dict, prop: dict) -> tuple[dict, str]:
    """-> (ctx_row_or_empty, how) where how is 'id' | 'team_date' | 'miss'.

    Returning HOW it resolved is the point: a caller that cannot tell an empty
    context from a resolved one reports "signal did not fire" for what is
    really "I never had the data", which is the bug this module exists to fix.
    """
    if not index:
        return ({}, 'miss')
    gid = prop.get('game_id')
    if gid is not None:
        hit = index.get('by_id', {}).get(str(gid))
        if hit:
            return (hit, 'id')
    gd = prop.get('game_date')
    if gd:
        for f in _PROP_TEAM_FIELDS:
            v = prop.get(f)
            if not v:
                continue
            hit = index.get('by_team_date', {}).get((str(gd), str(v).upper()))
            if hit:
                return (hit, 'team_date')
    return ({}, 'miss')
