"""Is every SOS/SOR ranking valid, complete, and reaching the game row?

ANDY 2026-10-22: "I also wnat to do a spot check on all sports that are live
current teams SOR and SOS, verify calculations are correct and ranked
correctly".

The spot check came back clean on all six sports, so this script exists to
keep it that way without anyone re-deriving it by hand. It lives apart from
verify_team_stat_integrity.py because it needs neither a stats table nor a
results-table join to do its main job: team_computed_stats is the single
shared home of the ranked values and the check is identical for all six
sports, while that script is built around two sports' per-sport schemas.

Runs in nightly_cross_sport.sh immediately after populate_game_sor.py, so it
grades the numbers that run just wrote.

╔══ THE TRAP THIS SCRIPT EXISTS TO NOT FALL INTO ═══════════════════════════╗
Doing this by hand I reported a defect THREE TIMES and all three were my own
measurement error - the same error each time: I POOLED GAME ROWS ACROSS
DATES.

    attempt 1  "28 of 32 teams carry more than one (value,rank) pair" and
               "** NOT MONOTONIC **", over game_date >= 2026-10-15.
    attempt 2  called the rank span 1..137 against 129 teams a count
               mismatch, same pooled window.
    attempt 3  "duplicate rank 72 - Marshall 1.808 vs Sam Houston 1.796",
               over 2026-10-20..2026-11-05.

None of the three was a bug. populate_game_sor.py writes ONLY games on/after
today and refuses earlier ones, because team_computed_stats is a CURRENT
snapshot and stamping it onto a played game would put post-game information
on a pre-game row (project_rolling_stats_leak_trap_929). So a past game row
holds the rating AS OF ITS OWN KICKOFF - by design, and the entire point.
Marshall was #72 when its 10-20 row was written; Sam Houston was #72 when its
10-21 row was written. Both rows are correct. Comparing them to each other is
comparing two different weeks and calling the difference corruption.

THEREFORE: the ranking is validated INSIDE ONE SNAPSHOT (the source table,
which carries exactly one vintage), and game rows are compared to the source
ONLY for game_date >= today ET. Past rows are never compared to anything.
╚═══════════════════════════════════════════════════════════════════════════╝

╔══ THE SECOND TRAP: ROUNDING TIES ARE NOT ORDER VIOLATIONS ════════════════╗
compute_margin_strength assigns rank from the UNROUNDED rating
(`sorted(vals, key=lambda x: -vals[x])`, then `enumerate(order, 1)`) but
stores `raw_value = round(vals[t], 3)`. Two teams 0.0004 apart therefore
store the SAME value at adjacent ranks:

    NCAAF sos         Florida 3.944 #18   LSU 3.944 #19
    NBA   sos         Sacramento 0.322 #11   Atlanta 0.322 #12
    NHL   sor_margin  Vegas 0.469 #4   Carolina 0.469 #5
    MLB   sos         Tampa Bay -0.004 #19   Arizona -0.004 #20

A naive monotonicity test sorts by the stored value, breaks those ties the
other way, and reports 1-2 violations per surface on five of six sports. All
of them are ties. An order violation counts ONLY when the two stored values
actually differ.
╚═══════════════════════════════════════════════════════════════════════════╝

WHAT THIS CATCHES THAT THE VALUE CHECKS DO NOT. A ranking can be perfectly
contiguous, tie-free and monotonic and still be wrong, because the fit ran on
a short schedule. nightly_cross_sport.sh records the precedent in its own
comments: "a deleted opponent inflated Miami (OH) to SOS rank 2 of 137 with
nothing to flag it until Andy eyeballed it." Nothing about ranks 1..137 with
no duplicates would have shown that. So this also counts each rated team's
completed games and flags any team rated off materially fewer games than its
league's median - the shape that defect takes.

CLI
    python verify_sor_sos_integrity.py                  # all sports
    python verify_sor_sos_integrity.py --sport NBA --sport NHL
    python verify_sor_sos_integrity.py --warn-only      # always exit 0
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import statistics
import sys

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: The four ranked surfaces compute_margin_strength writes per sport. `sor`
#: and `sos` are ALIASES of the margin pair, written under the keys
#: GameDetailV2 already hardcodes because there is no OTA path; both pairs are
#: checked because a divergence between an alias and its source is itself a
#: defect.
SURFACES = ('sos', 'sor', 'sos_margin', 'sor_margin')

SPORTS = ('NFL', 'NCAAF', 'NBA', 'NHL', 'NCAAB', 'MLB')

#: Only these two sports have home/away sor+sos columns on their game
#: context, so only these two can have a game row that disagrees with the
#: source. The other four compute the values and never join them to a game:
#: the engine cannot see them and no signal can reference them. Reported as
#: INFO, not a failure - that is a missing migration, not corrupt data.
CTX_WITH_SOR = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}

RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results',
           'NCAAB': 'ncaab_game_results', 'MLB': 'mlb_game_results'}

#: A rated team with fewer than this fraction of its league's MEDIAN completed
#: games is rated off a partial schedule. Not a hard count, because the
#: fraction has to work for a 6-game NCAAF week and a 100-game MLB season
#: alike, and because byes and postponements make an absolute floor wrong.
SHORT_SCHEDULE_FRAC = 0.60

#: Below this many median games the completeness check cannot say anything -
#: every team is near zero in the first week of a season, so the ratio is
#: noise. NCAAB in October sits here by design.
MIN_MEDIAN_GAMES = 4

#: A ranking older than this is stale regardless of whether it is internally
#: valid. The nightly runs daily, so anything past two cycles has been failing
#: quietly (feedback_green_step_that_writes_nothing).
MAX_AGE_HOURS = 48


def page(tbl, params, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=q,
                         timeout=180)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} {r.status_code} {r.text[:200]}')
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def et_today():
    """ET calendar date. zoneinfo, NOT a fixed offset - ET is -4 only until
    2026-11-01, and a hardcoded -4 during EST shifts the 'upcoming games'
    cutoff by a full day for part of every night (B122)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:                                          # noqa: BLE001
        return (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(hours=5)).strftime('%Y-%m-%d')


