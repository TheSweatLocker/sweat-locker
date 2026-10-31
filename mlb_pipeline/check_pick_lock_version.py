"""Which version of enforce_pick_lock is actually LIVE?

== 2026-10-31 . A MIGRATION IN THE REPO THAT IS NOT IN THE DATABASE =======
Today's two stale NCAAF lines could not be corrected: the PATCH returned
HTTP 200 with the full row echoed back, and the readback still showed the
old number. The trigger had silently reverted `primary_play`.

That should NOT happen. `20261007a_pick_lock_freezes_side_not_number.sql`
exists precisely for it, after Andy asked on 10-07: "I need the line
movement to assessed and not stale lines post as picks thats what i need
just fix the systems." Under that version our change is explicitly allowed
-- same side, same market, same team at the head of the label, kickoff not
yet passed -- and the function's own comment says so: "Same bet, new number.
Let it through."

HOW WE KNOW THE OLD ONE IS LIVE, from the code and not from a guess. In
20261007a, EVERY insert into `pick_lock_drift` supplies a non-null `reason`
(the started branch writes 'game already started - price is frozen', the
refusal branch writes a formatted side/type/team diff). Today's refusals are
logged with `reason = NULL`. A function that always writes a reason cannot
produce a null one, so the deployed body predates it.

The `reason` COLUMN does exist -- selecting it returns 200, not 400 -- so
the ALTER TABLE half of that migration landed and the CREATE OR REPLACE
FUNCTION half did not. The likeliest cause is the file's trailing
`NOTIFY pgrst, 'reload schema'`, which makes Andy's SQL client report
`42P01 relation "a" does not exist` whether the migration failed OR fully
succeeded (feedback_notify_pgrst_breaks_andys_sql_client). A partial apply
read as a total failure is exactly what that note warns about -- and it is
why the lesson is to QUERY FOR THE ARTEFACT rather than believe the error.

This script is that query, so the next apply is confirmed by evidence:
  1. does `pick_lock_drift.reason` exist at all?
  2. has ANY refusal ever been logged with a non-null reason?
  3. how many refusals are number-only changes -- the ones 20261007a would
     allow and the deployed version is eating?

It writes nothing.

USAGE
  python check_pick_lock_version.py
"""
from __future__ import annotations

import collections
import os
import re
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def team_head(lbl: str) -> str:
    """Everything up to the LAST space -- the same rule the trigger uses."""
    return re.sub(r'\s+\S+$', '', str(lbl or '')).strip()


