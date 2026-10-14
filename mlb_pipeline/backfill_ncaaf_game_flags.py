#!/usr/bin/env python3
"""Fill NULL conference_game / neutral_site on ncaaf_game_results from CFBD.

══ 2026-10-14 · THE GAP ══
Both columns are populated on 100% of 2022, 2023, 2024 and 2025 (3,705-3,831
rows each) and on **0 of 576** in 2026, 474 of them already played.

Nothing currently writes them. `resolve_ncaaf_results` pulls CFBD /games but
only PATCHes scores and the outcomes derived from them;
`ncaaf_team_game_stats_pull` does set `conference_game` but on
`ncaaf_team_game_stats`, a different table. The 2022-2025 values came from a
historical import that is not part of the running pipeline, so the current
season simply never gets them.

It matters because both are real model inputs, not decoration:
  * `ncaaf_cohort_backfill` reads conference_game to build its cohorts
  * `ncaaf_total_logreg_train` lists neutral_site and conference_game among
    its context features
  * a NULL reads as False to any truthiness test, so a conference game and a
    neutral-site game are silently mislabelled rather than skipped — a WRONG
    feature, not a missing one

══ WHY NOT DERIVE IT ══
A conference map would be the obvious shortcut and it is a trap in college
football specifically: realignment moves teams between conferences almost
every year, so a single map is wrong for some fraction of any multi-season
backfill. Same failure mode as the NFL division map, where a team's historical
rivals spanned the 2002 realignment and the derived map claimed 35 teams.
CFBD ships the flag per game, as of that game. Use the source.

══ SCOPE GUARD ══
Fills ONLY conference_game and neutral_site, and only where the stored value
is NULL. The allow-list is asserted below rather than left as a convention,
because the sibling script backfill_nfl_game_metadata exists precisely because
running a full-payload backfill over a current season would have written NULL
over live closing lines on unplayed rows.

IDEMPOTENT: fills NULLs only.

USAGE
    python backfill_ncaaf_game_flags.py                  # dry run, 2026
    python backfill_ncaaf_game_flags.py --apply
    python backfill_ncaaf_game_flags.py --season 2025 --apply
"""
from __future__ import annotations

import argparse
import collections
import sys

import requests
from urllib.parse import quote as _urlquote

import market_line as ML
from resolve_ncaaf_results import _fold_name, cfbd_get

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SAFE_COLS = ('conference_game', 'neutral_site')
_FORBIDDEN = ('close_', 'open_', '_score', 'spread_result', 'total_result',
              'home_win', 'total_points')
for _c in SAFE_COLS:
    assert not any(f in _c for f in _FORBIDDEN), f'SAFE_COLS must not include {_c}'



def _q(v) -> str:
    """Percent-encode a value used in a PostgREST filter.

    Load-bearing, not hygiene. NCAAF game_ids embed the team names and several
    contain an ampersand — Texas A&M, North Carolina A&T, Florida A&M, Prairie
    View A&M. Unencoded, the '&' TERMINATES the query parameter: the filter
    silently becomes game_id=eq.<...>_Texas A plus a stray 'M' param, the PATCH
    matches ZERO rows, and PostgREST answers 204, which reads as success.

    Measured 2026-10-14: a backfill reported "patched 571/571" and a read-back
    found 553 populated. The 18 missing rows were every fixture with an '&' in
    a team name. feedback_unencoded_url_filter_eats_writes.
    """
    return _urlquote(str(v), safe='')

