"""Is sharp money winning or losing — and does it get better with an SP+ /
EPA gap behind it?

ANDY 2026-10-12: "want to assess money flow data vs results, is our sharp
data losing more than winning, what about if sharps when on a team and a
certain gap in sp+ off def epa?"

DEFINITION. Sharp money is HANDLE heavier than TICKETS on the same side:
divergence = money_pct - bets_pct. A side with 40% of bets and 60% of the
money is being backed by fewer, larger wagers. public_splits_archive
carries that per source (cz / fr / ftp / oc), so the divergence is averaged
across whichever sources reported.

WHAT IT IS SCORED AGAINST. Two things, deliberately:
  * the base rate of that SIDE (home sides are scored against how often
    home covers/wins), so a signal that merely leans home is not credited
    for the home-field advantage;
  * and for spreads, the result against the number, not the winner.

THE INTERACTION is the question with real teeth: sharp money ALONE is a
crowded, well-known signal. Sharp money AGREEING with a model gap is the
kind of conjunction that is less picked over. Both are reported, plus the
cells where they disagree — because if sharp+gap is good only because gap
is good, the disagreement cell shows it immediately.

MULTIPLICITY. Several divergence thresholds x two gap families x two
markets is a lot of cells. Every cell is printed with its own 2SE and
nothing is selected; a cell that does not clear its band is not a finding.

WRITES NOTHING.

CLI
    python audit_sharp_money_edges.py --sport NCAAF
    python audit_sharp_money_edges.py --sport MLB --market ml
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

SPORTS = {'NFL': ('nfl_game_context', 'nfl_game_results', +1),
          'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', -1),
          'MLB': ('mlb_game_context', 'mlb_game_results', -1),
          'NHL': ('nhl_game_context', 'nhl_game_results', -1)}


def page(table, params, cap=60000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def profit(odds, won):
    """Units won on a 1u stake at American odds."""
    o = f(odds)
    if o is None or o == 0:
        return None
    if won:
        return (o / 100.0) if o > 0 else (100.0 / -o)
    return -1.0


def boot_ci(vals, iters=8000, seed=13):
    """Percentile bootstrap CI of mean ROI. The hit-rate band says nothing
    about money once prices are mixed, and a fade of a favourite-priced side
    can win 60% and still lose."""
    rng = random.Random(seed)
    n = len(vals)
    out = []
    for _ in range(iters):
        t = 0.0
        for _i in range(n):
            t += vals[rng.randrange(n)]
        out.append(t / n)
    out.sort()
    return out[int(iters * 0.025)] * 100, out[int(iters * 0.975)] * 100


def rate(label, sub, base, pad=40):
    """Hit rate AND ROI. A cell is only a finding if the money agrees.

    ══ WHY ROI IS NOT OPTIONAL HERE ══
    The first version of this printed hit rate only, and NCAAF ML showed
    "sharp money + SP+ gap AGREES = 72.1% vs a 44.1% base, +28.0". That
    cell is the favourite-selection artefact, not an edge: the conjunction
    selects the better team, the better team is short-priced, and short
    prices win often while paying nothing. Two things exposed it — the
    same conjunction graded against the SPREAD showed -0.9, and the ROI
    below is negative. A side base rate corrects for home-field; nothing
    but price corrects for price.
    """
    n = len(sub)
    if n < 25:
        print(f'    {label:<{pad}} n={n} too thin')
        return
    w = sum(r['won'] for r in sub)
    hit = w / n * 100
    se2 = (0.5 / n ** 0.5) * 200
    edge = hit - base
    pf = [profit(r.get('odds'), r['won']) for r in sub]
    pf = [p for p in pf if p is not None]
    px = [f(r.get('odds')) for r in sub]
    px = [v for v in px if v not in (None, 0) and abs(v) <= 2000]
    med_px = statistics.median(px) if px else None
    roi = (statistics.fmean(pf) * 100) if pf else None
    roi_s = f'{roi:+6.1f}%' if roi is not None else '    n/a'
    if med_px is not None:
        roi_s += f' @{med_px:+5.0f}'
    flag = ''
    if abs(edge) > se2:
        flag = '  <== beats band' if edge > 0 else '  <== LOSES beyond band'
    ci = ''
    ci_clears = False
    if len(pf) >= 30:
        clo, chi = boot_ci(pf)
        ci_clears = not (clo <= 0 <= chi)
        ci = f'  CI[{clo:+6.1f},{chi:+6.1f}]' + ('' if not ci_clears else ' *')
    # The EDGE label requires the ROI interval to exclude zero, not just a
    # positive point estimate. An earlier version flagged a +6.5% cell whose
    # CI ran [-7.2, +20.2] as an EDGE — the same standard that was used to
    # reject the sharp x SP+ cell, applied inconsistently in the other
    # direction. One bar, both directions.
    if roi is not None and roi > 2.0 and edge > se2 and ci_clears:
        flag = '  <== EDGE (hit AND money, CI excludes 0)'
    elif roi is not None and roi > 2.0 and edge > se2:
        flag = '  <== promising, CI straddles 0'
    print(f'    {label:<{pad}} {w}-{n - w}  {hit:5.1f}%  base {base:5.1f}%  '
          f'edge {edge:+5.1f}  ROI {roi_s}{ci}  n={n:<5} '
          f'±{se2:.0f}{flag}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=sorted(SPORTS))
    ap.add_argument('--market', default='rl', choices=('rl', 'ml', 'total'))
    a = ap.parse_args()
    ctx_t, res_t, sign = SPORTS[a.sport]

    sp = [x for x in page('public_splits_archive', {
        'select': 'game_id,sport,market,pick_side,cz_money_pct,cz_bets_pct,'
                  'fr_handle_pct,fr_bettors_pct,ftp_money_pct,ftp_bets_pct,'
                  'oc_money_pct,oc_bets_pct,captured_at,current_odds,'
                  'current_line',
        'sport': f'eq.{a.sport}'}) if str(x.get('market')) == a.market]
    # select='*' on purpose: the gap columns are sport-specific
    # (home_sp_plus and *_off_epa_pp exist on NCAAF, not MLB), and naming
    # them explicitly 400s the whole query on every other sport, which
    # reads back as "nothing joined" rather than as an error.
    ctx = {str(g['game_id']): g for g in page(ctx_t, {'select': '*'})}
    # ══ WHERE THE PRICE COMES FROM ══
    # public_splits_archive.current_odds exists but is NULL on every row,
    # so the price cannot come from the split. The results tables carry
    # closing moneylines under two different naming schemes (measured
    # 2026-10-12: NCAAF 518/573 populated since 8/1, MLB 789/807, NFL
    # 274/274), so ML ROI is real. No spread price column exists in any
    # sport, so ATS uses the -110 convention for football only; MLB's
    # runline prices swing from +100 to +160 and a flat -110 there would
    # be fiction, so it is left as n/a rather than invented.
    ml_cols = (('home_ml_close', 'away_ml_close') if a.sport == 'MLB'
               else ('close_home_ml', 'close_away_ml'))
    res = page(res_t, {'select': 'game_id,game_date,home_team,away_team,'
                                 'home_score,away_score,close_spread,'
                                 + ','.join(ml_cols)})
    idx = {(str(r['game_date'])[:10], str(r['home_team']).lower(),
            str(r['away_team']).lower()): r for r in res}
    # ══ TWO JOIN PATHS, BECAUSE ONE SPORT DOES NOT HAVE THE OTHER'S ══
    # Measured 2026-10-12: splits.game_id hits <res>.game_id directly on
    # MLB 675/679, NCAAF 469/483, NHL 97/97 — but NFL 0/90, the known
    # NFL/NCAAF id split. NFL instead hits nfl_game_context 90/90, so it
    # reaches results via (date, home, away).
    #
    # Going through context ALONE is what made MLB look like it had 50
    # readings out of 42,736 split rows: mlb_game_context is a
    # current-games table holding 39 rows, not a season of history. The
    # context table is now only asked for the SP+/EPA gap columns, which
    # is all it was ever able to answer.
    by_id = {str(r['game_id']): r for r in res if r.get('game_id')}
    print(f'=== sharp money vs results · {a.sport} · {a.market} ===')
    print(f'  {len(sp)} split readings · {len(ctx)} context · {len(res)} results\n')

    # latest reading per (game, side)
    latest = {}
    for x in sp:
        k = (str(x['game_id']), str(x.get('pick_side') or '').upper())
        c = str(x.get('captured_at') or '')
        if k not in latest or c > latest[k][0]:
            latest[k] = (c, x)

    rows = []
    n_direct = n_bridge = 0
    for (gid, side), (_c, x) in latest.items():
        if side not in ('HOME', 'AWAY'):
            continue
        g = ctx.get(gid) or {}
        r = by_id.get(gid)
        if r is not None:
            n_direct += 1
        elif g:
            r = idx.get((str(g.get('game_date'))[:10],
                         str(g.get('home_team')).lower(),
                         str(g.get('away_team')).lower()))
            if r is not None:
                n_bridge += 1
        if not r:
            continue
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        if hs is None or as_ is None:
            continue
        divs = []
        for m, b in (('cz_money_pct', 'cz_bets_pct'),
                     ('fr_handle_pct', 'fr_bettors_pct'),
                     ('ftp_money_pct', 'ftp_bets_pct'),
                     ('oc_money_pct', 'oc_bets_pct')):
            mv, bv = f(x.get(m)), f(x.get(b))
            if mv is not None and bv is not None:
                divs.append(mv - bv)
        if not divs:
            continue
        margin = hs - as_
        if a.market == 'ml':
            won = (margin > 0) if side == 'HOME' else (margin < 0)
            base_home = None
        else:
            cs = f(r.get('close_spread')) or f(g.get('close_spread'))
            if cs is None:
                continue
            cover = margin - (cs * sign)
            if abs(cover) < 1e-9:
                continue
            won = (cover > 0) if side == 'HOME' else (cover < 0)
        # model gaps
        hsp, asp = f(g.get('home_sp_plus')), f(g.get('away_sp_plus'))
        sp_gap = (hsp - asp) if (hsp is not None and asp is not None) else None
        ho, ao = f(g.get('home_off_epa_pp')), f(g.get('away_off_epa_pp'))
        hd, ad = f(g.get('home_def_epa_pp')), f(g.get('away_def_epa_pp'))
        epa_gap = ((ho - ad) - (ao - hd)) if None not in (ho, ao, hd, ad) else None
        opp_odds = None
        if a.market == 'ml':
            odds = r.get(ml_cols[0] if side == 'HOME' else ml_cols[1])
            opp_odds = r.get(ml_cols[1] if side == 'HOME' else ml_cols[0])
        elif a.sport in ('NFL', 'NCAAF'):
            odds = -110          # convention; football ATS is near-always -110
        else:
            odds = None          # MLB/NHL runline price is not stored
        if a.market != 'ml' and odds is not None:
            opp_odds = -110
        rows.append({'side': side, 'won': 1 if won else 0,
                     'div': statistics.fmean(divs), 'odds': odds,
                     'opp_odds': opp_odds,
                     'sp_gap': sp_gap, 'epa_gap': epa_gap})

    if not rows:
        print('  nothing joined')
        return 0
    # base rate PER SIDE so a home lean is not credited as skill
    hb = [r['won'] for r in rows if r['side'] == 'HOME']
    ab = [r['won'] for r in rows if r['side'] == 'AWAY']
    base_h = statistics.fmean(hb) * 100 if hb else 50.0
    base_a = statistics.fmean(ab) * 100 if ab else 50.0
    print(f'  {len(rows)} side-readings joined · HOME side wins '
          f'{base_h:.1f}% (n={len(hb)}) · AWAY {base_a:.1f}% (n={len(ab)})')
    print(f'  join: {n_direct} by game_id direct to results, {n_bridge} '
          f'bridged via context')
    n_gap = sum(1 for r in rows if r['sp_gap'] is not None
                or r['epa_gap'] is not None)
    print(f'  {n_gap} of those carry a model gap (SP+ or EPA)')
    n_px = sum(1 for r in rows if f(r.get('odds')) not in (None, 0))
    print(f'  {n_px} carry a price, so ROI is computed on that subset')

    def basefor(sub):
        if not sub:
            return 50.0
        return statistics.fmean(base_h if r['side'] == 'HOME' else base_a
                                for r in sub)

    print('\n  SHARP MONEY ALONE (divergence = handle% - tickets%):')
    for lo in (0, 5, 10, 15, 20):
        sub = [r for r in rows if r['div'] >= lo]
        rate(f'divergence >= +{lo}', sub, basefor(sub))
    for hi in (-5, -10, -15, -20, -25):
        sub = [r for r in rows if r['div'] <= hi]
        rate(f'divergence <= {hi} (public side)', sub, basefor(sub))

    # ══ THE FADE, ON THE SAME GAMES ══
    # A losing cell is only a playable signal if betting AGAINST it makes
    # money, and that is not the mirror bucket: divergence is averaged over
    # whichever sources reported, so `div >= +20` (n=169 on MLB ml) and
    # `div <= -20` (n=17) are NOT the same games. The only honest test is to
    # take each high-divergence reading and buy the OTHER side of that same
    # game at the other side's own closing price. For ML there is no push, so
    # the opposite side won exactly when this one lost.
    print()
    print('  FADE — buy the other side of those same games, at ITS price:')
    for lo in (5, 10, 15, 20):
        sub = [r for r in rows if r['div'] >= lo
               and f(r.get('opp_odds')) not in (None, 0)]
        fade = [{'side': r['side'], 'won': 1 - r['won'],
                 'odds': r['opp_odds']} for r in sub]
        rate(f'FADE of divergence >= +{lo}', fade,
             100.0 - basefor(sub) if sub else 50.0)
    print("  (base shown is the complement of the faded side's base rate)")

    for gname, gkey in (('SP+ gap', 'sp_gap'), ('off-def EPA gap', 'epa_gap')):
        have = [r for r in rows if r.get(gkey) is not None]
        if len(have) < 60:
            print(f'\n  {gname}: n={len(have)} too thin to interact')
            continue
        print(f'\n  SHARP MONEY x {gname} '
              f'(gap signed toward the side being read):')
        for lo in (5, 10):
            for agree in (True, False):
                sub = []
                for r in have:
                    if r['div'] < lo:
                        continue
                    g = r[gkey] if r['side'] == 'HOME' else -r[gkey]
                    if (g > 0) == agree:
                        sub.append(r)
                lbl = (f'div >= +{lo} AND gap '
                       f'{"AGREES" if agree else "DISAGREES"}')
                rate(lbl, sub, basefor(sub))
    print('\n  Every cell carries its own 2SE. Several thresholds x two gap')
    print('  families is many tests — a cell that does not clear its band is')
    print('  not a finding, and the DISAGREES rows exist so a conjunction')
    print('  cannot take credit for the gap alone.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
