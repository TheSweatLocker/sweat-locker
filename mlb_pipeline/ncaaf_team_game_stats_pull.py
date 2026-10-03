#!/usr/bin/env python3
"""Ingest PER-GAME NCAAF team stats from CFBD into ncaaf_team_game_stats.

Andy 2026-09-30: "there has to be a trail to getting that data to improve
projection across both football nfl and ncaaf."

WHY THIS EXISTS
A point-in-time opponent-adjusted rating built from FINAL SCORES cannot beat or
even add to the closing line. Out-of-sample on 113 NCAAF games
(project_pit_reconstruction_ceiling_930):

    raw closing line      9.87   <- the bar
    rating only          18.58
    market + rating      13.36   (3.49 WORSE than the line)

and a ridge sweep proved the fit was not the problem: as regularisation rises
the market coefficient goes to 1.0 and the rating coefficient collapses to
0.48, converging toward the line without ever beating it. There was no missing
weighting to solve for. There was a missing INPUT — the final score is exactly
what the closing line has already absorbed.

This pulls the inputs that are NOT in the score: per-game EPA (CFBD calls it
ppa), success rate, explosiveness, line yards, stuff rate, plus yards,
turnovers, possession time and third-down conversion.

WHY PER-GAME AND NOT SEASON-LEVEL
ncaaf_stats_pull.py hits /stats/season/advanced, which returns CURRENT-STATE
aggregates. Joining those to a week-1 game lets a model see weeks 1-4 — the
leak that produced a fake 67.8% z=+4.07 on 09-29
(project_rolling_stats_leak_trap_929). Per-game rows are immutable once the
game is final, so any as-of-date aggregate can be REBUILT from them with no
snapshot and no leakage. That is the whole point.

THREE ENDPOINTS, JOINED ON CFBD gameId
  /games                 startDate (the only real timestamp), teams, points,
                         neutralSite, and homePregameElo/awayPregameElo —
                         CFBD's Elo computed BEFORE kickoff, so leak-free by
                         construction and worth testing against the SRS that
                         just failed
  /stats/game/advanced   offense/defense ppa, successRate, explosiveness,
                         lineYards, stuffRate, standard/passing-down splits
  /games/teams           totalYards, turnovers, possessionTime, thirdDownEff,
                         sacks, TFL, penalties

/games is the spine: it is the only one of the three carrying a date, and
without a date there is no point-in-time ordering. A game missing from /games
is skipped rather than stored dateless.

WEEK 0 IS REAL in college football (late-August kickoffs), so the week loop
starts at 0, not 1. Postseason is pulled separately because CFBD keys bowls
under seasonType=postseason with its own week numbering.

IDEMPOTENT: upsert on (cfbd_game_id, team). Safe to re-run; re-running a week
whose games have since gone final is how in-progress rows get completed.

USAGE
    python ncaaf_team_game_stats_pull.py --dry-run
    python ncaaf_team_game_stats_pull.py                     # current season
    python ncaaf_team_game_stats_pull.py --season 2025       # one past season
    python ncaaf_team_game_stats_pull.py --backfill 2024 2026
    python ncaaf_team_game_stats_pull.py --season 2026 --week 5
"""
import argparse
import datetime as dt
import os
import sys
import time

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
CFBD_KEY = os.environ.get('CFBD_API_KEY') or os.environ.get('CFBD_KEY')
if not SB or not KEY:
    print('ncaaf_team_game_stats_pull: no Supabase credentials — skipping')
    sys.exit(0)
if not CFBD_KEY:
    print('ncaaf_team_game_stats_pull: CFBD_API_KEY missing — cannot pull')
    sys.exit(1)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}
CFBD = 'https://api.collegefootballdata.com'
CH = {'Authorization': f'Bearer {CFBD_KEY}'}
TABLE = 'ncaaf_team_game_stats'
MAX_WEEK = 16


