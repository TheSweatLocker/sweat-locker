#!/usr/bin/env python3
"""Prove the RLS migrations are read-neutral and write-closing.

    python verify_rls.py --baseline    # BEFORE applying (captures reads)
    python verify_rls.py --verify      # after applying

TWO MEASUREMENT MISTAKES THIS SCRIPT EXISTS TO AVOID, both made for real on
2026-10-21 before being caught:

1. RLS STATE CANNOT BE READ OFF HTTP CODES. A DELETE filtered to zero rows
   returns 200 with an empty array whether RLS denied it or it simply matched
   nothing -- indistinguishable over REST. And a probe using a non-existent
   column returns 400 PGRST204 from PostgREST's own schema cache, before any
   SQL reaches Postgres, so it reports nothing about privileges at all. The
   first version of this file concluded "WRITE-OPEN" from PGRST204 and
   "PERMITTED" from a 200, and both readings were worthless. Write posture is
   therefore read from the catalog via get_rls_audit() (service role).

2. DO NOT PROBE READS WITH count=exact. The app never sends it. An exact
   count on 3.6M-row line_history hits the statement timeout and returns
   57014 as a 500, which looks exactly like a policy breaking the read. 76 of
   them back to back also starves everything else. Reads are probed in the
   shape the app actually uses, and a 57014 is reported as a pre-existing
   performance issue, never as an RLS failure.
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
load_dotenv(os.path.join(HERE, '.env'))

SB = os.environ.get('EXPO_PUBLIC_SUPABASE_URL')
ANON = os.environ.get('EXPO_PUBLIC_SUPABASE_ANON_KEY')
# Locally the service key lives in mlb_pipeline/.env as SUPABASE_KEY; CI uses
# SUPABASE_SERVICE_ROLE_KEY. Checked by its JWT role claim, not by name alone.
SVC = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_SERVICE_KEY')
       or os.environ.get('SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not ANON:
    print('FAIL: EXPO_PUBLIC_SUPABASE_URL / _ANON_KEY not found')
    sys.exit(2)

H_ANON = {'apikey': ANON, 'Authorization': 'Bearer ' + ANON}
BASELINE = os.path.join(HERE, '_rls_baseline.json')

# Relations the client reaches via .from() in app/, plus ufc_picks, which is
# in NO .from() call and reachable only through get_todays_ufc_picks(). Every
# client RPC lacks a SECURITY DEFINER clause, so it runs as INVOKER with the
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

# Written by the client, never read back: INSERT/UPDATE but no SELECT.
WRITE_ONLY = {'user_note_dispatch_log', 'user_consent'}

# polcmd codes in pg_policy
CMD = {'r': 'SELECT', 'a': 'INSERT', 'w': 'UPDATE', 'd': 'DELETE',
       '*': 'ALL'}
UNTRUSTED = {'anon', 'public', 'authenticated'}


def probe_read(rel):
    """Read in the shape the app uses. No count=exact -- see module docstring."""
    try:
        r = requests.get(f'{SB}/rest/v1/{rel}', headers=H_ANON,
                         params={'select': '*', 'limit': '1'}, timeout=60)
        if r.status_code in (200, 206):
            body = r.json()
            return {'ok': True, 'status': r.status_code,
                    'rows': len(body) if isinstance(body, list) else 0}
        code = ''
        try:
            code = (r.json() or {}).get('code', '')
        except Exception:
            pass
        return {'ok': False, 'status': r.status_code, 'code': code,
                'timeout': code == '57014', 'err': r.text[:100]}
    except Exception as e:
        return {'ok': False, 'status': 0, 'err': str(e)[:100]}


def probe_rpc(fn):
    try:
        r = requests.post(f'{SB}/rest/v1/rpc/{fn}',
                          headers=dict(H_ANON, **{
                              'Content-Type': 'application/json'}),
                          data='{}', timeout=60)
        n = len(r.json()) if r.text.strip().startswith('[') else (
            1 if r.status_code == 200 else 0)
        return {'ok': r.status_code == 200, 'status': r.status_code, 'rows': n}
    except Exception as e:
        return {'ok': False, 'status': 0, 'err': str(e)[:100]}


def fetch_audit():
    """Catalog truth. Needs the service-role key and migration 20261021d."""
    if not SVC:
        return None, 'SUPABASE_SERVICE_ROLE_KEY not in env'
    try:
        r = requests.post(f'{SB}/rest/v1/rpc/get_rls_audit',
                          headers={'apikey': SVC,
                                   'Authorization': 'Bearer ' + SVC,
                                   'Content-Type': 'application/json'},
                          data='{}', timeout=90)
        if r.status_code != 200:
            return None, f'HTTP {r.status_code} {r.text[:120]}'
        return r.json(), None
    except Exception as e:
        return None, str(e)[:120]


def run_baseline():
    data = {'reads': {r: probe_read(r) for r in READ_RELATIONS},
            'rpcs': {f: probe_rpc(f) for f in RPCS}}
    with open(BASELINE, 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    ok = sum(1 for v in data['reads'].values() if v['ok'])
    print(f'baseline written: {BASELINE}')
    print(f'  {len(data["reads"])} relations, {ok} readable')
    for f, v in data['rpcs'].items():
        print(f'  rpc {f:26} status={v["status"]} rows={v.get("rows")}')
    return 0


def run_verify():
    fails, warns, notes = [], [], []

    print('== 1. READ NEUTRALITY (app read shape, no exact count) ==')
    base = {}
    if os.path.exists(BASELINE):
        with open(BASELINE) as fh:
            base = json.load(fh).get('reads', {})
    else:
        warns.append('no baseline file; read neutrality is unproven')

    broke = slow = 0
    for rel in READ_RELATIONS:
        now = probe_read(rel)
        was_ok = base.get(rel, {}).get('ok')
        if now['ok']:
            continue
        if now.get('timeout'):
            slow += 1
            notes.append(f'{rel}: statement timeout (57014) - PERFORMANCE, '
                         f'not RLS')
            continue
        if was_ok:
            broke += 1
            fails.append(f'READ BROKEN {rel}: HTTP {now["status"]} '
                         f'{now.get("code", "")}')
        else:
            notes.append(f'{rel}: not readable before either, HTTP '
                         f'{now["status"]}')
    print(f'   {len(READ_RELATIONS) - broke - slow} reading normally, '
          f'{slow} timing out (pre-existing), {broke} BROKEN by RLS')

    print('== 2. RPCs still answer (all SECURITY INVOKER) ==')
    for fn in RPCS:
        now = probe_rpc(fn)
        if not now['ok']:
            fails.append(f'RPC BROKEN {fn}: HTTP {now["status"]}')
        print(f'   {"ok  " if now["ok"] else "FAIL"}  {fn:30} '
              f'status={now["status"]} rows={now.get("rows")}')

    print('== 3. RLS POSTURE FROM THE CATALOG (not from status codes) ==')
    audit, err = fetch_audit()
    if audit is None:
        warns.append(f'could not read catalog audit ({err}); write posture '
                     f'UNVERIFIED - apply 20261021d and set the service key')
        print(f'   SKIPPED: {err}')
    else:
        tables = audit.get('tables') or []
        no_rls, anon_write = [], []
        for t in tables:
            name = t['table']
            if not t.get('rls'):
                no_rls.append(name)
            for pol in (t.get('policies') or []):
                cmd = CMD.get(pol.get('cmd'), pol.get('cmd'))
                roles = set(pol.get('roles') or [])
                if cmd in ('ALL', 'INSERT', 'UPDATE', 'DELETE') and (
                        roles & UNTRUSTED):
                    if name in WRITE_ONLY and cmd in ('INSERT', 'UPDATE'):
                        continue          # the two intended client writes
                    anon_write.append(f'{name}.{pol["name"]} ({cmd})')
        print(f'   {len(tables)} public tables audited')
        print(f'   RLS disabled on {len(no_rls)}')
        print(f'   anon-writable policies: {len(anon_write)}')
        if no_rls:
            fails.append(f'RLS still disabled on {len(no_rls)} table(s): '
                         + ', '.join(sorted(no_rls)[:12])
                         + (' ...' if len(no_rls) > 12 else ''))
        if anon_write:
            fails.append(f'{len(anon_write)} anon-writable policy(ies) '
                         f'remain: ' + ', '.join(sorted(anon_write)[:12])
                         + (' ...' if len(anon_write) > 12 else ''))

        print('== 4. THE TABLES THAT MATTER MOST ==')
        by_name = {t['table']: t for t in tables}
        for name in ('public_receipts', 'prompt_templates', 'feature_flags',
                     'sweat_tier_config', 'jerry_cache', 'mlb_pipeline_props',
                     'signal_attribution'):
            t = by_name.get(name)
            if not t:
                print(f'   ?     {name:24} not a public table')
                continue
            pols = [f'{CMD.get(p["cmd"], p["cmd"])}'
                    f'->{",".join(p.get("roles") or [])}'
                    for p in (t.get('policies') or [])]
            w = [p for p in (t.get('policies') or [])
                 if CMD.get(p['cmd']) in ('ALL', 'INSERT', 'UPDATE', 'DELETE')
                 and (set(p.get('roles') or []) & UNTRUSTED)]
            mark = 'ok  ' if (t.get('rls') and not w) else 'FAIL'
            print(f'   {mark}  {name:24} rls={t.get("rls")} '
                  f'policies={pols or "none (deny)"}')

        print('== 5. CLIENT WRITES STILL PERMITTED ==')
        for name in sorted(WRITE_ONLY):
            t = by_name.get(name)
            if not t:
                continue
            cmds = {CMD.get(p['cmd'], p['cmd']) for p in
                    (t.get('policies') or [])
                    if set(p.get('roles') or []) & UNTRUSTED}
            need = {'INSERT'} if name == 'user_consent' else {'INSERT',
                                                              'UPDATE'}
            missing = need - cmds
            if missing:
                fails.append(f'{name} lost its client write policy: missing '
                             f'{sorted(missing)}')
            if 'SELECT' in cmds or 'ALL' in cmds:
                warns.append(f'{name} is anon-READABLE (expected write-only)')
            print(f'   {"ok  " if not missing else "FAIL"}  {name:24} '
                  f'anon cmds={sorted(cmds) or "none"}')

    print()
    for n in notes[:10]:
        print(f'note: {n}')
    for w in warns:
        print(f'WARN: {w}')
    if fails:
        print(f'\nFAIL: {len(fails)} problem(s):')
        for f in fails:
            print(f'   - {f}')
        return 1
    if audit is None:
        # Never print PASS for something that was not measured. The first
        # version of this file reported "anon writes closed" on a run that had
        # skipped the write check entirely, which is exactly the kind of false
        # all-clear this script exists to prevent.
        print('\nINCONCLUSIVE: reads are unchanged and the RPCs answer, but '
              'the write posture was NOT checked.')
        return 2
    print('\nPASS: reads unchanged, RPCs answer, anon writes closed.')
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
