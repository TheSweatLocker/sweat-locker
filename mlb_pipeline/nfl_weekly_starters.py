"""NFL weekly starter puller — box-score-backed, ESPN news on top.

For each upcoming NFL game (regular season, playoffs, or preseason), resolves
the projected starting QB and writes to nfl_starters. Feeds the game-detail
NFL slot's Starter QB card.

══ 2026-10-01 · THIS TABLE WAS WRONG ON ~72% OF TEAMS ══
Measured against completed box scores in nfl_player_stats, nfl_starters named
the actual starting QB on only 8 of 32 teams in week 2 and 9 of 32 in week 3.
It said Joe Fagnano for BAL (not Lamar Jackson), Andy Dalton for PHI (not Jalen
Hurts), Drew Allar for PIT (not Aaron Rodgers), Tommy DeVito for NE, Mac Jones
for SF, Joshua Dobbs for DET, Davis Mills for HOU. The app renders this in the
QB Matchup card, so users were reading a wrong starter on most games.

Three compounding causes, all fixed here:

1. roster_qb1() claimed in its own docstring to read a "depth chart" and
   "picks first". ESPN's roster endpoint is NOT a depth chart — it is
   ALPHABETICAL BY SURNAME. Baltimore's QBs come back in the order
   Fagnano(12), Huntley(5), Jackson(8), Thompson(11), so "first" is whoever
   sorts first. That single off-by-assumption explains every wrong name:
   Dalton<Hurts, Allar<Rodgers, DeVito<Maye, Jones<Purdy, Dobbs<Goff,
   Mills<Stroud. The 8-9 teams it got right were coincidences where the real
   starter also sorts first (Allen, Burrow). ESPN exposes no usable depth
   chart: teams/{t}/depthchart returns {} and teams/{t}?enable=depthchart
   carries no depth key. So the roster path is REMOVED, not repaired — there
   is no ordering in it to trust.

2. It ran for every team on every run, because both scoreboard paths above it
   are empty days before kickoff: week-4 events return probables=0 AND
   leaders=0. A fallback that silently handles 100% of cases is not a
   fallback, it is the implementation.

3. The `leaders` matcher tested lgroup['abbreviation'] against
   'passingYards'/'passingTouchdowns'. Those are ESPN's `name` values; the
   abbreviation is 'YDS'/'TD'. So even when leaders ARE populated the match
   never fired. Now checks name, abbreviation and displayName.

══ WHAT REPLACES IT ══
nfl_player_stats (nflverse box scores) is ground truth for who actually took
the snaps, and it uses the same team vocabulary as nfl_game_context. The team's
pass-attempt leader in the most recent COMPLETED week predicts the next week's
starter:

    wk1 leader -> wk2 starter   31/32 = 97%
    wk2 leader -> wk3 starter   27/32 = 84%
    combined                    58/64 = 91%   (vs 28% for the old code)

All five wk2->wk3 misses were injury churn (Williams out -> Keenum; Daniels out
-> Mariota; Penix, Murray and Darnold returning), so the resolver additionally
skips anyone nfl_injuries rules out for the target week.

Resolution order, best evidence first:
  1. ESPN `probables`      — actual game-day news, when published
  2. ESPN passing `leaders` — season-to-date leader for the matchup
  3. Box-score carryover    — last completed week's attempt leader, injury-gated
  4. Nothing               — write no row rather than guess a name

`source` records which path won, so a wrong row can be traced to its evidence
instead of being indistinguishable from a good one.

Only weeks STRICTLY BEFORE the target week are consulted, so backfilling an
earlier week cannot read a box score from after the game it is predicting.

Cadence:
  - Wed 6pm ET (opens the week)
  - Sat 10am ET (locks Sunday early slate)
  - Sun 12pm ET (final for Sun late + MNF)

Extends easily to RB1/WR1 when ESPN starts publishing that fully. For v1
we only pull QB — highest-value single-position surface.

Usage:
  python nfl_weekly_starters.py                     # current season/week
  python nfl_weekly_starters.py --season 2026 --week 1
  python nfl_weekly_starters.py --season-type PRE   # preseason
  python nfl_weekly_starters.py --dry-run
"""
import argparse
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# ESPN scoreboard — regular season vs preseason use different `seasontype`
# 1 = preseason, 2 = regular, 3 = postseason
SEASON_TYPE_MAP = {'PRE': 1, 'REG': 2, 'POST': 3}
ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'

# ESPN team abbrev → nfl_data_py convention (matches nfl_team_stats.team)
# ESPN uses 'WSH', pipeline uses 'WAS' etc. Add divergences here.
#
# 2026-10-01: 'LAR' used to map to itself. Every table this joins to spells the
# Rams 'LA' — nfl_game_context, nfl_player_stats and nfl_team_aliases
# (canonical_name) all use 'LA'. The app looks the card up with
# .in('team', [homeTeam, awayTeam]) where those come from game context, so a
# row stored under 'LAR' matched nothing and the Rams' QB Matchup card was
# silently blank all season — a different failure from the wrong-name bug and
# invisible next to it.
TEAM_MAP: dict[str, str] = {
    'WSH': 'WAS',   # Commanders
    'JAX': 'JAX',   # ok
    'LAR': 'LA',    # Rams — pipeline-wide spelling is 'LA'
    'LAC': 'LAC',
    # Rest match 1:1
}
def _map_team(esp: str) -> str:
    return TEAM_MAP.get(esp, esp)


