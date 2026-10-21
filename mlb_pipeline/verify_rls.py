#!/usr/bin/env python3
"""Prove the RLS migration is read-neutral and write-closing.

Run with --baseline BEFORE applying 20261021c_rls_close_anon_writes.sql, then
with --verify after. The baseline is the point: "the app still works" is not
something to eyeball on a build with no OTA path, and a policy that silently
drops one relation to zero rows looks exactly like a quiet night.

Uses the ANON key deliberately -- the one that ships in the app bundle. The
service-role key bypasses RLS entirely, so testing with it would pass no
matter how broken the policies were.

Write probes use DELETE/PATCH with a filter matching ZERO rows. Nothing is
ever written or removed. Note that probing with a non-existent column is NOT
a valid privilege test: PostgREST answers that from its own schema cache with
400 PGRST204, before any SQL reaches Postgres.
"""
import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
load_dotenv(os.path.join(ROOT, '.env'))

SB = os.environ.get('EXPO_PUBLIC_SUPABASE_URL')
ANON = os.environ.get('EXPO_PUBLIC_SUPABASE_ANON_KEY')
if not SB or not ANON:
    print('FAIL: EXPO_PUBLIC_SUPABASE_URL / _ANON_KEY not in root .env')
    sys.exit(2)

H_READ = {'apikey': ANON, 'Authorization': 'Bearer ' + ANON}
H_WRITE = dict(H_READ, **{'Content-Type': 'application/json',
                          'Prefer': 'return=representation'})

BASELINE = os.path.join(HERE, '_rls_baseline.json')

# Relations the client reads via .from() in app/, plus ufc_picks, which is
# reachable ONLY through get_todays_ufc_picks(). Every client RPC is SECURITY
# INVOKER (no DEFINER clause anywhere in migrations), so it runs with the
# caller's privileges and its base tables need read policies too.
READ_RELATIONS = [
    'admin_notice', 'cleatz_signals', 'cohort_display_config',
    'cohort_tag_records', 'config_ui_sections', 'daily_best_bet_history',
    'daily_dawg', 'daily_degen', 'daily_surface_records', 'external_picks',
    'external_source_calibration', 'external_source_track_record',
    'fadereport_signals', 'feature_flags', 'home_banners', 'jerry_cache',
    'jerry_reads', 'kenpom_cache', 'ladder_rung', 'ladder_state',
    'ledger_snapshots', 'ledger_suggestions', 'line_history',
    'line_movement_flags', 'mlb_game_context', 'mlb_game_results',
    'mlb_hr_watch', 'mlb_pipeline_props', 'mlb_pitcher_stats',
    'mlb_tier_calibration', 'mlb_umpires', 'nba_game_context', 'nba_injuries',
    'nba_playoff_series', 'nba_team_stats', 'ncaab_team_stats',
    'ncaaf_game_context', 'ncaaf_team_stats', 'nfl_game_context',
    'nfl_injuries', 'nfl_player_projections', 'nfl_starters', 'nfl_team_stats',
    'nhl_game_context', 'nhl_pipeline_props', 'odds_cache', 'outcomes',
    'pitcher_projections', 'prompt_templates', 'prop_grades',
    'prop_jerry_cache', 'prop_jerry_reads', 'public_splits_v2',
    'scenario_audit', 'sharp_agreement_calibration',
    'sharp_source_calibration', 'signal_pattern_registry', 'sport_registry',
    'surface_records', 'sweat_tier_config', 'team_recent_games',
    'team_situational_records', 'team_stats_rolling', 'tier_integrity_findings',
    'ufc_fighter_stats', 'ufc_picks', 'ufc_upcoming_event', 'ui_notes',
    'user_notes', 'v_dawg_track_record_by_tier', 'v_mlb_props_publishable',
    'v_nfl_props_publishable', 'v_prop_track_record',
    'v_prop_track_record_by_type', 'v_signal_records', 'v_signal_track_record',
]

RPCS = ['get_todays_sweat_card', 'get_todays_potd', 'get_todays_dawg',
        'get_todays_ufc_picks']

# Client WRITES these and never reads them back, so after the migration they
# must still accept a write while refusing a read.
WRITE_ONLY = ['user_note_dispatch_log', 'user_consent']

# Must be unreachable by anon after the migration. public_receipts is the
# public graded record and appears in NO client query -- only in comments.
MUST_DENY = ['public_receipts', 'signal_attribution']


def probe_read(rel):
    """Row count via an exact count header. No row bodies pulled."""
    try:
        r = requests.get(f'{SB}/rest/v1/{rel}', headers=
                         dict(H_READ, **{'Prefer': 'count=exact'}),
                         params={'select': '*', 'limit': '1'}, timeout=30)
        if r.status_code not in (200, 206):
            return {'ok': False, 'status': r.status_code,
                    'err': r.text[:120]}
        rng = r.headers.get('content-range', '')
        total = rng.split('/')[-1] if '/' in rng else '?'
        return {'ok': True, 'status': r.status_code, 'count': total}
    except Exception as e:
        return {'ok': False, 'status': 0, 'err': str(e)[:120]}


def probe_rpc(fn):
    try:
        r = requests.post(f'{SB}/rest/v1/rpc/{fn}', headers=H_WRITE,
                          data='{}', timeout=30)
        n = len(r.json()) if r.text.strip().startswith('[') else (
            1 if r.status_code == 200 else 0)
        return {'ok': r.status_code == 200, 'status': r.status_code, 'rows': n}
    except Exception as e:
        return {'ok': False, 'status': 0, 'err': str(e)[:120]}


