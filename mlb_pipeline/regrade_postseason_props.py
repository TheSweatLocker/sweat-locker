#!/usr/bin/env python3
"""Re-grade props the MLB gameLog call could not see, because it asked for
regular season only.

WHY (2026-10-07)
----------------
Andy: "Yastrzemski under hit not graded, it hit. POTD yesterday Yamamoto
graded as lost when he went 21 [outs]."

Both true, one cause. `grade_prop_jerry_reads._fetch_stat_for_date` requested

    stats=gameLog&group=pitching&season=2026&sportId=1

with no `gameType`, and the MLB API defaults that to REGULAR SEASON. Verified
on Yamamoto (pid 808967) for 2026-10-06:

    no gameType  -> 28 splits, 2026-10-06 ABSENT
    gameType=R   -> 28 splits, 2026-10-06 ABSENT
    gameType=P   ->  1 split,  2026-10-06 PRESENT, IP 7.0 (= 21 outs)
    gameType=R,P -> 29 splits, 2026-10-06 PRESENT

So for the whole postseason — which is the only baseball being played in
October — the stat fallback returned None. Rows then landed as UNGRADEABLE
(Yastrzemski, who actually went 0-for, a WIN on Under 0.5) or took a wrong
result from elsewhere (Yamamoto, graded Loss against a 17.5-out line he
cleared with 21).

The source call is fixed. The rows already stamped are not: both graders
select `result IS NULL`, so nothing revisits a row once it carries any value.
This re-grades them from the now-visible postseason log.

ONLY CHANGES WHAT IT CAN PROVE. Each row is re-graded from the MLB API and
written only when the API gives a definite answer that DIFFERS from what is
stored. A row the API still cannot see is left exactly as it is and counted
as unresolved — never cleared, never guessed.

    python regrade_postseason_props.py                 # report
    python regrade_postseason_props.py --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: MLB postseason 2026 began 2026-09-30. Nothing before it is affected,
#: because the regular-season log the old call used was correct for it.
POSTSEASON_FROM = '2026-09-30'


def page(table, params):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                         timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code}: {r.text[:200]}')
            return out
        chunk = r.json()
        if not isinstance(chunk, list):
            return out
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--since', default=POSTSEASON_FROM)
    args = ap.parse_args()
    print(f'=== regrade_postseason_props · since {args.since} · '
          f'{"APPLY" if args.apply else "DRY"} ===\n')

    import grade_prop_jerry_reads as G

    rows = page('prop_jerry_reads',
                {'select': 'id,game_date,player_name,prop_type,direction,'
                           'prop_line,call_verdict,result,resolved_at',
                 'game_date': f'gte.{args.since}',
                 'result': 'not.is.null'})
    print(f'graded prop_jerry_reads since {args.since}: {len(rows)}')

    # ══ A PASS IS NOT A PICK, AND A VOID IS A DECISION ══
    # First pass of this script would have "corrected" 94 rows from
    # NO_ACTION to Win/Loss. Every one of them is verdict=PASS — Jerry
    # declined the prop, and NO_ACTION is the right record of that (249 such
    # rows since 09-30, all consistent). Converting them would have invented
    # graded picks that were never made and inflated the denominator with
    # takes nobody published.
    #
    # Void is likewise a deliberate state (16 rows), not a missing grade.
    #
    # So the scope is only rows that ARE a take and ARE already graded
    # win/loss or stranded UNGRADEABLE — exactly the population the
    # postseason blindness could have got wrong.
    GRADEABLE_VERDICTS = {'BACK', 'FADE', 'PRIME', 'STRONG', 'LEAN'}
    SKIP_RESULTS = {'NO_ACTION', 'VOID', 'PUSH'}
    before = len(rows)
    rows = [r for r in rows
            if str(r.get('call_verdict') or '').upper() in GRADEABLE_VERDICTS
            and str(r.get('result') or '').upper() not in SKIP_RESULTS]
    print(f'  of those, real takes with a win/loss-shaped result: {len(rows)}'
          f'   (skipped {before - len(rows)} passes / voids / pushes)')

    changes, unresolved, agreed = [], 0, 0
    for r in rows:
        api = G._grade_from_mlb_api(r, str(r['game_date'])[:10])
        if api is None:
            unresolved += 1
            continue
        base, actual = api
        want = G.flip_for_fade(base, r.get('call_verdict'))
        have = str(r.get('result') or '')
        if have.upper() == str(want).upper():
            agreed += 1
            continue
        changes.append((r, want, actual, have))

    print(f'  agrees with the API            : {agreed}')
    print(f'  API still cannot see the game  : {unresolved}')
    print(f'  WRONG, correctable             : {len(changes)}')
    if changes:
        bk = collections.Counter(f'{c[3]} -> {c[1]}' for c in changes)
        print()
        for k, v in bk.most_common():
            print(f'    {k:<28} {v}')
        print()
        for r, want, actual, have in changes[:25]:
            print(f'    {r["game_date"]} {str(r["player_name"])[:20]:20s} '
                  f'{r["prop_type"]:<12} {r["direction"]:<5} '
                  f'{r["prop_line"]!s:>5}  actual={actual!s:>5}  '
                  f'{have} -> {want}')

    if not args.apply:
        print(f'\n{len(changes)} row(s) would change. Re-run with --apply.')
        return 0

    ok = bad = 0
    for r, want, actual, _have in changes:
        body = {'result': want,
                'resolved_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        resp = requests.patch(f'{SB}/rest/v1/prop_jerry_reads',
                              headers=H_W, timeout=60,
                              params={'id': f'eq.{r["id"]}'},
                              data=json.dumps(body))
        back = resp.json() if resp.content else []
        if (resp.status_code in (200, 204) and back
                and str(back[0].get('result')).upper() == str(want).upper()):
            ok += 1
        else:
            bad += 1
            print(f'    ! id={r["id"]} not confirmed ({resp.status_code}): '
                  f'{resp.text[:120]}')
    print(f'\nprop_jerry_reads corrected {ok}/{len(changes)} '
          f'(verified by read-back), {bad} failed')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