def _age_hours(stamp: str):
    """Hours since an ISO timestamp, or None if it will not parse."""
    s = (stamp or '').replace('Z', '+00:00')
    for cut in (None, 26, 19):
        try:
            d = dt.datetime.fromisoformat(s if cut is None else s[:cut])
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return (dt.datetime.now(dt.timezone.utc) - d).total_seconds() / 3600
        except ValueError:
            continue
    return None


def season_start(sport: str):
    """The real season boundary from sport_registry.

    A `season` filter does NOT exclude preseason: preseason games carry the
    SAME season value, so counting games by season alone inflates every
    team's game count with exhibition results
    (feedback_season_filter_does_not_exclude_preseason).
    """
    try:
        rows = page('sport_registry', {'select': '*', 'sport': f'eq.{sport}'})
    except RuntimeError:
        return None
    for r in rows:
        v = r.get('season_start')
        if v:
            return str(v)[:10]
    return None


def check_ranking(sport, rows) -> list[str]:
    """Every surface's ranking, validated inside the one source snapshot."""
    problems = []
    for key in SURFACES:
        sub = [r for r in rows if r.get('stat_key') == key]
        if not sub:
            print(f'   {key:<11} ABSENT - nothing writes this surface for '
                  f'{sport}')
            problems.append(f'{sport}/{key} absent')
            continue
        teams = collections.Counter(r.get('team') for r in sub)
        ranks = [int(r['rank']) for r in sub if r.get('rank') is not None]
        issues = []

        if len(ranks) != len(sub):
            issues.append(f'{len(sub) - len(ranks)} row(s) with a NULL rank')
        dup_t = {t: n for t, n in teams.items() if n > 1}
        if dup_t:
            # The documented shape: a team's rating changed but its old row
            # survived, so two rows compete and every rank below them shifts.
            issues.append(f'{len(dup_t)} team(s) with >1 row {list(dup_t)[:4]} '
                          f'- a surviving stale row pushes every rank below it')
        dup_r = len(ranks) - len(set(ranks))
        if dup_r:
            issues.append(f'{dup_r} duplicate rank(s)')
        if ranks and (min(ranks) != 1 or max(ranks) != len(teams)):
            issues.append(f'rank span {min(ranks)}..{max(ranks)} is not '
                          f'1..{len(teams)} over {len(teams)} rated teams')

        ls = {r.get('league_size') for r in sub}
        if len(ls) > 1:
            issues.append(f'league_size disagrees within one ranking: '
                          f'{sorted(x for x in ls if x is not None)}')
        elif ls and next(iter(ls)) not in (None, len(teams)):
            issues.append(f'league_size {next(iter(ls))} != {len(teams)} '
                          f'rated teams')

        seasons = {r.get('season') for r in sub}
        if len(seasons) > 1:
            issues.append(f'TWO SEASONS fused into one ranking: '
                          f'{sorted(x for x in seasons if x is not None)}')

        stamps = {str(r.get('refreshed_at'))[:16] for r in sub}
        if len(stamps) > 1:
            issues.append(f'{len(stamps)} refresh vintages mixed into one '
                          f'ranking: {sorted(stamps)[:4]}')
        age = _age_hours(str(sorted(stamps)[-1]) if stamps else '')
        if age is not None and age > MAX_AGE_HOURS:
            issues.append(f'ranking is {age:.0f}h old (> {MAX_AGE_HOURS}h) - '
                          f'the nightly has not written it in two cycles')

        # Rank vs value. Ties in the STORED value are legal: rank came from
        # the unrounded rating, raw_value is round(v, 3). See the header.
        order = sorted(sub, key=lambda r: -(_num(r.get('raw_value')) or 0.0))
        viol = [(order[i - 1], order[i]) for i in range(1, len(order))
                if order[i].get('rank') is not None
                and order[i - 1].get('rank') is not None
                and int(order[i]['rank']) <= int(order[i - 1]['rank'])
                and _num(order[i].get('raw_value'))
                != _num(order[i - 1].get('raw_value'))]
        if viol:
            a, b = viol[0]
            issues.append(
                f'{len(viol)} rank/value order violation(s) that are NOT '
                f'stored-value ties, e.g. {a.get("team")} '
                f'v={a.get("raw_value")} #{a.get("rank")} ranked ABOVE '
                f'{b.get("team")} v={b.get("raw_value")} #{b.get("rank")}')

        stamp = sorted(stamps)[-1] if stamps else '?'
        if issues:
            print(f'   {key:<11} ** {len(issues)} PROBLEM(S) ** '
                  f'{len(teams)} teams, refreshed {stamp}')
            for m in issues:
                print(f'                 - {m}')
            problems += [f'{sport}/{key}: {m}' for m in issues]
        else:
            print(f'   {key:<11} ok   {len(teams)} teams · ranks '
                  f'1..{max(ranks)} contiguous, no dups · rank 1 = highest '
                  f'value · refreshed {stamp}')
    return problems