def cfbd(path, params, tries=3):
    """GET with retry. Raises on persistent failure — a swallowed error here
    would look like 'that week had no games', which is the exact failure mode
    this codebase keeps relearning."""
    last = None
    for i in range(tries):
        try:
            r = requests.get(CFBD + path, headers=CH, params=params, timeout=60)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(1.5 * (i + 1))
            continue
        if r.status_code == 200:
            b = r.json()
            return b if isinstance(b, list) else []
        if r.status_code in (429, 500, 502, 503, 504):
            last = f'{r.status_code}: {r.text[:120]}'
            time.sleep(2.0 * (i + 1))
            continue
        raise RuntimeError(f'CFBD {path} {params} -> {r.status_code}: {r.text[:160]}')
    raise RuntimeError(f'CFBD {path} {params} failed after {tries}: {last}')


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _int(v):
    f = _num(v)
    return None if f is None else int(round(f))


def _mmss(v):
    """'31:24' -> 1884 seconds. CFBD sends possessionTime as MM:SS."""
    if v is None:
        return None
    s = str(v)
    if ':' in s:
        try:
            m, sec = s.split(':')[:2]
            return int(m) * 60 + int(sec)
        except ValueError:
            return None
    return _int(s)


def _eff(v):
    """'7-14' -> (7, 14). CFBD sends thirdDownEff as conv-att."""
    if v is None:
        return (None, None)
    s = str(v)
    if '-' in s:
        a, b = s.split('-')[:2]
        return (_int(a), _int(b))
    return (None, None)


def _pen_yards(v):
    """'3-35' -> 35. CFBD sends totalPenaltiesYards as COUNT-YARDS.

    2026-10-03 · this column was 0 of 1776 rows populated. The value was being
    read with _int(), which returns None for '3-35' exactly as it would for a
    genuinely absent stat — so a 100% parse failure was indistinguishable from
    CFBD not supplying the field, and nothing flagged it.

    It mattered because penalty_yds_pg is published for 266 teams WITH a
    national rank and was cited as a pick reason on the 10-03 Florida Atlantic
    card ("Penalty home undisciplined"), while the only table that could have
    checked it held nothing. Same shape as thirdDownEff, which _eff() has
    always handled correctly two lines below in the same dict literal.

    Returns the YARDS half, matching the column name. The count half is
    available in the same string if a penalties-per-game stat is ever wanted;
    there is no column for it today.
    """
    if v is None:
        return None
    s = str(v).strip()
    if '-' in s:
        parts = s.split('-')
        if len(parts) >= 2:
            return _int(parts[1])
        return None
    # A bare number means CFBD changed the shape; take it rather than drop it.
    return _int(s)


def _et_date(iso):
    """CFBD startDate is UTC. Our tables key on the ET calendar day, and the
    UTC/ET split has already produced duplicate NCAAF rows once
    (dedupe_ncaaf_results.py), so convert rather than truncate."""
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    return (d - dt.timedelta(hours=4)).date().isoformat()


def _split(adv_side):
    """Flatten one offense/defense block, including the nested down splits."""
    d = adv_side or {}
    std = d.get('standardDowns') or {}
    pas = d.get('passingDowns') or {}
    return {
        'plays': _int(d.get('plays')), 'drives': _int(d.get('drives')),
        'ppa': _num(d.get('ppa')), 'total_ppa': _num(d.get('totalPPA')),
        'success_rate': _num(d.get('successRate')),
        'explosiveness': _num(d.get('explosiveness')),
        'power_success': _num(d.get('powerSuccess')),
        'stuff_rate': _num(d.get('stuffRate')),
        'line_yards': _num(d.get('lineYards')),
        'second_level_yards': _num(d.get('secondLevelYards')),
        'open_field_yards': _num(d.get('openFieldYards')),
        'rushing_plays': _int(d.get('rushingPlays')),
        'passing_plays': _int(d.get('passingPlays')),
        'std_downs_ppa': _num(std.get('ppa')),
        'pass_downs_ppa': _num(pas.get('ppa')),
    }


