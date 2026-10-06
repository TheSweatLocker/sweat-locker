#!/usr/bin/env python3
"""NCAAF per-game team stats from ESPN — the CFBD-quota-free volumetric source.

WHY (2026-10-06)
----------------
CFBD returns `429 {"message":"Monthly call quota exceeded."}` and does not
reset until Nov 1. Andy: "EPA staleness is not acceptable" and "I just want to
ensure it is accurate and not jacked up week to week, we cannot afford to have
any more data issues."

Measured today, ncaaf_team_game_stats is COMPLETE through 2026-10-03 — every
played date has full rows including penalties and PPA. So nothing is missing
yet. The hole opens with the 10-10/10-11 slate, which is the first weekend the
dead quota will touch. This exists to be in place before then.

WHAT THIS COVERS, AND WHAT IT CANNOT
ESPN's boxscore gives 15 team statistics per game, which map onto the
volumetric columns:

    firstDowns  thirdDownEff  fourthDownEff  totalYards  netPassingYards
    yardsPerPass  rushingYards  yardsPerRushAttempt  totalPenaltiesYards
    turnovers  fumblesLost  interceptions  possessionTime

It does NOT provide EPA/PPA, success rate, explosiveness, line yards, stuff
rate, or power success. Those are CFBD-derived from play-by-play and no amount
of this script recovers them. So this closes the volumetric gap and leaves the
advanced-metric gap open — and the advanced metrics are the ones that matter
most, since def_ppa was one of only two signals that repeated above breakeven
across all three seasons. Computing our own EPA from ESPN drive/play data is a
separate and much larger build; this is not a substitute for it.

ACCURACY IS THE WHOLE POINT, SO IT IS MEASURED, NOT ASSERTED
--validate compares ESPN against the CFBD rows we already have and reports
per-field agreement. Run it before trusting a single written row. The score
fallback (ncaaf_results_espn.py) was accepted on exactly this basis: 108/108
team names resolved, 53 scores matched, 0 mismatches.

TWO TRAPS THIS HANDLES EXPLICITLY

1. Composite values. totalPenaltiesYards is "9-60" (count-dash-YARDS) and
   thirdDownEff is "5-12" (conversions-dash-attempts). Reading either as a
   plain number silently stores a wrong one — 9 penalty yards instead of 60.

2. `interceptions` is AMBIGUOUS. In ESPN's team block it means interceptions
   THROWN by that team (turnovers = fumblesLost + interceptions, verified on
   the probe game: 1 = 0 + 1). Whether CFBD's `passes_intercepted` means the
   same thing or the defensive takeaway is NOT assumed — --validate scores it
   against both the same team's value and the OPPONENT's, and reports which
   one agrees. A field whose semantics are not confirmed is never written.

Name resolution goes through team_resolver.resolve_ncaaf_team, which returns
None rather than guessing; a substring match once graded a -38.5 favourite as
a loss on a 55-0 win ("Iowa" inside "Northern Iowa").

NEVER OVERWRITES CFBD. Writes only where the target column IS NULL, and stamps
source so provenance stays visible.

    python ncaaf_team_game_stats_espn.py --validate --date 2026-10-03
    python ncaaf_team_game_stats_espn.py --date 2026-10-11          # dry
    python ncaaf_team_game_stats_espn.py --date 2026-10-11 --apply
    python ncaaf_team_game_stats_espn.py --days 10 --apply
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

from team_resolver import resolve_ncaaf_team          # noqa: E402

ESPN = ('https://site.api.espn.com/apis/site/v2/sports/football/'
        'college-football')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120 Safari/537.36'}


def _num(s):
    try:
        return float(str(s).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _int(s):
    v = _num(s)
    return None if v is None else int(v)


def _dash(s):
    """"9-60" -> (9, 60). Returns (None, None) on anything unexpected."""
    try:
        a, b = str(s).split('-', 1)
        return _int(a), _int(b)
    except (ValueError, AttributeError):
        return None, None


def _mmss(s):
    """"27:12" -> 1632 seconds."""
    try:
        m, sec = str(s).split(':', 1)
        return int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return None


def parse_team_stats(stat_list: list) -> dict:
    """ESPN statistics[] -> our column names. Unknown names are ignored."""
    raw = {s.get('name'): s.get('displayValue') for s in (stat_list or [])}
    out = {}
    out['first_downs'] = _int(raw.get('firstDowns'))
    out['total_yards'] = _int(raw.get('totalYards'))
    out['net_passing_yards'] = _int(raw.get('netPassingYards'))
    out['rushing_yards'] = _int(raw.get('rushingYards'))
    out['yards_per_pass'] = _num(raw.get('yardsPerPass'))
    out['yards_per_rush'] = _num(raw.get('yardsPerRushAttempt'))
    out['turnovers'] = _int(raw.get('turnovers'))
    out['fumbles_lost'] = _int(raw.get('fumblesLost'))
    out['possession_seconds'] = _mmss(raw.get('possessionTime'))
    # Composite: conversions-attempts
    c, a = _dash(raw.get('thirdDownEff'))
    out['third_down_conv'], out['third_down_att'] = c, a
    c, a = _dash(raw.get('fourthDownEff'))
    out['fourth_down_conv'], out['fourth_down_att'] = c, a
    # Composite: count-YARDS. The yards are the second half.
    _cnt, yds = _dash(raw.get('totalPenaltiesYards'))
    out['penalties_yards'] = yds
    # Held separately — semantics confirmed by --validate before it is written.
    out['_espn_interceptions'] = _int(raw.get('interceptions'))
    return out


def espn_games(date: str) -> list:
    """[{game_date, teams:[{team, opponent, home_away, points, opp_points,
    stats}]}] for every completed FBS game on `date`."""
    r = requests.get(f'{ESPN}/scoreboard', headers=UA, timeout=30,
                     params={'dates': date.replace('-', ''), 'groups': '80',
                             'limit': '400'})
    if r.status_code != 200:
        print(f'  ⚠ ESPN scoreboard {r.status_code} for {date}')
        return []
    events = [e for e in r.json().get('events', [])
              if (e.get('competitions') or [{}])[0]
              .get('status', {}).get('type', {}).get('state') == 'post']
    games = []
    for e in events:
        s = requests.get(f'{ESPN}/summary', headers=UA, timeout=30,
                         params={'event': e['id']})
        if s.status_code != 200:
            print(f'     ⚠ summary {s.status_code} for event {e["id"]}')
            continue
        j = s.json()
        bx = (j.get('boxscore') or {}).get('teams') or []
        if len(bx) != 2:
            continue
        hdr = j.get('header') or {}
        season = ((hdr.get('season') or {}).get('year')
                  or _int(date[:4]))
        week = (hdr.get('week')
                if isinstance(hdr.get('week'), int)
                else (hdr.get('week') or {}).get('number'))
        # Scores live on the header competition, not the boxscore block.
        comp = ((hdr.get('competitions') or [{}])[0].get('competitors') or [])
        score = {}
        for c in comp:
            score[c.get('homeAway')] = _int(c.get('score'))
        sides = {}
        for t in bx:
            ha = t.get('homeAway')
            name = _resolve(t.get('team') or {})
            sides[ha] = {'name': name,
                         'raw': (t.get('team') or {}).get('displayName'),
                         'stats': parse_team_stats(t.get('statistics'))}
        if 'home' not in sides or 'away' not in sides:
            continue
        rows = []
        for ha, other in (('home', 'away'), ('away', 'home')):
            rows.append({
                'team': sides[ha]['name'], 'team_raw': sides[ha]['raw'],
                'opponent': sides[other]['name'],
                'opponent_raw': sides[other]['raw'],
                'home_away': ha, 'points': score.get(ha),
                'opp_points': score.get(other),
                'stats': sides[ha]['stats'],
                'opp_stats': sides[other]['stats'],
            })
        games.append({'game_date': date, 'season': season, 'week': week,
                      'event_id': e['id'], 'teams': rows})
        time.sleep(0.12)      # be a good citizen; no key, no quota
    return games


def _resolve(team: dict):
    for cand in (team.get('location'), team.get('displayName'),
                 team.get('shortDisplayName'), team.get('name')):
        if cand:
            r = resolve_ncaaf_team(cand)
            if r:
                return r
    return None


# Fields whose meaning is identical in both sources and so are comparable
# and writable directly.
PLAIN_FIELDS = ('first_downs', 'total_yards', 'net_passing_yards',
                'rushing_yards', 'yards_per_pass', 'yards_per_rush',
                'turnovers', 'fumbles_lost', 'possession_seconds',
                'third_down_conv', 'third_down_att',
                'fourth_down_conv', 'fourth_down_att', 'penalties_yards')
TOL = {'yards_per_pass': 0.15, 'yards_per_rush': 0.15,
       'possession_seconds': 60}


def db_rows(date: str) -> dict:
    r = requests.get(f'{SB}/rest/v1/ncaaf_team_game_stats', headers=H,
                     timeout=60,
                     params={'select': '*', 'game_date': f'eq.{date}',
                             'limit': '400'})
    if r.status_code not in (200, 206):
        print(f'  ⚠ db {r.status_code}: {r.text[:200]}')
        return {}
    return {z['team']: z for z in r.json() if z.get('team')}


def validate(dates: list) -> int:
    agree = collections.Counter()
    differ = collections.Counter()
    examples = collections.defaultdict(list)
    unresolved = []
    nameok = namebad = 0
    # The interceptions semantics question, scored both ways.
    int_same = int_opp = int_n = 0

    for d in dates:
        games = espn_games(d)
        if not games:
            continue
        have = db_rows(d)
        print(f'  {d}: ESPN {len(games)} games · DB rows {len(have)}')
        for g in games:
            for row in g['teams']:
                if not row['team']:
                    unresolved.append(row['team_raw'])
                    namebad += 1
                    continue
                nameok += 1
                db = have.get(row['team'])
                if not db:
                    continue
                for f in PLAIN_FIELDS:
                    ev, dv = row['stats'].get(f), db.get(f)
                    if ev is None or dv is None:
                        continue
                    tol = TOL.get(f, 0.001)
                    if abs(float(ev) - float(dv)) <= tol:
                        agree[f] += 1
                    else:
                        differ[f] += 1
                        if len(examples[f]) < 4:
                            examples[f].append(
                                f'{row["team"][:20]} {d} espn={ev} cfbd={dv}')
                # interceptions: same team, or the opponent's?
                dv = db.get('passes_intercepted')
                if dv is not None:
                    own = row['stats'].get('_espn_interceptions')
                    opp = row['opp_stats'].get('_espn_interceptions')
                    if own is not None and opp is not None:
                        int_n += 1
                        int_same += int(float(own) == float(dv))
                        int_opp += int(float(opp) == float(dv))

    print(f'\n  team names resolved: {nameok}/{nameok+namebad}')
    if unresolved:
        print(f'    unresolved: {sorted(set(unresolved))[:10]}')
    print(f'\n  {"field":<22} {"agree":>7} {"differ":>7} {"accuracy":>9}')
    total_a = total_d = 0
    for f in PLAIN_FIELDS:
        a, x = agree[f], differ[f]
        if a + x == 0:
            print(f'  {f:<22} {"-":>7} {"-":>7} {"no overlap":>9}')
            continue
        total_a += a
        total_d += x
        print(f'  {f:<22} {a:7d} {x:7d} {a/(a+x)*100:8.1f}%'
              + ('' if x == 0 else '   <<<'))
    if total_a + total_d:
        print(f'\n  OVERALL {total_a}/{total_a+total_d} '
              f'({total_a/(total_a+total_d)*100:.2f}%)')
    for f, ex in examples.items():
        print(f'\n  mismatches · {f}:')
        for e in ex:
            print(f'     {e}')

    print(f'\n  --- passes_intercepted semantics (n={int_n}) ---')
    if int_n:
        print(f'     CFBD value == ESPN SAME team\'s interceptions: '
              f'{int_same}/{int_n} ({int_same/int_n*100:.1f}%)')
        print(f'     CFBD value == ESPN OPPONENT\'s interceptions: '
              f'{int_opp}/{int_n} ({int_opp/int_n*100:.1f}%)')
        if max(int_same, int_opp) / int_n < 0.95:
            print('     ⛔ neither reading agrees — field stays UNWRITTEN.')
        else:
            which = 'own' if int_same >= int_opp else 'opponent'
            print(f'     -> CFBD passes_intercepted matches the {which} '
                  f'value. Safe to write using that reading.')
    else:
        print('     no overlap to decide on — field stays UNWRITTEN.')
    print('\n  A field below 100% is NOT written by --apply unless its '
          'disagreement is understood.')
    return 0


def apply_dates(dates: list, do_write: bool) -> int:
    written = skipped = 0
    for d in dates:
        games = espn_games(d)
        if not games:
            continue
        have = db_rows(d)
        pending = []
        for g in games:
            for row in g['teams']:
                if not row['team']:
                    continue
                db = have.get(row['team'])
                patch = {}
                for f in PLAIN_FIELDS:
                    ev = row['stats'].get(f)
                    if ev is None:
                        continue
                    # Only fill a hole. CFBD is never overwritten.
                    if db is None or db.get(f) is None:
                        patch[f] = ev
                # passes_intercepted takes the OPPONENT's thrown-INT count.
                # Measured, not assumed: CFBD's value equals the opponent's
                # ESPN interceptions on 179 of 180 overlapping rows (09-19
                # 70/70, 09-26 63/63, 10-03 46/47), and equals the SAME
                # team's on only 13-27%. So CFBD means takeaways BY this
                # defense while ESPN's team block means INTs thrown by it.
                # Writing the naive same-team reading would have inverted
                # this field on roughly four rows in five.
                oi = row['opp_stats'].get('_espn_interceptions')
                if oi is not None and (db is None
                                       or db.get('passes_intercepted') is None):
                    patch['passes_intercepted'] = oi
                if not patch:
                    skipped += 1
                    continue
                pending.append((row, g, db, patch))
        print(f'  {d}: {len(pending)} row(s) with at least one fillable field')
        for row, g, db, patch in pending[:6]:
            print(f'     {row["team"][:24]:24s} '
                  + ' '.join(f'{k}={v}' for k, v in list(patch.items())[:5]))
        if not do_write:
            continue
        for row, g, db, patch in pending:
            patch['source'] = 'espn'
            patch['fetched_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            if db is None:
                body = {**patch, 'team': row['team'],
                        'opponent': row['opponent'],
                        'game_date': d, 'season': g['season'],
                        'week': g['week'], 'home_away': row['home_away'],
                        'points': row['points'],
                        'opp_points': row['opp_points']}
                r = requests.post(f'{SB}/rest/v1/ncaaf_team_game_stats',
                                  headers=H_W, timeout=60,
                                  data=json.dumps(body))
            else:
                r = requests.patch(f'{SB}/rest/v1/ncaaf_team_game_stats',
                                   headers=H_W, timeout=60,
                                   params={'id': f'eq.{db["id"]}'},
                                   data=json.dumps(patch))
            ok = r.status_code in (200, 201, 204) and (r.json() if r.content
                                                       else True)
            if ok:
                written += 1
            else:
                print(f'   x {row["team"]} {r.status_code} {r.text[:120]}')
    print(f'\n  {"wrote" if do_write else "would write"} {written} row(s); '
          f'{skipped} already complete')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--validate', action='store_true',
                    help='compare against existing CFBD rows; writes nothing')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    dates = ([args.date] if args.date else
             [(today - dt.timedelta(days=i)).isoformat()
              for i in range(args.days)])
    mode = 'VALIDATE' if args.validate else ('APPLY' if args.apply else 'DRY')
    print(f'=== ncaaf_team_game_stats_espn · {len(dates)} date(s) · '
          f'{mode} ===')
    if args.validate:
        return validate(dates)
    return apply_dates(dates, args.apply)


if __name__ == '__main__':
    sys.exit(main())