def check_schedule_completeness(sport, rows) -> list[str]:
    """Is any rated team rated off a materially short schedule?

    This is the Miami (OH) shape: a deleted opponent left that team rated off
    a partial schedule at SOS rank 2 of 137, and every rank-level check passed
    while it was wrong.
    """
    tbl = RESULTS.get(sport)
    rated = {r.get('team') for r in rows if r.get('stat_key') == 'sor'}
    if not tbl or not rated:
        return []
    start = season_start(sport)
    today = et_today()
    params = {'select': 'game_date,home_team,away_team,home_score,away_score'}
    if start:
        params['and'] = f'(game_date.gte.{start},game_date.lt.{today})'
    else:
        params['game_date'] = f'lt.{today}'
    try:
        games = page(tbl, params)
    except RuntimeError as e:
        print(f'   schedule   UNVERIFIED - {tbl} unreadable ({e})'[:160])
        return [f'{sport}: schedule completeness unverified']
    played = collections.Counter()
    for g in games:
        if g.get('home_score') is None or g.get('away_score') is None:
            continue
        for side in ('home_team', 'away_team'):
            t = g.get(side)
            if t:
                played[t] += 1
    counts = [played.get(t, 0) for t in rated]
    med = statistics.median(counts) if counts else 0
    if med < MIN_MEDIAN_GAMES:
        print(f'   schedule   n/a  median {med:.0f} completed games per rated '
              f'team is under {MIN_MEDIAN_GAMES} - too early in the season '
              f'for the ratio to mean anything')
        return []
    floor = med * SHORT_SCHEDULE_FRAC
    short = sorted(((t, played.get(t, 0)) for t in rated
                    if played.get(t, 0) < floor), key=lambda x: x[1])
    #: A name that resolves in team_computed_stats but not in the results
    #: table counts as ZERO games here, which is a join failure, not a short
    #: schedule - and it is the louder of the two, so it is named separately.
    zero = [t for t, n in short if n == 0]
    if not short:
        print(f'   schedule   ok   median {med:.0f} completed games · every '
              f'one of {len(rated)} rated teams at or above '
              f'{floor:.1f} ({SHORT_SCHEDULE_FRAC:.0%} of median)')
        return []
    print(f'   schedule   ** {len(short)} rated team(s) below '
          f'{floor:.1f} games ({SHORT_SCHEDULE_FRAC:.0%} of the {med:.0f} '
          f'median) - rated off a PARTIAL schedule **')
    for t, n in short[:12]:
        tag = '  <- 0 games: this is a NAME JOIN failure, not a bye' if n == 0 \
              else ''
        print(f'                 {t:<30}{n} completed games{tag}')
    msgs = [f'{sport}: {len(short)} team(s) rated off a partial schedule '
            f'(median {med:.0f})']
    if zero:
        msgs.append(f'{sport}: {len(zero)} rated team(s) have ZERO games in '
                    f'{tbl} - name join failure: {zero[:6]}')
    return msgs


