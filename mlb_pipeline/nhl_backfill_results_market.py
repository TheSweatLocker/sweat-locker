#!/usr/bin/env python3
"""Fill NULL market columns on PLAYED nhl_game_results rows from line_history.

══ 2026-10-13 · WHY THIS EXISTS ══
Andy: "St Louis Blues name not matching, some items are missing in game detail
regarding them."

Two separate failures shared that one symptom. The first was the odds join in
nhl_game_context (exact-string team names across two feeds that spell
St. Louis differently) — fixed in nhl_game_context.py. This script repairs the
second: StL's PLAYED games carry no market data at all, so everything derived
from a closing line is empty for that team.

Measured 2026-10-13 on the 2026-27 regular season (games from 09-29, when the
market actually opens — the 09-19..09-26 preseason block legitimately has no
line and is excluded):

    played regular-season games        89
    carrying close_puckline            84   (94.4%)
    missing                             5   ← ALL FIVE are St. Louis Blues
                                             (10-02, 10-03, 10-06, 10-08, 10-10)

No other team is missing a single game. The consequence ran straight to the
game card: backfill_nhl_team_tendencies reads spread_result / total_result /
close_puckline off these rows, so St. Louis came back ats_last5 0-0,
ou_last5 0-0, covers_as_fav_pct None while all 31 other teams were populated.

WHAT IS NOT YET PROVEN. nhl_odds_pull writes with resolution=merge-duplicates
on game_id, so it overwrites rather than skips, and the Odds API name IS in
NHL_TEAMS — meaning it should have landed. Why these five rows specifically
never received a pre-game capture is NOT established; the pull only stores
pre-game events (is_pregame), so a plausible cause is that St. Louis's games
were consistently captured late, but that is a hypothesis and is logged as one.
This script repairs the DATA; the writer-side root cause stays open.

line_history already holds the quotes (1,096-1,460 per St. Louis matchup), so
the repair costs no Odds API credits.

SAFETY
  * Only PLAYED games (both scores present).
  * Only fills a column that is currently NULL. Never overwrites a stored
    closing line — that number is what grading compares against.
  * Requires MIN_BOOKS quotes behind a median, the same floor
    backfill_closing_lines uses. One book posting an alternate line is how the
    original bad-number class got in.
  * Derives spread_result / total_result only for rows where it just filled
    the line, and only from the final scores.
  * Dry-run by default.

Sign convention: close_puckline is HOME-perspective, NEGATIVE = home favoured.

USAGE
    python nhl_backfill_results_market.py                      # dry run
    python nhl_backfill_results_market.py --apply
    python nhl_backfill_results_market.py --since 2026-09-29 --apply
"""
from __future__ import annotations

import argparse
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

#: Same floor as backfill_closing_lines.MIN_BOOKS.
MIN_BOOKS = 3

#: The NHL regular season opened 2026-09-29. Preseason games before that are in
#: nhl_game_results with scores and no market, which is correct — there is no
#: closing line to find, and including them would make the coverage figure a
#: lie about the half that matters.
DEFAULT_SINCE = '2026-09-29'


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


