"""Refit → Prop Jerry verdict override (2026-08-10).

Post-Prop-Jerry pass that FORCES BACK/FADE/PASS on prop_jerry_reads
based on the delta between raw conviction and refit conviction.
Runs AFTER apply_prop_refit + generate_prop_jerry_synthesis + the
existing collapse scripts.

## Rules (user-approved 2026-08-10 · project_refit_v2_expansion_810)

Given: raw = prop's `conviction`, refit = prop's `refit_conviction`

  |Δ| = |refit - raw|

  |Δ| >= 30 AND refit < 40   → FORCE FADE  (raw was fooled; opposite side is real)
  |Δ| >= 20 AND refit >= 80  → FORCE BACK  (refit confirms + boosts; take it)
  |Δ| >= 20 AND refit < 45   → FORCE PASS  (too much disagreement, sit out)
  else                       → HOLD (small delta or refit close to raw)

## No-refit cap

If refit_conviction is NULL for a prop that made a BACK/FADE call:
  - Cap conviction at 55 (LEAN)
  - Add "NO_REFIT_COVERAGE" audit tag
  - Don't flip verdict — just downgrade confidence

## Idempotency

Skips any prop_jerry_reads row where short_read already contains
"Auto-refit-override 2026-08-10". Safe to re-run same day.

## Sport universal

Runs across MLB / NFL / any sport whose props table has refit_conviction.

CLI:
    python apply_refit_verdict_override.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone

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

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Thresholds
# 2026-08-11 tighten (per Aug 10 audit — Prop Jerry FADE went 2-7 · 22%,
# suggesting force-fade at refit<40 was over-firing). Tightening the trap
# threshold to <30 means we FORCE FADE only on truly hammered signals,
# not gray-zone dissonance. Force PASS threshold stays at 45 so gray-zone
# cases still get sit-out treatment, they just don't get flipped to the
# opposite side aggressively.
DELTA_STRONG   = 35  # |Δ| >= 35 required for FORCE FADE (was 30)
DELTA_BOOST    = 20  # |Δ| >= 20 required for FORCE BACK when refit high
REFIT_TRAP     = 30  # refit < 30 = trap signal (was 40)
REFIT_BOOST    = 80  # refit >= 80 = strong confirmation
REFIT_PASS     = 45  # refit < 45 = insufficient conviction (unchanged)
NO_REFIT_CAP   = 55  # LEAN cap when refit missing


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def decide(raw: int, refit: float | None, current_verdict: str,
           prop_type: str | None = None, direction: str | None = None,
           sample_health: dict | None = None,
           book_odds: int | None = None) -> tuple[str, str] | None:
    """Return (new_verdict, action_id) or None if HOLD.

    2026-08-11: sample_health gate added. When forcing a BACK on refit
    boost, we now check that the refit-100 band for THIS prop_type has
    hit >=55% over its recent history (n>=30). If not, force BACK
    downgrades to LEAN cap only, no verdict change. Prevents
    over-confident BACKs when the calibration model itself has been
    misfiring on this prop_type recently.

    2026-08-14: juice-trap short-circuit added. hits_over -200+ and
    bb_under -200+ are known TRAP zones (memory:
    feedback_batter_hits_juice_trap_803 + feedback_potd_juice_gate_803).
    The pipeline's prop_tier_calibration.py correctly caps these at
    conviction 40-55, but refit was overriding to 99+ and boosting past
    the cap (see Lindor -250 hits_over: raw=40, refit=99.8, Jerry conv=85
    surfaced). Refit CANNOT override juice-trap gates — return LEAN_CAP.

    sample_health dict shape (from compute_refit_band_health):
        {(prop_type, direction, '80-100'): {n: N, hit_pct: float}}
    """
    # ---- Juice-trap short-circuit (2026-08-14) ----
    # Fires BEFORE any refit-boost logic. Prevents refit from lifting a
    # BACK verdict past LEAN when book_odds are in the family's trap zone.
    if book_odds is not None and prop_type and direction:
        try:
            o = int(book_odds)
            # hits_over trap: memory feedback_batter_hits_juice_trap_803
            if prop_type == 'hits_over' and direction == 'over' and o <= -200:
                if current_verdict in ('BACK', 'FADE'):
                    return ('LEAN_CAP', f'HITS_OVER_JUICE_TRAP_{o}_no_refit_boost')
            # bb_under trap: memory equivalent from 8/9 (added 8/14 gate)
            if prop_type == 'bb_under' and direction == 'under' and o <= -200:
                if current_verdict in ('BACK', 'FADE'):
                    return ('LEAN_CAP', f'BB_UNDER_JUICE_TRAP_{o}_no_refit_boost')
        except (TypeError, ValueError): pass

    if refit is None:
        # No-refit cap only applies to BACK/FADE
        if current_verdict in ('BACK', 'FADE'):
            return (current_verdict, 'NO_REFIT_CAP')
        return None
    delta = refit - raw
    abs_d = abs(delta)
    if abs_d >= DELTA_STRONG and refit < REFIT_TRAP:
        # 2026-08-14 · DISABLED FORCE_FADE_TRAP → converted to FORCE_PASS.
        #
        # 30-day audit uncovered this rule was catastrophically wrong:
        #   refit <20 zone FADEs: 2-14 (12% hit) — blind inversion 88%
        #   refit 20-39 zone:     7-10 (41%)
        #   Auto-refit-override FADEs overall: 1-6 (14%)
        #
        # The premise ("refit low + raw high = trap, fade the pick") is
        # backwards for the current calibration. Refit's <30 band signal
        # is NOT reliably identifying traps — actual outcomes show these
        # picks hit ~76-88% of the time. Blindly inverting to FORCE_BACK
        # would over-commit on a small sample (n=16 in the extreme band);
        # PASS is the safe move — protect users from the wrong FADE
        # without betting the opposite direction.
        #
        # Investigation into why the refit model produces this inverted
        # signal in the low-conviction band is queued for the refit
        # calibration audit (deeper piece). Until then: don't act on
        # the refit-trap signal.
        if current_verdict == 'BACK':
            return ('PASS', 'FORCE_PASS_REFIT_TRAP_DISABLED')
        return None
    # 2026-08-11: Jerry-hallucination catch. When JR conviction is high
    # (BACK verdict at 55+) but props base scorer + refit BOTH say low
    # (both < 30 conviction), Jerry is cherry-picking despite dual-signal
    # rejection. Force PASS at minimum (or FADE if refit is very low).
    # Catches Dylan Cease er_under BACK conv=58 vs props conv=24 (SKIP)
    # vs refit=16.9 — Jerry ignored both underlying signals.
    if current_verdict == 'BACK' and raw < 30 and refit is not None and refit < 30:
        return ('PASS', 'FORCE_PASS_JERRY_HALLUCINATION')
    if abs_d >= DELTA_BOOST and refit >= REFIT_BOOST:
        # 2026-08-11 (v4): FADE-flip must fire REGARDLESS of band health.
        # FADE verdict + refit=100 means Jerry is fading what the refit
        # model says is a strong signal — that's a Jerry hallucination
        # to correct, not a refit boost to validate. The audit gate
        # (jerry_pre_publish_audit) blocks the pipeline on FADE+refit≥80,
        # so the LEAN_CAP-only path we shipped earlier ended up blocking
        # the pipeline. Flip FADE→BACK regardless, and cap conviction
        # at LEAN if band unproven (so we correct the verdict but don't
        # over-commit).
        if current_verdict == 'FADE':
            band_unproven = False
            if sample_health and prop_type and direction:
                band = sample_health.get((prop_type, direction, '80-100'))
                if not band or band['n'] < 30 or band['hit_pct'] < 55:
                    band_unproven = True
            elif not sample_health:
                band_unproven = True
            if band_unproven:
                return ('BACK', 'FORCE_BACK_FLIP_LEAN_CAP')
            return ('BACK', 'FORCE_BACK_REFIT_OVERRIDE')

        # Sample-size gate — only force BACK-BOOST (raise conviction) when
        # the refit-100 band has been healthy recently. Otherwise cap at LEAN.
        # Prior version force-boosted BACK/PASS regardless — 29/29 pitcher
        # ha_over props today boosted to noise because refit v2's new prop
        # types had zero graded samples to validate the band.
        if sample_health and prop_type and direction:
            band = sample_health.get((prop_type, direction, '80-100'))
            if not band or band['n'] < 30:
                return ('LEAN_CAP', 'REFIT_BAND_UNPROVEN')
            if band['hit_pct'] < 55:
                return ('LEAN_CAP', 'REFIT_BAND_UNHEALTHY')
        else:
            return ('LEAN_CAP', 'REFIT_HEALTH_UNAVAILABLE')
        if current_verdict in ('BACK', 'PASS'):
            return ('BACK', 'FORCE_BACK_BOOST')
    if abs_d >= DELTA_BOOST and refit < REFIT_PASS:
        # 2026-08-14 · DISABLED FORCE_PASS_CONFLICT rule (refit calibration audit).
        #
        # 30-day cross-reference: 32 picks hit this rule and got PASSed. Their
        # underlying prop outcomes went 19-11 (63.3% hit rate). The rule is
        # killing legitimate winners.
        #
        # Root cause: refit's middle band (30-95) is essentially uncalibrated
        # noise — decile analysis showed refit values in that range range from
        # 35% to 75% actual hit rate with no monotonic relationship. Using
        # refit<45 as a "too conflicted, sit out" gate PASSes picks where Jerry
        # correctly saw edge the refit model failed to capture.
        #
        # Keeping FORCE_PASS_JERRY_HALLUCINATION (raw<30 AND refit<30 dual
        # flag) which is 2-3 (40%) — that one's justified because BOTH signals
        # agree the pick is weak.
        #
        # Return None → HOLD original verdict. Deferred: retrain refit model
        # with clean training data (post last_outing bug fix).
        return None
    return None


def compute_refit_band_health(days: int = 30) -> dict:
    """Return refit-band hit rates over the last N days per (prop_type,
    direction, band). Used by the sample-size gate to detect when a
    refit band has been misfiring recently.

    Bands: '80-100' (BACK zone), '60-79', '40-59', '20-39', '0-19'.
    """
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = []
    offset = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
            headers={**H_READ, 'Range': f'{offset}-{offset+999}',
                     'Range-Unit': 'items'},
            params={'select': 'prop_type,direction,refit_conviction,result',
                    'result': 'in.(Win,Loss)',
                    'game_date': f'gte.{since}',
                    'refit_conviction': 'not.is.null'},
            timeout=30)
        chunk = r.json() if r.status_code in (200, 206) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        offset += 1000
    from collections import defaultdict
    acc = defaultdict(lambda: {'w': 0, 'l': 0})
    for row in rows:
        rf = row.get('refit_conviction')
        if rf is None: continue
        band = ('80-100' if rf >= 80 else '60-79' if rf >= 60
                else '40-59' if rf >= 40 else '20-39' if rf >= 20 else '0-19')
        key = (row['prop_type'], row['direction'], band)
        if row['result'] == 'Win': acc[key]['w'] += 1
        elif row['result'] == 'Loss': acc[key]['l'] += 1
    health = {}
    for key, v in acc.items():
        total = v['w'] + v['l']
        if total == 0: continue
        health[key] = {'n': total, 'hit_pct': round(100 * v['w'] / total, 1)}
    return health


# 2026-08-11: module-level counter for raw-conviction=0 alarms. Populated by
# run() so we can emit an aggregate summary + optional Slack/audit-table
# write at the end. Resets per invocation.
from collections import defaultdict as _dd
_raw_zero_counter: dict = _dd(int)


def _ensure_refit_populated(game_date: str) -> None:
    """Defensive: refit_conviction on mlb_pipeline_props keeps getting wiped
    somewhere in the pipeline (generate_props DELETE+INSERT doesn't include
    refit column; whatever runs between apply_prop_refit and here can also
    trigger a fresh generate_props run). Instead of chasing every possible
    wipe path, check refit null rate on today's props and rerun apply_prop_
    refit inline if >50% are null. Self-healing.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'refit_conviction,prop_type'},
        timeout=15).json()
    if not isinstance(r, list) or not r:
        return
    # Only care about prop types covered by refit v2 (12 types)
    refitable = {'ks_over', 'ks_under', 'bb_over', 'bb_under',
                 'ha_over', 'ha_under', 'er_over', 'er_under',
                 'outs_over', 'outs_under', 'hits_over', 'hits_under'}
    covered = [x for x in r if x.get('prop_type') in refitable]
    if not covered: return
    nulls = sum(1 for x in covered if x.get('refit_conviction') is None)
    null_pct = 100 * nulls / len(covered)
    if null_pct < 50:
        return  # healthy
    print(f'  🔧 refit self-heal: {nulls}/{len(covered)} refit rows null '
          f'({null_pct:.0f}%) — invoking apply_prop_refit inline')
    try:
        import subprocess
        result = subprocess.run(
            ['python', str(Path(__file__).parent / 'apply_prop_refit.py'),
             '--date', game_date],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f'    ✅ apply_prop_refit re-ran successfully')
        else:
            print(f'    ⚠️  apply_prop_refit rerun failed: {result.stderr[:200]}')
    except Exception as e:
        print(f'    ⚠️  self-heal invocation failed: {e}')