def fetch_scoreboard(season: int, week: int, season_type: str = 'REG') -> list:
    stype = SEASON_TYPE_MAP.get(season_type.upper(), 2)
    params = {'year': season, 'seasontype': stype, 'week': week}
    r = requests.get(ESPN_SCOREBOARD, params=params, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ ESPN scoreboard {r.status_code}: {r.text[:200]}')
        return []
    return r.json().get('events') or []


def _paged(table: str, **params) -> list[dict]:
    """Fetch a whole table. PostgREST caps a page at 1000 rows and returns the
    truncation silently — nfl_player_stats is already past 3,300 rows for 2026,
    so a single unpaged GET would drop the later weeks without any error.
    """
    out: list[dict] = []
    offset = 0
    while True:
        q = dict(params)
        q['limit'] = 1000
        q['offset'] = offset
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=q,
                         timeout=60)
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < 1000:
            return out
        offset += 1000


def load_box_score_qbs(season: int) -> dict[str, list[tuple]]:
    """{team: [(week, attempts, player_name), ...]} newest week first, then
    most attempts first within a week.

    The pass-attempt leader is the operational definition of "who started" —
    nfl_player_stats has no start flag, and the leader matched the eye test on
    all 32 teams for weeks 1-3 (Lamar, Hurts, Mahomes, Rodgers, Goff, Stroud).

    EVERY passer is kept, not just the leader, because the second name in a week
    is the best available depth signal. Tampa Bay is the case that proves it:
    Baker Mayfield is Out for week 4 with a dislocated thumb, and the only other
    QB to throw a pass for TB all season is Jalon Daniels — 3 attempts in week 3,
    listed Full for week 4, and the man actually starting. Keeping only leaders
    made him invisible and left TB with no row at all.

    position=='QB' is required so a trick-play throw by a receiver cannot be
    promoted to starting quarterback by a one-attempt tiebreak.
    """
    try:
        rows = _paged('nfl_player_stats', select='*', season=f'eq.{season}')
    except Exception as e:
        print(f'  ⚠ box-score load failed: {e}')
        return {}
    by_team: dict[str, list[tuple]] = {}
    for x in rows:
        att = float(x.get('attempts') or 0)
        if att <= 0:
            continue
        if str(x.get('position') or 'QB').upper() != 'QB':
            continue
        team, week = x.get('team'), x.get('week')
        if not team or week is None:
            continue
        by_team.setdefault(team, []).append((week, att, x.get('player_name')))
    for v in by_team.values():
        v.sort(reverse=True)     # newest week first, most attempts first
    return by_team


def load_ruled_out(season: int, week: int) -> set[tuple]:
    """{(team, player_name)} ruled out for `week` — the churn the 91% rule misses.

    Reuses nfl_qb_injury_gate.OUT_STATUSES so this file cannot drift into a
    second, stricter definition of "out" than the gate the models already use.
    'Questionable' is deliberately NOT out.
    """
    try:
        from nfl_qb_injury_gate import OUT_STATUSES
    except ImportError:
        print('  ⚠ injury gate unavailable — not injury-filtering starters')
        return set()
    try:
        rows = _paged('nfl_injuries', select='*', season=f'eq.{season}',
                      week=f'eq.{week}')
    except Exception as e:
        print(f'  ⚠ injury load failed: {e}')
        return set()
    return {(x.get('team'), x.get('player_name')) for x in rows
            if str(x.get('injury_status') or '').strip().lower() in OUT_STATUSES}


def actual_starter(team: str, week: int, by_team: dict) -> Optional[dict]:
    """Who actually started `week` for `team` — that week's own attempt leader.

    For a COMPLETED week this is not a leak, it is the answer: the table is
    rendered in the app as the game's Starter QB, and for a game already played
    the honest value is the man who played. The forward path below deliberately
    refuses to look at the target week; this one exists only for weeks whose
    box score is final, and the caller must opt in with --retrospective.

    Week 1 can only be filled this way — it has no prior week to carry forward,
    which is why its rows were left holding the old alphabetical guesses.
    """
    cands = [(a, n) for (w, a, n) in by_team.get(team, []) if w == week]
    if not cands:
        return None
    return {'name': max(cands)[1], 'id': None, 'source': f'box_score_actual_w{week}'}


def box_score_starter(team: str, target_week: int,
                      by_team: dict, ruled_out: set) -> Optional[dict]:
    """Most recent completed-week attempt leader for `team` who is not ruled out.

    Only weeks STRICTLY BEFORE target_week are considered — reading the target
    week's own box score would be predicting a game from its own result, and
    would make a backfill look far better than the live path ever can.
    """
    for week, _att, name in by_team.get(team, []):
        if week >= target_week:
            continue
        if (team, name) in ruled_out:
            continue
        return {'name': name, 'id': None, 'source': f'box_score_w{week}'}
    return None


