"""Re-run compute_primary_play + NRFI ensemble after enrich_monte_carlo lands.

Sequencing bug this fixes: game_context.py builds primary_play BEFORE
enrich_monte_carlo.py sets mc_high_conf_flag / mc_probabilities. So when
compute_primary_play checks `ctx.get('mc_high_conf_flag')` at build time,
it's always None — the MC HIGH-CONF headline block short-circuits false
and the scorer falls through to weaker tiers or emits nothing.

Same pattern we fixed for panel_implied on 7/25. Runs AFTER
enrich_monte_carlo.py in the workflow so the recomputed primary_play
sees every lens.

Usage:
    python recompute_primary_play.py                # today's slate
    python recompute_primary_play.py --date 2026-07-26
    python recompute_primary_play.py --date 2026-07-26 --dry-run

Required env: SUPABASE_URL, SUPABASE_KEY
"""
import argparse, os, requests, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# Load .env if present (harmless in GitHub Actions where env is already set)
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

from game_context import compute_primary_play
from enrich_monte_carlo import _compute_nrfi_ensemble


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def run(date_str: str, dry_run: bool = False, force: bool = False) -> None:
    print(f'=== recompute_primary_play · {date_str} ===')
    ctxs = requests.get(
        f'{SB}/rest/v1/mlb_game_context?game_date=eq.{date_str}&select=*',
        headers=H, timeout=15,
    ).json()
    res_rows = requests.get(
        f'{SB}/rest/v1/mlb_game_results?game_date=eq.{date_str}'
        f'&select=game_id,model_pred_spread,model_pred_total,projected_spread,projected_total',
        headers=H, timeout=15,
    ).json()
    res = {r['game_id']: r for r in res_rows if isinstance(r, dict)}

    if not ctxs or not isinstance(ctxs, list):
        print('  ⚠ no context rows found')
        return

    patched = 0
    changed_pp = 0
    changed_ens = 0
    engine_counts = {'ensemble_v2': 0, 'legacy_fallback': 0}
    fallback_reasons: dict[str, int] = {}
    for c in ctxs:
        gid = c.get('game_id')
        if not gid:
            continue
        away = c.get('away_team', '?')[:20]; home = c.get('home_team', '?')[:20]

        # Merge results-side model fields into ctx (compute_primary_play
        # reads model_pred_spread which lives on mlb_game_results)
        r_ = res.get(gid, {})
        ctx_merged = {**c, **{k: v for k, v in r_.items() if v is not None}}

        old_pp = c.get('primary_play') or {}
        old_key = f"{old_pp.get('tier')}·{old_pp.get('label')}·{old_pp.get('type')}"

        # 2026-08-17 cutover fix: try ensemble_scorer first (mirror of the
        # upload_game_context wrapper), fall back to legacy compute_primary_play
        # if ensemble returns None. Previously this script always called the
        # raw legacy path, silently CLOBBERING ensemble output from
        # upload_game_context. Every day since the 8/16 cutover, the
        # workflow ran game_context.py (writes ensemble output) then
        # recompute_primary_play.py (overwrites with legacy) — leaving
        # 0 rows with _engine='ensemble_v2'. Fix: same ensemble→fallback
        # pattern here.
        new_pp = None
        ensemble_error = None
        ensemble_no_pick_reason = None
        # 2026-08-19: previously wrapped the ensemble call in a bare
        # `except Exception: pass` — on 8/18, 7/15 games silently fell
        # back to legacy because of transient errors (registry cache warm,
        # PostgREST hiccup, etc). Rerunning the same rows the next day all
        # succeeded. Fix: retry once on exception, capture the reason so
        # the aggregate counter can surface it, don't hide silently.
        from ensemble_scorer import score_game as _ensemble_score
        from game_context import _compose_ensemble_sub
        import time as _time
        decision = None
        for attempt in (1, 2):
            try:
                decision = _ensemble_score('MLB', ctx_merged)
                break
            except Exception as e:
                ensemble_error = f'{type(e).__name__}: {e}'
                if attempt == 1:
                    _time.sleep(0.5)  # brief cooldown, then retry
                    continue
        if decision is not None:
            top = decision.top()
            if top.pick is None:
                ensemble_no_pick_reason = (
                    f'all markets returned _no_pick '
                    f'(ml={decision.ml.tier}, rl={decision.rl.tier}, total={decision.total.tier})'
                )
            else:
                new_pp = {
                    'type': top.market, 'tier': top.tier, 'label': top.display_label,
                    'side': top.side, 'line': top.line, 'conviction': top.conviction,
                    'score': round(top.score, 2), 'sub': _compose_ensemble_sub(top),
                    'audit_note': (f'ensemble_scorer v2 · recompute · {len(top.contributions)} sources · '
                                   f'score={top.score:.2f} margin={top.margin:+.2f}'),
                    '_engine': 'ensemble_v2',
                    '_ensemble_sources': [
                        {'signal_key': c.signal_key, 'class': c.signal_class,
                         'side': c.side, 'weight': round(c.weight, 2),
                         'n': c.n, 'contribution': round(c.contribution, 2),
                         'prose': c.display_prose}
                        for c in top.contributions[:8]
                    ],
                    '_ensemble_all_markets': {
                        'ml':    {'pick': decision.ml.pick, 'label': decision.ml.display_label,
                                  'tier': decision.ml.tier, 'conviction': decision.ml.conviction},
                        'rl':    {'pick': decision.rl.pick, 'label': decision.rl.display_label,
                                  'tier': decision.rl.tier, 'conviction': decision.rl.conviction},
                        'total': {'pick': decision.total.pick, 'label': decision.total.display_label,
                                  'tier': decision.total.tier, 'conviction': decision.total.conviction},
                    },
                }

        # If ensemble didn't produce a pick, log why (loud, not silent) so
        # slate-wide fallback patterns surface in the run log instead of
        # being invisible until someone checks the DB.
        if new_pp is None:
            _reason = ensemble_error or ensemble_no_pick_reason or 'ensemble returned None (hard_suppress?)'
            print(f'  ⚠️  ensemble fallback {ctx_merged.get("away_team","?")[:12]} @ {ctx_merged.get("home_team","?")[:12]}: {_reason}')
            # Bucket the reason so the summary shows which failure mode dominated
            _bucket = _reason.split(':')[0][:40] if ':' in _reason else _reason[:40]
            fallback_reasons[_bucket] = fallback_reasons.get(_bucket, 0) + 1
            engine_counts['legacy_fallback'] += 1
        else:
            engine_counts['ensemble_v2'] += 1

        if new_pp is None:
            try:
                new_pp = compute_primary_play(ctx_merged)
                if isinstance(new_pp, dict):
                    new_pp['_engine'] = 'legacy_compute_primary_play'
                    # 2026-08-19: legacy compute_primary_play emits label but
                    # often leaves `side` null (bug: side is embedded in the
                    # `type` field for totals, and derived from label for ml/rl).
                    # The app's Sharp Card reads pp.side to look up odds, so
                    # null side → null odds → no card display. Backfill side
                    # from the label + type here so downstream is stable.
                    if not new_pp.get('side'):
                        _mkt = str(new_pp.get('type') or '').lower()
                        _label = str(new_pp.get('label') or '').lower()
                        if _mkt in ('over', 'under'):
                            new_pp['side'] = _mkt.upper()
                            new_pp['type'] = 'total'
                        elif 'over' in _label:
                            new_pp['side'] = 'OVER'; new_pp['type'] = new_pp.get('type') or 'total'
                        elif 'under' in _label:
                            new_pp['side'] = 'UNDER'; new_pp['type'] = new_pp.get('type') or 'total'
                        else:
                            _home = (c.get('home_team') or '').lower()
                            _away = (c.get('away_team') or '').lower()
                            if _home and _home in _label:
                                new_pp['side'] = 'HOME'
                            elif _away and _away in _label:
                                new_pp['side'] = 'AWAY'
                            else:
                                # Last-word fallback (skip 'sox' — collides Bos/CWS)
                                _hk = _home.split()[-1] if _home else ''
                                _ak = _away.split()[-1] if _away else ''
                                if _hk and _hk != 'sox' and _hk in _label:
                                    new_pp['side'] = 'HOME'
                                elif _ak and _ak != 'sox' and _ak in _label:
                                    new_pp['side'] = 'AWAY'
            except Exception as e:
                print(f'  ✗ {away} @ {home}: compute_primary_play failed — {e}')
                continue

        # 2026-08-23: extracted to defensive_gates.py so game_context.py and
        # recompute_primary_play.py apply the identical MC-dissent + OC-flip
        # logic. Previously each path had its own inline block, and the
        # recompute path was missing MC-dissent entirely — surfaced tonight
        # when Orioles PRIME 86 (MC 40%) + Mariners PRIME 84 (MC 35%) went
        # through recompute and escaped demotion. Now a gate can never again
        # be on one path but not the other.
        from defensive_gates import apply_all_defensive_gates
        apply_all_defensive_gates(new_pp, c)

        new_key = f"{(new_pp or {}).get('tier')}·{(new_pp or {}).get('label')}·{(new_pp or {}).get('type')}"
        pp_changed = old_key != new_key

        # 2026-08-17: also patch when the ensemble engine tag needs updating.
        # Without this, unchanged picks keep their old un-tagged (or legacy-
        # tagged) primary_play, so telemetry can't tell what actually ran.
        old_engine = old_pp.get('_engine')
        new_engine = (new_pp or {}).get('_engine')
        engine_changed = old_engine != new_engine and new_pp is not None
        if engine_changed and not pp_changed:
            pp_changed = True  # trigger patch below to stamp the fresh engine
        if force and new_pp is not None:
            pp_changed = True  # user asked to force-write even if unchanged

        # NRFI ensemble uses mc_p_nrfi + sklearn nrfi_score
        mc = c.get('mc_probabilities') or {}
        mc_p_nrfi = mc.get('mc_p_nrfi') if isinstance(mc, dict) else None
        nrfi_score = c.get('nrfi_score')
        old_ens_tier = c.get('nrfi_ensemble_tier')
        old_ens_pick = c.get('nrfi_ensemble_pick')
        try:
            new_ens = _compute_nrfi_ensemble(mc_p_nrfi, nrfi_score) or {}
        except Exception:
            new_ens = {}
        ens_changed = (new_ens.get('nrfi_ensemble_tier') != old_ens_tier
                       or new_ens.get('nrfi_ensemble_pick') != old_ens_pick)

        if pp_changed: changed_pp += 1
        if ens_changed: changed_ens += 1

        marker = ''
        if pp_changed: marker += ' PP↻'
        if ens_changed: marker += ' NRFI↻'
        old_str = f"{old_pp.get('tier','—')}·{old_pp.get('label','—')}"[:34]
        new_str = f"{(new_pp or {}).get('tier','—')}·{(new_pp or {}).get('label','—')}"[:34]
        print(f'  {away:<20} @ {home:<20}  {old_str:<34} → {new_str:<34}{marker}')

        if dry_run:
            continue

        patch = {}
        if pp_changed:
            # 2026-08-28: REVERTED sub-blank logic. Ensemble regenerates
            # `sub` on every score_game() call to match the current pick
            # (verified 8/28 — ATL sub matches ATL label post-flip). My
            # previous fix was solving a race that doesn't exist for
            # ensemble-generated sub. Instead it caused every flipped
            # game to show "Analysis pending" on the app because I
            # blanked the fresh sub instead of the stale one.
            patch['primary_play'] = new_pp   # None is valid — clears stale play
            patch['primary_play_computed_at'] = datetime.now(timezone.utc).isoformat()
        if ens_changed and new_ens:
            patch['nrfi_ensemble_pick'] = new_ens.get('nrfi_ensemble_pick')
            patch['nrfi_ensemble_tier'] = new_ens.get('nrfi_ensemble_tier')
            patch['nrfi_ensemble_conf'] = new_ens.get('nrfi_ensemble_conf')
            patch['nrfi_ensemble_reason'] = new_ens.get('nrfi_ensemble_reason')
        if not patch:
            continue

        r = requests.patch(
            f'{SB}/rest/v1/mlb_game_context?game_id=eq.{gid}',
            headers=HW, json=patch, timeout=15,
        )
        if r.status_code < 300:
            patched += 1
        else:
            print(f'    ✗ patch failed {r.status_code}: {r.text[:150]}')

        # 2026-08-23: snapshot on EVERY publish (append-mode). Prior version
        # gated on pp_changed AND upserted (game_id, snapshot_source), which
        # collapsed to one row per source per game — killing the audit trail
        # this table exists to build. See migration
        # 20260823_primary_play_snapshots_append_mode.sql for schema change.
        # Fails soft if table missing.
        if new_pp:
            snap = {
                'sport': 'MLB',
                'game_date': date_str,
                'game_id': gid,
                'snapshot_source': 'recompute',
                'home_team': home,
                'away_team': away,
                'primary_play': new_pp,
                'pick_type': new_pp.get('type'),
                'pick_label': new_pp.get('label'),
                'pick_side': new_pp.get('side'),
                'pick_line': new_pp.get('line'),
                'tier': new_pp.get('tier'),
                'conviction': new_pp.get('conviction'),
                'score': new_pp.get('score'),
            }
            try:
                sr = requests.post(
                    f'{SB}/rest/v1/primary_play_snapshots',
                    headers={**HW, 'Prefer': 'return=minimal'},
                    json=snap, timeout=10,
                )
                # Silent unless failure; 404 = migration not applied yet
                if sr.status_code >= 400 and sr.status_code != 404:
                    print(f'    ⚠ snapshot write {sr.status_code}: {sr.text[:80]}')
            except Exception:
                pass  # snapshot is best-effort, don't break recompute if it fails

    print(f'\n{"[DRY] " if dry_run else "✓ "}patched={patched}/{len(ctxs)}  '
          f'primary_play changed={changed_pp}  nrfi_ensemble changed={changed_ens}')
    total = engine_counts['ensemble_v2'] + engine_counts['legacy_fallback']
    if total:
        pct = 100.0 * engine_counts['ensemble_v2'] / total
        print(f'  engine attribution: ensemble_v2={engine_counts["ensemble_v2"]}  '
              f'legacy_fallback={engine_counts["legacy_fallback"]}  ({pct:.0f}% ensemble)')
        if fallback_reasons:
            print('  fallback reasons:')
            for reason, cnt in sorted(fallback_reasons.items(), key=lambda x: -x[1]):
                print(f'    {cnt}x  {reason}')
        # Watchdog: if >30% of games fell back, that's a systemic issue worth
        # calling out visibly (the whole point of this exercise — 8/18 hit
        # 47% fallback silently because errors were swallowed).
        if engine_counts['legacy_fallback'] and pct < 70.0:
            print(f'  🚨 WATCHDOG: only {pct:.0f}% of games ran ensemble_v2 — '
                  f'expected ≥90%. Investigate fallback reasons above.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='force re-write even when tier/label/type unchanged '
                         '(needed after ensemble internals changed and only '
                         'ensemble_sources chip contributions shift)')
    args = ap.parse_args()
    run(args.date or _today_et(), dry_run=args.dry_run, force=args.force)


if __name__ == '__main__':
    main()
