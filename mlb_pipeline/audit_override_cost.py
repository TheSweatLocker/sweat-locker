"""Which named override costs us money? — NCAAF / NFL sides.

ANDY 2026-10-10, three times over: "Still dont think you are going about this
correctly."

He was right, and this is what I had wrong. Every test I ran was of the form
"does feature X beat the closing spread". That is the hardest target in the
building — the line exists to make covering a coin flip — so a null there says
almost nothing about our product. The question I never asked is whether OUR
OWN NUMBER is any good, and what happens to it between projection and publish.

WHAT THE FIRST PASS FOUND, leak-free, 255 NCAAF games
  our projected_spread   MAE 14.83  bias -5.47  r +0.443  slope 0.706
  the market close        MAE 11.20  bias -0.46  r +0.757  slope 0.990
The bias is not a constant. It is CONCENTRATED IN BIG FAVOURITES: on a home
favourite of 14+ we are 15.79 points light (n=88); on an away favourite of
7-14 we are 8.00 points light (n=17). Near pick-em we are as accurate as the
market (-1.06 vs -0.59). We compress every big number toward the middle, which
mechanically points the pick at the dog — and that IS the long-observed dog
bias, now measured rather than inferred.

WHY THAT IS NOT THE WHOLE STORY
A worse margin predictor can still be a better PICK, because picking only
needs the right SIDE of one number. So the second question: on the same games,
how does following our own projection compare with the pick we actually
published?

WHAT THIS SCRIPT MEASURES
primary_play is a JSON blob carrying an authoritative `side` (HOME/AWAY/
OVER/UNDER) AND a full audit trail of every override that fired — _ml_reroute,
_edge_cap, _oc_flipped, _discipline_cap, _lr_disagreement_cap,
_heavy_ml_reroute, _ncaaf_hi_conv_dog_cap and more. So the overrides are not a
black box; each one is a flag we can grade.

  1. PAIRED: follow-the-projection vs the published side, same games, same
     denominator, so the comparison is not a denominator artefact.
  2. McNEMAR on the DISCORDANT games only — the games where the two
     disagree are the only ones carrying information about which is better.
  3. PER-OVERRIDE: for each logged flag, the cover rate of picks carrying it
     against picks without it. A flag that loses is a named, removable thing.

A TRAP THIS SCRIPT AVOIDS. Side cannot be read by matching the team name
against the blob: its prose names BOTH teams on 42% of rows, so a
home-name-first check silently relabels away picks as home. The `side` field
is parsed instead. The first version of this analysis got that wrong and its
published-pick numbers were not usable.

LEAKAGE. projected_spread lives in an upserted table, so it COULD have been
recomputed after the fact. It demonstrably was not: a post-game projection
would beat the market, and ours has MAE 14.83 against the market's 11.20. An
honest check that passes is worth more than an assumption.

WRITES NOTHING.

CLI
    python audit_override_cost.py
    python audit_override_cost.py --sport NFL
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
CTX = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
RES = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
#: NCAAF stores a NEGATIVE close_spread for a HOME favourite, NFL POSITIVE.
#: Verified on 6,444 graded NCAAF games (home wins 78.9% when spread<0,
#: correlation -0.723, spread_result agrees with margin+spread>0 on 99.8%).
HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': +1.0}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:150]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _blob(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith('{'):
        try:
            return json.loads(v)
        except Exception:                                   # noqa: BLE001
            return None
    return None


def rate(vals, label, floor=25, pad=34):
    if len(vals) < floor:
        print(f'    {label:<{pad}}n={len(vals):<4} under the n>={floor} floor')
        return None
    hit = statistics.fmean(vals) * 100
    se2 = (0.5 / len(vals) ** 0.5) * 100 * 2
    print(f'    {label:<{pad}}{hit:6.2f}%  n={len(vals):<4} '
          f'+/-{se2:4.1f}pp  {hit - BREAKEVEN:+6.2f} vs BE')
    return hit


def band(mh):
    a = abs(mh)
    return ('A fav 0-3 ' if a < 3 else 'B fav 3-7 ' if a < 7 else
            'C fav 7-14' if a < 14 else 'D fav 14+ ')


def load(sport):
    ctx = _page(CTX[sport], {'select': 'game_id,game_date,home_team,away_team,'
                                       'primary_play,projected_spread'})
    res = _page(RES[sport], {'select': 'game_id,game_date,home_team,away_team,'
                                       'close_spread,spread_result'})
    byid = {str(x['game_id']): x for x in res}
    bydha = {(str(x.get('game_date'))[:10],
              str(x.get('home_team') or '').lower(),
              str(x.get('away_team') or '').lower()): x for x in res}
    rows, nojoin, nograde = [], 0, 0
    for g in ctx:
        r0 = byid.get(str(g['game_id'])) or bydha.get(
            (str(g.get('game_date'))[:10],
             str(g.get('home_team') or '').lower(),
             str(g.get('away_team') or '').lower()))
        if not r0:
            nojoin += 1
            continue
        sr = str(r0.get('spread_result') or '').lower()
        if sr not in ('home_covered', 'away_covered'):
            nograde += 1
            continue
        mkt = _f(r0.get('close_spread'))
        if mkt is None:
            continue
        pp = _blob(g.get('primary_play')) or {}
        side = str(pp.get('side') or '').upper()
        rows.append({
            'gid': str(g['game_id']), 'date': str(g.get('game_date'))[:10],
            'mh': mkt * HOME_FAV_SIGN[sport],
            'hc': 1 if sr == 'home_covered' else 0,
            'proj': _f(g.get('projected_spread')),
            'side': side if side in ('HOME', 'AWAY') else None,
            'market': str(pp.get('type') or ''),
            'tier': str(pp.get('tier') or ''),
            'conv': _f(pp.get('conviction')),
            'flags': sorted(k for k in pp if k.startswith('_')),
        })
    print(f'    {len(ctx)} ctx rows · {len(rows)} graded+joined · '
          f'{nojoin} unjoinable · {nograde} ungraded')
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--min-edge', type=float, default=0.5, dest='min_edge')
    a = ap.parse_args()
    sport = a.sport
    print(f'=== {sport} OVERRIDE COST AUDIT')
    rows = load(sport)
    if len(rows) < 40:
        print('    too few rows.')
        return 0

    def proj_side(x):
        """HOME if our own number likes the home side of the close."""
        if x['proj'] is None:
            return None
        e = x['proj'] - x['mh']
        return None if abs(e) < a.min_edge else ('HOME' if e > 0 else 'AWAY')

    pair = [x for x in rows if proj_side(x) and x['side']]
    print(f'\n  PAIRED on the SAME {len(pair)} games (both a projection edge')
    print('  and a published side). Identical denominator, so a difference')
    print('  here is not a sample artefact.')
    pj = [x['hc'] if proj_side(x) == 'HOME' else 1 - x['hc'] for x in pair]
    pb = [x['hc'] if x['side'] == 'HOME' else 1 - x['hc'] for x in pair]
    rate(pj, 'follow OUR OWN projection')
    rate(pb, 'the side we PUBLISHED')
    agree = sum(1 for x in pair if proj_side(x) == x['side'])
    print(f'    publish agrees with our own projection on {agree}/{len(pair)}'
          f' = {agree / len(pair) * 100:.1f}%')

    # ---- McNemar on the discordant games -------------------------------
    disc = [x for x in pair if proj_side(x) != x['side']]
    pw = sum(1 for x in disc
             if (x['hc'] if proj_side(x) == 'HOME' else 1 - x['hc']) == 1)
    bw = len(disc) - pw
    print(f'\n  McNEMAR — only the {len(disc)} games where the two DISAGREE')
    print('  carry information about which is better:')
    print(f'    projection right, publish wrong : {pw}')
    print(f'    publish right, projection wrong : {bw}')
    if pw + bw >= 10:
        z = (pw - bw) / math.sqrt(pw + bw)
        share = pw / (pw + bw) * 100
        print(f'    projection wins {share:.1f}% of the disagreements, '
              f'z = {z:+.2f}')
        print(f'    -> {"SIGNIFICANT at 2SE" if abs(z) >= 2 else "not significant at 2SE"}'
              f'; every override is a coin flip we are paying for'
              if abs(z) >= 2 else '')

    # ---- by market favourite size --------------------------------------
    print('\n  BY MARKET FAVOURITE SIZE (same games in each row):')
    for b in sorted({band(x['mh']) for x in pair}):
        sub = [x for x in pair if band(x['mh']) == b]
        if len(sub) < 20:
            print(f'    {b}  n={len(sub)} too small')
            continue
        p = statistics.fmean(x['hc'] if proj_side(x) == 'HOME' else 1 - x['hc']
                             for x in sub) * 100
        q = statistics.fmean(x['hc'] if x['side'] == 'HOME' else 1 - x['hc']
                             for x in sub) * 100
        ag = sum(1 for x in sub if proj_side(x) == x['side'])
        se2 = (0.5 / len(sub) ** 0.5) * 100 * 2
        print(f'    {b}  n={len(sub):<4} projection {p:6.2f}%  published '
              f'{q:6.2f}%  delta {q - p:+6.2f}pp (+/-{se2:.1f})  '
              f'agree {ag / len(sub) * 100:3.0f}%')

    # ---- per-override attribution --------------------------------------
    graded = [x for x in rows if x['side']]
    base = [x['hc'] if x['side'] == 'HOME' else 1 - x['hc'] for x in graded]
    print(f'\n  PER-OVERRIDE: published-side cover rate WITH each flag vs')
    print(f'  WITHOUT it. Baseline = all {len(graded)} published sides at '
          f'{statistics.fmean(base) * 100:.2f}%.')
    counts = collections.Counter(f for x in graded for f in x['flags'])
    print(f'      {"flag":<30}{"with":>20}{"without":>18}{"delta":>9}')
    deltas = []
    for flag, c in counts.most_common():
        if c < 20:
            continue
        w = [x['hc'] if x['side'] == 'HOME' else 1 - x['hc']
             for x in graded if flag in x['flags']]
        o = [x['hc'] if x['side'] == 'HOME' else 1 - x['hc']
             for x in graded if flag not in x['flags']]
        if len(w) < 20 or len(o) < 20:
            continue
        hw, ho = statistics.fmean(w) * 100, statistics.fmean(o) * 100
        se2 = (0.5 / len(w) ** 0.5) * 100 * 2
        deltas.append((hw - ho, flag, hw, len(w), ho, len(o), se2))
        print(f'      {flag:<30}{f"{hw:.1f}% n={len(w)}":>20}'
              f'{f"{ho:.1f}% n={len(o)}":>18}{hw - ho:+8.1f}')
    print(f'    ({len(counts)} distinct flags seen; those under n=20 omitted)')

    print('\n' + '=' * 74)
    if deltas:
        deltas.sort()
        worst = deltas[0]
        print(f'  WORST flag: {worst[1]} — {worst[2]:.1f}% with (n={worst[3]})'
              f' vs {worst[4]:.1f}% without, {worst[0]:+.1f}pp, '
              f'2SE +/-{worst[6]:.1f}pp')
        sig = [d for d in deltas if d[0] < 0 and abs(d[0]) > d[6]]
        print(f'  {len(sig)} flag(s) are negative by more than their own 2SE '
              f'band.')
        print('  These are NAMED, REMOVABLE code paths — not a missing lens.')
    print('\n  Multiplicity: flags were not selected, every flag with n>=20 is')
    print('  listed, so read the delta against the 2SE column rather than')
    print('  picking the largest number.')

    lr_flip_report(sport, rows)
    return 0


def lr_flip_report(sport, rows):
    """The LR override, separated into SELECTION and CAUSE.

    The with/without table above credits the _pre_lr cluster with -14.4pp, but
    that comparison is contaminated: the LR-touched cohort is 68.4% mid-range
    favourites (3-7) against 17.1% of all graded games, so it is being
    compared against a different kind of game. The honest test is WITHIN the
    cohort — on the very same games, how did the side LR replaced compare
    with the side LR published? Same games, same band mix, no selection.

    And only a minority of overrides are side FLIPS. An override that keeps
    the side cannot change a spread result, so the flips must carry whatever
    effect exists. They are isolated here rather than pooled.
    """
    ctx = _page(CTX[sport], {'select': 'game_id,game_date,home_team,'
                                       'away_team,primary_play'})
    res = {str(x['game_id']): x for x in _page(
        RES[sport], {'select': 'game_id,spread_result'})}
    flips, same, promos = [], [], []
    T = {'COVERAGE': 0, 'LEAN': 1, 'STRONG': 2, 'PRIME': 3}
    for g in ctx:
        pp = _blob(g.get('primary_play'))
        if not pp:
            continue
        pre = pp.get('_pre_lr')
        if not isinstance(pre, dict):
            continue
        sr = str((res.get(str(g['game_id'])) or {}).get('spread_result')
                 or '').lower()
        if sr not in ('home_covered', 'away_covered'):
            continue
        hc = 1 if sr == 'home_covered' else 0
        ps = str(pre.get('side') or '').upper()
        cs = str(pp.get('side') or '').upper()
        if ps not in ('HOME', 'AWAY') or cs not in ('HOME', 'AWAY'):
            continue
        rec = {'g': f"{g.get('away_team')} @ {g.get('home_team')}",
               'd': str(g.get('game_date'))[:10],
               'pre': ps, 'pub': cs, 'pret': str(pre.get('tier')),
               'pubt': str(pp.get('tier')),
               'prehit': hc if ps == 'HOME' else 1 - hc,
               'pubhit': hc if cs == 'HOME' else 1 - hc}
        (flips if ps != cs else same).append(rec)
        if T.get(rec['pubt'], -1) > T.get(rec['pret'], -1):
            promos.append(rec)

    print('\n' + '=' * 74)
    print(f'  THE LR OVERRIDE, WITHIN-COHORT (no selection effect)')
    allr = flips + same
    if not allr:
        print('    no graded LR-touched games.')
        return
    print(f'    {len(allr)} graded LR-touched games · {len(flips)} are SIDE '
          f'FLIPS, {len(same)} keep the side')
    print(f'    side LR REPLACED  covered '
          f'{statistics.fmean(x["prehit"] for x in allr) * 100:5.1f}% '
          f'(n={len(allr)})')
    print(f'    side LR PUBLISHED covered '
          f'{statistics.fmean(x["pubhit"] for x in allr) * 100:5.1f}% '
          f'(n={len(allr)})')
    if same:
        print(f'    ...of which the {len(same)} NON-flips are identical by '
              f'construction at '
              f'{statistics.fmean(x["pubhit"] for x in same) * 100:.1f}% — an '
              f'override that keeps the side cannot move a spread result.')
    if flips:
        w = sum(x['pubhit'] for x in flips)
        pw = sum(x['prehit'] for x in flips)
        print(f'\n    THE {len(flips)} SIDE FLIPS carry all of it:')
        print(f'      side LR replaced : {pw}-{len(flips) - pw} = '
              f'{pw / len(flips) * 100:.1f}%')
        print(f'      the flip we shipped: {w}-{len(flips) - w} = '
              f'{w / len(flips) * 100:.1f}%')
        ha = sum(1 for x in flips if x['pre'] == 'HOME' and x['pub'] == 'AWAY')
        print(f'      {ha}/{len(flips)} of the flips are HOME -> AWAY')
        # Honest power statement. These are ONE measurement, not two:
        # flipping the side inverts the result, so 3-11 and 11-3 are the same
        # 14 games seen from both ends.
        n, k = len(flips), min(w, len(flips) - w)
        p2 = 2 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
        print(f'      two-sided binomial vs a coin flip: p = {p2:.3f} on '
              f'n={n}')
        print('      NOTE: the two lines above are ONE measurement seen from')
        print('      both ends — flipping a side inverts the result — so do')
        print('      not read them as two independent confirmations.')
        print('\n      every flip:')
        for x in sorted(flips, key=lambda z: z['d']):
            print(f"        {x['d']}  {x['g'][:34]:<34} "
                  f"{x['pre']}->{x['pub']}  {x['pret']}->{x['pubt']:<8} "
                  f"{'FLIP LOST' if not x['pubhit'] else 'flip won'}")
    if promos:
        print(f'\n    LR TIER PROMOTIONS (stake raised), n={len(promos)}: '
              f'{statistics.fmean(x["pubhit"] for x in promos) * 100:.1f}% '
              f'cover')
        c = collections.Counter((x['pret'], x['pubt']) for x in promos)
        for k2, v in c.most_common():
            sub = [x for x in promos if (x['pret'], x['pubt']) == k2]
            print(f'      {k2[0]:>8} -> {k2[1]:<8} n={v:<3} cover '
                  f'{statistics.fmean(x["pubhit"] for x in sub) * 100:5.1f}%')
    print('\n  WHAT IS ALREADY IN THE CODE: defensive_gates.py:1387 already')
    print('  caps NCAAF lr_v1 PRIME to STRONG, documented on lr_v1 NCAAF')
    print('  going 5-10 (n=15). That cap addresses the TIER. It does not')
    print('  touch the SIDE, and the side flip is the part that loses.')


if __name__ == '__main__':
    sys.exit(main())
