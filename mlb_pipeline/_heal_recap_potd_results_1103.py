"""Mirror the decided POTD result onto the recap snapshots that never got it.

== WHY ====================================================================
`resolve_game_results._refresh_next_day_yesterday_recap` mirrored the POTD
result by looking for `type == 'POTD'` among the recap's top_8 entries. The
LIVE top_8 does carry that tag (114 of 185 cards); the RECAP COPY does not
-- measured across every sweat_card row, `yesterday_recap.top_8[].type` is
None on **946 of 1,033** entries and 'POTD' on **12**. So the mirror almost
never found the pick and `yesterday_recap.potd.result` stayed Pending on the
home screen while the real result sat in daily_best_bet_history.

That lookup is fixed to read daily_best_bet_history (keyed on bet_date, and
cross-sport, which POTD now is). This heals the rows already written.

Measured before running: of 164 cards carrying a yesterday_recap.potd,
    120  result already settled
     17  PENDING while daily_best_bet_history holds a DECIDED result  <- here
     27  no decided result in history either -- NOT this bug, left alone

This is not re-grading. daily_best_bet_history is the authoritative POTD row
and the recap block is a display snapshot of it; the app renders the
snapshot, so the snapshot has to agree
(feedback_fix_the_cache_not_the_frozen_receipt). Nothing is invented: a row
whose own result is null or Pending is skipped, and the pick's label, line,
odds and matchup are never touched -- only `result`.

Readback-verified per card.

    python _heal_recap_potd_results_1103.py            # dry run
    python _heal_recap_potd_results_1103.py --apply
"""
import json
import os
import sys
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv
PENDING = ('', 'pending')
#: Only a real OUTCOME may be mirrored onto a display field. 5 of the 17
#: candidate rows carry result='no-pick' -- 4 of them with sport and lean
#: both null, i.e. there was no POTD that day at all. 'no-pick' is a
#: bookkeeping status, not a grade, and the app's recap block renders this
#: value directly (feedback_result_column_is_title_case).
OUTCOMES = ('win', 'loss', 'push', 'void')


def page(table: str, select: str) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=90,
                         params={'select': select, 'limit': 1000,
                                 'offset': off})
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    cards = [r for r in page('jerry_cache', 'cache_key,data')
             if str(r['cache_key']).startswith('sweat_card_')]
    hist = {}
    for h in page('daily_best_bet_history', 'bet_date,sport,game,lean,result'):
        hist.setdefault(str(h['bet_date']), []).append(h)

    plan, no_hist, settled = [], 0, 0
    for r in cards:
        d = r.get('data')
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:                            # noqa: BLE001
                continue
        if not isinstance(d, dict):
            continue
        y = d.get('yesterday_recap') or {}
        sd = y.get('date')
        potd = y.get('potd') or {}
        if not sd or not potd:
            continue
        if str(potd.get('result') or '').strip().lower() not in PENDING:
            settled += 1
            continue
        done = [x for x in hist.get(str(sd), [])
                if str(x.get('result') or '').strip().lower() in OUTCOMES]
        if not done:
            no_hist += 1
            continue
        plan.append((r['cache_key'], d, sd, potd.get('result'), done[0]))

    print(f'sweat_card rows with a yesterday_recap.potd: '
          f'{settled + no_hist + len(plan)}')
    print(f'  already settled                 : {settled}')
    print(f'  pending, no decided outcome row : {no_hist}  (left alone)')
    print(f'  pending, history HAS a result   : {len(plan)}  <- repairing')
    print()
    for key, _d, sd, was, h in sorted(plan):
        print(f"  {key:24} recap-of {sd}  {str(was)!r:9} -> "
              f"{str(h.get('sport')):6} {str(h.get('lean'))[:34]:34} "
              f"{h['result']}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    rc = 0
    print()
    for key, d, sd, _was, h in plan:
        d['yesterday_recap']['potd']['result'] = h['result']
        d['yesterday_recap']['potd']['_result_source'] = {
            'from': 'daily_best_bet_history',
            'bet_date': sd,
            'note': 'recap mirror could not find type==POTD in the snapshot',
        }
        pr = requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W,
                            timeout=30, params={'cache_key': f'eq.{key}'},
                            data=json.dumps({'data': d}))
        if pr.status_code not in (200, 204):
            print(f'  !! {key} PATCH {pr.status_code}: {pr.text[:160]}')
            rc = 1
            continue
        rb = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H, timeout=30,
                          params={'select': 'data', 'cache_key': f'eq.{key}'})
        got = (rb.json() or [{}])[0].get('data') or {}
        if isinstance(got, str):
            got = json.loads(got)
        now = (((got.get('yesterday_recap') or {}).get('potd')) or {}).get('result')
        if now != h['result']:
            print(f'  !! {key} NOT CONFIRMED -- readback result={now!r}')
            rc = 1
        else:
            print(f'  confirmed {key}  potd.result={now}')
    return rc


if __name__ == '__main__':
    sys.exit(main())