def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.\n')

    rows = [r for r in page('ncaaf_game_results', {'select': '*'})
            if str(r.get('season')) == str(a.season)]
    print(f'=== ncaaf_game_results season {a.season}: {len(rows)} stored rows')
    if not rows:
        print('  nothing stored for that season')
        return 0

    games = []
    for st in ('regular', 'postseason'):
        games += cfbd_get('/games', {'year': a.season, 'seasonType': st})
    print(f'  CFBD /games returned {len(games)} rows')
    if not games:
        print('  ✗ CFBD returned nothing — is CFBD_API_KEY set?')
        return 1

    # Fold team names: CFBD and our table do not always agree on punctuation
    # or the "State"/"St." style, which is what _fold_name exists for.
    src = {}
    for g in games:
        h = _fold_name(g.get('homeTeam') or g.get('home_team') or '')
        aw = _fold_name(g.get('awayTeam') or g.get('away_team') or '')
        if not h or not aw:
            continue
        cg = g.get('conferenceGame', g.get('conference_game'))
        ns = g.get('neutralSite', g.get('neutral_site'))
        src[(h, aw)] = {'conference_game': None if cg is None else bool(cg),
                        'neutral_site': None if ns is None else bool(ns)}
    print(f'  indexed {len(src)} CFBD fixtures by folded team pair')

    before = {c: sum(1 for r in rows if r.get(c) is not None) for c in SAFE_COLS}
    patches, unmatched = [], 0
    filled = collections.Counter()
    for r in rows:
        k = (_fold_name(str(r.get('home_team') or '')),
             _fold_name(str(r.get('away_team') or '')))
        p = src.get(k)
        if not p:
            unmatched += 1
            continue
        body = {c: p[c] for c in SAFE_COLS
                if r.get(c) is None and p.get(c) is not None}
        if body:
            for c in body:
                filled[c] += 1
            patches.append((r['game_id'], body))

    print(f'  matched to CFBD: {len(rows) - unmatched}/{len(rows)}'
          + (f'   UNMATCHED {unmatched}' if unmatched else ''))
    print(f'\n  {"column":<18}{"before":>8}{"would fill":>12}{"after":>8}')
    for c in SAFE_COLS:
        print(f'  {c:<18}{before[c]:>8}{filled[c]:>12}{before[c] + filled[c]:>8}')
    if filled['conference_game']:
        # Sanity: roughly 60-70% of FBS regular-season games are conference
        # games. A share far from that means the fold matched the wrong rows.
        tru = sum(1 for _g, b in patches if b.get('conference_game') is True)
        share = 100 * tru / filled['conference_game']
        print(f'\n  conference_game TRUE on {share:.1f}% of filled rows '
              f'(expect ~55-70% for FBS)')
        if not (35 <= share <= 85):
            print('  ✗ share implausible — refusing to write')
            return 1

    print(f'\n  rows needing a patch: {len(patches)}')
    if not a.apply:
        for gid, body in patches[:4]:
            print(f'    {gid}: {body}')
        return 0

    ok = 0
    for gid, body in patches:
        # URL-ENCODE the game_id. NCAAF ids embed the team names and several
        # contain '&' (Texas A&M, North Carolina A&T, Florida A&M). Unencoded,
        # the '&' TERMINATES the query parameter: the filter silently becomes
        # game_id=eq.<...>_Texas A with a stray 'M' param, the PATCH matches 0
        # rows, and PostgREST returns 204 — reported as success. 18 of 571 rows
        # were lost to exactly this before it was caught.
        # feedback_unencoded_url_filter_eats_writes
        pr = requests.patch(f'{SB}/rest/v1/ncaaf_game_results?game_id=eq.{_q(gid)}',
                            headers=H_W, json=body, timeout=30)
        if pr.status_code in (200, 204):
            ok += 1
        else:
            print(f'  ⚠ {gid} {pr.status_code}: {pr.text[:200]}')
    print(f'  patched {ok}/{len(patches)}')

    chk = [r for r in page('ncaaf_game_results', {'select': '*'})
           if str(r.get('season')) == str(a.season)]
    print('\n  VERIFY by re-read:')
    for c in SAFE_COLS:
        n = sum(1 for r in chk if r.get(c) is not None)
        print(f'    {c:<18}{n}/{len(chk)}')
    return 0 if ok == len(patches) else 1


if __name__ == '__main__':
    sys.exit(main())
