#!/usr/bin/env python3
"""Do our panelists hold different opinions, or one opinion in several hats?

== 2026-10-14 - WHY THIS EXISTS ==
Andy's design has the models as a board of panelists, each with their own data
and their own take, and the engine weighing what the panel concludes. The whole
value of a panel is DISAGREEMENT: five analysts who always say the same thing
are one analyst, and a "consensus of 4 of 5" among duplicates is not a
consensus at all -- it is one model with a confidence multiplier bolted on.

So before any panelist is weighted, promoted or wired into a consensus, it has
to be shown to be a distinct voice. This measures that the only way that
matters for betting: how often does each PAIR of panelists take the SAME SIDE
of the posted line?

Side agreement, not deviation correlation, is the right test. Two models can
correlate at +0.9 in deviation and still split on which side of the number they
land, and it is the side that gets bet.

== WHAT IT FOUND ON FIRST RUN ==
    NCAAF TOTAL   all 6 pairs agree 99.5-100%  -- ONE panelist printed 4 times
    NFL   TOTAL   projected/v3/model 98.4-100% -- one bloc; only mc and panel
                                                  are distinct voices
    NFL   MARGIN  projected vs v3 100%, model vs projected 96.7%,
                  panel vs v4 96.9%            -- only mc is independent
    NCAAF MARGIN  projected vs sp_plus 98.8%, mc vs both 99.6%
                                               -- only epa is independent

Which explains why consensus signals have never earned their keep: requiring
two panelists to agree is free when they are the same number.

NOTHING IS WIRED. This is the gate that says which panelists are allowed to
count as separate votes.

USAGE
    python audit_panelist_independence.py
    python audit_panelist_independence.py --sport NFL
    python audit_panelist_independence.py --threshold 0.90
"""
from __future__ import annotations

import argparse
import itertools
import sys

import build_panelist_records as B

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

#: At or above this side-agreement rate two panelists are the same voice and
#: must not be counted as independent votes.
DUPLICATE_AT = 0.95
NEAR_AT = 0.85
#: Below this many shared picks the pair is unmeasured, not independent.
MIN_OVERLAP = 25


def sides(sport: str, market: str):
    """Per panelist: {game index -> +1 over/home, -1 under/away, 0 pass}."""
    tbl = (B.MARGIN_PANELISTS if market == 'margin'
           else B.TOTAL_PANELISTS).get(sport) or {}
    rows = B.joined(sport)
    emin = B.EDGE_MIN[sport]
    out = {k: {} for k in tbl}
    for i, (c, g, _hs, _as) in enumerate(rows):
        if market == 'margin':
            cs = B._f(g.get('close_spread'))
            if cs is None:
                continue
            home_line = -cs if sport == 'NFL' else cs
            base = -home_line
        else:
            base = B._f(g.get('close_total'))
            if base is None:
                continue
        for k, acc in tbl.items():
            pv = acc(c)
            if pv is None:
                continue
            d = pv - base
            out[k][i] = 0 if abs(d) < emin else (1 if d > 0 else -1)
    return out, len(rows)


def run(sport: str, market: str, dup: float, near: float) -> list[tuple]:
    if market == 'margin' and sport not in B.REAL_SPREAD:
        return []
    sd, ngames = sides(sport, market)
    if not sd:
        return []
    names = sorted(sd)
    print(f'\n{"="*80}\n{sport} - {market.upper()}   {ngames} graded games '
          f'- pairwise agreement on SIDE')
    if not names:
        return []
    dups, blocs = [], []
    for a, b in itertools.combinations(names, 2):
        both = [i for i in sd[a]
                if i in sd[b] and sd[a][i] and sd[b][i]]
        if len(both) < MIN_OVERLAP:
            print(f'  {a:>10} vs {b:<10}  n={len(both):>4}  '
                  f'too few shared picks to judge')
            continue
        same = sum(1 for i in both if sd[a][i] == sd[b][i])
        pct = same / len(both)
        flag = ''
        if pct >= dup:
            flag = '  <== SAME VOICE, not two votes'
            dups.append((a, b, pct))
        elif pct >= near:
            flag = '  <-- near-duplicate'
            dups.append((a, b, pct))
        print(f'  {a:>10} vs {b:<10}  n={len(both):>4}  '
              f'agree {100*pct:>5.1f}%{flag}')
    if dups:
        # transitively collapse duplicate pairs into blocs
        parent = {n: n for n in names}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b, _p in dups:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        groups: dict[str, list[str]] = {}
        for n in names:
            groups.setdefault(find(n), []).append(n)
        blocs = [sorted(v) for v in groups.values()]
        print(f'  -> {len(dups)} duplicate pair(s). The board has '
              f'{len(names)} nameplates and {len(blocs)} actual voice(s):')
        for g in sorted(blocs, key=len, reverse=True):
            if len(g) > 1:
                print(f'       one voice: {", ".join(g)}')
            else:
                print(f'       distinct : {g[0]}')
    else:
        print(f'  no duplicate pairs — all {len(names)} panelists are '
              f'distinct voices')
    return [(sport, market, a, b, p) for a, b, p in dups]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(B.RESULTS))
    ap.add_argument('--threshold', type=float, default=DUPLICATE_AT,
                    help=f'same-voice agreement rate (default {DUPLICATE_AT})')
    ap.add_argument('--near', type=float, default=NEAR_AT)
    a = ap.parse_args()
    sports = ([a.sport] if a.sport
              else ['NFL', 'NCAAF', 'NBA', 'NCAAB', 'MLB', 'NHL'])
    print('PANELIST INDEPENDENCE — a panel of duplicates is one analyst.')
    print(f'Same voice at >= {100*a.threshold:.0f}% side agreement; '
          f'near-duplicate at >= {100*a.near:.0f}%; '
          f'under {MIN_OVERLAP} shared picks is unmeasured.')
    allduped = []
    for s in sports:
        for m in ('margin', 'total'):
            try:
                allduped += run(s, m, a.threshold, a.near)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} - {m}: FAILED {type(e).__name__}: {str(e)[:110]}')
    if allduped:
        print(f'\n{"="*80}\n{len(allduped)} duplicate pair(s) across all '
              f'sports. Each is a vote the engine is double-counting.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
