"""What actually feeds the picks, per sport — and how much of it is NULL?

ANDY 2026-10-12: "i want to look into data feeding the picks acrsoss the app
in every sport, are we getting enough externals, are ther eany null columns
in model or engine calculations, not juts these other basic questions that
question the data and the engine processes".

WHY THIS IS NOT audit_dead_signals. That tool answers a binary: can this
signal fire at all? It flags a column only at >=99% NULL. But a column that
is 85% NULL does not read as broken anywhere — the signal fires, the
registry shows a record, and nobody notices it is being decided on 15% of
games. That middle band is invisible today and it is where this looks.

Measured examples that motivated it (2026-10-12):
    nfl_game_context.home_qb_career_h_r_delta_pp   15/316   4.7%
        Written today to fix four signals that had never fired. The
        enrichment reached 15 rows, so they still effectively cannot fire —
        and nothing reports that, because the column is no longer "absent".
    ncaaf_game_context.home_last_result_ats        64/536  11.9%
    nfl_game_context.features_computed_at          COLUMN MISSING
        Migration 20261012c was written and never applied.

FOUR SECTIONS
  1. SIGNAL INPUTS. Every column named by an enabled signal's
     condition_expr, with its populated rate. Graded DEAD (>=99% null),
     CRIPPLED (50-99%), THIN (20-50%), OK. A signal is only as good as the
     thinnest column it reads, so each signal is also scored by its worst
     input.
  2. EXTERNALS. Per source per sport: how many picks, how fresh, and
     whether the source has gone silent. A source that stopped three weeks
     ago still shows a lifetime record.
  3. GRADING INPUTS. close_spread / close_total / moneyline / scores on the
     results tables, because an ungradeable game is a pick that never
     enters the record — and a market with no stored price cannot have an
     honest ROI.
  4. PROP INPUTS. projection / final_value / conviction coverage, and the
     share flagged proj_unfed.

WRITES NOTHING.

CLI
    python audit_data_feeds.py
    python audit_data_feeds.py --sport NFL
    python audit_data_feeds.py --section externals
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context',
       'NBA': 'nba_game_context', 'NCAAB': 'ncaab_game_context'}
RES = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
       'MLB': 'mlb_game_results', 'NHL': 'nhl_game_results',
       'NBA': 'nba_game_results', 'NCAAB': 'ncaab_game_results'}
PROPS = {'NFL': 'nfl_pipeline_props', 'MLB': 'mlb_pipeline_props',
         'NHL': 'nhl_pipeline_props', 'NBA': 'nba_pipeline_props'}

# ══ THE SAME FACT HAS A DIFFERENT COLUMN NAME IN EVERY SPORT ══
# This is not cosmetic. Any cross-sport tool that assumes one name reads a
# 400 as "no data" — it happened twice inside this file alone (NHL results
# reported as empty when they held 172 rows). Discovered names, 2026-10-12:
#
#   moneyline   MLB   home_ml_close / away_ml_close
#               else  close_home_ml / close_away_ml
#   spread      NFL/NCAAF/MLB  close_spread
#               NHL            close_puckline
#
# AND THE PRICE COVERAGE IS LOPSIDED. NHL is the only sport storing a price
# for the spread and the total (close_puckline_home_price /
# close_puckline_away_price / close_total_over_price /
# close_total_under_price). NFL, NCAAF and MLB store NO spread or total
# price at all, which is why their ATS ROI has to be computed at an assumed
# -110 while NHL could use the real number.
ML_COLS = {'MLB': ('home_ml_close', 'away_ml_close')}
ML_DEFAULT = ('close_home_ml', 'close_away_ml')
SPREAD_COLS = {'NHL': 'close_puckline'}
SPREAD_DEFAULT = 'close_spread'
#: sport -> price columns for the spread and total, where they exist at all.
PRICE_COLS = {'NHL': ('close_puckline_home_price', 'close_total_over_price')}

_COL = re.compile(r'\bctx\.([A-Za-z_][A-Za-z0-9_]*)')


def page(table, params, cap=200000):
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
                if r.status_code == 404:
                    return out
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(3)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def grade(pct):
    if pct < 1:
        return 'DEAD'
    if pct < 50:
        return 'CRIPPLED' if pct < 20 else 'THIN'
    return 'OK'


def section_signals(sports):
    print('=' * 78)
    print(' 1 · SIGNAL INPUTS — every column an enabled signal reads')
    print('=' * 78)
    for sport in sports:
        ctx_t = CTX.get(sport)
        if not ctx_t:
            continue
        rows = page(ctx_t, {'select': '*'})
        sigs = page('signal_sources', {
            'select': 'signal_key,class,condition_expr',
            'sport': f'eq.{sport}', 'enabled': 'eq.true'})
        print(f'\n-- {sport}: {len(rows)} context rows · {len(sigs)} enabled '
              f'signals')
        if not rows:
            print('   context table EMPTY — every game-context signal in this '
                  'sport is inert')
            continue
        # ══ PAST AND FUTURE MUST BE SCORED SEPARATELY ══
        # nfl_game_context holds the whole remaining season: 123 past rows
        # and 193 future ones. Gametime inputs CANNOT exist on a future row
        # — weather, the model panel and the Madden delta are pulled near
        # kickoff — so a single denominator makes healthy columns look
        # broken. Measured 2026-10-12: panel_pred_home_pts is 60.2% on past
        # games and 6.7% on future, which reads as 27.5% pooled and is not a
        # defect. A first pass of this tool reported "70 of 123 NFL signals
        # crippled" on the pooled number; that figure was inflated by games
        # that have not happened yet.
        #
        # PAST coverage is what the registry's track record rests on.
        # FUTURE coverage is what today's picks are actually decided on.
        # Both are reported; neither alone is the answer.
        cols = set(rows[0].keys())
        today = time.strftime('%Y-%m-%d')
        past = [r for r in rows if str(r.get('game_date'))[:10] < today]
        fut = [r for r in rows if str(r.get('game_date'))[:10] >= today]
        print(f'   {len(past)} past · {len(fut)} upcoming '
              f'(scored separately — gametime data cannot exist on a future '
              f'row)')
        pop, pop_f = {}, {}
        for c in cols:
            base = past or rows
            pop[c] = sum(1 for r in base if r.get(c) is not None) / len(base) * 100
            pop_f[c] = (sum(1 for r in fut if r.get(c) is not None)
                        / len(fut) * 100) if fut else float('nan')

        # which columns do signals actually read?
        refd = defaultdict(list)
        missing = defaultdict(list)
        for s in sigs:
            cond = s.get('condition_expr') or ''
            for c in sorted(set(_COL.findall(cond))):
                if c in pop:
                    refd[c].append(s['signal_key'])
                else:
                    missing[c].append(s['signal_key'])
        if not refd and not missing:
            print('   no ctx.* columns referenced (all handler/prop scope)')
            continue
        buckets = defaultdict(list)
        for c, keys in refd.items():
            buckets[grade(pop[c])].append((pop[c], c, len(keys)))
        for g in ('DEAD', 'CRIPPLED', 'THIN', 'OK'):
            items = sorted(buckets.get(g) or [])
            if not items:
                continue
            if g == 'OK':
                print(f'   {g:<9} {len(items)} columns')
                continue
            print(f'   {g:<9} {len(items)} columns:')
            for pct, c, nk in items[:14]:
                print(f'       {c[:40]:<42}past {pct:5.1f}%  '
                      f'upcoming {pop_f.get(c, float("nan")):5.1f}%  '
                      f'{nk} signal(s)')
            if len(items) > 14:
                print(f'       … and {len(items) - 14} more')
        if missing:
            print(f'   COLUMN DOES NOT EXIST: {len(missing)}')
            for c, keys in sorted(missing.items())[:10]:
                print(f'       {c[:44]:<46}read by {len(keys)} signal(s)')

        # a signal is only as good as its thinnest input
        worst = []
        for s in sigs:
            cond = s.get('condition_expr') or ''
            cs = [c for c in set(_COL.findall(cond)) if c in pop]
            if not cs:
                continue
            w = min(pop[c] for c in cs)
            if w < 50:
                worst.append((w, s['signal_key']))
        worst.sort()
        if worst:
            print(f'   {len(worst)} signals decided on <50% of PAST games '
                  f'(worst input shown):')
            for w, k in worst[:10]:
                print(f'       {k[:46]:<48}{w:5.1f}%')
            if len(worst) > 10:
                print(f'       … and {len(worst) - 10} more')


def section_externals(sports):
    print('\n' + '=' * 78)
    print(' 2 · EXTERNALS — who is still reporting, and how fresh')
    print('=' * 78)
    # select='*' on purpose. The first version named created_at and market,
    # neither of which exists on external_picks — PostgREST answered 400 and
    # the section printed "empty" for a table holding 13,019 rows. An
    # explicit SELECT turns a schema guess into a silent blank, which is the
    # single most repeated trap in this codebase.
    rows = page('external_picks', {'select': '*'})
    if not rows:
        print('  external_picks unreadable or empty')
        return
    today = time.strftime('%Y-%m-%d')
    by = defaultdict(lambda: {'n': 0, 'last': '', 'first': 'z'})
    graded_n: dict = {}
    for r in rows:
        k = (str(r.get('sport')), str(r.get('source')))
        if r.get('result') is not None:
            graded_n[k] = graded_n.get(k, 0) + 1
        d = str(r.get('game_date') or '')[:10]
        b = by[k]
        b['n'] += 1
        if d > b['last']:
            b['last'] = d
        if d and d < b['first']:
            b['first'] = d
    print(f'  {len(rows)} external picks · {len(by)} (sport, source) pairs\n')
    print(f"  {'sport':<8}{'source':<26}{'picks':>7}{'first':>12}{'last':>12}"
          f"{'age':>6}")
    print('  ' + '-' * 72)
    cur = defaultdict(int)
    for (sport, src), b in sorted(by.items(), key=lambda kv: (kv[0][0], -kv[1]['n'])):
        try:
            age = ((time.mktime(time.strptime(today, '%Y-%m-%d'))
                    - time.mktime(time.strptime(b['last'], '%Y-%m-%d')))
                   / 86400) if b['last'] else 999
        except Exception:                                      # noqa: BLE001
            age = 999
        flag = ''
        if age > 14:
            flag = '  SILENT'
        elif age <= 3:
            cur[sport] += 1
        g = graded_n.get((sport, src), 0)
        print(f"  {sport:<8}{src[:25]:<26}{b['n']:>7}{b['first']:>12}"
              f"{b['last']:>12}{age:>5.0f}d{flag}  graded {g}")
    print('  ' + '-' * 72)
    print('  live sources (reported within 3 days) per sport: '
          + ', '.join(f'{s}={n}' for s, n in sorted(cur.items())) or '  none')
    for sport in sports:
        if sport not in cur:
            print(f'    {sport}: NO live external source')


def section_grading(sports):
    print('\n' + '=' * 78)
    print(' 3 · GRADING INPUTS — can a pick even enter the record?')
    print('=' * 78)
    print(f"\n  {'sport':<8}{'rows':>7}{'scores':>9}{'spread':>9}{'total':>9}"
          f"{'home ml':>9}")
    print('  ' + '-' * 54)
    for sport in sports:
        res_t = RES.get(sport)
        if not res_t:
            continue
        # select='*' on purpose, AND the column list is discovered from the
        # row rather than assumed. The first version named close_total and
        # close_home_ml; NHL carries neither, PostgREST answered 400, and the
        # section printed "no rows" for a table with 172 of them. That is the
        # second time this exact trap fired inside this one file, which is
        # why nothing here names a column it has not seen.
        rows = page(res_t, {'select': '*', 'game_date': 'gte.2026-08-01'})
        if not rows:
            print(f'  {sport:<8}{"(no 2026-08+ rows)":>40}')
            continue
        n = len(rows)
        have = set(rows[0].keys())
        mlc = ML_COLS.get(sport, ML_DEFAULT)
        ml_col = next((c for c in mlc if c in have), None)

        def pc(c):
            if c is None or c not in have:
                return None
            return sum(1 for r in rows if r.get(c) is not None) / n * 100

        sp_col = SPREAD_COLS.get(sport, SPREAD_DEFAULT)
        px = PRICE_COLS.get(sport, (None, None))

        def fmt(v):
            return '  absent' if v is None else f'{v:>6.0f}%'
        print(f'  {sport:<8}{n:>7}{fmt(pc("home_score")):>9}'
              f'{fmt(pc(sp_col)):>9}{fmt(pc("close_total")):>9}'
              f'{fmt(pc(ml_col)):>9}{fmt(pc(px[0])):>12}'
              f'{fmt(pc(px[1])):>12}')
    print('\n  A market with no stored price cannot carry an honest ROI —')
    print('  it gets graded at an assumed -110 or not at all.')


def section_props(sports):
    print('\n' + '=' * 78)
    print(' 4 · PROP INPUTS')
    print('=' * 78)
    print(f"\n  {'sport':<8}{'rows':>8}{'projection':>12}{'final_value':>13}"
          f"{'conviction':>12}")
    print('  ' + '-' * 54)
    for sport in sports:
        t = PROPS.get(sport)
        if not t:
            continue
        rows = page(t, {'select': '*', 'game_date': 'gte.2026-09-01'},
                    cap=20000)
        if not rows:
            print(f'  {sport:<8}{"(no 2026-09+ rows)":>44}')
            continue
        n = len(rows)
        have = rows[0].keys()

        def pc(c):
            if c not in have:
                return None
            return sum(1 for r in rows if r.get(c) is not None) / n * 100
        def fmt(v):
            return '   absent' if v is None else f'{v:>10.0f}%'
        print(f'  {sport:<8}{n:>8}{fmt(pc("projection")):>12}'
              f'{fmt(pc("final_value")):>13}{fmt(pc("conviction")):>12}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--section', default='all',
                    choices=('all', 'signals', 'externals', 'grading',
                             'props'))
    a = ap.parse_args()
    sports = ([a.sport.upper()] if a.sport else
              ['NFL', 'NCAAF', 'MLB', 'NHL', 'NBA', 'NCAAB'])
    print(f'=== data feeding the picks · {", ".join(sports)} · read-only ===')
    if a.section in ('all', 'signals'):
        section_signals(sports)
    if a.section in ('all', 'externals'):
        section_externals(sports)
    if a.section in ('all', 'grading'):
        section_grading(sports)
    if a.section in ('all', 'props'):
        section_props(sports)
    return 0


if __name__ == '__main__':
    sys.exit(main())