def run(since: str, apply: bool) -> None:
    res = page('nhl_game_results', {'select': '*', 'game_date': f'gte.{since}'})
    played = [r for r in res
              if r.get('home_score') is not None and r.get('away_score') is not None]
    need = [r for r in played
            if r.get('close_puckline') is None or r.get('close_total') is None]
    print(f'=== nhl_game_results market backfill · since {since}')
    print(f'  rows={len(res)}  played={len(played)}  '
          f'missing a line={len(need)}')
    if not need:
        print('  nothing to fill')
        return

    by_team: dict[str, int] = {}
    for r in need:
        for t in (r.get('home_team'), r.get('away_team')):
            by_team[t] = by_team.get(t, 0) + 1
    print('  teams affected: ' + ', '.join(
        f'{t} ({n})' for t, n in sorted(by_team.items(), key=lambda kv: -kv[1])))

    sp = ML.fetch_market('NHL', 'spread', since)
    to = ML.fetch_market('NHL', 'total', since)
    by_sp = ML.index_by_matchup(sp)
    by_to = ML.index_by_matchup(to)
    # build_matcher already lowercases and strips periods, so it resolves
    # 'St. Louis Blues' against line_history's 'St Louis Blues'.
    m_sp = ML.build_matcher(by_sp, 'NHL')
    m_to = ML.build_matcher(by_to, 'NHL')
    print(f'  line_history: {len(sp)} spread quotes / {len(by_sp)} matchups, '
          f'{len(to)} total quotes / {len(by_to)} matchups')

    filled = thin = nomatch = 0
    patches: list[tuple[str, dict, str]] = []
    for r in need:
        home, away = r.get('home_team'), r.get('away_team')
        body: dict = {}
        notes = []

        if r.get('close_puckline') is None:
            mt = m_sp(away, home)
            if not mt:
                nomatch += 1
            else:
                hv = (by_sp.get(mt) or {}).get('home')
                if hv and hv[1] >= MIN_BOOKS:
                    # close_puckline ONLY. nhl_game_results has no
                    # close_spread column — that mirror lives on
                    # nhl_game_context (nhl_game_context.py writes it so the
                    # shared MARKET strip can read hockey). Naming it here
                    # returned PGRST204 and PostgREST dropped the ENTIRE
                    # patch, all five rows, rather than the one bad key.
                    body['close_puckline'] = float(hv[0])
                    notes.append(f'pl={hv[0]} ({hv[1]}bk)')
                elif hv:
                    thin += 1
                    notes.append(f'pl THIN {hv[1]}bk')

        if r.get('close_total') is None:
            mt = m_to(away, home)
            if mt:
                ov = (by_to.get(mt) or {}).get('over')
                if ov and ov[1] >= MIN_BOOKS:
                    body['close_total'] = float(ov[0])
                    notes.append(f'tot={ov[0]} ({ov[1]}bk)')
                elif ov:
                    notes.append(f'tot THIN {ov[1]}bk')

        # Derive the graded result ONLY from the line this run just filled and
        # the final score. A row whose line stays NULL keeps a NULL result —
        # inventing one would be worse than the gap.
        hs, as_ = float(r['home_score']), float(r['away_score'])
        margin = hs - as_
        if 'close_puckline' in body and r.get('spread_result') is None:
            cov = margin + float(body['close_puckline'])
            body['spread_result'] = ('push' if abs(cov) < 1e-9
                                     else 'home_covered' if cov > 0
                                     else 'away_covered')
            notes.append(body['spread_result'])
        if 'close_total' in body and r.get('total_result') is None:
            tot = hs + as_
            body['total_result'] = ('push' if abs(tot - float(body['close_total'])) < 1e-9
                                    else 'over' if tot > float(body['close_total'])
                                    else 'under')
            notes.append(body['total_result'])

        if body:
            filled += 1
            patches.append((r['game_id'], body, f"{r.get('game_date')} "
                                                f"{away} @ {home} {as_:.0f}-{hs:.0f}"))
            print(f"    {r.get('game_date')} {str(away)[:22]:<23}@ "
                  f"{str(home)[:22]:<23} {'  '.join(notes)}")

    print(f'\n  fillable={filled}  thin(<{MIN_BOOKS} books)={thin}  '
          f'no line_history match={nomatch}')
    if not apply:
        print(f'  [dry] {len(patches)} rows would be patched — pass --apply')
        return

    ok = 0
    for gid, body, label in patches:
        pr = requests.patch(f'{SB}/rest/v1/nhl_game_results?game_id=eq.{gid}',
                            headers=H_WRITE, json=body, timeout=30)
        if pr.status_code in (200, 204):
            ok += 1
        else:
            # A PATCH naming a column the table lacks drops the WHOLE patch
            # (PGRST204), so this must be loud rather than counted as success.
            print(f'    ⚠ patch {gid} ({label}) {pr.status_code}: {pr.text[:200]}')
    print(f'  patched {ok}/{len(patches)}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=DEFAULT_SINCE)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.\n')
    run(a.since, a.apply)


if __name__ == '__main__':
    main()
