"""Measure EVERY signal in signal_sources, and report THREE states.

WHY THIS EXISTS
---------------
Andy 2026-11-09: *"any theory or innovative idea i have ever proposed has
been shot down due to laziness or incorrect backtesting."* Audited, and he
was substantially right -- but the mechanism was not bad backtests, it was
NON-MEASUREMENT plus verdicts issued anyway:

  * 50 of 55 "noise" / "no edge" claims in docs/daily quote NO sample size.
  * Every "no edge" verdict re-checked had a CI CONTAINING break-even
    (NBA gap 48.11% n=106 -> CI 38.8-57.5).
  * 660 of 860 signals had never been measured at all; of the 200
    measured, 148 sat on n<30. Only 52 of 860 (6%) had n>=30.
  * 273 signals disabled -- 230 of them on n<30 or never measured.
  * 24 signals whose origin names a user audit: all enabled, 0 measured.

The asymmetry was the bug. "Edge" required n>=30 and a control; "no edge"
accepted any n at all. So this tool refuses to collapse to two states:

    VALIDATED       CI lower bound beats the bar
    ANTI_VALIDATED  CI upper bound is below the bar (fade candidate)
    UNDETERMINED    n < MIN_N, or the CI straddles the bar

UNDETERMINED is a real answer and is always reported, never silently
dropped. A signal with no attribution rows is UNDETERMINED (never
measured) -- that is a backlog item, NOT a negative verdict.

HOW A SIGNAL IS GRADED
----------------------
`signal_attribution.signal_was_right` grades the side THE SIGNAL named,
not the side we published. Verified 2026-11-09: it mirrors our own pick
result 98.7% when the signal agreed with us and only 2.4% when it
disagreed -- i.e. it is independently correct, which is what a signal
needs. (`result` on the same row IS our pick's outcome; averaging THAT
punishes a signal for disagreeing -- see
project_signal_hit_rate_was_our_pick_rate.)

Every signal is also quoted against a DIRECTION-ONLY CONTROL: the blanket
rate of always taking that signal's own modal side in that sport+market.
A home-leaning signal in a sport where home covers 55% must beat 55%, not
52.38%, or it is a home bias with extra steps.

CONVENTIONS, measured not assumed (printed on every run)
--------------------------------------------------------
close_line is the HOME line, negative when home is favoured:
    home_covered  <=>  actual_margin + close_line > 0
Measured 2026-11-09: reproduces our own graded rl results on
2943/3234 = 91.0%. The run prints this every time; if it drops below 85%
the orientation is no longer established and the numbers are unproven.

Break-even is market-specific. 52.38% is the -110 spread/total figure and
it UNDERSTATES a moneyline, where books average -120..-203 (BE 56-58%).
See feedback_flat_110_inflates_every_ml_figure.

USAGE
    python validate_signals.py                      # all sports, report
    python validate_signals.py --sport NBA
    python validate_signals.py --min-n 50
    python validate_signals.py --only-undetermined  # the backlog
    python validate_signals.py --apply              # write back

--apply writes hit_rate_pct/sample_n for VALIDATED and ANTI_VALIDATED
signals ONLY. It never writes an UNDETERMINED signal, because an
unmeasured signal must not outweigh a measured one
(feedback_unmeasured_signal_must_not_outweigh_measured).
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
SB = os.getenv('SUPABASE_URL')
KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
HDR = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

MIN_N = 30                 # Andy's standing gate: every % shows n, gate n>=30
BE_SPREAD = 52.38          # -110
BE_ML_FLOOR = 56.0         # ML books average far worse than -110
Z = 1.96


def page(table, **params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=HDR, params=p,
                         timeout=180)
        if r.status_code != 200:
            raise SystemExit(f'!! {table} {r.status_code} {r.text[:300]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def wilson(w, n):
    """95% CI for a proportion. No proportion is quoted without one."""
    if not n:
        return 0.0, 100.0
    p = w / n
    d = 1 + Z * Z / n
    c = p + Z * Z / (2 * n)
    m = Z * ((p * (1 - p) / n + Z * Z / (4 * n * n)) ** .5)
    return 100 * (c - m) / d, 100 * (c + m) / d


def side_covered(row, side):
    """Did `side` win ITS OWN BET on this game? None if unprovable/push.

    The market decides the question, and conflating the two is a documented
    trap (feedback_ml_vs_rl_conflation):

      ml            did they WIN                  margin > 0
      rl / spread   did they beat the NUMBER      margin + close_line > 0
      total         did the game go over/under    actual_total vs close_line

    The HOME-line orientation is measured, not assumed -- see
    convention_check(), which prints the reproduction rate every run.

    Grading an ml row against the spread is what the first version of this
    file did. VERIFIED 2026-11-09 that the stored target does it right:
    `signal_was_right` on ml rows matches WIN on 1268/1268 = 100.0% and the
    spread test on only 81.7%. The control below has to use the same basis
    as the signal's own hit rate or the comparison is meaningless.
    """
    side = str(side or '').upper()
    market = str(row.get('pick_market') or '').lower()
    cl = f(row.get('close_line'))
    if side in ('HOME', 'AWAY'):
        m = f(row.get('actual_margin'))
        if m is None:
            return None
        if market == 'ml':
            got = m                          # moneyline: just win the game
        else:
            if cl is None:
                return None
            got = m + cl
        if abs(got) < 1e-9:
            return None                      # push / tie
        return (got > 0) if side == 'HOME' else (got < 0)
    if side in ('OVER', 'UNDER'):
        t = f(row.get('actual_total'))
        if t is None or cl is None:
            return None
        if abs(t - cl) < 1e-9:
            return None                      # push
        return (t > cl) if side == 'OVER' else (t < cl)
    return None


def benchmark(market, control):
    """The bar a signal must clear: its own direction-only control, but
    never below the market's break-even."""
    be = BE_ML_FLOOR if str(market or '').lower() == 'ml' else BE_SPREAD
    return max(be, control if control is not None else be), be


