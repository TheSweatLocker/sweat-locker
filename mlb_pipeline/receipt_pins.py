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
