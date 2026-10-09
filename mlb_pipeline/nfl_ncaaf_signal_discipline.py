"""Signal-driven tier discipline for NFL + NCAAF primary_play.

Reads real per-signal performance data from v_signal_records and
applies two hard gates to nfl_game_context.primary_play + ncaaf_game_context.primary_play:

  1. LR-warn hard-cap: if LR shadow DISAGREES with pick direction
     strongly (p_home_win >= 0.60 opposite pick OR <= 0.40 opposite
     pick), cap the tier at PASS (or LEAN if we want to keep it on
     the card as a low-conviction lean). The published hit rate is read
     live from v_signal_records per sport (2026-09-26: NCAAF LR-warn
     7-29, 19.4%, n=36 — against 66.7% on LR-agree). It used to be the
     literal "4.3%", measured once on n=22 and never refreshed.

     RESOLVED 2026-09-26 (Andy: "flip as necessary for those"). At
     19.4% on n=36 this is not a dampened lean, it is a losing cohort,
     so an UNLOCKED pick carrying it is now PASS rather than a playable
     LEAN. A pick the database has already stamped (20260926b) keeps the
     tier it shipped with and only gets its explanatory text refreshed —
     re-tiering a published pick would break the lock built three days
     earlier to stop picks moving between runs, and would rewrite
     receipts after users had already seen them. All 18 on the 09-26
     board were locked and playing that day, so none of them moved.

  2. Anchor cap: if spread_anchor_weight > 0 AND tier in
     PRIME/STRONG, downgrade to LEAN. The anchor fires when the model
     is uncertain — anchored picks should never ride at top tier.

     2026-09-27 RE-MEASURE. This said "anchored picks hit 30% (n=40)".
     That is wrong. Over every graded NFL + NCAAF pick carrying an
     anchor weight (n=92 decided):
         anchored       49-43   53.3%
         not anchored  130-86   60.2%
         NCAAF anchored 34-37   47.9%  (n=71)
         NFL   anchored 15-6    71.4%  (n=21)
     The ~7pp gap justifies a demotion; 30% never existed. The figure
     was also being shown to users — see the flag text below.

Runs post-nfl_game_context.build (after primary_play is set) and
BEFORE sharp_card composition (so Sharp Card sees the gated tier).

Idempotent: reads primary_play, applies gates, PATCHes back. Safe
to run multiple times per day.

CLI:
  python nfl_ncaaf_signal_discipline.py                 # today
  python nfl_ncaaf_signal_discipline.py --date 2026-09-14
  python nfl_ncaaf_signal_discipline.py --sport NCAAF
  python nfl_ncaaf_signal_discipline.py --dry-run
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta, date as _date
from typing import Optional

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p); break

SB = os.environ.get('SUPABASE_URL')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
if not (SB and K):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')

H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Gate constants
LR_WARN_HARD_THRESHOLD = 0.60   # LR probability on OPPOSITE side must exceed this to trigger warn-cap
ANCHOR_CAP_TIERS = {'PRIME', 'STRONG'}   # tiers to downgrade when anchor fires
ANCHOR_CAP_NEW_TIER = 'LEAN'

# 2026-09-20 — make the cap DATA, not just prose.
#
# On the 09-20 NFL slate, 5 of 14 picks carried PRIME/STRONG while their
# own `sub` read "capped to LEAN":
#     GB @ NYJ   PRIME/66   _pre_lr COVERAGE   (LR override promoted it)
#     WAS @ DAL  STRONG/63  _pre_lr LEAN
#     SEA @ ARI  STRONG/62  _pre_lr LEAN
#     JAX @ DEN  STRONG/56  _pre_lr LEAN
#
# Cause: this module wrote the cap into `tier` and `sub`, but the LR
# override in defensive_gates builds a FRESH primary_play and copies only
# a handful of fields forward. The capped tier was discarded; the warning
# sentence survived. The user saw PRIME on a pick whose own text said it
# should be LEAN because anchored picks hit 30%.
#
# Fix: record the cap as a structured field the override can honour.
# `tier`/`sub` still change for anything reading them directly.
_TIER_RANK = {'PASS': 0, 'SKIP': 0, 'LIGHT': 1, 'COVERAGE': 2,
              'LEAN': 3, 'STRONG': 4, 'PRIME': 5}

# 2026-09-26 — the published hit rate was a LITERAL, and it was stale.
#
# Andy: "LR-warn hits 4.3% historically is published under a playable
# LEAN. If spots where LR warns against the pick hit 4.3% of the time,
# that isn't a capped LEAN — it's a fade."
#
# He is right twice over. The 4.3% was measured once, on n=22 NCAAF
# games on 09-12, and hardcoded into the user-facing string — while this
# module's own docstring claims it "reads real per-signal performance
# data from v_signal_records". It never did. The live number today is
# 19.4% (7-29, n=36 NCAAF), so for two weeks every card carrying this
# flag published a figure wrong by 15 points.
#
# Now sourced live, with the sample size attached — per
# feedback_sample_size_with_pct, every percentage we show carries its n.
# If the view has no row for a sport yet, the sentence simply omits the
# rate rather than inventing one.
_LR_WARN_CACHE: dict = {}


def _lr_warn_record(sport: str) -> Optional[dict]:
    """Live LR-warn hit rate for a sport, or None. Cached per run."""
    key = (sport or '').upper()
    if key in _LR_WARN_CACHE:
        return _LR_WARN_CACHE[key]
    rec = None
    try:
        r = requests.get(f'{SB}/rest/v1/v_signal_records', headers=H_READ,
                         params={'select': 'wins_lifetime,losses_lifetime,'
                                           'hit_pct_lifetime',
                                 'sport': f'eq.{key}',
                                 'signal_key': 'eq.LR_SHADOW',
                                 'kind': 'eq.warn', 'limit': '1'}, timeout=20)
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, list) and body:
                rec = body[0]
    except Exception as e:
        print(f'  ⚠ LR-warn record lookup failed ({type(e).__name__}) — '
              f'flag will omit the rate')
    _LR_WARN_CACHE[key] = rec
    return rec


#: Floor below which the warn cohort's own record cannot steer the gate.
_LR_WARN_EVIDENCE_MIN_N = 40
#: -110 breakeven. A cohort has to clear THIS, not 50%, to be worth playing.
_LR_WARN_BREAKEVEN = 52.38


def lr_warn_verdict(sport: str) -> dict:
    """Should the LR-warn cap fire for THIS sport? -> {cap, shadow, why}

    ══ 2026-10-09 · THE GATE WAS SPORT-BLIND AND NFL DISAGREES ══
    This module caps any pick the LR shadow strongly contradicts, and the
    justification above cites NCAAF numbers only ("7-29, 19%, n=36"). But
    v_signal_records — already read by _lr_warn_record for the user-facing
    SENTENCE, and never consulted for the DECISION — says the same gate
    performs oppositely in the two leagues:

        NCAAF  LR_SHADOW/warn   26-52   33.3%   n=78   2SE +/-11.3pp
        NFL    LR_SHADOW/warn   11-4    73.3%   n=15   2SE +/-25.8pp

    NCAAF is emphatic and the cap is right there: 33.3% sits far below the
    52.38% breakeven, well outside its own band. NFL points the other way,
    and Andy caught a live instance — TB +8.5 on 2026-10-08 carried this
    warning, was capped to COVERAGE, and won by 16.5 points. His read was
    "was correct on TB +8.5 yesterday just the conviction was off."

    SO WHY NOT JUST TURN IT OFF FOR NFL: because n=15 gives a 2SE band of
    +/-25.8pp, which comfortably contains breakeven. Flipping a suppression
    gate on fifteen games is the same mistake as trusting any other n=15
    result, and this session has already retracted two findings for exactly
    that. feedback_suppression_gate_needs_shadow is the standing rule —
    shadow a suppression gate and exit on evidence, do not guess.

    So: cap where the evidence supports capping, keep capping where the
    evidence is merely ambiguous, and in that ambiguous case STAMP the pick
    so the counterfactual is recorded and the question can actually be
    settled instead of re-argued. The gate only stops firing once a sport's
    warn cohort clears breakeven by more than two standard errors on at
    least _LR_WARN_EVIDENCE_MIN_N decisions.
    """
    rec = _lr_warn_record(sport)
    if not rec or rec.get('hit_pct_lifetime') is None:
        return {'cap': True, 'shadow': False,
                'why': 'no warn-cohort record for this sport — cap by default'}
    w = int(rec.get('wins_lifetime') or 0)
    l = int(rec.get('losses_lifetime') or 0)
    n = w + l
    if n < 1:
        return {'cap': True, 'shadow': False, 'why': 'empty warn cohort'}
    hit = float(rec['hit_pct_lifetime'])
    se2 = 2 * (0.5 / n ** 0.5) * 100
    beats = hit - _LR_WARN_BREAKEVEN > se2
    if beats and n >= _LR_WARN_EVIDENCE_MIN_N:
        return {'cap': False, 'shadow': False,
                'why': (f'{sport} warn cohort {w}-{l} ({hit:.1f}%, n={n}) '
                        f'beats breakeven by >2SE ({se2:.1f}pp) — the cap is '
                        f'refuted, stop firing it')}
    if hit > _LR_WARN_BREAKEVEN:
        # Points the other way but not provably — cap, and record it.
        return {'cap': True, 'shadow': True,
                'why': (f'{sport} warn cohort {w}-{l} ({hit:.1f}%, n={n}) is '
                        f'ABOVE breakeven but inside its 2SE band '
                        f'({se2:.1f}pp) — still capping, shadowing for '
                        f'evidence')}
    return {'cap': True, 'shadow': False,
            'why': (f'{sport} warn cohort {w}-{l} ({hit:.1f}%, n={n}) is '
                    f'below breakeven — cap justified')}


#: A user-facing rate needs more than the old n>=10. See _lr_warn_sentence.
_LR_WARN_PUBLISH_MIN_N = 20
_LR_WARN_TIER_CACHE: dict = {}


def _lr_warn_tier_record(sport: str, tier: str) -> Optional[tuple]:
    """(w, l) for this sport's warn cohort AT THIS TIER, or None.

    ══ 2026-10-09 · THE POOLED RATE WAS MIXING TIERS ══
    v_signal_records groups by (sport, signal_key, kind) only, so the
    sentence quoted ONE number across every tier. Broken out by pick_tier,
    the NFL warn cohort that reads 11-4 (73.3%) overall is:

        LEAN       8-0   (100%)     <- carries the entire result
        COVERAGE   3-2   ( 60%)
        STRONG     0-2   (  0%)

    So a user reading a STRONG pick was shown "73%" for a tier that had gone
    0-2, and the 73% itself rests on a single eight-game streak. Pooling
    across tiers is exactly feedback_tier_mix_reverses_the_sign. NCAAF is
    uniformly bad by comparison (COVERAGE 1-9, LEAN 20-38, STRONG 2-4), which
    is why its pooled 33.3% was not misleading — it happened to agree with
    every cell.

    Reads signal_attribution directly because the view cannot express this.
    """
    k = ((sport or '').upper(), (tier or '').upper())
    if k in _LR_WARN_TIER_CACHE:
        return _LR_WARN_TIER_CACHE[k]
    out = None
    try:
        r = requests.get(f'{SB}/rest/v1/signal_attribution', headers=H_READ,
                         params={'select': 'result',
                                 'sport': f'eq.{k[0]}',
                                 'signal_key': 'eq.LR_SHADOW',
                                 'kind': 'eq.warn',
                                 'pick_tier': f'eq.{k[1]}',
                                 'result': 'in.(W,L)',
                                 'limit': '2000'}, timeout=20)
        if r.status_code == 200 and isinstance(r.json(), list):
            res = [str(x.get('result')) for x in r.json()]
            out = (res.count('W'), res.count('L'))
    except Exception as e:
        print(f'  ⚠ LR-warn tier record lookup failed ({type(e).__name__})')
    _LR_WARN_TIER_CACHE[k] = out
    return out


def _lr_warn_sentence(sport: str, tier: str = '') -> str:
    """The warning's track record AT THE TIER THIS PICK WILL SHIP AS.

    Quotes the tier-specific cohort, not the pooled one, and stays silent
    rather than publishing a rate off a thin cell. Two guards, both of which
    the previous version failed:
      * TIER-SCOPED, so a STRONG pick is not shown a LEAN cohort's record.
      * n >= _LR_WARN_PUBLISH_MIN_N, raised from 10. NFL's pooled figure was
        n=15 with 8 of it in one cell; publishing a percentage off that is
        the kind of number Andy has had to correct before (the hardcoded
        4.3% that was wrong by 15 points for two weeks).
    """
    rec = _lr_warn_tier_record(sport, tier) if tier else None
    if rec:
        w, l = rec
        n = w + l
        if n >= _LR_WARN_PUBLISH_MIN_N:
            return (f' At this tier, picks carrying this warning have gone '
                    f'{w}-{l} ({100.0 * w / n:.0f}%, n={n}).')
        # Tier cell too thin — say nothing rather than fall back to the
        # pooled figure, which is what made this misleading in the first
        # place.
        return ''
    # No tier given (caller predates this change): fall back to pooled, but
    # only at the raised floor.
    pooled = _lr_warn_record(sport)
    if not pooled or pooled.get('hit_pct_lifetime') is None:
        return ''
    w = pooled.get('wins_lifetime') or 0
    l = pooled.get('losses_lifetime') or 0
    n = w + l
    if n < _LR_WARN_PUBLISH_MIN_N:
        return ''
    return (f' Picks carrying this warning have gone {w}-{l} '
            f'({float(pooled["hit_pct_lifetime"]):.0f}%, n={n}, all tiers).')


def _record_cap(pp: dict, cap_tier: str, cap_conv: int, reason: str) -> None:
    """Apply a cap AND leave a durable record of it on the pick.

    Keeps the strictest cap when several gates fire, so a later, looser
    gate cannot quietly undo a stricter one.
    """
    prev = pp.get('_discipline_cap') or {}
    prev_tier = str(prev.get('tier') or '').upper()
    if prev_tier and _TIER_RANK.get(prev_tier, 9) <= _TIER_RANK.get(cap_tier, 9):
        cap_tier = prev_tier
        cap_conv = min(int(prev.get('max_conviction') or cap_conv), cap_conv)
    # ══ 2026-10-03 · A CAP MUST NEVER RAISE A TIER ══
    # This compared only against a PREVIOUS cap, not against the tier the
    # pick currently holds, so "cap at LEAN" PROMOTED anything below LEAN.
    # Surfaced the moment the window widened: three locked NFL picks moved
    # COVERAGE -> LEAN on the LR-warn gate — i.e. the gate for a cohort
    # running 17-40 (29.8%) pushed three picks from unpublished into a
    # published tier. The pre-existing one-day window hid it because the
    # rows it touched were already LEAN or above.
    #
    # Same family as feedback_tier_demotion_needs_stake_boundary: a demotion
    # that lands on the tier you already hold is a no-op, and one that lands
    # ABOVE it is an upgrade wearing a demotion's name.
    cur_tier = str(pp.get('tier') or '').upper()
    if cur_tier and _TIER_RANK.get(cur_tier, 9) < _TIER_RANK.get(cap_tier, 9):
        cap_tier = cur_tier
    reasons = list(prev.get('reasons') or [])
    if reason not in reasons:
        reasons.append(reason)
    pp['tier'] = cap_tier
    pp['conviction'] = min(int(pp.get('conviction') or 0), cap_conv)
    pp['_discipline_cap'] = {'tier': cap_tier, 'max_conviction': cap_conv,
                             'reasons': reasons}


def _strip_lr_warn(pp: dict) -> None:
    """Remove any previously written LR-warn sentence from `sub`.

    2026-09-26: the published rate moved from a hardcoded 4.3% to the
    live figure, but _append_flag only skips EXACT duplicates — so a
    re-run would have left the stale sentence in place and appended the
    new one beside it, showing users two different hit rates for the same
    signal in one line. The old text has to come out before the new one
    goes in. Matches on the stable prefix, not on the rate, so it also
    catches whatever the rate happened to be on an older run.
    """
    sub = str(pp.get('sub') or '')
    if '⚠ LR shadow warns other way' not in sub:
        return
    kept = []
    for part in sub.split(' · '):
        if part.strip().startswith('⚠ LR shadow warns other way'):
            continue
        kept.append(part)
    pp['sub'] = ' · '.join(kept).strip(' ·')


def _append_flag(pp: dict, flag: str) -> None:
    """Append a gate warning to `sub` at most once.

    GB @ NYJ carried the identical anchor warning FOUR times on 09-20 —
    the module re-ran and blindly appended each pass.
    """
    sub = str(pp.get('sub') or '').strip()
    if flag in sub:
        return
    pp['sub'] = f'{sub} · {flag}' if sub else flag


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _fetch_games(sport: str, game_date: str, days: int = 0) -> list[dict]:
    """Games from `game_date` through `game_date + days` inclusive.

    ══ 2026-10-03 · THE PASS BRANCH WAS STRUCTURALLY UNREACHABLE ══
    This filtered `game_date = eq.<one day>`, defaulting to TODAY. The
    LR-warn gate below has two branches: an unlocked pick becomes PASS, a
    locked one is only capped to LEAN because re-tiering a published pick
    would rewrite a receipt. NCAAF's lock window is Thu 8am ET -> Sun
    (lock_football_slate.py), so by the time a Saturday game's own date
    arrived its pick was ALWAYS already stamped — and a one-day window
    never looked at it any earlier.

    Net effect measured across all history: the gate chose CAP 25 times and
    PASS once. The fade it was built to apply in 09-26 has essentially never
    been applied, and 14 picks on the 10-03 board shipped as playable LEANs
    while sitting in a cohort that is 17-40, 29.8% (n=57) against 92-47,
    66.2% (n=139) for picks the LR shadow agrees with.

    The proof it was the window and not the lock: on 10-03 there were 8
    future NCAAF picks already carrying a strong LR disagreement, 6 of them
    still UNLOCKED, and every one had `_discipline_cap` absent entirely —
    the pass had never evaluated them at all. The monotonic protection in
    _record_cap works fine; nothing was overwriting a PASS, because no PASS
    was ever produced.

    So this is a read-window bug, the same shape as
    project_read_window_five_constants_929 (NFL lost 14 Sunday reads every
    Wednesday to a window that only looked at one day). Widening it lets the
    gate decide while the pick is still unlocked, which is also the only
    point at which PASS is the honest answer — the locked carve-out then
    protects what it was written to protect instead of swallowing the slate.
    """
    tbl = 'nfl_game_context' if sport == 'NFL' else 'ncaaf_game_context'
    params = {
        'select': 'game_id,game_date,home_team,away_team,primary_play,'
                  'spread_anchor_weight,pick_locked_at',
    }
    if days and days > 0:
        end = (_date.fromisoformat(game_date)
               + timedelta(days=days)).isoformat()
        params['game_date'] = f'gte.{game_date}'
        params['and'] = f'(game_date.lte.{end})'
    else:
        params['game_date'] = f'eq.{game_date}'
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-499'},
                     params=params, timeout=20)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def _apply_gates(pp: dict, spread_anchor_weight,
                 sport: Optional[str] = None,
                 locked: bool = False) -> tuple[dict, list[str]]:
    """Return (new_pp, applied_gates_list). new_pp is a copy with
    tier / conviction possibly capped + gate reasons appended to `sub`.

    `locked` = this pick has already been published and stamped by the
    database pick lock (20260926b). Tier is then left exactly as it
    shipped; only the explanatory text is refreshed. See the LR-warn
    gate below for why that distinction matters.
    """
    if not isinstance(pp, dict):
        return pp, []

    tier = str(pp.get('tier') or '').upper()
    conv = pp.get('conviction') or 0
    side = str(pp.get('side') or '').upper()
    market = str(pp.get('type') or '').lower()
    label = pp.get('label') or ''
    applied: list[str] = []

    new_pp = dict(pp)

    # Gate 1: LR-warn hard-cap (only applies to ML / spread / rl picks)
    if market in ('ml', 'spread', 'rl') and side in ('HOME', 'AWAY'):
        lr = pp.get('_lr_ml_shadow') or {}
        if isinstance(lr, dict):
            try:
                p_home = float(lr.get('p_home_win'))
                # Opposite of pick: HOME pick → LR says AWAY strongly (p_home <= 1 - threshold)
                #                   AWAY pick → LR says HOME strongly (p_home >= threshold)
                lr_disagrees_strongly = (
                    (side == 'HOME' and p_home <= (1.0 - LR_WARN_HARD_THRESHOLD)) or
                    (side == 'AWAY' and p_home >= LR_WARN_HARD_THRESHOLD)
                )
                # ══ 2026-10-09 · CONSULT THE GATE'S OWN TRACK RECORD ══
                # The verdict is per SPORT. Until today this gate fired
                # identically for NCAAF (warn cohort 33.3%, n=78 — cap
                # plainly right) and NFL (73.3%, n=15 — points the other
                # way). See lr_warn_verdict for why NFL is shadowed rather
                # than switched off on fifteen games.
                _verdict = lr_warn_verdict(sport)
                if lr_disagrees_strongly and not _verdict['cap']:
                    # Evidence has refuted the cap for this sport. Leave the
                    # pick's tier alone, but say so on the pick so the change
                    # is visible rather than a silent behaviour swap.
                    new_pp['_lr_warn_released'] = {
                        'p_home': round(p_home, 4), 'why': _verdict['why'],
                    }
                    applied.append(f'lr_warn_released:p={p_home:.2f}')
                    lr_disagrees_strongly = False
                if lr_disagrees_strongly:
                    # 2026-09-26 · LEAN -> PASS for picks that have not
                    # shipped yet.
                    #
                    # Andy: "If spots where LR warns against the pick hit
                    # 4.3% of the time, that isn't a capped LEAN — it's a
                    # fade." The real figure is worse than a rounding
                    # error either way: 7-29, 19%, n=36. A LEAN is a
                    # playable call, and publishing a playable call on a
                    # cohort that loses four times out of five is not a
                    # dampened opinion, it is a bad pick with a caveat.
                    #
                    # BUT a locked pick is one users have already seen and
                    # may have bet. All 18 on the 09-26 board were locked
                    # and playing that day. Re-tiering those would break
                    # the lock built three days earlier specifically to
                    # stop picks moving between runs, and would rewrite
                    # receipts after the fact. So the honest gate applies
                    # going forward; anything already stamped keeps the
                    # tier it shipped with and only gets refreshed text.
                    if locked:
                        if tier in ('PRIME', 'STRONG'):
                            new_pp['tier'] = 'LEAN'
                        tier = 'LEAN'  # for cascade with anchor check below
                        new_pp['conviction'] = min(conv, 55)
                        conv = new_pp['conviction']
                        _record_cap(new_pp, 'LEAN', 55,
                                    f'lr_warn:p_home={p_home:.2f}')
                        _cap_word = 'capped to LEAN'
                    else:
                        new_pp['tier'] = 'PASS'
                        tier = 'PASS'
                        new_pp['conviction'] = min(conv, 40)
                        conv = new_pp['conviction']
                        _record_cap(new_pp, 'PASS', 40,
                                    f'lr_warn_pass:p_home={p_home:.2f}')
                        _cap_word = 'no play'
                    # ══ SHADOW THE CAP WHERE THE EVIDENCE IS AMBIGUOUS ══
                    # NFL's warn cohort currently reads 11-4 (73.3%, n=15) —
                    # above breakeven but inside its own 2SE band. The cap
                    # still fires, because fifteen games cannot retire a
                    # suppression gate. But the pick now carries what it
                    # WOULD have shipped as, so the counterfactual is on the
                    # record and the question gets settled by data instead of
                    # re-argued next week. feedback_suppression_gate_needs_shadow.
                    if _verdict.get('shadow'):
                        new_pp['_lr_warn_shadow'] = {
                            'p_home': round(p_home, 4),
                            'tier_without_cap': pp.get('tier'),
                            'conviction_without_cap': pp.get('conviction'),
                            'tier_with_cap': new_pp.get('tier'),
                            'why': _verdict['why'],
                        }
                        applied.append('lr_warn_shadowed')
                    _strip_lr_warn(new_pp)
                    _flag = (f'⚠ LR shadow warns other way (p_home={p_home:.2f}) — '
                             f'{_cap_word}.'
                             # Pass the tier the pick will SHIP as, so the
                             # quoted record is that tier's cohort rather
                             # than a pool spanning tiers that went 8-0 and
                             # 0-2.
                             + _lr_warn_sentence(sport,
                                                 str(new_pp.get('tier') or '')))
                    _append_flag(new_pp, _flag)
                    applied.append(f'lr_warn_{"cap" if locked else "pass"}:p={p_home:.2f}')
            except (TypeError, ValueError):
                pass

    # Gate 2: Anchor cap (any market type, if anchor fired)
    try:
        aw = float(spread_anchor_weight) if spread_anchor_weight is not None else 0.0
        if aw > 0 and tier in ANCHOR_CAP_TIERS:
            _record_cap(new_pp, ANCHOR_CAP_NEW_TIER, 60, f'anchor:w={aw:.2f}')
            # ── 2026-09-27 · THE 30% WAS WRONG, AND IT WAS ON THE CARD ──
            #
            # Andy: "'Anchored picks hit 30% historically' undercuts the
            # pick shown right above it. If that stat is real, anchored
            # picks shouldn't publish."
            #
            # Re-measured the same day over every graded NFL + NCAAF pick
            # with an anchor weight — 92 decided picks, not the 40 the old
            # note cited:
            #     anchored       49-43   53.3%   (n=92)
            #     not anchored  130-86   60.2%   (n=216)
            #     NCAAF anchored 34-37   47.9%   (n=71)
            #     NFL   anchored 15-6    71.4%   (n=21)
            # So the real figure is 53%, not 30%, and NFL anchored picks
            # are the BEST bucket on the board. The card was asserting a
            # number 23 points off and using it to talk users out of picks
            # that were performing.
            #
            # The cap itself still stands — anchored picks do trail
            # un-anchored ones by ~7pp, which is what a tier demotion is
            # for. What is removed is the stale hard-coded hit rate. A
            # number nobody re-measures will drift again, so the flag now
            # states the MECHANISM, which stays true, and the measured
            # rates live in this comment where they carry their date and n.
            _flag = (f'⚠ Market anchor active (w={aw:.2f}) — model is '
                     f'uncertain and has been pulled toward the market, '
                     f'so the tier is capped at LEAN.')
            _append_flag(new_pp, _flag)
            applied.append(f'anchor_cap:w={aw:.2f}')
    except (TypeError, ValueError):
        pass

    return new_pp, applied


def _patch(tbl: str, game_id: str, new_pp: dict) -> bool:
    """PATCH primary_play, and PROVE the row was actually hit.

    ══ 2026-10-03 · "Texas A&M" SILENTLY ATE EVERY WRITE ══
    The filter was interpolated straight into the URL:

        f'{SB}/rest/v1/{tbl}?game_id=eq.{game_id}'

    NCAAF game ids are built from team names, so Arkansas @ Texas A&M is
    `ncaaf_20261003_Arkansas_Texas A&M`. The bare `&` ENDS the query string:
    PostgREST saw `game_id=eq.ncaaf_20261003_Arkansas_Texas A` plus a stray
    `M` param, matched zero rows, and returned **200**. A PATCH that updates
    nothing is a successful PATCH, so this returned True and the caller
    counted it as applied.

    Observed exactly that today: the run printed
    `Arkansas +13.5  LEAN → PASS · gates: lr_warn_pass:p=0.76`
    and the row came back LEAN on re-read, three times in a row. A
    `Prefer: return=representation` probe returned 0 rows, which is what
    finally gave it away — the status code never could.

    Two fixes, both needed:
      * pass the filter through `params=` so requests percent-encodes `&`
        and the space;
      * check that a row actually came back, because a no-op PATCH is
        indistinguishable from a successful one by status alone
        (feedback_204_is_not_a_write, same lesson, different table).

    Any team with `&` or other URL-significant characters in its name hit
    this. Texas A&M is the obvious one; the encoding fix covers the rest
    rather than special-casing a name.
    """
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}',
        headers={**H_WRITE, 'Prefer': 'return=representation'},
        params={'game_id': f'eq.{game_id}'},
        json={'primary_play': new_pp},
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(f'      ⚠ PATCH {r.status_code}: {(r.text or "")[:160]}')
        return False
    try:
        rows = r.json() if (r.text or '').strip() else []
    except ValueError:
        rows = []
    if not rows:
        print(f'      ⚠ PATCH matched NO ROWS for game_id={game_id!r} '
              f'— write discarded, status was {r.status_code}')
        return False
    return True


def run(sport: Optional[str] = None,
        game_date: Optional[str] = None,
        dry_run: bool = False,
        days: int = 0) -> None:
    gd = game_date or _today_et()
    sports = [sport] if sport else ['NFL', 'NCAAF']
    span = f'{gd}..+{days}d' if days else gd
    print(f'=== nfl_ncaaf_signal_discipline · {span} · sports={sports} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    for sp in sports:
        tbl = 'nfl_game_context' if sp == 'NFL' else 'ncaaf_game_context'
        games = _fetch_games(sp, gd, days=days)
        n_unlocked = sum(1 for g in games if not g.get('pick_locked_at'))
        print(f'  {sp}: {len(games)} games in ctx '
              f'({n_unlocked} still unlocked — only these can reach PASS)')
        capped = 0
        no_change = 0
        for g in games:
            pp = g.get('primary_play') or {}
            aw = g.get('spread_anchor_weight')
            # A pick the database has already stamped is published;
            # the LR-warn gate must not re-tier it. See _apply_gates.
            new_pp, gates = _apply_gates(pp, aw, sp,
                                         locked=bool(g.get('pick_locked_at')))
            if not gates:
                no_change += 1
                continue
            gid = g.get('game_id')
            label = pp.get('label', '?')
            old_tier = pp.get('tier', '?')
            new_tier = new_pp.get('tier', '?')
            _lk = 'locked' if g.get('pick_locked_at') else 'unlocked'
            print(f'    {str(g.get("game_date")):10s} {label:24s} '
                  f'{old_tier} → {new_tier}  [{_lk}] · gates: {",".join(gates)}')
            if not dry_run and gid:
                if _patch(tbl, gid, new_pp):
                    capped += 1
                else:
                    print(f'      ⚠ PATCH failed for {gid}')
        print(f'  {sp}: {capped} capped · {no_change} unchanged')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=['NFL', 'NCAAF'])
    p.add_argument('--date', dest='game_date')
    p.add_argument('--dry-run', action='store_true')
    # 2026-10-03: a forward window, so the LR-warn gate can decide while the
    # pick is still UNLOCKED. Default 0 keeps the historical single-day
    # behaviour for any caller that wants exactly one date.
    p.add_argument('--days', type=int, default=0,
                   help='also process games up to N days ahead (0 = just the '
                        'one date). Needed for PASS to be reachable: NCAAF '
                        'locks Thu->Sun, so a weekend pick is already stamped '
                        'by the time its own date arrives.')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.game_date, dry_run=args.dry_run,
        days=args.days)


if __name__ == '__main__':
    main()
