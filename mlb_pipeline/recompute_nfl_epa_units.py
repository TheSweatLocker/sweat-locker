"""Put NFL EPA on the same footing as NCAAF, and label what cannot be.

Andy 2026-09-26, on Carolina @ Cleveland:
  "NFL EPA is still cumulative while NCAAF is per-play. OFF PASS EPA
   26.2 / 4.8 ... Oregon State's card the same evening showed DEF PASS
   EPA 0.424 / 0.199 — correctly per-play. Same column label, two
   definitions, split by sport."

He is right, and it is worse than cosmetic: a percentile computed over a
season TOTAL ranks teams partly by how many games they have played, and
a user comparing an NFL card to an NCAAF card is reading the same words
against numbers two orders of magnitude apart.

    team_stats_rolling_full, NFL:  ROUND(pass_epa, 3)      <- season total
    team_stats_rolling_full, NCAAF: off_epa / plays        <- per play

OFFENSE IS FIXABLE PROPERLY. nfl_team_stats carries pass_attempts and
rush_attempts, so pass_epa / pass_attempts is EPA per dropback and
rush_epa / rush_attempts is EPA per carry — the same quantity NCAAF
shows, and the number analysts actually quote.

DEFENSE IS NOT, AND IS NOT FAKED. nfl_team_defense_stats has `games` and
nothing else — no opponent play counts — so a per-play defensive EPA
cannot be derived without inventing a denominator. Rather than guess at
~33 dropbacks a game and present the result as if it were measured, the
defensive rows keep their per-game value and get an explicit "/G" label.
The user can then see that one is a rate and the other is a per-game
figure, which is the actual state of the data.

Written to team_computed_stats so 20260926d makes these authoritative
over the matview — the same reversible override used for the NCAAF
yardage denominators. Deleting these rows hands the stats straight back.
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Minimum attempts before a per-play rate means anything. Two games of
# dropbacks is ~60; below 25 one bad series dominates the number.
MIN_ATTEMPTS = 25


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
            prev_rank = i + 1
            prev_val = val
            out[team] = prev_rank
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    off = requests.get(f'{SB}/rest/v1/nfl_team_stats', headers=H, params={
        'select': 'team,games,pass_epa,rush_epa,pass_attempts,rush_attempts',
        'season': f'eq.{args.season}', 'season_type': 'eq.REG',
        'limit': '100'}, timeout=60).json()
    dfn = requests.get(f'{SB}/rest/v1/nfl_team_defense_stats', headers=H, params={
        'select': 'team,games,def_pass_epa_allowed,def_rush_epa_allowed',
        'season': f'eq.{args.season}', 'season_type': 'eq.REG',
        'limit': '100'}, timeout=60).json()
    print(f'NFL {args.season}: {len(off)} offense rows, {len(dfn)} defense rows')

    specs = []

    # ── offense: genuine per-play, matching NCAAF ──────────────────────
    for key, epa_col, att_col, label in (
            ('off_pass_epa', 'pass_epa', 'pass_attempts', 'Off Pass EPA'),
            ('off_rush_epa', 'rush_epa', 'rush_attempts', 'Off Rush EPA')):
        vals = []
        for r in off:
            epa, att = _f(r.get(epa_col)), _f(r.get(att_col))
            if epa is None or not att or att < MIN_ATTEMPTS:
                continue
            vals.append((r['team'], round(epa / att, 3)))
        specs.append((key, vals, 'higher', label, ''))

    # ── defense: per-GAME, and the label now says so ───────────────────
    for key, col, label in (
            ('def_pass_epa', 'def_pass_epa_allowed', 'Def Pass EPA/G'),
            ('def_rush_epa', 'def_rush_epa_allowed', 'Def Rush EPA/G')):
        vals = []
        for r in dfn:
            v = _f(r.get(col))
            if v is None:
                continue
            vals.append((r['team'], round(v, 3)))
        specs.append((key, vals, 'lower', label, ''))

    payload = []
    for key, vals, direction, label, unit in specs:
        if not vals:
            print(f'  ⚠ {key}: no usable rows')
            continue
        rk = _ranked(vals, direction == 'higher')
        size = len(vals)
        for team, v in vals:
            payload.append({
                'sport': 'NFL', 'team': team, 'season': args.season,
                'stat_key': key, 'raw_value': v, 'rank': rk[team],
                'league_size': size, 'direction': direction,
                'display_label': label, 'unit': unit,
            })
        best = min(vals, key=lambda kv: rk[kv[0]])
        lo, hi = min(v for _, v in vals), max(v for _, v in vals)
        print(f'  {key:<14} {size:2d} teams · range {lo:+.3f}..{hi:+.3f} · '
              f'best {best[0]} {best[1]:+.3f}  [{label}]')

    print(f'\n  rows to write: {len(payload)}')
    if args.dry_run:
        print('  (dry run — nothing written)')
        return 0

    for i in range(0, len(payload), 500):
        r = requests.post(f'{SB}/rest/v1/team_computed_stats', headers=dict(
            H, **{'Content-Type': 'application/json',
                  'Prefer': 'resolution=merge-duplicates,return=minimal'}),
            json=payload[i:i + 500], timeout=120)
        if r.status_code not in (200, 201, 204):
            print(f'  ✖ write failed {r.status_code}: {(r.text or "")[:250]}')
            return 1

    keys = sorted({s[0] for s in specs})
    back = requests.get(f'{SB}/rest/v1/team_computed_stats', headers=H, params={
        'select': 'stat_key', 'sport': 'eq.NFL', 'season': f'eq.{args.season}',
        'stat_key': f'in.({",".join(keys)})', 'limit': '1000'}, timeout=60).json()
    print(f'  wrote {len(payload)} · verified in table: {len(back)}')
    return 0 if len(back) == len(payload) else 1


if __name__ == '__main__':
    raise SystemExit(main())
