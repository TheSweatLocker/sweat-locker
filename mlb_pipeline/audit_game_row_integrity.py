"""Duplicate game rows and sign-flipped lines — catch them BEFORE picks lock.

ANDY 2026-10-09, on the Red River game: "there is no match data in game detail
for texas". Tracing it found three symptoms with one root cause, and this is
the guard that would have caught them.

WHAT WENT WRONG, and why it is worth a permanent check
Red River is a NEUTRAL-SITE game (Cotton Bowl) and the sources do not agree
which team is home. That produced:

    ncaaf_game_results 2026-10-10
      Oklahoma @ Texas      close_spread = +6.5   <- canonical; context uses it
      Texas    @ Oklahoma   close_spread = +7.5   <- reversed row

By the convention verified on 6,444 graded games (home wins 78.9% when
spread < 0; correlation(close_spread, home_margin) = -0.723; spread_result
agrees with margin+spread>0 on 99.8%), a NEGATIVE spread means the home team
is favoured. So the reversed row is CORRECT for its own ordering — Texas away
and favoured by 7.5 — while the canonical row claims the UNRANKED road team is
favoured by 6.5 over the AP #1 home team. The sign was never re-flipped when
the orderings were merged.

Six independent indicators say Texas is favoured: AP #1 vs unranked, SP+ 22.4
vs 12.8, SOR 7.90 (#19) vs 1.92 (#75), our own projected_spread of Texas by
11.82, the reversed row's +7.5, and pickswise listing "Oklahoma RL 7.5" (the
dog taking points). Only the canonical +6.5 disagrees.

THE DOWNSTREAM COST was not cosmetic:
  * a FABRICATED EDGE — the engine compared its projection (Texas by 11.8) to
    a line it read as Oklahoma by 6.5 and saw ~18 points of disagreement, the
    largest on the slate and entirely an artefact of the sign;
  * a PICK LABEL THAT MISREPRESENTS THE BET — "Texas +6.5" says Texas is
    TAKING points when Texas is laying them;
  * THIN EXTERNALS — sources listing Texas first never match an
    Oklahoma-first slug, so the game showed 3 externals against 10-12 on
    comparable games, which is what Andy actually noticed.

WHAT THIS CHECKS
  1. DUPLICATE game-pairs: more than one row for the same (date, unordered
     team pair). Measured 5 of 15,598 in NCAAF — rare, but two of the five are
     this weekend's marquee games, so rarity is not safety. Reports whether
     the duplicates are REVERSED (different home/away) or same-ordering with
     DIFFERENT LINES, because those are different bugs: the first is an
     identity problem, the second means one of the two lines is simply wrong.
  2. SIGN ANOMALIES: a market line that contradicts our own sp_gap by a wide
     margin. Gated deliberately — 18 of 254 comparable NCAAF games (7.1%)
     disagree legitimately, so a bare disagreement is NOT a defect. This flags
     only where the gap is large AND the AP poll corroborates the direction,
     which is what separates Red River from an honest model/market difference.

WRITES NOTHING. The point is to run before the lock, not to rewrite after it.

CLI
    python audit_game_row_integrity.py
    python audit_game_row_integrity.py --sport NCAAF --upcoming-only
"""
from __future__ import annotations

