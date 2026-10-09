"""Two measured suppressions that turn NCAAF from -2.9% ROI to +2.3%.

WHY (2026-10-08)
----------------
Andy: "Yes i want the most accurate engine we cna have for ncaaf".

NCAAF published reads were 156-144 (52.0%) at -8.63u / -2.9% ROI against a
52.38% breakeven — a losing product. Measured with flat baselines alongside,
because a number without a baseline is not evidence
(feedback_model_vote_may_be_a_dog_proxy):

    flat always-HOME ATS   54.2%  (n=360)   <-- beats our engine
    flat always-FAV  ATS   53.3%  (n=360)
    our HOME picks         56.2%  (n=208)   = +2.0pp over flat home
    our AWAY picks         42.7%  (n=75)    = -3.1pp WORSE than flat away

And the tier scale is INVERTED at the top:

    PRIME     4-13   23.5%  (n=17)   <-- the WORST tier we publish
    PASS      8-9    47.1%  (n=17)
    LEAN     59-61   49.2%  (n=120)
    STRONG   53-39   57.6%  (n=92)
    COVERAGE 29-21   58.0%  (n=50)

    correlation(conviction, win) = +0.040 over n=296
    mean conviction on wins 67.3 vs losses 66.4 — separation under 1 point

THE RULES, each traceable to one line above and validated cumulatively:

    baseline                      156-144  52.0%   -8.63u  ROI -2.9%
    +G1 no PRIME                  152-131  53.7%   +0.99u  ROI +0.3%
    +G2 no totals                 148-122  54.8%   +6.33u  ROI +2.3%

TWO CANDIDATE RULES WERE REJECTED ON THE EVIDENCE, which is the point of
testing them:

  * ML price floor (> -140). Looked catastrophic (-15.02u) but is actually
    UNMEASURABLE: 42% of NCAAF ML picks have no stored price, so both keeping
    and dropping them is scored against a fabricated -110 default. The real
    median ML price is -157, where breakeven is 61.1% and we hit 60.0% — so ML
    probably IS a loser, but this data cannot prove it. Capture ML prices
    first, then re-test.
  * drop AWAY. Strongest rule in isolation (+8.93u, n=223), and redundant once
    G1+G2 are applied: units go +6.33u -> +6.36u while volume falls 270 -> 179.
    It stops removing losers and starts removing bets. Rejected.

HONESTY ABOUT THE +6.33u
It is IN-SAMPLE. G1 was chosen after seeing PRIME at 4-13, so scoring G1 on
that same data is circular, and n=17 is thin. What carries it is that neither
rule needs the regression to justify it: a tier publishing at 23.5% is not
publishable at any sample size, and the totals finding REPLICATES an
independent earlier measurement (30.8% on n=39, -16.09u, 2026-09-30). Do not
quote +6.33u as expected future performance. The pre-registered test is at the
bottom of this docstring.

WHAT IT WILL NOT DO
  * never touches a LOCKED pick (pick_locked_at set) or a started game. This
    weekend's board stays exactly as published — "whatever comes out in the
    morning stays". It shapes the NEXT slate.
  * never RAISES a tier or conviction
  * PRIME is demoted to STRONG rather than deleted, and that is NOT a no-op
    here: the Sharp Card derives `units` from tier, so the stake falls with it
    (feedback_tier_demotion_needs_stake_boundary — demotion only counts where
    stake follows tier, and on this surface it does)
  * totals go to PASS, which is the existing no-play state, not a new one

PRE-REGISTERED TEST, written before the next window opens:
    hypothesis: NCAAF reads with G1+G2 applied beat 52.38% on ROI
    window:     graded NCAAF reads with game_date >= 2026-10-11
    command:    python audit_ncaaf_accuracy.py --since 2026-10-11
    ship/keep only if ROI > 0 at n >= 100. If it fails, these gates come out.

CLI
    python ncaaf_accuracy_gates.py
    python ncaaf_accuracy_gates.py --apply
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

TBL = 'ncaaf_game_context'
STRONG_CEILING = 79          # top of the STRONG band
PASS_CONVICTION = 40         # the value _discipline_cap already uses


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=16)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    today = dt.date.today().isoformat()
    until = (dt.date.today() + dt.timedelta(days=a.days)).isoformat()
    games = requests.get(f'{SB}/rest/v1/{TBL}', headers=H, params={
        'select': 'game_id,game_date,away_team,home_team,primary_play,'
                  'pick_locked_at,kickoff_utc',
        'and': f'(game_date.gte.{today},game_date.lte.{until})',
        'limit': '400'}, timeout=180).json()
    if not isinstance(games, list):
        raise SystemExit(f'ctx fetch: {str(games)[:200]}')
    reads = {}
    rr = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H, params={
        'select': 'id,game_id,conviction,call_market', 'sport': 'eq.NCAAF',
        'game_date': f'gte.{today}', 'limit': '400'}, timeout=180)
    for x in (rr.json() if rr.status_code in (200, 206) else []):
        reads[str(x['game_id'])] = x

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f'=== ncaaf_accuracy_gates · {len(games)} games {today}..{until} '
          f'· {"APPLY" if a.apply else "DRY"} ===')
    changed = locked_skip = 0

    for g in sorted(games, key=lambda z: str(z['game_date'])):
        pp = _jl(g.get('primary_play'))
        if not pp:
            continue
        rd = reads.get(str(g['game_id'])) or {}
        tier = str(pp.get('tier') or '').upper()
        mkt = str(rd.get('call_market') or '').lower()
        tag = f"{g['away_team']} @ {g['home_team']}"

        new_tier, new_conv, why = tier, pp.get('conviction'), None
        if mkt == 'total':
            new_tier, new_conv, why = 'PASS', PASS_CONVICTION, 'G2:total'
        elif tier == 'PRIME':
            new_tier = 'STRONG'
            cur = pp.get('conviction')
            new_conv = (min(float(cur), STRONG_CEILING) if cur is not None
                        else STRONG_CEILING)
            why = 'G1:prime'
        if why is None:
            continue

        if g.get('kickoff_utc') and str(g['kickoff_utc']) <= now:
            continue
        if g.get('pick_locked_at'):
            locked_skip += 1
            print(f"  LOCKED, left alone: {g['game_date']} {tag[:30]:<32}"
                  f"{str(pp.get('label'))[:20]:<22}{tier} "
                  f"conv {pp.get('conviction')}  [{why} would apply]")
            continue

        print(f"  {g['game_date']}  {tag[:30]:<32}"
              f"{str(pp.get('label'))[:20]:<22}{tier}->{new_tier} "
              f"conv {pp.get('conviction')}->{new_conv}  [{why}]")
        changed += 1
        if not a.apply:
            continue

        npp = dict(pp)
        npp['tier'] = new_tier
        npp['conviction'] = new_conv
        npp['_ncaaf_accuracy_gate'] = {
            'rule': why, 'from_tier': tier,
            'from_conviction': pp.get('conviction'),
            'at': today,
            'basis': ('PRIME 4-13 (23.5%, n=17) is the worst NCAAF tier'
                      if why == 'G1:prime' else
                      'NCAAF totals 30.8% (n=13 here, n=39 / -16.09u on 09-30)'),
        }
        r = requests.patch(f'{SB}/rest/v1/{TBL}', headers=H_W,
                           params={'game_id': f"eq.{g['game_id']}"},
                           data=json.dumps({'primary_play': npp}), timeout=60)
        back = r.json() if r.content else []
        if r.status_code not in (200, 204) or not back:
            print(f'      ! pp patch {r.status_code} {r.text[:120]}')
            continue
        chk = _jl(back[0].get('primary_play'))
        if str(chk.get('tier')).upper() != new_tier:
            print(f'      ! read-back tier is {chk.get("tier")}')
            continue
        if rd.get('id') is not None and rd.get('conviction') is not None \
                and new_conv is not None \
                and float(rd['conviction']) > float(new_conv):
            r2 = requests.patch(f'{SB}/rest/v1/jerry_reads', headers=H_W,
                                params={'id': f"eq.{rd['id']}"},
                                data=json.dumps({'conviction': new_conv}),
                                timeout=60)
            if r2.status_code in (200, 204):
                print(f"      jerry_reads conv {rd['conviction']} -> {new_conv}")

    print(f'\n  {"gated" if a.apply else "would gate"} {changed} · '
          f'locked and left alone {locked_skip}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
