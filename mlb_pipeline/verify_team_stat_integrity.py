#!/usr/bin/env python3
"""verify_team_stat_integrity.py - are the displayed football stats RIGHT?

== WHY THIS EXISTS ==
2026-10-21. Spot-checking displayed stats was easy for NBA and NHL because
those tables carry wins and losses, and a W-L can be cross-validated against
graded games (30 of 30 matched over 1,397 NBA games). NFL and NCAAF carry no
such column: nfl_team_stats is pass/rush/EPA/FG counters and nothing in it
can be checked against a score.

So "have we verified accuracy of displayed stats" had no answer for the two
football sports, and the honest position was that we had not.

== WHAT CAN BE CHECKED WITHOUT A SECOND FEED ==
Two things, and neither needs an external source.

  1. THE DENOMINATOR. `games` must equal the number of completed games that
     team actually played in *_game_results for the same season and season
     type. This is the check worth having, because almost every stat defect
     this project has found was a denominator defect rather than a numerator
     one: preseason folded into a season filter (the season value is
     IDENTICAL, so `season = X` does not exclude it), a season counted
     twice, or a table that stopped updating while the schedule went on. A
     wrong `games` silently rescales every per-game rate derived from it.

  2. INTERNAL INVARIANTS. Completions cannot exceed attempts. Field goals
     made cannot exceed attempted. A stored percentage must reproduce from
     its own numerator and denominator. Conversions cannot exceed chances.
     Counters cannot be negative. These catch the mis-mapped column, which
     is the defect that put NFL off-EPA-per-PLAY beside def-EPA-per-GAME -
     two numbers ~30x apart under one label.

What this CANNOT check is whether 4,312 passing yards is the real figure.
That needs a second feed, and the summary line says so rather than leaving
it as a caveat buried in a comment.

Usage:
    python verify_team_stat_integrity.py                  # all sports
    python verify_team_stat_integrity.py --sport NFL
    python verify_team_stat_integrity.py --season 2026
"""
from __future__ import annotations
import argparse, os, sys
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: Per sport: the stats table, the results table, and how each names a
#: regular-season row. The two tables do NOT use the same column name or the
#: same vocabulary, which is precisely the difference that makes one shared
#: "exclude preseason" filter wrong in one of the two places.
SPORTS = {
    'NFL': dict(stats='nfl_team_stats', results='nfl_game_results',
                stats_type_col='season_type', results_type_col='game_type',
                reg=('REG', 'regular', 'regular season')),
    # scope_col: a column that is populated ONLY for the population the
    # results table actually covers. SP+ is published for FBS teams only, so
    # sp_overall IS NULL is the non-FBS flag - no hardcoded team list, and it
    # stays correct as teams move divisions.
    'NCAAF': dict(stats='ncaaf_team_stats', results='ncaaf_game_results',
                  stats_type_col='season_type', results_type_col='season_type',
                  reg=('regular', 'reg', 'regular season'),
                  scope_col='sp_overall'),
}

#: Rows that are not teams. ncaaf_team_stats carries a 'nationalAverages'
#: row - the FBS mean, used as a comparison baseline - with sp_overall
#: populated and games NULL, so every check treats it as a team with no
#: games. Named explicitly rather than guessed at from the shape, because a
#: real team with a NULL games count is a defect worth seeing.
SENTINEL_TEAMS = {'nationalaverages', 'national averages', 'average',
                  'fbs average', 'all'}

#: (numerator, denominator) - the first can never exceed the second.
LE_PAIRS = [
    ('pass_completions', 'pass_attempts'),
    ('fg_made', 'fg_att'),
    ('third_down_conv', 'third_downs'),
    ('fourth_down_conv', 'fourth_downs'),
]

#: (pct_column, numerator, denominator, tolerance). A stored rate must
#: reproduce from its own parts. The scale (0-1 vs 0-100) is resolved per
#: row rather than assumed, because this project has shipped both.
PCT_TRIPLES = [('fg_pct', 'fg_made', 'fg_att', 0.02)]

#: Counters that cannot be negative. A negative EPA is fine and expected; a
#: negative attempt count is a parse failure.
NONNEG = ('games', 'pass_attempts', 'pass_completions', 'pass_yards',
          'pass_tds', 'pass_ints', 'rush_attempts', 'rush_yards', 'rush_tds',
          'fg_made', 'fg_att', 'penalties', 'penalty_yards', 'def_sacks',
          'def_ints', 'third_downs', 'fourth_downs', 'turnovers')


