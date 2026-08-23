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

# 2026-08-10: sport-agnostic registry. Adding a sport = one dict entry.
# Each sport specifies its ctx table + a "total" market key + column names
# so the discipline gate works uniformly across MLB/NFL/NCAAF/etc.
SPORT_CONFIG = {
    'MLB': {
        'ctx_table': 'mlb_game_context',
        'total_col': 'close_total',
        'jerry_total_col': 'jerry_pred_total',
        'proj_total_col': 'projected_total',
    },
    'NFL': {
        'ctx_table': 'nfl_game_context',
        'total_col': 'close_total',
        'jerry_total_col': 'jerry_pred_total',
        'proj_total_col': 'projected_total',
    },
    'NCAAF': {
        'ctx_table': 'ncaaf_game_context',
        'total_col': 'close_total',
        'jerry_total_col': 'jerry_pred_total',
        'proj_total_col': 'projected_total',
    },
    # 2026-08-10: NCAAB + NHL added per user directive. Same discipline
    # gate applies — sharp $ on totals is fade material regardless of
    # sport, model consensus override same shape. Ctx tables + column
    # names identical convention across sports (established during
    # multi-sport migrations). Refit values may not exist yet for these
    # sports — gate will silently no-op on missing columns.
    'NCAAB': {
        'ctx_table': 'ncaab_game_context',
        'total_col': 'close_total',
        'jerry_total_col': 'jerry_pred_total',
        'proj_total_col': 'projected_total',
    },
    'NHL': {
        'ctx_table': 'nhl_game_context',
        'total_col': 'close_total',
        'jerry_total_col': 'jerry_pred_total',
        'proj_total_col': 'projected_total',
    },
    'NBA': {
        'ctx_table': 'nba_game_context',
        'total_col': 'close_total',
        'jerry_total_col': 'jerry_pred_total',
        'proj_total_col': 'projected_total',
    },
}

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


def evaluate(read: dict, ctx: dict, sport: str = 'MLB') -> tuple[str, str, dict] | None:
    """Return (rule_id, new_side, audit) if a violation is found, else None.

    Sport-agnostic — reads column names from SPORT_CONFIG so MLB/NFL/
    NCAAF all use the same discipline gate against their own ctx tables.
    """
    cfg = SPORT_CONFIG.get(sport, SPORT_CONFIG['MLB'])
    mkt = (read.get('call_market') or '').lower()
    if mkt != 'total':
        return None  # ML sharp is a POSITIVE signal (73% yesterday) — don't fade
    side = (read.get('call_side') or '').upper()
    if side not in ('OVER', 'UNDER'):
        return None
    line = ctx.get(cfg['total_col'])
    if line is None:
        return None
    j_tot = ctx.get(cfg['jerry_total_col'])
    v3_tot = ctx.get(cfg['proj_total_col'])
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
        'audit_notes': note,  # 2026-08-13: audit_notes not short_read
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
    """Update mlb_game_context.primary_play when Jerry gets flipped by the
    sharp-fade / model-consensus discipline, so the app-side sweat card
    badge stays coherent with the flipped Jerry call.

    2026-08-23 EXPANSION: was gated to only run when primary_play was set
    by Jerry-fallback ('jerry_read fallback' in audit_note). Now also
    fires when primary_play._engine=='ensemble_v2' — Rockies @ Guardians
    tonight showed the mismatch: ensemble picked Over 11 LEAN 66 (Coors
    park + Griffin first-inn), then Rule B flipped Jerry to UNDER 11
    based on model consensus (J=9.11, v3=10.5, MC=8.65 all under),
    leaving TWO opposing labels on the same game card. Now the flip
    also patches primary_play so the badges agree. Original tier is
    preserved in _flipped_by audit trail.
    """
    pp = ctx.get('primary_play') or {}
    if not isinstance(pp, dict) or not pp.get('audit_note'): return
    audit = (pp.get('audit_note') or '').lower()
    engine = str(pp.get('_engine') or '')
    # Two acceptable trigger paths:
    #   legacy jerry-fallback (original behavior)
    #   ensemble_v2 with matching market — new 2026-08-23 path
    from_jerry_fallback = 'jerry_read fallback' in audit
    from_ensemble = engine == 'ensemble_v2' and str(pp.get('type', '')).lower() == 'total'
    if not (from_jerry_fallback or from_ensemble): return
    line = ctx.get('close_total')
    new_label = f'{new_side.title()} {line}'
    if pp.get('label') == new_label: return
    pp_new = dict(pp)
    pp_new['label'] = new_label
    pp_new['side'] = new_side.upper()
    pp_new['sub'] = f'{pp_new.get("sub","")} · Flipped by sharp-fade / model-consensus discipline'[:250]
    pp_new['audit_note'] = (pp_new.get('audit_note','') +
                            f' · side flipped {pp.get("side")}->{new_side.upper()} by collapse_sharp_fade_violations')[:250]
    # Preserve original ensemble pick for audit trail
    pp_new['_flipped_by'] = {
        'orig_side': pp.get('side'),
        'orig_label': pp.get('label'),
        'orig_tier': pp.get('tier'),
        'source': 'collapse_sharp_fade_violations',
    }
    # Cap conviction so display doesn't show original PRIME/STRONG number
    # on a flipped side — mirrors the LEAN cap the Jerry read uses.
    if isinstance(pp_new.get('conviction'), (int, float)):
        pp_new['conviction'] = min(int(pp_new['conviction']), 55)
    if dry_run:
        print(f'    [DRY] primary_play label {pp.get("label")} -> {new_label}')
        return
    pr = requests.patch(f'{SB}/rest/v1/mlb_game_context?game_id=eq.{ctx["game_id"]}',
                        headers=H_WRITE, json={'primary_play': pp_new}, timeout=10)
    if pr.status_code not in (200, 204):
        print(f'    primary_play patch failed: {pr.status_code}')


