"""Join NBA injuries onto nba_game_context, weighted by real importance.

ANDY 2026-10-13: "do both properly, everything should be done properly or
logged if we have issues for root cause correction later".

THE GAP. nba_game_context.home_starters_out / away_starters_out /
home_injury_impact / away_injury_impact are read by the engine
(nba_game_context.py:305 reads injury_impact) and are 0 of 81 populated.
Nothing has ever written them. Star availability is the most price-moving
input in basketball, so this is the largest NBA readiness gap ahead of the
2026-10-20 opener — bigger than the externals gap.

WHY IT IS NOW BUILDABLE. Two sources already exist and were simply never
joined:

  nba_injuries          ESPN /nba/injuries, pulled by nba_injuries_pull.py.
                        player_name, team_abbrev, status (OUT / GTD),
                        reason.
  nba_player_game_logs  58,600 rows, and critically it carries `started`
                        as a real boolean alongside minutes and points. So
                        "is this player a starter" is a MEASUREMENT here,
                        not a guess from a minutes threshold.

WHAT IS WRITTEN, and the naming is deliberate:

  *_starters_out    text[] — the NAMES of OUT players whose start rate is
                    >= START_RATE_MIN over their most recent season with
                    games. A player who came off the bench all year is not
                    a starter being out, and counting him as one is what
                    would make this column lie.

                    The type was verified against the PostgREST schema, not
                    assumed: a first pass wrote an integer count and every
                    PATCH failed 22P02 "expected JSON array". The column is
                    text[], which is strictly more useful — a signal can
                    test both presence and who.
  *_injury_impact   points-per-game lost, summed over OUT players. A
                    magnitude rather than a count, because two bench
                    players out is not the same event as one star out.

ONLY `OUT` COUNTS. nba_injuries is 186 GTD to 28 OUT; GTD ("game-time
decision") is not an absence and treating it as one would mark almost every
team as depleted every night.

THREE TRAPS HANDLED EXPLICITLY, each one already paid for elsewhere today:

 1. nba_injuries ACCUMULATES rather than replacing — it held 77 rows stamped
    2026-08-25 alongside 137 from today, with players appearing twice. Rows
    are deduped to the latest updated_at per (team, player) before use, or a
    7-week-old absence would be reported as current.
 2. team_abbrev vs full name. Injuries and game logs use "GSW"; the context
    table uses "Golden State Warriors". Mapped explicitly for all 30 clubs —
    the same mismatch silently produced 0 of 31 matches in
    enrich_team_trends until today.
 3. PGRST204 strip-retry on the PATCH. One absent column otherwise rejects
    the whole write, which is exactly how 17 trend fields were being lost in
    MLB and NBA.

Dry by default. --apply to write.

CLI
    python enrich_nba_injuries_to_context.py
    python enrich_nba_injuries_to_context.py --apply
    python enrich_nba_injuries_to_context.py --apply --days 10
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

H_WRITE = {**ML.H, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

#: A player must have started at least this share of his games to count as
#: a starter being out.
START_RATE_MIN = 0.50
#: Minimum games before a start rate is trusted at all.
MIN_GAMES = 5

#: abbrev -> full team name as nba_game_context stores it.
ABBREV = {
    'ATL': 'Atlanta Hawks', 'BOS': 'Boston Celtics', 'BKN': 'Brooklyn Nets',
    'BRK': 'Brooklyn Nets', 'CHA': 'Charlotte Hornets',
    'CHI': 'Chicago Bulls', 'CLE': 'Cleveland Cavaliers',
    'DAL': 'Dallas Mavericks', 'DEN': 'Denver Nuggets',
    'DET': 'Detroit Pistons', 'GS': 'Golden State Warriors',
    'GSW': 'Golden State Warriors', 'HOU': 'Houston Rockets',
    'IND': 'Indiana Pacers', 'LAC': 'LA Clippers',
    'LAL': 'Los Angeles Lakers', 'MEM': 'Memphis Grizzlies',
    'MIA': 'Miami Heat', 'MIL': 'Milwaukee Bucks',
    'MIN': 'Minnesota Timberwolves', 'NO': 'New Orleans Pelicans',
    'NOP': 'New Orleans Pelicans', 'NY': 'New York Knicks',
    'NYK': 'New York Knicks', 'OKC': 'Oklahoma City Thunder',
    'ORL': 'Orlando Magic', 'PHI': 'Philadelphia 76ers',
    'PHX': 'Phoenix Suns', 'PHO': 'Phoenix Suns',
    'POR': 'Portland Trail Blazers', 'SAC': 'Sacramento Kings',
    'SA': 'San Antonio Spurs', 'SAS': 'San Antonio Spurs',
    'TOR': 'Toronto Raptors', 'UTA': 'Utah Jazz', 'UTAH': 'Utah Jazz',
    'WAS': 'Washington Wizards', 'WSH': 'Washington Wizards',
}


def page(table, params, cap=200000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(3)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _key(name):
    return re.sub(r'[^a-z]', '', str(name or '').lower())


def build_importance():
    """player key -> {'start_rate', 'mpg', 'ppg', 'games', 'season'}.

    Uses the most recent season in which the player actually has games, so
    a rookie with only 2026-27 preseason logs is scored on those and a
    veteran on his last real season rather than on a 3-game sample.
    """
    logs = page('nba_player_game_logs', {
        'select': 'player_name,team_abbrev,season,minutes,points,started,'
                  'game_date'})
    by = defaultdict(lambda: defaultdict(list))
    for x in logs:
        k = _key(x.get('player_name'))
        if not k:
            continue
        by[k][str(x.get('season'))].append(x)
    out = {}
    for k, seasons in by.items():
        season = max(seasons)                 # lexical works on 'YYYY-YY'
        rows = seasons[season]
        if len(rows) < MIN_GAMES and len(seasons) > 1:
            # too thin to judge; fall back to the previous season
            prev = sorted(seasons)[-2]
            if len(seasons[prev]) > len(rows):
                season, rows = prev, seasons[prev]
        mins = [f(r.get('minutes')) for r in rows]
        mins = [m for m in mins if m is not None]
        pts = [f(r.get('points')) for r in rows]
        pts = [p for p in pts if p is not None]
        starts = [1 if r.get('started') else 0 for r in rows
                  if r.get('started') is not None]
        out[k] = {
            'games': len(rows), 'season': season,
            'mpg': statistics.fmean(mins) if mins else 0.0,
            'ppg': statistics.fmean(pts) if pts else 0.0,
            'start_rate': (statistics.fmean(starts) if starts else 0.0),
        }
    return out


def latest_injuries():
    """(team_full, player_key) -> row, deduped to the newest updated_at.

    nba_injuries accumulates: measured 2026-10-13 it held 77 rows stamped
    2026-08-25 beside 137 from that day, same players in both. Without this
    a 7-week-old absence reads as current.
    """
    rows = page('nba_injuries', {'select': '*'})
    best = {}
    for x in rows:
        team = ABBREV.get(str(x.get('team_abbrev') or '').upper())
        if not team:
            continue
        k = (team, _key(x.get('player_name')))
        u = str(x.get('updated_at') or '')
        if k not in best or u > best[k][0]:
            best[k] = (u, x)
    return {k: v[1] for k, v in best.items()}, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--days', type=int, default=14,
                    help='upcoming window to enrich')
    a = ap.parse_args()

    inj, raw_n = latest_injuries()
    imp = build_importance()
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    until = (today + timedelta(days=a.days)).isoformat()

    print('=== NBA injuries -> game context ===')
    print(f'  nba_injuries: {raw_n} rows -> {len(inj)} after dedupe to latest '
          f'per (team, player)')
    print(f'  importance built for {len(imp)} players from '
          f'nba_player_game_logs (uses the real `started` flag)')

    out_by_team = defaultdict(list)
    for (team, pk), row in inj.items():
        if str(row.get('status') or '').upper() not in ('OUT', 'O'):
            continue           # GTD is not an absence
        m = imp.get(pk) or {}
        out_by_team[team].append({
            'player': row.get('player_name'),
            'ppg': m.get('ppg', 0.0), 'mpg': m.get('mpg', 0.0),
            'start_rate': m.get('start_rate', 0.0),
            'games': m.get('games', 0),
            'starter': (m.get('start_rate', 0.0) >= START_RATE_MIN
                        and m.get('games', 0) >= MIN_GAMES),
        })
    n_out = sum(len(v) for v in out_by_team.values())
    n_start = sum(1 for v in out_by_team.values() for p in v if p['starter'])
    n_unknown = sum(1 for v in out_by_team.values() for p in v
                    if p['games'] == 0)
    print(f'  {n_out} players listed OUT across {len(out_by_team)} teams · '
          f'{n_start} are starters (start rate >= {START_RATE_MIN:.0%}, '
          f'>= {MIN_GAMES} games)')
    print(f'  {n_unknown} OUT players have NO game logs — counted in neither '
          f'starters_out nor impact, and listed below rather than assumed\n')

    games = [g for g in page('nba_game_context', {'select': '*'})
             if str(today) <= str(g.get('game_date'))[:10] <= until]
    print(f'  {len(games)} context rows in [{today} .. {until}]')
    wrote = 0
    for g in sorted(games, key=lambda z: str(z.get('game_date'))):
        patch = {}
        desc = []
        for side in ('home', 'away'):
            team = str(g.get(f'{side}_team'))
            outs = out_by_team.get(team, [])
            starters = [p for p in outs if p['starter']]
            # text[] of names, per the schema — never a count.
            patch[f'{side}_starters_out'] = [str(p['player'])
                                             for p in starters]
            patch[f'{side}_injury_impact'] = round(
                sum(p['ppg'] for p in outs), 1)
            if starters:
                desc.append(f'{side}: ' + ', '.join(
                    f"{p['player']} ({p['ppg']:.1f}ppg)" for p in starters))
        if not a.apply:
            print(f"  [DRY] {g.get('game_date')} "
                  f"{str(g.get('away_team'))[:18]:<20} @ "
                  f"{str(g.get('home_team'))[:18]:<20} "
                  f"starters_out {len(patch['away_starters_out'])}/"
                  f"{len(patch['home_starters_out'])}  impact "
                  f"{patch['away_injury_impact']}/{patch['home_injury_impact']}"
                  + (f"  [{' | '.join(desc)}]" if desc else ''))
            wrote += 1
            continue
        # PGRST204 strip-retry: one absent column must not reject the write.
        pr = requests.patch(
            f'{ML.SB}/rest/v1/nba_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=patch, timeout=20)
        stripped = []
        for _ in range(len(patch)):
            if pr.status_code in (200, 204):
                break
            try:
                body = pr.json()
            except Exception:                                  # noqa: BLE001
                break
            if str(body.get('code')) != 'PGRST204':
                break
            mm = re.search(r"'([^']+)' column", str(body.get('message') or ''))
            col = mm.group(1) if mm else None
            if not col or col not in patch:
                break
            patch.pop(col)
            stripped.append(col)
            if not patch:
                break
            pr = requests.patch(
                f'{ML.SB}/rest/v1/nba_game_context?game_id=eq.{g["game_id"]}',
                headers=H_WRITE, json=patch, timeout=20)
        if pr.status_code in (200, 204):
            wrote += 1
            if stripped:
                print(f'    ⚠ stripped absent column(s) {stripped}')
        else:
            print(f'    ✗ {g["game_id"]}: {pr.status_code} {pr.text[:120]}')

    print(f'\n{"[DRY] would write" if not a.apply else "✓ wrote"} {wrote} rows')
    if n_unknown:
        print('\n  OUT players with no game logs (not counted — logged for '
              'root cause):')
        for team, v in sorted(out_by_team.items()):
            for p in v:
                if p['games'] == 0:
                    print(f'      {team:<24} {str(p["player"])[:26]}')
        print('  These are mostly rookies/two-way players with no NBA logs '
              'yet. They cannot move starters_out until they have games, '
              'which is correct — but if a known starter appears here it '
              'means nba_player_game_logs is missing him.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
