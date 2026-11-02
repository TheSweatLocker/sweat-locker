"""Which (prop_type, tier) cells actually earn a card slot, per sport?

ANDY 2026-11-01, after the day audit: "If we cant surface nfl proops 8 weeks
in we are failing majorly ... WHY CANT WE SURFACE PROPS WHAT DO YOU MEAN BY
THAT."

The answer was three hardcoded strings -- fetch_top_props reads
"mlb_pipeline_props", the pick construction stamps that same literal as
source_table, and generate_sharp_card does the same. So NFL props have never
been eligible for either card.

The reason NOT to just swap the table name is measured: that fetch orders by
`conviction.desc`, and NFL prop conviction is INVERTED (PRIME 49.1% n=226 vs
LEAN 53.5% n=1122 lifetime; today PRIME 38.5% n=13 vs LIGHT 52.9% n=87). So
the naive wiring would select the worst rows on the board.

This measures every (prop_type, tier) cell per sport and reports only the
ones fit to hardcode as an allowlist:

  * n >= 30                      -- the house n gate
  * ROI > 0 at the row's OWN price (never flat -110)
  * holds in BOTH date halves    -- the split-half test that killed the
                                    B2B x top-offence cell and the
                                    "sharp shape"

Everything that fails is printed too, so the rejects are on the record.

  python _measure_prop_cells_1102.py
"""
from __future__ import annotations
import collections, os, sys
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
MIN_N = 30

def page(t, p):
    out, off = [], 0
    while off < 120000:
        r = requests.get(SB + '/rest/v1/' + t, headers=H, timeout=240,
                         params=dict(p, limit=1000, offset=off, order='id.asc'))
        if r.status_code != 200:
            print('  ! %s HTTP %s %s' % (t, r.status_code, r.text[:160])); return out
        b = r.json(); out += b
        if len(b) < 1000: return out
        off += 1000
    return out

def fl(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def unit(res, odds):
    o = fl(odds)
    if o is None: return None          # priceless -> excluded, never assumed
    if res == 'WIN': return (o / 100.0) if o > 0 else (100.0 / abs(o))
    if res == 'LOSS': return -1.0
    return 0.0

def summarise(rows):
    w = sum(1 for r in rows if r['_res'] == 'WIN')
    L = sum(1 for r in rows if r['_res'] == 'LOSS')
    u = sum(r['_u'] for r in rows)
    n = w + L
    return n, w, L, (100.0 * w / n if n else 0.0), u, (100.0 * u / n if n else 0.0)

for sport, tbl in (('NFL', 'nfl_pipeline_props'), ('NBA', 'nba_pipeline_props'),
                   ('NHL', 'nhl_pipeline_props'), ('MLB', 'mlb_pipeline_props')):
    rows = page(tbl, {'select': 'id,game_date,prop_type,direction,tier,result,'
                                'book_over_odds,book_under_odds,conviction'})
    good = []
    for r in rows:
        res = str(r.get('result') or '').upper()
        if res not in ('WIN', 'LOSS'):
            continue
        odds = (r.get('book_under_odds') if str(r.get('direction')) == 'under'
                else r.get('book_over_odds'))
        u = unit(res, odds)
        if u is None:
            continue
        r['_res'] = res; r['_u'] = u
        good.append(r)
    print('\n' + '=' * 78)
    print('%s  %d rows, %d graded WITH a price' % (sport, len(rows), len(good)))
    print('=' * 78)
    if not good:
        print('  nothing graded with a price -- no cells measurable')
        continue
    dates = sorted({str(r.get('game_date')) for r in good})
    mid = dates[len(dates) // 2]
    print('  date range %s .. %s   split at %s' % (dates[0], dates[-1], mid))
    n, w, L, hit, u, roi = summarise(good)
    print('  BASELINE all graded: %d-%d  %.1f%%  %+.2fu  ROI %+.1f%%  n=%d'
          % (w, L, hit, u, roi, n))
    cells = collections.defaultdict(list)
    for r in good:
        cells[(str(r.get('prop_type')), str(r.get('tier')))].append(r)
    passed, failed = [], []
    for key, rs in cells.items():
        n, w, L, hit, u, roi = summarise(rs)
        if n < MIN_N:
            continue
        e = [r for r in rs if str(r.get('game_date')) < mid]
        la = [r for r in rs if str(r.get('game_date')) >= mid]
        ne, we, Le, he, ue, roie = summarise(e)
        nl, wl, Ll, hl, ul, roil = summarise(la)
        both = (roie > 0 and roil > 0 and ne >= 8 and nl >= 8)
        rowinfo = (key, n, w, L, hit, roi, ne, roie, nl, roil, both)
        (passed if (roi > 0 and both) else failed).append(rowinfo)
    passed.sort(key=lambda z: -z[5])
    failed.sort(key=lambda z: -z[5])
    print('\n  >>> CELLS FIT TO HARDCODE (n>=%d, ROI>0, positive in BOTH halves)'
          % MIN_N)
    print('  %-26s %-9s %-7s %-9s %-16s %s'
          % ('prop_type', 'tier', 'hit', 'ROI', 'early half', 'late half'))
    if not passed:
        print('    NONE')
    for key, n, w, L, hit, roi, ne, roie, nl, roil, both in passed:
        print('  %-26s %-9s %-7s %-9s %-16s %s'
              % (key[0][:26], key[1], '%.1f%%' % hit, '%+.1f%%' % roi,
                 '%+.1f%% n=%d' % (roie, ne), '%+.1f%% n=%d' % (roil, nl)))
        print('       %d-%d  n=%d' % (w, L, n))
    print('\n  --- rejected (n>=%d but ROI<=0 or fails split-half) ---' % MIN_N)
    for key, n, w, L, hit, roi, ne, roie, nl, roil, both in failed[:14]:
        why = 'ROI<=0' if roi <= 0 else 'fails split-half'
        print('  %-26s %-9s %-7s %-9s n=%-5d %-18s early %+.1f%% late %+.1f%%'
              % (key[0][:26], key[1], '%.1f%%' % hit, '%+.1f%%' % roi, n, why,
                 roie, roil))
    print('\n  TIER ROLL-UP (why conviction order must not be used):')
    for t in ('PRIME', 'STRONG', 'LEAN', 'LIGHT', 'COVERAGE', 'SKIP'):
        rs = [r for r in good if str(r.get('tier')) == t]
        if not rs: continue
        n, w, L, hit, u, roi = summarise(rs)
        print('    %-9s %d-%d  %.1f%%  ROI %+.1f%%  n=%d' % (t, w, L, hit, roi, n))
