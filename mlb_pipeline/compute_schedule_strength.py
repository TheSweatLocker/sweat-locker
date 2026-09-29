"""Strength of Schedule and Strength of Record, every sport.

Andy 2026-09-26: "SOS and SOR added somewhere in game detail across all
sports, will def help in college sports modeling with the red green
highlighting which is better."

THEY ARE NOT THE SAME THING, which is why both are here:

  SOS — how hard the opponents you have played are. Says nothing about
        you. A 1-4 team can have the best SOS in the country.
  SOR — how impressive YOUR results are given that schedule. Roughly:
        would an average team have gone 4-1 against those five teams?
        This is the one that separates teams, and it is the one college
        football arguments are actually about.

    SOR = your win% − (win% an average team would expect vs this slate)

  So +0.30 means you are winning 30 points more often than a neutral
  team would against the same opponents. Negative means the record
  flatters you.

HEAD-TO-HEAD IS EXCLUDED, and it matters more than it sounds. A naive
SOS averages your opponents' win%, but those records INCLUDE their games
against you — so every team you lose to looks stronger precisely because
it beat you. First pass showed Charlotte at 0-2 with an SOS of 1.000 for
exactly that reason. Opponent win% here drops all games against the team
being rated, which is the standard correction.

WHERE IT LANDS: team_stats_rolling, the table the Team Stats card already
renders, as two ordinary stat_keys. No new component, no new fetch, no
new workflow step — the existing card picks them up with its percentile
chip, its head-to-head colouring and its ⓘ, because that card is generic
over stat_key.

    python compute_schedule_strength.py --dry-run
    python compute_schedule_strength.py
    python compute_schedule_strength.py --sport NCAAF
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

TARGET = 'team_computed_stats'
SPORTS = ['NCAAF', 'NFL', 'MLB', 'NBA', 'NHL', 'NCAAB']

# A team needs this many decided games before its record means anything,
# and before it is allowed to contribute to anyone else's SOS.
MIN_GAMES = 2


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:180]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def counts_competitively(sport: str, row: dict) -> bool:
    """False for exhibition games that must not feed SOS or SOR.

    2026-09-28, Andy on the first NHL game detail: "SOS and SOR look
    strangely numbered dont think corretc in nhl." They were not correct.
    Measured: all 130 NHL season-2026 rows in team_recent_games carry
    game-type digits '01' — every single one a PRESEASON game. So each
    team's SOS and SOR were computed from 4-5 exhibitions, which is how
    Dallas ended up rated 0.75 "Strength of Record" and a third of the
    league landed on 0.1667 (literally one win in six).

    Preseason results are the least meaningful games in hockey: coaches
    rotate four lines, split goalies by period and play prospects who will
    not be on the roster. Rating a team on them is worse than having no
    rating, because a number on the card reads as knowledge.

    NHL game IDs encode the type at digits 4-6: 01 preseason, 02 regular
    season, 03 playoffs. Only NHL is filtered here — the other sports do
    not use this id shape, and their own exhibition games are excluded
    upstream of team_recent_games. With this in place NHL simply has no
    SOS/SOR until the regular season produces decided games, which is the
    correct state rather than a fabricated one, and it fills in on its own.
    """
    if sport != 'NHL':
        return True
    gid = str(row.get('game_id') or '')
    if len(gid) < 6:
        return False          # unparseable — do not guess it counts
    return gid[4:6] in ('02', '03')


def compute(sport: str, season: int) -> list[dict]:
    rows = page('team_recent_games', {
        'sport': f'eq.{sport}', 'season': f'eq.{season}',
        'select': 'team,opp,won,game_id'})
    decided = [r for r in rows
               if r.get('won') is not None and r.get('opp')
               and counts_competitively(sport, r)]
    if not decided:
        return []

    # Per-team record, and per-(team, opponent) record so head-to-head can
    # be removed from the opponent's win% below.
    rec = defaultdict(lambda: [0, 0])
    vs = defaultdict(lambda: [0, 0])
    opps = defaultdict(list)
    for r in decided:
        t, o, w = r['team'], r['opp'], bool(r['won'])
        rec[t][0 if w else 1] += 1
        vs[(t, o)][0 if w else 1] += 1
        opps[t].append(o)

    def winpct_excluding(opp: str, against: str):
        """Opponent's win% with its games vs `against` removed.

        Without this, every team that beats you inflates your SOS because
        its record includes that win — which is how a 0-2 team ended up
        with a perfect 1.000 strength of schedule.
        """
        w, l = rec[opp]
        ow, ol = vs[(opp, against)]
        w -= ow
        l -= ol
        n = w + l
        return (w / n) if n > 0 else None

    out = []
    for team, (w, l) in rec.items():
        n = w + l
        if n < MIN_GAMES:
            continue
        ratios = [winpct_excluding(o, team) for o in opps[team]]
        ratios = [x for x in ratios if x is not None]
        if not ratios:
            continue
        sos = sum(ratios) / len(ratios)
        # Win rate a neutral team would expect against this exact slate.
        expected = sum(1.0 - x for x in ratios) / len(ratios)
        sor = (w / n) - expected
        out.append({'team': team, 'sos': round(sos, 4), 'sor': round(sor, 4),
                    'games': n})
    return out


def _ranked(vals: list[dict], key: str) -> dict:
    """Rank 1 = best, TIES SHARE A RANK. Both metrics are higher-is-better:
    a higher SOS means a tougher slate (so the record deserves more
    credit), a higher SOR means outperforming it.

    Competition ranking (1,2,2,4) is not cosmetic here — it is required for
    correctness downstream. Early in a season these values are coarsely
    quantised: a win rate over four opponents can only land on a handful of
    values, so on 2026-09-26 twenty-eight NCAAF teams shared SOS 0.5000
    exactly. Ranking by array position gave them 91 through 118, and the
    client turns rank into a percentile — so GameDetailV2 rendered "40th
    %ile" against "22nd %ile", and advantage() painted one side green and
    the other red, for two schedules that are identical to four decimals.
    That is precisely the manufactured-edge complaint Andy raised about the
    situational section. Every stat already in team_stats_rolling_full
    tie-ranks (29 teams at 1.0 turnovers/game all carry rank 46); this
    brings the computed half in line with the matview half.
    """
    order = sorted(vals, key=lambda x: -x[key])
    out: dict = {}
    prev_val, prev_rank = None, 0
    for i, v in enumerate(order):
        cur = v[key]
        # float equality is the right test: identical arithmetic on identical
        # inputs yields bit-identical results, and near-but-not-equal values
        # are genuinely different schedules that should not be merged.
        if prev_val is not None and cur == prev_val:
            out[v['team']] = prev_rank          # tie -> same rank
        else:
            prev_rank = i + 1                   # skip the consumed slots
            prev_val = cur
            out[v['team']] = prev_rank
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    sports = [args.sport.upper()] if args.sport else SPORTS

    payload = []
    for sport in sports:
        vals = compute(sport, args.season)
        if not vals:
            print(f'  {sport}: no decided games for {args.season} — skipped')
            continue
        rk_sos = _ranked(vals, 'sos')
        rk_sor = _ranked(vals, 'sor')
        size = len(vals)
        for v in vals:
            payload.append({
                'sport': sport, 'team': v['team'], 'season': args.season,
                'stat_key': 'sos', 'raw_value': v['sos'],
                'rank': rk_sos[v['team']], 'league_size': size,
                'direction': 'higher', 'display_label': 'Strength of Sched',
                'unit': '',
            })
            payload.append({
                'sport': sport, 'team': v['team'], 'season': args.season,
                'stat_key': 'sor', 'raw_value': v['sor'],
                'rank': rk_sor[v['team']], 'league_size': size,
                'direction': 'higher', 'display_label': 'Strength of Record',
                'unit': '',
            })
        top = sorted(vals, key=lambda x: -x['sor'])[:3]
        print(f'  {sport}: {size} teams · best SOR ' +
              ', '.join(f"{t['team']} {t['sor']:+.3f}" for t in top))

    print(f'\n  rows to write: {len(payload)}')
    if args.dry_run:
        print('  DRY RUN — no writes.')
        return
    if not payload:
        return

    written = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/{TARGET}'
            '?on_conflict=sport,team,season,stat_key',
            headers=H_W, json=chunk, timeout=120)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ write -> {r.status_code}: {(r.text or "")[:240]}')

    # Read back — a 2xx is not proof the rows landed.
    chk = requests.get(f'{SB}/rest/v1/{TARGET}',
                       headers={**H, 'Prefer': 'count=exact', 'Range': '0-0'},
                       params={'select': 'team', 'season': f'eq.{args.season}',
                               'stat_key': 'in.(sos,sor)'}, timeout=60)
    landed = (chk.headers.get('content-range') or '').split('/')[-1]
    print(f'  wrote {written}/{len(payload)} · rows in table: {landed}')


if __name__ == '__main__':
    main()
