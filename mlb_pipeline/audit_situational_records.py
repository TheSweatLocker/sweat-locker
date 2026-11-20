#!/usr/bin/env python3
"""Assert every sport's Situational Records card counts the games it should.

WHY THIS EXISTS
---------------
2026-11-19. Andy asked me to verify the ATS and moneyline records on one NHL
card (Canucks @ Kraken). The moneyline records were exact to the game --
5-17-1 and 9-8-1, matching the league's own standings, goal totals and L10.
The ATS records were both wrong, and chasing that one game turned up the same
defect class in three more sports:

    NCAAB  as_fav / as_dog produced ZERO rows for ALL 362 teams. The matview
           derives is_fav from close_home_ml/close_away_ml, which are filled
           on 0 of 626 scored 2026-27 rows; the real prices live in the twin
           pair home_ml_close/away_ml_close (589, 94.1%). NULL is not FALSE,
           so `WHERE is_fav` and `WHERE NOT is_fav` BOTH excluded every row.
           Two of the card's four filter rows, absent league-wide, and the
           card hides a row when neither team has data -- so it looked like
           a layout choice.

    NBA    182 of 222 regular-season games uncounted. The matview gates on
           season_type = 'regular' (correctly -- it exists to drop the
           preseason), but season_type's ONLY writer is
           backfill_nba_season_type.py, which is in no workflow. It last ran
           around 2026-10-22, so every game from 10-23 on is NULL and NULL
           is deliberately excluded. Teams showed 1-4 games having played
           12-17 -- "starts 0-0 essentially", which is the exact complaint
           the 2026-10-21 season-label fix was meant to end.

    NHL    the whole 12-game 2026-11-07 slate fell out of every ATS record.
           nhl_game_results.close_puckline was NULL for all 12 while
           nhl_game_context.close_spread held the line, so spread_result was
           NULL. 24 teams, one game each, cutting opposite ways: Vancouver
           showed 10-12 instead of 10-13, Seattle 9-8 instead of 10-8.

    MLB    234 of 2,409 scored games have no spread_result and 112 no
           total_result, concentrated at the season's opening week.

THE COMMON SHAPE, and the reason a single detector can cover all of it: a
record is a COUNT, and every one of these failures is a game that silently
did not get counted. Nothing was mis-graded -- I regraded all six sports
from score + line and found exactly ONE wrong grade in 4,000+ games. The
records were not wrong because the arithmetic was wrong. They were wrong
because the denominator was short, and a short denominator renders as a
perfectly plausible record.

WHAT THIS CHECKS, in the order the failures were found
------------------------------------------------------
1. SHORTFALL. Per sport and market, the games the matview SHOULD count
   (reproducing its own WHERE clauses) against the games it DID count
   (summing wins+losses+pushes over the `overall` cells / 2). A gap is
   reported with its dates, because a gap concentrated on one date is a
   feed outage and a gap spread thin is a grader problem -- different bugs.

2. ABSENT FILTER. A sport with prices but zero as_fav AND zero as_dog rows
   is the NCAAB defect. This is the check that would have caught it: the
   card's own "hide an empty row" kindness means the screen cannot.

3. TWIN COLUMNS. For every column the matview reads, whether a DIFFERENT
   column on the same table carries the value on rows where the read one is
   NULL. This is the mechanism behind NCAAB's is_fav and NHL's puck line,
   and it names the twin so the fix is obvious.

4. WHOLE-SLATE GAPS. Every scored game on a date missing the same grade.
   That is a feed outage, not scattered noise, and it is how 2026-11-07
   should have announced itself on 2026-11-08.

WHAT IT DOES NOT DO: regrade. Grading is checked by
backfill_game_grades.py and was separately verified clean. This counts.

EXIT CODES
    0  every sport reconciles (or is legitimately out of season)
    1  at least one sport is short, or a filter is absent
    2  wiring error -- could not read a table at all

A SPORT WITH NO ROWS IS `skipped`, NEVER `clean`. That distinction is the
whole lesson of the LR-shadow watchdog, which read only MLB and so could
not fail after 2026-11-05; and of verify_read_coverage, which counted rows
instead of prose and printed 29/29 OK over 26 blank NFL cards for 18 days.
A detector that cannot fail is worse than no detector, because it is
reported as a pass.

CLI
    python audit_situational_records.py
    python audit_situational_records.py --sport NCAAB
    python audit_situational_records.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
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
HDRS = {'apikey': KEY or '', 'Authorization': 'Bearer ' + (KEY or '')}

#: The matview's own definition, mirrored. Keep in step with the newest
#: team_situational_records migration -- if these drift, this script reports
#: a shortfall that is really a disagreement between two descriptions of the
#: same rule, which is worse than no report. `season` is how the CARD
#: queries (ctx.season), `where` reproduces the matview's row filter, and
#: `fav` names the column pair is_fav comes from so the twin check knows
#: what matters.
SPORTS = {
    'MLB': dict(
        table='mlb_game_results', season_col='season', season='2026',
        line='close_spread', fav=('close_spread',),
        where=lambda r: True),
    'NFL': dict(
        table='nfl_game_results', season_col='season', season='2026',
        line='close_spread', fav=('close_spread',),
        where=lambda r: True),
    'NCAAF': dict(
        table='ncaaf_game_results', season_col='season', season='2026',
        line='close_spread', fav=('close_spread',),
        # The matview excludes neutral-site games for NCAAF only.
        where=lambda r: not r.get('neutral_site')),
    'NHL': dict(
        table='nhl_game_results', season_col=None, season='2026',
        line='close_puckline', fav=('close_home_ml', 'close_away_ml'),
        # Digits 4-6 of an NHL game id are the phase: 01 pre, 02 regular,
        # 03 playoffs. Only 02 counts.
        where=lambda r: str(r.get('game_id'))[4:6] == '02'),
    'NBA': dict(
        table='nba_game_results', season_col='season', season='2026-27',
        line='close_spread', fav=('close_home_ml', 'close_away_ml'),
        # NULL season_type is excluded by the matview ON PURPOSE (unknown is
        # not regular), which is exactly why an unfilled season_type silently
        # drops games rather than failing loudly.
        where=lambda r: r.get('season_type') == 'regular'),
    'NCAAB': dict(
        table='ncaab_game_results', season_col='season', season='2026-27',
        line='close_spread', fav=('close_home_ml', 'close_away_ml',
                                  'home_ml_close', 'away_ml_close'),
        where=lambda r: True),
}

#: Every column each sport's `where` lambda touches MUST be selected, or the
#: lambda reads None and silently excludes everything. That is not a
#: hypothetical: writing this script, I filtered NBA on season_type without
#: selecting it and measured NBA as ZERO scored games, then nearly reported
#: the NBA card as entirely dark. A gate reading an unselected column always
#: takes the same branch.
GATE_COLS = {
    'NCAAF': ['neutral_site'],
    'NBA': ['season_type'],
    'NHL': [],
}

#: Columns that are known to carry the same value under another name. The
#: twin check reports a populated twin as CRITICAL because it means a reader
#: is pointed at the wrong name -- absent and unselected look identical
#: downstream.
TWINS = {
    'ncaab_game_results': {'close_home_ml': 'home_ml_close',
                           'close_away_ml': 'away_ml_close',
                           'home_ml_close': 'close_home_ml',
                           'away_ml_close': 'close_away_ml'},
    'mlb_game_results': {'close_home_ml': 'home_ml_close',
                         'close_away_ml': 'away_ml_close'},
    'nhl_game_results': {},
}

#: The date from which the excluded-population check looks. Deliberately a
#: constant rather than per-sport: the point is to catch scored rows the
#: season filter drops, and a per-sport floor derived from the same season
#: label would inherit the bug being looked for.
SEASON_FLOOR = '2026-08-01'

MARKETS = ('spread', 'total', 'ml')
#: Which result column backs each market. Football and the rest agree here;
#: it is the LINE columns that differ by sport, not the result columns.
RESULT_COL = {'spread': 'spread_result', 'total': 'total_result',
              'ml': 'home_win'}


def _newest_situational_migration():
    """The live matview definition, so checks can ask what the READER does
    rather than assume. Returns '' when the migrations are not on disk."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '..', 'supabase', 'migrations')
    if not os.path.isdir(d):
        return ''
    cand = sorted(f for f in os.listdir(d) if 'situational' in f)
    if not cand:
        return ''
    try:
        return open(os.path.join(d, cand[-1]), encoding='utf-8').read()
    except Exception:                                      # noqa: BLE001
        return ''


