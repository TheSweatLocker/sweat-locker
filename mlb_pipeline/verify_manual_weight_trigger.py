# -*- coding: utf-8 -*-
"""Verify trg_preserve_manual_signal_weight against the LIVE database.

== WHY THIS EXISTS (2026-11-27) =========================================
`test_manual_weight_override.py` covers the reader -- 44 offline cases
driving the real `_score_market`. It cannot cover the WRITER-side guard,
because that guard is a Postgres trigger. Until this script ran, the single
load-bearing claim of migration 20261126a was untested:

    "the override survives the 14 bulk writers of recommended_weight"

A trigger that silently fails to fire looks exactly like a trigger that
works, right up until a nightly refit erases Andy's weight and the picks
move with no record of why. That is the shape of
[[feedback_the_guard_was_never_asked]] and
[[feedback_execute_the_migration_not_just_read_it]]: the migration PARSED,
which is strictly weaker than the migration WORKING.

== WHY IT BUILDS ITS OWN ROW ============================================
It inserts a row under sport='SELFTEST', exercises the trigger on that, and
deletes it. No live signal is touched, so a crash mid-run cannot leave a
real signal carrying a test weight. 'SELFTEST' reaches no pick: every
scoring path filters by a real sport, and no signal_sources row maps to it.

== WHAT IT PROVES, each a documented failure mode ========================
  A  A bulk writer PATCHing recommended_weight does NOT clear the override,
     AND recommended_weight still moves. Preserve, not reject -- a
     rejecting trigger would freeze the measurement and hide the drift
     ([[feedback_a_refused_upsert_freezes_a_lie]]).
  B  Hand-written SQL setting manual_weight WITHOUT advancing weight_set_at
     is ignored. This is the documented consequence in the apply doc; if it
     did not hold, the trigger's one condition would be wrong.
  C  A PostgREST batch upsert unioning NULL into keys it never sent cannot
     null the override ([[feedback_postgrest_batch_normalize_keys]]).
  D  hit_rate / sample_n / tier keep refreshing UNDERNEATH a pinned weight,
     which is what audit_manual_signal_weights.py reads to show the
     measurement drifting away from the hand value.
  E  A write that DOES advance weight_set_at lands. Without this, a trigger
     that simply blocked everything would pass A-D.
  F  The three CHECK constraints reject out-of-range and half-written
     overrides at the DB, not just in the CLI
     ([[feedback_model_output_needs_a_plausibility_guard]]).

Exit 0 = every case passed. Exit 1 = a case failed. Exit 2 = could not run
(no credentials, or the migration is not applied).

    python verify_manual_weight_trigger.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL') or ''
KEY = os.environ.get('SUPABASE_KEY') or ''
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
     'Content-Type': 'application/json'}

TBL = f'{SB}/rest/v1/signal_registry'
SPORT = 'SELFTEST'
NAME = '_manual_weight_trigger_selftest'
SEL = ('id,signal_name,sport,market_scope,recommended_weight,hit_rate,'
       'sample_n,tier,manual_weight,weight_source,weight_note,'
       'weight_set_at,weight_set_by,weight_review_at')

_results: list = []


def _chk(label: str, got, want) -> None:
    ok = got == want
    _results.append((label, ok, got, want))
    print(f'  {"PASS" if ok else "FAIL"}  {label}')
    if not ok:
        print(f'          got  {got!r}')
        print(f'          want {want!r}')


def _read(row_id: int) -> dict:
    r = requests.get(TBL, headers=H,
                     params={'select': SEL, 'id': f'eq.{row_id}'}, timeout=20)
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f'read back failed: {r.status_code} {r.text[:200]}')
    return rows[0]


def _patch(row_id: int, body: dict) -> tuple:
    r = requests.patch(TBL, headers=H, params={'id': f'eq.{row_id}'},
                       data=json.dumps(body), timeout=20)
    # A blocked PATCH returns 200/204 with zero rows changed, so never infer
    # the write from the status code -- read the row back.
    return r.status_code, _read(row_id)


def _cleanup(row_id) -> None:
    if row_id is None:
        return
    r = requests.delete(TBL, headers=H, params={'id': f'eq.{row_id}'},
                        timeout=20)
    left = requests.get(TBL, headers=H,
                        params={'select': 'id', 'sport': f'eq.{SPORT}'},
                        timeout=20).json()
    n_left = len(left) if isinstance(left, list) else -1
    print(f'\ncleanup: DELETE {r.status_code}, rows left under '
          f'sport={SPORT}: {n_left}')
    if n_left != 0:
        print('  !! a SELFTEST row survived -- delete it by hand:')
        print(f'     DELETE FROM public.signal_registry '
              f"WHERE sport = '{SPORT}';")


def main() -> int:
    if not SB or not KEY:
        print('! SUPABASE_URL / SUPABASE_KEY not set')
        return 2

    probe = requests.get(TBL, headers=H,
                         params={'select': 'manual_weight', 'limit': 1},
                         timeout=20)
    if probe.status_code != 200:
        print('signal_registry.manual_weight is absent -- 20261126a is not '
              'applied. Nothing to verify.')
        return 2

    # Clear any row left behind by an earlier crashed run.
    requests.delete(TBL, headers=H, params={'sport': f'eq.{SPORT}'},
                    timeout=20)

    print('=' * 72)
    print('TRIGGER VERIFICATION  trg_preserve_manual_signal_weight')
    print('=' * 72)

    ins = requests.post(
        TBL, headers={**H, 'Prefer': 'return=representation'},
        data=json.dumps([{
            'signal_name': NAME, 'sport': SPORT, 'market_scope': 'total',
            'category': 'h2h', 'description': 'trigger self-test, deleted '
                                              'at the end of this run',
            'hit_rate': 50.0, 'sample_n': 10, 'tier': 'UNVALIDATED',
            'recommended_weight': 0.30, 'origin': 'SELFTEST_20261127',
        }]), timeout=20)
    if ins.status_code not in (200, 201):
        print(f'! insert failed {ins.status_code}: {ins.text[:300]}')
        return 2
    row_id = ins.json()[0]['id']
    print(f'\nself-test row id={row_id} sport={SPORT}\n')

    rc = 1
    try:
        # ---- E first: a real override must LAND, or A-D pass trivially ----
        print('E  a write that advances weight_set_at LANDS')
        _, r = _patch(row_id, {
            'manual_weight': 0.45, 'weight_source': 'manual',
            'weight_note': 'self-test', 'weight_set_by': 'selftest',
            'weight_review_at': '2027-01-26',
            'weight_set_at': '2026-11-27T12:00:00+00:00'})
        _chk('manual_weight stored', r['manual_weight'], 0.45)
        _chk('weight_source stored', r['weight_source'], 'manual')
        _chk('weight_review_at stored', str(r['weight_review_at'])[:10],
             '2027-01-26')
        if r['manual_weight'] != 0.45:
            print('\n!! the override never landed -- every case below would '
                  'pass for the wrong reason. Stopping.')
            return 1

        # ---- A: the 14 bulk writers of recommended_weight ----
        print('\nA  bulk refit PATCHes recommended_weight, no weight_set_at')
        _, r = _patch(row_id, {'recommended_weight': 0.77})
        _chk('override PRESERVED', r['manual_weight'], 0.45)
        _chk('weight_source preserved', r['weight_source'], 'manual')
        _chk('recommended_weight STILL REFRESHES (preserve, not reject)',
             r['recommended_weight'], 0.77)

        # ---- B: hand SQL without the timestamp ----
        print('\nB  hand SQL sets manual_weight, no weight_set_at')
        _, r = _patch(row_id, {'manual_weight': 0.99,
                               'weight_note': 'HIJACK'})
        _chk('manual_weight unchanged', r['manual_weight'], 0.45)
        _chk('weight_note unchanged', r['weight_note'], 'self-test')

        # ---- C: batch upsert unioning NULL ----
        print('\nC  batch upsert unions NULL into the override columns')
        _, r = _patch(row_id, {'manual_weight': None, 'weight_source': None,
                               'weight_note': None, 'weight_set_by': None,
                               'weight_review_at': None})
        _chk('manual_weight survived the NULL', r['manual_weight'], 0.45)
        _chk('weight_source survived the NULL', r['weight_source'], 'manual')
        _chk('weight_review_at survived the NULL',
             str(r['weight_review_at'])[:10], '2027-01-26')
        _chk('weight_set_by survived the NULL', r['weight_set_by'],
             'selftest')

        # ---- D: the measurement must stay live beside the override ----
        print('\nD  nightly refresh moves the measurement under a pinned '
              'weight')
        _, r = _patch(row_id, {'hit_rate': 52.1, 'sample_n': 44,
                               'tier': 'MEASURED_FLAT'})
        _chk('hit_rate moved', r['hit_rate'], 52.1)
        _chk('sample_n moved', r['sample_n'], 44)
        _chk('tier moved', r['tier'], 'MEASURED_FLAT')
        _chk('override still pinned', r['manual_weight'], 0.45)

        # ---- F: the CHECK constraints ----
        print('\nF  the DB rejects values the CLI would have caught anyway')
        st, _ = _patch(row_id, {'manual_weight': 1.5,
                                'weight_set_at': '2026-11-27T13:00:00+00:00'})
        _chk('manual_weight 1.5 REJECTED (range check)', st >= 400, True)
        st, _ = _patch(row_id, {'manual_weight': -0.1,
                                'weight_set_at': '2026-11-27T13:01:00+00:00'})
        _chk('manual_weight -0.1 REJECTED (range check)', st >= 400, True)
        st, _ = _patch(row_id, {'weight_source': 'guess',
                                'weight_set_at': '2026-11-27T13:02:00+00:00'})
        _chk("weight_source 'guess' REJECTED (enum check)", st >= 400, True)
        st, _ = _patch(row_id, {'weight_source': 'manual',
                                'manual_weight': None,
                                'weight_set_at': '2026-11-27T13:03:00+00:00'})
        _chk("source='manual' with a NULL weight REJECTED (half-written)",
             st >= 400, True)
        # 0.0 is a DELIBERATE value ("mute this"), not an absent one.
        _, r = _patch(row_id, {'manual_weight': 0.0,
                               'weight_source': 'manual',
                               'weight_set_at': '2026-11-27T13:04:00+00:00'})
        _chk('manual_weight 0.0 ACCEPTED (a mute is a real override)',
             r['manual_weight'], 0.0)

        failed = [x for x in _results if not x[1]]
        print('\n' + '-' * 72)
        print(f'{len(_results) - len(failed)}/{len(_results)} passed')
        if failed:
            print('\nFAILED:')
            for label, _ok, got, want in failed:
                print(f'  * {label}: got {got!r}, want {want!r}')
            print('\nThe trigger is the ONLY thing standing between a '
                  'hand-set weight and the 14 bulk writers of '
                  'recommended_weight. Do not set an override until this '
                  'passes.')
        else:
            print('the trigger holds against every bulk-write shape, and a '
                  'deliberate override still lands.')
        rc = 1 if failed else 0
    finally:
        _cleanup(row_id)
    return rc


if __name__ == '__main__':
    sys.exit(main())
