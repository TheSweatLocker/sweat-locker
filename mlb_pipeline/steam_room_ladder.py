"""Steam Room Ladder engine (2026-08-13).

Scans today's plays across all ladder-eligible sports, checks each against
the tight qualifier gates, upserts a ladder_rung when one clears. Also
maintains ladder_state (current active rung, streak counters).

Ladder Rung qualifies when ALL:
  * PRIME OR STRONG tier
  * Model win probability >= 60% (base gate)
  * Cohort win rate >= LADDER_COHORT_HIT_MIN
  * Cohort sample size >= LADDER_COHORT_N_MIN
  * Model consensus 4+ / 5 lens agreement
  * Edge (model_win_prob - implied_prob) >= LADDER_EDGE_MIN_PP
  * Absolute juice cap: -500 (safety ceiling)
  * NOT sharp-fade flagged · NOT refit-trap flagged
  * Sides ONLY OR totals with 5+ sharp-signal confluence

Frequency target: 2 rungs/week minimum.
Self-tune: if rungs/week drops below 2 for 2 consecutive weeks, LOOSEN
one gate (cohort_hit_min: 60 → 58). If rungs/week exceeds 4 for 2
consecutive weeks, TIGHTEN (consensus: 4/5 → 5/5).

Sport-universal — pulls plays from mlb_game_context, nfl_game_context,
ncaaf_game_context, ncaab_game_context (as those tables materialize).
Only considers sports with sport_registry.ladder_eligible=true.

CLI:
    python steam_room_ladder.py [--date YYYY-MM-DD] [--dry-run]

Runs daily after primary_play settles. If a qualifier fires, ladder_state
flips to 'active' with that rung. If none, ladder_state stays 'waiting'.
Result backfill (Win/Loss/Push) runs via resolve_ladder_results.py after
games grade.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

# 2026-09-06 tier consolidation: shared publishability gate for every
# composer (sharp card, ladder, prop card, future surfaces).
# ══ 2026-11-09 · THE LADDER HAD NO COHERENCE GATE ═══════════════════════
# The ladder reads *_game_context DIRECTLY, so it bypasses the gate placed on
# the ctx->jerry_reads bridge and is a SIXTH writer of a published play. The
# moment the win_prob fix below made 'rl' picks priceable, the first thing the
# relaxed scan qualified was `Houston Rockets -8.5` -- the pick the Sweat Card,
# the Sharp and the POTD anchor all refused BY NAME the same morning, because
# our own projection has Houston by 3.3 and the pick lays 8.5. It arrived with
# "edge +18.7pp", which is what an incoherent pick looks like from the inside.
#
# On a COMPOUNDING surface this is the worst place to miss it, so the gate goes
# here too. Not a new rule -- the same `coherence_ok` the other surfaces use.
try:
    from retier_from_sport_bars import coherence_ok as _COHERE
except Exception as _e:   # pragma: no cover
    _COHERE = None
    print(f'  ! ladder coherence gate UNAVAILABLE ({_e!r}) '
          f'— rl/spread rungs would ride unchecked')

from prop_publishability import (
    is_publishable as _is_prop_publishable,
    effective_tier as _effective_prop_tier,
)

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Qualifier gates — see project_steam_room_ladder for tuning history.
# 2026-09-02 REVERT: rolled back 8/18 loosening. Post-loosening the ladder
# ran 5-9 (35.7%, -5.74u) — the "2 rungs/week min" target was auto-
# loosening gates and picking marginal plays that lost more than they
# won. Reverting to pre-8/18 stricter thresholds. Accept fewer rungs
# (0/week possible) — no bet is a bet. Auto-loosener self-tune below
# is also disabled to prevent drift back into loosened state.
LADDER_TIER_MIN         = {'PRIME', 'STRONG'}  # LEAN removed — no ladder authority

# ══ 2026-11-08 · THE TIER FLOOR EXCLUDED THREE WHOLE SPORTS ═════════════
# ANDY: "we just need to ensure all sports are availbale for selection ...
# i want the volume revived".
#
# LADDER_TIER_MIN alone could never admit NHL, NBA or NCAAB. Measured on
# tonight's board:
#
#     NHL     34 picks   ALL COVERAGE        0 PRIME/STRONG
#     NBA     10 picks   ALL COVERAGE        0
#     NCAAB   46 picks   40 COVERAGE, 6 LEAN 0
#     NFL    140 picks   2 PRIME + 8 STRONG
#     NCAAF   87 picks   14 STRONG
#
# and that is not a tier problem, it is the conviction pile-up: the
# ensemble formula is sport-blind (50 + min(score*10,30) + ...) while the
# score it reads is not - median score NCAAF 0.630 vs NBA 0.080, NCAAB
# 0.070 - so those three sports cap at conviction 55, the tier thresholds
# put STRONG at 65, and every pick lands on COVERAGE. The ladder's tier
# floor then removed the sport entirely. Same single root cause as the
# all-NCAAF Sweat Card.
#
# So the ladder admits a pick EITHER on the raw tier (unchanged for
# NFL/NCAAF/MLB) OR on that sport's own RANK - conviction_scale's per-sport
# separation number, which is what POTD already ranks on. conviction itself
# is NOT rescaled: it is consumed as a win probability by
# model_edge.edge_pp, and inflating it would print a fake edge on every
# pick and switch off the cap that is currently telling the truth. That is
# the whole reason conviction_rank exists as a second number.
#
# THE BAR IS MEASURED, not chosen. Candidates clearing each rank bar over
# 11-05..11-08, and whether every sport and every day is represented:
#
#     bar    NHL  NBA  NCAAB  NFL  NCAAF   sports  days
#     >=60     2   16     27    5     21      5/5   4/4
#     >=65     2   15     21    4     18      5/5   4/4
#     >=70     0   12      1    2     14      4/5   4/4   <- loses NHL
#     >=75     0    6      0    2      9      3/5   4/4
#
# 65 is the highest bar that still represents all five sports every day;
# 70 drops NHL entirely because its best pick ranks 69.6. Raise it as the
# signal coverage improves - a rank is only as good as the signals under
# it, and NCAAB currently fires one signal type.
LADDER_RANK_MIN         = 65.0
LADDER_WIN_PROB_MIN     = 60.0     # pre-loosen baseline
LADDER_COHORT_HIT_MIN   = 60.0     # pre-loosen baseline
LADDER_COHORT_N_MIN     = 30       # pre-loosen baseline
LADDER_CONSENSUS_MIN    = 4        # pre-loosen baseline (4-of-5 lens)
LADDER_EDGE_MIN_PP      = 10.0     # pre-loosen baseline (real edge only)
LADDER_ABS_JUICE_CAP    = -250     # unchanged — compounding math destroys past -250
# 2026-11-10 · juice ceiling for a moneyline admitted on RANK rather than on
# tier. The documented heavy-favourite ML trap starts at -200, and a
# rank-admitted pick is the weaker of the two admission routes, so the two
# weaknesses are not allowed to stack on a compounding surface. A tiered
# (PRIME/STRONG) ML is still governed by LADDER_ABS_JUICE_CAP alone.
LADDER_RANK_ML_JUICE_CAP = -200
# 2026-11-04 · hard floor for the RELAXED scan only. Not a gate — nothing
# can buy a leg past it. A ladder leg is a compounding leg, and the relaxed
# scan's first LEAN candidate was a +2400 longshot our own model gave a 5.6%
# win probability. 45% is below any sane single-bet threshold on purpose:
# this is the "is this even a plausible side" bar, not a selection standard.
LADDER_RELAXED_WIN_PROB_FLOOR = 45.0
# ══ 2026-11-08 · THE CODE DEMANDED 4, THE UI PROMISES 3, ONLY 3 EXIST ══
# check_qualifier's own docstring says "Must clear at least 3 of 5 gates
# (matches UI copy)" and the gate comment repeats it, while this constant
# said 4. That is not a stricter policy, it is a contradiction - and the
# arithmetic made it fatal: of the five gates, the consensus one read a
# column that exists on NO context table (fixed in this commit) and the
# cohort one is documented in this very file as never matching an
# ensemble_v2 audit_note. Three gates were reachable and four were
# required, so the strict scan could not qualify anything on any sport.
# Measured: 6 of the 7 nights 11-01..11-08 produced no qualifier at all.
# 3 is what the docstring says, what the UI tells users, and what the
# gates can actually deliver.
LADDER_MIN_GATES        = 3        # 3-of-5, matching the UI copy + docstring
LADDER_AUTO_LOOSEN_DISABLED = True  # 2026-09-02: prevent drift back to bleeding gates

# 2026-08-20: cross-sport edge normalization. Different sports have different
# base-rate variance in edge_pp — a +7pp MLB edge is NOT the same as a +7pp
# NHL edge because puckline math + hockey scoring compress edges. Multipliers
# scale raw edge_pp into a common ranking metric so cross-sport candidates
# compare fairly. Starts at 1.0 for all — will tune once we have live
# multi-sport data (Oct+ when NFL/NCAAF/NHL/NBA come online). Values below
# are best-first-guess based on typical sport-book edge dispersion.
SPORT_EDGE_MULTIPLIER = {
    'MLB':   1.00,   # baseline — most historical data, calibrate others against this
    'NFL':   0.90,   # bigger spreads, edges compress — slightly discount
    'NCAAF': 0.90,   # same shape as NFL
    'NCAAB': 0.85,   # very high variance, edges harder to trust
    'NBA':   0.95,   # deep signal but tight lines
    'NHL':   1.10,   # puckline math means small edges more meaningful
    'UFC':   0.80,   # single-KO variance kills edge reliability
}

# Per-sport context table
CTX_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    'NBA':   'nba_game_context',
    'NHL':   'nhl_game_context',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _implied_prob(odds: Optional[int]) -> Optional[float]:
    if odds is None: return None
    try: o = int(odds)
    except (TypeError, ValueError): return None
    return 100.0 * (100 / (o + 100)) if o >= 0 else 100.0 * (abs(o) / (abs(o) + 100.0))


def get_ladder_eligible_sports() -> list:
    """Read sport_registry — return sports whose ladder_eligible=true."""
    r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H_READ,
        params={'ladder_eligible': 'eq.true', 'active': 'eq.true',
                'state': 'eq.in_season',
                'select': 'sport'},
        timeout=15)
    if r.status_code != 200: return []
    return [row['sport'] for row in r.json()]


def check_qualifier(row: dict, sport: str) -> Optional[dict]:
    """Return a candidate step dict if the play qualifies, else None.

    2026-08-19: rewritten to SOFT SCORING. Prior version required ALL 5 gates
    to pass — the "3 of 5" copy in the UI was already accurate for what we
    intended, but the backend was actually enforcing 5 of 5. Days with any
    single mid-tier signal (e.g. Boston ML at 47% MC prob vs 58 threshold)
    silently rejected every candidate and the ladder sat parked for weeks.

    New behavior: count gates passed, keep the pick, let caller pick the
    highest-scoring one. Must clear at least 3 of 5 gates (matches UI copy)
    AND must not trigger a hard blocker (deep juice, sharp-fade, trap flag).
    """
    pp = row.get('primary_play') or {}
    tier = pp.get('tier')
    # Admit on the raw tier OR on this sport's own rank. See LADDER_RANK_MIN.
    _rank = pp.get('_conviction_rank')
    if _rank is None:
        try:
            import conviction_scale as _cs_l
            _rank = _cs_l.rank_from_signals(
                sport, pp.get('score'),
                (pp.get('_conv_inputs') or {}).get('margin'))
        except Exception:                                      # noqa: BLE001
            _rank = None
    try:
        _rank = float(_rank) if _rank is not None else None
    except (TypeError, ValueError):
        _rank = None
    _by_tier = tier in LADDER_TIER_MIN
    _by_rank = _rank is not None and _rank >= LADDER_RANK_MIN
    if not (_by_tier or _by_rank):
        return None
    # A declared no-play is never a rung, whatever it ranks.
    if str(tier or '').upper() in ('PASS', 'SKIP'):
        return None

    # Gate 1: sides only (or totals/yrfi with signal confluence)
    # 2026-08-20: added 'rl' to is_side alongside 'spread' — ensemble emits
    # 'rl' as the market for run-line picks, not 'spread'. Prior bug: EVERY
    # RL-tiered pick got rejected here silently.
    market = pp.get('type')  # 'ml' | 'over' | 'under' | 'total' | 'spread' | 'rl' | 'yrfi' | 'nrfi'
    is_side = market in ('ml', 'spread', 'rl')
    is_total = market in ('over', 'under', 'total')
    is_nrfi = market in ('yrfi', 'nrfi')   # 2026-08-14: added YRFI/NRFI as ladder-eligible
    if not (is_side or is_total or is_nrfi): return None

    # Gate 1b: our own projection must cover the number we are laying.
    # Proven by its REFUSAL, not by a clean run (2026-11-08: a gate reading a
    # column no SELECT requested returned PASS forever). `pp` carries type,
    # line and side, and `row` is the ctx row holding projected_spread.
    if _COHERE is not None and market in ('rl', 'spread'):
        try:
            _cok, _cwhy = _COHERE(sport, row, pp)
        except Exception as _ce:
            _cok, _cwhy = False, (f'coherence gate raised {_ce!r} — refusing '
                                  f'rather than compounding an unchecked leg')
        if not _cok:
            print(f'    ⛔ ladder refused {pp.get("label")!r} ({sport}): {_cwhy}')
            return None

    # For totals, require 5+ sharp-signal confluence from sharp_scenario_matches
    if is_total:
        gid = row.get('game_id')
        if not gid: return None
        sm = requests.get(f'{SB}/rest/v1/sharp_scenario_game_matches', headers=H_READ,
            params={'game_id': f'eq.{gid}', 'market': 'eq.total',
                    'select': 'jerry_hint,hit_rate,n'}, timeout=10).json()
        if not isinstance(sm, list) or len(sm) < 5: return None

    # Model win probability
    # 2026-08-14 BUG FIX: previously read mc_p_home / mc_p_away — but actual
    # MC blob has mc_p_home_win / mc_p_away_win (see keys dump). Every ML
    # ladder candidate was returning win_prob=0.0% and getting filtered out
    # silently. Also totals were skipping win_prob entirely — added mc_p_over
    # / mc_p_under extraction so totals can qualify. YRFI/NRFI added too.
    mc_probs = row.get('mc_probabilities') or {}

    def _mcp(*names, complement_of=None):
        """First probability PRESENT among `names`, else 1 - its complement.

        ══ 2026-11-09 · THE LADDER WAS THE ONE READER THAT NEVER GOT THIS ══
        Three simulators name the same quantity differently, and two never
        write the away side at all:
            NHL    mc_p_home        (no mc_p_away  -> away ML unpriceable)
            NBA    mc_p_home_covers (no mc_p_away_covers)
            NCAAB  mc_p_over_line   (no mc_p_over/_under, no covers at all)
        `defensive_gates._mcp` and `ensemble_scorer` both already read every
        spelling with a complement. This function did not, so win_prob came
        back None on 14 of 37 live picks and `round(None, 1)` at the return
        crashed the relaxed scan on EVERY Daily Card run of 2026-11-09
        (12 of 12 failed, taking The Sharp and the Ladder to zero).

        Complementing is sound and is this repo's own convention: measured
        over 400 rows, mc_p_home + mc_p_away = 1.0000 median (tie prob
        0.0002, already excluded), and derive_cover_probability.py:226
        writes away_covers as 1 - home_covers.

        Tests PRESENCE, never truth. A genuine 0.0 is a probability, and the
        `x or None` this replaces turned it into a missing value -- the same
        falsy-zero class of bug as `Number(conviction) || 55`.
        """
        for nm in names:
            v = mc_probs.get(nm)
            if v is not None:
                try: return float(v) * 100
                except (TypeError, ValueError): pass
        for nm in (complement_of or ()):
            v = mc_probs.get(nm)
            if v is not None:
                try: return (1.0 - float(v)) * 100
                except (TypeError, ValueError): pass
        return None

    win_prob = None
    if isinstance(mc_probs, dict):
        label = (pp.get('label') or '').lower()
        home_team = (row.get('home_team') or '').lower()
        away_team = (row.get('away_team') or '').lower()
        _is_home = bool(home_team) and home_team in label
        _is_away = bool(away_team) and away_team in label
        if is_side and market == 'ml':
            if _is_home:
                win_prob = _mcp('mc_p_home_win', 'mc_p_home',
                                complement_of=('mc_p_away_win', 'mc_p_away'))
            elif _is_away:
                win_prob = _mcp('mc_p_away_win', 'mc_p_away',
                                complement_of=('mc_p_home_win', 'mc_p_home'))
        # 2026-11-09 'rl' ADDED. line 236 admits ('ml', 'spread', 'rl') as a
        # side, and this branch tested only 'spread' -- so every pick typed
        # 'rl' fell through all four branches and left win_prob None. 'rl' is
        # exactly what NBA, NHL and NCAAB emit for a spread/puck line, so the
        # ladder could never price one, on either side, for its whole life.
        elif is_side and market in ('spread', 'rl'):
            if _is_home:
                win_prob = _mcp('mc_p_home_covers',
                                complement_of=('mc_p_away_covers',))
            elif _is_away:
                win_prob = _mcp('mc_p_away_covers',
                                complement_of=('mc_p_home_covers',))
        elif is_total:
            if 'over' in label:
                win_prob = _mcp('mc_p_over', 'mc_p_over_line',
                                complement_of=('mc_p_under',))
            elif 'under' in label:
                win_prob = _mcp('mc_p_under',
                                complement_of=('mc_p_over', 'mc_p_over_line'))
        elif is_nrfi:
            if market == 'yrfi':
                win_prob = _mcp('mc_p_yrfi', complement_of=('mc_p_nrfi',))
            elif market == 'nrfi':
                win_prob = _mcp('mc_p_nrfi', complement_of=('mc_p_yrfi',))
    # Gate scoring — count how many of the 5 gates this pick clears.
    # Tier is gate #1 (already passed above — we're here because tier is
    # PRIME/STRONG/LEAN). Others are win_prob, consensus, edge, cohort.
    gates_passed = 1  # tier (or rank — see LADDER_RANK_MIN)
    gate_notes = [f'tier={tier}' + ('' if _by_tier
                                    else f' via rank {_rank:.0f}')]

    if win_prob is not None and win_prob >= LADDER_WIN_PROB_MIN:
        gates_passed += 1
        gate_notes.append(f'MC={win_prob:.0f}%✓')
    else:
        gate_notes.append(f'MC={win_prob:.0f}%' if win_prob else 'MC=?')

    # ══ 2026-11-08 · THIS GATE COULD NEVER PASS, ON ANY SPORT ══════════
    # It read `signal_confluence_support`, which does not exist on ANY of
    # the six context tables. `row.get(...) or 0` turned that into 0, and
    # `0 >= 4` is False forever, so one of the five gates was dead on every
    # pick in every sport.
    #
    # Why that emptied the ladder rather than just costing a point: the
    # cohort gate below is also effectively unreachable (its regex is
    # documented in this very file as never matching an ensemble_v2
    # audit_note), so only tier + win_prob + edge could ever pass -- a
    # maximum of 3 of 5 -- while LADDER_MIN_GATES is 4. The strict scan was
    # arithmetically incapable of qualifying a pick. Measured: 6 of the 7
    # nights 11-01..11-08 produced no qualifier at all, and the only rungs
    # since MLB ended came from the RELAXED 2-dry-day path.
    #
    # The real column is `signal_confluence_net`, filled 100% on every
    # sport (NHL 72/72, NBA 58/58, NCAAB 335/335, NFL 164/164, NCAAF
    # 99/99). It is SIGNED -- positive leans HOME, negative leans AWAY,
    # roughly -5..+5 over the five lenses named in
    # signal_confluence_breakdown (elo, xg_diff, goalie_sv, high_danger,
    # l10_goal_diff for NHL).
    #
    # So the magnitude alone is not the test: a +5 home confluence is
    # evidence AGAINST an away pick, not for it
    # (feedback_check_which_side_a_column_indexes). The gate now requires
    # the lenses to agree with the side WE are taking.
    consensus = 0          # recorded on the rung as consensus_lens
    _conf_net = row.get('signal_confluence_net')
    try:
        _conf_net = float(_conf_net) if _conf_net is not None else None
    except (TypeError, ValueError):
        _conf_net = None
    _our_side = None
    if is_side:
        _home_nm = (row.get('home_team') or '').lower()
        _lbl = (pp.get('label') or '').lower()
        if _home_nm and _home_nm in _lbl:
            _our_side = 'HOME'
        else:
            _our_side = str(pp.get('side') or '').upper() or 'AWAY'
    if _conf_net is None:
        gate_notes.append('consensus=?')
    else:
        # signed toward our side: positive = lenses agree with us
        _toward_us = _conf_net if _our_side == 'HOME' else -_conf_net
        if _our_side is None:          # totals: magnitude cannot pick a side
            _toward_us = abs(_conf_net)
        consensus = _toward_us
        if _toward_us >= LADDER_CONSENSUS_MIN:
            gates_passed += 1
            gate_notes.append(f'consensus={_toward_us:+.0f}/5 agrees✓')
        else:
            gate_notes.append(f'consensus={_toward_us:+.0f}/5')

    # Odds + edge
    # Column-name coalesce: MLB uses `home_ml_close`/`away_ml_close`,
    # NFL/NCAAF use `close_home_ml`/`close_away_ml`. Fall through to the
    # right one for each sport.
    home_ml_odds = row.get('home_ml_close') or row.get('close_home_ml') or row.get('home_ml')
    away_ml_odds = row.get('away_ml_close') or row.get('close_away_ml') or row.get('away_ml')
    odds = None
    label = (pp.get('label') or '').lower()
    if is_side and market == 'ml':
        home_team = (row.get('home_team') or '').lower()
        if home_team and home_team in label: odds = home_ml_odds
        else: odds = away_ml_odds
    # ══ 2026-11-08 · ONLY A MONEYLINE COULD EVER BE PRICED HERE ═════════
    # `odds` was assigned under `is_side and market == 'ml'` and nowhere
    # else, so every spread, puck line and total fell straight into the
    # `return None` below for having no price. The ladder's game path could
    # select ONE market in one direction, which is a large part of why its
    # history is 58 of 60 MLB (and 39 of those props, which come in on the
    # separate prop path).
    #
    # The prices were on the context row the whole time, measured today
    # since 11-01:
    #
    #     NHL  close_puckline_home_price / _away_price   38/72
    #     NHL  close_total_over_price   / _under_price   50/72
    #     NBA  close_spread_home_price  / _away_price    51/58
    #     NBA  close_total_over_price   / _under_price   51/58
    #
    # NCAAB, NFL and NCAAF carry no side/total price column, so they keep
    # the moneyline-only behaviour here rather than being handed a guess.
    # Same lesson as the jerry_reads side prices on 11-07: look for the
    # column before concluding the data is not there.
    if odds is None and is_side:
        _sd = str(pp.get('side') or '').upper()
        if not _sd:
            _home_nm = (row.get('home_team') or '').lower()
            _sd = 'HOME' if (_home_nm and _home_nm in label) else 'AWAY'
        for _k in ((f'close_puckline_{_sd.lower()}_price',
                    f'close_spread_{_sd.lower()}_price')):
            if row.get(_k) is not None:
                odds = row.get(_k)
                break
    if odds is None and is_total:
        _dir = 'over' if 'over' in label else 'under'
        _v = row.get(f'close_total_{_dir}_price')
        if _v is not None:
            odds = _v
    if odds is None: return None  # hard: can't size without odds
    try: odds_int = int(odds)
    except (TypeError, ValueError): return None
    if odds_int < LADDER_ABS_JUICE_CAP: return None  # hard: too deep juice
    # == 2026-11-10 . A RANK-ADMITTED PICK MUST NOT ALSO CARRY HEAVY JUICE ==
    #
    # Tonight the ladder's only qualifier was `Orlando Magic ML` at -229,
    # tier COVERAGE "via rank 78", 3/5 gates, with our own NBA LR calling it
    # a coin flip (p=0.48) and the pick itself manufactured by the reroute
    # path. LADDER_ABS_JUICE_CAP is -250, so -229 sailed through.
    #
    # The -250 cap was set when an ML candidate could only arrive on TIER,
    # i.e. PRIME or STRONG with real conviction behind it. The 11-08 rank
    # bypass deliberately opened the ladder to COVERAGE-tier picks, because
    # NBA/NHL/NCAAB cap at conviction 55 and could never reach STRONG --
    # Andy asked for that volume and it stays. But it changed what a
    # qualifier can be, and the price gate never moved with it.
    #
    # A rank-admitted pick is a WEAKER claim than a tiered one by
    # construction: it says "best available in its sport", not "earned a
    # stake". Pairing the weaker claim with heavy juice is the worst
    # combination on the one surface that compounds, and it runs straight
    # into a documented house rule -- the heavy-favourite ML trap at -200
    # and worse. Every ML rung the ladder has ever written sits at -135,
    # -104 or +110; the deep-juice rungs (-295, -209, -203, -201) were all
    # PROPS, which is a different gate and a different distribution.
    #
    # So the absolute cap is unchanged for a pick that earned its tier, and
    # a rank-admitted moneyline additionally has to clear the trap line.
    # This refuses by PRICE, not by tier, so it cannot re-break the 11-08
    # sport coverage: a rank-admitted ML at a normal price still qualifies.
    if ((not _by_tier) and is_side and market == 'ml'
            and odds_int < LADDER_RANK_ML_JUICE_CAP):
        print(f'    ⛔ ladder refused {label!r} ({sport}): rank-admitted '
              f'(tier={tier}) moneyline at {odds_int} is past the '
              f'{LADDER_RANK_ML_JUICE_CAP} heavy-favourite line')
        return None

    implied = _implied_prob(odds_int)
    if implied is None: return None
    edge_pp = (win_prob - implied) if win_prob is not None else -999
    # == 2026-11-10 . A NEGATIVE-EDGE LEG COULD STILL QUALIFY ==
    #
    # edge_pp is a SCORED gate, not a floor: missing it costs one of five
    # and a pick passes on three. So the edge could be negative -- our own
    # model probability BELOW the price's implied probability, i.e. we are
    # knowingly taking the worse of it -- and the leg still qualified on
    # tier + win_prob + consensus.
    #
    # Surfaced the moment the rank-admitted juice cap above refused Orlando
    # Magic: the relaxed scan fell through to `Bowling Green ML` at -205,
    # tier STRONG, model 61.2% against an implied 67.2% = edge -6.0pp, and
    # would have written it. That is the same defect class the coherence
    # gate already refuses by name ("our own number does not cover the line
    # we published") -- a pick contradicted by our own numbers -- only
    # expressed in price rather than in points. On the one surface that
    # compounds, a leg we price as -EV is the last thing that should fill a
    # dry streak.
    #
    # This is a hard floor, not a gate: nothing can buy past it, exactly
    # like LADDER_RELAXED_WIN_PROB_FLOOR. It only fires where the edge is
    # actually KNOWN -- win_prob None yields the -999 sentinel, which means
    # "unmeasured", and an unmeasured edge is not evidence of a bad one
    # (feedback_zero_rows_is_a_wiring_error). Those keep failing the scored
    # gate as before.
    #
    # No historical rung is affected: every game-path rung ever written
    # carries a positive edge (+2.9, +3.4, +20.7pp).
    if win_prob is not None and edge_pp < 0:
        print(f'    ⛔ ladder refused {label!r} ({sport}): our model has '
              f'{win_prob:.1f}% but {odds_int} implies {implied:.1f}% '
              f'- edge {edge_pp:+.1f}pp, we would be taking the worse of it')
        return None
    if edge_pp >= LADDER_EDGE_MIN_PP:
        gates_passed += 1
        gate_notes.append(f'edge={edge_pp:+.1f}pp✓')
    else:
        gate_notes.append(f'edge={edge_pp:+.1f}pp')

    # 2026-08-20: cohort backing is now OPTIONAL (bonus signal, not a hard
    # gate). Prior behavior required regex match on primary_play.audit_note
    # for "X% n=Y" cohort pattern — but ensemble_v2 audit_notes have a
    # different format ("ensemble_scorer v2 · N sources · score=... margin=...")
    # that never matches this regex. Result: EVERY ensemble-tiered pick got
    # silently rejected by this gate, ladder sat empty for weeks. Now:
    # try to parse cohort but don't require it; the other 4 gates (tier +
    # win_prob + consensus + edge) carry the qualification weight.
    cohort_hit = None
    cohort_n = None
    audit = pp.get('audit_note') or ''
    import re
    m = re.search(r'(\d+(?:\.\d+)?)%.*?n[=\s]?(\d+)', audit)
    if m:
        cohort_hit = float(m.group(1))
        cohort_n = int(m.group(2))
    if (cohort_hit is not None and cohort_hit >= LADDER_COHORT_HIT_MIN
            and cohort_n is not None and cohort_n >= LADDER_COHORT_N_MIN):
        gates_passed += 1
        gate_notes.append(f'cohort={cohort_hit}%✓')
    elif cohort_hit is not None:
        gate_notes.append(f'cohort={cohort_hit}%')

    # Must clear at least 3 of 5 gates (matches UI copy)
    if gates_passed < LADDER_MIN_GATES:
        return None

    # Hard blockers: sharp-fade / refit-trap always kill the pick
    if row.get('consensus_fade_flag') is True: return None
    if 'refit_trap' in audit.lower() or 'trap_cap' in audit.lower(): return None

    return {
        'game_date': row.get('game_date'),
        'sport': sport,
        'game_id': row.get('game_id'),
        'matchup': f"{row.get('away_team','?')} @ {row.get('home_team','?')}",
        'pick_side': pp.get('label'),
        'market': 'ml' if is_side and market == 'ml'
                  else 'spread' if is_side
                  else 'total',
        'odds_american': odds_int,
        'tier': tier,
        'conviction': pp.get('signal_floor'),
        # 2026-11-09 None-guard. `edge_pp` three lines down and the
        # edge calc above BOTH handle `win_prob is None`; this one did
        # not, and `round(None, 1)` raised TypeError out of the relaxed
        # scan on all 12 Daily Card runs. Returning None is correct and
        # needs no new policy: the caller's HARD WIN-PROBABILITY FLOOR
        # already refuses `_wpf is None` and already prints the reason.
        'model_win_prob': round(win_prob, 1) if win_prob is not None else None,
        'cohort_hit_rate': cohort_hit,
        'cohort_n': cohort_n,
        'consensus_lens': consensus,
        'edge_pp': round(edge_pp, 1) if win_prob is not None else None,
        'gates_passed': gates_passed,
        'qualification_notes': (
            f'{gates_passed}/5 gates · ' + ' · '.join(gate_notes) +
            f' · audit={audit[:60]}'
        ),
    }


def check_prop_qualifier(prop: dict, sport: str = 'MLB') -> Optional[dict]:
    """Emit a ladder rung candidate from a graded prop pick.

    2026-08-20: props added to ladder pool per user request. Prop
    qualification uses different signals than games (no MC prob, no
    consensus lens) so gates are re-mapped:

      1. tier ∈ PRIME/STRONG (playbook or legacy)
      2. book odds in [-300, +150]  ← user's feedback_prop_jerry_odds gate
      3. refit_conviction >= 40      ← not a trap
      4. playbook conviction >= 60   ← real ensemble backing
      5. NOT a hits_over_0.5 with null odds  ← user's juice-trap memo

    Same soft-scoring rule as games — need >= 3/5 to qualify.
    """
    # 2026-09-06 tier consolidation: shared publishability gate first.
    # Blocks props flagged by apply_refit_verdict_override (coverage kill,
    # playbook NO_VALIDATED_SIGNALS, or LR tier drift) even if the legacy
    # playbook_tier still says PRIME. Root cause of Messick U 1.5 ER
    # 9/5 ladder loss: legacy PRIME/86 while tier engine had it at
    # LEAN/50 with _playbook_prop_gate=NO_VALIDATED_SIGNALS.
    ok, reason = _is_prop_publishable(prop)
    if not ok:
        return None

    playbook_tier = (prop.get('playbook_tier') or '').upper()
    legacy_tier = (prop.get('legacy_tier') or '').upper()
    # Never trust playbook_tier above what the shared conservative
    # tier says — playbook can be stale relative to LR/refit overrides.
    lr_capped = (_effective_prop_tier(prop) or '').upper()
    if playbook_tier in ('PRIME','STRONG') and lr_capped:
        rank = {'PRIME': 3, 'STRONG': 2, 'LEAN': 1, 'SKIP': 0}
        if rank.get(lr_capped, 0) < rank.get(playbook_tier, 0):
            playbook_tier = lr_capped
    effective_tier = playbook_tier if playbook_tier in ('PRIME','STRONG') else legacy_tier
    if effective_tier not in ('PRIME','STRONG'):
        return None

    prop_type = prop.get('prop_type') or ''
    direction = (prop.get('direction') or '').lower()
    line = prop.get('prop_line')
    player = prop.get('player_name') or '?'

    # Odds — try both playbook_side + legacy fields
    odds = None
    for key in ('book_over_odds' if direction == 'over' else 'book_under_odds',):
        v = prop.get(key)
        if v is not None:
            try: odds = int(v); break
            except (TypeError, ValueError): pass

    # ══ 2026-10-03 · LINEUP STATE IS A RANKING PREFERENCE, NOT A GATE ══
    # Andy: "make ladder better", after Ha-Seong Kim Under 0.5 HITS sat on
    # the Ladder for two days as a Void — he never played.
    #
    # lineup_state is the most predictive field on an MLB prop in our own
    # data. Measured over every graded prop:
    #
    #     coverage_stub   12898-14386   47.3%   n=27284   void 7.3%
    #     confirmed        3034-2311    56.8%   n= 5345   void 4.2%
    #
    # A 9.5pp gap at z~12.5 — not a small-sample artefact. Stub props sit
    # BELOW breakeven; confirmed props clear it comfortably, and the void
    # rate is nearly double, which on a one-play-per-day compounding surface
    # means the ladder stalls rather than loses.
    #
    # First pass made this a HARD block. Andy: "i dont think the lineup will
    # confirm by the time we get ladder pick out so i am okay with no lineup
    # confirm gate." He is right about the cadence — the ladder publishes
    # early and MLB lineups post later, so a hard gate would zero the ladder
    # most days, and no play at all is worse than a stub play.
    #
    # So it is carried as a PREFERENCE instead: recorded on the candidate and
    # used to BREAK TIES in _pick_best, ahead of tier and edge. Costs nothing
    # on a day with no confirmed props, and takes the confirmed one whenever
    # the choice exists. Today that is the whole difference between Hagen
    # Smith Under 0.5 ER (coverage_stub, a 1.8-inning opener) and Tarik
    # Skubal Over 6.5 Ks (confirmed, a starter with a known workload).
    _lu = str(prop.get('lineup_state') or '').lower()

    gates_passed = 1  # tier
    gate_notes = [f'tier={effective_tier}', f'lineup={_lu or "?"}']

    # Gate 2: odds range
    if odds is not None and -300 <= odds <= 150:
        gates_passed += 1
        gate_notes.append(f'odds={odds:+d}✓')
    elif odds is not None:
        gate_notes.append(f'odds={odds:+d} OUT-OF-RANGE')
        return None  # hard block per feedback_prop_jerry_odds
    else:
        gate_notes.append('odds=?')
        # Hits over 0.5 with null odds is documented trap
        if prop_type == 'hits_over' and (line or 0) <= 0.5:
            return None

    # Gate 3: refit conviction
    refit = prop.get('legacy_refit_conviction') or prop.get('refit_conviction')
    try: refit_i = int(refit) if refit is not None else None
    except (TypeError, ValueError): refit_i = None
    if refit_i is not None and refit_i >= 40:
        gates_passed += 1
        gate_notes.append(f'refit={refit_i}✓')
    elif refit_i is not None:
        gate_notes.append(f'refit={refit_i}')

    # Gate 4: ensemble/playbook conviction
    pb_conv = prop.get('playbook_conviction') or prop.get('legacy_conviction')
    try: pb_conv_i = int(pb_conv) if pb_conv is not None else None
    except (TypeError, ValueError): pb_conv_i = None
    if pb_conv_i is not None and pb_conv_i >= 60:
        gates_passed += 1
        gate_notes.append(f'conv={pb_conv_i}✓')
    elif pb_conv_i is not None:
        gate_notes.append(f'conv={pb_conv_i}')

    # Gate 5: playbook backing THIS DIRECTION specifically
    #
    # 2026-08-24 ROOT-CAUSE FIX for wrong-side ladder qualification:
    # prop_playbook_decisions carries both `direction` (the listed line
    # side, e.g. 'under') AND `playbook_side` (BACK / FADE). Previously
    # this gate incremented on playbook_tier PRIME/STRONG regardless of
    # side — which meant a row like (direction='under', playbook_side='FADE',
    # edge_pp=+22 on OVER) would qualify as 'playbook✓' even though the
    # playbook is actively fading the under. Today's ladder pointed at
    # "Springs Under 3.5 ER" while the playbook said BACK THE OVER at
    # +22 edge. Users following the ladder would be on the wrong side.
    #
    # Fix: hard-disqualify FADE rows here. If a day has NO qualifiers
    # left, the RELAXED-scan fallback (line 460+) picks the next-best
    # candidate. Better to skip a day than ship a wrong-side pick as
    # THE one-play-per-day compounding ladder.
    if playbook_tier in ('PRIME', 'STRONG'):
        ps = (prop.get('playbook_side') or 'BACK').upper()
        if ps == 'FADE':
            gate_notes.append(f'playbook FADES this direction — DISQUALIFIED')
            return None  # hard block — ladder never points at faded side
        gates_passed += 1
        gate_notes.append(f'playbook✓ ({ps})')

    if gates_passed < LADDER_MIN_GATES:
        return None

    # Compose display label
    dir_word = 'Over' if direction == 'over' else 'Under'
    pretty_type = prop_type.replace('_', ' ').replace(' over', '').replace(' under', '')
    pick_label = f'{player} {dir_word} {line} {pretty_type.upper()}'

    return {
        'game_date': prop.get('game_date'),
        'sport': sport,
        'game_id': prop.get('game_id'),
        'matchup': prop.get('matchup') or 'Prop',
        'pick_side': pick_label,
        'market': 'prop',
        'odds_american': odds,
        'tier': effective_tier,
        'conviction': pb_conv_i,
        'model_win_prob': None,      # props don't have MC prob
        'cohort_hit_rate': None,
        'cohort_n': None,
        'consensus_lens': None,
        'edge_pp': None,
        'gates_passed': gates_passed,
        # 2026-10-03 · carried for the ranking preference in the sorts below.
        # Not written to ladder_rung (no such column); used only to break ties.
        '_lineup_confirmed': (_lu == 'confirmed'),
        'qualification_notes': (
            f'PROP · {gates_passed}/5 gates · ' + ' · '.join(gate_notes)
        ),
    }


def scan_and_maybe_qualify(game_date: str, dry_run: bool = False) -> Optional[dict]:
    """Iterate ladder-eligible sports; return first qualifying rung or None."""
    sports = get_ladder_eligible_sports()
    print(f'  ladder-eligible sports: {sports or "NONE (all off-season/preseason)"}')
    candidates = []

    # ══ 2026-10-22 · THE PROP PATH WAS MLB-ONLY AND THAT KILLED THE LADDER ══
    # Andy: "how do we open up Daily Degne and Ladder to all sports and plays
    # as long as theass through additioanl filter assesment".
    #
    # Measured: 57 of 57 rungs ever written are MLB, 38 of them PROPS, and
    # the last rung is 2026-10-19. The ladder reported "waiting for the next
    # qualifier" for three days and it looked like discipline. It was supply.
    # The game-side scan below has been cross-sport for months; this prop
    # scan was pinned to MLB with the comment "Only run for MLB since other
    # sports don't have prop_playbook_decisions rows yet". Other sports have
    # them now - the NHL and NBA boards both write playbook decisions - so
    # the condition outlived its reason and nobody revisited it.
    #
    # Now it iterates every ladder-eligible sport that HAS a props table.
    # NCAAF and NCAAB are absent from PROPS_TABLE on purpose: no props on
    # college, anywhere (feedback_college_sports_no_props).
    #
    # This does NOT loosen a single gate. check_prop_qualifier still has to
    # pass, which is the "additional filter assesment" - opening the supply
    # and lowering the bar are different changes and only the first is here.
    PROPS_TABLE = {
        'MLB': 'mlb_pipeline_props',
        'NFL': 'nfl_pipeline_props',
        'NHL': 'nhl_pipeline_props',
        'NBA': 'nba_pipeline_props',
    }
    for _sp in sports:
        _tbl = PROPS_TABLE.get(_sp)
        if not _tbl:
            continue
        pb = requests.get(f'{SB}/rest/v1/prop_playbook_decisions', headers=H_READ,
            params={'game_date': f'eq.{game_date}', 'sport': f'eq.{_sp}',
                    'playbook_tier': 'in.(PRIME,STRONG)',
                    'select': '*'}, timeout=15).json()
        if not isinstance(pb, list) or not pb:
            continue
        # select=* rather than a column list: nhl/nba_pipeline_props do NOT
        # carry every column mlb_pipeline_props has (player_team is absent on
        # both, checked), and an explicit SELECT naming a missing column
        # returns 400, which a caller reads as "no rows".
        legacy = requests.get(f'{SB}/rest/v1/{_tbl}', headers=H_READ,
            params={'game_date': f'eq.{game_date}', 'select': '*'},
            timeout=20).json()
        try:
            from prop_snapshot_overlay import overlay_from_snapshots
            if isinstance(legacy, list):
                legacy = overlay_from_snapshots(legacy, game_date, sport=_sp)
        except Exception:
            pass
        legacy_map = {}
        if isinstance(legacy, list):
            for p in legacy:
                k = (p.get('player_name'), p.get('prop_type'),
                     p.get('direction'), p.get('prop_line'))
                legacy_map[k] = p
        prop_qualifiers = 0
        for pb_row in pb:
            k = (pb_row.get('player_name'), pb_row.get('prop_type'),
                 pb_row.get('direction'), pb_row.get('prop_line'))
            lg = legacy_map.get(k, {})
            merged = {**lg, **pb_row}  # playbook wins on shared keys
            if 'tier' in lg and 'legacy_tier' not in merged:
                merged['legacy_tier'] = lg['tier']
            rung = check_prop_qualifier(merged, _sp)
            if rung:
                candidates.append(rung); prop_qualifiers += 1
        print(f'  {_sp} props evaluated: {len(pb)} playbook PRIME/STRONG · '
              f'{prop_qualifiers} qualified for ladder')

    for sport in sports:
        ctx_tbl = CTX_TABLE.get(sport)
        if not ctx_tbl: continue
        # 2026-08-19 bug fix: prior select included home_ml/away_ml which
        # don't exist in mlb_game_context (only _close columns) — whole query
        # returned a PostgREST error dict instead of a list, isinstance list
        # check silently skipped every sport, ladder never fired.
        # 2026-09-15: per-sport SELECT. `signal_confluence_support` only
        # exists in mlb_game_context. NFL/NCAAF/NCAAB have
        # `signal_confluence_net` and `signal_confluence_breakdown`
        # instead. Prior monolithic SELECT 400-errored on those sports
        # ("column X does not exist"), silently skipping every non-MLB
        # game from the ladder scan. Now: build the select list per-
        # sport, omit columns that don't exist. Downstream check_qualifier
        # reads via .get() with `or 0` defaults so missing values gate
        # to 0 without crashing.
        # ══ 2026-10-22 · STOP GUESSING THE COLUMN NAMES: SELECT * ══
        # This file has now been patched three times for the same reason — a
        # named column that one sport's context table does not have, which
        # makes PostgREST 400 and reads downstream as "no rows":
        #   09-15  per-sport ml_cols, because MLB names them one way
        #   10-03  consensus_fade_flag, absent on nhl_game_context, which
        #          excluded NHL from the ladder entirely and showed up only
        #          as "NHL query failed" in a log nobody was reading
        #   10-22  close_home_ml, absent on nba_game_context — so the NBA
        #          GAME-SIDE scan has been failing since NBA went live and
        #          no NBA side could ever reach the ladder
        #
        # The binary `if sport == 'MLB' else ...` was wrong the moment a
        # fourth sport arrived: NBA uses MLB's naming, not "everything
        # else's". Probed directly — mlb and nba carry home_ml_close, nfl
        # and nhl and ncaab carry BOTH conventions.
        #
        # check_qualifier already coalesces every spelling at read time
        # (`row.get('home_ml_close') or row.get('close_home_ml') or ...`), so
        # the column list was only ever a bandwidth optimisation on one row
        # per game. select=* costs nothing here and cannot 400. The next
        # sport added needs no edit.
        # select=* also means both signal_confluence_support (MLB) and
        # signal_confluence_net (the others) arrive when present, so the
        # row-level mapping below keeps working without a per-sport select.
        select_cols = '*'
        r = requests.get(f'{SB}/rest/v1/{ctx_tbl}', headers=H_READ,
            params={'game_date': f'eq.{game_date}', 'select': select_cols},
            timeout=15).json()
        if not isinstance(r, list):
            print(f'  ⚠️  {sport} query failed: {r}')
            continue
        # For non-MLB sports, alias signal_confluence_net → _support so
        # downstream check_qualifier code path works without special-casing.
        if sport != 'MLB':
            for row in r:
                if isinstance(row, dict) and 'signal_confluence_net' in row:
                    row['signal_confluence_support'] = row.get('signal_confluence_net')
        for row in r:
            rung = check_qualifier(row, sport)
            if rung: candidates.append(rung)
    if not candidates:
        # 2026-08-20: skip-day loosening. User: "ladder can skip one day but
        # shouldn't skip more than two." If no qualifier fires AND the last
        # 2 game_dates already had no rung, drop back to relaxed thresholds
        # and pick the BEST-available candidate even if it doesn't clear
        # the standard 3-of-5 gate.
        if _days_since_last_rung(game_date) >= 2:
            print('  ⚠️ 2+ days since last ladder rung — running RELAXED scan')
            relaxed = _relaxed_scan(game_date, sports)
            if relaxed:
                print(f'  🎯 Relaxed qualifier: {relaxed["matchup"]} · {relaxed["pick_side"]}')
                return relaxed
        print('  no qualifier fired — ladder stays parked')
        return None
    # Rank by (gates_passed DESC, tier PRIME>STRONG>LEAN, normalized edge DESC).
    # 2026-08-20: edge_pp normalized via SPORT_EDGE_MULTIPLIER so cross-sport
    # candidates compare fairly. NHL +7pp edge > MLB +7pp edge because puckline
    # math compresses edges — multipliers correct that.
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    def _norm_edge(c):
        e = c.get('edge_pp')
        if e is None: return -999
        return e * SPORT_EDGE_MULTIPLIER.get(c.get('sport'), 1.0)
    candidates.sort(key=lambda c: (
        # 2026-10-03 · CONFIRMED LINEUP RANKS FIRST, ahead of the gate count.
        # Stub props grade 47.3% (n=27,284) against 56.8% for confirmed
        # (n=5,345) — a 9.5pp gap at z~12.5, at double the void rate. One
        # additional passed gate has no measurement behind it at all, so when
        # the two disagree the lineup wins. Today that is the difference
        # between Hagen Smith Under 0.5 ER (5/5 gates, coverage_stub, a
        # 1.8-inning opener) and Tarik Skubal Over 6.5 Ks (4/5, confirmed, a
        # starter with a known workload).
        #
        # Still only a PREFERENCE — it reorders qualifiers, it never rejects
        # one. Andy: "the ladder should never go 2-3 days without firing."
        # Nothing here can empty the board; the RELAXED scan after 2 dry days
        # is untouched.
        0 if c.get('_lineup_confirmed') else 1,
        -(c.get('gates_passed') or 0),
        tier_rank.get(c.get('tier'), 9),
        -_norm_edge(c),
    ))
    winner = candidates[0]
    edge_raw = winner.get('edge_pp')
    mult = SPORT_EDGE_MULTIPLIER.get(winner.get('sport'), 1.0)
    edge_str = (f'{edge_raw:+.1f}pp × {mult} = {edge_raw*mult:+.1f}'
                if edge_raw is not None else 'n/a')
    print(f'  🎯 Ladder qualifier: {winner["matchup"]} · {winner["pick_side"]} '
          f'({winner["gates_passed"]}/5 gates, edge {edge_str})')
    return winner


def _days_since_last_rung(game_date: str) -> int:
    """How many calendar days since the last ladder_rung fired (any sport).
    Returns 0 if a rung exists for today OR yesterday, 1 for two days ago,
    etc. Used for skip-day loosening — after 2+ dry days, we relax gates."""
    r = requests.get(f'{SB}/rest/v1/ladder_rung', headers=H_READ,
        params={'select': 'game_date', 'order': 'game_date.desc', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return 0
    rows = r.json()
    if not isinstance(rows, list) or not rows: return 99  # never fired
    last = rows[0].get('game_date')
    if not last: return 0
    try:
        from datetime import date
        gd = date.fromisoformat(game_date)
        ld = date.fromisoformat(last)
        return (gd - ld).days
    except Exception:
        return 0


def _relaxed_scan(game_date: str, sports: list) -> Optional[dict]:
    """Relaxed re-scan when ladder has been dark 2+ days. Drops the 3/5
    gate requirement AND lowers edge threshold to +2pp. Picks the best-
    scoring candidate that clears the minimal safety bar (no sharp-fade,
    real odds, not a refit-trap). Ensures ladder never sits empty >2 days.

    Universal across sports — same signal reads, just softer gates.
    """
    from copy import deepcopy
    original_edge = globals()['LADDER_EDGE_MIN_PP']
    original_consensus = globals()['LADDER_CONSENSUS_MIN']
    original_cohort_hit = globals()['LADDER_COHORT_HIT_MIN']
    original_cohort_n = globals()['LADDER_COHORT_N_MIN']
    original_min_gates = globals()['LADDER_MIN_GATES']
    # ══ 2026-11-04 · THE TIER WHITELIST WAS THE ONE GATE NOT RELAXED ════
    # ANDY: "Ladder should be every other day at least." Measured: rungs on
    # 16 of the last 30 days but only 4 of the last 15.
    #
    # This scan already drops five constants (edge 10->2pp, consensus 4->1,
    # cohort 60%/n30 -> 50%/n10, gates 3of5 -> 2of5) and still returned
    # None, because LADDER_TIER_MIN = {PRIME, STRONG} is enforced at the TOP
    # of check_qualifier and was never in the relaxed set. Instrumented
    # tonight: 25 rows reached check_qualifier across NBA/NHL/NCAAB/NCAAF,
    # 8 of them LEAN, and 0 passed.
    #
    # So LEAN is admitted HERE ONLY — after 2+ dry days, which is exactly
    # what this function exists for. The normal scan keeps PRIME/STRONG.
    #
    # Why the distinction matters and is kept: the ladder COMPOUNDS, so a
    # weak leg is worse here than on a single-pick surface, which is the
    # reasoning behind both "LEAN removed — no ladder authority" and
    # LADDER_ABS_JUICE_CAP = -250 ("compounding math destroys past -250").
    # Relaxing the floor for a dry streak is a different thing from
    # relaxing it by default, and the rung is stamped
    # '[RELAXED · 2+ dry days]' so the record shows which it was.
    original_tier_min = globals()['LADDER_TIER_MIN']
    try:
        globals()['LADDER_TIER_MIN'] = {'PRIME', 'STRONG', 'LEAN'}
        globals()['LADDER_EDGE_MIN_PP'] = 2.0
        globals()['LADDER_CONSENSUS_MIN'] = 1
        globals()['LADDER_COHORT_HIT_MIN'] = 50.0
        globals()['LADDER_COHORT_N_MIN'] = 10
        globals()['LADDER_MIN_GATES'] = 2  # 2 of 5 in relaxed mode
        candidates = []
        _seen: dict = {}      # sport -> picks that reached the gates
        _rej: dict = {}       # reason -> count, so a park is explainable
        for sport in sports:
            ctx_tbl = CTX_TABLE.get(sport)
            if not ctx_tbl: continue
            # select=* for the reason spelled out in scan_qualifiers: three
            # separate 400s from named columns one sport lacks, the latest
            # being close_home_ml on nba_game_context.
            select_cols = '*'
            r = requests.get(f'{SB}/rest/v1/{ctx_tbl}', headers=H_READ,
                params={'game_date': f'eq.{game_date}', 'select': select_cols},
                timeout=15).json()
            if not isinstance(r, list): continue
            if sport != 'MLB':
                for row in r:
                    if isinstance(row, dict) and 'signal_confluence_net' in row:
                        row['signal_confluence_support'] = row.get('signal_confluence_net')
            for row in r:
                pp = row.get('primary_play') or {}
                # In relaxed mode, accept any picked play with a tier + label
                # that has real odds and isn't sharp-fade flagged.
                if not pp.get('tier') or not pp.get('label'): continue
                _seen[sport] = _seen.get(sport, 0) + 1
                if row.get('consensus_fade_flag') is True:
                    _rej['consensus_fade'] = _rej.get('consensus_fade', 0) + 1
                    continue
                rung = check_qualifier(row, sport)
                if not rung:
                    # ══ 2026-11-04 · A PARKED LADDER MUST SAY WHY ════════
                    # The scan printed one line, 'no qualifier fired', for
                    # every possible cause: wrong market, tier below the
                    # floor, too few gates. Andy's standing ask is a rung
                    # every other day, so a silent park is unactionable -
                    # I had to instrument this by hand to learn that 25
                    # rows reached check_qualifier and 0 passed.
                    #
                    # check_qualifier has many bare `return None` exits, so
                    # tally what we can see from here rather than rewrite
                    # its signature: tier and market are both on the row.
                    _t = str(pp.get('tier') or '?').upper()
                    _m = str(pp.get('type') or '?').lower()
                    if _t not in LADDER_TIER_MIN:
                        _rej[f'tier<{"/".join(sorted(LADDER_TIER_MIN))}'] =                             _rej.get(f'tier<{"/".join(sorted(LADDER_TIER_MIN))}', 0) + 1
                    elif _m not in ('ml', 'spread', 'rl', 'over', 'under',
                                    'total', 'yrfi', 'nrfi'):
                        _rej[f'market:{_m}'] = _rej.get(f'market:{_m}', 0) + 1
                    else:
                        _rej['gates/confluence'] =                             _rej.get('gates/confluence', 0) + 1
                    continue
                # ══ HARD WIN-PROBABILITY FLOOR ON A COMPOUNDING PRODUCT ══
                # Admitting LEAN made the relaxed scan fire — on 'Fairleigh
                # Dickinson ML @ +2400' with model_win_prob 5.6. Our own
                # model said that leg wins 5.6% of the time, and the ladder
                # COMPOUNDS, so one such leg destroys the rung.
                #
                # It got through because win probability is one of five
                # gates and relaxed mode needs only two: a pick can clear
                # consensus and edge while failing win-prob catastrophically.
                # The docstring promises candidates "clear the minimal safety
                # bar" — 5.6% is not a safety bar, so make it one. This is a
                # HARD floor, not a gate: no combination of other signals
                # can buy a leg past it.
                _wp = rung.get('model_win_prob')
                try:
                    _wpf = float(_wp)
                except (TypeError, ValueError):
                    _wpf = None
                if _wpf is None or _wpf < LADDER_RELAXED_WIN_PROB_FLOOR:
                    print(f'    - relaxed: refused {rung.get("pick_side")!r} '
                          f'— model win prob {_wp}% is below the '
                          f'{LADDER_RELAXED_WIN_PROB_FLOOR:.0f}% hard floor '
                          f'for a compounding leg')
                    continue
                rung['qualification_notes'] = '[RELAXED · 2+ dry days] ' + (rung.get('qualification_notes') or '')
                candidates.append(rung)
        if not candidates:
            print(f'    - relaxed: scanned {sum(_seen.values())} pick(s) '
                  f'{dict(sorted(_seen.items()))}, none qualified '
                  f'— refused by {dict(sorted(_rej.items()))}')
            return None
        tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
        candidates.sort(key=lambda c: (
            0 if c.get('_lineup_confirmed') else 1,   # see lineup_state note
            -(c.get('gates_passed') or 0),
            tier_rank.get(c.get('tier'), 9),
            -(c.get('edge_pp') or -999),
        ))
        return candidates[0]
    finally:
        globals()['LADDER_EDGE_MIN_PP'] = original_edge
        globals()['LADDER_CONSENSUS_MIN'] = original_consensus
        globals()['LADDER_COHORT_HIT_MIN'] = original_cohort_hit
        globals()['LADDER_COHORT_N_MIN'] = original_cohort_n
        globals()['LADDER_MIN_GATES'] = original_min_gates
        globals()['LADDER_TIER_MIN'] = original_tier_min


def upsert_rung_and_state(rung: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f'  [DRY] would upsert rung: {rung}')
        return
    # 2026-09-15 fix: previously stripped gates_passed with a comment
    # about the 20260819_ladder_gates_passed.sql migration not being
    # applied. Migration IS applied — verified by direct PATCH round-trip
    # today. Column stayed NULL on all 15 existing rows because this
    # writer explicitly dropped it. Downstream retro auditing of "which
    # gates passed for each qualifier" was blank as a result.
    # Now: write gates_passed directly. Keep the qualification_notes
    # summary line for backward-compat with old consumers that still
    # parse it.
    # 2026-10-03 · drop internal-only keys. _lineup_confirmed is carried on
    # the candidate purely to break ties in the sorts above; ladder_rung has
    # no such column, and PostgREST 400s on an unknown one — which would
    # take the whole rung write down.
    write_row = {k: v for k, v in rung.items() if not k.startswith('_')}
    write_row['qualification_notes'] = (
        f"[gates={rung.get('gates_passed')}/5] " + (rung.get('qualification_notes') or '')
    )[:500]
    pr = requests.post(f'{SB}/rest/v1/ladder_rung',
        headers={**H_WRITE, 'Prefer': 'return=representation'},
        json=write_row, timeout=15)
    if pr.status_code not in (200, 201, 204):
        print(f'  rung insert failed: {pr.status_code} {pr.text[:200]}')
        return
    inserted = pr.json()
    rung_id = inserted[0]['id'] if isinstance(inserted, list) and inserted else None
    if not rung_id: return
    # 2026-09-17: PUBLISH-LOCK. Ladder's rung IS the pick a user
    # sees — lock tier + conviction at publish time via shared
    # publish_lock table. market='ladder', source_id=rung id.
    # Fail-soft.
    try:
        from prop_publish_lock import lock_publish as _lock
        _lock(rung.get('sport') or 'MLB',
              'ladder',
              rung_id,
              (rung.get('tier') or '').upper(),
              rung.get('conviction'),
              'ladder')
    except Exception as _e:
        print(f'  ⚠ publish_lock (ladder) failed silently: {_e}')
    # Update ladder_state
    st = requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.1',
        headers=H_WRITE, json={
            'status': 'active',
            'active_rung_id': rung_id,
            'last_updated_at': datetime.now(timezone.utc).isoformat(),
            'note': f'Active rung: {rung["matchup"]} · {rung["pick_side"]}',
        }, timeout=10)
    print(f'  wrote rung id={rung_id} + updated ladder_state (status: {st.status_code})')


def set_state_waiting(dry_run: bool = False) -> None:
    if dry_run:
        print('  [DRY] would set ladder_state=waiting'); return
    requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.1',
        headers=H_WRITE, json={
            'status': 'waiting',
            'active_rung_id': None,
            'last_updated_at': datetime.now(timezone.utc).isoformat(),
            'note': 'Waiting for the next qualifier',
        }, timeout=10)
    print('  ladder_state → waiting')


def already_locked_today(game_date: str) -> bool:
    """2026-08-20: no mid-day swaps. If a rung already exists for
    game_date, keep it. User caught 4 swaps in one day (Milwaukee →
    Grayson → Nats → Grayson) as recompute picked different tops as
    data freshened. Ladder is a "step of the day" product — one pick
    per day, no rethink. Once a game grades LOSS, THAT'S when we allow
    the next day's pick to be different.

    Returns True if a rung for this date exists (regardless of result).
    """
    r = requests.get(f'{SB}/rest/v1/ladder_rung',
        params={'game_date': f'eq.{game_date}', 'select': 'id,pick_side'},
        headers=H_READ, timeout=10)
    if r.status_code != 200: return False
    rows = r.json()
    if isinstance(rows, list) and rows:
        print(f'  🔒 ladder already locked for {game_date}: '
              f'{rows[0].get("pick_side")} (id={rows[0].get("id")}) — no swap')
        return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--force', action='store_true',
                   help='Bypass the day-lock and pick a new rung (dev / recovery only)')
    args = p.parse_args()
    game_date = args.date or _et_today()
    print(f'=== Steam Room Ladder · {game_date} ===')

    # Day-lock: skip re-picking if today already has a rung. Prevents the
    # every-30-min-recompute swap thrashing that hit today.
    if not args.force and not args.dry_run and already_locked_today(game_date):
        return

    rung = scan_and_maybe_qualify(game_date, dry_run=args.dry_run)
    if rung:
        upsert_rung_and_state(rung, dry_run=args.dry_run)
    else:
        set_state_waiting(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
