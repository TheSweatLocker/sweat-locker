"""Re-run ensemble scoring on NFL ctx after team_form + defense + team stats
land (2026-08-28).

Sequencing bug this fixes: nfl_game_context.py builds primary_play from
a freshly-composed row that reflects the current state of nfl_team_stats
+ nfl_team_defense_stats at build time. But other enrichment steps
(team_form venue-split L10 + H2H, defense-stats backfill, panel
projections) run AFTER ctx build and PATCH the row in place. Signals
that need those fields don't fire on the initial score, so the picks
freeze at a partial-context version.

Mirrors MLB (recompute_primary_play.py) + NCAAF
(recompute_ncaaf_primary_play.py) patterns: read ctx rows AFTER all
enrichment, re-score via ensemble_scorer.score_game('NFL'), PATCH
primary_play in place. 3-retry with backoff on the PATCH.

Run this in the workflow AFTER:
  - Refresh team + player stats (nflverse)
  - NFL team defense stats backfill
  - Team form + trends enrichment

CLI:
  python recompute_nfl_primary_play.py                 # today's slate
  python recompute_nfl_primary_play.py --date 2026-09-07
  python recompute_nfl_primary_play.py --days 14       # full week window
  python recompute_nfl_primary_play.py --dry-run
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


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_ctx_window(start_date: str, days: int, lookback: int = 0) -> list[dict]:
    # 2026-09-14 v1.0.1: added `lookback` so the window can look BEHIND
    # the anchor date. Prior forward-only window orphaned any game whose
    # primary_play was stamped once at seed-time and never re-touched by
    # a later recompute pass — root cause of the NFL W1 LR-shadow gap
    # (NE@SEA 9/10 + SF@LA 9/11 had null _lr_ml_shadow from a July seed
    # while every game 9/13+ had shadow from the same-day recompute).
    # Default lookback is 0 to stay backward-compat with legacy callers;
    # the argparse default in main() bumps it to 7 for the daily cron.
    anchor = datetime.fromisoformat(start_date)
    start = (anchor - timedelta(days=lookback)).date().isoformat()
    end   = (anchor + timedelta(days=days-1)).date().isoformat()
    out = []
    for off in range(0, 5000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/nfl_game_context'
            f'?game_date=gte.{start}&game_date=lte.{end}'
            f'&select=*&limit=1000&offset={off}',
            headers=H_R, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): break
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


def _already_started(ctx: dict) -> bool:
    """True once a game's number must be treated as frozen.

    Prefers an explicit kickoff timestamp; falls back to game_date vs
    today ET. Errs toward True — treating a pregame row as started only
    costs a line refresh, while treating a played game as pregame
    restates a settled price.
    """
    for key in ('commence_time', 'kickoff_utc', 'game_time_utc', 'start_time'):
        raw = ctx.get(key)
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            pass
    gd = ctx.get('game_date')
    if not gd:
        return True
    try:
        return datetime.fromisoformat(str(gd)).date() < datetime.fromisoformat(_et_today()).date()
    except (TypeError, ValueError):
        return True


def _normalize_pp_side_label(pp: dict, ctx: dict) -> dict:
    """Guard: for rl/total picks, recompute label from side + line so a
    downstream mutator that flips side but not label can't leave the
    two contradicting each other.

    2026-09-17: Andy caught NYG @ LA with side=AWAY + label='LA -7' +
    line=None. The ensemble picked AWAY_RL (NYG +7 dissent) but the
    label got stale-cached from a prior HOME_RL pass. Post-writer
    normalizer re-derives label from side using NFL sign convention
    (pos close_spread = home favorite).

    2026-09-22: that same row survived to kickoff and graded wrong — the
    normalizer was correct but never ran. Two holes, both closed here;
    the third (it lived behind the week-lock early-return) is closed in
    run().

      1. 'spread' was not in the ptype allowlist. nfl_game_context emits
         BOTH 'rl' (sharp/ensemble path) and 'spread' (model-edge path,
         line ~1125) for the same market, so half of all spread picks
         were never checked.
      2. When close_spread was NULL the function returned the stale
         label untouched. The number needs a line, but the TEAM never
         did — it is knowable from side alone. A label naming the wrong
         team is the error that flips a graded result; a stale number
         only misprices it. Fix the team even when the line is unknown.

    Idempotent — running on an already-consistent pp is a no-op.
    """
    if not isinstance(pp, dict): return pp
    ptype = str(pp.get('type') or '').lower()
    side  = str(pp.get('side') or '').upper()
    if ptype not in ('rl', 'spread', 'total') or side not in ('HOME', 'AWAY', 'OVER', 'UNDER'):
        return pp

    # 2026-09-22 FROZEN-NUMBER GUARD. close_spread/close_total keep moving
    # after kickoff (and after settlement). Re-deriving the number from
    # them on a game already played would silently restate the price we
    # claim to have taken — turning 'LV +6.5' into 'LV +2.5' on a game
    # that finished days ago. That is the same class of error as
    # nhl_game_results.close_puckline holding moneylines: a plausible
    # number in the right column, wrong by provenance.
    #
    # So: the TEAM is always repairable (it is knowable from `side`, which
    # the week lock already froze, and a wrong team flips the grade). The
    # NUMBER is only re-derived while the game is still in the future.
    line_frozen = bool(_already_started(ctx))

    home = ctx.get('home_team') or 'HOME'
    away = ctx.get('away_team') or 'AWAY'
    try:
        raw_sp = float(ctx.get('close_spread')) if ctx.get('close_spread') is not None else None
    except (TypeError, ValueError):
        raw_sp = None
    try:
        raw_total = float(ctx.get('close_total')) if ctx.get('close_total') is not None else None
    except (TypeError, ValueError):
        raw_total = None

    # NFL: pos close_spread = home favorite. Flip to home-perspective
    # line so HOME +/- matches display. (MLB/NCAAF/etc use neg=home fav
    # natively — those recompute scripts each have their own normalizer.)
    home_line = -raw_sp if raw_sp is not None else None

    new_label = pp.get('label')
    new_line  = pp.get('line')
    if ptype in ('rl', 'spread'):
        picked = home if side == 'HOME' else away
        if home_line is not None and not line_frozen:
            signed = home_line if side == 'HOME' else -home_line
            new_line  = signed
            new_label = f'{picked} {signed:+g}'
        else:
            # Either no line to re-derive from, or the game has started
            # and the number is frozen. Still correct the team when the
            # label names the other side.
            #
            # The number travels with the team: a spread is quoted from
            # the perspective of whoever is named, so 'LA -7' re-pointed
            # at NYG is 'NYG +7', not 'NYG -7'. Negate on swap — keeping
            # the magnitude (which is the frozen market price) while
            # fixing the side it is quoted from.
            other = away if side == 'HOME' else home
            cur = str(new_label or '').strip()
            if cur and other and cur.split()[0] == other:
                rest = cur.split(' ', 1)
                if len(rest) > 1:
                    try:
                        flipped = -float(rest[1].replace(' ', ''))
                        new_label = f'{picked} {flipped:+g}'
                        if new_line is not None:
                            new_line = -float(new_line)
                    except ValueError:
                        new_label = f'{picked} {rest[1]}'
                else:
                    new_label = picked
    elif ptype == 'total':
        want = 'Over' if side == 'OVER' else 'Under'
        if raw_total is not None and not line_frozen:
            new_label = f'{want} {raw_total:g}'
            new_line = raw_total
        else:
            # Frozen (or no close_total): the NUMBER stays, but an
            # Over/Under word contradicting `side` is the same defect as
            # a spread naming the wrong team — it flips the grade — so
            # repair the direction and leave the price alone.
            cur = str(new_label or '').strip()
            head = cur.split(' ', 1)[0].lower() if cur else ''
            if head in ('over', 'under') and head != want.lower():
                rest = cur.split(' ', 1)
                new_label = f'{want} {rest[1]}' if len(rest) > 1 else want

    if new_label != pp.get('label') or new_line != pp.get('line'):
        pp = dict(pp)
        prior_label = pp.get('label')
        pp['label'] = new_label
        pp['line']  = new_line
        pp['_label_normalized_at'] = datetime.now(timezone.utc).isoformat()
        pp['_label_prior'] = prior_label
    return pp


# ══ 2026-09-26 · HONOUR THE PUBLISH LOCK ══
# Andy: "why did the Akron game primary play change, now says Over when it
# said Akron +13.5 — I thought NCAAF picks were locked for the weekend."
#
# They were supposed to be. pick_lock.lock_active('NCAAF') returns True from
# Thu 8am ET through Sunday and did return True at the moment the pick
# changed. But the lock was only wired into nfl_game_context.py's write path, and this
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
    if not lock_active('NFL'):
        return False
    try:
        rr = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_W, timeout=15,
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
        print(f'  🔒 NFL lock: kept published {old!r} (recompute wanted {new!r})')
    return True

def patch_pp(game_id: str, pp: dict) -> bool:
    # nfl_game_context has no primary_play_computed_at col (same as
    # ncaaf_game_context). Skip the stamp.
    if _pp_locked(game_id, pp):
        return False
    payload = {'primary_play': pp}
    for attempt in range(3):
        try:
            r = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{game_id}',
                               headers=H_W, json=payload, timeout=30)
            if r.status_code in (200, 201, 204): return True
        except requests.exceptions.RequestException:
            if attempt == 2: return False
            time.sleep(2 ** attempt)
    return False


def run_labels_only(start_date: str, days: int, dry_run: bool = False,
                    lookback: int = 0) -> int:
    """Run ONLY the side/label normalizer. Deliberately ignores the week
    lock; returns the number of rows repaired.

    WHY THIS BYPASSES THE LOCK. The lock exists to stop mid-week ensemble
    drift from flipping a pick out from under a frozen jerry writeup — it
    protects side/tier/market. This pass changes NONE of those: it only
    rewrites `label` and `line` to agree with the side the lock already
    froze. A label contradicting its own side is not a pick that needs
    protecting, it is a display bug, and leaving it frozen is what let
    NYG @ LA reach kickoff labelled 'LA -7' while side said AWAY.

    Same precedent as nfl_pipeline.yml's line-only jerry alignment step,
    which bypasses the lock for line drift on exactly this reasoning.

    Full recompute stays locked. Only the normalizer runs here.
    """
    _lb_str = f' (-{lookback}d back)' if lookback else ''
    print(f'=== recompute_nfl_primary_play · LABELS-ONLY · '
          f'{start_date} +{days-1}d{_lb_str} ===')
    rows = fetch_ctx_window(start_date, days, lookback=lookback)
    print(f'  ctx rows in window: {len(rows)}')
    fixed = failed = 0
    for g in rows:
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict) or not pp:
            continue
        new_pp = _normalize_pp_side_label(pp, g)
        if new_pp is pp or new_pp.get('label') == pp.get('label') and \
                new_pp.get('line') == pp.get('line'):
            continue
        tag = f"{g.get('away_team')}@{g.get('home_team')}"
        print(f"  {'[DRY] ' if dry_run else ''}{tag:<12} "
              f"side={pp.get('side'):<5} "
              f"'{pp.get('label')}' -> '{new_pp.get('label')}'  "
              f"line {pp.get('line')} -> {new_pp.get('line')}")
        if dry_run:
            fixed += 1
        elif patch_pp(g['game_id'], new_pp):
            fixed += 1
        else:
            failed += 1
            print(f'    PATCH FAILED {g["game_id"]}')
    print(f'  {"would repair" if dry_run else "repaired"} {fixed}'
          + (f'  FAILED {failed}' if failed else ''))
    return failed


def run(start_date: str, days: int, dry_run: bool = False, lookback: int = 0) -> None:
    _lb_str = f' (-{lookback}d back)' if lookback else ''
    print(f'=== recompute_nfl_primary_play · {start_date} +{days-1}d{_lb_str} ===')

    # 2026-09-16 WEEK-LOCK GUARD. Once Thu 8am ET passes, primary_play
    # for the week is frozen (locks LR shadow + tier + call into the
    # writeup they were generated against). NFL_UNLOCK_WEEK=1 env
    # override or explicit --force flag bypasses. Prevents mid-week
    # ensemble drift from flipping picks out from under locked jerry
    # writeups. Shares nfl_week_write_locked() semantics with
    # generate_nfl_game_reads.upsert_jerry_read_nfl + the alignment
    # backfill so all three lock in sync.
    import os as _os
    if _os.environ.get('NFL_UNLOCK_WEEK') != '1' and not dry_run:
        try:
            from generate_nfl_game_reads import nfl_week_write_locked
            if nfl_week_write_locked():
                print('  🔒 NFL week-locked (post Thu 8am ET) — skipping recompute. '
                      'NFL_UNLOCK_WEEK=1 or --dry-run to inspect.')
                return
        except Exception as _e:
            print(f'  ⚠ lock-check import failed ({_e}) — proceeding')

    try:
        from ensemble_scorer import score_game
        from game_context import _compose_ensemble_sub
        # 2026-09-13: NFL W1 losers audit found _lr_ml_shadow empty on
        # MIA/GB/PHI/LAC — root cause: this recompute built a fresh new_pp
        # from scorer output and never ran defensive_gates, whose
        # unconditional LR-shadow backfill (defensive_gates.py:1044-1055)
        # is where _lr_ml_shadow gets stamped every pass. NCAAF's
        # recompute_ncaaf_primary_play.py:129 already does this correctly
        # — porting the same call here. Also gives us the LR-warn cap +
        # anchor cap + juice trap on the recomputed pick.
        from defensive_gates import apply_all_defensive_gates
    except ImportError as e:
        print(f'  FAIL importing scorer: {e}'); return

    rows = fetch_ctx_window(start_date, days, lookback=lookback)
    print(f'  ctx rows in window: {len(rows)}')

    changed = 0; patched = 0
    for g in rows:
        old_pp = g.get('primary_play') or {}
        old_key = f"{old_pp.get('type')}/{old_pp.get('label')}/{old_pp.get('tier')}"
        try:
            decision = score_game('NFL', g)
        except Exception as e:
            print(f'  score failed {g.get("away_team")}@{g.get("home_team")}: {e}'); continue
        if decision is None: continue
        top = decision.top()
        if top.pick is None: continue

        new_pp = {
            'type': top.market, 'tier': top.tier, 'label': top.display_label,
            'side': top.side, 'line': top.line, 'conviction': top.conviction,
            'score': round(top.score, 2), 'sub': _compose_ensemble_sub(top),
            'audit_note': (f'ensemble_scorer v2 · NFL · recompute · {len(top.contributions)} sources · '
                           f'score={top.score:.2f} margin={top.margin:+.2f}'),
            '_engine': 'ensemble_v2',
            '_ensemble_sources': [
                {'signal_key': c.signal_key, 'class': c.signal_class,
                 'side': c.side, 'weight': round(c.weight, 2),
                 'n': c.n, 'contribution': round(c.contribution, 2),
                 'prose': c.display_prose}
                for c in top.contributions[:8]
            ],
        }
        # 2026-09-13: apply defensive gates (LR override, juice trap,
        # anchor, publish gate, and — critically — unconditional
        # _lr_ml_shadow / _lr_total_shadow backfill). Without this,
        # the fresh scorer output ships with no LR data, breaking
        # every downstream gate that reads primary_play._lr_ml_shadow
        # (LR-warn cap in nfl_ncaaf_signal_discipline, POTD gate,
        # Sharp Card LR conflict check, watchdogs). Sport='NFL'
        # is critical — default is MLB and silently no-ops on NFL.
        try:
            _rebuilt = apply_all_defensive_gates(new_pp, g, sport='NFL')
            if isinstance(_rebuilt, dict):
                new_pp = _rebuilt
        except Exception as _e:
            # Never break the recompute over a gate failure — same
            # convention as ncaaf recompute + MLB recompute.
            print(f'  ! defensive_gates raised: {_e} (continuing)')

        # 2026-09-13 second pass: preserve every shadow field written by
        # upstream steps before this recompute (nfl_ml_logreg_predict writes
        # _logreg_shadow; nfl_goat_composite writes _goat_shadow). Preserve
        # step runs AFTER defensive_gates because the LR-override PRIME path
        # builds a fresh new_pp and discards _goat_shadow / _logreg_shadow
        # even when we pre-populate them (verified with dry run: 6/12 → 4/12
        # coverage regression on games that hit the PRIME override). Explicit
        # copy-forward list beats "carry everything starting with _" because
        # some `_pre_*` fields would leak stale tier info onto a fresh pick.
        _SHADOW_FIELDS_TO_PRESERVE = ('_goat_shadow', '_logreg_shadow')
        if isinstance(old_pp, dict):
            for _shadow_key in _SHADOW_FIELDS_TO_PRESERVE:
                if _shadow_key in old_pp and _shadow_key not in new_pp:
                    new_pp[_shadow_key] = old_pp[_shadow_key]

        # 2026-09-17: side-label consistency normalizer. If a downstream
        # gate (defensive_gates, LR promotion, calibration flip) mutated
        # `side` but not `label`, the two disagree on which team we're
        # backing. Recompute label from side + close_spread every write.
        new_pp = _normalize_pp_side_label(new_pp, g)

        new_key = f"{new_pp['type']}/{new_pp['label']}/{new_pp['tier']}"
        # 2026-09-13: also patch when the visible pick hasn't changed
        # but the LR shadow was missing on old_pp — otherwise the
        # freshly-computed shadow gets discarded and downstream gates
        # keep no-op-ing. Cheap fix; PATCH per-game is fine.
        _shadow_was_missing = not isinstance(old_pp.get('_lr_ml_shadow'), dict)
        if new_key == old_key and not _shadow_was_missing:
            continue
        changed += 1

        marker = f'  {g.get("game_date")} {g.get("away_team","?"):5s} @ {g.get("home_team","?"):5s}  {old_key[:34]:34s} → {new_key[:34]}'
        print(marker)
        if dry_run: continue
        if patch_pp(g['game_id'], new_pp): patched += 1

    # 2026-09-11 PERMANENT FIX for Jerry vs primary_play badge drift on NFL.
    # Ports MLB's 2026-09-09 auto-align pattern (see recompute_primary_play.py:344).
    # Jerry cache key is per-NFL-week (Thu lock), so once Jerry writes on
    # Thursday, later primary_play recomputes leave jerry_reads.call_* pointing
    # at the stale pick. Games tab badge (from jerry_reads) then disagrees with
    # the primary_play chip (WAS@PHI, GB@MIN, MIA@LV — reported 2026-09-11).
    # backfill_jerry_pick_alignment applies enforce_primary_play_alignment()
    # against every jerry_read row in the window; diff-only PATCH so it's cheap.
    if not dry_run and patched > 0:
        try:
            import subprocess
            from pathlib import Path as _P
            print(f'\n  → auto-aligning jerry_reads via backfill_jerry_pick_alignment')
            _script = str(_P(__file__).parent / 'backfill_jerry_pick_alignment.py')
            # 2026-09-18 Andy directive "one source of truth per pick".
            # Propagate NFL_UNLOCK_WEEK=1 to the alignment subprocess.
            # Rule: if primary_play just changed (patched > 0), jerry MUST
            # re-align regardless of week-lock. Prior behavior: post-Thu
            # recompute changed pp but alignment sub-process hit the
            # week-lock guard in backfill_jerry_pick_alignment and silently
            # skipped, leaving jerry stale (badge divergence Andy caught on
            # Vikings/Bengals/Ravens 9/18). If recompute is running at all,
            # its output should propagate — no partial locks.
            _sub_env = {**os.environ, 'NFL_UNLOCK_WEEK': '1'}
            r = subprocess.run(
                [sys.executable, _script, '--sport', 'NFL'],
                capture_output=True, text=True, timeout=180, env=_sub_env)
            for line in ((r.stdout or '') + (r.stderr or '')).splitlines()[-10:]:
                print(f'    {line}')
            if r.returncode != 0:
                print(f'    ⚠ align exit {r.returncode}')
        except Exception as e:
            print(f'    ⚠ auto-align failed: {e} (jerry_reads may be out of sync)')

    prefix = '[DRY] ' if dry_run else ''
    print(f'\n{prefix}changed={changed}  patched={patched}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Anchor date (default: today ET)')
    ap.add_argument('--days', type=int, default=14, help='Forward window from anchor (default 14 = full NFL week)')
    ap.add_argument('--lookback', type=int, default=7,
                    help='Days BEHIND anchor to include (default 7). Catches games '
                         'from earlier in the current NFL week that were stamped '
                         'once at seed-time and never re-touched by a later '
                         'recompute pass — root cause of the NFL W1 LR-shadow gap.')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--labels-only', action='store_true',
                    help='Run ONLY the side/label normalizer, ignoring the '
                         'week lock. Never changes side/tier/market — only '
                         'makes label+line agree with the frozen side. Safe '
                         'to run at any point in the week, including kickoff.')
    args = ap.parse_args()
    if args.labels_only:
        failed = run_labels_only(args.date or _et_today(), args.days,
                                 args.dry_run, lookback=args.lookback)
        sys.exit(1 if failed else 0)
    run(args.date or _et_today(), args.days, args.dry_run, lookback=args.lookback)


if __name__ == '__main__':
    main()