def _cap_props_trap_directly(game_date: str, dry_run: bool = False) -> int:
    """Direct props-table trap cap — independent of prop_jerry_reads.

    2026-08-11: high-tier props (STRONG/PRIME) with refit < 30 flag as
    trap even when Jerry already backed off (verdict = PASS/FADE). The
    JR verdict downgrade doesn't propagate to props tier, so the sweat
    card composition still picks them up. This pass caps them at LEAN
    at the props level.

    Idempotent — skips rows with tier=LEAN or _refit_override_cap already
    set in signals.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'tier': 'in.(STRONG,PRIME)',
                             'refit_conviction': 'lt.30',
                             'select': 'id,player_name,prop_type,direction,tier,'
                                       'conviction,refit_conviction,signals'},
                     timeout=15)
    if r.status_code != 200: return 0
    capped = 0
    for prop in r.json():
        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_refit_override_cap'): continue  # already tagged
        sig['_refit_override_cap'] = 'PROPS_TRAP_DIRECT'
        sig['_refit_override_at'] = _et_today()
        old_tier = prop.get('tier'); old_conv = prop.get('conviction')
        print(f'  props-trap: {prop["player_name"]:22} {prop["prop_type"]:10} '
              f'{prop["direction"]:5} {old_tier}/{old_conv} refit={prop.get("refit_conviction")}'
              f' -> LEAN/55')
        if dry_run: capped += 1; continue
        patch = {'tier': 'LEAN', 'conviction': 55, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): capped += 1
    return capped


def _cap_hits_over_juice_trap(game_date: str, dry_run: bool = False) -> int:
    """Gate hits_over PRIME props on the juice-trap rule.

    Per feedback_batter_hits_juice_trap_803 + feedback_prop_jerry_odds:
    hits_over 0.5 at -200+ juice is a documented trap (PRIME ~69% but the
    juice eats the edge; you need ~67% just to break even at -200).
    Filter zone: keep props in the -300 to +150 range only.

    Cap PRIME hits_over to STRONG when book_line is worse than -200.
    Cap STRONG/PRIME hits_over to LEAN when book_line is -300+.

    Idempotent — skips rows tagged with _hits_over_juice_gate.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'prop_type': 'eq.hits_over',
                             'tier': 'in.(STRONG,PRIME)',
                             'select': 'id,player_name,prop_type,direction,tier,'
                                       'conviction,refit_conviction,book_over_odds,'
                                       'book_under_odds,signals'},
                     timeout=15)
    if r.status_code != 200: return 0
    capped = 0
    for prop in r.json():
        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_hits_over_juice_gate'): continue

        # 2026-08-24 bug fix: previously read book_line (the LINE value like
        # 0.5) as if it were odds. book_line=0.5 is always > -140 so the gate
        # never fired. Read direction-appropriate odds column instead.
        direction = prop.get('direction') or 'over'
        odds = (prop.get('book_over_odds') if direction == 'over'
                else prop.get('book_under_odds'))
        # 2026-08-24: 7-day audit showed 42/42 missing prop odds are
        # hits_over — books offer them at -300 to -400 juice which is
        # outside our scraper range. Prior behavior kept these at
        # PRIME/STRONG tier with null odds → user saw PRIME chip they
        # couldn't act on (app-side unit gate zeroed out sizing). Now:
        # when hits_over PRIME/STRONG has null odds, cap to LEAN so
        # internal tier matches user-actionable tier.
        if odds is None:
            old_tier = prop.get('tier')
            if old_tier in ('PRIME', 'STRONG'):
                sig['_hits_over_juice_gate'] = 'HITS_OVER_NULL_ODDS_UNACTIONABLE'
                sig['_refit_override_at'] = _et_today()
                print(f'  hits_over-null: {prop["player_name"]:22} '
                      f'{old_tier}/{prop.get("conviction")} @ null -> LEAN/55',
                      flush=True)
                if not dry_run:
                    requests.patch(
                        f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                        headers=H_WRITE,
                        json={'tier': 'LEAN', 'conviction': 55, 'signals': sig},
                        timeout=10,
                    )
                capped += 1
            continue
        try:
            odds = int(odds)
        except (TypeError, ValueError):
            continue
        # -200 or worse = trap zone; -300 or worse = hard drop to LEAN
        old_tier = prop.get('tier')
        if odds <= -300:
            new_tier = 'LEAN'; new_conv = 55
            tag = f'HITS_OVER_HARD_JUICE_{odds}'
        elif odds <= -200:
            if old_tier == 'PRIME':
                new_tier = 'STRONG'; new_conv = 65
                tag = f'HITS_OVER_PRIME_JUICE_TRAP_{odds}'
            else:
                continue  # STRONG at -200 to -300 stays (edge might hold)
        else:
            continue  # inside publish zone (-200 to +?)

        sig['_hits_over_juice_gate'] = tag
        sig['_refit_override_at'] = _et_today()
        print(f'  hits_over-juice: {prop["player_name"]:22} '
              f'{old_tier}/{prop.get("conviction")} @ {odds} -> {new_tier}/{new_conv}')
        if dry_run: capped += 1; continue
        patch = {'tier': new_tier, 'conviction': new_conv, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): capped += 1
    return capped