def verdict_for(n, hits, bar, min_n):
    if n < min_n:
        return 'UNDETERMINED', f'n={n} < {min_n}'
    lo, hi = wilson(hits, n)
    if lo > bar:
        return 'VALIDATED', f'CI low {lo:.1f} > bar {bar:.1f}'
    if hi < bar:
        return 'ANTI_VALIDATED', f'CI high {hi:.1f} < bar {bar:.1f}'
    return 'UNDETERMINED', f'CI {lo:.1f}-{hi:.1f} straddles bar {bar:.1f}'


def convention_check(rows):
    """Print the reproduction rate so the orientation is visible every run."""
    ok = tot = 0
    for r in rows:
        if str(r.get('pick_market')) != 'rl':
            continue
        res = str(r.get('result') or '').upper()
        ps = str(r.get('pick_side') or '').upper()
        if res not in ('W', 'L', 'WIN', 'LOSS') or ps not in ('HOME', 'AWAY'):
            continue
        cov = side_covered(r, ps)
        if cov is None:
            continue
        tot += 1
        ok += (cov == (res in ('W', 'WIN')))
    pct = 100.0 * ok / tot if tot else 0.0
    print(f'convention check: close_line as the HOME line reproduces our own '
          f'graded rl results on {ok}/{tot} = {pct:.1f}%')
    if pct < 85:
        print('  !! below 85% -- the orientation is NOT established; '
              'treat every number below as unproven')
    print()