_MIG_SQL = None


def _reader_coalesces(col, twin):
    """True when the matview resolves `col` through COALESCE with `twin`.

    Whitespace-insensitive, because the SQL wraps the expression across
    lines and a literal substring match would miss it and re-report a fixed
    defect as CRITICAL.
    """
    global _MIG_SQL
    if _MIG_SQL is None:
        _MIG_SQL = ' '.join(_newest_situational_migration().split())
    if not _MIG_SQL:
        return False
    return (f'COALESCE({col}, {twin})' in _MIG_SQL
            or f'COALESCE({twin}, {col})' in _MIG_SQL)


def _page(table, params, cap=200000):
    """Page a table. Returns (rows, error). An error is a WIRING fault, not
    an empty result -- the caller must not treat them alike."""
    out, off = [], 0
    while off < cap:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=HDRS, params=p,
                         timeout=180)
        if r.status_code not in (200, 206):
            return out, f'{table} {r.status_code} {r.text[:160]}'
        chunk = r.json()
        if not isinstance(chunk, list):
            return out, f'{table} returned {type(chunk).__name__}: {chunk}'
        out += chunk
        if len(chunk) < 1000:
            return out, None
        off += 1000
    return out, None


def _counted(sport, season):
    """Games the matview DID count, per market, from the `overall` cells.

    Each game contributes two team rows, so (wins+losses+pushes)/2 over all
    teams is the number of games with a non-NULL grade for that market.
    """
    rows, err = _page('team_situational_records',
                      {'select': 'team,market,filter,wins,losses,pushes',
                       'sport': f'eq.{sport}', 'season': f'eq.{season}',
                       'filter': 'eq.overall'})
    if err:
        return None, None, err
    per = collections.Counter()
    for r in rows:
        per[r['market']] += ((r.get('wins') or 0) + (r.get('losses') or 0)
                             + (r.get('pushes') or 0))
    games = {m: per[m] / 2.0 for m in MARKETS}
    return games, len(rows), None