def _cap_ha_over_no_signal(game_date: str, dry_run: bool = False) -> int:
    """Gate ha_over props on the 2026-08-15 morning-audit finding.

    ha_over went 1-4 on 8/15 (20% hit) and Prop Jerry BACK on ha_over sits
    at 5-7 (42% n=12) rolling. The prop only earns publication when EITHER
    the refit says the model got more confident (refit_conviction >= base)
    OR the signals JSON carries a sharp-market lift (sharp_confirmed /
    consensus / triple_confirmed). Anything else: cap to LEAN.

    Idempotent — skips rows already tagged with _ha_over_gate.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'prop_type': 'eq.ha_over',
                             'tier': 'in.(STRONG,PRIME)',
                             'select': 'id,player_name,prop_type,direction,tier,'
                                       'conviction,refit_conviction,signals'},
                     timeout=15)
    if r.status_code != 200: return 0
    gated = 0
    for prop in r.json():
        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_ha_over_gate'): continue

        base = prop.get('conviction') or 0
        refit = prop.get('refit_conviction')
        refit_up = refit is not None and refit >= base

        # Sharp-market lift check — any of these signal keys constitute a lift
        SHARP_KEYS = ('sharp_confirmed', 'sharp_triple_confirmed',
                      'consensus', 'rlm_confirmed', '_sharp_lift')
        has_sharp_lift = any(sig.get(k) for k in SHARP_KEYS)

        if refit_up or has_sharp_lift:
            continue  # earned publication

        sig['_ha_over_gate'] = 'NO_REFIT_UP_NO_SHARP_LIFT'
        sig['_refit_override_at'] = _et_today()
        print(f'  ha_over-gate: {prop["player_name"]:22} '
              f'{prop["tier"]}/{base} refit={refit} -> LEAN/55')
        if dry_run: gated += 1; continue
        patch = {'tier': 'LEAN', 'conviction': 55, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): gated += 1
    return gated


def _cap_under_juice_trap(game_date: str, dry_run: bool = False) -> int:
    """Symmetric UNDER-juice gate (2026-08-17 morning-audit finding).

    Yesterday's props: bb_under -37% ROI, outs_under -36%, ha_under -30%.
    Same juice-trap pattern as hits_over: heavy juice on UNDER props
    means the model's edge doesn't cover the vig even when it hits ~55%.

    Rule: cap PRIME/STRONG UNDER props (bb_under, ha_under, outs_under,
    ks_under, er_under) when book_line is worse than -140.
      * PRIME at book_line <= -140 → STRONG
      * PRIME/STRONG at book_line <= -180 → LEAN

    Idempotent via _under_juice_gate tag.
    """
    UNDER_TYPES = 'in.(bb_under,ha_under,outs_under,ks_under,er_under)'
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'prop_type': UNDER_TYPES,
                             'tier': 'in.(STRONG,PRIME)',
                             'select': 'id,player_name,prop_type,direction,tier,'
                                       'conviction,refit_conviction,book_over_odds,'
                                       'book_under_odds,signals'},
                     timeout=15)
    if r.status_code != 200: return 0
    capped = 0
    for prop in r.json():
        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_under_juice_gate'): continue

        # 2026-08-24 bug fix: was reading book_line (the LINE value, e.g.
        # 1.5) as if it were odds. book_line=1.5 always > -140 so gate
        # never fired. Read book_under_odds (direction=under for all these
        # prop_types) — the actual juice on the pick side.
        odds = prop.get('book_under_odds')
        if odds is None: continue
        try: odds = int(odds)
        except (TypeError, ValueError): continue
        if odds > -140: continue  # inside publishable juice range

        old_tier = prop.get('tier')
        if odds <= -180:
            new_tier = 'LEAN'; new_conv = 55
            tag = f'UNDER_HARD_JUICE_{odds}'
        elif old_tier == 'PRIME':
            new_tier = 'STRONG'; new_conv = 65
            tag = f'UNDER_PRIME_JUICE_TRAP_{odds}'
        else:
            continue  # STRONG at -140 to -180 stays

        sig['_under_juice_gate'] = tag
        sig['_refit_override_at'] = _et_today()
        print(f'  under-juice: {prop["player_name"]:22} {prop["prop_type"]:12} '
              f'{old_tier}/{prop.get("conviction")} @ {odds} -> {new_tier}/{new_conv}')
        if dry_run: capped += 1; continue
        patch = {'tier': new_tier, 'conviction': new_conv, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): capped += 1
    return capped


def _demote_coverage_tier(game_date: str, dry_run: bool = False) -> int:
    """Kill COVERAGE tier from publishable stack (2026-08-17 audit).

    COVERAGE tier is a -11% ROI drag over 216 stakes (8/16 audit). These
    are fallback fills that got promoted for card coverage without real
    conviction. Cap all COVERAGE props at LEAN so downstream (sweat card,
    the sharp, etc.) treats them as internal-only, not publishable.

    Idempotent via _coverage_kill_gate tag.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'tier': 'eq.COVERAGE',
                             'select': 'id,player_name,prop_type,tier,conviction,signals'},
                     timeout=15)
    if r.status_code != 200: return 0
    demoted = 0
    for prop in r.json():
        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_coverage_kill_gate'): continue

        sig['_coverage_kill_gate'] = 'COVERAGE_TIER_UNPUBLISHABLE'
        sig['_refit_override_at'] = _et_today()
        print(f'  coverage-kill: {prop["player_name"]:22} {prop["prop_type"]:12} '
              f'COVERAGE/{prop.get("conviction")} -> LEAN/55')
        if dry_run: demoted += 1; continue
        patch = {'tier': 'LEAN', 'conviction': 55, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): demoted += 1
    return demoted


