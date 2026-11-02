#!/usr/bin/env python3
"""Grade the SHARP side and the PUBLIC side separately, every sport, every market.

== 2026-11-02 - WHY THIS DID NOT EXIST ====================================
Andy: "deep dive into the relationship of results vs public money flow
backing vs sharp, across all sports ... is sharp money right ever? Also look
into external picks and money flow lined up for college basketball."

grade_money_flow.py answers HALF of that. It reads `line_snapshot`, which is
100% source='oddscrowd', and in that feed `pick_side` is the PUBLIC majority
side. So everything the engine knows about money flow is a measurement of the
PUBLIC, on the one feed that barely covers anything but baseball:

    oc_money_pct non-null in public_splits_archive, by sport
        MLB   84%        NHL    14%
        NCAAF  6%        NCAAB   0%
        NFL    5%

The other three feeds -- fadereport (fr), fadethepublic (ftp), cleatz (cz) --
key on `sharp_side_norm` and store the handle/ticket share ON THE SHARP SIDE
(archive_public_splits.latest_fr / .latest_ftp / .latest_cz, all three keyed
`(game_id, mkt, sharp_side)`). oddscrowd keys on the public side
(latest_oc -> line_snapshot.pick_side).

    => public_splits_archive.pick_side CARRIES TWO DIFFERENT MEANINGS IN ONE
       COLUMN, decided by which scraper populated the row.

That is the mechanical explanation for
project_cz_splits_contradict_oddscrowd_1029 ("fav ticket share MIN 37->80,
LAC 70->41 ... 4 of 4 crossed 50"). The feeds were never contradicting each
other. They were labelling different sides, and the comparison read one as the
other. A table called public_splits_archive has three of its four feeds
indexing the SHARP side.

Nothing has ever graded those three feeds, so "is sharp money right" has never
been answered on our own data. This answers it.

TWO THINGS IT REFUSES TO ASSUME:
  * NCAAB's spread sign. grade_money_flow.HCAP_SIGN has no NCAAB entry at all,
    and feedback_edge_formula_sign_audit is explicit that the sign differs by
    sport (NFL -1, everyone else +1). So the sign is MEASURED here from
    close_spread vs final margin, and the sport's spreads are dropped if it
    cannot be established, rather than inheriting NBA's.
  * that a sharp side with high handle is the same signal as one with low
    handle. "Sharp" means little when the sharp side is also the popular side,
    so the contrarian cells are reported separately.

Every cell prints n, breakeven at the real price, ROI at each row's own price,
and BOTH date halves, per feedback_sample_size_with_pct and the n>=30 gate.

    python _sharp_vs_public_1102.py
    python _sharp_vs_public_1102.py --sport NCAAB
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grade_money_flow as G          # noqa: E402  (audited settle/implied/page)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                                  # noqa: BLE001
    pass

CTX = dict(G.CTX, NCAAB='ncaab_game_context')
RES = dict(G.RES, NCAAB='ncaab_game_results')
SHARP_FEEDS = (('fr', 'fr_handle_pct', 'fr_bettors_pct'),
               ('ftp', 'ftp_money_pct', 'ftp_bets_pct'),
               ('cz', 'cz_money_pct', 'cz_bets_pct'))
FLOOR = 30


def _settle(sport, market, side, g, sign):
    """G.settle with an injected sign so a sport absent from HCAP_SIGN works."""
    had = sport in G.HCAP_SIGN
    old = G.HCAP_SIGN.get(sport)
    G.HCAP_SIGN[sport] = sign
    try:
        return G.settle(sport, market, side, g)
    finally:
        if had:
            G.HCAP_SIGN[sport] = old
        else:
            G.HCAP_SIGN.pop(sport, None)


def verify_hcap_sign(sport, games):
    """Return (+1.0/-1.0, note) for close_spread, or (None, why) if unestablished.

    If close_spread is the HOME-side betting line (-7.5 when home is favoured)
    then margin and close_spread move OPPOSITE ways, and sign=+1.0 is right,
    because settle() computes eff = margin + sign*cs for HOME. A POSITIVE
    correlation means the column is stored the other way round (NFL's case).
    """
    pairs = []
    for g in games:
        cs = G.f(g.get('close_spread'))
        hs, a_s = G.f(g.get('home_score')), G.f(g.get('away_score'))
        if cs is None or hs is None or a_s is None:
            continue
        pairs.append((cs, hs - a_s))
    if len(pairs) < FLOOR:
        return None, f'only {len(pairs)} graded spreads, under the {FLOOR} floor'
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs) ** 0.5
    syy = sum((p[1] - my) ** 2 for p in pairs) ** 0.5
    if sxx == 0 or syy == 0:
        return None, 'close_spread or margin is constant'
    corr = sxy / (sxx * syy)
    if abs(corr) < 0.25:
        return None, (f'corr(close_spread, margin)={corr:+.3f} on n={n} is too '
                      f'weak to fix a sign')
    sign = 1.0 if corr < 0 else -1.0
    # Confirm it: under the right sign, blanket HOME ATS must land near 50%.
    w = l = 0
    for g in games:
        r = _settle(sport, 'rl', 'HOME', g, sign)
        if r == 'WIN':
            w += 1
        elif r == 'LOSS':
            l += 1
    ats = 100.0 * w / max(w + l, 1)
    note = f'corr={corr:+.3f} n={n} -> sign {sign:+.0f}; HOME ATS {ats:.1f}% n={w+l}'
    if not 40.0 <= ats <= 60.0:
        return None, note + ' -- outside 40-60%, NOT TRUSTED'
    return sign, note


def breakeven(market, side, c, g):
    """Breakeven % for this side. ML off the real price; never a flat -110."""
    if market != 'ml':
        return G.FLAT_BREAKEVEN
    want = (('close_home_ml', 'home_ml_close') if side == 'HOME'
            else ('close_away_ml', 'away_ml_close'))
    for src in (c, g):
        if not src:
            continue
        for cc in want:
            imp = G.implied(src.get(cc))
            if imp is not None:
                return imp
    return None


def report(title, cells):
    print(f'\n{"=" * 104}\n{title}\n{"=" * 104}')
    print(f'  {"cell":<32}{"market":<7}{"W-L-P":>12}{"hit%":>8}{"be":>8}'
          f'{"edge":>8}{"ROI%":>8}   early/late     95% CI')
    shown = 0
    for (label, market), rs in sorted(cells.items()):
        w = sum(1 for r in rs if r['res'] == 'WIN')
        l = sum(1 for r in rs if r['res'] == 'LOSS')
        p = sum(1 for r in rs if r['res'] == 'PUSH')
        n = w + l
        if n < FLOOR:
            continue
        shown += 1
        hit = 100.0 * w / n
        bes = [r['be'] for r in rs if r['be'] is not None]
        be = sum(bes) / len(bes) if bes else G.FLAT_BREAKEVEN
        # ROI at each row's OWN price (feedback_flat_110_inflates_every_ml_figure)
        prof = 0.0
        staked = 0
        for r in rs:
            if r['res'] not in ('WIN', 'LOSS') or not r['be'] or r['be'] <= 0:
                continue
            dec = 100.0 / r['be']          # decimal payout implied by the price
            staked += 1
            prof += (dec - 1.0) if r['res'] == 'WIN' else -1.0
        roi = (100.0 * prof / staked) if staked else float('nan')
        rs2 = sorted(rs, key=lambda r: r['date'])
        half = len(rs2) // 2               # split by ROW COUNT, not median date

        def hr(sub):
            ww = sum(1 for r in sub if r['res'] == 'WIN')
            ll = sum(1 for r in sub if r['res'] == 'LOSS')
            return f'{100.0 * ww / (ww + ll):.0f}%' if ww + ll >= 8 else '--'

        lo, hi = G.wilson(w, n)
        print(f'  {label:<32}{market:<7}{f"{w}-{l}-{p}":>12}{hit:>7.1f}%'
              f'{be:>7.1f}%{hit - be:>+8.1f}{roi:>8.1f}   '
              f'{hr(rs2[:half]):>4} / {hr(rs2[half:]):<4}  [{lo:.0f},{hi:.0f}]')
    if not shown:
        print(f'  (no cell reaches n>={FLOOR})')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    a = ap.parse_args()
    sports = [a.sport] if a.sport else sorted(CTX)

    print('loading public_splits_archive ...')
    arch = G.page('public_splits_archive',
                  {'select': 'sport,game_id,market,pick_side,captured_at,'
                             'oc_money_pct,oc_bets_pct,fr_handle_pct,'
                             'fr_bettors_pct,ftp_money_pct,ftp_bets_pct,'
                             'cz_money_pct,cz_bets_pct'}, 'captured_at.desc')
    print(f'  {len(arch)} rows')
    last = {}
    for r in arch:
        k = (str(r.get('sport')), str(r.get('game_id')),
             str(r.get('market')).lower(), str(r.get('pick_side')).upper())
        if k not in last:                  # captured_at.desc -> first is latest
            last[k] = r
    print(f'  {len(last)} distinct (sport, game, market, side)')

    for sport in sports:
        if sport not in RES:
            print(f'\n!! {sport}: no results table mapped - SKIPPED')
            continue
        ctx_idx, res_gid, res_key = {}, {}, {}
        try:
            for c in G.page(CTX[sport], {'select': '*'}, 'game_date.desc'):
                ctx_idx[str(c.get('game_id'))] = c
        except Exception as e:                                     # noqa: BLE001
            print(f'  !! ctx {sport}: {e}')
        try:
            for g in G.page(RES[sport], {'select': '*'}, 'game_date.desc'):
                if g.get('home_score') is None:
                    continue
                if g.get('game_id') is not None:
                    res_gid[str(g.get('game_id'))] = g
                res_key[(G.nz(g.get('away_team')), G.nz(g.get('home_team')),
                         str(g.get('game_date'))[:10])] = g
        except Exception as e:                                     # noqa: BLE001
            print(f'  !! results {sport}: {e}')

        sign = G.HCAP_SIGN.get(sport)
        if sign is not None:
            note = f'inherited from grade_money_flow.HCAP_SIGN = {sign:+.0f}'
        else:
            pool = list(res_gid.values()) or list(res_key.values())
            sign, why = verify_hcap_sign(sport, pool)
            note = 'MEASURED: ' + why

        pub, shp = defaultdict(list), defaultdict(list)
        miss = defaultdict(int)
        for (sp, gid, market, side), r in last.items():
            if sp != sport:
                continue
            if market not in ('rl', 'spread', 'puckline', 'total', 'ml'):
                continue
            mk = 'rl' if market in ('rl', 'spread', 'puckline') else market
            c = ctx_idx.get(gid)
            g = res_gid.get(gid)
            if not g and c:
                g = res_key.get((G.nz(c.get('away_team')),
                                 G.nz(c.get('home_team')),
                                 str(c.get('game_date'))[:10]))
            if not g:
                miss['no final score'] += 1
                continue
            if mk == 'rl' and sign is None:
                miss['spread sign unestablished'] += 1
                continue
            res = _settle(sport, mk, side, g, sign if sign is not None else 1.0)
            if res is None:
                miss['could not settle'] += 1
                continue
            date = str((c or g).get('game_date') or g.get('game_date'))[:10]
            rec = {'res': res, 'be': breakeven(mk, side, c, g), 'date': date}

            # ---- PUBLIC lens: oddscrowd, pick_side IS the public side ----
            ob = G.f(r.get('oc_bets_pct'))
            if ob is not None and ob >= 50:
                # below 50 the public is the OTHER side, which is a different
                # row; do not silently grade this one as if it were theirs
                for band in (55, 60, 65, 70):
                    if ob >= band:
                        pub[(f'public tickets >={band}%', mk)].append(rec)

            # ---- SHARP lens: fr/ftp/cz, pick_side IS the sharp side ----
            for tag, mcol, bcol in SHARP_FEEDS:
                mv = G.f(r.get(mcol))
                if mv is None:
                    continue
                shp[(f'{tag}: sharp side, any handle', mk)].append(rec)
                lab = ('sharp handle <25% (contrarian)' if mv < 25 else
                       'sharp handle 25-50%' if mv < 50 else
                       'sharp handle 50-75%' if mv < 75 else
                       'sharp handle >=75% (with public)')
                shp[(f'{tag}: {lab}', mk)].append(rec)
                bv = G.f(r.get(bcol))
                if bv is not None and mv - bv >= 10:
                    shp[(f'{tag}: handle-tickets >=10pp', mk)].append(rec)

        print(f'\n\n{"#" * 104}\n### {sport}   spread sign: {note}'
              f'\n###   unresolved: {dict(miss)}\n{"#" * 104}')
        report(f'{sport} - PUBLIC lens (oddscrowd; grading the PUBLIC side)', pub)
        report(f'{sport} - SHARP lens (fr/ftp/cz; grading the SHARP side)', shp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
