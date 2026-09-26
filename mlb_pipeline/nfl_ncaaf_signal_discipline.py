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
     PRIME/STRONG, downgrade to LEAN. Anchored picks hit 30% (n=40).
     The anchor fires when the model is uncertain — anchored picks
     should never ride at top tier.

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
from datetime import datetime, timezone, timedelta
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


def _lr_warn_sentence(sport: str) -> str:
    rec = _lr_warn_record(sport)
    if not rec or rec.get('hit_pct_lifetime') is None:
        return ''
    w = rec.get('wins_lifetime') or 0
    l = rec.get('losses_lifetime') or 0
    n = w + l
    if n < 10:          # too thin to publish as a rate
        return ''
    return (f' Picks carrying this warning have gone {w}-{l} '
            f'({float(rec["hit_pct_lifetime"]):.0f}%, n={n}).')


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


def _fetch_games(sport: str, game_date: str) -> list[dict]:
    tbl = 'nfl_game_context' if sport == 'NFL' else 'ncaaf_game_context'
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-499'},
                     params={
                         'game_date': f'eq.{game_date}',
                         'select': 'game_id,home_team,away_team,primary_play,'
                                   'spread_anchor_weight,pick_locked_at',
                     },
                     timeout=20)
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
                    _strip_lr_warn(new_pp)
                    _flag = (f'⚠ LR shadow warns other way (p_home={p_home:.2f}) — '
                             f'{_cap_word}.' + _lr_warn_sentence(sport))
                    _append_flag(new_pp, _flag)
                    applied.append(f'lr_warn_{"cap" if locked else "pass"}:p={p_home:.2f}')
            except (TypeError, ValueError):
                pass

    # Gate 2: Anchor cap (any market type, if anchor fired)
    try:
        aw = float(spread_anchor_weight) if spread_anchor_weight is not None else 0.0
        if aw > 0 and tier in ANCHOR_CAP_TIERS:
            _record_cap(new_pp, ANCHOR_CAP_NEW_TIER, 60, f'anchor:w={aw:.2f}')
            _flag = (f'⚠ Market anchor active (w={aw:.2f}) — '
                     f'model uncertain, capped to LEAN. Anchored picks hit 30% historically.')
            _append_flag(new_pp, _flag)
            applied.append(f'anchor_cap:w={aw:.2f}')
    except (TypeError, ValueError):
        pass

    return new_pp, applied


def _patch(tbl: str, game_id: str, new_pp: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{game_id}',
        headers=H_WRITE,
        json={'primary_play': new_pp},
        timeout=10,
    )
    return r.status_code in (200, 204)


def run(sport: Optional[str] = None,
        game_date: Optional[str] = None,
        dry_run: bool = False) -> None:
    gd = game_date or _today_et()
    sports = [sport] if sport else ['NFL', 'NCAAF']
    print(f'=== nfl_ncaaf_signal_discipline · {gd} · sports={sports} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    for sp in sports:
        tbl = 'nfl_game_context' if sp == 'NFL' else 'ncaaf_game_context'
        games = _fetch_games(sp, gd)
        print(f'  {sp}: {len(games)} games in ctx')
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
            print(f'    {label:24s} {old_tier} → {new_tier}  · gates: {",".join(gates)}')
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
    args = p.parse_args()
    run(sport=args.sport, game_date=args.game_date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
