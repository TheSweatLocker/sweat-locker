"""Refresh blended team-stat features on NFL rows the odds window misses.

ANDY 2026-10-12, working the plumbing list.

THE GAP. nfl_game_context builds rows from Odds API events, and the odds
feed only returns ~8 days out. Everything past that keeps whatever it was
written with — which, for the 2026 season, is the preseason enrichment of
2026-07-28/29 running on LAST SEASON's team stats.

Measured today: 358 team-slots beyond the window hold 2025 rates exactly,
across every blended column (penalties, pass_yards, rush_yards, pass_tds).

THIS IS NOT INERT, which is why it is worth a job rather than a shrug.
179 NFL rows sit beyond the window and 164 of them carry a primary_play —
50 STRONG and 2 PRIME among them. Game detail reads primary_play live, so
a user opening a week-8 game sees a tiered pick computed from last
season's numbers. They do self-heal once the game enters the odds window,
but "wrong until shortly before kickoff" is not the same as "fine".

WHAT IT TOUCHES, and what it deliberately does not. Only the blended
team-stat columns and the games_played counts that drive them. It does
NOT write primary_play, tiers, lines or any model output — recomputing
the PICK is recompute_nfl_primary_play's job, and it will pick up fresh
inputs on its next run. Keeping those separate means this job cannot move
a published pick.

features_computed_at IS written here. The contract on that column is
"whoever recomputes the blended team-stat features stamps it" — this job
does exactly that, so stamping it is correct and leaving it alone would
make the column lie in the opposite direction. The narrow PATCHers (MC,
LR, v3, v4, QB, primary_play) still must not.

DRY BY DEFAULT.

CLI
    python refresh_nfl_team_features.py                 # dry, all future
    python refresh_nfl_team_features.py --apply
    python refresh_nfl_team_features.py --since 2026-10-20 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import nfl_game_context as N
from sweat_dates import et_today

SB, H_READ = N.SB, N.H_READ
H_W = {**H_READ, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=None,
                    help='default: today ET')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    since = a.since or et_today()
    season = N._season_for_date(dt.date.fromisoformat(since)) \
        if hasattr(N, '_season_for_date') else dt.date.fromisoformat(since).year

    cur = N.load_team_stats(season)
    pri = N.load_team_stats(season - 1)
    gp = N.load_team_games_played(season)
    print(f'=== refresh_nfl_team_features · from {since} · season {season} ===')
    print(f'    team stats: {len(cur)} current / {len(pri)} prior · '
          f'{len(gp)} teams with games played')
    if not cur:
        print('  ! no current-season team stats — refusing to write '
              'prior-season values over the whole slate')
        return 1

    rows, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
                         params={'select': 'game_id,game_date,home_team,'
                                           'away_team,home_penalties_pg',
                                 'game_date': f'gte.{since}',
                                 'limit': '1000', 'offset': str(off)},
                         timeout=60)
        if r.status_code not in (200, 206):
            print(f'  ! fetch {r.status_code}: {r.text[:160]}')
            return 1
        b = r.json() or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    print(f'    {len(rows)} future context rows\n')

    # Same blend the main enrichment uses, so the two cannot disagree.
    def blend(team, field):
        c = cur.get(team) or {}
        p = pri.get(team) or {}
        return N._nfl_blend_pg(c.get(field), gp.get(team, c.get('games') or 0),
                               p.get(field), p.get('games') or 0,
                               field=field, per_game=True)

    FIELDS = [
        ('penalties', 'penalties_pg'), ('penalty_yards', 'penalty_yds_pg'),
        ('pass_yards', 'pass_yds_pg'), ('pass_tds', 'pass_tds_pg'),
        ('pass_ints', 'pass_ints_pg'), ('rush_yards', 'rush_yds_pg'),
        ('rush_tds', 'rush_tds_pg'), ('rush_first_downs', 'rush_first_downs_pg'),
        ('sacks_suffered', 'sacks_suffered_pg'),
    ]
    # features_computed_at ships ahead of its migration (20261012c). A
    # PATCH naming a column the schema does not have 400s and drops the
    # WHOLE patch — unlike the upsert path in nfl_game_context, which has
    # strip-retry. Probe once rather than lose every write to schema lag.
    _probe = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
                          params={'select': 'features_computed_at',
                                  'limit': '1'}, timeout=30)
    HAS_FCA = _probe.status_code in (200, 206)
    if not HAS_FCA:
        print('  ° features_computed_at not in schema yet — writing the '
              'stat columns and skipping the stamp (apply 20261012c)')

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    changed, same, skipped = 0, 0, 0
    sample = []
    for g in rows:
        h, aw = g.get('home_team'), g.get('away_team')
        if not h or not aw:
            skipped += 1
            continue
        patch = {'home_games_played': gp.get(h, 0),
                 'away_games_played': gp.get(aw, 0)}
        if HAS_FCA:
            patch['features_computed_at'] = now
        for src, suffix in FIELDS:
            patch[f'home_{suffix}'] = blend(h, src)
            patch[f'away_{suffix}'] = blend(aw, src)
        before = g.get('home_penalties_pg')
        after = patch.get('home_penalties_pg')
        if before is not None and after is not None and \
                abs(float(before) - float(after)) < 0.005:
            same += 1
        else:
            changed += 1
            if len(sample) < 6:
                sample.append((str(g['game_date'])[:10], aw, h, before, after))
        if a.apply:
            requests.patch(f"{SB}/rest/v1/nfl_game_context"
                           f"?game_id=eq.{g['game_id']}",
                           headers={**H_W, 'Prefer': 'return=minimal'},
                           json=patch, timeout=30)

    print(f'  home_penalties_pg would change on {changed} rows, '
          f'already current on {same}, skipped {skipped}')
    for d, aw, h, b, af in sample:
        print(f'    {d}  {aw:>3}@{h:<4}  {b} -> {af}')
    if not a.apply:
        print('\n  [DRY] nothing written. Re-run with --apply.')
        return 0

    # A 204 is not a write — prove it landed.
    _sel = ('game_date,features_computed_at,home_penalties_pg' if HAS_FCA
            else 'game_date,home_penalties_pg')
    chk = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
                       params={'select': _sel,
                               'game_date': f'gte.{since}', 'limit': '1000'},
                       timeout=60).json()
    if not HAS_FCA:
        # Without the column there is nothing to stamp, so verify the thing
        # this job actually changed instead of reporting 0/N and looking
        # like a failed write.
        fresh = sum(1 for x in chk if x.get('home_penalties_pg') is not None)
        print(f'\n  VERIFY by re-read: {fresh}/{len(chk)} rows carry a '
              f'penalties value (stamp pending migration 20261012c)')
        return 0
    stamped = sum(1 for x in chk
                  if str(x.get('features_computed_at') or '')[:10] == et_today())
    print(f'\n  VERIFY by re-read: {stamped}/{len(chk)} rows carry today\'s '
          f'features_computed_at')
    return 0 if stamped else 1


if __name__ == '__main__':
    sys.exit(main())
