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


def run(date_str: str, dry_run: bool = False) -> None:
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
        try:
            from ensemble_scorer import score_game as _ensemble_score
            from game_context import _compose_ensemble_sub
            decision = _ensemble_score('MLB', ctx_merged)
            if decision is not None:
                top = decision.top()
                if top.pick is not None:
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
        except Exception:
            pass  # ensemble unavailable — fall back below

        if new_pp is None:
            try:
                new_pp = compute_primary_play(ctx_merged)
                if isinstance(new_pp, dict):
                    new_pp['_engine'] = 'legacy_compute_primary_play'
            except Exception as e:
                print(f'  ✗ {away} @ {home}: compute_primary_play failed — {e}')
                continue

        new_key = f"{(new_pp or {}).get('tier')}·{(new_pp or {}).get('label')}·{(new_pp or {}).get('type')}"
        pp_changed = old_key != new_key

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

    print(f'\n{"[DRY] " if dry_run else "✓ "}patched={patched}/{len(ctxs)}  '
          f'primary_play changed={changed_pp}  nrfi_ensemble changed={changed_ens}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(args.date or _today_et(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