def page(params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/pick_lock_drift', headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            return None, r
        d = r.json()
        out += d
        if len(d) < 1000:
            return out, r
        off += 1000


def main() -> int:
    # 1. does the column exist? A 400 names a column as readily as a table
    #    (feedback_select_star_before_concluding_absent).
    probe = requests.get(f'{SB}/rest/v1/pick_lock_drift', headers=H,
                         timeout=60, params={'select': 'reason', 'limit': 1})
    has_col = probe.status_code == 200
    print('=== enforce_pick_lock: which version is live? ===')
    print(f'  pick_lock_drift.reason column exists: '
          f'{"YES" if has_col else "NO"}'
          f'{"" if has_col else "  (HTTP %s %s)" % (probe.status_code, probe.text[:80])}')

    rows, resp = page({'select': 'sport,game_id,kept_label,wanted_label,'
                                 'reason,attempted_at,kept,wanted',
                       'order': 'attempted_at.desc'})
    if rows is None:
        print(f'  ! pick_lock_drift HTTP {resp.status_code} '
              f'{resp.text[:140]} -- CANNOT VERIFY')
        return 2
    print(f'  refusals logged: {len(rows)}')
    with_reason = [x for x in rows if x.get('reason')]
    print(f'  with a non-null reason: {len(with_reason)}')

    # == JUDGE ON THE NEWEST REFUSAL, NOT ON "EVER" ==================
    # My first version asked whether ANY refusal had ever carried a reason,
    # said yes, and concluded 20261007a was live. It is not. Reasons appear
    # ONLY between 2026-10-07T22:52 and 2026-10-08T19:03 -- applied on the
    # 7th, live about 21 hours, function body reverted on the 8th. Every
    # refusal since carries a null reason. "Has it ever worked" and "is it
    # working now" are different questions; only the second one matters.
    if with_reason:
        first = min(str(x['attempted_at']) for x in with_reason)[:16]
        last = max(str(x['attempted_at']) for x in with_reason)[:16]
        print(f'  reasons span {first} -> {last}')
    # == A SUCCESSFUL NUMBER-ONLY WRITE IS THE POSITIVE PROOF ==========
    # Judging on the newest REFUSAL alone has a hole: once the fix is live,
    # number-only writes stop being refused, so the newest refusal is
    # forever the last one from the broken era and the verdict stays
    # "reverted" no matter what. No refusal is evidence of SUCCESS.
    #
    # So look for the behaviour only 20261007a permits: a line refresh that
    # LANDED on a locked, not-yet-started row. refresh_unposted_pick_lines
    # stamps `_line_refreshed` on every one, so those stamps are the
    # artefact. Checked AFTER the newest refusal, the two together say which
    # body is live.
    newest = rows[0] if rows else None
    newest_refusal = str(newest.get('attempted_at') or '') if newest else ''
    # The key alone is not enough: 13 of 15 rows carrying `_line_refreshed`
    # have it present but EMPTY (was/now/by all null), written by something
    # else in a different shape. Counting those put the figure at 15 when
    # only 2 were real. So require the stamp this tool writes AND a non-null
    # new value -- a marker that proves nothing is worse than no marker.
    landed = []
    for t in ('ncaaf_game_context', 'nfl_game_context'):
        g = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=90,
                         params={'select': 'game_id,primary_play',
                                 'primary_play->_line_refreshed': 'not.is.null',
                                 'limit': 500})
        if g.status_code != 200:
            continue
        for x in g.json():
            pp = x.get('primary_play')
            if isinstance(pp, str):
                try:
                    pp = __import__('json').loads(pp)
                except Exception:
                    continue
            ref = (pp or {}).get('_line_refreshed') or {}
            if (isinstance(ref, dict)
                    and ref.get('now') is not None
                    and ref.get('by') == 'refresh_unposted_pick_lines'):
                landed.append((t, x))
    print(f'  line refreshes that LANDED on a locked row: {len(landed)}')
    live_new = bool(landed) or bool(newest and newest.get('reason'))
    verdict = ('20261007a IS LIVE' if live_new else
               'AN OLDER VERSION IS LIVE' +
               (' -- it was live once and has been REVERTED' if with_reason
                else ' -- 20261007a has never run'))
    if landed:
        print(f'  -> a number-only write succeeded on a locked row, which the '
              f'older body forbids')
    elif newest_refusal:
        print(f'  -> no landed refresh found; judging on the newest refusal '
              f'at {newest_refusal[:16]}')
    print(f'\n  VERDICT: {verdict}')
    if newest:
        print(f'  newest refusal {str(newest.get("attempted_at"))[:16]} '
              f'reason={newest.get("reason")!r}')
    if live_new and with_reason and not (newest and newest.get('reason')):
        print('  (the newest REFUSAL predates the fix, which is expected: '
              'once the number is allowed\n   through it is no longer '
              'refused, so no new refusal gets logged.)')
    if with_reason and not live_new:
        print('  The reasoned window proves it applied once, so something '
              'replaced the\n  function body afterwards. The likeliest cause '
              'is a migration replay whose\n  history did not contain '
              '20261007a: it stops at 20260929d and restores the\n  old '
              'body. RE-APPLY THROUGH THE RECORDED MIGRATION PATH, not as a '
              'loose\n  paste, or the next push reverts it again.')

    # 3. how much is the deployed version eating?
    # A number-only change needs the SAME side and SAME market as well as the
    # same team. Comparing the label head alone counted 'PIT -4.5' -> 'PIT ML'
    # (a market change) and 'SF -2.5' -> 'SF +5.5' (a side flip) as
    # number-only, which inflated this badly.
    numonly = []
    for x in rows:
        k, w = str(x.get('kept_label') or ''), str(x.get('wanted_label') or '')
        if not (k and w) or k == w:
            continue
        if not (team_head(k) and team_head(k) == team_head(w)):
            continue
        kept, want = x.get('kept') or {}, x.get('wanted') or {}
        if not isinstance(kept, dict) or not isinstance(want, dict):
            continue
        if (kept.get('side') == want.get('side')
                and kept.get('type') == want.get('type')):
            numonly.append(x)
    print(f'\n  of {len(rows)} refusals, {len(numonly)} are NUMBER-ONLY '
          f'(same team at the head of the label)')
    print('  -> every one of those is a line refresh the deployed trigger '
          'refused and 20261007a would allow')
    by = collections.Counter(str(x.get('sport')) for x in numonly)
    print(f'  by sport: {dict(by)}')
    for x in numonly[:10]:
        print(f'    {str(x.get("attempted_at"))[:16]} {str(x.get("sport")):6} '
              f'{x.get("kept_label")!r} -> {x.get("wanted_label")!r}')

    print('\n  TO FIX: apply '
          'supabase/migrations/20261007a_pick_lock_freezes_side_not_number.sql'
          '\n  Then re-run THIS script: a non-null reason on the next refusal '
          'is the\n  confirmation. Do NOT trust the SQL client’s own '
          'message -- the file ends\n  in NOTIFY pgrst, which reports 42P01 '
          'whether it failed or succeeded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
