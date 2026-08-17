"""Seed signal_sources with UFC fight-market signal catalog (2026-08-16).

UFC operates on `ufc_picks` rows (one per fight), not a game_context
table. Signals read fight-level stats + fighter stats via ctx fields.

Candidate set: FIGHTER_A_ML / FIGHTER_B_ML (mapped in ensemble_scorer
_label_from_candidate).

Signal universe covers what a human handicapper considers:
  * model: pipeline's win-prob output, ev tier
  * striking: SLpM edge, striking accuracy edge, defense
  * grappling: TD accuracy, TD defense
  * cardio / experience: fights, decision rate (5-round conditioning)
  * juice trap: heavy-fav trap fade
  * external: handicappers (via handler)

Prop signals (method, distance, rounds) live separate — this is fight
market only.

CLI:
  python seed_ufc_signal_sources.py
  python seed_ufc_signal_sources.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

UFC_SIGNALS = [
    # ── MODEL CLASS ─────────────────────────────────────────────────
    {
        'signal_key': 'ufc_pipeline_pick_a',
        'class': 'model', 'market_scope': 'fight',
        'condition_expr': 'ctx.p_winner_a is not None and float(ctx.p_winner_a) >= 0.55',
        'side_expr': '"FIGHTER_A_ML"',
        'strength_expr': 'min((float(ctx.p_winner_a) - 0.5) * 2, 1.0)',
        'display_prose_template': 'model gives {fighter_a} the edge ({p_winner_a})',
        'description': 'Pipeline win-prob model favors fighter A.',
    },
    {
        'signal_key': 'ufc_pipeline_pick_b',
        'class': 'model', 'market_scope': 'fight',
        'condition_expr': 'ctx.p_winner_a is not None and float(ctx.p_winner_a) <= 0.45',
        'side_expr': '"FIGHTER_B_ML"',
        'strength_expr': 'min((0.5 - float(ctx.p_winner_a)) * 2, 1.0)',
        'display_prose_template': 'model gives {fighter_b} the edge',
    },
    {
        'signal_key': 'ufc_ev_prime',
        'class': 'model', 'market_scope': 'fight',
        'condition_expr': 'ctx.ev_tier == "PRIME" and ctx.recommended_side is not None',
        'side_expr': '"FIGHTER_A_ML" if str(ctx.recommended_side).lower() == "a" else "FIGHTER_B_ML"',
        'strength_expr': '0.8',
        'display_prose_template': 'EV tier PRIME on this side — model-vs-market gap is significant',
        'description': 'ufc_compute_ev PRIME tier signal.',
    },
    {
        'signal_key': 'ufc_ev_strong',
        'class': 'model', 'market_scope': 'fight',
        'condition_expr': 'ctx.ev_tier == "STRONG" and ctx.recommended_side is not None',
        'side_expr': '"FIGHTER_A_ML" if str(ctx.recommended_side).lower() == "a" else "FIGHTER_B_ML"',
        'strength_expr': '0.6',
        'display_prose_template': 'EV tier STRONG on this side',
    },

    # ── STRIKING CLASS ───────────────────────────────────────────────
    {
        'signal_key': 'ufc_striking_edge_a',
        'class': 'striking', 'market_scope': 'fight',
        'condition_expr': 'ctx.slpm_a is not None and ctx.slpm_b is not None and (float(ctx.slpm_a) - float(ctx.slpm_b)) >= 1.0',
        'side_expr': '"FIGHTER_A_ML"',
        'strength_expr': 'min((float(ctx.slpm_a) - float(ctx.slpm_b)) / 3.0, 1.0)',
        'display_prose_template': '{fighter_a} lands {slpm_a} strikes/min vs {slpm_b} for {fighter_b}',
    },
    {
        'signal_key': 'ufc_striking_edge_b',
        'class': 'striking', 'market_scope': 'fight',
        'condition_expr': 'ctx.slpm_a is not None and ctx.slpm_b is not None and (float(ctx.slpm_b) - float(ctx.slpm_a)) >= 1.0',
        'side_expr': '"FIGHTER_B_ML"',
        'strength_expr': 'min((float(ctx.slpm_b) - float(ctx.slpm_a)) / 3.0, 1.0)',
        'display_prose_template': '{fighter_b} lands {slpm_b} strikes/min vs {slpm_a} for {fighter_a}',
    },
    {
        'signal_key': 'ufc_str_def_a',
        'class': 'striking', 'market_scope': 'fight',
        'condition_expr': 'ctx.str_def_a is not None and float(ctx.str_def_a) >= 60',
        'side_expr': '"FIGHTER_A_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{fighter_a} has elite striking defense ({str_def_a}%)',
    },
    {
        'signal_key': 'ufc_str_def_b',
        'class': 'striking', 'market_scope': 'fight',
        'condition_expr': 'ctx.str_def_b is not None and float(ctx.str_def_b) >= 60',
        'side_expr': '"FIGHTER_B_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{fighter_b} has elite striking defense ({str_def_b}%)',
    },

    # ── GRAPPLING CLASS ──────────────────────────────────────────────
    {
        'signal_key': 'ufc_td_edge_a',
        'class': 'grappling', 'market_scope': 'fight',
        'condition_expr': 'ctx.td_acc_a is not None and ctx.td_def_b is not None and (float(ctx.td_acc_a) - float(ctx.td_def_b)) >= 20',
        'side_expr': '"FIGHTER_A_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{fighter_a} lands {td_acc_a}% takedowns vs {td_def_b}% TD defense — grappling mismatch',
    },
    {
        'signal_key': 'ufc_td_edge_b',
        'class': 'grappling', 'market_scope': 'fight',
        'condition_expr': 'ctx.td_acc_b is not None and ctx.td_def_a is not None and (float(ctx.td_acc_b) - float(ctx.td_def_a)) >= 20',
        'side_expr': '"FIGHTER_B_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{fighter_b} lands {td_acc_b}% takedowns vs {td_def_a}% TD defense — grappling mismatch',
    },

    # ── JUICE-TRAP CLASS (2026-08-16 audit): 90%+ win-prob favs 2-6 (33%) ──
    {
        'signal_key': 'ufc_heavy_juice_fade_a',
        'class': 'juice_trap', 'market_scope': 'fight',
        'condition_expr': 'ctx.odds_a_median is not None and float(ctx.odds_a_median) < 1.5',
        'side_expr': '"FIGHTER_B_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{fighter_a} priced at heavy juice — historical 33% hit at 90%+ win-prob favorites',
        'description': 'Morning-audit UFC finding: heavy-juice favs bleed. Fade to dog.',
    },
    {
        'signal_key': 'ufc_heavy_juice_fade_b',
        'class': 'juice_trap', 'market_scope': 'fight',
        'condition_expr': 'ctx.odds_b_median is not None and float(ctx.odds_b_median) < 1.5',
        'side_expr': '"FIGHTER_A_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{fighter_b} priced at heavy juice — historical 33% hit at 90%+ win-prob favorites',
    },

    # ── EXPERIENCE CLASS ────────────────────────────────────────────
    {
        'signal_key': 'ufc_5rd_conditioning_a',
        'class': 'cardio', 'market_scope': 'fight',
        'condition_expr': 'ctx.fight_order == 1 and ctx.total_fights_a is not None and int(ctx.total_fights_a) >= 15 and ctx.total_fights_b is not None and int(ctx.total_fights_b) < 10',
        'side_expr': '"FIGHTER_A_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{fighter_a} has 5-round experience ({total_fights_a} fights) vs {fighter_b} ({total_fights_b}) — cardio edge in main event',
    },

    # ── HANDLER-BASED (external + scenarios if UFC ever has them) ───
    {
        'signal_key': 'external_handicapper_pick_ufc',
        'class': 'external_pick', 'market_scope': 'fight',
        'condition_expr': '_HANDLER_EXTERNAL',
        'side_expr': '_HANDLER_EXTERNAL',
        'strength_expr': '_HANDLER_EXTERNAL',
        'display_prose_template': 'external analysts are on this fighter',
    },
]


def upsert(dry_run: bool = False):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in UFC_SIGNALS:
        row = {
            'signal_key': s['signal_key'], 'sport': 'UFC',
            'market_scope': s.get('market_scope', 'fight'),
            'class': s['class'],
            'condition_expr': s['condition_expr'],
            'side_expr': s['side_expr'],
            'strength_expr': s.get('strength_expr', '0.5'),
            'weight_registry_key': s.get('weight_registry_key'),
            'hit_rate_pct': s.get('hit_rate_pct'),
            'sample_n': s.get('sample_n'),
            'display_prose_template': s.get('display_prose_template'),
            'description': s.get('description'),
            'enabled': s.get('enabled', True),
            'origin': 'SEEDED_UFC',
            'updated_at': now_iso,
        }
        payloads.append(row)
    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]
    print(f'=== seeding UFC signal_sources · {len(normalized)} rows ===')
    for row in normalized: print(f'  {row["signal_key"]:<40} [{row["class"]:<12}] {row["market_scope"]}')
    if dry_run: print('\n[DRY-RUN] no writes'); return
    written = 0
    for i in range(0, len(normalized), 100):
        pr = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
                           headers=H_WRITE, json=normalized[i:i+100], timeout=15)
        if pr.status_code in (200, 201, 204): written += min(100, len(normalized) - i)
        else: print(f'  ✗ {pr.status_code}: {pr.text[:200]}')
    print(f'  ✓ wrote {written} UFC signals')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    upsert(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