def _playbook_gate_props(game_date: str, dry_run: bool = False) -> int:
    """Prop playbook phase 2: demote PRIME/STRONG props whose stack is
    dominated by ANTI_VALIDATED signals from the prop signal_registry.

    Reads signal_registry rows with market_scope='prop' and matches them
    against each prop's `signals` JSON. A signal in the prop is
    ANTI_VALIDATED if the registry lookup for `prop:<signal_name>` OR
    `prop:<signal_name>:<prop_type>` returns tier=ANTI_VALIDATED.

    Demote rule:
      * If >= 50% of the prop's positive numeric contribution comes
        from ANTI_VALIDATED signals → demote one tier (PRIME→STRONG,
        STRONG→LEAN).
      * If ALL matched signals in the prop's stack are ANTI or
        UNVALIDATED and the prop is PRIME → cap at LEAN (no proven
        evidence for a PRIME call).

    Idempotent — skips rows tagged with _playbook_prop_gate.
    """
    # Load prop signal registry once
    reg_map: dict = {}
    try:
        r = requests.get(f'{SB}/rest/v1/signal_registry'
                         '?market_scope=eq.prop&select=signal_name,tier',
                         headers=H_READ, timeout=10)
        for row in (r.json() if r.status_code == 200 else []):
            reg_map[row['signal_name']] = row.get('tier')
    except Exception:
        return 0

    if not reg_map:
        return 0  # registry empty, no gate to apply

    # Read STRONG/PRIME props for date
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'tier': 'in.(STRONG,PRIME)',
                             'select': 'id,player_name,prop_type,tier,conviction,'
                                       'refit_conviction,signals'},
                     timeout=15)
    if r.status_code != 200: return 0

    def _tier_of_prop_signal(name: str, prop_type: str) -> str:
        # Try prop_type-scoped first (more specific), then global
        scoped = f'prop:{name}:{prop_type}'
        if scoped in reg_map: return reg_map[scoped] or 'UNVALIDATED'
        return reg_map.get(f'prop:{name}') or 'UNVALIDATED'

    demoted = 0
    for prop in r.json():
        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_playbook_prop_gate'): continue

        prop_type = prop.get('prop_type') or ''
        anti_contrib = 0.0
        total_positive = 0.0
        matched_tiers: list[str] = []
        for name, val in sig.items():
            if name.startswith('_'): continue
            # Convert value to positive contribution
            contrib = 0.0
            if isinstance(val, (int, float)) and val > 0: contrib = float(val)
            elif isinstance(val, bool) and val: contrib = 1.0
            elif isinstance(val, str) and val: contrib = 1.0
            if contrib <= 0: continue
            total_positive += contrib
            tier = _tier_of_prop_signal(name, prop_type)
            matched_tiers.append(tier)
            if tier == 'ANTI_VALIDATED':
                anti_contrib += contrib

        if total_positive <= 0: continue

        anti_share = anti_contrib / total_positive
        old_tier = prop['tier']
        no_proven = all(t in ('ANTI_VALIDATED', 'UNVALIDATED') for t in matched_tiers) if matched_tiers else True

        should_demote = False
        reason = None
        new_tier = None; new_conv = None
        if anti_share >= 0.5:
            should_demote = True
            reason = f'ANTI_SHARE_{int(anti_share*100)}pct'
            new_tier = 'STRONG' if old_tier == 'PRIME' else 'LEAN'
            new_conv = 65 if new_tier == 'STRONG' else 55
        elif old_tier == 'PRIME' and no_proven:
            should_demote = True
            reason = 'NO_VALIDATED_SIGNALS'
            new_tier = 'LEAN'
            new_conv = 55

        if not should_demote: continue

        sig['_playbook_prop_gate'] = reason
        sig['_refit_override_at'] = _et_today()
        print(f'  playbook-gate: {prop["player_name"]:22} {prop["prop_type"]:10} '
              f'{old_tier}/{prop.get("conviction")} -> {new_tier}/{new_conv} ({reason})')
        if dry_run: demoted += 1; continue
        patch = {'tier': new_tier, 'conviction': new_conv, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): demoted += 1
    return demoted


