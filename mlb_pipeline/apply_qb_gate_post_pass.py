"""Re-apply the QB injury gate to STORED picks, on every copy, every day.

WHY (2026-10-08)
----------------
Andy: "what do you think is the best answr to ensure model precition reflect
injuries liek starting QBs rpviding a pick with predate model data or missing
factor sis not the rigth answer".

The honest answer is that we already detect this correctly and we already
decided what to do about it. `nfl_qb_injury_gate` reads `nfl_injuries`,
identifies a ruled-out starter, and caps the pick to COVERAGE / conviction 45
because a model whose inputs predate the news cannot claim conviction about
it. That reasoning is right and it is not what is broken.

TWO THINGS ARE BROKEN, AND THEY ARE THE SAME TWO THINGS BROKEN EVERYWHERE
ELSE IN THIS PIPELINE TODAY:

1. THE GATE RUNS ONCE, AT GENERATION. An injury reported after the pick is
   written never re-triggers it. Caleb Williams was reported Out on 2026-10-08
   for a 10-11 game whose pick was locked 10-04. Nothing re-reads the injury
   table after a pick exists, so the gate's correctness depends entirely on
   the news arriving before the pick — which is backwards, because injury news
   arrives LAST.

2. IT CAPS ONE COPY. On CHI @ GB the gate had capped
   `primary_play` to COVERAGE / 45 while `jerry_reads.conviction` still read
   **79** — the badge gated, the writeup not. Measured across the 15-game week-5
   slate, that was 1 of 15 games where the two stored convictions disagreed,
   and it was the one game with a ruled-out quarterback.

So: run the gate continuously over stored picks, and write the verdict to
every copy. No new model, no new signal — the same decision, enforced.

WHAT IT WILL NOT DO
  * never touches a started game
  * never RAISES a tier or conviction — a gate only ever restricts, so a
    cleared injury does not silently promote a pick back onto a card
  * never writes when the gate returns no verdict
  * `jerry_reads` is synced DOWN to the gated conviction only; side, market
    and line are never touched here (those belong to the line normalizer)

ON THE DEEPER QUESTION — why not just model the backup?
Because the models cannot represent him. Team efficiency, power ratings and
the Monte Carlo are all accumulated with the starter on the field, and the
QB rating carried as a team input is the starter's. Adjusting that honestly
needs a calibrated starter-vs-backup delta we have not measured. Until we
have, "we do not have a model opinion on this game" is the truthful output,
and COVERAGE says exactly that while still showing the game. Capping is not a
placeholder for prediction here; it IS the correct answer.

CLI
    python apply_qb_gate_post_pass.py --days 10
    python apply_qb_gate_post_pass.py --days 10 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML
import nfl_qb_injury_gate as QG

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

_RANK = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2, 'COVERAGE': 3, 'PASS': 4,
         'SKIP': 5}


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=10)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    until = (dt.date.today() + dt.timedelta(days=args.days)).isoformat()
    games = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H, params={
        'select': 'game_id,game_date,week,season_week,home_team,away_team,'
                  'home_qb_name,away_qb_name,home_qb_madden_ovr,'
                  'away_qb_madden_ovr,primary_play,kickoff_utc',
        'and': f'(game_date.gte.{today},game_date.lte.{until})',
        'limit': '200'}, timeout=120).json()
    reads = {str(a['game_id']): a for a in requests.get(
        f'{SB}/rest/v1/jerry_reads', headers=H, params={
            'select': 'id,game_id,conviction,call_text',
            'sport': 'eq.NFL', 'game_date': f'gte.{today}',
            'limit': '300'}, timeout=120).json()}

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f'=== QB gate post-pass · {len(games)} NFL games {today}..{until} '
          f'· {"APPLY" if args.apply else "DRY"} ===')
    flagged = pp_fixed = rd_fixed = 0

    for g in sorted(games, key=lambda z: (str(z['game_date']), z['away_team'])):
        if g.get('kickoff_utc') and str(g['kickoff_utc']) <= now:
            continue
        v = QG.assess(g, SB, H)
        if not v:
            continue
        flagged += 1
        pp = _jl(g.get('primary_play'))
        tag = f"{g['away_team']}@{g['home_team']}"
        print(f"  {g['game_date']}  {tag:<10}{v['severity'].upper():<14}"
              f"{v['team']} {v['qb']} {v['status']}  (rep wk {v.get('report_week')})")
        if not pp:
            print('      no primary_play — nothing to gate')
            continue
        before = (pp.get('tier'), pp.get('conviction'))
        after = QG.apply_to_pick(dict(pp), g, SB, H)
        at, ac = after.get('tier'), after.get('conviction')

        # A gate RESTRICTS. Refuse any result that would raise either axis,
        # so a cleared or downgraded injury can never promote a stored pick.
        if _RANK.get(str(at).upper(), 9) < _RANK.get(str(before[0]).upper(), 9):
            print(f'      refusing tier RAISE {before[0]} -> {at}')
            at = before[0]
            after['tier'] = before[0]
        if before[1] is not None and ac is not None and float(ac) > float(before[1]):
            print(f'      refusing conviction RAISE {before[1]} -> {ac}')
            ac = before[1]
            after['conviction'] = before[1]

        if (at, ac) != before:
            print(f'      primary_play {before[0]} conv {before[1]} '
                  f'-> {at} conv {ac}')
            pp_fixed += 1
            if args.apply:
                r = requests.patch(
                    f'{SB}/rest/v1/nfl_game_context?game_id=eq.{g["game_id"]}',
                    headers=H_W, json={'primary_play': after}, timeout=60)
                if r.status_code not in (200, 204):
                    print(f'        ! pp patch {r.status_code} {r.text[:110]}')
        else:
            print(f'      primary_play already {at} conv {ac}')

        # THE SECOND COPY. This is the half that was missing: the gate capped
        # primary_play and left jerry_reads at its ungated conviction.
        a = reads.get(str(g['game_id']))
        if not a or ac is None:
            continue
        rc = a.get('conviction')
        if rc is not None and float(rc) > float(ac):
            print(f'      jerry_reads conv {rc} -> {ac}   <-- was ungated')
            rd_fixed += 1
            if args.apply:
                r = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{a["id"]}',
                                   headers=H_W, json={'conviction': ac},
                                   timeout=60)
                if r.status_code not in (200, 204):
                    print(f'        ! read patch {r.status_code} {r.text[:110]}')

    print(f'\n  {flagged} game(s) with a QB flag · '
          f'primary_play {"changed" if args.apply else "would change"} '
          f'{pp_fixed} · jerry_reads {"synced" if args.apply else "to sync"} '
          f'{rd_fixed}')


if __name__ == '__main__':
    main()
