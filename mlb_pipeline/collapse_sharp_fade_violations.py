"""Sharp-fade discipline enforcement (2026-08-10).

Codifies the sharp-fade rules that were documented but never wired into
Jerry synthesis. Runs AFTER generate_jerry_synthesis.py and the two
existing collapse scripts (opposite-direction and pitcher-thesis).

## The rules (from your calibration memory)

**Rule A: Sharp-fade on totals.** Per `feedback_sharp_money_fade_808` +
`project_sharp_money_fade_808`: sharp $ on TOTALS is fade material. 42%
lifetime hit, 33% yesterday. If:

  1. Jerry pick market == 'total'
  2. Sharp $ (oddscrowd_snapshot.total.money) >= 65% on one side
  3. Jerry's pick side == sharp side
  4. 2+ of (jerry_pred_total, projected_total, MC.mc_mean_total)
     sit on the OPPOSITE side of sharp with gap >= 0.3

→ FLIP Jerry's pick to opposite side, cap conviction at 55 (LEAN),
  audit note explains the flip. Also update mlb_game_context.primary_play
  when it inherited from this Jerry fallback so app-side badge matches.

**Rule B: Model-consensus override.** If ALL THREE models (Jerry, v3, MC)
agree on one side of a total with gap >= 0.5 AND Jerry's pick is the
OPPOSITE side, that's Jerry fighting his own math. Flip to the model
consensus side, cap at LEAN, audit note.

Rule B catches cases like today's COL @ ARI where Jerry picked UNDER 8.5
while J=9.1, v3=9.0, MC=9.1 all say OVER (plus sharp 79% OVER).

## Sample size guard

Only apply Rule A when sharp cohort has enough n. Skip if sharp fade
calibration is still in LOG mode (won't have enough sample yet).

## Idempotent

Safe to re-run. Skips reads that already have 'sharp-fade override' in
audit_note. Won't double-flip.

CLI:
    python collapse_sharp_fade_violations.py [--date YYYY-MM-DD] [--dry-run]
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

# Thresholds (calibrated to feedback_sharp_money_discipline_802 +
# feedback_sharp_money_fade_808).
SHARP_HEAVY_PCT     = 65      # Sharp $ >= 65% counts as "heavy" position
MODEL_DISAGREE_GAP  = 0.3     # A model "disagrees" with sharp if projection
                              # sits >=0.3 runs opposite the sharp side of the line
MODEL_CONSENSUS_GAP = 0.5     # For Rule B, all 3 models must gap >=0.5
FLIP_CAP_CONV       = 55      # Post-flip conviction cap (LEAN band)


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _parse_snap(s):
    if not s: return {}
    if isinstance(s, str):
        try: return json.loads(s)
        except: return {}
    return s


def _model_side(pred, line, gap=MODEL_DISAGREE_GAP):
    """Return 'OVER' / 'UNDER' / 'FLAT' for a total projection vs line."""
    if pred is None or line is None: return 'FLAT'
    try:
        if pred > line + gap: return 'OVER'
        if pred < line - gap: return 'UNDER'
    except (TypeError, ValueError):
        pass
    return 'FLAT'


def evaluate(read: dict, ctx: dict) -> tuple[str, str, dict] | None:
    """Return (rule_id, new_side, audit) if a violation is found, else None."""
    mkt = (read.get('call_market') or '').lower()
    if mkt != 'total':
        return None  # ML sharp is a POSITIVE signal (73% yesterday) — don't fade
    side = (read.get('call_side') or '').upper()
    if side not in ('OVER', 'UNDER'):
        return None
    line = ctx.get('close_total')
    if line is None:
        return None
    j_tot = ctx.get('jerry_pred_total')
    v3_tot = ctx.get('projected_total')
    mc_tot = (ctx.get('mc_probabilities') or {}).get('mc_mean_total')
    j_side = _model_side(j_tot, line)
    v3_side = _model_side(v3_tot, line)
    mc_side = _model_side(mc_tot, line)
    other = 'UNDER' if side == 'OVER' else 'OVER'

    # --- Rule A: sharp-fade on total ---
    snap = _parse_snap(ctx.get('oddscrowd_snapshot'))
    seg = snap.get('total') or {}
    sharp_side = (seg.get('pick') or '').upper()
    sharp_money = seg.get('money') or 0
    if sharp_side == side and sharp_money >= SHARP_HEAVY_PCT:
        # Count models on the FADE side (opposite sharp)
        against = sum(1 for s in (j_side, v3_side, mc_side) if s == other)
        if against >= 2:
            return ('SHARP_FADE_TOTAL', other, {
                'sharp_side': sharp_side, 'sharp_money': sharp_money,
                'models_against_sharp': against,
                'j': j_tot, 'v3': v3_tot, 'mc': mc_tot, 'line': line,
            })

    # --- Rule B: model-consensus override ---
    against_pick = sum(1 for s in (j_side, v3_side, mc_side) if s == other)
    if against_pick == 3:
        # All 3 models on opposite of Jerry pick. Verify gap is meaningful (>=0.5).
        strong = True
        for pred, actual_side in ((j_tot, j_side), (v3_tot, v3_side), (mc_tot, mc_side)):
            if pred is None:
                strong = False; break
            gap = abs(float(pred) - float(line))
            if gap < MODEL_CONSENSUS_GAP:
                strong = False; break
        if strong:
            return ('MODEL_CONSENSUS_OVERRIDE', other, {
                'j': j_tot, 'v3': v3_tot, 'mc': mc_tot, 'line': line,
            })
    return None


def apply_flip(read: dict, ctx: dict, rule_id: str, new_side: str,
                audit: dict, dry_run: bool = False) -> bool:
    line = ctx.get('close_total')
    new_call_text = f'{new_side.title()} {line}'
    orig_conv = read.get('conviction') or 0
    new_conv = min(FLIP_CAP_CONV, max(50, orig_conv))
    note = ''
    if rule_id == 'SHARP_FADE_TOTAL':
        note = (f'[Auto-flipped 2026-08-10 sharp-fade discipline: '
                f'sharp ${audit["sharp_money"]}% on {audit["sharp_side"]} '
                f'but {audit["models_against_sharp"]} models sit on {new_side} '
                f'(J={audit["j"]}, v3={audit["v3"]}, MC={audit["mc"]} vs line {line}). '
                f'Sharp on totals hits ~42% lifetime — flipped to {new_side} at LEAN. '
                f'Original take: {(read.get("short_read") or "")[:250]}]')[:500]
    else:  # MODEL_CONSENSUS_OVERRIDE
        note = (f'[Auto-flipped 2026-08-10 model-consensus override: '
                f'all 3 models on {new_side} vs Jerry pick '
                f'(J={audit["j"]}, v3={audit["v3"]}, MC={audit["mc"]} vs line {line}). '
                f'Jerry fighting own math — flipped to consensus side at LEAN. '
                f'Original take: {(read.get("short_read") or "")[:250]}]')[:500]
    payload = {
        'call_side': new_side,
        'call_text': new_call_text,
        'conviction': new_conv,
        'short_read': note,
    }
    if dry_run:
        print(f'    [DRY] {rule_id}: {read["call_text"]}({orig_conv}) -> {new_call_text}({new_conv})')
        return True
    pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{read["id"]}',
                        headers=H_WRITE, json=payload, timeout=10)
    if pr.status_code not in (200, 204):
        print(f'    patch failed: {pr.status_code} {pr.text[:120]}')
        return False
    return True


def sync_primary_play(read: dict, ctx: dict, new_side: str, dry_run: bool = False) -> None:
    """Update mlb_game_context.primary_play IF it was set from Jerry fallback
    with the OLD side. This keeps the app-side sweat card badge coherent
    with the flipped Jerry read.
    """
    pp = ctx.get('primary_play') or {}
    if not isinstance(pp, dict) or not pp.get('audit_note'): return
    if 'jerry_read fallback' not in (pp.get('audit_note') or '').lower(): return
    line = ctx.get('close_total')
    new_label = f'{new_side.title()} {line}'
    if pp.get('label') == new_label: return
    pp_new = dict(pp)
    pp_new['label'] = new_label
    pp_new['sub'] = f'{pp_new.get("sub","")} · Jerry flipped by sharp-fade discipline'[:250]
    pp_new['audit_note'] = (pp_new.get('audit_note','') +
                            ' · label updated after 2026-08-10 sharp-fade flip')[:250]
    if dry_run:
        print(f'    [DRY] primary_play label {pp.get("label")} -> {new_label}')
        return
    pr = requests.patch(f'{SB}/rest/v1/mlb_game_context?game_id=eq.{ctx["game_id"]}',
                        headers=H_WRITE, json={'primary_play': pp_new}, timeout=10)
    if pr.status_code not in (200, 204):
        print(f'    primary_play patch failed: {pr.status_code}')


def run(game_date: str, dry_run: bool = False) -> int:
    reads = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': 'eq.MLB', 'game_date': f'eq.{game_date}',
                'call_market': 'eq.total',
                'select': 'id,game_id,call_market,call_side,call_line,call_text,'
                          'conviction,short_read'},
        timeout=15).json()
    if not isinstance(reads, list):
        print(f'  fetch failed: {reads}'); return 0
    ctxs = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,away_team,home_team,close_total,jerry_pred_total,'
                          'projected_total,mc_probabilities,oddscrowd_snapshot,primary_play'},
        timeout=15).json()
    ctx_by = {c['game_id']: c for c in ctxs}

    flips = 0
    for read in reads:
        # Idempotent: skip if already flipped
        if 'Auto-flipped 2026-08-10' in (read.get('short_read') or ''):
            continue
        ctx = ctx_by.get(read['game_id'])
        if not ctx: continue
        result = evaluate(read, ctx)
        if not result: continue
        rule_id, new_side, audit = result
        matchup = f'{ctx.get("away_team","?")[:8]}@{ctx.get("home_team","?")[:8]}'
        print(f'  {matchup:20} {rule_id}: flip {read["call_side"]} -> {new_side}  '
              f'(conv {read.get("conviction")} -> {FLIP_CAP_CONV})')
        if apply_flip(read, ctx, rule_id, new_side, audit, dry_run=dry_run):
            sync_primary_play(read, ctx, new_side, dry_run=dry_run)
            flips += 1

    print(f'\n=== sharp-fade discipline: {flips} flip(s) applied'
          f'{" (dry-run)" if dry_run else ""} ===')
    return flips


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