def extract_qb(competition: dict, side_home: bool) -> Optional[dict]:
    """Pull projected starter QB from a competition, or None.

    Returns None rather than guessing — the caller then falls back to box-score
    carryover. The removed roster fallback used to answer here unconditionally,
    which is why the two paths below were never exercised in production.
    """
    comps = competition.get('competitors') or []
    for c in comps:
        want = 'home' if side_home else 'away'
        if c.get('homeAway') != want:
            continue
        for p in (c.get('probables') or []):
            ath = p.get('athlete') or {}
            if (ath.get('position') or {}).get('abbreviation') == 'QB':
                return {'name': ath.get('displayName'), 'id': ath.get('id'),
                        'source': 'espn_probable'}
        # ESPN labels this group by `name`; `abbreviation` is 'YDS'/'TD'. The
        # old code only compared abbreviation, so this branch never fired.
        for lgroup in (c.get('leaders') or []):
            tags = {str(lgroup.get(k) or '').lower()
                    for k in ('name', 'abbreviation', 'displayName')}
            if not ({'passingyards', 'passingtouchdowns', 'passing yards',
                     'yds', 'td'} & tags):
                continue
            for leader in lgroup.get('leaders') or []:
                ath = leader.get('athlete') or {}
                if (ath.get('position') or {}).get('abbreviation') == 'QB':
                    return {'name': ath.get('displayName'), 'id': ath.get('id'),
                            'source': 'espn_leader'}
    return None


def upsert_starter(season: int, week: int, season_type: str,
                   team: str, position: str, player_name: str,
                   player_id: Optional[str] = None,
                   source: str = 'espn_scoreboard',
                   dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] {season} W{week} {season_type} {team} {position} = '
              f'{player_name}  [{source}]')
        return True
    payload = {
        'season': season, 'week': week, 'season_type': season_type,
        'team': team, 'position': position, 'player_name': player_name,
        'player_id': player_id, 'is_starter': True, 'source': source,
    }
    r = requests.post(
        f'{SB}/rest/v1/nfl_starters?on_conflict=season,week,season_type,team,position',
        headers=H_WRITE, json=payload, timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f'    ⚠ upsert {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(season: Optional[int] = None, week: Optional[int] = None,
        season_type: str = 'REG', dry_run: bool = False,
        retrospective: bool = False) -> None:
    now_utc = datetime.now(timezone.utc)
    season = season or now_utc.year
    # Auto-derive week from ESPN scoreboard (its default view = current week)
    if week is None:
        params = {'seasontype': SEASON_TYPE_MAP.get(season_type.upper(), 2)}
        r = requests.get(ESPN_SCOREBOARD, params=params, timeout=15).json()
        wk_info = r.get('week') or {}
        week = wk_info.get('number', 1)
        print(f'  auto-detected week={week}')

    print(f'== NFL starters · {season} · {season_type} · W{week} ==')
    events = fetch_scoreboard(season, week, season_type)
    print(f'  {len(events)} events on ESPN scoreboard')

    by_team = load_box_score_qbs(season)
    ruled_out = load_ruled_out(season, week)
    print(f'  box-score history: {len(by_team)} teams · '
          f'{len(ruled_out)} player(s) ruled out in W{week}')

    total = 0
    by_source: dict[str, int] = {}
    unresolved: list[str] = []
    for ev in events:
        for c in ev.get('competitions') or []:
            for home_flag in (True, False):
                for comp in c.get('competitors') or []:
                    want = 'home' if home_flag else 'away'
                    if comp.get('homeAway') != want:
                        continue
                    espn_abbrev = (comp.get('team') or {}).get('abbreviation', '?')
                    team = _map_team(espn_abbrev)
                    qb = (actual_starter(team, week, by_team)
                          if retrospective else None)
                    if not (qb and qb.get('name')):
                        qb = extract_qb(c, home_flag)
                    if not (qb and qb.get('name')):
                        qb = box_score_starter(team, week, by_team, ruled_out)
                    if qb and qb.get('name'):
                        src = qb.get('source') or 'espn_scoreboard'
                        ok = upsert_starter(season, week, season_type, team, 'QB',
                                            qb['name'], qb.get('id'),
                                            source=src, dry_run=dry_run)
                        if ok:
                            total += 1
                            by_source[src] = by_source.get(src, 0) + 1
                    else:
                        # No evidence at all. Writing a guess here is what put
                        # Joe Fagnano in front of users, so write nothing.
                        unresolved.append(team)
                    break

    print(f'\nSummary: {total} starter QB rows written for {season} W{week} {season_type}')
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f'   {src:18} {n}')
    if unresolved:
        print(f'   unresolved (no row written): {", ".join(sorted(unresolved))}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int)
    p.add_argument('--week', type=int)
    p.add_argument('--season-type', default='REG', choices=['REG', 'POST', 'PRE'])
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--retrospective', action='store_true',
                   help='week is COMPLETE: record who actually started it, '
                        'from that box score. Required for week 1.')
    args = p.parse_args()
    run(season=args.season, week=args.week, season_type=args.season_type,
        dry_run=args.dry_run, retrospective=args.retrospective)
