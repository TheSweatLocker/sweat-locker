"""Point an NCAAB results row at the row already stored for that game.

══ 2026-11-06 · R166 ════════════════════════════════════════════════════
`ncaab_game_results.game_id` used to embed the game's **UTC** date. It now
embeds the ET one (see sweat_dates.et_and_utc_stamps), because the UTC date
filed most of the slate a day late and `grade_public_receipts` joins a
receipt to its result on (game_date, away_team, home_team).

Every row written before the change is still keyed by the UTC id. Three
writers upsert into the table `on_conflict=game_id`, so a payload carrying
the new ET id would not conflict with anything — it would **INSERT a second
row for the same game**. On one date that is 165 duplicates, in the table
that grades the published record.

So before writing, each row is asked: is this game already stored under its
legacy id? If yes, write to THAT id. The row keeps the key it has always
had and gets the corrected `game_date`; new games get the ET id. Either way
there is exactly one row per game.

This is the companion to [[feedback_fix_every_writer_to_the_table]]: the
reconciliation lives here once rather than in three copies, because three
copies of the original derivation is what caused R166.
"""
from __future__ import annotations

import os
from typing import Iterable

import requests

#: PostgREST `in.(...)` rides in the URL and this stack silently TRUNCATES
#: a long one: measured 2026-11-06, 25 ids / 1,028 chars returned 200 and
#: 50 ids / 2,022 chars returned PGRST100. Keep chunks well under that.
_CHUNK = 20


def stored_ids(ids: Iterable[str], sb: str, headers: dict,
               timeout: int = 30) -> set:
    """The subset of `ids` that already exist in ncaab_game_results."""
    ids = [str(i) for i in ids if i]
    out: set = set()
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i:i + _CHUNK]
        q = ','.join('"%s"' % g.replace('"', '') for g in chunk)
        try:
            r = requests.get(
                '%s/rest/v1/ncaab_game_results' % sb.rstrip('/'),
                params={'game_id': 'in.(%s)' % q, 'select': 'game_id'},
                headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise RuntimeError(
                'ncaab_result_ids: id lookup failed (%s). Refusing to '
                'continue: an unreadable lookup is indistinguishable from '
                '"not stored yet" and would insert a duplicate row for '
                'every game.' % e)
        if r.status_code != 200:
            raise RuntimeError(
                'ncaab_result_ids: id lookup HTTP %s %s. Refusing to '
                'continue for the same reason.' % (r.status_code, r.text[:160]))
        for row in r.json():
            if row.get('game_id'):
                out.add(row['game_id'])
    return out


def reconcile(rows: list, sb: str, headers: dict,
              id_key: str = 'game_id',
              legacy_key: str = 'legacy_game_id',
              label: str = '') -> int:
    """Remap each row onto its legacy row where one exists. Returns the count.

    Rows without a legacy id are left alone, so this is safe to call on a
    mixed batch. `legacy_key` is popped either way — it is a transport
    field, and an unknown column 400s the whole upsert batch
    ([[feedback_select_star_before_concluding_absent]]).
    """
    if not rows:
        return 0
    want = set()
    for r in rows:
        if r.get(id_key):
            want.add(r[id_key])
        if r.get(legacy_key):
            want.add(r[legacy_key])
    have = stored_ids(want, sb, headers)

    n = 0
    for r in rows:
        legacy = r.pop(legacy_key, None)
        cur = r.get(id_key)
        if not legacy or not cur or legacy == cur:
            continue
        if cur not in have and legacy in have:
            r[id_key] = legacy
            n += 1
    if n:
        print('  %s%d row(s) remapped onto their legacy UTC-keyed id so the '
              'date is PATCHED, not duplicated' % (label, n))
    return n
