#!/usr/bin/env python3
"""Catch implausible market values in every context and results table.

══ 2026-10-14 · WHY A WATCHDOG RATHER THAN A FIX AT THE WRITER ══
NFL `nfl_game_context` held `close_total=96.5` and `close_spread=-21.5` for
CHI @ CAR 2026-09-13, while `nfl_game_results` correctly held 47.5 and -3.0.
That game finished 59-37: 96 points, margin 22. So the stored "closing line"
was the final score, offset by half a point, on both markets at once.

I could not identify the writer. Static search found nothing that derives a
line from scores, and `line_history` has no quotes for that fixture to
compare against, so "a bad odds-feed row" remains a live alternative to "a
results-to-context writer". Guessing at a mechanism and patching the wrong
script would leave the real one running.

So this detects the CONDITION instead of the cause, which works regardless of
which is true, and repairs from the table that is right. It is the same
argument `backfill_game_grades` makes for its sanity bands:

    "A wrong grade is worse than a missing one because it looks like
     evidence."

A fabricated line is worse than a missing one for exactly that reason — and
`nhl_game_results.close_puckline` already proved it, having held moneyline
prices on 1,120 rows while producing a 52.5/47.5 home-away split that looked
unremarkable enough that nobody questioned it.

BANDS. Wide on purpose. The job is to catch a value that is not a line at all
(a price, a score, a total in a spread column), not to second-guess a real
outlier. NHL/NBA/NCAAB match the bands already in backfill_game_grades so the
two cannot drift apart.

REPAIR. `--repair` copies a plausible value from the sport's RESULTS row onto
an implausible CONTEXT row for the same fixture. Never the other direction:
results is the graded source of record. `open_*` columns are reported but NOT
repaired — the `freeze_opening_lines` trigger silently discards any UPDATE to
them (and still returns 200), so a corrupt opener needs the migration in
20261014a_opening_line_repair.sql first.

EXIT CODE. 0 unless --strict, so adding this to a workflow cannot start
failing the pipeline on a pre-existing condition. With --strict it exits 1
when anything implausible survives, which is the right mode once the backlog
is clean.

USAGE
    python watchdog_market_sanity.py
    python watchdog_market_sanity.py --sport NFL
    python watchdog_market_sanity.py --repair
    python watchdog_market_sanity.py --strict        # for CI, once clean
"""
from __future__ import annotations

import argparse
import collections
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

#: spread_sane = largest plausible ABSOLUTE handicap. total_range = (lo, hi).
#: NHL / NBA / NCAAB deliberately mirror backfill_game_grades.SPORTS.
SPORTS = {
    'NFL':   {'ctx': 'nfl_game_context',   'res': 'nfl_game_results',
              'spread_col': 'close_spread', 'spread_sane': 30.0,
              'total_range': (28.0, 70.0)},
    'NCAAF': {'ctx': 'ncaaf_game_context', 'res': 'ncaaf_game_results',
              # FCS cupcakes really do close at -45, so this band is wide.
              'spread_col': 'close_spread', 'spread_sane': 60.0,
              'total_range': (25.0, 95.0)},
    # MLB: the run line is ±1.5 on 2,130 of 2,155 populated rows and ±2.5 on
    # 8 more, so 3.5 is already generous. Totals reach 15.5 at Coors, hence the
    # wide ceiling — the point is to catch values that are not full-game
    # totals at all. The low end matters: MLB also stores `f5_total_line`
    # (first five innings, ~3.5-5.5), and the 1.5/2.5/3.5/4.5 values found in
    # close_total look like an F5 line written into the full-game column.
    'MLB':   {'ctx': 'mlb_game_context',   'res': 'mlb_game_results',
              'spread_col': 'close_spread', 'spread_sane': 3.5,
              'total_range': (5.5, 16.0)},
    'NHL':   {'ctx': 'nhl_game_context',   'res': 'nhl_game_results',
              'spread_col': 'close_puckline', 'spread_sane': 3.0,
              'total_range': (3.0, 10.0)},
    'NBA':   {'ctx': 'nba_game_context',   'res': 'nba_game_results',
              'spread_col': 'close_spread', 'spread_sane': 40.0,
              'total_range': (150.0, 300.0)},
    'NCAAB': {'ctx': 'ncaab_game_context', 'res': 'ncaab_game_results',
              'spread_col': 'close_spread', 'spread_sane': 60.0,
              'total_range': (100.0, 200.0)},
}


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            # A missing table is not a failure here (NCAAB is empty out of
            # season); a real error still prints.
            if r.status_code != 404:
                print(f'  ! {table} {r.status_code} {r.text[:140]}')
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bad_fields(row: dict, cfg: dict) -> list[tuple]:
    """-> [(column, value, why)] for every implausible market value."""
    out = []
    lo, hi = cfg['total_range']
    sane = cfg['spread_sane']
    # dict.fromkeys to DEDUPE while keeping order: for every sport except NHL
    # `spread_col` IS 'close_spread', and listing both reported each hit twice.
    for col in dict.fromkeys([cfg['spread_col'], 'close_spread',
                              'open_spread', 'open_puckline']):
        v = _f(row.get(col))
        if v is not None and abs(v) > sane:
            out.append((col, v, f'|{v}| > {sane} — not a handicap'))
    for col in ('close_total', 'open_total'):
        v = _f(row.get(col))
        if v is not None and not (lo <= v <= hi):
            out.append((col, v, f'outside [{lo}, {hi}]'))
    return out


