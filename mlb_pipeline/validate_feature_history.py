#!/usr/bin/env python3
"""Step 3 — THE GATE. Validate reconstructed features against stored ones.

build_feature_history.py rebuilds point-in-time team features from results.
Nothing may be trained on that table until the reconstruction is shown to
reproduce what was actually stored on the days where both exist. If
reconstructed SOR on 2026-10-04 does not match what was stored that day, the
rebuild is wrong and everything downstream is untrustworthy.

WHAT MAKES THIS AWKWARD, AND WHY THE SCRIPT TESTS IT RATHER THAN ASSUMING
-------------------------------------------------------------------------
`*_game_context` rows are MUTABLE and get rewritten. So a stored "last 5 ATS"
on a game from three weeks ago may hold either

    (a) the value as of that game  -- what we want to compare against, or
    (b) a value refreshed later, i.e. the last 5 before TODAY

Against (b) a perfect reconstruction still mismatches, and reading that as "my
rebuild is broken" would be wrong. So before comparing, the script measures
which regime the stored column is in: if a team's stored value is IDENTICAL
across games on different dates, the column was refreshed wholesale and cannot
referee a point-in-time rebuild. That is reported as INCONCLUSIVE, not as a
pass and not as a failure.

SAMPLE SIZES ARE THE POINT OF THE WHOLE PROJECT, SO THEY ARE STATED LOUDLY
--------------------------------------------------------------------------
Measured 2026-10-13, stored SOR/SOS only begins 2026-10-10:

    nfl_game_context    14 past rows carry home_sor  (of 123)
    ncaaf_game_context  46 past rows carry home_sor  (of 472)
    ncaaf_game_context 448 past rows carry home_ats_last5

So the SOR check is a SMOKE TEST for sign, scale and team-alignment — the three
ways a rebuild goes grossly wrong — and explicitly not proof of agreement.
The ATS check is the one with real n.

USAGE
    python validate_feature_history.py --sport NFL
    python validate_feature_history.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys

import requests

import build_feature_history as B
from compute_margin_strength import H, SB

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NHL': 'nhl_game_context', 'NBA': 'nba_game_context',
       'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return (num / (dx * dy)) if dx and dy else None


def refreshed_wholesale(ctx: list[dict], col_h: str) -> tuple[bool, str]:
    """Is the stored column as-of-game-date, or refreshed to one value?

    If a team's stored value is identical on games played on DIFFERENT dates,
    the column cannot have been written as-of each game.
    """
    per_team = collections.defaultdict(set)
    for r in ctx:
        v, t, d = r.get(col_h), r.get('home_team'), str(r.get('game_date'))[:10]
        if v is None or not t:
            continue
        per_team[t].add((d, round(float(v), 4)))
    multi = {t: s for t, s in per_team.items()
             if len({d for d, _ in s}) > 1}
    if not multi:
        return False, 'only one dated value per team — cannot tell'
    const = sum(1 for t, s in multi.items() if len({v for _, v in s}) == 1)
    frac = const / len(multi)
    if frac >= 0.8:
        return True, (f'{const}/{len(multi)} teams have ONE value across '
                      f'multiple dates — column was refreshed wholesale')
    return False, (f'{const}/{len(multi)} teams constant across dates — '
                   f'values do vary by date, so as-of comparison is meaningful')


def run(sport: str) -> int:
    tbl = CTX[sport]
    ctx = page(tbl, {'select': '*'})
    if not ctx:
        print(f'{sport}: {tbl} empty')
        return 1
    rows = B.build(sport, quiet=True)
    recon = {str(r['game_id']): r for r in rows if r['game_id']}
    print(f'=== validate {sport}: {len(rows)} reconstructed, {len(ctx)} context rows')

    joined = [(c, recon[str(c.get('game_id'))]) for c in ctx
              if str(c.get('game_id')) in recon]
    print(f'  joined on game_id: {len(joined)}')
    if not joined:
        # NFL (and NHL) context and results live in different id spaces —
        # context stores an md5 hash, results stores YYYYMMDD_AWAY_HOME. See
        # nfl_game_id_bridge. Team ABBREVIATIONS are identical across the two
        # for NFL, so (date, home, away) resolves it without guessing.
        by_key = {(r['game_date'], str(r['home_team']), str(r['away_team'])): r
                  for r in rows}
        joined = [(c, by_key[k]) for c in ctx
                  if (k := (str(c.get('game_date'))[:10],
                            str(c.get('home_team')),
                            str(c.get('away_team')))) in by_key]
        print(f'  game_id spaces differ — fell back to (date, home, away): '
              f'{len(joined)} joined')
    if not joined:
        print('  ✗ ZERO overlap on either key — cannot validate this way.')
        return 1

    fails = 0

    # ── Block 1: SOR / SOS smoke test ────────────────────────────────
    print('\n  [1] SOR / SOS vs stored (smoke test — sign, scale, alignment)')
    wholesale, why = refreshed_wholesale(ctx, 'home_sor')
    print(f'      stored-column regime: {why}')
    for stored_h, stored_a, mine in (('home_sor', 'away_sor', 'srs'),
                                     ('home_sos', 'away_sos', 'sos')):
        pairs = []
        for c, r in joined:
            for side, col in (('home', stored_h), ('away', stored_a)):
                sv, mv = c.get(col), r['features'].get(f'{side}_{mine}')
                if sv is not None and mv is not None:
                    pairs.append((float(sv), float(mv)))
        if len(pairs) < 3:
            print(f'      {mine}: n={len(pairs)} — too few stored values to '
                  f'compare (this IS the constraint the project exists for)')
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        c_ = _corr(xs, ys)
        md = statistics.mean([abs(a - b) for a, b in pairs])
        print(f'      {mine}: n={len(pairs)}  corr={c_:+.3f}  '
              f'mean|diff|={md:.3f}  stored sd={statistics.pstdev(xs):.2f} '
              f'mine sd={statistics.pstdev(ys):.2f}')
        if wholesale:
            print('        INCONCLUSIVE — stored column is not as-of-date')
        elif c_ is not None and c_ < 0:
            print('        ✗ NEGATIVE correlation — sign or team alignment is '
                  'inverted')
            fails += 1
        elif c_ is not None and c_ < 0.5:
            print('        ⚠ weak agreement — investigate before training')

    # ── Block 2: ATS last-5, the block with real n ───────────────────
    print('\n  [2] ATS last-5 vs stored (the dense check)')
    if 'home_ats_last5' not in ctx[0]:
        print('      column absent on this sport — skipped')
    else:
        wholesale2, why2 = refreshed_wholesale(ctx, 'home_ats_last5')
        print(f'      stored-column regime: {why2}')
        exact = near = tot = 0
        diffs = []
        for c, r in joined:
            for side in ('home', 'away'):
                sv = c.get(f'{side}_ats_last5')
                if sv is None:
                    continue
                f = r['features']
                # Compare the SAME window. The first cut of this block used
                # the season-to-date cover count and only accepted teams with
                # <= 5 priced games, which made a window mismatch look like a
                # 34.9% agreement rate. ats_l5_covers is the true last-5.
                mv = f.get(f'{side}_ats_l5_covers')
                if mv is None:
                    continue
                tot += 1
                d = abs(int(sv) - int(mv))
                diffs.append(d)
                exact += 1 if d == 0 else 0
                near += 1 if d <= 1 else 0
        if tot < 10:
            print(f'      n={tot} comparable rows — too few; the windows do '
                  f'not line up on enough games')
        else:
            print(f'      n={tot}  exact={exact} ({100*exact/tot:.1f}%)  '
                  f'within1={near} ({100*near/tot:.1f}%)  '
                  f'mean|diff|={statistics.mean(diffs):.2f}')
            if wholesale2:
                print('        INCONCLUSIVE — stored column is not as-of-date')
            elif exact / tot < 0.5:
                print('        ⚠ under half match exactly — the window '
                      'definitions differ; reconcile before trusting either')

    # ── Block 2b: reproduce the LIVE definition exactly ──────────────
    # Block 2 compares two different definitions, so a low match rate there is
    # not evidence of a broken rebuild. Measured causes of the difference, from
    # backfill_ncaaf_team_tendencies:
    #   * days_lookback=400 — its L5 CROSSES the season boundary; the
    #     reconstruction resets per season on purpose (a college roster at
    #     season N-1 is barely the same team).
    #   * it reads `spread_result` directly instead of deriving cover from
    #     close_spread, and NCAAF close_spread is only 38-42% populated, so the
    #     two game SETS differ.
    # This block applies the live definition to the same raw results and checks
    # the walk-forward plumbing can hit the stored number. If THIS matches, the
    # rebuild is sound and Block 2's gap is purely definitional.
    print('\n  [2b] same window, LIVE definition (spread_result, cross-season)')
    res = page({'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
                'NHL': 'nhl_game_results', 'NBA': 'nba_game_results',
                'MLB': 'mlb_game_results',
                'NCAAB': 'ncaab_game_results'}[sport], {'select': '*'})
    per = collections.defaultdict(list)
    for g in res:
        sr, d = g.get('spread_result'), str(g.get('game_date'))[:10]
        if not sr or not d:
            continue
        for team, as_side in ((g.get('home_team'), 'HOME'),
                              (g.get('away_team'), 'AWAY')):
            if team:
                per[team].append((d, sr, as_side))
    for t in per:
        per[t].sort()
    exact2 = tot2 = 0
    diffs2 = []
    for c, _r in joined:
        d = str(c.get('game_date'))[:10]
        for side in ('home', 'away'):
            sv = c.get(f'{side}_ats_last5')
            team = c.get(f'{side}_team')
            if sv is None or not team:
                continue
            prior = [e for e in per.get(team, []) if e[0] < d][-5:]
            if not prior:
                continue
            cov = sum(1 for _d, sr, sd in prior
                      if (sr == 'home_covered' and sd == 'HOME')
                      or (sr == 'away_covered' and sd == 'AWAY'))
            tot2 += 1
            diffs2.append(abs(int(sv) - cov))
            exact2 += 1 if int(sv) == cov else 0
    if tot2 < 10:
        print(f'      n={tot2} — too few to judge')
    else:
        print(f'      n={tot2}  exact={exact2} ({100*exact2/tot2:.1f}%)  '
              f'mean|diff|={statistics.mean(diffs2):.2f}')
        if exact2 / tot2 >= 0.9:
            print('        ✓ the live definition IS reproducible walk-forward '
                  '— Block 2\'s gap is definitional, not a rebuild error')
        elif exact2 / tot2 > (exact / tot if tot else 0):
            print('        ⚠ closer than Block 2 but not exact — some stored '
                  'rows were refreshed after their game')
        else:
            print('        ✗ cannot reproduce the stored column even with its '
                  'own definition — investigate before training')
            fails += 1

    # ── Block 3: internal invariants (always decisive) ───────────────
    print('\n  [3] internal invariants (independent of any stored column)')
    bad_games = sum(1 for r in rows
                    if r['home_games_used'] < 0 or r['away_games_used'] < 0)
    bad_fed = sum(1 for r in rows if r['srs_fed'] and
                  (r['home_games_used'] < B.MIN_GAMES_SRS
                   or r['away_games_used'] < B.MIN_GAMES_SRS))
    srs_on_zero = sum(1 for r in rows
                      if r['home_games_used'] == 0 and r['home_srs'] is not None)
    wl = 0
    for r in rows:
        f = r['features']
        for side in ('home', 'away'):
            w, l, n = (f.get(f'{side}_wins'), f.get(f'{side}_losses'),
                       f.get(f'{side}_games_used'))
            if None in (w, l, n):
                continue
            if w + l > n:
                wl += 1
    print(f'      negative games_used:              {bad_games}')
    print(f'      srs_fed with < {B.MIN_GAMES_SRS} games:           {bad_fed}')
    print(f'      SRS present on a 0-game team:     {srs_on_zero}')
    print(f'      wins+losses exceeding games_used: {wl}')
    for label, v in (('negative games_used', bad_games),
                     ('fed flag below threshold', bad_fed),
                     ('rating on zero history', srs_on_zero),
                     ('record exceeding games played', wl)):
        if v:
            print(f'      ✗ {label}: {v}')
            fails += 1
    if not any((bad_games, bad_fed, srs_on_zero, wl)):
        print('      ✓ all internal invariants hold')

    print(f'\n  {"✗ GATE FAILED" if fails else "✓ no hard failures"} '
          f'({fails} hard failure(s))')
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX))
    return run(ap.parse_args().sport)


if __name__ == '__main__':
    sys.exit(main())
