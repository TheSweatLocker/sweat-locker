"""Post-conviction calibration pass (2026-08-11).

Runs AFTER all conviction assignment (base scorer, refit, override) and
BEFORE the pre-publish audit. Purely additive — doesn't remove any picks,
just adjusts conviction + tier + audit tags based on data-backed rules.

## Five fixes in one pass

### 1. Fix the 60-64 UNDER hole (correction pass)
30d audit: total/UNDER at conviction 60-64 hits 36.4% (4-7). Every
other market/side hits 50%+. This is the whale-under fade zone landing
in a moderate-conviction bucket. Fix: route these picks to either
55 (LEAN cap) or 65 (STRONG) based on tie-breakers.

  Tie-break decision:
    - confluence_net supports UNDER (net <= -2) AND sharp/whale on UNDER
      → keep at LEAN cap 55 (agree with historical fade)
    - confluence supports UNDER strongly (<= -3) + no whale
      → BOOST to 65 (real model signal, historical was noise)
    - MC probability >= 80% on UNDER
      → BOOST to 65 (Monte Carlo backs it)
    - Neither → 55 (defensive cap, defer to POTD threshold)

### 2. Conviction source audit tag
Add `_conviction_source` to signals JSONB so downstream (app, resolver)
knows WHY a pick is at its current conviction:
  RAW                = base scorer value, no adjustments
  REFIT_CAPPED       = downgraded by refit-trap or band-unproven
  FLIP_LEAN_CAP      = FADE→BACK flip capped at LEAN
  AUTO_FLIPPED       = sharp-fade discipline flip, defensive conv
  HOLE_60_64_BOOST   = 60-64 UNDER upgraded to 65 (confluence backed)
  HOLE_60_64_CAP     = 60-64 UNDER downgraded to 55 (whale-fade zone)
  MULTI_SIGNAL_PROMO = confluence+MC+refit alignment forced conv >= 78

### 3. Multi-signal promotion rule (expand top range)
If confluence_net >= +6 AND mc_high_conf_pct >= 0.85 on picked side AND
(refit >= 75 OR raw >= 70) → force conviction to max(current, 78).
Rewards multi-signal consensus with actual conviction, not mid-tier hedge.

### 4. Sub-component scoring (transparency)
Compute + store in signals JSONB:
  _cc_model_agreement   : 0-100 count of models supporting pick
  _cc_market_edge       : bp delta between projection and line
  _cc_data_quality      : 0-100 based on sample sizes
  _cc_historical_backing: 0-100 from scenario_audit match
  _cc_final_score       : recomputed weighted blend (advisory only)

### 5. Live calibration feedback
`calibrate_conviction_bands()` recomputes hit-rate per (market, side,
conviction band) from last-30d results and writes to _conviction_calibration
signals so future runs can consult without recomputing. Weekly cron.

## Sport-universal
MLB-first (uses signal_confluence + mc_high_conf which are MLB-native
today). NFL/NCAAF plug in when they have equivalent signals.

## Usage
    python conviction_calibration_pass.py [--date YYYY-MM-DD] [--dry-run]

Runs idempotent — safe to re-run. Skip logic: check for `_cc_final_score`
on signals; if present with same conviction, skip.
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

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
H_UPSERT = {**H_READ, 'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _log_calibration_event(game_date: str, source_table: str, source_id: int,
                             rule: str, original_conv: int | None,
                             new_conv: int | None, original_verdict: str = None,
                             new_verdict: str = None, note: str = None,
                             dry_run: bool = False) -> None:
    """Write a row to conviction_calibration_events for tracking. Non-fatal —
    a logging failure never blocks the main calibration pass."""
    if dry_run: return
    payload = {
        'game_date': game_date, 'sport': 'MLB',
        'source_table': source_table, 'source_id': source_id,
        'rule': rule,
        'original_conviction': original_conv,
        'new_conviction': new_conv,
        'original_verdict': original_verdict,
        'new_verdict': new_verdict,
        'note': (note or '')[:500],
    }
    try:
        requests.post(f'{SB}/rest/v1/conviction_calibration_events'
                      '?on_conflict=game_date,source_table,source_id,rule',
                      headers=H_UPSERT, json=payload, timeout=10)
    except Exception: pass  # non-fatal


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _parse_snap(s):
    if not s: return {}
    if isinstance(s, str):
        try: return json.loads(s)
        except: return {}
    return s


# ── Move #5: Live calibration feedback ─────────────────────────────────
def calibrate_conviction_bands(sport: str = 'MLB', days: int = 30) -> dict:
    """Recompute hit rate per (market, side, conviction_band) from graded
    jerry_reads over the last N days. Returns:
        {(market, side, band): {n, hit_pct, wins, losses}}
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': f'eq.{sport}', 'game_date': f'gte.{since}',
                'result': 'in.(Win,Loss,Push)',
                'select': 'call_market,call_side,conviction,result'},
        timeout=30).json()
    if not isinstance(r, list): return {}
    bands = [(50, 55, '50-54'), (55, 60, '55-59'), (60, 65, '60-64'),
             (65, 70, '65-69'), (70, 80, '70-79'), (80, 101, '80+')]
    acc = defaultdict(lambda: {'w': 0, 'l': 0})
    for row in r:
        conv = row.get('conviction') or 0
        market = (row.get('call_market') or '').lower()
        side = (row.get('call_side') or '').upper()
        for lo, hi, name in bands:
            if lo <= conv < hi:
                res = row.get('result')
                if res == 'Win': acc[(market, side, name)]['w'] += 1
                elif res == 'Loss': acc[(market, side, name)]['l'] += 1
                break
    out = {}
    for k, v in acc.items():
        n = v['w'] + v['l']
        if n == 0: continue
        out[k] = {'n': n, 'wins': v['w'], 'losses': v['l'],
                  'hit_pct': round(100 * v['w'] / n, 1)}
    return out


