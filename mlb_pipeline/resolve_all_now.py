#!/usr/bin/env python3
"""Run the ENTIRE resolution chain, in dependency order, idempotently.

WHY (2026-10-05)
----------------
Andy: "we continue to have issue resolving sweatcard recap as snell prop and
LAD game are unresolved on home screen ... every fucking day its the same shit
over and over ... if we cant resolve play efficiently how do i have any trust
that selection logic is correct."

The logic was not the problem this morning. ATL @ LAD was Final 3-2 on
statsapi, `mlb_pipeline_props` had already graded Blake Snell er_under 1.5 as
a WIN off final_value=1 — and the receipt and the card payload still said
Pending, because the jobs that carry a result from one to the next had not
run.

MEASURED, not guessed. `mlb_grade_overnight` fires three cron slots meant to
land 2:30 / 4:30 / 5:30am ET. Actual fire times from workflow_heartbeat:

    slot 630  scheduled 06:30 UTC   actual 11:55-12:45   ~5.5h late
    slot 830  scheduled 08:30 UTC   actual 13:37-14:17   ~5.7h late
    slot 930  scheduled 09:30 UTC   actual 14:23-15:56   ~5.5h late

and on 10-05 none of the three had fired by 13:45 UTC — 22.8h after the
previous run. GitHub Actions treats scheduled workflows as low priority and
delays or drops them under load. So the "overnight" grader routinely runs
between 8am and noon ET, which is AFTER Andy looks. No amount of grading logic
fixes a job that has not started.

WHAT THIS IS
------------
One command that runs every link of the chain in dependency order, reports
what each one changed, and ends with a verdict naming anything still
unresolved. Safe to run any number of times — every step underneath only
fills NULLs and never regrades.

Dependency order matters and is the whole point. A result has to travel:

    box score -> <sport>_game_results      (resolvers)
              -> prop / read source tables (graders)
              -> public_receipts           (receipt grader)
              -> jerry_cache card payload  (card refresh)
              -> daily_surface_records     (aggregator)
              -> surface_records           (rollup the app reads)

Run it out of order and each hop reads the previous hop's stale value, which
is exactly the half-resolved state Andy keeps finding.

    python resolve_all_now.py                 # today + yesterday
    python resolve_all_now.py --days 3
    python resolve_all_now.py --skip-rollup   # grading only, no record rewrite
"""
from __future__ import annotations
import argparse, datetime as dt, os, subprocess, sys, time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

PY = sys.executable


def _et_today() -> dt.date:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()


def run(label: str, args: list, timeout: int = 900) -> tuple[bool, str]:
    t0 = time.time()
    try:
        p = subprocess.run([PY] + args, cwd=str(_HERE), capture_output=True,
                           text=True, encoding='utf-8', errors='replace',
                           timeout=timeout)
        out = (p.stdout or '') + (p.stderr or '')
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        return False, f'TIMEOUT after {timeout}s'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    dur = time.time() - t0
    # Keep the last few meaningful lines — enough to see what changed.
    lines = [l.rstrip() for l in out.split('\n') if l.strip()]
    tail = ' | '.join(lines[-3:])[:260]
    print(f'  {"OK " if ok else "FAIL"} {label:<34} {dur:5.1f}s  {tail}')
    if not ok:
        # == 2026-10-06 - PERSIST WHAT FAILED ==
        # A chain run reported STEPS FAILED: ['prop_jerry_reads',
        # 'NFL props'] and BOTH exited 0 when re-run by hand minutes later,
        # so the cause was transient and UNKNOWABLE - the only record was
        # three tail lines in a console buffer that was already gone. Same
        # blindness the GitHub gate had until its step-failure tally was
        # published to the DB. Keep the full output on disk, named and
        # timestamped, so the next transient failure is diagnosable instead
        # of a shrug.
        try:
            d = _HERE / '_chain_failures'
            d.mkdir(exist_ok=True)
            safe = ''.join(c if (c.isalnum() or c in '-_') else '_'
                           for c in label)[:60]
            stamp = dt.datetime.now().strftime('%Y%m%dT%H%M%S')
            fp = d / (stamp + '_' + safe + '.log')
            hdr = ('# ' + label + chr(10) + '# argv: ' + repr(args)
                   + chr(10) + '# ' + dt.datetime.now().isoformat()
                   + chr(10) * 2)
            fp.write_text(hdr + out, encoding='utf-8', errors='replace')
            print('       -> full output: ' + fp.name)
        except Exception as e:
            print('       -> could not save failure log: ' + type(e).__name__)
    return ok, out


