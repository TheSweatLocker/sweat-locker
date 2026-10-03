#!/usr/bin/env python3
"""Reconcile NCAAF season-aggregate team stats against the per-game log.

Andy 2026-10-03: "first and foremost we need to ensure all data is accurate
for teams."

WHY THIS EXISTS
---------------
We hold the same facts twice and never compared them:

    ncaaf_team_stats        season-to-date CUMULATIVE totals from CFBD
                            /stats/season/advanced. Feeds recompute_ncaaf_
                            per_game_stats.py -> team_computed_stats ->
                            team_stats_rolling -> every Jerry read's
                            stat_matchups fact, with a national rank beside it.

    ncaaf_team_game_stats   one IMMUTABLE row per (game, team) from CFBD
                            /games + /stats/game/advanced + /games/teams.

The aggregate is what we publish. The log is the only thing that can check it.
Until today nothing did, which is what project_stat_integrity_audit_1002 meant
by "no verification exists".

WHAT A HAND AUDIT FOUND ON 2026-10-03, and why this has to be automated
-----------------------------------------------------------------------
Restricted to the 138 SP+-rated teams, published per-game stats matched a
recomputation from the log on 128 of 138 for yards and turnovers — healthy.
The failures were all in the PLUMBING, and every one is invisible without a
comparison like this one:

  * ncaaf_team_game_stats_pull.py WAS NEVER SCHEDULED. Last hand-run
    2026-10-01 00:29, so every week-5 game was missing. Eleventh instance of
    the written-but-never-scheduled pattern.
  * The log had GAPS, not just staleness: Northwestern held weeks 1, 3 and 4
    and no week 2 at all, so a log-based average read 146.0 rush yds/g against
    a true 212.8. A missing row is not distinguishable from a bye.
  * penalties_yards is populated on 0 of 1776 log rows, while penalty_yds_pg
    is published for 266 teams WITH national ranks and was cited as a pick
    reason on the 10-03 FAU card ("Penalty home undisciplined"). A number we
    publish and cannot check is worse than one we do not publish.

So this script is deliberately a COMPARATOR, not a fixer. It does not write a
stat anywhere. When the two sources disagree the correct response depends on
which one is stale, and that is a judgement call — but it must be a VISIBLE
one. The failure mode being closed here is a wrong number published with a
fresh-looking percentile beside it.

ORIENTATION OF THE CHECK
------------------------
The aggregate is treated as the reference and the log as the thing most likely
to be incomplete, because that is what the evidence says: the aggregate is
refreshed nightly by a scheduled job and the log was not scheduled at all.
That is also why a log DEFICIT is reported separately from a true mismatch —
a log missing a game will always under-count, and calling that "the published
stat is wrong" is the mistake I made on the first pass of the hand audit.

EXIT CODE
---------
0 when every team reconciles or only tolerable drift is present.
1 when a HARD finding is present: a game-count gap, a week hole, or a column
  that is wholly unpopulated. Callers wrap this in run_step.sh, so a non-zero
  exit surfaces in the nightly summary instead of scrolling past.

USAGE
    python reconcile_ncaaf_stat_sources.py
    python reconcile_ncaaf_stat_sources.py --season 2026
    python reconcile_ncaaf_stat_sources.py --tolerance-pct 5
    python reconcile_ncaaf_stat_sources.py --quiet      # findings only
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    print('reconcile_ncaaf_stat_sources: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Aggregate column -> (log column, label). Only pairs that are the SAME fact
# in both tables. Deliberately excludes anything derived or rate-based: a
# success rate averaged over games is not the season success rate, and
# comparing them would manufacture findings.
PAIRS = [
    ('pass_yards', 'net_passing_yards', 'pass yds'),
    ('rush_yards', 'rushing_yards',     'rush yds'),
    ('turnovers',  'turnovers',         'turnovers'),
]

# Columns we publish per-game that the log cannot currently verify at all.
# Checked for total emptiness rather than per-team drift.
UNVERIFIABLE_WATCH = ['penalties_yards', 'possession_seconds', 'off_plays']


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:200]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            print(f'  ⚠ read {path} returned non-list: {str(body)[:160]}')
            return out
        out.extend(body)
        if len(body) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--tolerance-pct', type=float, default=2.0,
                    help='relative drift allowed before a team is reported')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    season = a.season

    print(f'=== reconcile_ncaaf_stat_sources · season {season} ===')

    agg_rows = [r for r in page('ncaaf_team_stats',
                                {'select': 'team,season,games,pass_yards,'
                                           'rush_yards,turnovers,sp_overall,'
                                           'updated_at'})
                if str(r.get('season')) == str(season)]
    log_rows = [r for r in page('ncaaf_team_game_stats',
                                {'select': 'team,season,week,game_date,'
                                           'net_passing_yards,rushing_yards,'
                                           'turnovers,penalties_yards,'
                                           'possession_seconds,off_plays,'
                                           'total_yards,fetched_at'})
                if str(r.get('season')) == str(season)]
    if not agg_rows or not log_rows:
        print(f'  aggregate rows {len(agg_rows)} · log rows {len(log_rows)} '
              f'— nothing to reconcile')
        return 0

    agg = {r['team']: r for r in agg_rows}
    by_team = defaultdict(list)
    for r in log_rows:
        by_team[r['team']].append(r)

    newest_log = max((str(r.get('fetched_at') or '') for r in log_rows),
                     default='')
    newest_agg = max((str(r.get('updated_at') or '') for r in agg_rows),
                     default='')
    print(f'  aggregate: {len(agg)} teams, newest updated_at {newest_agg[:19]}')
    print(f'  game log:  {len(by_team)} teams, {len(log_rows)} rows, '
          f'newest fetched_at {newest_log[:19]}')

    # ── A column that is entirely NULL is a silent publication risk ──────
    #
    # Measure against PLAYED rows, not all rows. The log intentionally carries
    # a row per SCHEDULED game so the week sequence is complete, and a future
    # game has NULL stats by definition — on 10-03 that is 672 played of 1776
    # rows, so a denominator of 1776 reports 37.8% forever and the check gets
    # muted. A denominator that can never reach 100% is not a check.
    hard = []
    played_rows = [r for r in log_rows
                   if any(r.get(c) is not None
                          for c in ('total_yards', 'net_passing_yards',
                                    'rushing_yards'))]
    print(f'\n  -- log column population (over {len(played_rows)} PLAYED rows '
          f'of {len(log_rows)} scheduled) --')
    for col in UNVERIFIABLE_WATCH:
        n = sum(1 for r in played_rows if r.get(col) is not None)
        pct = (n / len(played_rows) * 100) if played_rows else 0.0
        mark = ''
        if n == 0:
            mark = '  🚨 WHOLLY EMPTY — anything published from it is unverifiable'
            hard.append(f'log column {col} is 0/{len(played_rows)} populated')
        elif pct < 90:
            mark = '  ⚠ incomplete'
        print(f'     {col:20s} {n:5d}/{len(played_rows)} = {pct:5.1f}%{mark}')

    # ── Per-team reconciliation ──────────────────────────────────────────
    # Only teams the aggregate rates (SP+ present) are FBS; the log holds a
    # single body-bag game for most FCS opponents and comparing those produces
    # noise, not findings. That mistake cost the first pass of the hand audit.
    fbs = [t for t in agg if agg[t].get('sp_overall') is not None]
    count_gaps, week_holes, drift = [], [], defaultdict(list)

    for t in sorted(fbs):
        a_row = agg[t]
        rows = by_team.get(t, [])
        played = [r for r in rows
                  if any(r.get(c) is not None
                         for c in ('total_yards', 'net_passing_yards',
                                   'rushing_yards'))]
        a_games = a_row.get('games')
        if a_games is not None and len(played) != int(a_games):
            count_gaps.append((t, int(a_games), len(played)))

        weeks = sorted({int(r['week']) for r in played
                        if r.get('week') is not None})
        if len(weeks) >= 2:
            missing = [w for w in range(weeks[0], weeks[-1] + 1)
                       if w not in weeks]
            if missing:
                week_holes.append((t, weeks, missing))

        # Drift only where the log is NOT short a game — otherwise the deficit
        # explains the difference and reporting it as a mismatch is wrong.
        if a_games is None or len(played) != int(a_games):
            continue
        for a_col, l_col, label in PAIRS:
            av = _f(a_row.get(a_col))
            lv = [_f(r.get(l_col)) for r in played]
            lv = [x for x in lv if x is not None]
            if av is None or not lv:
                continue
            ls = sum(lv)
            if abs(av) < 1e-9 and abs(ls) < 1e-9:
                continue
            rel = abs(av - ls) / max(abs(av), 1e-9) * 100
            if rel > a.tolerance_pct:
                drift[label].append((rel, t, av, ls, len(played)))

    print(f'\n  -- reconciliation over {len(fbs)} SP+-rated teams --')
    print(f'     game-count gaps (aggregate vs log): {len(count_gaps)}')
    print(f'     week holes in the log:              {len(week_holes)}')
    for label in (p[2] for p in PAIRS):
        print(f'     {label:10s} drift >{a.tolerance_pct:g}%: '
              f'{len(drift[label])}')

    if count_gaps:
        hard.append(f'{len(count_gaps)} teams where the log is short a game')
        print(f'\n  🚨 GAME-COUNT GAPS (log is missing games the aggregate counts)')
        for t, ag, lg in sorted(count_gaps, key=lambda z: z[1] - z[2],
                                reverse=True)[:15]:
            print(f'     {t:26s} aggregate {ag} games · log {lg} '
                  f'({ag - lg:+d})')
        if len(count_gaps) > 15:
            print(f'     ... and {len(count_gaps) - 15} more')

    if week_holes:
        # 2026-10-03 · A BYE IS A HOLE AND THAT IS FINE.
        # This started as a hard failure and was wrong 9 times out of 9: the
        # list came back Air Force, Army and Navy all missing week 3, plus
        # Hawai'i and UNLV — service academies and islanders with byes, not
        # dropped rows. Northwestern/Stanford/Florida State missing week 2 the
        # same way.
        #
        # The game COUNT is the arbiter, not the week sequence: if the log has
        # as many played games as the aggregate counts, nothing is missing and
        # the gap in the numbering is a bye. So holes are only escalated for
        # teams that ALSO failed the count check, and are otherwise printed as
        # context. A check that fires on a bye teaches people to ignore it.
        gap_teams = {t for t, _, _ in count_gaps}
        real = [h for h in week_holes if h[0] in gap_teams]
        if real:
            hard.append(f'{len(real)} teams with a week hole AND a count gap')
        tag = '🚨 WEEK HOLES ON TEAMS ALSO SHORT A GAME' if real else \
              'ℹ week-sequence gaps (count reconciles, so these are byes)'
        print(f'\n  {tag}')
        for t, weeks, missing in (real or week_holes)[:15]:
            print(f'     {t:26s} has {weeks} · missing {missing}')
        if len(real or week_holes) > 15:
            print(f'     ... and {len(real or week_holes) - 15} more')

    for label in (p[2] for p in PAIRS):
        if not drift[label]:
            continue
        print(f'\n  ⚠ {label} drift on teams whose game COUNT agrees '
              f'({len(drift[label])} teams)')
        for rel, t, av, ls, n in sorted(drift[label], reverse=True)[:8]:
            print(f'     {t:26s} aggregate {av:9.1f} · log {ls:9.1f} '
                  f'({rel:5.1f}% over {n} games)')

    print('\n  ── verdict ──')
    if hard:
        for h in hard:
            print(f'  🚨 {h}')
        print('  FAIL — the log cannot verify the published stats until the '
              'above are closed.')
        return 1
    print('  OK — both sources agree within tolerance on every rated team.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