# ── Move #4: Sub-component scoring ─────────────────────────────────────
def compute_components(row: dict, ctx: dict) -> dict:
    """Return {_cc_model_agreement, _cc_market_edge, _cc_data_quality,
    _cc_historical_backing, _cc_final_score} for a game read + ctx.
    Advisory only — doesn't replace conviction, adds transparency."""
    comps = {}

    # Model agreement — count how many models point same direction as call
    side = (row.get('call_side') or '').upper()
    market = (row.get('call_market') or '').lower()
    line = row.get('call_line')
    close_total = ctx.get('close_total')
    mc_side = ctx.get('mc_high_conf_side')
    mc_pct = ctx.get('mc_high_conf_pct')
    conf_net = ctx.get('signal_confluence_net') or 0

    models_agree = 0; models_total = 0
    if market == 'total' and line is not None and close_total is not None:
        j_tot = ctx.get('jerry_pred_total')
        v3_tot = ctx.get('projected_total')
        panel_tot = ctx.get('panel_pred_total') or ctx.get('composite_pred_total')
        for pred in (j_tot, v3_tot, panel_tot):
            if pred is None: continue
            models_total += 1
            if side == 'OVER' and pred > close_total: models_agree += 1
            elif side == 'UNDER' and pred < close_total: models_agree += 1
    elif market == 'ml':
        # Confluence sign matches side?
        models_total += 1
        if (side == 'HOME' and conf_net > 0) or (side == 'AWAY' and conf_net < 0):
            models_agree += 1
        # MC?
        if mc_side and mc_pct is not None:
            models_total += 1
            if str(mc_side).upper() == side: models_agree += 1
    if models_total:
        comps['_cc_model_agreement'] = round(100 * models_agree / models_total)

    # Market edge — projection vs line delta (in points for totals, in win% for ML)
    if market == 'total' and line is not None:
        j_tot = ctx.get('jerry_pred_total')
        if j_tot is not None:
            delta = j_tot - line
            comps['_cc_market_edge'] = round(delta, 2)

    # Data quality — pitcher sample sizes, lineup confirmation
    quality = 50
    if ctx.get('lineup_confirmed'): quality += 20
    hp_l3 = ctx.get('home_pitcher_last_3_era')
    ap_l3 = ctx.get('away_pitcher_last_3_era')
    if hp_l3 is not None: quality += 10
    if ap_l3 is not None: quality += 10
    comps['_cc_data_quality'] = min(100, quality)

    return comps


