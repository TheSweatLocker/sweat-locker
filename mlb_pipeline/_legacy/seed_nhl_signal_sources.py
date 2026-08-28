"""Seed signal_sources with NHL game-market signal catalog (2026-08-16).

NHL data model — foundation shipped 2026-08-16 (migration 20260816d).
Signals will start firing once nhl_game_context enrichment cron runs.

Signal classes for NHL:
  * model:       projected_total, panel_pred_total, panel_pred_spread, MC
  * goalie:      goalie SV% edge, GSAA edge, L5 form, back-to-back starter
  * shots:       xGF/60 edge, high-danger edge, corsi-for edge
  * team_form:   L10 goals per game hot/cold, PP%/PK% special-teams edges
  * situational: rest advantage, back-to-back fatigue, home/road streak,
                 travel distance
  * cohort:      confluence net home/away
  * split:       handler-based sharp splits (same as MLB/NFL)
  * scenario:    handler-based scenario matches
  * external:    handicapper picks weighted by NHL track record

CLI:
  python seed_nhl_signal_sources.py
  python seed_nhl_signal_sources.py --dry-run
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

NHL_SIGNALS = [
    # ── MODEL CLASS ─────────────────────────────────────────────────
    {
        'signal_key': 'nhl_projected_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.projected_total is not None and ctx.close_total is not None and abs(float(ctx.projected_total) - float(ctx.close_total)) >= 0.5',
        'side_expr': '"OVER" if float(ctx.projected_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 1.5, 1.0)',
        'display_prose_template': 'model sees {projected_total} goals vs market {close_total}',
    },
    {
        'signal_key': 'nhl_projected_spread',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.projected_spread is not None and abs(float(ctx.projected_spread)) >= 0.5',
        'side_expr': '"HOME_ML" if float(ctx.projected_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.projected_spread)) / 1.5, 1.0)',
        'display_prose_template': 'model projects home by {projected_spread} goals',
    },
    {
        'signal_key': 'nhl_panel_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.panel_pred_total is not None and ctx.close_total is not None and abs(float(ctx.panel_pred_total) - float(ctx.close_total)) >= 0.5',
        'side_expr': '"OVER" if float(ctx.panel_pred_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.panel_pred_total) - float(ctx.close_total)) / 1.5, 1.0)',
        'display_prose_template': 'external panel projects {panel_pred_total} vs market {close_total}',
    },

    # ── GOALIE CLASS (biggest single per-game NHL variable) ─────────
    {
        'signal_key': 'nhl_home_goalie_elite',
        'class': 'goalie', 'market_scope': 'total',
        'condition_expr': 'ctx.home_goalie_sv_pct is not None and float(ctx.home_goalie_sv_pct) >= 0.920',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((float(ctx.home_goalie_sv_pct) - 0.900) / 0.030, 1.0)',
        'display_prose_template': '{home_goalie} is elite ({home_goalie_sv_pct} SV%) — favors UNDER',
    },
    {
        'signal_key': 'nhl_away_goalie_elite',
        'class': 'goalie', 'market_scope': 'total',
        'condition_expr': 'ctx.away_goalie_sv_pct is not None and float(ctx.away_goalie_sv_pct) >= 0.920',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((float(ctx.away_goalie_sv_pct) - 0.900) / 0.030, 1.0)',
        'display_prose_template': '{away_goalie} is elite ({away_goalie_sv_pct} SV%) — favors UNDER',
    },
    {
        'signal_key': 'nhl_home_goalie_weak',
        'class': 'goalie', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_goalie_sv_pct is not None and float(ctx.home_goalie_sv_pct) <= 0.895',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min((0.910 - float(ctx.home_goalie_sv_pct)) / 0.030, 1.0)',
        'display_prose_template': '{home_goalie} struggling ({home_goalie_sv_pct} SV%) — leaks goals',
    },
    {
        'signal_key': 'nhl_away_goalie_weak',
        'class': 'goalie', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_goalie_sv_pct is not None and float(ctx.away_goalie_sv_pct) <= 0.895',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min((0.910 - float(ctx.away_goalie_sv_pct)) / 0.030, 1.0)',
        'display_prose_template': '{away_goalie} struggling ({away_goalie_sv_pct} SV%) — leaks goals',
    },
    {
        'signal_key': 'nhl_home_goalie_l5_heater',
        'class': 'goalie', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_goalie_last5_sv_pct is not None and float(ctx.home_goalie_last5_sv_pct) >= 0.930',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_goalie} on a heater — {home_goalie_last5_sv_pct} SV% last 5',
    },
    {
        'signal_key': 'nhl_away_goalie_l5_heater',
        'class': 'goalie', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_goalie_last5_sv_pct is not None and float(ctx.away_goalie_last5_sv_pct) >= 0.930',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_goalie} on a heater — {away_goalie_last5_sv_pct} SV% last 5',
    },

    # ── SHOTS / xG CLASS ────────────────────────────────────────────
    {
        'signal_key': 'nhl_xgf_edge_home',
        'class': 'shots', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_xgf_per60 is not None and ctx.away_xgf_per60 is not None and (float(ctx.home_xgf_per60) - float(ctx.away_xgf_per60)) >= 0.4',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min((float(ctx.home_xgf_per60) - float(ctx.away_xgf_per60)) / 1.0, 1.0)',
        'display_prose_template': '{home_team} generates {home_xgf_per60} xG/60 vs {away_xgf_per60} — offensive edge',
    },
    {
        'signal_key': 'nhl_xgf_edge_away',
        'class': 'shots', 'market_scope': 'ml',
        'condition_expr': 'ctx.away_xgf_per60 is not None and ctx.home_xgf_per60 is not None and (float(ctx.away_xgf_per60) - float(ctx.home_xgf_per60)) >= 0.4',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min((float(ctx.away_xgf_per60) - float(ctx.home_xgf_per60)) / 1.0, 1.0)',
        'display_prose_template': '{away_team} generates {away_xgf_per60} xG/60 vs {home_xgf_per60} — offensive edge',
    },
    {
        'signal_key': 'nhl_defensive_matchup_under',
        'class': 'shots', 'market_scope': 'total',
        'condition_expr': 'ctx.home_xga_per60 is not None and ctx.away_xga_per60 is not None and float(ctx.home_xga_per60) <= 2.4 and float(ctx.away_xga_per60) <= 2.4',
        'side_expr': '"UNDER"',
        'strength_expr': '0.4',
        'display_prose_template': 'both teams strong defensively ({home_xga_per60} vs {away_xga_per60} xGA/60) — low-event game',
    },

    # ── TEAM FORM CLASS ─────────────────────────────────────────────
    {
        'signal_key': 'nhl_home_offense_hot',
        'class': 'team_form', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_l10_goals_per_game is not None and float(ctx.home_l10_goals_per_game) >= 3.8',
        'side_expr': '"OVER"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} scoring {home_l10_goals_per_game} G/game L10 — offense hot',
    },
    {
        'signal_key': 'nhl_away_offense_hot',
        'class': 'team_form', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_l10_goals_per_game is not None and float(ctx.away_l10_goals_per_game) >= 3.8',
        'side_expr': '"OVER"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} scoring {away_l10_goals_per_game} G/game L10 — offense hot',
    },
    {
        'signal_key': 'nhl_home_offense_cold',
        'class': 'team_form', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_l10_goals_per_game is not None and float(ctx.home_l10_goals_per_game) <= 2.3',
        'side_expr': '"UNDER"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} scoring just {home_l10_goals_per_game} G/game L10 — offense cold',
    },
    {
        'signal_key': 'nhl_away_offense_cold',
        'class': 'team_form', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_l10_goals_per_game is not None and float(ctx.away_l10_goals_per_game) <= 2.3',
        'side_expr': '"UNDER"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} scoring just {away_l10_goals_per_game} G/game L10 — offense cold',
    },
    {
        'signal_key': 'nhl_home_pp_elite',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': 'ctx.home_pp_pct is not None and float(ctx.home_pp_pct) >= 25.0',
        'side_expr': '"OVER"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_team} power play converting {home_pp_pct}% — dangerous with the man advantage',
    },
    {
        'signal_key': 'nhl_home_pk_elite',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': 'ctx.home_pk_pct is not None and float(ctx.home_pk_pct) >= 84.0',
        'side_expr': '"UNDER"',
        'strength_expr': '0.25',
        'display_prose_template': '{home_team} PK unit at {home_pk_pct}% — shuts down opp special teams',
    },

    # ── SITUATIONAL CLASS ───────────────────────────────────────────
    {
        'signal_key': 'nhl_home_back_to_back',
        'class': 'situational', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_back_to_back == True',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.35',
        'display_prose_template': '{home_team} on 2nd of back-to-back — legs suspect',
        'description': 'Documented NHL fade: back-to-back home teams cover ATS at reduced rates.',
    },
    {
        'signal_key': 'nhl_away_back_to_back',
        'class': 'situational', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_back_to_back == True',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.35',
        'display_prose_template': '{away_team} on 2nd of back-to-back after travel — cardio concern',
    },
    {
        'signal_key': 'nhl_home_rest_edge',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_rest_days is not None and ctx.away_rest_days is not None and (int(ctx.home_rest_days) - int(ctx.away_rest_days)) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_team} {home_rest_days} days rest vs {away_rest_days} for {away_team}',
    },
    {
        'signal_key': 'nhl_away_long_road_trip',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.away_consecutive_road_games is not None and int(ctx.away_consecutive_road_games) >= 5',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{away_team} on game {away_consecutive_road_games} of road trip — fatigue',
    },

    # ── COHORT ──────────────────────────────────────────────────────
    {
        'signal_key': 'nhl_confluence_home',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min(int(ctx.signal_confluence_net) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors home ({signal_confluence_net})',
    },
    {
        'signal_key': 'nhl_confluence_away',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) <= -2',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors away ({signal_confluence_net})',
    },

    # ── HANDLERS (sport-universal) ──────────────────────────────────
    {
        'signal_key': 'sharp_split_triple_confirmed_nhl',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG', 'side_expr': '_HANDLER_LINE_FLAG', 'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'all three public-split sources agree sharp money is on this side',
    },
    {
        'signal_key': 'sharp_split_confirmed_nhl',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG', 'side_expr': '_HANDLER_LINE_FLAG', 'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'two split sources agree sharp money is here',
    },
    {
        'signal_key': 'sharp_scenario_match_nhl',
        'class': 'scenario', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_SCENARIO', 'side_expr': '_HANDLER_SCENARIO', 'strength_expr': '_HANDLER_SCENARIO',
        'display_prose_template': 'historical pattern hit {hit_rate}% in {sample_n} similar spots',
    },
    {
        'signal_key': 'external_handicapper_pick_nhl',
        'class': 'external_pick', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_EXTERNAL', 'side_expr': '_HANDLER_EXTERNAL', 'strength_expr': '_HANDLER_EXTERNAL',
        'display_prose_template': 'external analysts are on this side',
    },
]


def upsert(dry_run: bool = False):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in NHL_SIGNALS:
        row = {
            'signal_key': s['signal_key'], 'sport': 'NHL',
            'market_scope': s.get('market_scope', 'multi'),
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
            'origin': 'SEEDED_NHL',
            'updated_at': now_iso,
        }
        payloads.append(row)
    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]
    print(f'=== seeding NHL signal_sources · {len(normalized)} rows ===')
    for row in normalized: print(f'  {row["signal_key"]:<40} [{row["class"]:<12}] {row["market_scope"]}')
    if dry_run: print('\n[DRY-RUN] no writes'); return
    written = 0
    for i in range(0, len(normalized), 100):
        pr = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
                           headers=H_WRITE, json=normalized[i:i+100], timeout=15)
        if pr.status_code in (200, 201, 204): written += min(100, len(normalized)-i)
        else: print(f'  ✗ {pr.status_code}: {pr.text[:200]}')
    print(f'  ✓ wrote {written} NHL signals')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    upsert(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