def fmt(x, spec='.1f'):
    return format(x, spec) if x is not None else '-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    ap.add_argument('--min-n', type=int, default=MIN_N)
    ap.add_argument('--only-undetermined', action='store_true')
    ap.add_argument('--only-validated', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--limit-print', type=int, default=60)
    args = ap.parse_args()

    src = page('signal_sources',
               select='signal_key,sport,class,enabled,hit_rate_pct,sample_n,'
                      'origin,market_scope')
    att = page('signal_attribution',
               select='signal_key,sport,signal_side,signal_was_right,'
                      'pick_market,pick_side,result,actual_margin,'
                      'actual_total,close_line,game_date')
    if args.sport:
        sp = args.sport.upper()
        src = [r for r in src if str(r.get('sport') or '').upper() == sp]
        att = [r for r in att if str(r.get('sport') or '').upper() == sp]

    convention_check(att)

    # direction-only controls: blanket rate of taking SIDE in (sport, market)
    ctl = defaultdict(lambda: [0, 0])
    for r in att:
        for side in ('HOME', 'AWAY', 'OVER', 'UNDER'):
            cov = side_covered(r, side)
            if cov is None:
                continue
            k = (str(r.get('sport')), str(r.get('pick_market')), side)
            ctl[k][1] += 1
            ctl[k][0] += bool(cov)

    # Group by (signal_key, SPORT). 47 keys carry one signal_sources row per
    # sport and 39 appear under several sports in attribution, so keying on
    # signal_key alone hands every sport the POOLED total: the first run of
    # this file printed `home_ats_hot_at_home` as n=60 / 61.7% identically
    # for all six sports, which is the pooling-across-scales trap
    # (feedback_pooled_slope_across_scales_is_fake).
    by_sig = defaultdict(list)
    for r in att:
        if r.get('signal_was_right') is None:
            continue
        side = str(r.get('signal_side') or '').upper()
        if side in ('', 'NONE', 'NEUTRAL'):
            continue
        by_sig[(r['signal_key'], str(r.get('sport') or '').upper())].append(r)

    rows = []
    for s in src:
        key = s['signal_key']
        reg_sport = str(s.get('sport') or '').upper()
        if reg_sport:
            rs = by_sig.get((key, reg_sport), [])
        else:
            # no sport on the registry row: pool, and SAY SO in the output
            rs = [r for (k, _sp), v in by_sig.items() if k == key for r in v]
        n = len(rs)
        hits = sum(1 for r in rs if bool(r['signal_was_right']))
        side = (Counter(str(r['signal_side']).upper()
                        for r in rs).most_common(1)[0][0] if rs else '-')
        market = (Counter(str(r['pick_market']) for r in rs)
                  .most_common(1)[0][0] if rs
                  else (s.get('market_scope') or '-'))
        sport = s.get('sport') or (rs[0]['sport'] if rs else '-')
        c = ctl.get((str(sport), str(market), side))
        control = (100.0 * c[0] / c[1]) if c and c[1] >= args.min_n else None
        bar, be = benchmark(market, control)
        v, why = verdict_for(n, hits, bar, args.min_n)
        if n == 0:
            why = 'NEVER MEASURED - no attribution rows'
        lo, hi = wilson(hits, n) if n else (None, None)
        rows.append({
            'key': key, 'sport': sport, 'class': s.get('class'),
            'enabled': s.get('enabled'), 'origin': s.get('origin'),
            'market': market, 'side': side, 'n': n, 'hits': hits,
            'pct': (100.0 * hits / n) if n else None,
            'lo': lo, 'hi': hi, 'control': control, 'bar': bar, 'be': be,
            'verdict': v, 'why': why,
        })

    tally = Counter(r['verdict'] for r in rows)
    never = sum(1 for r in rows if r['n'] == 0)
    print('=' * 104)
    label = f'  (sport={args.sport.upper()})' if args.sport else ''
    print(f'{len(rows)} signals in signal_sources{label}')
    print('=' * 104)
    for v in ('VALIDATED', 'ANTI_VALIDATED', 'UNDETERMINED'):
        print(f'  {v:<16} {tally.get(v, 0):>4}')
    print(f'  {"":<16}      of the UNDETERMINED, {never} have NEVER been '
          f'measured (no attribution rows at all)')
    print()

    if args.only_undetermined:
        show = [r for r in rows if r['verdict'] == 'UNDETERMINED' and r['n']]
    elif args.only_validated:
        show = [r for r in rows
                if r['verdict'] in ('VALIDATED', 'ANTI_VALIDATED')]
    else:
        show = [r for r in rows if r['n'] >= args.min_n]
    show.sort(key=lambda r: -(r['n'] or 0))

    print(f'{"signal":<40} {"sport":<6} {"mkt":<5} {"sd":<5} {"n":>4} '
          f'{"hit%":>6} {"95% CI":>13} {"ctl":>6} {"bar":>6}  verdict')
    print('-' * 112)
    for r in show[:args.limit_print]:
        ci = f'{r["lo"]:.1f}-{r["hi"]:.1f}' if r['n'] else '-'
        print(f'{r["key"][:40]:<40} {str(r["sport"])[:6]:<6} '
              f'{str(r["market"])[:5]:<5} {r["side"][:5]:<5} {r["n"]:>4} '
              f'{fmt(r["pct"]):>6} {ci:>13} {fmt(r["control"]):>6} '
              f'{r["bar"]:>6.1f}  {r["verdict"]}')
    if len(show) > args.limit_print:
        print(f'... +{len(show) - args.limit_print} more')
    print()

    # the backlog, stated as a backlog and not as a negative verdict
    print('NEVER MEASURED, by sport (UNDETERMINED, not "no edge"):')
    nm = Counter(str(r['sport']) for r in rows if r['n'] == 0)
    for sp, c in nm.most_common():
        print(f'   {sp:<8} {c:>4}')
    uo = [r for r in rows if r['n'] == 0 and 'user' in str(r['origin']).lower()]
    if uo:
        live = sum(1 for r in uo if str(r['enabled']).lower() == 'true')
        print(f'   of which {len(uo)} came from a USER AUDIT and {live} are '
              f'still enabled and firing live')

    if args.apply:
        wrote, failed = 0, []
        for r in rows:
            if r['verdict'] == 'UNDETERMINED':
                continue
            resp = requests.patch(
                f'{SB}/rest/v1/signal_sources',
                headers={**HDR, 'Content-Type': 'application/json',
                         'Prefer': 'return=representation'},
                params={'signal_key': f'eq.{r["key"]}'},
                json={'hit_rate_pct': round(r['pct'], 2), 'sample_n': r['n']},
                timeout=60)
            # a 200 with [] is a REFUSED write, not a success (a 204 is not a
            # write either) -- confirm by readback, never by status alone
            body = resp.json() if resp.status_code == 200 else None
            if resp.status_code == 200 and body:
                wrote += 1
            else:
                failed.append((r['key'], resp.status_code,
                               resp.text[:80]))
        print(f'\n--apply: wrote {wrote} measured signals '
              f'(UNDETERMINED left untouched)')
        for k, sc, tx in failed[:10]:
            print(f'   !! {k} {sc} {tx}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
