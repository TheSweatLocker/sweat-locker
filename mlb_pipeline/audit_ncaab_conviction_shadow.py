"""Shadow the rebuilt NCAAB conviction against the live gates.

== 2026-11-13 . WHY THIS EXISTS =========================================
ncaab_conviction replaces a dead constant (266 of 274 rows inside 50-54)
with a calibrated probability (50-97). The resolver stamps it onto every
pick under `_cal_*` and deliberately does NOT overwrite
primary_play.conviction, because five live gates encode the old meaning:

    jerry_anchor_potd._conviction_tier    conv >= 80 -> "prime", and that
                                          string reaches the POTD publish lock
    generate_sweat_card CARD_UNPROVEN_GATE['NCAAB']  min_conviction 51
    conviction_scale.ANCHORS['NCAAB']     (51, 52, 54, 62), measured 11-07
                                          against the OLD distribution
    steam_room_ladder                     "NBA/NHL/NCAAB cap at conviction
                                           55 and could never reach STRONG"
    the app                               "{conviction}% confidence" subtitle

A de-vigged -800 favourite is a true 89, so adopting the number without
re-pinning those gates would manufacture a PRIME on heavy chalk -- the
display-value-became-a-gating-tier bug that has already cost two
negative-edge POTDs and 58 live cards. So the rebuild ships measured and
shadowed, and this prints the delta every night so the switch is thrown on
evidence rather than on the strength of one replay
([[feedback_suppression_gate_needs_shadow]]).

WHAT TO LOOK FOR. The adoption is safe when, over a useful run of slates,
`would pass` is at or below `passes today` on BOTH gates under the re-pinned
anchors. On the 11-07..11-12 replay it was: POTD 50 -> 36 eligible, card
50 -> 22 legs.

Exits 0 always by default: this is a report, not a gate. --strict exits 1
when adoption would be LOOSER than today on either surface, which is the
one outcome that should block it.

    python audit_ncaab_conviction_shadow.py                 # today
    python audit_ncaab_conviction_shadow.py --days-back 7
    python audit_ncaab_conviction_shadow.py --strict
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

import conviction_scale as CS                      # noqa: E402
import ncaab_conviction as NC                      # noqa: E402

#: The anchors adoption would install, measured on the NEW distribution over
#: the 11-07..11-12 board (n=162 priced picks). Re-measure with this script
#: before adopting -- that is what `suggested anchors` below prints.
# 2026-11-13 . ADOPTED. These are the LIVE anchors now; the audit
# re-measures them against tonight's board and reports drift.
PROPOSED_ANCHORS = (50.0, 54.0, 63.0, 69.0)


def _et_today() -> str:
    return datetime.now(ZoneInfo('America/New_York')).date().isoformat()


def load(dates: list) -> list:
    r = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H, timeout=90,
                     params={'game_date': f"in.({','.join(dates)})",
                             'select': 'game_date,game_id,home_team,away_team,'
                                       'primary_play', 'limit': '3000'})
    if r.status_code != 200:
        raise RuntimeError(f'ncaab_game_context {r.status_code}: {r.text[:300]}')
    return [x for x in (r.json() or []) if x.get('primary_play')]


def _pct(v: list, p: float) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    return float(s[min(len(s) - 1, int(p * len(s)))])


def run(days_back: int = 0, strict: bool = False) -> int:
    base = datetime.fromisoformat(_et_today()).date()
    dates = [(base - timedelta(days=i)).isoformat() for i in range(days_back + 1)]
    rows = load(sorted(dates))
    print(f'== audit_ncaab_conviction_shadow · {min(dates)} .. {max(dates)} ==')
    print(f'  {len(rows)} NCAAB picks\n')
    if not rows:
        print('  no picks — nothing to shadow')
        return 0

    from generate_sweat_card import card_unproven_gate_ok

    old_c, new_c = [], []
    old_card = new_card = old_potd = new_potd = 0
    src = Counter()
    missing_stamp = 0
    deltas = []

    saved = CS.ANCHORS.get('NCAAB')
    for r in rows:
        pp = r['primary_play'] or {}
        oc = pp.get('conviction')
        nc = pp.get('_cal_conviction')
        if '_cal_conviction' not in pp:
            missing_stamp += 1
        src[pp.get('_cal_source') or 'not_stamped'] += 1
        price = pp.get('_cal_price_used')
        if price is None:
            # the gate's own odds band needs a price; -110 is the NCAAB
            # spread/total quote and is what the resolver used
            price = -110
        if oc is not None:
            old_c.append(float(oc))
            CS.ANCHORS['NCAAB'] = saved
            if card_unproven_gate_ok('NCAAB', oc, price)[0]:
                old_card += 1
            if CS.potd_eligible('NCAAB', oc):
                old_potd += 1
        if nc is not None:
            new_c.append(float(nc))
            CS.ANCHORS['NCAAB'] = PROPOSED_ANCHORS
            if card_unproven_gate_ok('NCAAB', nc, price)[0]:
                new_card += 1
            if CS.potd_eligible('NCAAB', nc):
                new_potd += 1
        if oc is not None and nc is not None:
            deltas.append((float(nc) - float(oc), r, pp))
    CS.ANCHORS['NCAAB'] = saved

    if missing_stamp:
        print(f'  ! {missing_stamp} of {len(rows)} picks carry NO '
              f'_cal_conviction — the resolver stamp did not run on them')

    def dist(v, lab):
        if not v:
            print(f'  {lab:18s} none')
            return
        print(f'  {lab:18s} n={len(v):4d}  min {min(v):.0f}  '
              f'p50 {_pct(v,.5):.0f}  p75 {_pct(v,.75):.0f}  '
              f'p90 {_pct(v,.9):.0f}  max {max(v):.0f}')
    print('  CONVICTION DISTRIBUTION')
    dist(old_c, 'live (old scale)')
    dist(new_c, 'calibrated')
    if new_c:
        print(f'\n  suggested anchors for conviction_scale.ANCHORS[\'NCAAB\']: '
              f'({_pct(new_c,.5):.1f}, {_pct(new_c,.75):.1f}, '
              f'{_pct(new_c,.9):.1f}, {max(new_c):.1f})')
        print(f'  this script is pinned at: {PROPOSED_ANCHORS}')
    print(f'\n  p_win source: {dict(src)}')
    unp = src.get('unpriced', 0) + src.get('none', 0)
    if unp:
        print(f'  {unp} pick(s) carry NO calibrated conviction — no two-way '
              f'price. That is deliberate: an unquoted game had been reading '
              f'97 from the model alone.')

    print('\n  GATES (old conviction + old anchors) vs (calibrated + proposed)')
    looser = []
    for lab, o, n in (('card_unproven_gate_ok', old_card, new_card),
                      ('conviction_scale.potd_eligible', old_potd, new_potd)):
        verdict = 'STRICTER' if n < o else ('same' if n == o else 'LOOSER')
        print(f'    {lab:32s} today {o:4d}  ->  would be {n:4d}   {verdict}')
        if n > o:
            looser.append((lab, o, n))

    # The card gate is a raw floor, not a percentile, so re-pinning the
    # anchors does nothing for it. Say what floor adoption would need --
    # that is the actionable number, and the whole point of the report.
    if new_c:
        from generate_sweat_card import CARD_UNPROVEN_SPORT_GATE as CARD_UNPROVEN_GATE
        cur = CARD_UNPROVEN_GATE.get('NCAAB', {}).get('min_conviction')
        need = None
        for floor in range(50, 98):
            k = sum(1 for c in new_c if c > floor)
            if k <= old_card:
                need = (floor, k)
                break
        print(f'\n  CARD_UNPROVEN_GATE[\'NCAAB\'].min_conviction is {cur}. '
              f'Against the calibrated scale')
        if need:
            print(f'  it would need to be >= {need[0]} to pass no more than '
                  f'today\'s {old_card} ({need[1]} would pass).')
        else:
            print(f'  no floor at or below 97 reproduces today\'s {old_card}.')

    if deltas:
        deltas.sort(key=lambda x: -abs(x[0]))
        print('\n  biggest movers (calibrated - live):')
        for d, r, pp in deltas[:8]:
            print(f'    {d:+6.0f}  {r["game_date"]}  '
                  f'{str(pp.get("label"))[:30]:30s} {str(pp.get("type")):6s} '
                  f'live {pp.get("conviction")} -> cal {pp.get("_cal_conviction")}'
                  f'  raw_delta {pp.get("_cal_raw_delta_pp")}')

    # A credited edge above zero means a market has earned weight in
    # ncaab_conviction.W_MODEL. Until then this must print nothing.
    credited = [pp.get('_cal_credited_edge_pp') for _, _, pp in deltas
                if pp.get('_cal_credited_edge_pp')]
    nz = [c for c in credited if c and abs(float(c)) > 1e-9]
    print(f'\n  credited edge non-zero on {len(nz)} pick(s)  '
          f'(W_MODEL {NC.W_MODEL})')
    if nz:
        print('    a market has earned weight — re-read ncaab_conviction '
              'before trusting these')

    if looser and strict:
        print('\n  FAIL: adoption would be LOOSER than today on '
              + ', '.join(l[0] for l in looser))
        return 1
    # == ADOPTION DRIFT ================================================
    # conviction IS _cal_conviction as of 2026-11-13 (Andy: "adopt").
    # If the two diverge, something downstream is writing conviction
    # after the resolver and the sport has gone back to a number that
    # nothing measured.
    _drift = []
    for _r in rows:
        _p = _r['primary_play'] or {}
        if _p.get('conviction') != _p.get('_cal_conviction'):
            _drift.append(_p)
    print('')
    if _drift:
        print('  !! ADOPTION DRIFT: ' + str(len(_drift)) + ' of '
              + str(len(rows)) + ' picks carry conviction != _cal_conviction')
        for _p in _drift[:6]:
            print('       ' + str(_p.get('label')) + ': live '
                  + str(_p.get('conviction')) + ' vs calibrated '
                  + str(_p.get('_cal_conviction')))
    else:
        print('  adoption holds: conviction == _cal_conviction on all '
              + str(len(rows)) + ' picks')

    print('\n  (report only; adopting the number is a five-gate migration '
          'named in the module docstring)')
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--days-back', type=int, default=0)
    p.add_argument('--strict', action='store_true',
                   help='exit 1 if adoption would loosen either gate')
    a = p.parse_args()
    raise SystemExit(run(days_back=a.days_back, strict=a.strict))
