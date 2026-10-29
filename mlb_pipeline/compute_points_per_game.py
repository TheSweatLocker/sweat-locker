"""Points SCORED per game for NFL + NCAAF, from the same games as points allowed.

== 2026-10-19 - WHY THIS EXISTS ==
ANDY, asking for the Team Stats card to pair a team's offence against the
opponent's defence: "altering team stats to more offensive rushing yards
listed next to opponent defensive rushing yards allowed, poit allowed vs
point against, do i need to name all or you catch ddrift?"

The yardage pairs already exist. The POINTS pair does not, and it is the one
he named. Verified today against the live schema:

    nfl_team_stats          32 cols  -  no points column at all
    nfl_team_defense_stats  12 cols  -  def_ppg
    ncaaf_team_stats        44 cols  -  no points column at all
    ncaaf_team_defense_stats         -  def_ppg

So the card can show what a football team CONCEDES and not what it SCORES.
B68 noted the asymmetry ("probably just an omission"); it blocks the row Andy
asked for by name.

WHY BOTH SIDES ARE RECOMPUTED, NOT JUST THE MISSING ONE. A matchup row is a
comparison, so the two numbers have to come from one universe. def_ppg is
computed upstream over whatever games that puller saw; points scored computed
here comes from <sport>_game_results. Pairing them would silently compare two
denominators. Both are therefore computed from the SAME result rows, and the
script REFUSES to write when its own points_allowed disagrees with the
upstream def_ppg by more than MAX_PPG_DRIFT - because a disagreement means one
of the two universes is wrong, and that is a finding, not a row to publish.

WHY team_computed_stats AND NOT A MIGRATION. 20260926d makes any stat_key
present in that table authoritative for its (sport, season), suppressing the
matview half. team_stats_rolling_full has been amended by ~10 migrations and
its live definition cannot be read from here, so a CREATE OR REPLACE is the
view-drift trap (feedback_publishable_view_drift) with a matview's blast
radius. Rollback is a DELETE of these rows - nothing else.

NCAAF CAVEAT, STATED RATHER THAN HIDDEN. ncaaf_game_results carries FBS games
only, so an FBS team's games against FCS opponents are in neither the
numerator nor the denominator. The average stays self-consistent; it is simply
computed over the FBS slate. Teams under MIN_GAMES get no row at all rather
than a thin one.

Ranks are competition-ranked (1,2,2,4) so equal values cannot render as
different percentiles - same as compute_schedule_strength._ranked.

USAGE
    python compute_points_per_game.py --dry-run          # compare, write nothing
    python compute_points_per_game.py --sport NFL
    python compute_points_per_game.py                    # NFL + NCAAF
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

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

#: a team needs this many completed games before a per-game average means
#: anything. Week 6 NFL teams have 5-6; an NCAAF team with 1 FBS game does
#: not get a row.
MIN_GAMES = 3
#: how far our points_allowed may sit from the upstream def_ppg before this
#: script treats the pair as coming from two different universes and refuses.
#: 1.5 pts/g is roughly one game's worth of difference over a 6-game sample;
#: anything larger is a denominator mismatch, not rounding.
MAX_PPG_DRIFT = 1.5

RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results'}
#: regular season only, and the column that says so differs per sport
REG_FILTER = {'NFL': ('game_type', 'REG'), 'NCAAF': ('season_type', 'regular')}
UPSTREAM_DEF = {'NFL': 'nfl_team_defense_stats',
                'NCAAF': 'ncaaf_team_defense_stats'}


def _page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=120)
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
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _ranked(pairs, higher_is_better: bool) -> dict:
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


def run(sport: str, season: int, dry: bool, force: bool) -> int:
    tbl = RESULTS[sport]
    col, val = REG_FILTER[sport]
    rows = _page(tbl, {'select': f'home_team,away_team,home_score,away_score,{col}',
                       'season': f'eq.{season}'})
    games = [r for r in rows
             if _f(r.get('home_score')) is not None
             and _f(r.get('away_score')) is not None
             and str(r.get(col) or '').lower() in (val.lower(), '')]
    bar = '=' * 72
    print(f'\n{bar}\n{sport} {season}: {len(games)} completed regular-season games '
          f'(of {len(rows)} rows)\n{bar}')
    if not games:
        print('  0 games - a WIRING error, not a finding. Check season + '
              f'{col} filter.')
        return 1

    scored, allowed, played = defaultdict(float), defaultdict(float), defaultdict(int)
    for g in games:
        h, a = str(g['home_team']), str(g['away_team'])
        hs, as_ = _f(g['home_score']), _f(g['away_score'])
        scored[h] += hs
        allowed[h] += as_
        played[h] += 1
        scored[a] += as_
        allowed[a] += hs
        played[a] += 1

    # -- the upstream defence table defines the RANKING UNIVERSE ------------
    # Read before the universe is fixed, because it decides who is in it.
    # `games` is read alongside def_ppg because the drift gate below cannot
    # tell a WRONG number from a LAGGING one without the denominator.
    up, up_games = {}, {}
    try:
        for r in _page(UPSTREAM_DEF[sport], {'select': 'team,def_ppg,games',
                                             'season': f'eq.{season}'}):
            v = _f(r.get('def_ppg'))
            if v is not None:
                up[str(r['team'])] = v
                g = _f(r.get('games'))
                if g is not None:
                    up_games[str(r['team'])] = int(g)
    except RuntimeError as e:
        print(f'  ! upstream def_ppg unreadable: {e}')

    thin = sorted(t for t in played if played[t] < MIN_GAMES)
    teams = sorted(t for t in played if played[t] >= MIN_GAMES)
    print(f'  teams with >= {MIN_GAMES} games : {len(teams)}')
    if thin:
        print(f'  teams skipped (too thin)  : {len(thin)}  e.g. '
              + ', '.join(f'{t} ({played[t]}g)' for t in thin[:4]))
    # 20260926d is explicit that a computed stat_key REPLACES the matview's
    # whole universe for that sport+season, and that "rank and league_size
    # are only meaningful relative to one ranking universe". The first
    # dry-run produced 140 NCAAF teams against the upstream's 133: the extra
    # 7 are non-FBS sides that appear in ncaaf_game_results only as
    # opponents. Writing them would silently re-rank every FBS team against
    # a 140-team pool while every other row on the same card ranks over 133.
    # So the upstream defence table, which already owns points_allowed_pg,
    # defines who is eligible. NFL is 32 = 32 and unaffected.
    if up:
        outside = [t for t in teams if t not in up]
        teams = [t for t in teams if t in up]
        if outside:
            print(f'  outside the {len(up)}-team upstream universe: '
                  f'{len(outside)} dropped  e.g. ' + ', '.join(outside[:4]))
    if not teams:
        print('  nothing above the games floor - refusing to write')
        return 1

    pts_for = [(t, round(scored[t] / played[t], 1)) for t in teams]
    pts_ag = [(t, round(allowed[t] / played[t], 1)) for t in teams]
    ours = dict(pts_ag)
    gaps = sorted(((abs(ours[t] - up[t]), t, ours[t], up[t])
                   for t in teams if t in up), reverse=True)
    print(f'\n  CROSS-CHECK vs {UPSTREAM_DEF[sport]}.def_ppg on {len(gaps)} teams:')
    if gaps:
        print(f'    mean |gap| {sum(g[0] for g in gaps) / len(gaps):.2f} pts/g '
              '- worst 4:')
        for gap, t, o, u in gaps[:4]:
            print(f'      {t:<22} ours {o:>5.1f}   upstream {u:>5.1f}   '
                  f'gap {gap:>4.1f}')
        # ══ 2026-10-29 · A LAG IS NOT A DISAGREEMENT ════════════════════
        # This gate refused to write on every run from 10-27 18:12 to
        # 10-29, leaving NCAAF points_pg/points_allowed_pg two days stale
        # while NFL (which runs second and passes) stayed current. The two
        # teams it flagged were explained to the second decimal:
        #
        #   ours 8 games, upstream 7      drop our week 9 and we MATCH
        #   Jacksonville State  159/7 = 22.71  vs upstream 22.71
        #   Middle Tennessee    193/7 = 27.571 vs upstream 27.57
        #
        # Exactly 4 of 228 teams disagreed on game count and ALL FOUR were
        # ours = upstream + 1: ncaaf_team_defense_stats ingests on a weekly
        # cadence, ncaaf_game_results is same-night. JSU's extra game was a
        # shutout (0 allowed vs a 22.7 average) and MTSU's was 44 allowed vs
        # 27.6, which is the only reason those two crossed 1.5 pts/g while
        # Kennesaw State and New Mexico State — same lag — stayed under it.
        # So the threshold was sampling how EXTREME the newest game was, not
        # whether the data is sound.
        #
        # A team we are AHEAD on is therefore exempt: the gap has a known
        # cause and our own two numbers still come from one game set, which
        # is what the card pairs (20260926d makes the computed stat_key
        # authoritative, so points_pg and points_allowed_pg here replace
        # upstream rather than sit beside it).
        #
        # A team we are BEHIND on stays in the gate — that direction means
        # OUR results are missing a game, which is a real finding.
        # --force is deliberately NOT the fix: forcing would have paired a
        # week-9 points_pg against a week-8 def_ppg, the exact
        # two-denominator bug this script exists to prevent.
        ahead = {t for t in teams
                 if up_games.get(t) is not None and played[t] > up_games[t]}
        behind = {t for t in teams
                  if up_games.get(t) is not None and played[t] < up_games[t]}
        if ahead:
            print(f'  {len(ahead)} teams where WE are ahead of upstream '
                  '(exempt from the gate, gap explained by the denominator):')
            for t in sorted(ahead)[:6]:
                print(f'      {t:<22} ours {played[t]}g   upstream '
                      f'{up_games[t]}g   gap {abs(ours[t] - up[t]):.1f}')
        if behind:
            print(f'  !! {len(behind)} teams where UPSTREAM is ahead of us — '
                  'our results are missing games:')
            for t in sorted(behind)[:6]:
                print(f'      {t:<22} ours {played[t]}g   upstream '
                      f'{up_games[t]}g')
        over = [g for g in gaps if g[0] > MAX_PPG_DRIFT and g[1] not in ahead]
        if over and not force:
            print(f'\n  >> {len(over)} teams exceed MAX_PPG_DRIFT={MAX_PPG_DRIFT} '
                  'pts/g on the SAME game count. The two universes')
            print('     disagree, so pairing them would compare different '
                  'denominators. REFUSING to write.')
            for gap, t, o, u in over[:6]:
                print(f'      {t:<22} ours {o:>5.1f}   upstream {u:>5.1f}   '
                      f'gap {gap:>4.1f}   ({played[t]}g both)')
            print('     Re-run with --force only once the gap is explained.')
            return 2
    else:
        print('    no overlap - denominator unverifiable, writing points_pg only')

    specs = [('points_pg', pts_for, 'higher', 'Points/G')]
    if gaps:
        specs.append(('points_allowed_pg', pts_ag, 'lower', 'Points Allowed/G'))
    payload = []
    for stat_key, vals, direction, label in specs:
        rk = _ranked(vals, direction == 'higher')
        size = len(vals)
        for team, v in vals:
            payload.append({
                'sport': sport, 'team': team, 'season': season,
                'stat_key': stat_key, 'raw_value': v, 'rank': rk[team],
                'league_size': size, 'direction': direction,
                'display_label': label, 'unit': '',
                # refreshed_at is a column DEFAULT, and a DEFAULT only fires
                # on INSERT - these are upserts, so an unstamped row keeps
                # the timestamp of its first write and matchup_story's
                # staleness guard then drops a perfectly good value.
                'refreshed_at': _NOW,
            })
        best = min(vals, key=lambda kv: rk[kv[0]])
        print(f'  {stat_key:<20} {size:3d} teams · best {best[0]} {best[1]}')

    print(f'\n  rows to write: {len(payload)}')
    if dry:
        print('  (dry run - nothing written)')
        return 0

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

    # A 2xx is not proof of a write. Read it back.
    keys = ','.join(s[0] for s in specs)
    back = _page('team_computed_stats', {
        'select': 'stat_key,team,raw_value', 'sport': f'eq.{sport}',
        'season': f'eq.{season}', 'stat_key': f'in.({keys})'})
    print(f'  wrote {wrote} · verified in table: {len(back)}')
    return 0 if len(back) >= len(payload) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(RESULTS), action='append')
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='write even when our points_allowed disagrees upstream')
    a = ap.parse_args()
    rc = 0
    for sp in (a.sport or sorted(RESULTS)):
        rc = max(rc, run(sp, a.season, a.dry_run, a.force))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
