"""Is every NFL line we published today a number books actually post?

The slate-commitment guard in generate_sweat_card makes the FIRST published
line canonical for every later surface. That is right when the first publish
is real and wrong when it is not: 'IND +7.5' was published at 07:57 today and
has ZERO captures TODAY, so the guard propagated a number that is not
gettable this morning onto the card.

CORRECTION, same morning: the first cut of this said +7.5 "appears in zero
captures" full stop. False, and from truncated output -- a desc-ordered
1000-row page of a 9,772-row history. +7.5 was posted 1,437 times, it was
the number for much of the week. It is STALE, not fabricated. The test that
actually matters is whether the line is posted TODAY (+7.5: 0 today, +10:
227 today), so that is what this checks. feedback_never_measure_from_truncated_output.

A receipt that is BOTH priceless and at an unposted line cannot be settled
honestly -- receipt_invariants already refuses to grade a priceless row, and a
line nobody posted has no counterparty. Those are the only rows this voids.
Everything else is reported and left alone.

NOTE line_history keys NFL as '20261101_AWAY_HOME' while the receipts and the
context use a 32-hex id, so the join is by (date, away, home) -- see R137.

  python _audit_nfl_published_lines_1101.py           # audit only
  python _audit_nfl_published_lines_1101.py --apply   # void the unsettleable
"""
from __future__ import annotations
import argparse, collections, json, os, sys
from pathlib import Path
import requests
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
for l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in l and not l.startswith('#'):
        k, v = l.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
HW = dict(H, **{'Content-Type': 'application/json',
                'Prefer': 'return=representation'})
D = '2026-11-01'
VOIDS = ('VOID', 'VOIDED', 'NO_ACTION')

def g(p, **kw):
    r = requests.get(SB + '/rest/v1/' + p, headers=H, params=kw, timeout=180)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (p, r.status_code, r.text[:200])); return []
    return r.json()
def page(t, p):
    out, off = [], 0
    while off < 40000:
        b = g(t, **dict(p, limit=1000, offset=off))
        if not b: break
        out += b; off += 1000
    return out
def fl(v):
    try: return float(v)
    except (TypeError, ValueError): return None

ap = argparse.ArgumentParser(); ap.add_argument('--apply', action='store_true')
A = ap.parse_args()

ctx = g('nfl_game_context', select='game_id,home_team,away_team', game_date='eq.' + D, limit=50)
KEY = {x['game_id']: '%s_%s_%s' % (D.replace('-', ''), x['away_team'], x['home_team'])
       for x in ctx}
NM = {x['game_id']: '%s @ %s' % (x['away_team'], x['home_team']) for x in ctx}

posted = {}
for gid, k in KEY.items():
    # captured_at >= today: a line real last week but gone this morning is
    # not a line we can publish. Paged on a STABLE key (id.asc) -- the first
    # cut ordered by captured_at.desc and silently truncated.
    rows = page('line_history', {'select': 'side,line,market', 'sport': 'eq.NFL',
                                 'game_id': 'eq.' + k, 'market': 'eq.spread',
                                 'captured_at': 'gte.' + D, 'order': 'id.asc'})
    c = collections.Counter()
    for r in rows:
        ln = fl(r.get('line'))
        if ln is not None:
            c[(str(r.get('side')).lower(), ln)] += 1
    posted[gid] = c

rc = g('public_receipts', select='id,surface,market,pick_label,pick_line,pick_odds,'
       'pick_side,result,game_id,published_at', sport='eq.NFL', game_date='eq.' + D,
       limit=400)
live = [x for x in rc if str(x.get('result') or '').upper() not in VOIDS
        and str(x.get('market')) in ('rl', 'spread')]
print('=== %d live NFL spread/rl receipts on %s ===' % (len(live), D))
print('  %-11s %-16s %-14s %-7s %-7s %-22s %s'
      % ('surface', 'game', 'label', 'line', 'odds', 'captures TODAY', 'verdict'))
kill = []
for x in sorted(live, key=lambda z: str(z.get('pick_label'))):
    gid = x.get('game_id'); ln = fl(x.get('pick_line')); od = x.get('pick_odds')
    c = posted.get(gid) or collections.Counter()
    side = str(x.get('pick_side') or '').lower()
    n_home = c.get(('home', ln), 0); n_away = c.get(('away', ln), 0)
    n = (n_home if side == 'home' else n_away if side == 'away'
         else max(n_home, n_away))
    priceless = od is None
    unposted = (n == 0)
    verdict = ('UNSETTLEABLE (not posted today + priceless)'
               if (unposted and priceless)
               else 'not posted today' if unposted
               else 'no price' if priceless else 'ok')
    print('  %-11s %-16s %-14s %-7s %-7s %-22s %s'
          % (str(x.get('surface'))[:11], NM.get(gid, '?')[:16],
             str(x.get('pick_label'))[:14], ln, od, '%d captures' % n, verdict))
    if unposted and priceless:
        kill.append(x)
print('\n  UNSETTLEABLE: %d' % len(kill))
if not kill: sys.exit(0)
if not A.apply:
    print('  dry run -- rerun with --apply'); sys.exit(0)
ok = 0
for x in kill:
    aud = {'void': {'at': D, 'by': '_audit_nfl_published_lines_1101',
                    'reason': ('receipt carries no price AND its line has 0 '
                               'line_history captures TODAY, so it cannot be '
                               'bet this morning and receipt_invariants '
                               'refuses to grade a priceless row. The line '
                               'was real earlier in the week -- stale, not '
                               'fabricated. Voided so the slate commitment '
                               'stops pinning the card to a number that is '
                               'no longer gettable.')}}
    r = requests.patch(SB + '/rest/v1/public_receipts', headers=HW, timeout=60,
                       params={'id': 'eq.%s' % x['id']},
                       json={'result': 'VOID', 'audit': aud})
    b = g('public_receipts', select='id,result', id='eq.%s' % x['id'])
    got = (b or [{}])[0].get('result')
    if r.status_code in (200, 204) and str(got).upper() in VOIDS:
        ok += 1; print('  voided %s %r -> %s' % (x['id'], x.get('pick_label'), got))
    else:
        print('  x void %s HTTP %s readback result=%r' % (x['id'], r.status_code, got))
print('\n  voided %d/%d (verified by readback)' % (ok, len(kill)))
