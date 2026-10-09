"""Re-run ensemble scoring on NCAAF ctx after tendencies backfill (2026-08-28).

Sequencing bug this fixes: ncaaf_game_context.py builds primary_play from
a freshly-composed row that does NOT read team_form tendency fields
(home_ats_l10_at_home, away_ml_l10_on_road, etc.). Those fields get
populated in a downstream PATCH by backfill_ncaaf_team_tendencies.py.
Result: primary_play was frozen at the pre-tendency scoring (usually
ml/LEAN) and never picked up SPREAD/team_form signals that need those
fields to fire.

Mirrors MLB's recompute_primary_play.py pattern — reads ctx rows AFTER
tendencies land, re-scores with score_game, patches primary_play in place.

Run this in the workflow AFTER backfill_ncaaf_team_tendencies.py.

CLI:
  python recompute_ncaaf_primary_play.py                 # today
  python recompute_ncaaf_primary_play.py --date 2026-08-29
  python recompute_ncaaf_primary_play.py --days 10       # today + next 9 days
  python recompute_ncaaf_primary_play.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass



def _preserve_manual(old_pp, new_pp):
    """Guarded wrapper — an import problem must not break a recompute."""
    try:
        from manual_pick_override import preserve
        return preserve(old_pp or {}, new_pp)
    except Exception as e:                              # noqa: BLE001
        print(f'  ! manual-override preserve unavailable ({e})')
        return new_pp, None

def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_ctx_window(start_date: str, days: int, lookback: int = 0) -> list[dict]:
    # 2026-09-15: added lookback param (ported from NFL fix 657a738d).
    # Prior forward-only window orphaned any game whose primary_play was
    # stamped once at seed-time and never re-touched by a later same-day
    # recompute — root cause of NFL W1 LR-shadow gap. Same class of bug
    # would trip NCAAF as its weekly cadence produces the same "long
    # gap between seed + game" pattern. Default 0 for back-compat with
    # legacy callers; argparse default in main() is 7 for daily cron.
    anchor = datetime.fromisoformat(start_date)
    start = (anchor - timedelta(days=lookback)).date().isoformat()
    end   = (anchor + timedelta(days=days-1)).date().isoformat()
    out = []
    for off in range(0, 5000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_context'
            f'?game_date=gte.{start}&game_date=lte.{end}'
            f'&select=*&limit=1000&offset={off}',
            headers=H_R, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): break
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


# ══ 2026-09-26 · HONOUR THE PUBLISH LOCK ══
# Andy: "why did the Akron game primary play change, now says Over when it
# said Akron +13.5 — I thought NCAAF picks were locked for the weekend."
#
# They were supposed to be. pick_lock.lock_active('NCAAF') returns True from
# Thu 8am ET through Sunday and did return True at the moment the pick
# changed. But the lock was only wired into ncaaf_game_context.py's write path, and this
# script PATCHes primary_play directly — so it walked straight past it.
#
# That is the exact failure pick_lock's own docstring records for MLB:
# "its recompute script honoured the lock while the context builder wrote
# primary_play directly". Same bug, mirror image: here the context builder
# honours it and the recompute script does not.
#
# A published pick now survives; a game with no published pick is still
# written, because a gap is not a change.
def _pp_locked(game_id, new_pp):
    """True when a published pick exists and must not be overwritten."""
    try:
        from pick_lock import lock_active
    except Exception as _e:
        print(f'  ⚠ pick_lock unavailable ({type(_e).__name__}) — writing UNLOCKED')
        return False
    if not lock_active('NCAAF'):
        return False
    try:
        rr = requests.get(f'{SB}/rest/v1/ncaaf_game_context', headers=H_W, timeout=15,
                          params={'game_id': f'eq.{game_id}',
                                  'select': 'primary_play'})
        rows = rr.json() if rr.status_code == 200 else []
    except Exception:
        return True   # cannot verify under an active lock -> refuse to write
    if not rows:
        return False
    published = rows[0].get('primary_play')
    if not published:
        return False  # a gap is not a change
    old = published.get('label') if isinstance(published, dict) else published
    new = new_pp.get('label') if isinstance(new_pp, dict) else new_pp
    if old != new:
        print(f'  🔒 NCAAF lock: kept published {old!r} (recompute wanted {new!r})')
    return True

def patch_pp(game_id: str, pp: dict) -> bool:
    # 2026-08-28 said: no `primary_play_computed_at` column, so "skip the
    # stamp — recompute timing is inferable from ... the row's updated_at."
    #
    # ══ 2026-09-30 CORRECTION ══
    # It was not inferable, because THIS FUNCTION never set updated_at and
    # neither does any trigger — verified by patching 374 NCAAF rows today
    # and seeing zero timestamp movement. A comment asserting a property the
    # code does not provide, which is the same trap as the 09-20 note that
    # blocked a fix for nine days (feedback_comment_asserting_a_measurement).
    #
    # NCAAF got away with it only by accident: ncaaf_game_context.py rebuilds
    # the row minutes before this recompute runs in the same pipeline pass, so
    # updated_at landed close enough to pick time for the leak guard to work.
    # NFL has no such luck — 239 of 284 rows sat at their July INSERT time, so
    # `updated_at < kickoff_utc` passed every row trivially and 77% of NFL was
    # unmeasurable.
    #
    # Stamping it here makes the column mean what every backtest assumes: when
    # the PICK was last written, not when the stats around it were.
    #
    # NOTE this will legitimately SHRINK leak-free NCAAF samples: a pick
    # recomputed inside the lookback window after kickoff now stamps a
    # post-kickoff time and gets excluded, where before it slipped through.
    # That is the guard becoming honest, not a regression.
    if _pp_locked(game_id, pp):
        return False
    payload = {'primary_play': pp,
               'updated_at': datetime.now(timezone.utc).isoformat()}
    for attempt in range(3):
        try:
            r = requests.patch(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
                               headers=H_W, json=payload, timeout=30)
            if r.status_code in (200, 201, 204): return True
        except requests.exceptions.RequestException:
            if attempt == 2: return False
            time.sleep(2 ** attempt)
    return False


def run(start_date: str, days: int, dry_run: bool = False, lookback: int = 0) -> None:
    _lb_str = f' (-{lookback}d back)' if lookback else ''
    print(f'=== recompute_ncaaf_primary_play · {start_date} +{days-1}d{_lb_str} ===')
    try:
        from ensemble_scorer import score_game
        from game_context import _compose_ensemble_sub
        from defensive_gates import apply_all_defensive_gates, reroute_ml_if_trapped
    except ImportError as e:
        print(f'  FAIL importing scorer: {e}'); return

    rows = fetch_ctx_window(start_date, days, lookback=lookback)
    print(f'  ctx rows in window: {len(rows)}')

    changed = 0; patched = 0; rerouted = 0
    for g in rows:
        old_pp = g.get('primary_play') or {}
        old_key = f"{old_pp.get('type')}/{old_pp.get('label')}/{old_pp.get('tier')}"
        try:
            decision = score_game('NCAAF', g)
        except Exception as e:
            print(f'  score failed {g.get("away_team")}@{g.get("home_team")}: {e}'); continue
        if decision is None: continue
        # 2026-09-01: heavy-fav ML reroute (see ncaaf_game_context.py note)
        decision = reroute_ml_if_trapped(decision, g, sport='NCAAF')
        top = decision.top()
        if top.pick is None: continue
        _reroute = getattr(top, '_ml_reroute', None)
        _audit = (f'ensemble_scorer v2 · NCAAF · recompute · {len(top.contributions)} sources · '
                  f'score={top.score:.2f} margin={top.margin:+.2f}')
        if _reroute:
            _audit += f' · ML-reroute({_reroute["orig_market"]}→{top.market} @ {_reroute["orig_ml_price"]})'
            rerouted += 1

        new_pp = {
            'type': top.market, 'tier': top.tier, 'label': top.display_label,
            'side': top.side, 'line': top.line, 'conviction': top.conviction,
            'score': round(top.score, 2), 'sub': _compose_ensemble_sub(top),
            'audit_note': _audit,
            '_engine': 'ensemble_v2',
            '_ensemble_sources': [
                {'signal_key': c.signal_key, 'class': c.signal_class,
                 'side': c.side, 'weight': round(c.weight, 2),
                 'n': c.n, 'contribution': round(c.contribution, 2),
                 'prose': c.display_prose}
                for c in top.contributions[:8]
            ],
            # 2026-10-08 · PERSIST THE LOSING SIDE TOO.
            # B66 measured the registry hit_rate at r=+0.20 against
            # each signal's real performance (11pp mean error), so the
            # weights are close to noise and reweighting is the obvious
            # fix. It could not be EVALUATED: of 408 graded NCAAF picks,
            # 0 stored contributions for more than one side, and there
            # is no runner-up field — so "would the pick have flipped
            # under different weights" was unanswerable. Re-running the
            # scorer historically is not a substitute: team_stats_rolling
            # is current-only and leaks
            # (project_rolling_stats_leak_trap_929).
            # top.runner_up_contributions is already computed on every
            # decision and was thrown away. Same precedent as
            # _ensemble_all_markets below, which exists so the
            # "what if we'd played spread instead of ML" question stays
            # answerable. A shadow becomes measurable once this has a
            # few weeks of slates behind it.
            '_ensemble_runner_up': {
                'side': getattr(top, 'runner_up_side', None),
                'sources': [
                    {'signal_key': c.signal_key,
                     'class': c.signal_class,
                     'side': c.side,
                     'weight': round(c.weight, 2),
                     'n': c.n,
                     'contribution': round(c.contribution, 2),
                     'hit_rate': (round(c.hit_rate, 3)
                                  if c.hit_rate is not None else None)}
                    for c in getattr(top, 'runner_up_contributions',
                                     [])[:8]
                ],
            },
        }
        if _reroute:
            new_pp['_ml_reroute'] = _reroute
        # 2026-09-01: defensive gates (juice-trap floor -300 for NCAAF)
        new_pp = apply_all_defensive_gates(new_pp, g, sport='NCAAF')
        if new_pp is None: continue

        # 2026-09-12 LR DISAGREEMENT GUARDRAIL. Andy audit finding: 42 of 50
        # dog picks (84%) on 9/12 have LR giving picked team <35% outright
        # win probability. 20 hard-disagree (<25%). Root cause: signal-
        # stacking on FBS-vs-FCS/D-II buy games — three unreliable priors
        # (prior-season penalty rate, FCS/G5 L10-road ATS, D-II lineman
        # weight) compound to STRONG on dogs LR calls near-impossible.
        # Canary: Mercyhurst (D-II) +41.5 @ New Mexico — LR gives home
        # 98.6%, ensemble stacked to STRONG dog. This is a bias, not edge.
        #
        # Rule: if picked side's outright-win probability per LR ml shadow
        # is below LR_DISAGREE_HARD_THRESHOLD AND ensemble tier is
        # PRIME/STRONG, cap tier at LEAN. Below LR_DISAGREE_SUPPRESS_THRESHOLD
        # (very hard disagree), suppress entirely (return None → not
        # published as primary_play). LR-aligned picks untouched.
        #
        # ML/total markets: this reads p_home_win directly. Spread market:
        # a dog can COVER without winning outright, so <20% ML outright
        # is a "LR calls the picked team a serious longshot" signal, not
        # a "LR says the pick loses" signal. Cap-at-LEAN is appropriate
        # (still surfaces, downweighted), not suppression.
        try:
            _lr_ml = ((g.get('lr_shadow') or {}).get('p_home_win')
                      if isinstance(g.get('lr_shadow'), dict) else None)
            if _lr_ml is None:
                _lr_ml = ((new_pp.get('_lr_ml_shadow') or {}).get('p_home_win')
                          if isinstance(new_pp.get('_lr_ml_shadow'), dict) else None)
            if _lr_ml is not None:
                _pick_side = (new_pp.get('side') or '').upper()
                _picked_ml_p = float(_lr_ml) if _pick_side == 'HOME' else (1 - float(_lr_ml))
                LR_DISAGREE_HARD_THRESHOLD     = 0.20  # cap tier at LEAN
                LR_DISAGREE_SUPPRESS_THRESHOLD = 0.10  # suppress entirely
                _cur_tier = (new_pp.get('tier') or '').upper()
                if _picked_ml_p < LR_DISAGREE_SUPPRESS_THRESHOLD and _cur_tier in ('PRIME','STRONG','LEAN'):
                    print(f'    LR-SUPPRESS {g.get("away_team","?")[:12]}@{g.get("home_team","?")[:12]}: '
                          f'{_cur_tier}→PASS, picked-side LR p={_picked_ml_p:.2f} < {LR_DISAGREE_SUPPRESS_THRESHOLD}')
                    # Force tier=PASS so app filters it out. Cannot `continue`
                    # here — previous stored primary_play is likely also
                    # STRONG on the same pick (deterministic ensemble), so
                    # skipping the patch leaves the ship-worthy label
                    # intact. Explicit PASS write ensures suppression.
                    new_pp['tier'] = 'PASS'
                    new_pp['_lr_disagreement_cap'] = {
                        'orig_tier': _cur_tier, 'picked_ml_p': round(_picked_ml_p, 3),
                        'action': 'suppress', 'threshold': LR_DISAGREE_SUPPRESS_THRESHOLD,
                    }
                elif _picked_ml_p < LR_DISAGREE_HARD_THRESHOLD and _cur_tier in ('PRIME','STRONG'):
                    print(f'    LR-CAP    {g.get("away_team","?")[:12]}@{g.get("home_team","?")[:12]}: '
                          f'{_cur_tier}→LEAN, picked-side LR p={_picked_ml_p:.2f} < {LR_DISAGREE_HARD_THRESHOLD}')
                    new_pp['tier'] = 'LEAN'
                    new_pp['_lr_disagreement_cap'] = {
                        'orig_tier': _cur_tier, 'picked_ml_p': round(_picked_ml_p, 3),
                        'threshold': LR_DISAGREE_HARD_THRESHOLD,
                    }
        except Exception as _e:
            print(f'    ⚠ LR-guardrail check failed for {g.get("game_id","?")[:12]}: {_e}')
        # ══ 2026-10-06 · AN APPROVED MANUAL PICK SURVIVES RECOMPUTE ══
        # Nothing in the repo read `_manual_correction` (verified by grep
        # across every .py), so an approved override was silently reverted by
        # the next recompute. MIL @ SD 2026-10-06 is the case: Andy asked for
        # the Padres ML on the card, the override was applied, and the engine
        # underneath it says COVERAGE with LR suggesting the OPPOSITE side
        # (_lr_p_home_win 0.4469, suggested_side AWAY). That is the
        # "opposite sides two minutes apart" symptom — not a scorer race, a
        # human decision with no protection against recomputes that run all
        # day.
        new_pp, _mo_note = _preserve_manual(old_pp, new_pp)
        if _mo_note:
            print(f'  ✋ {_mo_note}')
        new_key = f"{new_pp['type']}/{new_pp['label']}/{new_pp['tier']}"
        if new_key == old_key: continue
        changed += 1

        marker = f'  {g.get("game_date")} {g.get("away_team","?"):22s} @ {g.get("home_team","?"):22s}  {old_key[:34]:34s} → {new_key[:34]}'
        print(marker)
        if dry_run: continue
        if patch_pp(g['game_id'], new_pp): patched += 1

    # 2026-09-11 PERMANENT FIX for Jerry vs primary_play badge drift on NCAAF.
    # Ports the MLB (2026-09-09) + NFL (2026-09-11) auto-align pattern. NCAAF
    # jerry_cache is per-day, so once Jerry writes on cron day X, a later
    # recompute changing primary_play leaves jerry_reads.call_* pointing at
    # the stale pick until the next day's cron regenerates Jerry. Games tab
    # badge then disagrees with primary_play chip. backfill_jerry_pick_alignment
    # applies enforce_primary_play_alignment() to every jerry_read in the
    # window with a diff-only PATCH, keeping the two surfaces in permanent
    # sync regardless of cron order.
    if not dry_run and patched > 0:
        try:
            import subprocess
            from pathlib import Path as _P
            print(f'\n  → auto-aligning jerry_reads via backfill_jerry_pick_alignment')
            _script = str(_P(__file__).parent / 'backfill_jerry_pick_alignment.py')
            r = subprocess.run(
                [sys.executable, _script, '--sport', 'NCAAF'],
                capture_output=True, text=True, timeout=180)
            for line in ((r.stdout or '') + (r.stderr or '')).splitlines()[-10:]:
                print(f'    {line}')
            if r.returncode != 0:
                print(f'    ⚠ align exit {r.returncode}')
        except Exception as e:
            print(f'    ⚠ auto-align failed: {e} (jerry_reads may be out of sync)')

    prefix = '[DRY] ' if dry_run else ''
    print(f'\n{prefix}changed={changed}  patched={patched}  rerouted={rerouted}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Anchor date (default: today ET)')
    ap.add_argument('--days', type=int, default=10, help='Forward window from anchor (default 10)')
    ap.add_argument('--lookback', type=int, default=7,
                    help='Days BEHIND anchor to include (default 7). Catches games '
                         'from earlier in the current NCAAF week that were stamped '
                         'once at seed-time and never re-touched by a later '
                         'recompute pass — same class of bug as the NFL W1 '
                         'LR-shadow gap fixed 2026-09-14.')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(args.date or _et_today(), args.days, args.dry_run, lookback=args.lookback)


if __name__ == '__main__':
    main()