def ungraded_report(days: int):
    """Receipts still unresolved on a PAST-or-today date, by surface.

    Returns (by_surface, error). The error is returned rather than swallowed:
    the first version passed `game_date.lte` as a parameter NAME, which is not
    PostgREST syntax. The query 400'd, the function returned {}, and the
    verdict printed "no ungraded receipts in window" while 41 sat ungraded.
    A false all-clear is worse than no check at all, so a failed query now
    says so instead of reading as success.
    """
    today = _et_today()
    lo = (today - dt.timedelta(days=days)).isoformat()
    hi = today.isoformat()
    out = {}
    r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, timeout=60,
                     params={'select': 'surface,sport,market,pick_label,game_date',
                             'result': 'is.null',
                             'and': f'(game_date.gte.{lo},game_date.lte.{hi})',
                             'limit': 1000})
    if r.status_code not in (200, 206):
        return out, f'{r.status_code} {r.text[:120]}'
    for x in r.json():
        out.setdefault(x['surface'], []).append(x)
    return out, None


def finals_without_scores(days: int) -> list:
    """Games on a PAST date that still have no score — genuinely stuck.

    Today's games are excluded on purpose. A verdict that lists tonight's
    unplayed slate as "blocking" is wrong every single morning, and a
    detector that cries wolf daily gets muted — which is the failure mode
    the overnight workflow already warns about in its own comments. Only a
    game whose date has passed and whose score is still NULL is a defect.
    """
    today = _et_today()
    lo = (today - dt.timedelta(days=days)).isoformat()
    hi = (today - dt.timedelta(days=1)).isoformat()   # strictly before today
    if hi < lo:
        return []
    bad = []
    for sport, tbl in (('MLB', 'mlb_game_results'), ('NFL', 'nfl_game_results'),
                       ('NHL', 'nhl_game_results'), ('NCAAF', 'ncaaf_game_results'),
                       ('NBA', 'nba_game_results')):
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=60,
                         params={'select': 'game_date,home_team,away_team,home_score',
                                 'home_score': 'is.null',
                                 'and': f'(game_date.gte.{lo},game_date.lte.{hi})',
                                 'limit': 200})
        if r.status_code in (200, 206):
            for x in r.json():
                bad.append((sport, x.get('game_date'), x.get('away_team'),
                            x.get('home_team')))
        else:
            print(f'  ⚠ {tbl} stuck-check {r.status_code}: {r.text[:90]}')
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=2,
                    help='how many days back the graders should cover')
    ap.add_argument('--skip-rollup', action='store_true',
                    help='stop before aggregate/surface_records')
    args = ap.parse_args()

    t0 = time.time()
    today = _et_today()
    print(f'=== resolve_all_now · ET {today} · {args.days}-day window ===\n')

    fails = []

    # ── 0. BOX SCORES. The bottom of the whole chain. ──────────────────
    # 2026-10-05: five receipts from 10-03/10-04 could not be settled by any
    # path, and the reason was here. They name prop tuples the prop table no
    # longer holds — Michael King ha_under 4.5 (the table has ha_over 1.5),
    # Austin Hays hits_under 0.5 (no prop row at all) — so the prop-table
    # settler has nothing to read. The box-score fallback should cover exactly
    # that case, except mlb_player_game_log had NO ROW for those players on
    # those dates either.
    #
    # It is kept current by this script, which until now ran ONLY inside
    # mlb_grade_overnight — the workflow measured at 5.5h late and some days
    # not at all. A store that stops updating is worse than none, because the
    # lookback thins out silently instead of failing. It belongs at the bottom
    # of the chain, before anything that reads a box score.
    lo = (today - dt.timedelta(days=args.days + 1)).isoformat()
    print('0. persist box scores')
    ok, _ = run('MLB player game log', ['backfill_mlb_player_game_log.py',
                                        '--start', lo, '--end', today.isoformat(),
                                        '--resume'])
    if not ok:
        fails.append('MLB player game log')

    # ── 1. SCORES. Nothing downstream can grade without these. ─────────
    print()
    print('1. ingest final scores')
    for label, cmd in (
        ('MLB results + props + card', ['resolve_game_results.py']),
        ('NFL results', ['resolve_nfl_results.py', '--season', str(today.year)]),
        ('NHL results', ['nhl_resolve_results.py', '--date', today.isoformat()]),
        ('NHL results (yesterday)', ['nhl_resolve_results.py', '--date',
                                     (today - dt.timedelta(days=1)).isoformat()]),
        ('NCAAF results', ['resolve_ncaaf_results.py']),
        # 2026-10-06: CFBD's monthly quota is exhausted, and
        # resolve_ncaaf_results sources its scores from CFBD — with the
        # quota dead it reports "CFBD games w/ scores: 0" and NCAAF
        # cannot be graded AT ALL. ESPN needs no key and no quota, and
        # agreed with CFBD on 53 of 53 scores for 2026-10-03 with 0
        # mismatches. Runs AFTER the CFBD attempt so CFBD stays primary
        # whenever it has quota, and only fills rows where home_score
        # IS NULL so a CFBD score is never overwritten.
        ('NCAAF results (ESPN fallback)', ['ncaaf_results_espn.py',
                                          '--days', '10', '--apply']),
        ('NBA results', ['nba_resolve_results.py', '--date', today.isoformat()]),
    ):
        ok, _ = run(label, cmd)
        if not ok:
            fails.append(label)

    # ── 2. SOURCE TABLES. Props and reads grade off the box score. ─────
    print('\n2. grade source tables')
    for label, cmd in (
        ('prop_jerry_reads', ['grade_prop_jerry_reads.py']),
        # 2026-10-05: NFL props were NOT in this chain and nothing else
        # ran them. The weekend audit reported "NFL props: no graded plays"
        # while nfl_pipeline_props held 245 NULL rows including ALL of
        # Sunday. Running resolve_nfl_props_espn --lookback 3 by hand
        # graded 196 immediately, and they were the BEST surface of the
        # weekend (+28.26u, +6.0%, with SKIP the only losing tier). A
        # surface that performs and is invisible because nothing grades
        # it is the worst version of this bug.
        ('MLB props', ['grade_props.py']),
        ('NFL props', ['resolve_nfl_props_espn.py', '--lookback',
                       str(max(args.days, 3))]),
        ('POTD', ['grade_potd.py']),
        # 2026-10-05: backfill_jerry_pick_alignment existed as a ONE-SHOT
        # from launch weekend and was never scheduled, so the badge and the
        # prose could disagree indefinitely. Running it today found a live
        # one: NCAAF USC @ Penn State published 'Penn State ML' against a
        # primary_play of 'USC ML'. It self-skips NFL inside the week lock
        # and skips started games, so it is safe on a timer.
        ('pick alignment', ['backfill_jerry_pick_alignment.py']),
    ):
        ok, _ = run(label, cmd)
        if not ok:
            fails.append(label)

    # ── 3. RECEIPTS. The immutable record inherits from the sources. ───
    print('\n3. grade receipts (the published record)')
    ok, _ = run('public_receipts', ['grade_public_receipts.py',
                                    '--days', str(args.days)])
    if not ok:
        fails.append('public_receipts')

    # ── 4. CARD PAYLOAD. What the home screen actually renders. ────────
    print('\n4. refresh card payloads')
    ok, _ = run('sweat_card top_8 + football', ['refresh_card_grades.py',
                                                '--days', str(args.days + 1),
                                                '--apply'])
    if not ok:
        fails.append('refresh_card_grades')

    # ── 5. ROLLUPS. Records the app reads. ─────────────────────────────
    if not args.skip_rollup:
        print('\n5. rebuild records')
        for i in range(args.days):
            d = (today - dt.timedelta(days=i)).isoformat()
            ok, _ = run(f'aggregate {d}', ['aggregate_daily_records.py',
                                           '--date', d])
            if not ok:
                fails.append(f'aggregate {d}')
        ok, _ = run('surface_records', ['compute_surface_records.py'], timeout=1800)
        if not ok:
            fails.append('surface_records')

    # ── VERDICT ───────────────────────────────────────────────────────
    print(f'\n=== verdict ({time.time()-t0:.0f}s) ===')
    if fails:
        print(f'  STEPS FAILED: {fails}')

    stuck = finals_without_scores(args.days)
    if stuck:
        print(f'  🚨 PAST games with NO final score: {len(stuck)}')
        for sp, d, a, h in stuck[:8]:
            print(f'     {sp:<6} {d} {a} @ {h}')
        print('     -> these block every grade behind them')
    else:
        print('  every PAST game in window has a final score')

    ung, ung_err = ungraded_report(args.days)
    n = sum(len(v) for v in ung.values())
    if ung_err:
        print(f'  ⚠ ungraded check FAILED ({ung_err}) — cannot confirm clean')
        fails.append('ungraded check')
    elif n:
        print(f'  receipts still ungraded: {n}')
        for surf, rows in sorted(ung.items(), key=lambda kv: -len(kv[1])):
            ex = ', '.join(str(x.get('pick_label'))[:26] for x in rows[:2])
            print(f'     {surf:<13} {len(rows):3d}  e.g. {ex}')
    else:
        print('  no ungraded receipts in window')

    clean = not fails and not stuck and n == 0 and not ung_err
    print(f'\n  {"CLEAN — chain fully resolved" if clean else "INCOMPLETE — see above"}')
    # Exit non-zero on a real failure so a workflow surfaces it, but NOT
    # merely because a game in progress has no score yet.
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
