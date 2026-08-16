"""Seed signal_sources with NFL signal catalog (2026-08-16).

NFL data model is thinner than MLB (60 columns vs 249) — season hasn't
fully baked in years of stats yet. Start with ~20 core signals covering:
  * model      — Panel, V4, V3, MC (spread + total)
  * situational — rest edge, division game, primetime
  * weather    — wind, temp, precipitation, roof/dome
  * cohort     — signal_confluence net (home/away)
  * split      — public split classification (handler)
  * scenario   — sharp scenario matches (handler)
  * external   — handicapper picks (handler)

Every signal grows automatically as the season provides more data —
backfill_signal_tiers.py --sport NFL --days 30 will auto-tier them.

CLI:
  python seed_nfl_signal_sources.py            # upsert all NFL signals
  python seed_nfl_signal_sources.py --dry-run
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

NFL_SIGNALS = [
    # ── MODEL CLASS ──────────────────────────────────────────────────
    {
        'signal_key': 'panel_pred_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.panel_pred_total is not None and ctx.close_total is not None and abs(float(ctx.panel_pred_total) - float(ctx.close_total)) >= 1.0',
        'side_expr': '"OVER" if float(ctx.panel_pred_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.panel_pred_total) - float(ctx.close_total)) / 5.0, 1.0)',
        'display_prose_template': 'external panel projects {panel_pred_total} vs market {close_total}',
    },
    {
        'signal_key': 'panel_pred_spread',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.panel_pred_home_pts is not None and ctx.panel_pred_away_pts is not None and abs(float(ctx.panel_pred_home_pts) - float(ctx.panel_pred_away_pts)) >= 3.0',
        'side_expr': '"HOME_ML" if float(ctx.panel_pred_home_pts) > float(ctx.panel_pred_away_pts) else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.panel_pred_home_pts) - float(ctx.panel_pred_away_pts)) / 10.0, 1.0)',
        'display_prose_template': 'external panel sees {panel_pred_home_pts}-{panel_pred_away_pts}',
    },
    {
        'signal_key': 'v4_spread_nfl',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.v4_spread is not None and abs(float(ctx.v4_spread)) >= 3.0',
        'side_expr': '"HOME_ML" if float(ctx.v4_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.v4_spread)) / 10.0, 1.0)',
        'display_prose_template': 'V4 sees {v4_spread}-point edge',
    },
    {
        'signal_key': 'v4_total_nfl',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.v4_total is not None and ctx.close_total is not None and abs(float(ctx.v4_total) - float(ctx.close_total)) >= 1.5',
        'side_expr': '"OVER" if float(ctx.v4_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.v4_total) - float(ctx.close_total)) / 5.0, 1.0)',
        'display_prose_template': 'V4 sees {v4_total} points vs market {close_total}',
    },
    {
        'signal_key': 'v3_spread_nfl',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.v3_spread is not None and abs(float(ctx.v3_spread)) >= 3.0',
        'side_expr': '"HOME_ML" if float(ctx.v3_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.v3_spread)) / 10.0, 1.0)',
        'display_prose_template': 'V3 model projects {v3_spread}-point edge',
    },
    {
        'signal_key': 'v3_total_nfl',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.v3_total is not None and ctx.close_total is not None and abs(float(ctx.v3_total) - float(ctx.close_total)) >= 1.5',
        'side_expr': '"OVER" if float(ctx.v3_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.v3_total) - float(ctx.close_total)) / 5.0, 1.0)',
        'display_prose_template': 'V3 model projects {v3_total} points vs market {close_total}',
    },
    {
        'signal_key': 'power_diff_edge',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.power_diff is not None and abs(float(ctx.power_diff)) >= 5.0',
        'side_expr': '"HOME_ML" if float(ctx.power_diff) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.power_diff)) / 15.0, 1.0)',
        'display_prose_template': 'power ratings favor by {power_diff}',
    },

    # ── SITUATIONAL CLASS ───────────────────────────────────────────
    {
        'signal_key': 'nfl_rest_advantage_home',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_rest is not None and ctx.away_rest is not None and (int(ctx.home_rest) - int(ctx.away_rest)) >= 3',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} coming off {home_rest} days rest vs {away_rest} for the away side',
    },
    {
        'signal_key': 'nfl_rest_advantage_away',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.away_rest is not None and ctx.home_rest is not None and (int(ctx.away_rest) - int(ctx.home_rest)) >= 3',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} coming off {away_rest} days rest vs {home_rest} for the home side',
    },
    {
        'signal_key': 'nfl_division_game',
        'class': 'situational', 'market_scope': 'multi',
        'condition_expr': 'ctx.div_game == True or ctx.div_game == 1',
        'side_expr': '"UNDER"',
        'strength_expr': '0.3',
        'display_prose_template': 'divisional game — historically play closer and lower-scoring',
        'description': 'Division games trend lower-scoring due to familiarity + tight games.',
    },

    # ── WEATHER CLASS ────────────────────────────────────────────────
    {
        'signal_key': 'nfl_high_wind_under',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.wind is not None and int(ctx.wind) >= 15 and (ctx.roof is None or ctx.roof not in ("dome","closed"))',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((int(ctx.wind) - 10) / 20.0, 1.0)',
        'display_prose_template': '{wind} mph wind at outdoor stadium — hurts passing + kicking games',
    },
    {
        'signal_key': 'nfl_cold_under',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.temp is not None and int(ctx.temp) <= 32 and (ctx.roof is None or ctx.roof not in ("dome","closed"))',
        'side_expr': '"UNDER"',
        'strength_expr': '0.35',
        'display_prose_template': 'game-time temp {temp}°F — cold games suppress offense',
    },
    {
        'signal_key': 'nfl_dome_over',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.roof is not None and ctx.roof in ("dome","closed")',
        'side_expr': '"OVER"',
        'strength_expr': '0.2',
        'display_prose_template': 'indoor game — pristine conditions favor offense',
    },

    # ── COHORT CLASS ─────────────────────────────────────────────────
    {
        'signal_key': 'nfl_confluence_home',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min(int(ctx.signal_confluence_net) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors home {signal_confluence_net}',
    },
    {
        'signal_key': 'nfl_confluence_away',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) <= -2',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors away {signal_confluence_net}',
    },

    # ── RUN-LINE (SPREAD) EQUIVALENTS ────────────────────────────────
    # NFL "RL" is the spread market — differentiating from ML via candidates
    {
        'signal_key': 'nfl_home_spread_edge',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.v4_spread is not None and ctx.close_spread is not None and (float(ctx.v4_spread) - float(ctx.close_spread)) >= 2.0',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min((float(ctx.v4_spread) - float(ctx.close_spread)) / 5.0, 1.0)',
        'display_prose_template': 'V4 sees home covering by {v4_spread} vs market spread {close_spread}',
    },
    {
        'signal_key': 'nfl_away_spread_edge',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.v4_spread is not None and ctx.close_spread is not None and (float(ctx.close_spread) - float(ctx.v4_spread)) >= 2.0',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min((float(ctx.close_spread) - float(ctx.v4_spread)) / 5.0, 1.0)',
        'display_prose_template': 'V4 sees away covering vs market spread {close_spread}',
    },

    # ── HANDLER-BASED (split/scenario/external) — sport-universal ───
    {
        'signal_key': 'sharp_split_triple_confirmed_nfl',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG',
        'side_expr': '_HANDLER_LINE_FLAG',
        'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'all three public-split sources agree sharp money is on this side',
    },
    {
        'signal_key': 'sharp_split_confirmed_nfl',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG',
        'side_expr': '_HANDLER_LINE_FLAG',
        'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'two split sources agree sharp money is here',
    },
    {
        'signal_key': 'sharp_scenario_match_nfl',
        'class': 'scenario', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_SCENARIO',
        'side_expr': '_HANDLER_SCENARIO',
        'strength_expr': '_HANDLER_SCENARIO',
        'display_prose_template': 'historical pattern hit {hit_rate}% in {sample_n} similar spots',
    },
    {
        'signal_key': 'external_handicapper_pick_nfl',
        'class': 'external_pick', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_EXTERNAL',
        'side_expr': '_HANDLER_EXTERNAL',
        'strength_expr': '_HANDLER_EXTERNAL',
        'display_prose_template': 'external analysts are on this side',
    },
]


def upsert(dry_run: bool = False):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in NFL_SIGNALS:
        row = {
            'signal_key':      s['signal_key'],
            'sport':           'NFL',
            'market_scope':    s.get('market_scope', 'multi'),
            'class':           s['class'],
            'condition_expr':  s['condition_expr'],
            'side_expr':       s['side_expr'],
            'strength_expr':   s.get('strength_expr', '0.5'),
            'weight_registry_key':    s.get('weight_registry_key'),
            'hit_rate_pct':           s.get('hit_rate_pct'),
            'sample_n':               s.get('sample_n'),
            'display_prose_template': s.get('display_prose_template'),
            'description':            s.get('description'),
            'enabled':                s.get('enabled', True),
            'origin':                 'SEEDED_NFL',
            'updated_at':             now_iso,
        }
        payloads.append(row)

    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]

    print(f'=== seeding NFL signal_sources · {len(normalized)} rows ===')
    for row in normalized:
        print(f'  {row["signal_key"]:<40} [{row["class"]:<12}] {row["market_scope"]:<5}')
    if dry_run:
        print('\n[DRY-RUN] no writes')
        return

    written = 0
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
            headers=H_WRITE, json=chunk, timeout=15)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} NFL signals')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    upsert(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