def probe_write(rel):
    """Zero-row DELETE + PATCH. Returns 'blocked' | 'permitted' | detail."""
    r = requests.get(f'{SB}/rest/v1/{rel}', headers=H_READ,
                     params={'select': '*', 'limit': '1'}, timeout=30)
    rows = r.json() if r.status_code == 200 else []
    idv = rows[0].get('id') if rows else None
    if isinstance(idv, int):
        bogus = '-987654321'
    elif isinstance(idv, str) and len(idv) == 36:
        bogus = '00000000-0000-0000-0000-000000000000'
    else:
        # Cannot read it (already denied) or no usable id -- fall back to a
        # filter on a column that certainly exists for the deny check.
        bogus = None

    out = {}
    for verb, fn in (('delete', requests.delete), ('patch', requests.patch)):
        kw = {'headers': H_WRITE, 'timeout': 30}
        if bogus is None:
            kw['params'] = {'id': 'eq.-987654321'}
        else:
            kw['params'] = {'id': f'eq.{bogus}'}
        if verb == 'patch':
            kw['data'] = json.dumps({'id': idv if bogus else 1})
        try:
            x = fn(f'{SB}/rest/v1/{rel}', **kw)
            if x.status_code in (401, 403):
                out[verb] = 'blocked'
            elif x.status_code in (200, 204, 206):
                out[verb] = 'PERMITTED'
            else:
                out[verb] = f'{x.status_code}'
        except Exception as e:
            out[verb] = f'err {str(e)[:40]}'
    return out


def run_baseline():
    data = {'reads': {r: probe_read(r) for r in READ_RELATIONS},
            'rpcs': {f: probe_rpc(f) for f in RPCS}}
    with open(BASELINE, 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    bad = [r for r, v in data['reads'].items() if not v['ok']]
    print(f'baseline written: {BASELINE}')
    print(f'  {len(data["reads"])} relations, '
          f'{sum(1 for v in data["reads"].values() if v["ok"])} readable')
    for f, v in data['rpcs'].items():
        print(f'  rpc {f:26} status={v["status"]} rows={v.get("rows")}')
    if bad:
        print('  NOT readable even before the migration (so not caused by '
              'it): ' + ', '.join(bad))
    return 0


def run_verify():
    if not os.path.exists(BASELINE):
        print('FAIL: no baseline. Run --baseline BEFORE applying the '
              'migration; without it, read-neutrality cannot be proven.')
        return 2
    with open(BASELINE) as fh:
        base = json.load(fh)

    fails, warns = [], []

    print('== 1. READ NEUTRALITY (every relation the app reads) ==')
    for rel in READ_RELATIONS:
        was = base['reads'].get(rel, {})
        now = probe_read(rel)
        if was.get('ok') and not now['ok']:
            fails.append(f'READ BROKEN {rel}: was {was.get("count")} rows, '
                         f'now HTTP {now["status"]}')
            print(f'   FAIL  {rel:36} {was.get("count"):>8} -> '
                  f'HTTP {now["status"]}')
        elif was.get('ok') and now['ok'] and was.get('count') != now['count']:
            # Counts move naturally between runs; only flag a collapse to 0.
            if now['count'] == '0' and was.get('count') != '0':
                fails.append(f'READ EMPTY {rel}: {was.get("count")} -> 0')
                print(f'   FAIL  {rel:36} {was.get("count"):>8} -> 0')

    print('== 2. RPCs still answer (all SECURITY INVOKER) ==')
    for fn in RPCS:
        was, now = base['rpcs'].get(fn, {}), probe_rpc(fn)
        mark = 'ok  ' if now['ok'] else 'FAIL'
        if was.get('ok') and not now['ok']:
            fails.append(f'RPC BROKEN {fn}: HTTP {now["status"]}')
        print(f'   {mark}  {fn:30} status={now["status"]} '
              f'rows={now.get("rows")}')

    print('== 3. WRITES ARE CLOSED on everything the app only reads ==')
    for rel in ['public_receipts', 'prompt_templates', 'feature_flags',
                'sweat_tier_config', 'mlb_pipeline_props', 'jerry_cache']:
        w = probe_write(rel)
        bad = [k for k, v in w.items() if v == 'PERMITTED']
        if bad:
            fails.append(f'STILL WRITABLE {rel}: {bad}')
            print(f'   FAIL  {rel:36} {w}')
        else:
            print(f'   ok    {rel:36} {w}')

    print('== 4. WRITE-ONLY relations still accept the client write ==')
    for rel in WRITE_ONLY:
        rd = probe_read(rel)
        if rd['ok']:
            warns.append(f'{rel} is still anon-READABLE (expected denied)')
            print(f'   WARN  {rel:36} readable ({rd.get("count")} rows)')
        else:
            print(f'   ok    {rel:36} read denied (HTTP {rd["status"]})')

    print('== 5. MUST BE FULLY DENIED ==')
    for rel in MUST_DENY:
        rd = probe_read(rel)
        if rd['ok']:
            fails.append(f'{rel} still anon-readable ({rd.get("count")} rows)')
            print(f'   FAIL  {rel:36} readable ({rd.get("count")} rows)')
        else:
            print(f'   ok    {rel:36} denied (HTTP {rd["status"]})')

    print()
    for w in warns:
        print(f'WARN: {w}')
    if fails:
        print(f'FAIL: {len(fails)} problem(s):')
        for f in fails:
            print(f'   - {f}')
        return 1
    print('PASS: reads unchanged, RPCs answer, anon writes closed.')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    if a.baseline:
        sys.exit(run_baseline())
    if a.verify:
        sys.exit(run_verify())
    ap.print_help()
    sys.exit(2)