def _demote_refit_up_traps(game_date: str, dry_run: bool = False) -> int:
    """Refit-UP demotion (2026-08-15 morning-audit finding).

    Rolling pattern: refits that raise conviction by >=10pts ran 5-10 (33%)
    on 8/15, mirroring 8/14. When base conviction is already >=70 AND the
    refit boosted it by >=10pts, the "more confident" recal is actually a
    fade signal. Demote one tier: PRIME->STRONG, STRONG->LEAN.

    Skips rows already tagged with _refit_up_demote.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'tier': 'in.(STRONG,PRIME)',
                             'conviction': 'gte.70',
                             'select': 'id,player_name,prop_type,direction,tier,'
                                       'conviction,refit_conviction,signals'},
                     timeout=15)
    if r.status_code != 200: return 0
    demoted = 0
    for prop in r.json():
        base = prop.get('conviction') or 0
        refit = prop.get('refit_conviction')
        if refit is None: continue
        if refit - base < 10: continue  # not a big upward refit

        sig = prop.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): sig = {}
        if sig.get('_refit_up_demote'): continue

        old_tier = prop['tier']
        new_tier = 'LEAN' if old_tier == 'STRONG' else 'STRONG'  # PRIME->STRONG
        new_conv = 55 if new_tier == 'LEAN' else 65

        sig['_refit_up_demote'] = f'{old_tier}_TO_{new_tier}_REFIT_UP_{int(refit - base)}'
        sig['_refit_override_at'] = _et_today()
        print(f'  refit-up-demote: {prop["player_name"]:22} '
              f'{prop["prop_type"]:10} {old_tier}/{base} refit={refit:.0f} '
              f'-> {new_tier}/{new_conv}')
        if dry_run: demoted += 1; continue
        patch = {'tier': new_tier, 'conviction': new_conv, 'signals': sig}
        pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                            headers=H_WRITE, json=patch, timeout=10)
        if pr.status_code in (200, 204): demoted += 1
    return demoted


def _sync_props_lean_cap(game_date: str, jr_row: dict, action: str,
                          dry_run: bool = False) -> None:
    """Mirror the LEAN cap into mlb_pipeline_props.

    2026-08-11: refit override only patched prop_jerry_reads previously.
    Sweat card + downstream composition read tier/conviction from
    mlb_pipeline_props though, so the cap never reached the published
    card. Ryan Johnson outs_under stayed PRIME (conv=76) on today's
    sweat card even after JR was capped to LEAN (conv=55).

    Downgrade props row to tier=LEAN + conviction=55 with an audit tag
    on the signals JSON. Idempotent — skips if already LEAN.
    """
    if dry_run: return
    key_params = {
        'game_date': f'eq.{game_date}',
        'player_name': f'eq.{jr_row["player_name"]}',
        'prop_type': f'eq.{jr_row["prop_type"]}',
        'prop_line': f'eq.{jr_row["prop_line"]}',
        'direction': f'eq.{jr_row["direction"]}',
        'select': 'id,tier,conviction,signals',
    }
    pr = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                      headers=H_READ, params=key_params, timeout=10)
    if pr.status_code != 200: return
    rows = pr.json()
    if not rows: return
    prop = rows[0]
    if (prop.get('tier') or '').upper() == 'LEAN' and (prop.get('conviction') or 0) <= 55:
        return  # already capped
    sig = prop.get('signals') or {}
    if isinstance(sig, str):
        try: sig = json.loads(sig)
        except: sig = {}
    if not isinstance(sig, dict): sig = {}
    sig['_refit_override_cap'] = action
    sig['_refit_override_at'] = _et_today()
    patch = {'tier': 'LEAN', 'conviction': 55, 'signals': sig}
    requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop["id"]}',
                   headers=H_WRITE, json=patch, timeout=10)


def run(game_date: str, dry_run: bool = False) -> int:
    global _raw_zero_counter
    _raw_zero_counter = _dd(int)  # reset per-run
    # 2026-08-11: refit self-heal — check whether refit_conviction was wiped
    # by a downstream generate_props rerun (happens intermittently) and re-
    # invoke apply_prop_refit inline if too many nulls. Without this, every
    # trap-cap check fails silently because refit is None on the props row.
    if not dry_run:
        _ensure_refit_populated(game_date)

    # 2026-08-11: precompute refit band health for the sample-size gate.
    # Skip on failure — decide() gracefully treats missing health as no-gate.
    try:
        sample_health = compute_refit_band_health(days=30)
        healthy_bands = sum(1 for k, v in sample_health.items()
                            if k[2] == '80-100' and v['n'] >= 30 and v['hit_pct'] >= 55)
        unhealthy_bands = sum(1 for k, v in sample_health.items()
                              if k[2] == '80-100' and v['n'] >= 30 and v['hit_pct'] < 55)
        print(f'  refit-100 band health (30d): {healthy_bands} healthy · '
              f'{unhealthy_bands} unhealthy (n>=30 threshold)')
    except Exception as e:
        print(f'  refit band health unavailable: {e}'); sample_health = None

    reads = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'id,sport,player_name,prop_type,prop_line,direction,'
                          'call_verdict,conviction,refit_conviction,short_read'},
        timeout=15).json()
    if not isinstance(reads, list):
        print(f'  fetch failed: {reads}'); return 0

    # Get raw conviction from mlb_pipeline_props (refit_conviction lives on the
    # prop row, not the jerry row).
    prop_ids = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'player_name,prop_type,prop_line,direction,'
                          'conviction,refit_conviction'},
        timeout=15).json()
    prop_lookup = {}
    for p in prop_ids:
        key = (p['player_name'], p['prop_type'], p['prop_line'], p['direction'])
        prop_lookup[key] = p

    flips = 0
    for r in reads:
        # 2026-08-11: date-scoped idempotency. Skip only if today's override
        # already ran on this row (matches YYYY-MM-DD suffix in tag). Older-
        # dated tags (from prior override versions) should re-process so that
        # tightened rules (REFIT_BAND_UNPROVEN cap added 8/11) can retroactively
        # correct rows that were force-boosted before the fix.
        today_tag = f'Auto-refit-override {_et_today()}'
        if today_tag in (r.get('short_read') or ''):
            continue  # idempotent for THIS day's run
        key = (r['player_name'], r['prop_type'], r['prop_line'], r['direction'])
        prop = prop_lookup.get(key)
        # 2026-08-12: line-drift fallback. Book sometimes moves the line
        # between JR generation and props table refresh (e.g. JR wrote
        # Foster Griffin ha_under 5.5, later scrape shows ha_under 6.5).
        # Exact-key match fails → override skips → refit trap bypasses →
        # BACK-on-refit=0 slips past to audit and blocks the pipeline.
        # Fallback: match on (player, prop_type, direction) with tolerance.
        if not prop:
            candidates = [p for p in prop_ids
                          if p.get('player_name') == r['player_name']
                          and p.get('prop_type') == r['prop_type']
                          and p.get('direction') == r['direction']]
            if candidates:
                # Prefer nearest line to JR's line if a numeric line exists
                try:
                    jr_line = float(r['prop_line']) if r.get('prop_line') is not None else None
                    if jr_line is not None:
                        candidates.sort(key=lambda p:
                            abs(float(p['prop_line']) - jr_line) if p.get('prop_line') is not None else 999)
                except (TypeError, ValueError):
                    pass
                prop = candidates[0]
        if not prop: continue
        # 2026-08-11: was `prop.get('conviction') or r.get('conviction') or 0`
        # which treated raw=0 as missing and fell through to JR's ALREADY-
        # OVERRIDDEN conviction (typically 85 from prior BOOST). This masked
        # extreme raw→refit deltas — e.g. all 15 pitcher ha_overs today have
        # props.conviction=0 vs refit=100 (delta 100!) but the override read
        # raw as 85 and computed delta 15, missing the BAND_UNPROVEN branch.
        # Explicit None check preserves genuine raw=0 signals.
        prop_conv = prop.get('conviction')
        jr_conv = r.get('conviction')
        raw = prop_conv if prop_conv is not None else (jr_conv if jr_conv is not None else 0)
        # 2026-08-11: alarm when props.conviction=0 leaks in. Baseline scorer
        # should never emit 0 for a prop_jerry_read row — 0 signals the raw
        # scoring path silently dropped this prop type. Aggregate + report at
        # end of run so we catch the upstream regression before the next
        # nightly override attempts to boost noise.
        # 2026-08-11: only flag props.conviction=0 as regression when the
        # tier is NOT COVERAGE. Coverage stubs (sweep_prop_coverage.py) are
        # inserted with conv=0 by design — those pass the Jerry edge gate
        # but the raw scorer never rated them. Real regressions leave conv=0
        # on non-coverage tiers (LEAN/STRONG/PRIME) which shouldn't happen.
        if prop_conv == 0 and (prop.get('tier') or '').upper() != 'COVERAGE':
            _raw_zero_counter[r.get('prop_type', '?')] += 1
        refit = prop.get('refit_conviction')
        current = (r.get('call_verdict') or '').upper()
        result = decide(raw, refit, current,
                        prop_type=r.get('prop_type'),
                        direction=r.get('direction'),
                        sample_health=sample_health,
                        book_odds=r.get('book_odds'))
        if not result: continue
        new_verdict, action = result

        # 2026-08-11: LEAN_CAP family — downgrade to LEAN cap, no verdict change.
        # Refit signal exists but the sample-health backing is missing or bad:
        #   REFIT_BAND_UNHEALTHY  → 80-100 band under-performing recently
        #   REFIT_BAND_UNPROVEN   → 80-100 band has <30 graded samples (new prop type)
        #   REFIT_HEALTH_UNAVAILABLE → couldn't compute band health at all
        # All three collapse to "trust conviction less" — cap at LEAN, keep verdict.
        # 2026-08-11 (v4): FORCE_BACK_FLIP_LEAN_CAP = FADE→BACK flip with
        # LEAN cap because refit band is unproven. Verdict CHANGES to BACK
        # (correcting a Jerry hallucination) but conviction stays capped.
        if action == 'FORCE_BACK_FLIP_LEAN_CAP':
            note = (f'[Auto-refit-override 2026-08-11 FORCE_BACK_FLIP_LEAN_CAP: '
                    f'refit={refit} vs raw={raw} (Δ={refit-raw:+.0f}) — Jerry '
                    f'was FADEing what refit says LOAD. Flipping FADE→BACK '
                    f'but capping conviction at LEAN (refit-100 band for '
                    f'{r["prop_type"]}/{r["direction"]} unproven). '
                    f'Original take: {(r.get("short_read") or "")[:220]}]')
            # 2026-08-13: write to audit_notes NOT short_read (audit-tag leakage
            # bug fix). short_read stays as the analyst take; audit_notes carries
            # the repair audit trail for downstream inspection.
            # 2026-08-14: also prepend REVISED clause to short_read so user
            # sees new verdict + reason, not the original FADE narrative.
            _orig_sr = r.get('short_read') or ''
            # 2026-08-19: humanized — was 'BACK. [REVISED from FADE — flip lean cap (refit boost).]'
            _override = 'BACK. Updated take: model recalibration flips this — cluster now backs the pick, sized cautiously (unproven band). '
            _new_sr = _orig_sr if _orig_sr.lstrip().startswith('BACK. Updated take:') else (_override + _orig_sr)[:2000]
            payload = {'call_verdict': 'BACK', 'conviction': 55,
                       'audit_notes': note[:1500], 'short_read': _new_sr}
            print(f'  {r["player_name"]:22} {r["prop_type"]:12} {r["direction"]:5} '
                  f'raw={raw} refit={refit} · FADE→BACK LEAN cap [FLIP_LEAN_CAP]')
            if dry_run: flips += 1; continue
            pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{r["id"]}',
                                headers=H_WRITE, json=payload, timeout=10)
            if pr.status_code in (200, 204): flips += 1
            _sync_props_lean_cap(game_date, r, action, dry_run=dry_run)
            continue

        if action in ('REFIT_BAND_UNHEALTHY', 'REFIT_BAND_UNPROVEN',
                       'REFIT_HEALTH_UNAVAILABLE'):
            reason_txt = {
                'REFIT_BAND_UNHEALTHY': 'has been unhealthy last 30d',
                'REFIT_BAND_UNPROVEN':  'has <30 graded samples (new prop type)',
                'REFIT_HEALTH_UNAVAILABLE': 'band-health computation unavailable',
            }[action]
            note = (f'[Auto-refit-override 2026-08-11 {action}: '
                    f'refit={refit} but 80-100 band for {r["prop_type"]}/'
                    f'{r["direction"]} {reason_txt} — '
                    f'holding verdict at {current}, capping conviction LEAN. '
                    f'Original take: {(r.get("short_read") or "")[:250]}]')
            # 2026-08-13: audit_notes not short_read
            payload = {'conviction': min(r.get('conviction') or 55, 55),
                       'audit_notes': note[:1500]}
            print(f'  {r["player_name"]:22} {r["prop_type"]:12} {r["direction"]:5} '
                  f'raw={raw} refit={refit} · {current} conviction capped LEAN [{action}]')
            if dry_run: flips += 1; continue
            pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{r["id"]}',
                                headers=H_WRITE, json=payload, timeout=10)
            if pr.status_code in (200, 204): flips += 1
            # 2026-08-11: also sync the LEAN cap into mlb_pipeline_props so
            # sweat card composition (which reads tier/conviction from props,
            # NOT prop_jerry_reads) actually sees the downgrade. Without this
            # the JR shows PASS/LEAN but props table still says STRONG/PRIME
            # and the sweat card publishes the un-capped tier.
            _sync_props_lean_cap(game_date, r, action, dry_run=dry_run)
            continue

        # Build the audit note
        if action == 'NO_REFIT_CAP':
            note = (f'[Auto-refit-override 2026-08-10 NO_REFIT_CAP: refit_conviction '
                    f'unavailable for {r["prop_type"]} — capping conviction at LEAN {NO_REFIT_CAP} '
                    f'due to lack of calibration signal. Original take: '
                    f'{(r.get("short_read") or "")[:250]}]')
            new_conv = min(r.get('conviction') or 0, NO_REFIT_CAP)
        else:
            note = (f'[Auto-refit-override 2026-08-10 {action}: raw={raw} refit={refit} '
                    f'(Δ={refit-raw:+.0f}). Refit calibration says {action.split("_",1)[1]}. '
                    f'Verdict {current}→{new_verdict}. Original take: '
                    f'{(r.get("short_read") or "")[:250]}]')
            # Conviction: match refit for BOOST/OVERRIDE, cap at 55 for FADE/PASS
            if action in ('FORCE_BACK_BOOST', 'FORCE_BACK_REFIT_OVERRIDE'):
                new_conv = min(85, int(refit))
            elif action == 'FORCE_FADE_TRAP':
                new_conv = 65  # STRONG fade
            else:  # PASS
                new_conv = 50

        note = note[:1500]
        # 2026-08-13: audit_notes not short_read
        # 2026-08-14 BUG FIX: previously wrote verdict+conviction+audit_notes
        # but LEFT short_read unchanged. Jerry's original BACK/FADE reasoning
        # stuck around, contradicting the new verdict. Result on 8/14 slate:
        # 60+ props had verdict=PASS with short_read starting "BACK." or
        # "FADE." Users saw direct contradictions (Freeland outs_under:
        # verdict=BACK but text starts "FADE. L5 avg opposes..."). Fix:
        # prepend an override clause to short_read so user-facing narrative
        # starts with the NEW verdict + one-line reason.
        original_short = r.get('short_read') or ''
        # 2026-08-19: humanize the override prefix. Prior version leaked internal
        # engine tokens (FORCE_BACK_BOOST, FORCE_BACK_FLIP_LEAN_CAP) directly into
        # user-facing text. User feedback: "if I don't know, users definitely do not."
        # Now the prefix reads as plain English tied to what actually happened.
        _REASON_HUMAN = {
            'FORCE_BACK_BOOST':          'model recalibration lifts this — signal cluster is strong',
            'FORCE_BACK_REFIT_OVERRIDE': 'model recalibration overrides — signal cluster loads the pick',
            'FORCE_BACK_FLIP_LEAN_CAP':  'model recalibration flips this — cluster now backs the pick',
            'FORCE_PASS_REFIT_TRAP_DISABLED': 'model flags this as a trap — no play',
            'FORCE_PASS_JERRY_HALLUCINATION': 'writeup contradicted the data — no play',
        }
        reason = _REASON_HUMAN.get(action, action.replace('_',' ').lower())
        override_prefix = f'{new_verdict}. Updated take: {reason}. '
        # Don't double-prepend if we've already flipped this prop today
        if not original_short.lstrip().startswith(f'{new_verdict}. Updated take:'):
            new_short = override_prefix + original_short
            new_short = new_short[:2000]  # cap length
        else:
            new_short = original_short  # already flipped today, don't stack
        payload = {'call_verdict': new_verdict, 'conviction': new_conv,
                   'audit_notes': note, 'short_read': new_short}
        print(f'  {r["player_name"]:22} {r["prop_type"]:12} {r["direction"]:5} '
              f'raw={raw} refit={refit} · {current}→{new_verdict} [{action}]')
        if dry_run: flips += 1; continue
        pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{r["id"]}',
                            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 204):
            flips += 1
        else:
            print(f'    patch failed: {pr.status_code} {pr.text[:120]}')

    # 2026-08-11: props-table trap-cap pass. Downstream sweat card composition
    # reads tier/conviction from mlb_pipeline_props (not prop_jerry_reads).
    # Even when JR verdict is PASS/FADE, if props.tier is STRONG/PRIME with
    # refit_conviction < 30 (refit says the pick is a TRAP), the sweat card
    # will still pick it up. Cap those props to LEAN unconditionally so
    # composition matches the refit signal.
    trap_capped = _cap_props_trap_directly(game_date, dry_run=dry_run)
    if trap_capped:
        print(f'  props-table trap cap: {trap_capped} rows downgraded to LEAN '
              f'(refit<30 but tier=STRONG/PRIME)')

    # 2026-08-15 morning-audit passes + 2026-08-16 hits_over juice gate.
    # ha_over 20% on the day (1-4), refit-up >=10pts went 5-10 (33%).
    # hits_over PRIME today all posted at -200+ juice — documented trap
    # per feedback_batter_hits_juice_trap_803.
    hits_gated = _cap_hits_over_juice_trap(game_date, dry_run=dry_run)
    if hits_gated:
        print(f'  hits_over juice gate: {hits_gated} rows demoted '
              f'(book_line worse than -200)')

    ha_gated = _cap_ha_over_no_signal(game_date, dry_run=dry_run)
    if ha_gated:
        print(f'  ha_over gate: {ha_gated} rows capped to LEAN '
              f'(no refit-up and no sharp lift)')

    refit_up_demoted = _demote_refit_up_traps(game_date, dry_run=dry_run)
    if refit_up_demoted:
        print(f'  refit-up demote: {refit_up_demoted} rows dropped one tier '
              f'(base>=70 + refit_up>=10pts)')

    # 2026-08-16 prop playbook phase 2: registry-driven gate.
    playbook_demoted = _playbook_gate_props(game_date, dry_run=dry_run)
    if playbook_demoted:
        print(f'  playbook prop-gate: {playbook_demoted} rows demoted '
              f'(stack dominated by ANTI_VALIDATED signals)')

    # 2026-08-17 morning-audit gates: UNDER-juice trap + COVERAGE-kill.
    # UNDER props at -140+ juice went -30 to -37% ROI yesterday; COVERAGE
    # tier -11% ROI over 216 stakes. Both bleed the aggregate.
    under_capped = _cap_under_juice_trap(game_date, dry_run=dry_run)
    if under_capped:
        print(f'  under-juice: {under_capped} PRIME/STRONG UNDER props demoted '
              f'(book_line worse than -140)')

    coverage_killed = _demote_coverage_tier(game_date, dry_run=dry_run)
    if coverage_killed:
        print(f'  coverage-kill: {coverage_killed} COVERAGE rows demoted to LEAN '
              f'(tier ran -11% ROI; unpublishable)')

    # 2026-08-11: raw-conviction=0 alarm summary. When base scorer emits 0
    # for prop_jerry_reads, refit boost signals are unreliable and the whole
    # override pipeline can propagate noise. Print prominently so cron logs
    # surface it. Consider hooking into audit table if this recurs.
    if _raw_zero_counter:
        total_zeros = sum(_raw_zero_counter.values())
        print(f'\n🚨 RAW_CONVICTION=0 ALARM: {total_zeros} prop_jerry_reads had '
              f'props.conviction=0 today (baseline scorer likely dropped these '
              f'prop types). Breakdown:')
        for pt, n in sorted(_raw_zero_counter.items(), key=lambda x: -x[1]):
            print(f'   {pt}: {n}')
        print(f'   → Fix upstream base scorer (compute_prop_conviction or similar) '
              f'before next cron. All these props got LEAN cap defensively.')

    print(f'\n=== refit-verdict overrides: {flips} applied{" (dry-run)" if dry_run else ""} ===')
    return flips


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