def run(sports: list[str], repair: bool,
        null_unfixable: bool = False) -> int:
    total_bad = 0
    unrepaired = 0
    for sport in sports:
        cfg = SPORTS[sport]
        ctx = page(cfg['ctx'], {'select': '*'})
        res = page(cfg['res'], {'select': '*'})
        if not ctx and not res:
            print(f'\n=== {sport}: both tables empty — skipped')
            continue
        by_fixture = {}
        for r in res:
            k = (str(r.get('game_date'))[:10], str(r.get('home_team')),
                 str(r.get('away_team')))
            by_fixture[k] = r

        print(f'\n=== {sport}: ctx={len(ctx)} res={len(res)}  '
              f'spread_sane={cfg["spread_sane"]} total={cfg["total_range"]}')
        found = collections.Counter()
        for label, rows in (('results', res), ('context', ctx)):
            for row in rows:
                bad = bad_fields(row, cfg)
                if not bad:
                    continue
                total_bad += 1
                found[label] += 1
                tag = (f"{str(row.get('game_date'))[:10]} "
                       f"{row.get('away_team')} @ {row.get('home_team')}")
                print(f'  [{label}] {tag}')
                for col, v, why in bad:
                    print(f'      {col} = {v}  ({why})')
                if label == 'results':
                    # ══ NO CLEANER TABLE EXISTS FOR A RESULTS ROW ══
                    # Results IS the graded source of record, so there is
                    # nothing to copy from. The only honest repair is to NULL
                    # the value, exactly as nhl_repair_puckline_pollution
                    # argued: a price cannot be inverted back into a line, and
                    # leaving -258 in place lets a future grader conclude the
                    # home team needed to win by 258.
                    #
                    # A grade is cleared ONLY when it is reproducible from the
                    # bogus value, which proves it was derived from it rather
                    # than graded independently.
                    if not null_unfixable:
                        unrepaired += len(bad)
                        continue
                    patch = {c: None for c, _, _ in bad}
                    hs, as_ = _f(row.get('home_score')), _f(row.get('away_score'))
                    sp_bad = next((v for c, v, _ in bad
                                   if c == cfg['spread_col']), None)
                    if (sp_bad is not None and hs is not None and as_ is not None
                            and row.get('spread_result')):
                        cov = (hs - as_) + sp_bad
                        repro = ('push' if abs(cov) < 1e-9 else
                                 'home_covered' if cov > 0 else 'away_covered')
                        if repro == row.get('spread_result'):
                            patch['spread_result'] = None
                            print('      -> spread_result was DERIVED from '
                                  'the bogus line; clearing it too')
                    pr = requests.patch(
                        f"{SB}/rest/v1/{cfg['res']}?game_id=eq.{row['game_id']}",
                        headers={**H, 'Content-Type': 'application/json',
                                 'Prefer': 'return=minimal'},
                        json=patch, timeout=30)
                    if pr.status_code in (200, 204):
                        print(f'      -> nulled {sorted(patch)}')
                    else:
                        print(f'      -> PATCH {pr.status_code}: '
                              f'{pr.text[:160]}')
                        unrepaired += len(bad)
                    continue
                if not repair:
                    unrepaired += len(bad)
                    continue
                k = (str(row.get('game_date'))[:10], str(row.get('home_team')),
                     str(row.get('away_team')))
                src = by_fixture.get(k)
                if not src:
                    print('      -> no results row for this fixture; '
                          'cannot repair')
                    unrepaired += len(bad)
                    continue
                patch = {}
                for col, _v, _why in bad:
                    sv = _f(src.get(col))
                    if sv is None:
                        continue
                    # Only accept a source value that is itself plausible.
                    probe = dict(row)
                    probe[col] = sv
                    if any(c == col for c, _, _ in bad_fields(probe, cfg)):
                        continue
                    patch[col] = sv
                if not patch:
                    print('      -> results has no plausible replacement')
                    unrepaired += len(bad)
                    continue
                pr = requests.patch(
                    f"{SB}/rest/v1/{cfg['ctx']}?game_id=eq.{row['game_id']}",
                    headers=H_W, json=patch, timeout=30)
                if pr.status_code not in (200, 204):
                    print(f'      -> PATCH {pr.status_code}: {pr.text[:160]}')
                    unrepaired += len(patch)
                    continue
                # Read the representation back: open_* is silently discarded by
                # freeze_opening_lines and still returns 200.
                try:
                    after = (pr.json() or [{}])[0]
                except Exception:                              # noqa: BLE001
                    after = {}
                for col, sv in patch.items():
                    got = _f(after.get(col))
                    if got is not None and abs(got - sv) < 1e-9:
                        print(f'      -> repaired {col} = {sv}')
                    elif col.startswith('open_'):
                        # freeze_opening_lines swallowed it and still returned
                        # 200. 20261014a adds the one sanctioned override, an
                        # RPC that sets sweat.allow_open_override for the
                        # duration of a single call.
                        rp = requests.post(
                            f'{SB}/rest/v1/rpc/repair_opening_line',
                            headers={**H, 'Content-Type': 'application/json'},
                            json={'p_table': cfg['ctx'],
                                  'p_game_id': str(row['game_id']),
                                  f'p_{col}': sv}, timeout=30)
                        if rp.status_code in (200, 204) and str(rp.text).strip() not in ('0', ''):
                            print(f'      -> repaired {col} = {sv} via '
                                  f'repair_opening_line RPC')
                        elif rp.status_code == 404:
                            print(f'      -> {col} REFUSED (still {got}) — '
                                  f'frozen by freeze_opening_lines, and '
                                  f'repair_opening_line does not exist yet. '
                                  f'Apply 20261014a_opening_line_repair.sql')
                            unrepaired += 1
                        else:
                            print(f'      -> {col} RPC {rp.status_code}: '
                                  f'{rp.text[:160]}')
                            unrepaired += 1
                    else:
                        print(f'      -> {col} REFUSED (still {got})')
                        unrepaired += 1
        if not found:
            print('  ✓ clean')
        else:
            print(f'  {dict(found)}')
    print(f'\nTOTAL implausible rows: {total_bad}'
          + (f'   still bad after repair: {unrepaired}' if repair else ''))
    return unrepaired if repair else total_bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS))
    ap.add_argument('--repair', action='store_true')
    ap.add_argument('--null-unfixable', action='store_true',
                    help='NULL an implausible value on a RESULTS row, where no '
                         'cleaner source exists, plus any grade reproducible '
                         'from it. A wrong line is worse than a missing one.')
    ap.add_argument('--strict', action='store_true',
                    help='exit 1 when anything implausible survives')
    a = ap.parse_args()
    n = run([a.sport] if a.sport else sorted(SPORTS), a.repair,
            a.null_unfixable)
    if a.strict and n:
        print('STRICT: implausible market values remain')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