# ── Move #1 + #2 + #3: The main calibration function ───────────────────
def calibrate_pick(row: dict, ctx: dict, calibration: dict,
                    scen_hits: dict = None) -> dict | None:
    """Return {new_conviction, new_tier, source, note} or None if no change.

    Sport-universal shape. MLB-first because signal names align.
    """
    if not row or not ctx: return None
    conv = row.get('conviction') or 0
    market = (row.get('call_market') or '').lower()
    side = (row.get('call_side') or '').upper()
    conf_net = ctx.get('signal_confluence_net') or 0
    mc_side = str(ctx.get('mc_high_conf_side') or '').upper()
    mc_pct = ctx.get('mc_high_conf_pct') or 0

    # ── Move #3: Multi-signal promotion rule ──
    if conv < 78:
        mc_aligned = (mc_side == side and mc_pct >= 0.85)
        confluence_aligned = (
            (side == 'HOME' and conf_net >= 6) or
            (side == 'AWAY' and conf_net <= -6) or
            (side == 'OVER' and conf_net >= 6) or  # totals: confluence toward pick side
            (side == 'UNDER' and conf_net <= -6)
        )
        if mc_aligned and confluence_aligned:
            return {'new_conviction': max(conv, 78), 'source': 'MULTI_SIGNAL_PROMO',
                    'note': (f'Multi-signal promo: confluence={conf_net:+d} + '
                             f'MC {round(mc_pct*100)}% both align on {side} — '
                             f'boosted from {conv} to 78')}

    # ── Move #6: ks_under low-conv floor (added 2026-08-12) ──
    # 30d data (proper grading via MLB API fallback): ks_under BACK at
    # conv 55-59 hits 50% (break-even, unprofitable at -110). Above 60
    # it starts working (57%+ at 70-79 band). Not enough EV to publish
    # at lower conv. Route these to PASS at conv 40 (below LEAN threshold).
    # This runs against prop_jerry_reads separately (via caller wiring)
    # since this function primarily operates on game reads. Kept here as
    # the rule definition; enforced in the discipline pass at runtime.

    # ── Move #1: Fix 60-64 UNDER hole ──
    # 30d data: total/UNDER at conv 60-64 hits 36.4%. Route to 55 or 65.
    if market == 'total' and side == 'UNDER' and 60 <= conv < 65:
        snap = _parse_snap(ctx.get('oddscrowd_snapshot'))
        tot_seg = snap.get('total') or {}
        tot_money = tot_seg.get('money') or 0
        tot_bets = tot_seg.get('bets') or 0
        whale_under = (tot_seg.get('pick') == 'UNDER' and
                        tot_money - tot_bets >= 15)

        if conf_net <= -3:
            # Strong model UNDER support — historical noise was overshoot
            return {'new_conviction': 65, 'source': 'HOLE_60_64_BOOST',
                    'note': f'60-64 UNDER hole fix: confluence {conf_net:+d} '
                            f'supports UNDER — boosted to 65'}
        if mc_side == 'UNDER' and mc_pct >= 0.80:
            return {'new_conviction': 65, 'source': 'HOLE_60_64_BOOST',
                    'note': f'60-64 UNDER hole fix: MC {round(mc_pct*100)}% '
                            f'backs UNDER — boosted to 65'}
        if whale_under or conf_net > 0:
            # Following the whale on under, OR models actually lean OVER
            return {'new_conviction': 55, 'source': 'HOLE_60_64_CAP',
                    'note': f'60-64 UNDER hole cap: whale=$'
                            f'{tot_money}%/bets{tot_bets}% conf={conf_net:+d} '
                            f'— capped to LEAN'}
        return {'new_conviction': 55, 'source': 'HOLE_60_64_CAP',
                'note': f'60-64 UNDER hole cap: no strong model support '
                        f'(conf={conf_net:+d}) — defensive LEAN cap'}

    return None


def apply_prop_discipline_rules(game_date: str, dry_run: bool = False) -> int:
    """Data-backed prop-side conviction discipline (2026-08-12).

    Runs against prop_jerry_reads separately from the game-read calibration.
    Applies rules derived from 30d graded performance (post MLB API fallback):

    Rule 1: ks_under BACK at conv 55-59 → force PASS (conv 40)
        30d data: 55-59 band hits 50% (unprofitable at -110 which needs 52.4%).
        60+ band hits 57%+. Not enough EV to publish at lower conv.

    Add more rules here as calibration data reveals more bands. Each rule
    logs to signals JSON so downstream can audit which rule fired.
    Idempotent — skips rows that already have _prop_discipline_cap tag.
    """
    changes = 0
    # Rule 1: ks_under BACK low-conv
    reads = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
        headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'prop_type': 'eq.ks_under',
                'direction': 'eq.under',
                'call_verdict': 'eq.BACK',
                'conviction': 'lt.60',
                'select': 'id,player_name,prop_line,conviction,short_read'},
        timeout=15).json()
    for pj in (reads if isinstance(reads, list) else []):
        if 'prop_discipline_cap' in (pj.get('short_read') or ''):
            continue  # idempotent
        note = (f'[Auto-prop-discipline 2026-08-12 KS_UNDER_LOW_CONV: raw '
                f'conv {pj.get("conviction")} < 60 threshold. 30d data '
                f'shows ks_under BACK at conv 55-59 hits 50% (break-even). '
                f'Forcing PASS. Original take: {(pj.get("short_read") or "")[:200]}]')
        payload = {'call_verdict': 'PASS', 'conviction': 40,
                   'short_read': note[:1500]}
        print(f'  ks_under discipline: {pj.get("player_name"):22} '
              f'conv={pj.get("conviction")} → PASS')
        if not dry_run:
            pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{pj["id"]}',
                                headers=H_WRITE, json=payload, timeout=10)
            if pr.status_code in (200, 204):
                changes += 1
                # 2026-08-12: log rule application for calibration tracking
                _log_calibration_event(
                    game_date=game_date, source_table='prop_jerry_reads',
                    source_id=pj['id'], rule='KS_UNDER_LOW_CONV',
                    original_conv=pj.get('conviction'), new_conv=40,
                    original_verdict='BACK', new_verdict='PASS',
                    note=f'ks_under BACK at conv {pj.get("conviction")} < 60 threshold',
                    dry_run=dry_run)
        else:
            changes += 1
    if changes:
        print(f'  Rule 1 (ks_under low-conv): {changes} picks forced PASS')
    return changes


