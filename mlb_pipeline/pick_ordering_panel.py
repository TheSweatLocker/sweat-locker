"""Can ANYTHING order our own picks better than conviction does?

ANDY 2026-10-09, after the TB case:
    2026-10-08  TB 24 @ DAL 16  TB +8.5  WON by 16.5  tier=COVERAGE conv=45
    2026-09-20  CLE 23 @ TB 19  TB ML    LOST         tier=PRIME    conv=72
"was correct on TB +8.5 yesterday just the conviction was off."

He is right, and it reframes the problem. The engine's SIDE SELECTION found a
16.5-point winner and ranked it 45 while ranking a loser 72. Conviction
correlates with winning at r~+0.03 in football — it is not predicting, it is
labelling. So the question is not "find a new signal" (SOR measured null on
11,856 games; money flow turned out to be a one-feed artefact). The question
is whether anything ALREADY INSIDE each pick orders them better than the
number we currently print.

THE BAR IS LOW AND EXPLICIT: beat r~+0.03, and clear 52.38%.

WHY THIS CAN BE DONE LEAK-FREE, which the earlier attempts could not
jerry_reads.input_snapshot is the FROZEN pre-game state, written at
generated_at (typically the night before kickoff) and never recomputed. That
matters enormously: ncaaf_game_context is upserted in place, so its
projections for a past game may have been rebuilt from post-game stats, and
regressing those on the outcome reads the outcome
(project_rolling_stats_leak_trap_929). The snapshot cannot have that problem.
Everything measured here comes out of that frozen blob:

    efficiency.{home,away}.{off_epa_pp, def_epa_pp, sp_overall}
    market.{spread, open_spread, total, open_total}   -> line movement
    primary_play.{tier, conviction, _engine, _pre_lr} -> internal disagreement
    sweat.{tier, score}

CANDIDATE ORDERINGS, each with a reason to exist
  conviction            the incumbent. Printed first so everything else is
                        measured against it rather than against nothing.
  line movement         did the market move TOWARD our side or away from it
                        between open and close? Moving against us means the
                        money disagreed with us after we committed.
  stat differential     frozen net efficiency (own offence minus own defence
                        allowed), home vs away, pointed at our side.
  sp_overall gap        the same question through SP+ instead of EPA.
  model vs market       how far our number sat from the book's. Large gaps are
                        either edge or error, and which one is the question.
  LR override           primary_play._pre_lr records what ensemble_v2 said
                        BEFORE the LR layer overrode it. Where the two
                        disagree, one of them is wrong — and
                        project_jerry_override_costs_the_edge_1007 measured
                        the models at 58% against what actually got published.

SAMPLE, stated up front because it bounds everything
NCAAF has 261 graded reads carrying a snapshot, 194 of them spreads, with
efficiency and market frozen on 191. NFL carries efficiency on ZERO rows, so
NFL cannot participate beyond conviction and _pre_lr (n=16) — this is an
NCAAF test, and saying otherwise would be inventing a sample. At n~191 the 2SE
band is about 7pp, so this can detect a real ordering effect and cannot
resolve a subtle one.

NOTHING IS FITTED — bucketed measurement with 2SE bands, so there is no
parameter to overfit. Tests are counted against the chance expectation.

WRITES NOTHING.

CLI
    python pick_ordering_panel.py
    python pick_ordering_panel.py --sport NCAAF --market rl --min-n 40
"""
from __future__ import annotations

import argparse
import collections
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
#: jerry_reads.result is TITLE case ('Win'/'Loss'), not upper — matching on
#: 'WIN' silently yields zero rows (feedback_result_column_is_title_case).
WIN = {'win', 'won', 'w'}
LOSS = {'loss', 'lost', 'l'}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
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