def run(game_date: str, sport: str = 'MLB', dry_run: bool = False) -> int:
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        print(f'  [{sport}] no config registered — skip'); return 0
    ctx_table = cfg['ctx_table']

    reads = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': f'eq.{sport}', 'game_date': f'eq.{game_date}',
                'call_market': 'eq.total',
                'select': 'id,game_id,call_market,call_side,call_line,call_text,'
                          'conviction,short_read'},
        timeout=15).json()
    if not isinstance(reads, list):
        print(f'  fetch failed: {reads}'); return 0
    # 2026-08-10: fallback-safe select — sport ctx tables may not yet
    # have all columns (e.g. no oddscrowd_snapshot on NHL). Try full
    # select first, drop unknown columns on 400 error.
    select_cols = (f'game_id,away_team,home_team,{cfg["total_col"]},'
                   f'{cfg["jerry_total_col"]},{cfg["proj_total_col"]},'
                   f'mc_probabilities,oddscrowd_snapshot,primary_play')
    resp = requests.get(f'{SB}/rest/v1/{ctx_table}', headers=H_READ,
        params={'game_date': f'eq.{game_date}', 'select': select_cols},
        timeout=15)
    if resp.status_code != 200:
        # Retry with minimal columns — sport ctx table may be sparse
        resp = requests.get(f'{SB}/rest/v1/{ctx_table}', headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'select': 'game_id,away_team,home_team'},
            timeout=15)
        if resp.status_code != 200:
            print(f'  [{sport}] ctx fetch failed {resp.status_code} — skip')
            return 0
    ctxs = resp.json()
    if not isinstance(ctxs, list):
        print(f'  [{sport}] unexpected ctx shape — skip')
        return 0
    ctx_by = {c['game_id']: c for c in ctxs if isinstance(c, dict) and c.get('game_id')}

    flips = 0
    for read in reads:
        # Idempotent: skip if already flipped
        if 'Auto-flipped 2026-08-10' in (read.get('short_read') or ''):
            continue
        ctx = ctx_by.get(read['game_id'])
        if not ctx: continue
        result = evaluate(read, ctx, sport=sport)
        if not result: continue
        rule_id, new_side, audit = result
        matchup = f'{ctx.get("away_team","?")[:8]}@{ctx.get("home_team","?")[:8]}'
        print(f'  [{sport}] {matchup:20} {rule_id}: flip {read["call_side"]} -> {new_side}  '
              f'(conv {read.get("conviction")} -> {FLIP_CAP_CONV})')
        if apply_flip(read, ctx, rule_id, new_side, audit, dry_run=dry_run):
            sync_primary_play(read, ctx, new_side, dry_run=dry_run)
            flips += 1

    print(f'\n=== sharp-fade discipline · {sport}: {flips} flip(s)'
          f'{" (dry-run)" if dry_run else ""} ===')
    return flips


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport', default='ALL',
                   help='MLB / NFL / NCAAF / ALL (loops all registered)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = list(SPORT_CONFIG.keys()) if args.sport == 'ALL' else [args.sport]
    for s in sports:
        run(game_date=args.date or _et_today(), sport=s, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