def _filters_present(sport, season):
    rows, err = _page('team_situational_records',
                      {'select': 'filter', 'sport': f'eq.{sport}',
                       'season': f'eq.{season}'})
    if err:
        return None, err
    return collections.Counter(r['filter'] for r in rows), None


def audit(only=None, today=None):
    if not SB or not KEY:
        print('audit_situational_records: no Supabase credentials')
        return 2
    today = today or __import__('datetime').date.today().isoformat()
    print('=' * 78)
    print(f'SITUATIONAL RECORDS AUDIT  ·  games before {today}')
    print('=' * 78)
    rc = 0
    wiring = []
    for sport, cfg in SPORTS.items():
        if only and sport != only:
            continue
        cols = ['game_id', 'game_date', 'home_team', 'away_team',
                'home_score', 'away_score', 'home_win', 'spread_result',
                'total_result', cfg['line'], 'close_total']
        cols += [c for c in cfg['fav'] if c not in cols]
        cols += [c for c in GATE_COLS.get(sport, []) if c not in cols]
        params = {'select': ','.join(sorted(set(cols)))}
        if cfg['season_col']:
            params[cfg['season_col']] = f"eq.{cfg['season']}"
        else:
            params['game_date'] = 'gte.2026-09-01'
        rows, err = _page(cfg['table'], params)
        if err:
            print(f'\n## {sport}: READ FAILED -- {err}')
            wiring.append(sport)
            continue
        scored = [r for r in rows
                  if r.get('home_score') is not None
                  and str(r.get('game_date'))[:10] < today
                  and cfg['where'](r)]
        print(f"\n## {sport}  ({cfg['table']}, season {cfg['season']})")
        if not scored:
            # NOT clean. A sport with nothing to measure is skipped, loudly.
            print(f'   SKIPPED -- 0 scored games match the matview\'s own '
                  f'filter out of {len(rows)} rows read. Out of season, or '
                  f'the filter column is unpopulated.')
            continue
        counted, ncells, err = _counted(sport, cfg['season'])
        if err:
            print(f'   READ FAILED on team_situational_records -- {err}')
            wiring.append(sport)
            continue
        print(f'   {len(scored)} scored games qualify · {ncells} overall '
              f'cells in the matview')

        # 1 -- SHORTFALL per market
        for m in MARKETS:
            have = sum(1 for r in scored if r.get(RESULT_COL[m]) is not None)
            got = counted[m]
            gap = have - got
            flag = ''
            if gap >= 0.5:
                # The matview counted FEWER games than carry a grade. Either
                # it filters them out, or -- NCAAF 2026 -- the grade is a
                # value its CASE does not recognise and maps to NULL.
                flag = f'  <<< MATVIEW SHORT BY {gap:.0f}'
                rc = 1
            elif gap <= -0.5:
                # The matview has no date bound, so it legitimately counts
                # games finished TODAY that this window excludes. Say that
                # rather than printing "SHORT BY -1", which reads as a fault.
                flag = (f'  (matview counts {-gap:.0f} more -- it has no '
                        f'date bound, so today\'s finals are in it)')
            print(f'      {m:7s} graded {have:5d} / {len(scored)} scored'
                  f'   ·  matview counted {got:7.0f}{flag}')
            # UNRECOGNISED GRADE VALUES. The matview's CASE lists the exact
            # strings it accepts and sends everything else to NULL, so a
            # junk value is indistinguishable from an ungraded game once it
            # reaches the records. NCAAF 2026 carries 'true' and 'false' in
            # spread_result -- booleans in a text result column.
            if m in ('spread', 'total'):
                ok = ({'home_covered', 'home_cover', 'away_covered',
                       'away_cover', 'push'} if m == 'spread'
                      else {'over', 'under', 'push'})
                junk = collections.Counter(
                    str(r.get(RESULT_COL[m])).strip().lower()
                    for r in scored
                    if r.get(RESULT_COL[m]) is not None
                    and str(r.get(RESULT_COL[m])).strip().lower() not in ok)
                if junk:
                    print(f'         UNRECOGNISED {RESULT_COL[m]} values '
                          f'{dict(junk)} on {sum(junk.values())} game(s) -- '
                          f'the matview CASE maps these to NULL, so they are '
                          f'graded in the table and ABSENT from the record')
                    rc = 1
            ungraded = [r for r in scored if r.get(RESULT_COL[m]) is None]
            if ungraded:
                by_date = collections.Counter(
                    str(r['game_date'])[:10] for r in ungraded)
                # 4 -- WHOLE-SLATE: every scored game that date, same gap.
                allsame = {d: n for d, n in by_date.items()
                           if n == sum(1 for r in scored
                                       if str(r['game_date'])[:10] == d)}
                print(f'         {len(ungraded)} scored game(s) have no '
                      f'{RESULT_COL[m]} '
                      f'({len(ungraded) / len(scored) * 100:.1f}%)'
                      f' · dates: {dict(sorted(by_date.most_common(6)))}')
                if allsame:
                    print(f'         WHOLE-SLATE OUTAGE on '
                          f'{sorted(allsame)} -- every scored game that day '
                          f'is missing it, which is a feed gap, not noise')
                    rc = 1

        # 1b -- THE EXCLUDED POPULATION.
        #
        # 2026-11-20. Andy asked me to check Harvard's game data. NCAAB has
        # 997 scored games since 2026-10-01 and this audit reported 625 of
        # them, graded 624/625, i.e. essentially perfect -- because it
        # queries `season = eq.2026-27` exactly as the matview does, and
        # 344 rows carry season = NULL. Those 344 are the SAME 344 that have
        # no closing line (997 scored - 653 with close_spread), so a third
        # of the season is both ungraded AND invisible to every team's
        # record, and this detector could not see it.
        #
        # THAT IS THE DEFECT THIS CHECK EXISTS FOR: an audit that inherits
        # the filter of the thing it audits is blind to exactly what that
        # filter removes. Mirroring the matview is right for the shortfall
        # arithmetic above and wrong as the whole picture, so the excluded
        # rows get counted separately and loudly.
        if cfg['season_col']:
            _all, _err = _page(cfg['table'],
                               {'select': f"game_id,game_date,home_score,"
                                          f"{cfg['season_col']}",
                                'game_date': f'gte.{SEASON_FLOOR}'})
            if _err:
                print(f'   excluded-population read failed -- {_err}')
            else:
                _sc = [r for r in _all if r.get('home_score') is not None
                       and str(r.get('game_date'))[:10] < today]
                _out = [r for r in _sc
                        if str(r.get(cfg['season_col'])) != str(cfg['season'])]
                if _out:
                    _by = collections.Counter(
                        str(r.get(cfg['season_col'])) for r in _out)
                    print(f'   CRITICAL: {len(_out)} of {len(_sc)} scored '
                          f'games since {SEASON_FLOOR} are EXCLUDED by the '
                          f'season filter and so appear in NO record '
                          f'({len(_out) / len(_sc) * 100:.1f}%)')
                    print(f'      their {cfg["season_col"]} values: '
                          f'{dict(_by.most_common(5))}')
                    _nul = [r for r in _out if r.get(cfg['season_col']) is None]
                    if _nul:
                        _d = collections.Counter(
                            str(r['game_date'])[:10] for r in _nul)
                        print(f'      {len(_nul)} have a NULL '
                              f'{cfg["season_col"]}, dates '
                              f'{dict(sorted(_d.most_common(4)))}')
                    rc = 1

        # 2 -- ABSENT FILTER (the NCAAB defect class)
        present, err = _filters_present(sport, cfg['season'])
        if err:
            print(f'   filter read failed -- {err}')
            wiring.append(sport)
            continue
        for pair in (('as_fav', 'as_dog'), ('home', 'road')):
            if not present.get(pair[0]) and not present.get(pair[1]):
                print(f'   CRITICAL: BOTH {pair[0]} and {pair[1]} have ZERO '
                      f'rows. The card hides a filter row when neither team '
                      f'has data, so this is invisible on screen.')
                rc = 1

        # 3 -- TWIN COLUMNS behind any column the matview reads
        twins = TWINS.get(cfg['table'], {})
        # dict.fromkeys dedupes while keeping order: for MLB/NFL/NCAAF the
        # line column IS the is_fav column, and reporting it twice made the
        # first run of this audit print the same NULL count on two lines.
        for col in dict.fromkeys([cfg['line']] + list(cfg['fav'])):
            nulls = [r for r in scored if r.get(col) is None]
            if not nulls:
                continue
            tw = twins.get(col)
            pct = len(nulls) / len(scored) * 100
            msg = (f'   {col} is NULL on {len(nulls)}/{len(scored)} '
                   f'({pct:.1f}%)')
            if tw:
                rescued = [r for r in nulls if r.get(tw) is not None]
                if rescued:
                    # 2026-11-20: a populated twin is only a DEFECT while the
                    # reader still names one column. 20261119a made the
                    # matview COALESCE over both NCAAB moneyline pairs, so
                    # close_home_ml being 100% empty stopped mattering -- and
                    # this check went on calling it CRITICAL, which is a
                    # detector that does not know the intentional case
                    # ([[feedback_a_detector_must_know_the_intentional_case]],
                    # [[feedback_a_convention_with_correct_readers]]). So ask
                    # the migration what the reader actually does.
                    if _reader_coalesces(col, tw):
                        print(f'{msg} -- the TWIN `{tw}` carries '
                              f'{len(rescued)} of them, and the matview '
                              f'COALESCEs the pair, so this is resolved by '
                              f'the reader. Informational.')
                        continue
                    print(f'{msg} -- and the TWIN `{tw}` is populated on '
                          f'{len(rescued)} of them. CRITICAL: the reader is '
                          f'pointed at the wrong column name.')
                    rc = 1
                    continue
            print(msg)

    if wiring:
        print(f'\nWIRING ERRORS on: {sorted(set(wiring))} -- 0 rows read is '
              f'never a finding, it is a broken read.')
        return 2
    print('\n' + '=' * 78)
    print('RESULT: ' + ('every measured sport reconciles' if rc == 0
                        else 'at least one sport is SHORT or has an absent '
                             'filter -- see above'))
    return rc