import argparse
import collections
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
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
CONTEXT = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
#: NCAAF/MLB store NEGATIVE for a home favourite; NFL stores POSITIVE. Both
#: verified empirically — getting this backwards inverts every finding below.
HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': +1.0}
#: Only flag a sign anomaly when our rating gap is at least this big. Below
#: it, market-vs-model disagreement is ordinary and common.
SP_GAP_FLOOR = 7.0


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--upcoming-only', action='store_true',
                    dest='upcoming_only')
    ap.add_argument('--today', default='2026-10-09')
    a = ap.parse_args()
    sport = a.sport

    # ── 1. DUPLICATE GAME-PAIRS ──────────────────────────────────────────
    cols = 'game_date,home_team,away_team,home_score,close_spread'
    res = _page(RESULTS[sport], {'select': cols})
    groups = collections.defaultdict(list)
    for x in res:
        d = str(x.get('game_date') or '')[:10]
        a_, h = str(x.get('away_team')), str(x.get('home_team'))
        if d and a_ and h:
            groups[(d, frozenset((a_, h)))].append(x)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if a.upcoming_only:
        dups = {k: v for k, v in dups.items() if k[0] >= a.today}
    print(f'=== {sport} ROW INTEGRITY')
    print(f'    {len(res)} result rows · {len(groups)} distinct '
          f'(date, team-pair) · {len(dups)} duplicated')
    if dups:
        print('\n  DUPLICATE GAME-PAIRS — two rows for one game:')
        for (d, pair), v in sorted(dups.items()):
            orders = {(str(x['away_team']), str(x['home_team'])) for x in v}
            lines = {_f(x.get('close_spread')) for x in v}
            kind = ('REVERSED home/away' if len(orders) > 1
                    else 'same ordering, DIFFERENT LINES'
                    if len(lines) > 1 else 'exact duplicate')
            played = any(x.get('home_score') is not None for x in v)
            print(f'\n    {d}  {sorted(pair)}  -> {kind}'
                  f'{"  [PLAYED]" if played else ""}')
            for x in v:
                print(f'        {str(x["away_team"])[:22]:<23}@ '
                      f'{str(x["home_team"])[:22]:<23}'
                      f'spread={x.get("close_spread")} '
                      f'score={x.get("home_score")}')
            if kind.startswith('REVERSED'):
                print('        ^ identity problem: externals and picks will')
                print('          split across the two slugs, and the merged')
                print('          row may carry the OTHER ordering\'s sign.')
            elif kind.startswith('same ordering'):
                print('        ^ one of these lines is simply wrong; the')
                print('          engine will price against whichever it reads.')

    # ── 2. SIGN ANOMALIES ────────────────────────────────────────────────
    ctx = _page(CONTEXT[sport],
                {'select': 'game_date,away_team,home_team,close_spread,'
                           'sp_gap,home_ap_rank,away_ap_rank,'
                           'projected_spread,pick_locked_at'})
    if a.upcoming_only:
        ctx = [x for x in ctx if str(x.get('game_date') or '') >= a.today]
    flags = []
    for x in ctx:
        cs, sg = _f(x.get('close_spread')), _f(x.get('sp_gap'))
        if cs is None or sg is None or abs(sg) < SP_GAP_FLOOR:
            continue
        # sp_gap > 0 means HOME is the better team by our rating.
        home_fav_market = (cs * HOME_FAV_SIGN[sport]) > 0
        if home_fav_market == (sg > 0):
            continue                      # market and rating agree
        # AP corroboration: does the poll back the rating rather than the
        # market? Without this the check would fire on ordinary disagreement.
        hr, ar = x.get('home_ap_rank'), x.get('away_ap_rank')
        ap_backs_rating = False
        if sg > 0 and hr is not None and ar is None:
            ap_backs_rating = True        # home ranked, away unranked
        elif sg < 0 and ar is not None and hr is None:
            ap_backs_rating = True
        elif hr is not None and ar is not None:
            ap_backs_rating = ((int(hr) < int(ar)) == (sg > 0))
        flags.append((x, ap_backs_rating))
    corroborated = [f for f in flags if f[1]]
    print(f'\n  SIGN ANOMALIES (|sp_gap| >= {SP_GAP_FLOOR:.0f} and market '
          f'disagrees): {len(flags)}')
    print(f'    of those, AP poll BACKS our rating against the market: '
          f'{len(corroborated)}  <-- these are the suspicious ones')
    for x, backed in sorted(flags, key=lambda z: -abs(_f(z[0]['sp_gap']) or 0)):
        tag = 'AP CORROBORATES -> LIKELY SIGN ERROR' if backed else \
            'no AP corroboration — may be an honest disagreement'
        print(f'\n    {x["game_date"]} {str(x["away_team"])[:18]:<19}@ '
              f'{str(x["home_team"])[:18]:<19}')
        print(f'        close_spread={_f(x["close_spread"]):+.1f}  '
              f'sp_gap={_f(x["sp_gap"]):+.1f}  '
              f'projected={x.get("projected_spread")}  '
              f'ap h={x.get("home_ap_rank")} a={x.get("away_ap_rank")}  '
              f'locked={"Y" if x.get("pick_locked_at") else "N"}')
        print(f'        {tag}')

    print('\n' + '=' * 70)
    print('  Run this BEFORE the lock. Nothing here is auto-corrected: 7.1% of')
    print('  comparable NCAAF games disagree between market and SP+ for honest')
    print('  reasons, so a blanket flip on disagreement would corrupt real')
    print('  lines. The AP-corroborated rows are the ones worth a human look.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