def load(sport, market):
    rows = _page('jerry_reads',
                 {'select': 'game_date,game_id,call_market,call_side,'
                            'conviction,result,input_snapshot,generated_at',
                  'sport': f'eq.{sport}'})
    out = []
    for x in rows:
        res = str(x.get('result') or '').lower()
        if res not in WIN and res not in LOSS:
            continue
        if market != 'all' and str(x.get('call_market')) != market:
            continue
        snap = x.get('input_snapshot')
        if not isinstance(snap, dict):
            continue
        pp = snap.get('primary_play') or {}
        eff = snap.get('efficiency') or {}
        mk = snap.get('market') or {}
        side = str(pp.get('side') or x.get('call_side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            continue
        g = {
            'date': str(x.get('game_date') or '')[:10],
            'won': 1 if res in WIN else 0,
            'side': side,
            'conv': _f(x.get('conviction')) or _f(pp.get('conviction')),
            'tier': str(pp.get('tier') or ''),
            'engine': str(pp.get('_engine') or ''),
            'pre_lr': pp.get('_pre_lr') if isinstance(pp.get('_pre_lr'),
                                                      dict) else None,
            'line': _f(pp.get('line')),
            'spread': _f(mk.get('spread')),
            'open_spread': _f(mk.get('open_spread')),
        }
        h, aw = eff.get('home') or {}, eff.get('away') or {}
        for nm, src in (('h', h), ('a', aw)):
            g[f'{nm}_off'] = _f(src.get('off_epa_pp'))
            g[f'{nm}_def'] = _f(src.get('def_epa_pp'))
            g[f'{nm}_sp'] = _f(src.get('sp_overall'))
        out.append(g)
    out.sort(key=lambda z: z['date'])
    return out


def rate(rows):
    if not rows:
        return None
    p = statistics.fmean([r['won'] for r in rows]) * 100
    se = (0.5 / len(rows) ** 0.5) * 100
    return p, len(rows), 2 * se


def show(label, rows, min_n, tests, note=''):
    r = rate(rows)
    if r is None or r[1] < min_n:
        print(f'      {label:<34}n={len(rows)}  under the n>={min_n} floor')
        return
    p, n, band = r
    edge = p - BREAKEVEN
    tag = ('ORDERS (>2SE over breakeven)' if edge > band else
           'suggestive' if edge > band / 2 else '')
    print(f'      {label:<34}{p:6.2f}%  n={n:<4} +/-{band:4.2f}pp  '
          f'vs 52.38 {edge:+6.2f}  {tag}{note}')
    tests.append((label, p, n, band, edge, tag))


def corr_with_win(rows, field):
    pts = [(r[field], r['won']) for r in rows if r.get(field) is not None]
    if len(pts) < 30:
        return None, len(pts)
    xs = [p[0] for p in pts]; ys = [float(p[1]) for p in pts]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, len(pts)
    try:
        return statistics.correlation(xs, ys), len(pts)
    except statistics.StatisticsError:
        return None, len(pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--market', default='rl')
    ap.add_argument('--min-n', type=int, default=40, dest='min_n')
    a = ap.parse_args()
    games = load(a.sport, a.market)
    print(f'=== {a.sport} PICK ORDERING · market={a.market} · '
          f'{len(games)} graded reads with a frozen snapshot')
    if not games:
        print('    nothing to measure.')
        return 0
    print(f'    {games[0]["date"]}..{games[-1]["date"]}')
    base = statistics.fmean([g['won'] for g in games]) * 100
    print(f'    base win rate {base:.2f}%  ·  breakeven {BREAKEVEN}%')
    print('    Everything below is leak-free: every input comes from the')
    print('    FROZEN input_snapshot written before kickoff, never from the')
    print('    live context table, which is upserted in place.')

    tests = []

    # ── THE INCUMBENT, first, so the rest is measured against it ─────────
    print('\n' + '-' * 74)
    print('  0. THE INCUMBENT — conviction. This is the number to beat.')
    c, n = corr_with_win(games, 'conv')
    print(f'\n      correlation(conviction, win) = '
          f'{"n/a" if c is None else f"{c:+.4f}"}  n={n}')
    print()
    for lo, hi in ((0, 55), (55, 65), (65, 75), (75, 101)):
        show(f'conviction {lo}-{hi - 1}',
             [g for g in games if g['conv'] is not None
              and lo <= g['conv'] < hi], a.min_n, tests)

    # ── LINE MOVEMENT ────────────────────────────────────────────────────
    print('\n' + '-' * 74)
    print('  1. LINE MOVEMENT — did the market move toward our side or away?')
    print('     Computed from the FROZEN open_spread and spread, so it is the')
    print('     move as it stood when we committed, not a later revision.')
    print()
    for g in games:
        g['move'] = None
        if g['spread'] is None or g['open_spread'] is None:
            continue
        # NCAAF: negative spread = home favourite. A spread going MORE
        # negative means the market moved toward HOME.
        d = g['spread'] - g['open_spread']
        toward_home = -d
        g['move'] = toward_home if g['side'] == 'HOME' else -toward_home
    mv = [g for g in games if g['move'] is not None]
    c, n = corr_with_win(mv, 'move')
    print(f'      correlation(move toward our side, win) = '
          f'{"n/a" if c is None else f"{c:+.4f}"}  n={n}')
    print()
    show('market moved TOWARD our side', [g for g in mv if g['move'] > 0],
         a.min_n, tests)
    show('market moved AGAINST our side', [g for g in mv if g['move'] < 0],
         a.min_n, tests)
    show('line did not move', [g for g in mv if g['move'] == 0],
         a.min_n, tests)

    # ── FROZEN STAT DIFFERENTIAL ─────────────────────────────────────────
    print('\n' + '-' * 74)
    print('  2. STAT DIFFERENTIAL (frozen) — net EPA, pointed at our side')
    print()
    for g in games:
        g['stat'] = None
        if None in (g['h_off'], g['h_def'], g['a_off'], g['a_def']):
            continue
        # def_epa_pp is points allowed per play, so LOWER is better and it is
        # subtracted from the side's own offence.
        net = (g['h_off'] - g['h_def']) - (g['a_off'] - g['a_def'])
        g['stat'] = net if g['side'] == 'HOME' else -net
    st = [g for g in games if g['stat'] is not None]
    c, n = corr_with_win(st, 'stat')
    print(f'      correlation(stat edge for our side, win) = '
          f'{"n/a" if c is None else f"{c:+.4f}"}  n={n}')
    print()
    show('stat differential FAVOURS our side', [g for g in st if g['stat'] > 0],
         a.min_n, tests)
    show('stat differential AGAINST our side', [g for g in st if g['stat'] < 0],
         a.min_n, tests)

    # ── SP+ GAP ──────────────────────────────────────────────────────────
    print('\n' + '-' * 74)
    print('  3. SP+ GAP (frozen) — the same question through SP+')
    print()
    for g in games:
        g['spgap'] = None
        if g['h_sp'] is None or g['a_sp'] is None:
            continue
        d = g['h_sp'] - g['a_sp']
        g['spgap'] = d if g['side'] == 'HOME' else -d
    sp = [g for g in games if g['spgap'] is not None]
    c, n = corr_with_win(sp, 'spgap')
    print(f'      correlation(SP+ gap for our side, win) = '
          f'{"n/a" if c is None else f"{c:+.4f}"}  n={n}')
    print()
    show('SP+ favours our side', [g for g in sp if g['spgap'] > 0],
         a.min_n, tests)
    show('SP+ against our side', [g for g in sp if g['spgap'] < 0],
         a.min_n, tests)

    # ── THE LR OVERRIDE ──────────────────────────────────────────────────
    print('\n' + '-' * 74)
    print('  4. THE LR OVERRIDE — where two of our own engines disagreed')
    print('     _pre_lr is what ensemble_v2 said BEFORE the LR layer changed')
    print('     it. project_jerry_override_costs_the_edge_1007 measured the')
    print('     models at 58% against what actually shipped.')
    print()
    pre = [g for g in games if g['pre_lr']]
    print(f'      reads carrying _pre_lr: {len(pre)}')
    if pre:
        same_side = [g for g in pre
                     if str(g['pre_lr'].get('side', '')).upper() == g['side']]
        diff_side = [g for g in pre
                     if str(g['pre_lr'].get('side', '')).upper() != g['side']]
        show('override KEPT the side', same_side, a.min_n, tests)
        show('override FLIPPED the side', diff_side, a.min_n, tests)
        for e in sorted({g['engine'] for g in games if g['engine']}):
            show(f'engine={e}', [g for g in games if g['engine'] == e],
                 a.min_n, tests)

    # ── TIER, as shipped ─────────────────────────────────────────────────
    print('\n' + '-' * 74)
    print('  5. TIER as shipped — does the published label order anything?')
    print()
    for t in sorted({g['tier'] for g in games if g['tier']}):
        show(f'tier={t}', [g for g in games if g['tier'] == t],
             a.min_n, tests)

    # ── VERDICT ──────────────────────────────────────────────────────────
    print('\n' + '=' * 74)
    hits = [t for t in tests if t[5].startswith('ORDERS')]
    print(f'  {len(tests)} buckets measured · {len(hits)} cleared breakeven '
          f'by >2SE')
    print(f'  Chance alone would produce about {len(tests) * 0.025:.1f}.')
    print('\n  CORRELATIONS WITH WINNING (the incumbent is conviction):')
    for fld, nm in (('conv', 'conviction (incumbent)'),
                    ('move', 'line movement toward us'),
                    ('stat', 'stat differential'), ('spgap', 'SP+ gap')):
        c, n = corr_with_win([g for g in games if g.get(fld) is not None], fld)
        print(f'    {nm:<30}{"n/a" if c is None else f"{c:+.4f}"}  n={n}')
    if hits:
        print('\n  CLEARED:')
        for lbl, p, n, band, edge, _t in sorted(hits, key=lambda z: -z[4]):
            print(f'    {lbl:<34}{p:6.2f}%  n={n:<4} edge {edge:+.2f}pp')
    else:
        print('\n  NOTHING cleared. At n~{} the 2SE band is ~{:.1f}pp, so a'
              .format(len(games), 2 * (0.5 / max(1, len(games)) ** 0.5) * 100))
        print('  real ordering effect would have shown and a subtle one would')
        print('  not. That bounds the claim rather than settling it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