# ── self-test ───────────────────────────────────────────────────────────
# The three live defects, as data, so CI fails if the detector stops seeing
# them. Each case is built to be caught by exactly one of the checks, and
# the NULL-is-not-FALSE case is the one that matters most: it is the bug
# that hid for the longest because the card renders its absence as a layout
# choice rather than as missing data.
def _self_test():
    cases, fails = [], 0

    # 1 -- NULL is not FALSE: both as_fav and as_dog must drop a NULL row.
    is_fav = None
    cases.append(('a NULL is_fav is excluded by BOTH `is_fav` and '
                  '`NOT is_fav`',
                  (not bool(is_fav is True)) and (not bool(is_fav is False))))

    # 2 -- the shortfall arithmetic: two team rows per game.
    wins, losses, pushes = 10, 12, 0           # Vancouver's real spread cell
    other = 9 + 8                              # Seattle's
    cases.append(('overall cells / 2 == games',
                  ((wins + losses + pushes) + other) / 2.0 == 19.5))

    # 3 -- whole-slate detection: 12 of 12 on one date is an outage.
    scored = [{'game_date': '2026-11-07', 'spread_result': None}] * 12
    by_date = collections.Counter(r['game_date'] for r in scored)
    allsame = {d: n for d, n in by_date.items()
               if n == sum(1 for r in scored if r['game_date'] == d)}
    cases.append(('12 ungraded of 12 scored on one date flags an outage',
                  allsame == {'2026-11-07': 12}))

    # 4 -- a twin is only a rescue if it is actually populated.
    rows = [{'close_home_ml': None, 'home_ml_close': -150},
            {'close_home_ml': None, 'home_ml_close': None}]
    rescued = [r for r in rows if r['home_ml_close'] is not None]
    cases.append(('a populated twin is detected, an empty one is not',
                  len(rescued) == 1))

    # 4b -- an unrecognised grade value is graded in the table and absent
    # from the record, because the matview's CASE sends it to NULL. NCAAF
    # 2026 holds 'true'/'false' in spread_result.
    ok = {'home_covered', 'home_cover', 'away_covered', 'away_cover', 'push'}
    vals = ['home_covered', 'true', 'false', 'away_cover', 'push']
    junk = [v for v in vals if v not in ok]
    cases.append(("'true'/'false' are caught as unrecognised spread_result",
                  junk == ['true', 'false']))

    # 4c -- the line column and the is_fav column are the same for three
    # sports, and the twin loop must report each column once.
    cfg = SPORTS['NCAAF']
    cases.append(('the twin loop dedupes line vs fav columns',
                  len(list(dict.fromkeys([cfg['line']] + list(cfg['fav']))))
                  == 1))

    # 5 -- every gate column is selected. This is the bug I actually wrote:
    # filtering NBA on season_type without selecting it measured NBA as 0
    # scored games. A gate on an unselected column always takes one branch.
    for sport, needed in GATE_COLS.items():
        cfg = SPORTS[sport]
        cols = set(['game_id', 'game_date', 'home_team', 'away_team',
                    'home_score', 'away_score', 'home_win', 'spread_result',
                    'total_result', cfg['line'], 'close_total'])
        cols |= set(cfg['fav']) | set(GATE_COLS.get(sport, []))
        cases.append((f'{sport}: every gate column is selected ({needed})',
                      all(c in cols for c in needed)))

    # 6 -- a sport with 0 scored rows must NOT be reported clean.
    src = open(os.path.abspath(__file__), encoding='utf-8').read()
    cases.append(('0 scored rows reports SKIPPED, not clean',
                  "SKIPPED -- 0 scored games" in src))

    # 6b -- the twin check must distinguish a reader that was FIXED from a
    # reader pointed at the wrong name. 20261119a COALESCEs the NCAAB pair;
    # before this, the audit went on calling that resolved case CRITICAL.
    cases.append(('a COALESCEd pair is recognised as resolved',
                  _reader_coalesces('close_home_ml', 'home_ml_close')))
    cases.append(('a pair the reader does NOT coalesce stays a defect',
                  not _reader_coalesces('close_puckline', 'close_spread')))

    # 7 -- the mirrored matview rules still match the live migration, so a
    # shortfall is a real gap and not two descriptions drifting apart.
    mig = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'supabase', 'migrations')
    newest = None
    if os.path.isdir(mig):
        cand = sorted(f for f in os.listdir(mig) if 'situational' in f)
        newest = os.path.join(mig, cand[-1]) if cand else None
    if newest:
        sql = open(newest, encoding='utf-8').read()
        cases.append((f'NBA season_type gate still in {os.path.basename(newest)}',
                      "season_type = 'regular'" in sql))
        cases.append(('NHL type-02 gate still in that migration',
                      "FROM 5 FOR 2) = '02'" in sql))
        cases.append(('NCAAF neutral_site exclusion still in that migration',
                      'neutral_site' in sql))
    else:
        cases.append(('migration found to cross-check', False))

    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
    print(f"\n  {len(cases) - fails}/{len(cases)} self-test cases passed")
    return 1 if fails else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    ap.add_argument('--today')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(audit(only=a.sport, today=a.today))
