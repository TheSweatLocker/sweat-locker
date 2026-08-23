"""Sport-universal end-to-end prop pipeline healthcheck (2026-08-23).

VERIFIES 8 LAYERS
-----------------
1. Ingest       — ctx fields populated (park, weather, ump, framing, bp, fatigue)
2. Scoring      — % of props with fired signals per family
3. Conviction   — tier + conviction distribution on opinionated picks
4. Refit        — % with non-null refit_conviction; blacklist working
5. Playbook     — shadow scorer agreement rate with legacy
6. L10 recent   — % with _stat_last10 in signals JSONB
7. Render       — % of prop_jerry_reads with render_sections
8. App surface  — % passing user filter (_coverage_kill_gate = null)

Plus SAMPLE TRACES on N=3 opinionated props — prints full 8-layer state.

Runs per cron. Read-only. Non-fatal — pure diagnostic. Findings feed
into wire-up backlog + drift detection.

Sport registry maps sport → tables so NFL/NCAAF/etc. work same way
when their prop pipelines produce rows.

Usage
-----
    python prop_pipeline_healthcheck.py                 # MLB today
    python prop_pipeline_healthcheck.py --sport NFL     # NFL today
    python prop_pipeline_healthcheck.py --sample 5      # 5 trace samples
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Sport → (props_table, ctx_table, jerry_table, sport_str)
SPORT_REG = {
    'MLB':   ('mlb_pipeline_props',   'mlb_game_context',   'prop_jerry_reads', 'MLB'),
    'NFL':   ('nfl_pipeline_props',   'nfl_game_context',   'prop_jerry_reads', 'NFL'),
    'NCAAF': ('ncaaf_pipeline_props', 'ncaaf_game_context', 'prop_jerry_reads', 'NCAAF'),
    'NCAAB': ('ncaab_pipeline_props', 'ncaab_game_context', 'prop_jerry_reads', 'NCAAB'),
    'NHL':   ('nhl_pipeline_props',   'nhl_game_context',   'prop_jerry_reads', 'NHL'),
    'NBA':   ('nba_pipeline_props',   'nba_game_context',   'prop_jerry_reads', 'NBA'),
}

# Per-sport key ctx fields to check populated (layer 1)
INGEST_FIELDS = {
    'MLB': ['park_run_factor', 'temperature', 'wind_speed', 'umpire',
            'home_bullpen_era', 'away_bullpen_era',
            'home_catcher_framing', 'away_catcher_framing',
            'home_pitcher_last_outing_pitches', 'away_pitcher_last_outing_pitches',
            'home_wrc_plus', 'away_wrc_plus'],
    'NFL': ['temperature', 'wind_speed', 'roof', 'home_rest', 'away_rest'],
    'NCAAF': ['temperature', 'wind_speed'],
    'NCAAB': [],
    'NHL': [],
    'NBA': [],
}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _stat_family(prop_type: str) -> str:
    if not prop_type: return ''
    for suf in ('_over', '_under'):
        if prop_type.endswith(suf): return prop_type[:-len(suf)]
    return prop_type


def _fired(signals) -> int:
    if not isinstance(signals, dict): return 0
    return sum(1 for k in signals.keys() if not k.startswith('_'))


def _pct(n, d): return round(100*n/d, 1) if d else 0


def audit(sport: str, game_date: str, sample_n: int = 3) -> dict:
    if sport not in SPORT_REG:
        return {'error': f'unknown sport: {sport}'}
    props_tbl, ctx_tbl, jerry_tbl, sport_str = SPORT_REG[sport]

    # Fetch everything we need
    props = requests.get(f'{SB}/rest/v1/{props_tbl}', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'id,player_name,prop_type,direction,tier,conviction,'
                          'refit_conviction,signals,game_id',
                'limit': 1000}, timeout=20).json() or []
    ctx = requests.get(f'{SB}/rest/v1/{ctx_tbl}', headers=H_READ,
        params={'game_date': f'eq.{game_date}', 'select': '*'}, timeout=20).json() or []
    ctx_by_gid = {r.get('game_id'): r for r in ctx if isinstance(r, dict)}
    jerry = requests.get(f'{SB}/rest/v1/{jerry_tbl}', headers=H_READ,
        params={'game_date': f'eq.{game_date}', 'sport': f'eq.{sport_str}',
                'select': 'player_name,prop_type,direction,input_snapshot'}, timeout=20).json() or []
    jerry_by_key = {(j['player_name'], j['prop_type'], j['direction']): j for j in jerry}
    playbook = requests.get(f'{SB}/rest/v1/prop_playbook_decisions', headers=H_READ,
        params={'game_date': f'eq.{game_date}', 'sport': f'eq.{sport_str}',
                'select': 'player_name,prop_type,direction,playbook_side,playbook_tier'}, timeout=15).json() or []
    playbook_by_key = {(p['player_name'], p['prop_type'], p['direction']): p for p in playbook}

    report = {
        'sport': sport, 'game_date': game_date,
        'props_count': len(props), 'ctx_count': len(ctx),
        'jerry_count': len(jerry), 'playbook_count': len(playbook),
        'layers': {},
    }

    # Layer 1: Ingest — ctx field population
    ingest_fields = INGEST_FIELDS.get(sport, [])
    field_coverage = {}
    for f in ingest_fields:
        populated = sum(1 for c in ctx if c.get(f) is not None)
        field_coverage[f] = {'populated': populated, 'total': len(ctx),
                              'pct': _pct(populated, len(ctx))}
    report['layers']['1_ingest'] = {'field_coverage': field_coverage}

    # Layer 2: Scoring — fired signals per family
    per_fam = defaultdict(lambda: {'count': 0, 'fired_counts': []})
    for p in props:
        fam = _stat_family(p.get('prop_type') or '')
        per_fam[fam]['count'] += 1
        per_fam[fam]['fired_counts'].append(_fired(p.get('signals')))
    scoring = {}
    for fam, d in per_fam.items():
        n = d['count']
        avg = sum(d['fired_counts'])/n if n else 0
        with_signals = sum(1 for c in d['fired_counts'] if c > 0)
        scoring[fam] = {'count': n, 'avg_fired': round(avg, 1),
                        'with_fired_pct': _pct(with_signals, n)}
    report['layers']['2_scoring'] = scoring

    # Layer 3: Conviction — tier distribution on opinionated
    opinionated = [p for p in props if _fired(p.get('signals')) > 0]
    tier_dist = Counter(p.get('tier') for p in opinionated)
    report['layers']['3_conviction'] = {
        'opinionated_count': len(opinionated),
        'tier_dist': dict(tier_dist),
    }

    # Layer 4: Refit — populated %; blacklist working (bb_under/ks_under/ha_over should be null)
    with_refit = sum(1 for p in props if p.get('refit_conviction') is not None)
    blacklist_families = {('bb_under', 'under'), ('ks_under', 'under'), ('ha_over', 'over')}
    blacklist_leaks = sum(1 for p in props
                          if (p.get('prop_type'), p.get('direction')) in blacklist_families
                          and p.get('refit_conviction') is not None)
    report['layers']['4_refit'] = {
        'with_refit_pct': _pct(with_refit, len(props)),
        'blacklist_leaks': blacklist_leaks,  # should be 0
    }

    # Layer 5: Playbook — agreement rate legacy vs shadow
    agree = disagree = pboth = 0
    for p in props:
        k = (p['player_name'], p['prop_type'], p['direction'])
        pb = playbook_by_key.get(k)
        if not pb: continue
        pboth += 1
        pb_side = str(pb.get('playbook_side','')).upper()
        legacy_direction = str(p.get('direction','')).upper()
        # BACK means playbook agrees with prop's direction; FADE means opposite
        if pb_side == 'BACK': agree += 1
        elif pb_side == 'FADE': disagree += 1
    report['layers']['5_playbook'] = {
        'both_scored': pboth, 'agree_count': agree, 'disagree_count': disagree,
        'agree_pct': _pct(agree, pboth),
    }

    # Layer 6: L10 recent form
    with_l10 = sum(1 for p in props if (p.get('signals') or {}).get('_stat_last10'))
    report['layers']['6_l10'] = {
        'with_l10_pct': _pct(with_l10, len(props)),
    }

    # Layer 7: Render sections
    with_sections = sum(1 for j in jerry
                        if (j.get('input_snapshot') or {}).get('render_sections'))
    report['layers']['7_render'] = {
        'jerry_count': len(jerry),
        'with_sections_pct': _pct(with_sections, len(jerry)),
    }

    # Layer 8: App surface (simulate filter — drops _coverage_kill_gate + SKIP w/o Jerry BACK)
    surfaced = 0
    for p in props:
        sig = p.get('signals') or {}
        if sig.get('_coverage_kill_gate'): continue
        if p.get('tier') == 'SKIP': continue  # simplification (Jerry-BACK path skipped)
        surfaced += 1
    report['layers']['8_surface'] = {
        'surfaced_count': surfaced,
        'filtered_pct': _pct(len(props) - surfaced, len(props)),
    }

    # SAMPLE TRACES on N opinionated props
    samples = []
    ranked = sorted(opinionated, key=lambda p: -(p.get('conviction') or 0))[:sample_n]
    for p in ranked:
        gid = p.get('game_id')
        c = ctx_by_gid.get(gid) or {}
        k = (p['player_name'], p['prop_type'], p['direction'])
        j = jerry_by_key.get(k) or {}
        pb = playbook_by_key.get(k) or {}
        sig = p.get('signals') or {}
        samples.append({
            'player': p['player_name'], 'prop': f"{p['prop_type']}/{p['direction']}",
            'tier': p['tier'], 'conv': p['conviction'], 'refit': p.get('refit_conviction'),
            'fired_signals': [k for k in sig.keys() if not k.startswith('_')],
            'has_l10': bool(sig.get('_stat_last10')),
            'ctx_has_park': c.get('park_run_factor'),
            'ctx_has_weather': c.get('temperature'),
            'ctx_has_ump': c.get('umpire'),
            'ctx_has_bp': c.get('home_bullpen_era'),
            'has_jerry_read': bool(j),
            'has_render_sections': bool((j.get('input_snapshot') or {}).get('render_sections')),
            'playbook_side': pb.get('playbook_side'),
            'would_surface': not sig.get('_coverage_kill_gate') and p['tier'] != 'SKIP',
        })
    report['samples'] = samples
    return report


def _print(rep: dict) -> None:
    if rep.get('error'):
        print(f'ERROR: {rep["error"]}'); return
    print(f'\n{"=" * 70}')
    print(f'  PROP PIPELINE HEALTHCHECK · {rep["sport"]} · {rep["game_date"]}')
    print(f'{"=" * 70}')
    print(f'Props: {rep["props_count"]}  Ctx: {rep["ctx_count"]}  '
          f'Jerry: {rep["jerry_count"]}  Playbook: {rep["playbook_count"]}')

    L = rep['layers']
    print(f'\n1. INGEST — ctx field coverage')
    for f, d in L['1_ingest']['field_coverage'].items():
        flag = '✅' if d['pct'] >= 90 else ('⚠️' if d['pct'] >= 60 else '🚨')
        print(f'   {flag} {f:<40} {d["populated"]}/{d["total"]} ({d["pct"]}%)')

    print(f'\n2. SCORING — fired signals per family')
    for fam, d in sorted(L['2_scoring'].items(), key=lambda x: -x[1]['count']):
        flag = '✅' if d['with_fired_pct'] >= 70 else ('⚠️' if d['with_fired_pct'] >= 30 else '🚨')
        print(f'   {flag} {fam:<12} {d["count"]:>3} props · avg {d["avg_fired"]} fired · '
              f'{d["with_fired_pct"]}% have any signals')

    print(f'\n3. CONVICTION — {L["3_conviction"]["opinionated_count"]} opinionated picks')
    for t, n in L['3_conviction']['tier_dist'].items():
        print(f'   {t:<8} {n}')

    r4 = L['4_refit']
    flag4 = '✅' if r4['blacklist_leaks'] == 0 else '🚨'
    print(f'\n4. REFIT — {r4["with_refit_pct"]}% populated · {flag4} '
          f'blacklist leaks: {r4["blacklist_leaks"]} (want 0)')

    r5 = L['5_playbook']
    print(f'\n5. PLAYBOOK — {r5["both_scored"]} co-scored · '
          f'agree {r5["agree_count"]}, disagree {r5["disagree_count"]} '
          f'({r5["agree_pct"]}% agree)')

    r6 = L['6_l10']; r7 = L['7_render']; r8 = L['8_surface']
    print(f'\n6. L10 recent form: {r6["with_l10_pct"]}% populated')
    print(f'7. RENDER sections:   {r7["with_sections_pct"]}% of {r7["jerry_count"]} jerry cards')
    print(f'8. APP SURFACE:       {r8["surfaced_count"]} cards would surface (filtered {r8["filtered_pct"]}%)')

    print(f'\n{"-" * 70}')
    print(f'SAMPLE TRACES ({len(rep["samples"])} opinionated picks — full flow):')
    for s in rep['samples']:
        surface_flag = '✅ SURFACE' if s['would_surface'] else '❌ FILTERED'
        print(f'\n  {s["player"]} · {s["prop"]} · [{s["tier"]} {s["conv"]}] refit={s["refit"]}')
        print(f'    layer 1 ctx:      park={s["ctx_has_park"]} temp={s["ctx_has_weather"]} '
              f'ump={"YES" if s["ctx_has_ump"] else "NO"} bp={s["ctx_has_bp"]}')
        print(f'    layer 2 fired:    {len(s["fired_signals"])} signals · {s["fired_signals"][:5]}')
        print(f'    layer 5 playbook: {s["playbook_side"] or "no decision"}')
        print(f'    layer 6 L10:      {"YES" if s["has_l10"] else "NO"}')
        print(f'    layer 7 render:   {"YES" if s["has_render_sections"] else "NO"}')
        print(f'    layer 8:          {surface_flag}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=list(SPORT_REG.keys()))
    p.add_argument('--date', default=None)
    p.add_argument('--sample', type=int, default=3)
    args = p.parse_args()
    gd = args.date or _today_et()
    rep = audit(args.sport, gd, sample_n=args.sample)
    _print(rep)


if __name__ == '__main__':
    main()
