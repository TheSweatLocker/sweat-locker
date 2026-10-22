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

#: Per sport, a table that bridges the two name spaces, and its two columns.
#: WITHOUT THIS THE CHECK CRIES WOLF.
#:
#: 2026-10-22. The first version of this verifier compared
#: ncaaf_team_stats.team directly against ncaaf_game_results.home/away_team
#: and reported five FBS teams with "a full season of stats and ZERO rows in
#: the results table" - App State/Appalachian State, Hawai'i/Hawaii, San José
#: State/San Jose State, UConn/Connecticut, UL Monroe/Louisiana Monroe. I
#: wrote that their game cards "can show no team stats at all."
#:
#: THAT WAS WRONG, and the measurement that settles it is the fill rate on
#: ncaaf_game_context - which is what the cards actually read - split on the
#: date the bridge was built:
#:
#:     the five, before 2026-09-10    sp_overall  0/9    ( 0.0%)
#:     the five, after  2026-09-10               21/25   (84.0%)
#:     control,  after  2026-09-10              761/841  (90.5%)
#:
#: So `ncaaf_team_name_aliases` (migration 20260910e) already bridges them
#: and has since 09-10; the two name spaces differ by design and something
#: else reconciles them. A checker that calls a BRIDGED difference a defect
#: is worse than no checker: it would have reported five fake findings every
#: night in ncaaf_pipeline.yml, and the real ones - two unresolved FBS games
#: and 25 teams a week stale - would have been read as more of the same.
#:
#: NCAAF has FIVE name-resolution layers (this table, ncaaf_team_aliases,
#: compute_margin_strength.NCAAF_ALIASES, resolve_ncaaf_results._NCAAF_ALIASES,
#: and the client's own fuzzy matcher). This resolves through the one that
#: owns the stats-to-context join, because that is the join being checked.
ALIAS_BRIDGE = {
    'NCAAF': dict(table='ncaaf_team_name_aliases',
                  # ctx/results spelling -> the stats table's spelling
                  from_col='ctx_name', to_col='cfbd_canonical'),
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


def _fold(s) -> str:
    """Case/space/diacritic-insensitive key for a team name.

    'Hawai'i' vs 'Hawaii' and 'San José State' vs 'San Jose State' are the
    same school written by two feeds, and they are the two of the five that
    no alias ROW can sensibly carry - they are not synonyms, they are the
    same string with an accent and an apostrophe. Folding here means the
    alias table only has to hold genuine synonyms (App State/Appalachian
    State), which is what it is for.
    """
    import unicodedata
    n = unicodedata.normalize('NFKD', str(s or ''))
    n = ''.join(c for c in n if not unicodedata.combining(c))
    for ch in ("'", '’', '.', '-'):
        n = n.replace(ch, '')
    return ' '.join(n.lower().split())


def load_alias_bridge(sport: str) -> dict:
    """{folded results/ctx name -> folded stats name}, or {} if none.

    A missing table is NOT fatal - it means this sport has no bridge, and
    then a name difference really is a finding. A failed REQUEST is
    different: returning {} there would turn a transient error into five
    fake defects, so it raises and the caller reports the sport as
    unverified.
    """
    cfg = ALIAS_BRIDGE.get(sport)
    if not cfg:
        return {}
    r = requests.get(f'{SB}/rest/v1/{cfg["table"]}', headers=H,
                     params={'select': f'{cfg["from_col"]},{cfg["to_col"]}',
                             'limit': '2000'}, timeout=30)
    if r.status_code == 404:
        print(f'  note: no {cfg["table"]} - name differences are real here')
        return {}
    if r.status_code != 200:
        raise RuntimeError(f'{cfg["table"]} HTTP {r.status_code}: '
                           f'{r.text[:160]}')
    out = {}
    for row in r.json() or []:
        a, b = row.get(cfg['from_col']), row.get(cfg['to_col'])
        if a and b:
            out[_fold(a)] = _fold(b)
    print(f'  alias bridge: {len(out)} pair(s) from {cfg["table"]}')
    return out


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
    bridge = load_alias_bridge(sport)
    # game_date is in the select because the duplicate check groups on it.
    # Without it every row grouped under the string 'None' and the whole
    # season collapsed into one bucket, which reported two different dates'
    # Georgia/Alabama games as a duplicate pair. The giveaway was the date
    # printing as None in the output - a grouping key that is constant is a
    # wiring error, not a finding.
    rp = {'select': 'game_date,home_team,away_team,home_score,away_score,'
                    'season,' + cfg['results_type_col']}
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
                # Key on the FOLDED, alias-resolved name, in the stats
                # table's space. Both sides of the comparison end up in one
                # vocabulary, so a difference the project deliberately
                # bridges elsewhere no longer reads as a missing team.
                k = (g.get('season'), bridge.get(_fold(t), _fold(t)))
                played[k] = played.get(k, 0) + 1
    print(f'  {len(results)} result row(s); {skipped_type} excluded as '
          f'non-regular by {cfg["results_type_col"]}; '
          f'{len(played)} (season, team) pairs played')

    # -- duplicate game rows ----------------------------------------------
    # ══ 2026-10-22 · THE GAME_ID IS BUILT OUT OF THE TEAM NAMES ══
    # ncaaf_game_results ids look like
    # 'ncaaf_20260903_Akron_Wake Forest', so the SAME GAME gets a SECOND
    # ROW the moment a feed sends a different spelling - that row's id
    # differs, so the upsert cannot collapse it. Measured on 2026: 19 true
    # duplicates, and they come in three flavours, in rising order of harm:
    #
    #   spelling      'Akron @ Wake Forest' and 'Akron @ Wake Forest Demon
    #                 Deacons'. Both scored. Inflates a team's game count,
    #                 which is the denominator of every per-game rate.
    #   half-scored   'UMass @ Rutgers' NULL alongside 'Massachusetts @
    #                 Rutgers' 21-37. The NULL twin reads as an ungraded
    #                 game forever - this is what made 8 games look
    #                 unresolved on 10-21.
    #   INVERTED      'Oklahoma @ Texas' NULL alongside 'Texas @ Oklahoma'
    #                 27-24. A neutral-site game (Red River, in Dallas) that
    #                 two feeds disagree about, so home and away are
    #                 SWAPPED between the rows. This is the dangerous one: a
    #                 pick graded against the wrong orientation inverts the
    #                 side.
    #
    # Checked by (date, unordered team PAIR) so an inversion is caught, not
    # by (date, home) which would miss exactly the harmful case.
    pair_groups = {}
    for g in results:
        h, a = g.get('home_team'), g.get('away_team')
        if not (h and a):
            continue
        hk = bridge.get(_fold(h), _fold(h))
        ak = bridge.get(_fold(a), _fold(a))
        k = (str(g.get('game_date'))[:10], tuple(sorted((hk, ak))))
        pair_groups.setdefault(k, []).append(g)
    dupes = {k: v for k, v in pair_groups.items() if len(v) > 1}
    if dupes:
        inverted, half, plain = [], [], []
        for k, v in sorted(dupes.items()):
            homes = {bridge.get(_fold(x.get('home_team')),
                                _fold(x.get('home_team'))) for x in v}
            scored = [x for x in v if x.get('home_score') is not None]
            if len(homes) > 1:
                inverted.append((k, v))
            elif 0 < len(scored) < len(v):
                half.append((k, v))
            else:
                plain.append((k, v))
        print(f'  ** {len(dupes)} duplicate game row group(s) in '
              f'{cfg["results"]} - the game_id is built from team NAMES, so '
              f'a second spelling creates a second row:')
        for label, group in (('HOME/AWAY INVERTED', inverted),
                             ('one twin unscored', half),
                             ('both scored', plain)):
            if not group:
                continue
            print(f'     {len(group)} {label}:')
            for k, v in group[:6]:
                print(f'       {k[0]}  {" / ".join(sorted(k[1]))}')
                for x in v:
                    print(f'          {str(x.get("away_team"))[:26]:26s} @ '
                          f'{str(x.get("home_team"))[:26]:26s} '
                          f'{x.get("home_score")}-{x.get("away_score")}')
            if len(group) > 6:
                print(f'       ... and {len(group) - 6} more')
        # Inversions are counted as problems; the other two are reported but
        # not counted, because they inflate a denominator rather than
        # threatening to grade a bet the wrong way round.
        problems_dupes = len(inverted)
        if inverted:
            print('     -> an INVERTED pair can grade a side backwards. '
                  'Check receipts on those game_ids before trusting them.')
    else:
        problems_dupes = 0

    problems = problems_dupes
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
    inflated = []   # results has MORE than stats -> duplicate rows
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
        key = (r.get('season'), _fold(r.get('team')))
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
        elif d > 0:
            # stats claims MORE games than the results table has -> the
            # results table is missing games.
            coverage.append((key[1], int(got), want))
        else:
            # 2026-10-22: the results table has MORE completed games than
            # the stats table claims, which cannot be a coverage gap - you
            # cannot play more games than you played. It is the duplicate
            # rows above inflating the count: Virginia Tech reads 10
            # completed against 7 because 'VMI Keydets @ Virginia Tech' and
            # 'VMI @ Virginia Tech' are the same game under two spellings.
            # Reported separately and pointed at the right table, because
            # calling it "missing games from the results table" sent the
            # reader to look for the opposite problem.
            inflated.append((key[1], int(got), want))

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

    if inflated:
        print(f'  {len(inflated)} in-scope team(s) whose COMPLETED count '
              f'exceeds stats.games by 2+ - not a coverage gap, these are '
              f'the duplicate rows above inflating the count:')
        for team, got, want in sorted(inflated):
            print(f'       {team:28s} stats={got}  counted={want}  '
                  f'(+{want - got} duplicate row(s))')

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