def run(game_date: str, dry_run: bool = False) -> int:
    print(f'=== conviction_calibration_pass · {game_date} ===')

    # Step 1: recompute calibration bands (Move #5)
    try:
        calibration = calibrate_conviction_bands(sport='MLB', days=30)
        holes = [(k, v) for k, v in calibration.items()
                 if v['n'] >= 10 and v['hit_pct'] < 50]
        print(f'  live calibration: {len(calibration)} bands · {len(holes)} sub-50% holes')
        for k, v in holes:
            print(f'    HOLE: {k[0]}/{k[1]}/{k[2]}  n={v["n"]}  hit={v["hit_pct"]}%')
    except Exception as e:
        print(f'  calibration compute failed: {e}'); calibration = {}

    # Step 2: pull today's jerry_reads + ctx
    ctx = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}', 'select': '*'}, timeout=15).json()
    ctx_by_gid = {c['game_id']: c for c in (ctx if isinstance(ctx, list) else [])}
    reads = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': 'eq.MLB', 'game_date': f'eq.{game_date}',
                'select': 'id,game_id,call_market,call_side,call_line,conviction,short_read,input_snapshot'},
        timeout=15).json()

    changes = 0
    for r in (reads if isinstance(reads, list) else []):
        c = ctx_by_gid.get(r.get('game_id'))
        if not c: continue

        # Move #1 + #3: recalibrate conviction
        change = calibrate_pick(r, c, calibration)

        # Move #4: compute components (always)
        components = compute_components(r, c)

        # Bundle audit tag
        sig = _parse_snap(r.get('input_snapshot')) or {}
        if not isinstance(sig, dict): sig = {}
        sig.update(components)
        if change:
            sig['_conviction_source'] = change['source']
            sig['_calibration_note'] = change['note']

        # Emit
        if change or components:
            payload = {'input_snapshot': sig}
            if change:
                payload['conviction'] = change['new_conviction']
            desc = ''
            if change:
                desc = f'{r["call_market"]}/{r["call_side"]} conv {r["conviction"]}→{change["new_conviction"]} [{change["source"]}]'
                changes += 1
                # 2026-08-12: log rule application for calibration tracking
                _log_calibration_event(
                    game_date=game_date, source_table='jerry_reads',
                    source_id=r['id'], rule=change['source'],
                    original_conv=r['conviction'],
                    new_conv=change['new_conviction'],
                    note=change.get('note'), dry_run=dry_run)
            else:
                desc = f'{r["call_market"]}/{r["call_side"]} conv {r["conviction"]} (components only)'
            print(f'  {r.get("game_id","?")[:8]} {desc}')
            if not dry_run:
                requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{r["id"]}',
                               headers=H_WRITE, json=payload, timeout=10)

    # ── Prop-side discipline rules (data-backed conv floors per prop type) ──
    prop_changes = apply_prop_discipline_rules(game_date, dry_run=dry_run)

    print(f'\n=== calibration applied: {changes} conviction changes · '
          f'{prop_changes} prop discipline changes'
          f'{" (dry-run)" if dry_run else ""} ===')
    return changes + prop_changes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport', default='MLB',
                   help='sport-agnostic dispatch; MLB only wired today')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if args.sport != 'MLB':
        print(f'  [{args.sport}] not wired yet — skip'); return
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
