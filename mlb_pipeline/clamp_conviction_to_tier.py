"""When a cap lowers the tier, lower the conviction with it.

WHY (2026-10-08)
----------------
Andy, on the NCAAF board before the weekend lock: "Yes we need to ensure NCAAF
is good to go on all fronts." Measured 14 of 66 upcoming NCAAF picks where the
tier and the conviction disagree against our own published taxonomy
(PRIME 80-100 / STRONG 65-79 / LEAN 50-64 / READ 30-49). Users read both off
the same card:

    Duke -6.5            tier STRONG   conv 95
    Penn State ML        tier STRONG   conv 90
    Ohio ML              tier LEAN     conv 86
    Florida -11.5        tier STRONG   conv 84
    North Dakota St ML   tier STRONG   conv 81

THE CAPS ARE NOT THE BUG. They are deliberate and evidence-backed:

    Duke  _edge_cap {to: STRONG, from: PRIME,
          reason: 'lr_v1 PRIME unproven in NCAAF (5-10, n=15) — capped'}

A PRIME that lr_v1 has gone 5-10 on should absolutely be capped. The bug is
that `_edge_cap` lowers the TIER and leaves the conviction where it was, so a
capped pick still advertises 95/100. The adjacent cap already does it right:

    Florida +9.5  _discipline_cap {tier: PASS, max_conviction: 40}  -> conv 40

`_discipline_cap` carries max_conviction and applies it. `_edge_cap` has no
such field. One cap clamps both axes, the other clamps one.

This is the same defect shape as the NFL QB gate capping `primary_play` to
COVERAGE/45 while `jerry_reads.conviction` still read 79 (fixed today in
apply_qb_gate_post_pass.py). A gate that restricts one copy of one axis is a
gate that half-fires.

WHAT IT DOES
Clamps conviction to the ceiling of the tier the pick actually carries, on
BOTH stored copies (primary_play and jerry_reads), but ONLY where a cap is
recorded in the blob. That restriction matters: where no cap exists, tier and
conviction were derived independently and disagreeing is a different problem
(see below) that needs a decision, not a clamp.

WHAT IT WILL NOT DO
  * never RAISES conviction, and never changes the tier, side, market or line
  * never touches a started game
  * never clamps a pick with no recorded cap — those are reported for a human
  * leaves COVERAGE alone: it is an internal gate verdict, not one of the
    published conviction bands, so it has no defensible ceiling here

THE PART THIS DOES NOT FIX, ON PURPOSE
Four upcoming NCAAF picks carry tier STRONG with conviction 57-63 and
`_edge_cap: None` — no cap fired. There the rule branch awarded STRONG on
edge+confluence while the score landed in the LEAN band, i.e. two independent
derivations of the same thing disagreeing at source. Clamping would hide that.
It is reported and left for Andy, because the honest fix is deciding which
derivation governs, not papering over the gap.

CLI
    python clamp_conviction_to_tier.py --sport NCAAF
    python clamp_conviction_to_tier.py --sport NCAAF --apply
    python clamp_conviction_to_tier.py --apply          # NFL + NCAAF
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

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context'}

#: Top of each published band (app/faq.tsx). COVERAGE is deliberately absent.
CEILING = {'PRIME': 100, 'STRONG': 79, 'LEAN': 64, 'READ': 49, 'PASS': 40}

#: Keys whose presence means "a gate deliberately lowered this pick".
CAP_KEYS = ('_edge_cap', '_discipline_cap', '_qb_injury_gate', '_qb_gate',
            '_lr_juice_cap_reason', '_heavy_ml_reroute')


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _caps(pp: dict) -> list[str]:
    return [k for k in CAP_KEYS if pp.get(k) not in (None, {}, [], '')]


def run(sport: str, apply: bool, days: int) -> tuple[int, int]:
    tbl = CTX.get(sport)
    if not tbl:
        print(f'  {sport}: no context table')
        return 0, 0
    today = dt.date.today().isoformat()
    until = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    games = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params={
        'select': 'game_id,game_date,away_team,home_team,primary_play,'
                  'kickoff_utc',
        'and': f'(game_date.gte.{today},game_date.lte.{until})',
        'limit': '400'}, timeout=180).json()
    if not isinstance(games, list):
        print(f'  {sport}: {str(games)[:160]}')
        return 0, 0
    reads = {}
    rr = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H, params={
        'select': 'id,game_id,conviction', 'sport': f'eq.{sport}',
        'game_date': f'gte.{today}', 'limit': '400'}, timeout=180)
    for a in (rr.json() if rr.status_code in (200, 206) else []):
        reads[str(a['game_id'])] = a

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    clamped, flagged = 0, 0
    print(f'=== {sport} · {len(games)} games {today}..{until} · '
          f'{"APPLY" if apply else "DRY"} ===')

    for g in sorted(games, key=lambda z: str(z['game_date'])):
        if g.get('kickoff_utc') and str(g['kickoff_utc']) <= now:
            continue
        pp = _jl(g.get('primary_play'))
        tier = str(pp.get('tier') or '').upper()
        conv = pp.get('conviction')
        if not tier or conv is None or tier not in CEILING:
            continue
        ceil = CEILING[tier]
        if float(conv) <= ceil:
            continue
        tag = f"{g['away_team']} @ {g['home_team']}"
        caps = _caps(pp)
        if not caps:
            flagged += 1
            print(f"  ? {g['game_date']}  {tag[:30]:<32}"
                  f"{str(pp.get('label'))[:20]:<22}{tier:<8}conv {conv} "
                  f"> {ceil} but NO CAP RECORDED — left alone, needs a "
                  f"decision")
            continue
        why = caps[0]
        reason = pp.get(why)
        rtxt = (reason.get('reason') if isinstance(reason, dict) else reason)
        print(f"  {g['game_date']}  {tag[:30]:<32}"
              f"{str(pp.get('label'))[:20]:<22}{tier:<8}"
              f"conv {conv} -> {ceil}   [{why}] {str(rtxt)[:60]}")
        clamped += 1
        if not apply:
            continue

        new_pp = dict(pp)
        new_pp['conviction'] = ceil
        new_pp['_conviction_clamp'] = {
            'from': conv, 'to': ceil, 'tier': tier, 'cap': why,
            'at': dt.date.today().isoformat(),
            'why': 'cap lowered the tier; conviction follows the tier ceiling',
        }
        r = requests.patch(f'{SB}/rest/v1/{tbl}',
                           headers=H_W,
                           params={'game_id': f"eq.{g['game_id']}"},
                           data=json.dumps({'primary_play': new_pp}),
                           timeout=60)
        back = r.json() if r.content else []
        if r.status_code not in (200, 204) or not back:
            print(f'      ! pp patch {r.status_code} {r.text[:120]}')
            continue
        chk = _jl(back[0].get('primary_play'))
        if int(chk.get('conviction') or -1) != ceil:
            print(f'      ! read-back says {chk.get("conviction")}')
            continue

        # THE SECOND COPY — the half that was missing on the QB gate.
        a = reads.get(str(g['game_id']))
        if a and a.get('conviction') is not None and \
                float(a['conviction']) > ceil:
            r2 = requests.patch(f'{SB}/rest/v1/jerry_reads', headers=H_W,
                                params={'id': f"eq.{a['id']}"},
                                data=json.dumps({'conviction': ceil}),
                                timeout=60)
            if r2.status_code in (200, 204):
                print(f"      jerry_reads conv {a['conviction']} -> {ceil}")
            else:
                print(f'      ! read patch {r2.status_code}')

    print(f'  {"clamped" if apply else "would clamp"} {clamped} · '
          f'{flagged} flagged with no cap (not touched)')
    return clamped, flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--days', type=int, default=12)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    sports = [a.sport.upper()] if a.sport else ['NFL', 'NCAAF']
    tc = tf = 0
    for s in sports:
        c, f = run(s, a.apply, a.days)
        tc += c
        tf += f
    print(f'\nTOTAL {"clamped" if a.apply else "would clamp"} {tc} · '
          f'{tf} need a decision')
    return 0


if __name__ == '__main__':
    sys.exit(main())
