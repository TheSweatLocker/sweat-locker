"""Do the 11-01 prop cells hold on an INDEPENDENT surface, or are they overfit?

== 2026-11-02 - THE QUESTION ==============================================
prop_cell_allowlist.py was measured on the SOURCE tables (nfl_pipeline_props
etc). This re-tests those same cells on `public_receipts` where
surface='prop_jerry' - rows that were actually PUBLISHED and graded through
a different path. If the cells only look good in the table they were fitted
on, they are overfit and the allowlist is worth nothing.

The surface this gates matters more than the card did. Measured since 09-01,
graded rows carrying a real price:

    NFL prop_jerry  2812  50.7%  ROI -4.2%
        PRIME         97  52.6%      -2.7%
        STRONG      1111  50.6%      -4.1%
        LEAN        1201  51.8%      -2.4%
        LIGHT        104  46.2%     -13.0%

NOT ONE TIER IS POSITIVE, and 739 of these were published on 11-01 alone.
That is the single largest measured drain in football. MLB on the same
surface is the opposite: PRIME +29.1% on n=1114.

So the test is: on NFL prop_jerry rows only, what does the allowlist keep,
what does it drop, and what would the surface have returned if it had
published only the kept rows? Printed with the dropped remainder alongside,
because a gate that improves the kept set while the dropped set is also
positive has not found an edge, only a smaller sample.

  python _validate_allowlist_on_prop_jerry_1102.py
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
from prop_cell_allowlist import ALLOWLIST, ODDS_MIN, ODDS_MAX  # noqa: E402


def page(table, params):
    out, off = [], 0
    while off < 200000:
        r = requests.get(SB + '/rest/v1/' + table, headers=H, timeout=180,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print('  ! HTTP %s %s' % (r.status_code, r.text[:200]))
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


def summarise(rows):
    w = l = 0
    u = 0.0
    for r in rows:
        o = fl(r.get('pick_odds'))
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
    print('  %-34s n=%-5d %-11s %6.1f%%  %+7.2fu  ROI %+6.1f%%'
          % (label, s['n'], '%d-%d' % (s['w'], s['l']), s['hit'], s['u'],
             s['roi']))


def main() -> int:
    rows = page('public_receipts',
                {'select': 'sport,tier,prop_type,result,pick_odds,game_date,'
                           'player_name,pick_label',
                 'surface': 'eq.prop_jerry', 'game_date': 'gte.2026-08-01'})
    print('prop_jerry receipts fetched: %d' % len(rows))
    for sport in ('NFL', 'MLB'):
        rs = [r for r in rows if r['sport'] == sport]
        cells = ALLOWLIST.get(sport) or {}
        if not rs:
            continue
        print('\n' + '=' * 74)
        print('%s  — %d receipts, allowlist holds %d cell(s)'
              % (sport, len(rs), len(cells)))
        print('=' * 74)
        show('ALL rows (what we publish today)', summarise(rs))

        def is_kept(r):
            key = (str(r.get('prop_type') or ''),
                   str(r.get('tier') or '').upper())
            if key not in cells:
                return False
            o = fl(r.get('pick_odds'))
            return o is not None and ODDS_MIN <= o <= ODDS_MAX

        kept = [r for r in rs if is_kept(r)]
        dropped = [r for r in rs if not is_kept(r)]
        sk, sd = summarise(kept), summarise(dropped)
        show('KEPT by the allowlist', sk)
        show('DROPPED by the allowlist', sd)
        if sk['n'] and sd['n']:
            print('  -> gate moves the published surface %+.1fpp ROI '
                  '(%+.1f%% kept vs %+.1f%% dropped), keeping %d of %d rows '
                  '(%.0f%%)'
                  % (sk['roi'] - sd['roi'], sk['roi'], sd['roi'],
                     sk['n'], sk['n'] + sd['n'],
                     100.0 * sk['n'] / (sk['n'] + sd['n'])))
            if sd['roi'] > 0:
                print('  !! the DROPPED set is also positive — the gate is '
                      'selecting a smaller sample, not an edge')

        print('\n  per allowlisted cell, on THIS surface:')
        print('  %-30s %-9s %6s %9s %8s %9s'
              % ('prop_type', 'tier', 'n', 'W-L', 'hit', 'ROI'))
        for (pt, tr), meta in sorted(cells.items(),
                                     key=lambda z: -z[1][1]):
            cr = [r for r in rs
                  if str(r.get('prop_type') or '') == pt
                  and str(r.get('tier') or '').upper() == tr
                  and fl(r.get('pick_odds')) is not None
                  and ODDS_MIN <= fl(r.get('pick_odds')) <= ODDS_MAX]
            s = summarise(cr)
            flag = ''
            if s['n'] == 0:
                flag = '   <- NOT PRESENT on this surface'
            elif s['n'] < 30:
                flag = '   <- n<30 here, not independently confirmed'
            elif s['roi'] <= 0:
                flag = '   <- NEGATIVE here, contradicts the source table'
            else:
                flag = '   <- holds'
            print('  %-30s %-9s %6d %9s %7.1f%% %+8.1f%% (source said %+.1f%% n=%d)%s'
                  % (pt[:30], tr, s['n'], '%d-%d' % (s['w'], s['l']),
                     s['hit'], s['roi'], meta[1], meta[2], flag))

        # what the surface actually publishes most of, so the drop is visible
        print('\n  biggest prop_types on this surface (what gets dropped):')
        cnt = collections.Counter(
            str(r.get('prop_type') or '?') for r in rs)
        for pt, c in cnt.most_common(8):
            pr = [r for r in rs if str(r.get('prop_type') or '?') == pt]
            s = summarise(pr)
            inlist = any(k[0] == pt for k in cells)
            print('    %-28s %5d rows  n=%-5d ROI %+6.1f%%  %s'
                  % (pt[:28], c, s['n'], s['roi'],
                     'allowlisted' if inlist else 'dropped'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