def check_game_rows(sport, rows) -> list[str]:
    """Do UPCOMING game rows carry exactly what the source says right now?

    Only game_date >= today ET. Past rows hold the as-of-kickoff snapshot on
    purpose and are never compared - see the header.
    """
    ctx = CTX_WITH_SOR.get(sport)
    if not ctx:
        print(f'   game rows  INFO: {sport} has no home/away sor/sos columns '
              f'on its game context, so the ENGINE never sees these values '
              f'(the app card reads team_computed_stats directly, so display '
              f'is fine). Closing that needs a migration plus '
              f'populate_game_sor support - not a data fix.')
        return []
    today = et_today()
    src = collections.defaultdict(dict)
    for r in rows:
        if r.get('rank') is None:
            continue
        v = _num(r.get('raw_value'))
        if v is None:
            continue
        src[r.get('stat_key')][r.get('team')] = (round(v, 3), int(r['rank']))
    try:
        games = page(ctx, {'select': '*', 'game_date': f'gte.{today}'})
    except RuntimeError as e:
        print(f'   game rows  UNVERIFIED - {ctx} unreadable ({e})'[:160])
        return [f'{sport}: game-row check unverified']
    problems = []
    for key in ('sos', 'sor'):
        match = stale = nullv = nosrc = 0
        shown = []
        for g in games:
            for side in ('home', 'away'):
                t = g.get(f'{side}_team')
                if not t:
                    continue
                s = src.get(key, {}).get(t)
                if s is None:
                    nosrc += 1
                    continue
                v, rk = g.get(f'{side}_{key}'), g.get(f'{side}_{key}_rank')
                if v is None or rk is None:
                    nullv += 1
                    continue
                if (round(_num(v), 3), int(rk)) == s:
                    match += 1
                else:
                    stale += 1
                    if len(shown) < 5:
                        shown.append((g.get('game_date'), t,
                                      (round(_num(v), 3), int(rk)), s))
        if match + stale + nullv + nosrc == 0:
            print(f'   game rows  {key}: no games on/after {today}')
            continue
        if stale or nullv:
            print(f'   game rows  {key} on/after {today}: ** {stale} STALE · '
                  f'{nullv} NULL ** · {match} match · {nosrc} no source row')
            for d, t, got, exp in shown:
                print(f'                 {d} {t:<26} row=(v {got[0]}, '
                      f'#{got[1]})  source=(v {exp[0]}, #{exp[1]})')
            if stale:
                problems.append(f'{sport}/{key}: {stale} upcoming team-slot(s) '
                                f'disagree with the current source')
            if nullv:
                problems.append(f'{sport}/{key}: {nullv} upcoming team-slot(s) '
                                f'NULL though the source has a value')
        else:
            print(f'   game rows  {key} on/after {today}: all {match} '
                  f'populated team-slots match the source · {nosrc} with no '
                  f'source row (non-FBS opponents are expected)')
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append',
                    help='repeatable; default every sport')
    ap.add_argument('--warn-only', action='store_true', dest='warn',
                    help='print findings but always exit 0')
    a = ap.parse_args()
    sports = [s.upper() for s in (a.sport or list(SPORTS))]
    unknown = [s for s in sports if s not in SPORTS]
    if unknown:
        print(f'unsupported: {unknown}; known: {list(SPORTS)}', file=sys.stderr)
        return 2

    print('=' * 74)
    print(f'SOS / SOR integrity · {et_today()} ET')
    print('=' * 74)
    all_problems = []
    for sport in sports:
        print(f'\n{sport}')
        try:
            rows = page('team_computed_stats',
                        {'select': 'team,stat_key,raw_value,rank,league_size,'
                                   'season,refreshed_at',
                         'sport': f'eq.{sport}',
                         'stat_key': f'in.({",".join(SURFACES)})'})
        except RuntimeError as e:
            # Not a pass. An unreadable source is an unverified sport.
            print(f'   UNVERIFIED - team_computed_stats unreadable: {e}'[:200])
            all_problems.append(f'{sport}: source unreadable')
            continue
        if not rows:
            print('   ** no sor/sos rows at all - the ranker has never '
                  'written this sport **')
            all_problems.append(f'{sport}: no sor/sos rows')
            continue
        for fn in (check_ranking, check_schedule_completeness, check_game_rows):
            try:
                all_problems += fn(sport, rows)
            except Exception as e:                             # noqa: BLE001
                print(f'   {fn.__name__} FAILED ({type(e).__name__}: {e}) - '
                      f'treat as UNVERIFIED, not as a pass'[:200])
                all_problems.append(f'{sport}: {fn.__name__} raised '
                                    f'{type(e).__name__}')

    print('')
    print('=' * 74)
    if not all_problems:
        print(f'PASS · {len(sports)} sport(s) · every ranking contiguous and '
              f'tie-consistent, every rated team on a full schedule, every '
              f'upcoming game row matching its source')
    else:
        print(f'{len(all_problems)} PROBLEM(S)')
        for m in all_problems:
            print(f'  - {m}')
    print('=' * 74)
    if a.warn and all_problems:
        print('--warn-only: exiting 0')
        return 0
    return 0 if not all_problems else 1


if __name__ == '__main__':
    sys.exit(main())