def _stats_map(team_block):
    return {s.get('category'): s.get('stat')
            for s in (team_block.get('stats') or []) if s.get('category')}


def build_rows(season, week, season_type='regular'):
    """One row per (game, team) for a single CFBD week."""
    p = {'year': season, 'week': week, 'seasonType': season_type}
    games = cfbd('/games', p)
    if not games:
        return []
    # /games is the spine: it is the only endpoint carrying a date.
    spine = {}
    for g in games:
        gid = g.get('id')
        if gid is None:
            continue
        spine[int(gid)] = g
    adv_by = {}
    for a in cfbd('/stats/game/advanced', {'year': season, 'week': week,
                                           'seasonType': season_type}):
        gid, tm = a.get('gameId'), a.get('team')
        if gid is not None and tm:
            adv_by[(int(gid), tm)] = a
    vol_by = {}
    for gt in cfbd('/games/teams', p):
        gid = gt.get('id')
        if gid is None:
            continue
        for t in (gt.get('teams') or []):
            if t.get('team'):
                vol_by[(int(gid), t['team'])] = t

    rows = []
    skipped_div = 0
    for gid, g in spine.items():
        # /games is unfiltered on purpose: an FBS team's rating needs its FULL
        # schedule, INCLUDING its FCS games, or strength-of-schedule is wrong.
        # But games where NEITHER side is FBS are noise we would carry across
        # every season, so drop those. Filtering here on the payload rather
        # than via ?classification= keeps the decision visible and countable.
        if 'fbs' not in (str(g.get('homeClassification') or '').lower(),
                         str(g.get('awayClassification') or '').lower()):
            skipped_div += 1
            continue
        hp, ap = _int(g.get('homePoints')), _int(g.get('awayPoints'))
        gdate = _et_date(g.get('startDate'))
        if gdate is None:
            continue          # no date == no point-in-time ordering; skip
        for team, opp, ha, pts, opts, elo, oelo in (
            (g.get('homeTeam'), g.get('awayTeam'), 'home', hp, ap,
             _num(g.get('homePregameElo')), _num(g.get('awayPregameElo'))),
            (g.get('awayTeam'), g.get('homeTeam'), 'away', ap, hp,
             _num(g.get('awayPregameElo')), _num(g.get('homePregameElo'))),
        ):
            if not team:
                continue
            row = {
                'cfbd_game_id': gid, 'team': team, 'opponent': opp,
                'season': season, 'season_type': season_type,
                'week': _int(g.get('week')),
                'start_date': g.get('startDate'), 'game_date': gdate,
                'home_away': ha, 'is_neutral': bool(g.get('neutralSite')),
                'conference_game': bool(g.get('conferenceGame')),
                'points': pts, 'opp_points': opts,
                'pregame_elo': elo, 'opp_pregame_elo': oelo,
                'source': 'cfbd',
                'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            a = adv_by.get((gid, team))
            if a:
                for pre, side in (('off', a.get('offense')), ('def', a.get('defense'))):
                    for k, v in _split(side).items():
                        row[f'{pre}_{k}'] = v
            v = vol_by.get((gid, team))
            if v:
                s = _stats_map(v)
                t3c, t3a = _eff(s.get('thirdDownEff'))
                t4c, t4a = _eff(s.get('fourthDownEff'))
                row.update({
                    'total_yards': _int(s.get('totalYards')),
                    'net_passing_yards': _int(s.get('netPassingYards')),
                    'rushing_yards': _int(s.get('rushingYards')),
                    'yards_per_pass': _num(s.get('yardsPerPass')),
                    'yards_per_rush': _num(s.get('yardsPerRushAttempt')),
                    'first_downs': _int(s.get('firstDowns')),
                    'turnovers': _int(s.get('turnovers')),
                    'fumbles_lost': _int(s.get('fumblesLost')),
                    'passes_intercepted': _int(s.get('passesIntercepted')),
                    'sacks': _num(s.get('sacks')),
                    'tackles_for_loss': _num(s.get('tacklesForLoss')),
                    'penalties_yards': _pen_yards(s.get('totalPenaltiesYards')),
                    'possession_seconds': _mmss(s.get('possessionTime')),
                    'third_down_conv': t3c, 'third_down_att': t3a,
                    'fourth_down_conv': t4c, 'fourth_down_att': t4a,
                })
            rows.append(row)
    if skipped_div:
        print('       (skipped %d non-FBS-vs-non-FBS game(s))' % skipped_div)
    return rows


def upsert(rows, chunk=400):
    """Batch upsert. Keys are UNIONED across the chunk first: PostgREST builds
    one INSERT from the first row's keys, so a chunk whose later rows carry
    columns the first row lacks silently drops them
    (feedback_postgrest_batch_normalize_keys)."""
    wrote = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        keys = set()
        for r in part:
            keys |= set(r)
        norm = [{k: r.get(k) for k in keys} for r in part]
        r = requests.post(f'{SB}/rest/v1/{TABLE}', headers=H_W,
                          params={'on_conflict': 'cfbd_game_id,team'},
                          json=norm, timeout=120)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f'upsert -> {r.status_code}: {(r.text or "")[:300]}')
        wrote += len(norm)
    return wrote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--week', type=int, help='single week (week 0 is real)')
    ap.add_argument('--backfill', nargs=2, type=int, metavar=('FROM', 'TO'))
    ap.add_argument('--postseason', action='store_true',
                    help='also pull seasonType=postseason')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if a.backfill:
        seasons = list(range(a.backfill[0], a.backfill[1] + 1))
    else:
        seasons = [a.season or dt.date.today().year]

    print(f'=== ncaaf_team_game_stats_pull · seasons {seasons} ===')
    grand = 0
    for season in seasons:
        # Week 0 is real in college football — late-August kickoffs.
        weeks = [a.week] if a.week is not None else list(range(0, MAX_WEEK + 1))
        for wk in weeks:
            try:
                rows = build_rows(season, wk)
            except RuntimeError as e:
                print(f'  {season} wk{wk:<2} ! {e}')
                continue
            if not rows:
                continue
            epa = sum(1 for r in rows if r.get('off_ppa') is not None)
            elo = sum(1 for r in rows if r.get('pregame_elo') is not None)
            vol = sum(1 for r in rows if r.get('total_yards') is not None)
            note = f'epa {epa}/{len(rows)} · elo {elo} · vol {vol}'
            if a.dry_run:
                print(f'  {season} wk{wk:<2} would write {len(rows):4d} rows  ({note})')
            else:
                n = upsert(rows)
                grand += n
                print(f'  {season} wk{wk:<2} wrote {n:4d} rows  ({note})')
        if a.postseason:
            try:
                rows = build_rows(season, 1, 'postseason')
                if rows:
                    if a.dry_run:
                        print(f'  {season} post  would write {len(rows)} rows')
                    else:
                        grand += upsert(rows)
                        print(f'  {season} post  wrote {len(rows)} rows')
            except RuntimeError as e:
                print(f'  {season} post  ! {e}')

    if a.dry_run:
        print('\n  (dry run — nothing written)')
        return 0
    # Read back: a 204 is not proof, and this table is new.
    r = requests.get(f'{SB}/rest/v1/{TABLE}',
                     headers={**H, 'Prefer': 'count=exact', 'Range': '0-0'},
                     params={'select': 'cfbd_game_id'}, timeout=60)
    total = r.headers.get('content-range', '').split('/')[-1]
    print(f'\n  upserted {grand} rows · table now holds {total}')
    return 0 if grand else 1


if __name__ == '__main__':
    raise SystemExit(main())
