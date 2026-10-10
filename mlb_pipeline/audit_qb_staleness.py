"""Is the QB our model is using actually playing? — NFL

ANDY 2026-10-09, on where football should improve. The answer this points at:
MLB's entire measured edge is moneyline (70.2%, +19.2% ROI, n=94 in the live
window) and MLB has ONE dominant knowable pre-game input — the starting
pitcher. Football's analogue is the quarterback, and the engine's own prose
already admits it is blind to him:

    "TB QB Baker Mayfield listed Out — EVERY model number on this game —
     projected score, total, margin and the Monte Carlo — was computed from
     team efficiency that predates it."

WHAT THIS MEASURES
For every game, whether the QB named in nfl_game_context is listed Out or
Doubtful on that week's injury report. If he is, every QB-derived feature on
the row describes someone who will not take a snap:
    {home,away}_qb_name, _qb_madden_ovr, _qb_top10_flag,
    _qb_vs_team_career_*, _qb_vs_team_recent_*, madden_qb_delta_home

WHAT IT FOUND ON FIRST RUN (2026-10-09)
    before 2026-09-28   27 stale-QB games · 0 warned (0%) · incl 4 PRIME, 6 STRONG
    2026-09-28 onward    5 stale-QB games · 5 warned (100%) · 1 LEAN, 4 COVERAGE
So DETECTION WAS FIXED around 09-28 and now catches every case — that is a
real improvement and this script is not claiming otherwise. Two gaps remain:

  1. THE RESPONSE IS ABSTENTION, NOT CORRECTION. A caught game gets capped to
     LEAN/COVERAGE, so we stop publishing rather than producing a right
     number. Each catch is a game we decline instead of price.
  2. THE FEATURES ARE STILL WRONG. Two warned games still carry a Madden
     rating for the absent QB — Lamar Jackson 94.0 on the upcoming BAL game,
     Caleb Williams 90.0. `madden_qb_delta_home` is a model input, so the
     projection is not merely unflagged-wrong, it is actively crediting a
     94-rated quarterback to a team that will not have him.

THE FIX THIS ARGUES FOR, and why it is not applied here
When the named QB is Out, NULL the QB-derived features rather than leaving
them describing the wrong player. Absent is honest; a 94 for a scratched
starter is false, and it biases the model toward that team. Deliberately NOT
done in this script because:
  * it changes model inputs, therefore picks, and picks are LOCKED
    (pick_locked_at) — rewriting inputs under a published pick is the drift
    this project built the lock to stop;
  * guessing the BACKUP is not available either. nfl_starters is wrong on 72%
    (project_nfl_starters_alphabetical_1001 — the ESPN roster is
    alphabetical), so there is no trustworthy replacement to swap in. Nulling
    is the only honest option and it is Andy's call.

WRITES NOTHING.

CLI
    python audit_qb_staleness.py
    python audit_qb_staleness.py --upcoming-only
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
OUT_STATUSES = ('out', 'doubtful')
#: Every context column computed FROM the named quarterback. If he is not
#: playing, each of these describes the wrong person.
QB_FEATURES = (
    '{s}_qb_madden_ovr', '{s}_qb_top10_flag',
    '{s}_qb_vs_team_career_qb_rating', '{s}_qb_vs_team_career_starts',
    '{s}_qb_vs_team_recent_qb_rating', '{s}_qb_vs_team_recent_n_starts',
)


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _same_person(a, b):
    """Loose name match, both directions.

    Injury reports and context disagree on suffixes and initials ("Michael
    Penix Jr." vs "Michael Penix"), so an exact match would miss real cases.
    Substring in BOTH directions catches those without matching unrelated
    players, because a QB surname is distinctive within one team-week.
    """
    a, b = (a or '').strip().lower(), (b or '').strip().lower()
    if not a or not b:
        return False
    return a in b or b in a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--upcoming-only', action='store_true',
                    dest='upcoming_only')
    ap.add_argument('--today', default='2026-10-09')
    a = ap.parse_args()

    inj = [x for x in _page('nfl_injuries',
                            {'select': 'season,week,team,player_name,'
                                       'position,injury_status,report_date'})
           if str(x.get('position')) == 'QB'
           and str(x.get('injury_status') or '').lower() in OUT_STATUSES]
    sel = ('game_date,season,week,away_team,home_team,away_qb_name,'
           'home_qb_name,primary_play,madden_qb_delta_home,pick_locked_at,'
           + ','.join(f.format(s=s) for s in ('home', 'away')
                      for f in QB_FEATURES))
    ctx = _page('nfl_game_context', {'select': sel})
    print(f'=== NFL QB staleness · {len(inj)} QB Out/Doubtful reports · '
          f'{len(ctx)} context rows')

    by_week = collections.defaultdict(list)
    for c in ctx:
        by_week[(str(c.get('season')), str(c.get('week')))].append(c)

    found = []
    for i in inj:
        nm = str(i.get('player_name') or '')
        for c in by_week.get((str(i['season']), str(i['week'])), []):
            for side in ('home', 'away'):
                if str(c[f'{side}_team']) != str(i['team']):
                    continue
                if not _same_person(nm, c.get(f'{side}_qb_name')):
                    continue
                pp = c.get('primary_play') or {}
                blob = json.dumps(pp)
                live_feats = {f.format(s=side): c.get(f.format(s=side))
                              for f in QB_FEATURES
                              if c.get(f.format(s=side)) is not None}
                found.append({
                    'date': str(c.get('game_date') or '')[:10],
                    'matchup': f"{c['away_team']} @ {c['home_team']}",
                    'side': side, 'qb': nm, 'status': i['injury_status'],
                    'tier': str(pp.get('tier') or '-'),
                    'warned': ('_qb_injury' in blob or 'listed Out' in blob
                               or 'listed Doubtful' in blob),
                    'locked': bool(c.get('pick_locked_at')),
                    'stale_feats': live_feats,
                    'madden_delta': c.get('madden_qb_delta_home'),
                })
    if a.upcoming_only:
        found = [f for f in found if f['date'] >= a.today]
    found.sort(key=lambda z: z['date'])
    print(f'    STALE: {len(found)} team-games name a QB who is '
          f'Out/Doubtful\n')
    if not found:
        print('    None. Every named QB is on the field.')
        return 0

    warned = [f for f in found if f['warned']]
    print(f'    warned by the engine: {len(warned)}/{len(found)} '
          f'({100 * len(warned) / len(found):.0f}%)')
    tiers = collections.Counter(f['tier'] for f in found)
    print(f'    tiers shipped: {dict(tiers)}')
    risky = [f for f in found if f['tier'] in ('PRIME', 'STRONG')
             and not f['warned']]
    if risky:
        print(f'\n    !! {len(risky)} PRIME/STRONG picks on a stale QB with '
              f'NO warning:')
        for f in risky:
            print(f'       {f["date"]} {f["matchup"]:<12} {f["side"]}QB='
                  f'{f["qb"][:20]:<21} {f["status"]:<9} tier={f["tier"]}')

    still_fed = [f for f in found if f['stale_feats']]
    print(f'\n    {len(still_fed)} of {len(found)} still FEED the model '
          f'features for the absent QB:')
    for f in still_fed[-12:]:
        feats = ', '.join(f'{k.split("_qb_")[-1]}={v}'
                          for k, v in f['stale_feats'].items())
        print(f'       {f["date"]} {f["matchup"]:<12} {f["qb"][:18]:<19} '
              f'{f["status"]:<9} warned={"Y" if f["warned"] else "N"} '
              f'locked={"Y" if f["locked"] else "N"}')
        print(f'            stale -> {feats[:120]}')
        if f['madden_delta'] is not None:
            print(f'            madden_qb_delta_home={f["madden_delta"]} '
                  f'(a MODEL INPUT built from the absent QB)')

    print('\n' + '=' * 70)
    print('  WHAT TO DO, and why this script does not do it')
    print('  Nulling the QB features is the honest correction — absent beats')
    print('  a 94 for a scratched starter, which actively biases the model')
    print('  toward that team. Not applied here because it changes model')
    print('  inputs and therefore picks, and these picks are LOCKED; and')
    print('  because no trustworthy backup exists to swap in (nfl_starters is')
    print('  wrong on 72%). So: Andy decides, and the null-not-guess shape is')
    print('  the recommendation.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
