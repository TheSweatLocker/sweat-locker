"""One rule, one implementation: never delete a row a published pick needs.

WHY THIS MODULE EXISTS (2026-10-06)
-----------------------------------
public_receipts is the immutable published-pick ledger. A prop receipt resolves
its result through its source row, so deleting that row strands the receipt at
result=NULL for good. Measured today: 238 prop receipts are permanently
unsettleable for exactly this reason, going back to June, and
"Mike Yastrzemski Under 0.5 Hits" shipped on the Sweat Card at PRIME/75 with no
source row at all.

THREE separate places delete these rows:

    generate_props.prune_stale_props        by age, the bulk deleter
    generate_props  scratched-starter pass  by (game_id, player, prop_type)
    dedup_prop_dupes                        duplicate losers, by id
    cleanup_stale_coverage_props            prop_jerry_reads, by id

The guard was written inline twice on 2026-10-06 and was about to be written a
third time. This repo has been bitten repeatedly by a second copy of a rule
drifting from the first, so it lives here once and everything imports it.

WHAT IS AND IS NOT THE PROBLEM
Receipts address mlb_pipeline_props by COMPOSITE KEY
("prop:Nick Pivetta|ks_over|3.5"), not by id. So the row ids churning when a
prop is re-created is HARMLESS — a fresh id still answers the same composite.
Only DELETION breaks a receipt. That is why these are pins and not an
id-stability fix.

prop_jerry_reads is different: receipts cite its numeric id, so there both
deletion and renumbering matter, and the pin is by id.

FAILS CLOSED, ALWAYS
Every function here returns a sentinel the caller must treat as "keep
everything" when the lookup cannot be completed. A pruner that cannot prove a
row is unreferenced must not delete it: keeping a stale row for a day costs
nothing, and deleting a pinned one is unrecoverable.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_SB = os.environ.get('SUPABASE_URL')
_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        or os.environ.get('SUPABASE_KEY'))
_H = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'}

# Returned when the lookup fails. `is` -checked by callers, so it cannot be
# confused with "nothing is pinned" (an empty set).
LOOKUP_FAILED = None


def pinned_prop_composites(game_date: str, source_table: str = 'mlb_pipeline_props'):
    """{'player|prop_type|prop_line'} that an UNGRADED receipt depends on.

    Returns LOOKUP_FAILED (None) if the receipts table cannot be read, which
    the caller must treat as "delete nothing".

    Only result IS NULL receipts pin. Once a receipt carries a result it no
    longer needs its source row, so pruning stays effective instead of
    freezing the table forever.
    """
    keys = set()
    try:
        r = requests.get(
            f'{_SB}/rest/v1/public_receipts', headers=_H, timeout=30,
            params={'select': 'source_id',
                    'source_table': f'eq.{source_table}',
                    'result': 'is.null',
                    'game_date': f'eq.{game_date}',
                    'limit': '5000'})
    except Exception:                                   # noqa: BLE001
        return LOOKUP_FAILED
    if r.status_code not in (200, 206):
        return LOOKUP_FAILED
    try:
        rows = r.json()
    except ValueError:
        return LOOKUP_FAILED
    if not isinstance(rows, list):
        return LOOKUP_FAILED
    for z in rows:
        sid = str(z.get('source_id') or '')
        if sid.startswith('prop:'):
            keys.add(sid[5:])
    return keys


def composite_of(row: dict) -> str:
    """The key a receipt would use for this prop row. Must match the writer's
    format exactly — 'player_name|prop_type|prop_line'."""
    return (f"{row.get('player_name')}|{row.get('prop_type')}|"
            f"{row.get('prop_line')}")


def pinned_read_ids(ids, source_table: str = 'prop_jerry_reads'):
    """Of `ids`, those an ungraded receipt cites by numeric id.

    Returns LOOKUP_FAILED on any error. Chunked because an in.() list of a few
    thousand ids exceeds the practical URL length.
    """
    want = [str(i) for i in ids]
    if not want:
        return set()
    pinned = set()
    for i in range(0, len(want), 150):
        csv = ','.join(f'"{x}"' for x in want[i:i + 150])
        try:
            r = requests.get(
                f'{_SB}/rest/v1/public_receipts', headers=_H, timeout=30,
                params={'select': 'source_id',
                        'source_table': f'eq.{source_table}',
                        'source_id': f'in.({csv})',
                        'result': 'is.null', 'limit': '1000'})
        except Exception:                               # noqa: BLE001
            return LOOKUP_FAILED
        if r.status_code not in (200, 206):
            return LOOKUP_FAILED
        try:
            rows = r.json()
        except ValueError:
            return LOOKUP_FAILED
        if not isinstance(rows, list):
            return LOOKUP_FAILED
        pinned.update(str(z['source_id']) for z in rows if z.get('source_id'))
    return pinned


def split_deletable(rows: list, pinned_keys):
    """(deletable, held) for prop rows, by composite key.

    pinned_keys of LOOKUP_FAILED holds EVERYTHING — the fail-closed path.
    """
    if pinned_keys is LOOKUP_FAILED:
        return [], list(rows)
    if not pinned_keys:
        return list(rows), []
    deletable, held = [], []
    for row in rows:
        (held if composite_of(row) in pinned_keys else deletable).append(row)
    return deletable, held


# ──────────────────────────────────────────────────────────────────────────
# PUBLISHED CALLS ARE FROZEN
# ──────────────────────────────────────────────────────────────────────────
# The prop pins above stop a row being DELETED out from under a receipt.
# jerry_reads fails the other way: the row survives and its CONTENT is
# rewritten. jerry_reads_dual_write upserts with
# `on_conflict=sport,game_id,game_date`, so every regeneration overwrites
# call_market / call_side / call_line / call_text on the SAME id — and the
# receipt cites that id.
#
# Measured 2026-10-06 across all 1,340 receipts citing jerry_reads:
#
#     agrees with its cited read      1,091   81.4%
#     market differs                    186
#     side and/or line differs           63
#     cited row is gone                   2
#                                     ------
#     disagree                          249   18.6%
#
# Receipt "Alabama -11.5" cites a read calling South Carolina +12.5.
# "BUF ML" cites a read calling Over 50.5. Those are not drift, they are
# different bets, and the lineage silently points at the wrong one.
#
# THE BOUNDARY THAT MATTERS is publication. Before a read is published,
# rewriting it freely is correct — that is lineups confirming and lines
# moving. After we have shown it to a subscriber, the call is a historical
# fact, and changing it is rewriting what we told them. So: a row that no
# receipt references stays fully mutable; a row that a receipt references
# keeps its published call and takes every other update (prose, prices,
# snapshot) as normal.
#
# This is the same boundary as the prop pins, deliberately — one rule for
# "a published pick owns its source row", applied to both failure modes.

def published_call(sport: str, game_id: str, game_date: str):
    """The call a receipt already froze for this read, or None if none has.

    Returns LOOKUP_FAILED when the receipts table cannot be read, which the
    caller must treat as "freeze nothing changed" — see freeze_published_call.

    Keyed on the read ROW, not on the receipt: several surfaces can cite one
    read, and they all froze the same call because they all read the same row.
    """
    if not (_SB and _KEY and game_id and game_date):
        return None
    try:
        rr = requests.get(
            f'{_SB}/rest/v1/jerry_reads', headers=_H, timeout=30,
            params={'select': 'id', 'sport': f'eq.{sport}',
                    'game_id': f'eq.{game_id}',
                    'game_date': f'eq.{game_date}', 'limit': '1'})
        if rr.status_code not in (200, 206):
            return LOOKUP_FAILED
        rows = rr.json()
        if not isinstance(rows, list) or not rows:
            return None            # no read row yet: nothing can be frozen
        read_id = str(rows[0]['id'])

        r = requests.get(
            f'{_SB}/rest/v1/public_receipts', headers=_H, timeout=30,
            params={'select': 'market,pick_side,pick_line,pick_label,'
                              'published_at',
                    'source_table': 'eq.jerry_reads',
                    'source_id': f'eq.{read_id}',
                    'order': 'published_at.asc', 'limit': '1'})
        if r.status_code not in (200, 206):
            return LOOKUP_FAILED
        recs = r.json()
        if not isinstance(recs, list):
            return LOOKUP_FAILED
        if not recs:
            return None            # never published: free to rewrite
        a = recs[0]
        return {'call_market': a.get('market'),
                'call_side': a.get('pick_side'),
                'call_line': a.get('pick_line'),
                'call_text': a.get('pick_label'),
                'published_at': a.get('published_at')}
    except Exception:                                   # noqa: BLE001
        return LOOKUP_FAILED


def freeze_published_call(payload: dict, sport: str, game_id: str,
                          game_date: str) -> tuple[dict, str | None]:
    """(payload, note) with the call fields pinned to what was published.

    Fails CLOSED: if the lookup cannot be completed we leave the published
    call in place rather than risk overwriting it, because an overwrite is
    unrecoverable and a stale call for one cron is not.
    """
    frozen = published_call(sport, game_id, game_date)
    if frozen is None:
        return payload, None                      # nothing published yet
    if frozen is LOOKUP_FAILED:
        for k in ('call_market', 'call_side', 'call_line', 'call_text'):
            payload.pop(k, None)
        return payload, ('receipt lookup failed — leaving the published '
                         'call untouched')

    def _same(a, b):
        if a is None or b is None:
            return True            # nothing published for that field
        try:
            return abs(float(a) - float(b)) <= 0.01
        except (TypeError, ValueError):
            return str(a).strip().upper() == str(b).strip().upper()

    changed = [k for k in ('call_market', 'call_side', 'call_line')
               if not _same(payload.get(k), frozen.get(k))]
    if not changed:
        return payload, None
    note = (f'call already published as '
            f'{frozen.get("call_text")!r} '
            f'({frozen.get("call_market")}/{frozen.get("call_side")}/'
            f'{frozen.get("call_line")}) at '
            f'{str(frozen.get("published_at"))[:19]} — keeping it, '
            f'refusing to rewrite {changed}')
    for k in ('call_market', 'call_side', 'call_line', 'call_text'):
        payload[k] = frozen.get(k)
    return payload, note
