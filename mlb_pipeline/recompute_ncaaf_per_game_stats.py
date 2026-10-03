"""Recompute NCAAF per-game volumetric stats over a trustworthy universe.

Andy 2026-09-26, twice, on two different cards:
  "539 yds/g -> 58th pct"
  "PASS 266 -> 41st, RUSH 192 -> 41st, TOTAL 457 -> 42nd ... the rate
   stats on the same card calibrate correctly. The break is isolated to
   the three per-game yardage rows plus turnovers."

He had it exactly localised. Here is the mechanism.

THE NUMERATOR AND THE DENOMINATOR COME FROM DIFFERENT UNIVERSES.
team_stats_rolling_full computes these as

    ncaaf_team_stats.<cumulative> / ncaaf_team_stats.games

The numerator is CFBD's season-to-date total, which covers every game a
team has played, FBS or FCS. The denominator is `games`, backfilled by
20260916f from a COUNT over ncaaf_game_results — a table that only
carries FBS results. For an FCS team we hold one game or none, so the
division is a full season's yardage over a denominator of 1:

    South Dakota State   2033 "yards per game"   (games=1)
    Tarleton State       1991                    (games=1)
    UT Rio Grande Valley 1912                    (games=1)

Measured 2026-09-26: 83 of 216 NCAAF rows have an implausible denominator
(>110 offensive plays per game — real football is 60-75). Those 83 rows
sort straight to the top of a `higher is better` ranking and occupy the
best ~90 ranks, so every genuine FBS offense is shoved down the board:
Ohio State's 523 yds/g rendered as the 55th percentile, and Auburn's 457
as the 42nd. The rate stats (EPA, success rate, third-down %) are ratios
that never touch `games`, which is precisely why Andy saw them calibrate
correctly on the same card. His diagnosis was right.

WHY THIS SCRIPT RATHER THAN A MATVIEW FIX. The correct home for this is
team_stats_rolling_full, but that matview has been amended by ~10
migrations and there is no SQL path from here to read its live
definition. Rewriting it from a guess is the CREATE OR REPLACE drift trap
(feedback_publishable_view_drift) with a matview's blast radius. So the
corrected rows are written to team_computed_stats — the real table added
by 20260926c — and 20260926d makes a stat_key present there authoritative
for that sport+season, suppressing the matview half. When the matview is
next rebuilt properly, deleting these rows hands the stat back with no
other change. Same mechanism that carries SOS/SOR.

WHAT HAPPENS TO THE UNTRUSTWORTHY TEAMS. They get no row at all, rather
than a fabricated one. GameDetailV2 already hides a stat row when both
sides are empty and renders one-sided when only one team has data, so an
FBS-vs-FCS card degrades to showing the FBS team's real number against a
blank — which is honest, and better than the current state where the FCS
column shows 2033 and wins the comparison.

Ranks are competition-ranked (1,2,2,4): identical values must not render
as different percentiles. See compute_schedule_strength._ranked.
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 2026-10-03 · GUARD THE .env READ. This open() was unconditional, so the
# script raised FileNotFoundError the moment it ran in GitHub Actions, where
# the env comes from secrets and no .env file exists. It had never mattered
# because the script was only ever run by hand locally — until I scheduled it
# in nightly_cross_sport.sh on 10-02, at which point it failed on its first
# nightly and NCAAF pass_yds_pg stayed pinned at the previous day while its
# sibling recompute_nfl_epa_units.py (same commit, adjacent line, guarded
# open) updated fine. That asymmetry is what gave it away.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

_NOW = datetime.now(timezone.utc).isoformat()

# A team's offensive snap count per game. Real college football sits near
# 60-75; the bound is deliberately loose because its job is to catch a
# denominator that is wrong by a FACTOR (games=1 when four were played),
# not to police a fast-paced offense. Nothing legitimate lands near 110.
MIN_PLAYS_PG = 35.0
MAX_PLAYS_PG = 110.0

# (stat_key, numerator fields to sum, direction, display label, unit).
# Metadata mirrors team_stats_rolling_full verbatim so the client renders
# a corrected row exactly as it renders a matview row.
SPECS = [
    ('pass_yds_pg',    ('pass_yards',),               'higher', 'Pass Yds/G',    'yd'),
    ('rush_yds_pg',    ('rush_yards',),               'higher', 'Rush Yds/G',    'yd'),
    ('total_yds_pg',   ('pass_yards', 'rush_yards'),  'higher', 'Total Yds/G',   'yd'),
    ('penalty_yds_pg', ('penalty_yards',),            'lower',  'Penalty Yds/G', 'yd'),
    ('turnovers_pg',   ('turnovers',),                'lower',  'Turnovers/G',   ''),
]


def _page(path: str, params: dict) -> list:
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


def _ranked(pairs: list[tuple[str, float]], higher_is_better: bool) -> dict:
    """Competition ranking, rank 1 = best, ties share a rank."""
    order = sorted(pairs, key=lambda kv: (-kv[1] if higher_is_better else kv[1]))
    out: dict = {}
    prev_val, prev_rank = None, 0
    for i, (team, val) in enumerate(order):
        if prev_val is not None and val == prev_val:
            out[team] = prev_rank
        else:
            prev_rank = i + 1
            prev_val = val
            out[team] = prev_rank
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    rows = _page('ncaaf_team_stats', {
        'select': 'team,games,pass_yards,rush_yards,penalty_yards,turnovers,'
                  'pass_attempts,rush_attempts',
        'season': f'eq.{args.season}'})
    print(f'ncaaf_team_stats rows for {args.season}: {len(rows)}')

    trusted, rejected = [], []
    for r in rows:
        g = _f(r.get('games'))
        if not g or g < 1:
            rejected.append((r['team'], 'no games count'))
            continue
        plays = (_f(r.get('pass_attempts')) or 0) + (_f(r.get('rush_attempts')) or 0)
        ppg = plays / g if plays else None
        if ppg is None:
            rejected.append((r['team'], 'no play counts to verify denominator'))
            continue
        if not (MIN_PLAYS_PG <= ppg <= MAX_PLAYS_PG):
            rejected.append((r['team'], f'{ppg:.0f} plays/g — games={g:.0f} under-counts'))
            continue
        trusted.append(r)

    print(f'  trusted denominator : {len(trusted)}')
    print(f'  rejected            : {len(rejected)}')
    for t, why in sorted(rejected)[:6]:
        print(f'      {t:<26} {why}')
    if len(rejected) > 6:
        print(f'      … and {len(rejected) - 6} more')

    if not trusted:
        print('  ✖ nothing trustworthy — refusing to write')
        return 1

    payload = []
    for stat_key, fields, direction, label, unit in SPECS:
        vals = []
        for r in trusted:
            g = _f(r['games'])
            parts = [_f(r.get(f)) for f in fields]
            if all(p is None for p in parts):
                continue
            vals.append((r['team'], round(sum(p or 0.0 for p in parts) / g, 1)))
        if not vals:
            print(f'  ⚠ {stat_key}: no values')
            continue
        rk = _ranked(vals, direction == 'higher')
        size = len(vals)
        for team, v in vals:
            payload.append({
                'sport': 'NCAAF', 'team': team, 'season': args.season,
                'stat_key': stat_key, 'raw_value': v, 'rank': rk[team],
                'league_size': size, 'direction': direction,
                'display_label': label, 'unit': unit,
                # 2026-10-02 · STAMP refreshed_at EXPLICITLY. The column is
                # `timestamptz DEFAULT now()` and a DEFAULT only fires on
                # INSERT; these writes are upserts, so every re-run took the
                # UPDATE path and the timestamp stayed pinned to the row's
                # first insert. Verified today: a run that corrected Penn
                # State 287.0 -> 243.8 and Northwestern 403.5 -> 269.0 left
                # all 133 pre-existing rows reading refreshed_at =
                # 2026-09-26. Values fresh, timestamp six days stale.
                #
                # That is not cosmetic. matchup_story.load_team_stats now
                # WITHHOLDS rows that are stale relative to the slate date,
                # so an unstamped row gets dropped even when its value is
                # correct — the guard would have silently emptied the NCAAF
                # matchup story. Same fix compute_schedule_strength.py took
                # on 09-30 for the same reason.
                'refreshed_at': _NOW,
            })
        best = min(vals, key=lambda kv: rk[kv[0]])
        print(f'  {stat_key:<16} {size:3d} teams · best {best[0]} {best[1]}')

    print(f'\n  rows to write: {len(payload)}')
    if args.dry_run:
        print('  (dry run — nothing written)')
        return 0

    wrote = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/team_computed_stats', headers=dict(
                H, **{'Content-Type': 'application/json',
                      'Prefer': 'resolution=merge-duplicates,return=minimal'}),
            json=chunk, timeout=120)
        if r.status_code not in (200, 201, 204):
            print(f'  ✖ write failed {r.status_code}: {(r.text or "")[:300]}')
            return 1
        wrote += len(chunk)

    # Read-back. A 2xx is not proof of a write (see the silent-success
    # class that has bitten this pipeline repeatedly).
    keys = {s[0] for s in SPECS}
    back = _page('team_computed_stats', {
        'select': 'stat_key,team', 'sport': 'eq.NCAAF',
        'season': f'eq.{args.season}',
        'stat_key': f'in.({",".join(sorted(keys))})'})
    print(f'  wrote {wrote} · rows verified in table: {len(back)}')
    return 0 if len(back) == len(payload) else 1


if __name__ == '__main__':
    raise SystemExit(main())
