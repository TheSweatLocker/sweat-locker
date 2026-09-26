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
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
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


def compute(sport: str, season: int) -> list[dict]:
    rows = page('team_recent_games', {
        'sport': f'eq.{sport}', 'season': f'eq.{season}',
        'select': 'team,opp,won'})
    decided = [r for r in rows if r.get('won') is not None and r.get('opp')]
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
    """Rank 1 = best. Both metrics are higher-is-better: a higher SOS means
    a tougher slate (so the record deserves more credit), a higher SOR
    means outperforming it."""
    order = sorted(vals, key=lambda x: -x[key])
    return {v['team']: i + 1 for i, v in enumerate(order)}


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
