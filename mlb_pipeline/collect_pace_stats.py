#!/usr/bin/env python3
"""Collect PACE / possession stats so leak-free history can accumulate.

Andy 2026-09-29, asked whether we track average possession time for all sports,
then: "C. but lets collect for each sport" — i.e. do NOT build a pace signal yet,
build the COLLECTION so the question can be answered properly later.

WHY COLLECTION IS THE WHOLE TASK
--------------------------------
Pace cannot be tested against results today. The source tables hold ONE current
season-to-date value per team, so joining them to a game that has already been
played uses end-of-season pace to "predict" a week-1 game — the stat contains the
game. That is project_rolling_stats_leak_trap_929, which produced a fake NCAAF
67.8% ATS / z=+4.07 before it was caught, and it has now bitten this project
three times.

The fix is history, and the rails already exist:

    team_computed_stats  ->  team_stats_rolling (view)  ->  snapshot_team_stats.py
                                                        ->  team_stats_rolling_history

team_stats_rolling_history is keyed (sport, team, season, stat_key,
snapshot_date), so a NEW stat_key needs NO schema change anywhere. Write pace to
team_computed_stats and it appears in the view, gets snapshotted daily, and in a
few weeks there is a real as-of series to test with
`captured_at < kickoff_utc`.

WHY team_computed_stats AND NOT THE MATVIEW
-------------------------------------------
team_stats_rolling_full has been amended by ~10 migrations and its live
definition cannot be read from the pipeline host. Rewriting it from a
reconstruction is the CREATE OR REPLACE drift trap with a matview's blast
radius — project_situational_matview_drift_928 is exactly that: a full CREATE
silently dropped NHL/NBA/NCAAB for three weeks. 20260926d established
team_computed_stats as the safe extension point, and it overrides per
(sport, season, stat_key). plays_pg and top_min_pg exist in NO sport's matview
rows, so nothing is overridden — they are purely additive.

WHAT EACH SPORT GETS, AND WHAT IT ALREADY HAD
---------------------------------------------
    NBA     pace            already a matview stat_key — already collected
    NCAAB   tempo           already a matview stat_key — already collected
    NHL     corsi_5v5       already collected; shot attempts ARE the possession
                            proxy in hockey (there is no possession clock)
    NCAAF   plays_pg        ADDED here
            top_min_pg      ADDED here — rescues possession_time_sec, which is
                            800/805 populated and was read by NOTHING: the
                            writer at ncaaf_game_context.py:1447 fills
                            home_top_min/away_top_min and no model, signal or
                            screen has ever consumed them
    NFL     plays_pg        ADDED here — NFL had no pace field of any kind
    MLB     (none)          DELIBERATELY NOT FAKED. mlb_team_offense carries no
                            plate appearances, pitches or innings — only rate
                            stats and runs/game — so no tempo metric is
                            derivable without new ingestion. Pitches/PA from the
                            MLB Stats API is the real answer and is out of scope
                            for "collect with what we have".

THE DENOMINATOR TRAP (NCAAF)
----------------------------
ncaaf_team_stats.games is backfilled from ncaaf_game_results, which holds FBS
results only, so an FCS team that played four games can carry games=1. Measured
today, straight from the table:

    Abilene Christian  games=1  ->  145.8 "minutes of possession per game"
    Furman             games=1  ->  139.0
    Gardner-Webb       games=2  ->   75.0
    Boise State        games=4  ->   37.0   (correct)
    Georgia            games=4  ->   29.4   (correct)

A game is 60 minutes, so 145.8 is not a fast offense, it is a denominator wrong
by a factor. Same bug 20260926d documents for yardage (South Dakota State at
2033 yds/g). So every value here passes a plays-per-game denominator check
BEFORE it is written, the same gate recompute_ncaaf_per_game_stats.py uses, plus
a second independent bound on possession itself. NFL is clean (32/32 usable,
50.7-70.7 plays/g) but runs the same gate — a check that only runs where a bug
is already known is a check that misses the next one.

NOT A SIGNAL YET
----------------
Nothing reads these to select, tier or price a pick, and nothing should until
they are graded. Two warnings for whoever does that study:
  * Time of possession is ENDOGENOUS — leading teams run the ball and burn
    clock, so TOP is partly a consequence of the scoreboard. plays_pg is closer
    to intent. Collect both, let the measurement choose.
  * Pace's edge is on TOTALS, and football totals are currently barred
    (TOP_PICK_BARRED; NCAAF totals 33.3% on n=48). So this is groundwork for
    reopening totals, not a quick win on sides.

USAGE
    python collect_pace_stats.py --dry-run
    python collect_pace_stats.py --sport NFL
    python collect_pace_stats.py --season 2026
"""
import argparse
import os
import sys

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
if not SB or not KEY:
    print('collect_pace_stats: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Offensive snaps per game. Bounds are deliberately loose: the job is to catch a
# denominator wrong by a FACTOR, not to police a fast offense. College sits
# ~60-75, NFL ~50-70; nothing legitimate lands near 110.
PLAYS_BOUNDS = {'NCAAF': (35.0, 110.0), 'NFL': (35.0, 110.0)}

# A 60-minute game, so one team's average possession cannot approach 60. Second,
# independent check on the same denominator.
TOP_MIN_BOUNDS = (18.0, 42.0)

SPORTS = {
    'NFL': {
        'table': 'nfl_team_stats',
        'select': 'team,games,pass_attempts,rush_attempts',
        'stats': [
            ('plays_pg', ('pass_attempts', 'rush_attempts'), 1.0,
             'higher', 'Plays/G', ''),
        ],
    },
    'NCAAF': {
        'table': 'ncaaf_team_stats',
        'select': 'team,games,pass_attempts,rush_attempts,possession_time_sec',
        'stats': [
            ('plays_pg', ('pass_attempts', 'rush_attempts'), 1.0,
             'higher', 'Plays/G', ''),
            # possession_time_sec is CUMULATIVE season seconds -> /games/60.
            ('top_min_pg', ('possession_time_sec',), 1.0 / 60.0,
             'higher', 'Time of Poss/G', 'min'),
        ],
    },
}


def _page(path, params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ranked(pairs, higher_is_better):
    """Competition ranking, rank 1 = best, ties share a rank."""
    order = sorted(pairs, key=lambda kv: (-kv[1] if higher_is_better else kv[1]))
    out, prev_val, prev_rank = {}, None, 0
    for i, (team, val) in enumerate(order):
        if prev_val is not None and val == prev_val:
            out[team] = prev_rank
        else:
            prev_rank, prev_val = i + 1, val
            out[team] = prev_rank
    return out


def run(sport, season, dry=False):
    cfg = SPORTS[sport]
    rows = _page(cfg['table'], {'select': cfg['select'], 'season': f'eq.{season}'})
    print(f'\n== {sport}: {cfg["table"]} rows for {season}: {len(rows)}')

    lo, hi = PLAYS_BOUNDS[sport]
    trusted, rejected = [], []
    for r in rows:
        g = _f(r.get('games'))
        if not g or g < 1:
            rejected.append((r.get('team'), 'no games count'))
            continue
        plays = (_f(r.get('pass_attempts')) or 0) + (_f(r.get('rush_attempts')) or 0)
        if not plays:
            rejected.append((r.get('team'), 'no play counts to verify denominator'))
            continue
        ppg = plays / g
        if not (lo <= ppg <= hi):
            rejected.append((r.get('team'),
                             f'{ppg:.0f} plays/g — games={g:.0f} under-counts'))
            continue
        trusted.append(r)

    print(f'   trusted denominator : {len(trusted)}')
    print(f'   rejected            : {len(rejected)}')
    for t, why in sorted(rejected, key=lambda x: str(x[0]))[:5]:
        print(f'       {str(t)[:26]:<26} {why}')
    if len(rejected) > 5:
        print(f'       … and {len(rejected) - 5} more')
    if not trusted:
        print('   x nothing trustworthy — refusing to write')
        return None

    payload = []
    for stat_key, fields, scale, direction, label, unit in cfg['stats']:
        vals = []
        for r in trusted:
            g = _f(r['games'])
            parts = [_f(r.get(f)) for f in fields]
            if all(p is None for p in parts):
                continue
            v = round(sum(p or 0.0 for p in parts) / g * scale, 1)
            # Second, independent sanity bound for possession.
            if stat_key == 'top_min_pg' and not (
                    TOP_MIN_BOUNDS[0] <= v <= TOP_MIN_BOUNDS[1]):
                continue
            vals.append((r['team'], v))
        if not vals:
            print(f'   ! {stat_key}: no values')
            continue
        rk = _ranked(vals, direction == 'higher')
        for team, v in vals:
            payload.append({
                'sport': sport, 'team': team, 'season': season,
                'stat_key': stat_key, 'raw_value': v, 'rank': rk[team],
                'league_size': len(vals), 'direction': direction,
                'display_label': label, 'unit': unit,
            })
        srt = sorted(vals, key=lambda kv: kv[1])
        print(f'   {stat_key:<12} {len(vals):3d} teams · '
              f'{srt[0][1]} ({srt[0][0]}) .. {srt[-1][1]} ({srt[-1][0]})')
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS))
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    print(f'=== collect_pace_stats · season {a.season} ===')
    print('NBA pace / NCAAB tempo / NHL corsi_5v5 already collected via the '
          'matview; MLB has no derivable tempo field (see docstring).')

    payload = []
    for s in ([a.sport] if a.sport else sorted(SPORTS)):
        got = run(s, a.season, dry=a.dry_run)
        if got:
            payload += got

    print(f'\n  rows to write: {len(payload)}')
    if a.dry_run:
        print('  (dry run — nothing written)')
        return 0
    if not payload:
        print('  nothing to write')
        return 1

    wrote = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/team_computed_stats',
            headers=dict(H, **{'Content-Type': 'application/json',
                               'Prefer': 'resolution=merge-duplicates,'
                                         'return=minimal'}),
            json=chunk, timeout=120)
        if r.status_code not in (200, 201, 204):
            print(f'  x write failed {r.status_code}: {(r.text or "")[:300]}')
            return 1
        wrote += len(chunk)

    # Read-back. A 2xx is not proof of a write — that silent-success class has
    # bitten this pipeline repeatedly.
    keys = sorted({p['stat_key'] for p in payload})
    sports = sorted({p['sport'] for p in payload})
    back = _page('team_computed_stats', {
        'select': 'sport,stat_key,team',
        'sport': f'in.({",".join(sports)})',
        'season': f'eq.{a.season}',
        'stat_key': f'in.({",".join(keys)})'})
    print(f'  wrote {wrote} · rows verified in table: {len(back)}')
    return 0 if len(back) >= len(payload) else 1


if __name__ == '__main__':
    raise SystemExit(main())
