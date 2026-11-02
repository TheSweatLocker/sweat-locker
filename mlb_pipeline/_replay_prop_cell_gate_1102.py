"""Replay the prop_jerry cell gate against rows it would really have seen.

== 2026-11-02 ================================================================
The gate added to generate_prop_jerry_synthesis.upsert_read demotes a non-MLB
read to PASS unless its (prop_type, tier) cell has a measured record. This
replays `publishable()` -- the SAME function the gate calls -- against every
published prop_jerry read joined to its source prop row, and grades the two
populations against what actually happened.

Replaying the real condition against real rows is the step that was missing
when 17 oddscrowd fade signals sat dead for weeks: a column-presence check
passed them all while `isinstance(x, dict)` returned False forever
(feedback_swallowed_nameerror_is_a_false_condition).

  python _replay_prop_cell_gate_1102.py
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}

sys.path.insert(0, str(Path(__file__).parent))
from prop_cell_allowlist import publishable  # noqa: E402

PROP_TBL = {'NFL': 'nfl_pipeline_props', 'NBA': 'nba_pipeline_props',
            'NHL': 'nhl_pipeline_props'}


def page(table, params):
    out, off = [], 0
    while off < 200000:
        r = requests.get(SB + '/rest/v1/' + table, headers=H, timeout=180,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print('  ! %s HTTP %s %s' % (table, r.status_code, r.text[:200]))
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000
    return out


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarise(rows, odds_key='_odds'):
    w = l = 0
    u = 0.0
    for r in rows:
        o = fl(r.get(odds_key))
        res = str(r.get('result') or '').upper()
        if o is None or res not in ('WIN', 'LOSS'):
            continue
        if res == 'WIN':
            u += (o / 100.0) if o > 0 else (100.0 / abs(o))
            w += 1
        else:
            u -= 1
            l += 1
    n = w + l
    return {'n': n, 'w': w, 'l': l, 'hit': (100.0 * w / n if n else 0.0),
            'u': u, 'roi': (100.0 * u / n if n else 0.0)}


def show(label, s):
    print('  %-36s n=%-5d %-11s %6.1f%%  %+8.2fu  ROI %+6.1f%%'
          % (label, s['n'], '%d-%d' % (s['w'], s['l']), s['hit'], s['u'],
             s['roi']))


def main() -> int:
    for sport, tbl in PROP_TBL.items():
        props = page(tbl, {'select': 'game_date,player_name,prop_type,'
                                     'direction,prop_line,tier,result,'
                                     'book_over_odds,book_under_odds',
                           'game_date': 'gte.2026-08-01'})
        by_key = {}
        for p in props:
            by_key[(str(p.get('game_date')), str(p.get('player_name')),
                    str(p.get('prop_type')), str(p.get('direction')),
                    fl(p.get('prop_line')))] = p
        reads = page('prop_jerry_reads',
                     {'select': 'sport,game_date,player_name,prop_type,'
                                'direction,prop_line,call_verdict',
                      'sport': f'eq.{sport}', 'game_date': 'gte.2026-08-01'})
        print('\n' + '=' * 78)
        print('%s — %d reads, %d source props' % (sport, len(reads), len(props)))
        print('=' * 78)
        if not reads:
            print('  no reads on this surface')
            continue

        published, kept, demoted, unjoined = [], [], [], 0
        reasons = collections.Counter()
        for rd in reads:
            v = str(rd.get('call_verdict') or '').upper()
            if not v or v == 'PASS':
                continue                       # already not a play
            key = (str(rd.get('game_date')), str(rd.get('player_name')),
                   str(rd.get('prop_type')), str(rd.get('direction')),
                   fl(rd.get('prop_line')))
            p = by_key.get(key)
            if p is None:
                unjoined += 1
                continue
            # the side we PUBLISH, and its price -- a FADE takes the other one
            d = str(rd.get('direction') or '').lower()
            side = d
            if v == 'FADE' and d in ('over', 'under'):
                side = 'under' if d == 'over' else 'over'
            row = dict(p)
            row['_odds'] = (p.get('book_over_odds') if side == 'over'
                            else p.get('book_under_odds'))
            # a FADE wins when the prop's own side loses
            res = str(p.get('result') or '').upper()
            if v == 'FADE' and res in ('WIN', 'LOSS'):
                res = 'LOSS' if res == 'WIN' else 'WIN'
            row['result'] = res
            published.append(row)
            ok, why = publishable(sport, p.get('prop_type'),
                                  p.get('direction'), p.get('tier'), v,
                                  p.get('book_over_odds'),
                                  p.get('book_under_odds'))
            (kept if ok else demoted).append(row)
            if not ok:
                reasons[why.split(' is not')[0][:52]] += 1

        print('  published reads joined to a source prop: %d  (unjoined %d)'
              % (len(published), unjoined))
        show('PUBLISHED today (all of them)', summarise(published))
        show('KEPT by the gate', summarise(kept))
        show('DEMOTED to PASS by the gate', summarise(demoted))
        sk, sd = summarise(kept), summarise(demoted)
        if sk['n'] and sd['n']:
            print('  -> %+.1fpp ROI, publishing %d of %d plays (%.0f%%)'
                  % (sk['roi'] - sd['roi'], sk['n'], sk['n'] + sd['n'],
                     100.0 * sk['n'] / (sk['n'] + sd['n'])))
            if sd['roi'] > 0:
                print('  !! the DEMOTED set is POSITIVE — this gate is '
                      'removing a winning population. Do not ship it.')
        if sk['n'] and sk['n'] < 30:
            print('  !! kept population is n=%d, under the house gate of 30'
                  % sk['n'])
        if reasons:
            print('\n  why rows were demoted:')
            for why, c in reasons.most_common(8):
                print('    %5d  %s' % (c, why))
    return 0


if __name__ == '__main__':
    sys.exit(main())