def page(tbl, params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H,
                         params={**params, 'limit': '1000',
                                 'offset': str(off)}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} HTTP {r.status_code}: {r.text[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_sport(sport, season):
    cfg = SPORTS[sport]
    print('')
    print(f'=== {sport} - {cfg["stats"]} ===')

    sp = {'select': '*'}
    if season:
        sp['season'] = f'eq.{season}'
    stats = page(cfg['stats'], sp)
    if not stats:
        where = f' for season {season}' if season else ''
        print(f'  0 rows in {cfg["stats"]}{where} - WIRING ERROR, not a finding')
        return 1

    seasons = sorted({r.get('season') for r in stats
                      if r.get('season') is not None})
    print(f'  {len(stats)} stat row(s) across season(s) {seasons}')

    # -- the denominator ---------------------------------------------------
    rp = {'select': 'home_team,away_team,home_score,away_score,season,'
                    + cfg['results_type_col']}
    if season:
        rp['season'] = f'eq.{season}'
    results = page(cfg['results'], rp)
    reg_lower = {str(x).lower() for x in cfg['reg']}
    played = {}
    skipped_type = 0
    for g in results:
        if str(g.get(cfg['results_type_col']) or '').lower() not in reg_lower:
            skipped_type += 1
            continue
        if g.get('home_score') is None or g.get('away_score') is None:
            continue
        for t in (g.get('home_team'), g.get('away_team')):
            if t:
                k = (g.get('season'), str(t))
                played[k] = played.get(k, 0) + 1
    print(f'  {len(results)} result row(s); {skipped_type} excluded as '
          f'non-regular by {cfg["results_type_col"]}; '
          f'{len(played)} (season, team) pairs played')

    problems = 0
    # Three buckets, not one count. Run unclassified on NCAAF this was "122
    # problems", which is unusable - 128 of those rows were FCS schools whose
    # full season sits in the stats table while the results table only carries
    # their one FBS crossover game. That is a LIMIT OF THE CHECK, not a defect,
    # and burying six real name mismatches inside it is how a finding gets
    # ignored.
    #
    # `scope_col` separates the two populations without a hardcoded team list:
    # SP+ is published for FBS only, so a NULL sp_overall IS the non-FBS flag.
    # Where a sport has no such column every row counts as in-scope.
    lag = []        # off by one: the stats feed is a week behind
    name_gap = []   # ZERO overlap: the two feeds spell this team differently
    coverage = []   # in both, but the results table is missing games
    out_of_scope = 0
    scope_col = cfg.get('scope_col')
    sentinels = 0
    for r in stats:
        if str(r.get(cfg['stats_type_col']) or '').lower() not in reg_lower:
            continue
        if str(r.get('team') or '').strip().lower() in SENTINEL_TEAMS:
            sentinels += 1
            continue
        in_scope = (scope_col is None) or (r.get(scope_col) is not None)
        key = (r.get('season'), str(r.get('team')))
        got = _num(r.get('games'))
        want = played.get(key, 0)
        if not in_scope:
            out_of_scope += 1
            continue
        if got is None:
            problems += 1
            print(f'  ** {key[1]}: games is NULL')
            continue
        d = int(got) - want
        if d == 0:
            continue
        if want == 0:
            # Not "no games played" - a team in the stats table with a full
            # season and NOT ONE row in the results table is two feeds
            # disagreeing about its name.
            name_gap.append((key[1], int(got)))
        elif abs(d) <= 1:
            lag.append((key[1], int(got), want))
        else:
            coverage.append((key[1], int(got), want))

    if sentinels:
        print(f'  {sentinels} sentinel row(s) skipped (not teams - e.g. '
              f'ncaaf_team_stats.nationalAverages, the FBS mean)')
    if out_of_scope:
        print(f'  {out_of_scope} row(s) out of scope ({scope_col} IS NULL - '
              f'non-FBS); the results table only carries their crossover '
              f'games, so its count is not their denominator')

    if name_gap:
        problems += len(name_gap)
        print(f'  ** NAME MISMATCH on {len(name_gap)} in-scope team(s) - a '
              f'full season of stats and ZERO rows in {cfg["results"]}, so '
              f'these stats can never join their games:')
        for team, got in sorted(name_gap):
            print(f'       {team:28s} stats.games={got}  results rows=0')

    if coverage:
        problems += len(coverage)
        print(f'  ** {len(coverage)} in-scope team(s) missing 2+ games from '
              f'{cfg["results"]}:')
        for team, got, want in sorted(coverage):
            print(f'       {team:28s} stats={got}  completed={want}')

    if lag:
        print(f'  LAG (not counted as a defect): {len(lag)} team(s) off by '
              f'one - the stats feed has not ingested the most recent week. '
              f'Per-game rates for these are on a stale denominator.')
        for team, got, want in sorted(lag)[:6]:
            print(f'       {team:28s} stats={got}  completed={want}')
        if len(lag) > 6:
            print(f'       ... and {len(lag) - 6} more')

    if not (name_gap or coverage or lag):
        print('  games: matches the completed-game count on every in-scope '
              'team-season')

    # -- internal invariants ----------------------------------------------
    present = set(stats[0].keys())
    for num, den in LE_PAIRS:
        if num not in present or den not in present:
            continue
        bad = [r for r in stats
               if _num(r.get(num)) is not None
               and _num(r.get(den)) is not None
               and _num(r.get(num)) > _num(r.get(den))]
        if bad:
            problems += len(bad)
            detail = ', '.join(f'{r.get("team")} {r.get(num)}/{r.get(den)}'
                               for r in bad[:5])
            print(f'  ** {num} > {den} on {len(bad)} row(s): {detail}')

    for col, num, den, tol in PCT_TRIPLES:
        if not {col, num, den} <= present:
            continue
        bad = []
        for r in stats:
            p, n, d = _num(r.get(col)), _num(r.get(num)), _num(r.get(den))
            if p is None or n is None or not d:
                continue
            expect = n / d
            if abs(p - expect) > tol and abs(p - expect * 100) > tol * 100:
                bad.append((r.get('team'), p, round(expect, 4), n, d))
        if bad:
            problems += len(bad)
            print(f'  ** {col} does not equal {num}/{den} on {len(bad)} row(s):')
            for team, p, e, n, d in bad[:5]:
                print(f'       {team:28s} stored={p}  {n}/{d}={e}')

    neg = [(r.get('team'), c, r.get(c)) for r in stats for c in NONNEG
           if c in present and (_num(r.get(c)) or 0) < 0]
    if neg:
        problems += len(neg)
        print(f'  ** negative counter on {len(neg)} field(s): {neg[:5]}')

    # -- freshness ---------------------------------------------------------
    stamps = [str(r.get('updated_at')) for r in stats if r.get('updated_at')]
    if stamps:
        newest = max(stamps)
        try:
            age = (dt.datetime.now(dt.timezone.utc)
                   - dt.datetime.fromisoformat(newest.replace('Z', '+00:00')))
            print(f'  freshest updated_at: {newest[:19]} '
                  f'({age.total_seconds() / 3600:.1f}h old)')
        except Exception:                                      # noqa: BLE001
            print(f'  freshest updated_at: {newest}')
    else:
        print('  no updated_at on any row - cannot judge freshness')

    if problems == 0:
        print(f'  {sport}: PASS on denominator + invariants. NOTE: stat '
              f'VALUES remain unverified - that needs a second feed.')
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append', help='NFL / NCAAF (default: all)')
    ap.add_argument('--season', type=int)
    a = ap.parse_args()
    sports = [s.upper() for s in (a.sport or list(SPORTS))]
    bad = [s for s in sports if s not in SPORTS]
    if bad:
        print(f'unsupported: {bad}; known: {list(SPORTS)}', file=sys.stderr)
        return 2
    total = 0
    for s in sports:
        try:
            total += check_sport(s, a.season)
        except Exception as e:                                 # noqa: BLE001
            print(f'  {s}: CHECK FAILED ({type(e).__name__}: {e}) - treat as '
                  f'UNVERIFIED, not as a pass', file=sys.stderr)
            total += 1
    print('')
    print('PASS' if total == 0 else f'{total} problem(s)')
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
