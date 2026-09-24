"""Does the NFL prop projection edge actually predict? Measure it.

CONTEXT
-------
nfl_pipeline_props.projection was NULL on 1,794 of 1,796 rows because the
bridge mapper dropped it (fixed 2026-09-24). Three enabled signal_sources
rows gate on it and were therefore dead:

    nfl_prop_projection_edge_supports  BACK 0.55  (>=15% edge)
    nfl_prop_projection_edge_opposes   FADE 0.60  (>=15% against)
    nfl_prop_projection_strong         BACK 0.75  (>=30% edge)

Before those weights go live, they should be earned. This measures whether
a bigger projection edge actually produces a better hit rate, on graded
history only.

WHAT THIS IS NOT
----------------
This is not an out-of-sample test of the projection itself. The generator
only publishes a prop when its own directional edge clears 6%, so every
row here already passed that filter — there is no zero-edge control group
in the data. What CAN be asked is the monotonicity question: among
published props, does more edge mean more wins? If the curve is flat or
inverted, the three signals are noise and should not be weighted, no
matter how reasonable they look.

The extreme-edge guard in nfl_prop_signal_discipline (edge_pct > 40 caps
at LEAN) already encodes a suspicion that huge edges are data errors
rather than real value. This measurement tests that suspicion directly by
reporting the top bucket separately instead of lumping it in.

Every percentage is printed with its n. Buckets under n=30 are marked as
too small to read, per standing rule.
"""
import os
import sys
import requests
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Case-folded, because nfl_pipeline_props stores 'Win'/'Loss' in title case
# while other surfaces use upper. Listing spellings is how the first run of
# this script silently measured zero rows and reported "nothing to measure".
WIN = {'WIN', 'W', 'HIT'}
LOSS = {'LOSS', 'L', 'MISS'}
MIN_N = 30


def _grade(r) -> str:
    """'' for anything not a settled win/loss — Void and UNGRADEABLE included."""
    v = str(r.get('result') or '').strip().upper()
    if v in WIN:
        return 'W'
    if v in LOSS:
        return 'L'
    return ''


def fetch_graded() -> list:
    out, offset = [], 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/nfl_pipeline_props', headers=H, timeout=180,
            params={'select': 'id,game_date,player_name,prop_type,prop_line,'
                              'direction,projection,tier,conviction,result,'
                              'final_value',
                    'order': 'id.asc', 'limit': 500, 'offset': offset})
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < 500:
            return out
        offset += 500


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(w, n):
    return f'{100.0*w/n:5.1f}%' if n else '    —'


def line(label, w, n):
    flag = '' if n >= MIN_N else '   (n<30 — not readable)'
    return f'  {label:28s} {w:>4d}-{n-w:<4d} {pct(w,n):>7s}  n={n:<5d}{flag}'


def main():
    rows = fetch_graded()
    graded = [r for r in rows
              if _grade(r)
              and _f(r.get('projection')) is not None
              and _f(r.get('prop_line')) is not None]
    print(f'rows: {len(rows)}   graded with a projection: {len(graded)}')
    if not graded:
        print('nothing to measure')
        return
    print(f'dates: {min(r["game_date"] for r in graded)} .. '
          f'{max(r["game_date"] for r in graded)}\n')

    def edge_of(r):
        proj, ln = _f(r['projection']), _f(r['prop_line'])
        if not ln:
            return None
        raw = (proj - ln) / ln
        return raw if r['direction'] == 'over' else -raw

    # Overall baseline
    w = sum(1 for r in graded if _grade(r) == 'W')
    print('BASELINE (every graded NFL prop carrying a projection)')
    print(line('all', w, len(graded)))

    # Monotonicity: does more edge win more?
    BUCKETS = [(0.06, 0.10), (0.10, 0.15), (0.15, 0.20),
               (0.20, 0.30), (0.30, 0.40), (0.40, 9.99)]
    print('\nBY DIRECTIONAL PROJECTION EDGE  (the monotonicity question)')
    agg = defaultdict(lambda: [0, 0])
    for r in graded:
        e = edge_of(r)
        if e is None:
            continue
        for lo, hi in BUCKETS:
            if lo <= e < hi:
                agg[(lo, hi)][1] += 1
                if _grade(r) == 'W':
                    agg[(lo, hi)][0] += 1
                break
    for lo, hi in BUCKETS:
        wn, nn = agg[(lo, hi)]
        if nn:
            hi_s = '+' if hi > 9 else f'-{hi*100:.0f}%'
            print(line(f'{lo*100:.0f}%{hi_s}', wn, nn))

    # Below/at-or-above the 15% signal threshold
    print('\nTHE 15% THRESHOLD the two BACK signals fire on')
    for label, test in (('edge < 15% (no signal)', lambda e: e < 0.15),
                        ('edge >= 15% (supports)', lambda e: e >= 0.15),
                        ('edge >= 30% (strong)', lambda e: e >= 0.30)):
        sub = [r for r in graded if (lambda e: e is not None and test(e))(edge_of(r))]
        print(line(label, sum(1 for r in sub if _grade(r) == 'W'), len(sub)))

    # Split by direction — an OVER-only or UNDER-only edge is a red flag
    print('\nBY DIRECTION  (a one-sided edge usually means a line-shape artifact)')
    for d in ('over', 'under'):
        sub = [r for r in graded if r['direction'] == d]
        print(line(d, sum(1 for r in sub if _grade(r) == 'W'), len(sub)))

    # And per prop family, to see if the edge is one stat carrying everything
    print('\nBY PROP FAMILY')
    fam = defaultdict(lambda: [0, 0])
    for r in graded:
        base = (r.get('prop_type') or '').rsplit('_', 1)[0]
        fam[base][1] += 1
        if _grade(r) == 'W':
            fam[base][0] += 1
    for base in sorted(fam, key=lambda k: -fam[k][1]):
        wn, nn = fam[base]
        print(line(base, wn, nn))


if __name__ == '__main__':
    main()
