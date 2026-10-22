#!/usr/bin/env python3
"""Backfill grades onto public_receipts rows whose SOURCE row is graded.

══ 2026-10-13 · WHY THIS EXISTS ══
`backfill_public_receipts.upsert_batch` writes with
    Prefer: resolution=ignore-duplicates
on_conflict=(sport,surface,game_date,source_id).

That is correct for the immutable half of a receipt — the published pick, its
line and its price must never be rewritten after the fact, which is the whole
point of the surface. But `result` and `graded_at` are the MUTABLE half: they
are NULL at publish time and arrive hours later when the source table is
graded. Ignore-duplicates means a receipt inserted before its grade exists can
never receive that grade. It is stuck at NULL forever.

Whether a receipt is affected is therefore a pure race:

    snapshot 390 (10-11 20:41) → receipt first written AFTER grading ran → LOSS
    snapshot 389 (10-11 11:15) → receipt first written BEFORE grading ran → NULL

Measured on discovery, ledger alone: 258 receipts, 232 agreeing with their
snapshot, **21 stale** (snapshot graded, receipt NULL), 0 conflicting, 0
orphaned. The stale 21 skew toward WINs, so the receipt trail under-reports the
surface rather than flattering it.

══ THE ONE RULE ══
This script only ever fills a NULL. It never overwrites a receipt that already
carries a result, even if the source now disagrees — that case is a genuine
grading dispute and must be surfaced as a CONFLICT for a human, not silently
resolved. `feedback_parse_failure_must_not_overwrite` is the same lesson from
the other direction.

Usage:
    python sync_receipt_grades.py                # dry-run, every source
    python sync_receipt_grades.py --apply
    python sync_receipt_grades.py --source ledger_snapshots --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# Windows console is cp1252 and this script prints ⚠ on every conflict.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_SERVICE_KEY')
       or os.environ.get('SUPABASE_KEY', ''))
if not SB or not KEY:
    sys.exit('SUPABASE_URL / key not set')

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

# ══ 2026-10-13 · WHY AN id MATCH IS NOT ENOUGH ══
# A receipt is frozen at publish time. Its source row is MUTABLE and gets
# regenerated. So `receipt.source_id == source.id` does not mean the two
# describe the same bet, and a grade disagreement is usually drift, not a
# mis-grade. Two measured examples:
#
#   receipt 20986  Greg Dulcich OVER 2.5 receptions  LOSS
#   prop_jerry_reads 92820  Greg Dulcich over 1.5    Win
#     → same player and family, line rewritten 2.5 → 1.5. Both grades are
#       correct FOR THEIR OWN LINE.
#
#   receipt 22020  BUF ML                            WIN   (BUF won 24-16)
#   jerry_reads 4685  Over 50.5 (total)              Loss  (40 points)
#     → same id, different MARKET entirely. Both correct.
#
# Comparing grades across that join produced 375 "conflicts" on the first run
# of this script, every one of them an artefact. So each source declares an
# identity check, and a grade is only ever compared — or patched — when the
# source row still describes the receipt's bet. Where identity cannot be
# established the row is counted as DRIFTED and left alone.
#
# `ledger_snapshots` is the one source that is immutable by contract
# (snapshot_ledger.py locks legs and odds at generation time), which is exactly
# why it is the only one that showed 232 agreements and 0 conflicts.


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _ident_ledger(rec: dict, src: dict) -> bool:
    """Frozen by contract. Confirm the ticket shape anyway."""
    legs = src.get('legs') or []
    n = len(legs) if isinstance(legs, list) else None
    if n is not None and f'{n} leg' not in str(rec.get('pick_label') or ''):
        return False
    ro, so = rec.get('pick_odds'), src.get('combined_odds')
    if ro is not None and so is not None and int(ro) != int(so):
        return False
    return True


def _ident_prop_jerry(rec: dict, src: dict) -> bool:
    if (str(rec.get('player_name') or '').strip().lower()
            != str(src.get('player_name') or '').strip().lower()):
        return False
    if (str(rec.get('prop_type') or '').strip().lower()
            != str(src.get('prop_type') or '').strip().lower()):
        return False
    return _num(rec.get('pick_line')) == _num(src.get('prop_line'))


def _ident_jerry_read(rec: dict, src: dict) -> bool:
    if (str(rec.get('market') or '').strip().lower()
            != str(src.get('call_market') or '').strip().lower()):
        return False
    rs, ss = rec.get('pick_side'), src.get('call_side')
    if rs and ss and str(rs).strip().upper() != str(ss).strip().upper():
        return False
    rl, sl = _num(rec.get('pick_line')), _num(src.get('call_line'))
    if rl is not None and sl is not None and rl != sl:
        return False
    return True


# source_table value on public_receipts → (table, key column, identity check)
#
# Only sources whose receipts carry the source row's `id` in source_id appear
# here. Two do NOT and are deliberately excluded — their source_id is a
# composite natural key, so joining them on `id` returned 100% orphan:
#     daily_best_bet_history  source_id='potd:2026-10-03'
#     mlb_pipeline_props      source_id='prop:Trevor Rogers|er_under|1.5'
# Those two embed the full identity in the key itself, so if they ever need a
# grade sync it must be keyed on that string, not on id. Neither showed a
# single ungraded-with-graded-source row, so there is nothing to fix today.
#
# daily_degen / daily_dawg have no `id` column at all, and jerry_cache.* is a
# cache blob rather than a table.
SOURCES: dict[str, tuple[str, str, object]] = {
    'ledger_snapshots': ('ledger_snapshots', 'id', _ident_ledger),
    'prop_jerry_reads': ('prop_jerry_reads', 'id', _ident_prop_jerry),
    'jerry_reads':      ('jerry_reads', 'id', _ident_jerry_read),
}

# Source tables spell the grade differently. Normalise to the receipt's
# vocabulary. NOTE: `result` on public_receipts is TITLE case nowhere — it is
# upper (WIN/LOSS/PUSH); see feedback_result_column_is_title_case for the
# surface that is NOT.
_RMAP = {
    'w': 'WIN', 'win': 'WIN', 'won': 'WIN',
    'l': 'LOSS', 'loss': 'LOSS', 'lost': 'LOSS', 'lose': 'LOSS',
    'p': 'PUSH', 'push': 'PUSH', 'tie': 'PUSH',
    'void': 'VOID', 'ungradeable': 'UNGRADEABLE',
    'no_action': 'NO_ACTION', 'no-pick': 'NO_ACTION', 'no play': 'NO_ACTION',
    'tracked_elsewhere': 'TRACKED_ELSEWHERE',
}

# A source row can say "not graded yet" in words rather than with a NULL.
# `daily_best_bet_history` holds 5 rows reading 'Pending'. Treating that as a
# grade would stamp a receipt with a non-terminal state and, because this
# script only ever fills NULLs, it could never be corrected afterwards.
_NOT_A_GRADE = {'pending', 'in_progress', 'in progress', 'live', 'tbd', 'none',
                'null', 'n/a', '-'}


def normalise(raw) -> str | None:
    """Source grade → the receipt vocabulary, or None if it isn't a grade yet.

    Returns None for 'Pending'-style values so they are counted as ungraded
    rather than patched in.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in _NOT_A_GRADE:
        return None
    return _RMAP.get(s.lower(), s.upper())


def paged(table: str, params: dict) -> list[dict]:
    """Full table read. 20+ scripts in this repo have been caught measuring
    from a silently truncated 1000-row page."""
    out: list[dict] = []
    off = 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ,
                         params=p, timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {table} {r.status_code}: {r.text[:200]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def sync_source(recv_source: str, apply: bool) -> dict:
    table, keycol, ident = SOURCES[recv_source]
    # select='*' deliberately. An explicit SELECT naming a column this table
    # does not have returns 400, which every caller in this repo has at some
    # point read as "no rows".
    src = {str(x.get(keycol)): x for x in paged(table, {'select': '*'})}
    recs = paged('public_receipts', {'select': '*',
                                     'source_table': f'eq.{recv_source}'})

    stale, conflict = [], []
    agree = both_null = orphan = drifted = 0
    for r in recs:
        s = src.get(str(r.get('source_id')))
        if s is None:
            orphan += 1
            continue
        if not ident(r, s):
            # The source row no longer describes this bet, so its grade says
            # nothing about this receipt in either direction.
            drifted += 1
            continue
        # Normalise BOTH sides. The first cut of this script normalised only
        # the source and compared it to the receipt's raw value, which turned
        # every title-cased 'Win' receipt into a fake CONFLICT — 571 of them on
        # prop_jerry_reads alone. public_receipts.result is not consistently
        # cased across writers (see feedback_result_column_is_title_case), so a
        # case-sensitive comparison here measures the casing, not the grade.
        exp = normalise(s.get('result'))
        got = normalise(r.get('result'))
        if exp is None and got is None:
            both_null += 1
        elif got is None:
            stale.append((r, s, exp))
        elif exp is not None and exp != got:
            conflict.append((r, exp, got))
        else:
            agree += 1

    print(f'\n=== {recv_source}')
    print(f'  receipts={len(recs)}  source rows={len(src)}')
    print(f'  agree={agree}  both ungraded={both_null}  orphan={orphan}')
    print(f'  DRIFTED (source row no longer the same bet) = {drifted}')
    print(f'  STALE (source graded, receipt NULL) = {len(stale)}')
    print(f'  CONFLICT (same bet, both graded, disagree)  = {len(conflict)}')
    for r, exp, got in conflict[:25]:
        print(f'    ⚠ CONFLICT receipt {r["id"]} {r["game_date"]} '
              f'src={r["source_id"]} {str(r.get("pick_label"))[:34]!r} '
              f'receipt={got} source={exp} — NOT patched, needs a human')

    patched = 0
    for r, s, exp in stale:
        body = {'result': exp}
        if s.get('graded_at'):
            body['graded_at'] = s['graded_at']
        if not apply:
            continue
        # PATCH by the receipt's own primary key. A filter on the composite
        # natural key needs URL-encoding and has eaten writes here before.
        pr = requests.patch(f'{SB}/rest/v1/public_receipts?id=eq.{r["id"]}',
                            headers=H_WRITE, json=body, timeout=30)
        if pr.status_code in (200, 204):
            patched += 1
        else:
            # A PATCH naming a column the table lacks drops the WHOLE patch
            # with PGRST204 — so a failure here is loud, never swallowed.
            print(f'    ⚠ patch receipt {r["id"]} {pr.status_code}: '
                  f'{pr.text[:200]}')
    if apply:
        print(f'  patched {patched}/{len(stale)}')
    elif stale:
        print(f'  would patch {len(stale)} (dry-run)')
        for r, s, exp in sorted(stale, key=lambda t: str(t[0]['game_date']))[-10:]:
            print(f'    {r["game_date"]} receipt={r["id"]} '
                  f'src={r["source_id"]} NULL -> {exp}')
    return {'stale': len(stale), 'conflict': len(conflict), 'patched': patched}


# ══ 2026-10-22 · THE OTHER HALF OF public_receipts WAS NEVER SYNCED ══
# Andy: "seeing Laker game on sweat card as undragded, when they covered the
# 5.5".
#
# The Lakers read graded Win, refresh_card_grades wrote Win into the card,
# and the Sweat Card RECEIPT still said NULL. Cause: public_receipts carries
# five source_table values and SOURCES above covers three. The rest were
# graded in their own home and the receipt never picked it up:
#
#   source_table                 10-21 ungraded   covered before?
#   jerry_reads                        0           yes
#   ledger_snapshots                   0           yes
#   prop_jerry_reads                   0           yes
#   jerry_cache.sweat_card             1           NO  <- the Lakers
#   jerry_cache.sharp_card             1           NO
#   daily_best_bet_history             2           NO  <- POTD, graded Win
#   line_snapshot (fade)               1           NO
#   daily_degen                        1           NO
#
# The comment above SOURCES says jerry_cache.* "is a cache blob rather than a
# table", which is true and is why it needs its own pass rather than a row in
# that dict: the grade lives inside a JSON list keyed by date, not in a
# column.
#
# TWO LISTS IN ONE BLOB, ONE OF THEM STALE. sweat_card_<date>.data holds the
# same pick in `football_picks` AND in `unified_top_picks`, and on 10-21 the
# Lakers entry had result='Win' in the first and NO result in the second.
# refresh_card_grades writes football_picks. Reading the wrong list would
# have reported the pick as still ungraded and looked like this fix failed.
# sharp_card is deliberately NOT here. Its blob
# (jerry_cache.sharp_card_<date>.data) holds `items[]` with conviction, line,
# odds, pick, tier - and NO result field at all. There is nothing to read, so
# listing it would make this pass examine 300+ receipts and grade none,
# looking like a working sync. Those receipts are filled by
# reconcile_receipt_results copying the grade from a sibling surface that
# published the same bet, which is how the 10-21 Memphis sharp_card receipt
# got its WIN.
BLOB_SOURCES = {
    'jerry_cache.sweat_card': 'sweat_card',
}

# ══ 2026-10-22 · SOURCES THAT ARE REAL TABLES BUT NOT KEYED ON `id` ══
# The comment above SOURCES says "daily_degen / daily_dawg have no `id`
# column at all", which is true and was treated as the end of the matter.
# They do have a `result` column and they ARE keyed - just not on id:
#
#   daily_degen   receipt source_id = the GAME DATE   ('2026-10-21')
#   daily_dawg    receipt source_id = 'dotd:<game_id>'
#
# Both tables are almost fully graded (daily_degen 165 of 166, daily_dawg
# 132 of 132), and every one of the 29 ungraded receipts pointing at them
# has a GRADED source row. They were never unreachable, only unmapped.
#
# (keycol, prefix to strip from source_id, label column or None)
KEYED_SOURCES = {
    'daily_degen': ('game_date', '', None),
    'daily_dawg':  ('game_id', 'dotd:', 'team'),
}


def _same_bet(receipt_label, source_label) -> bool:
    """Do these two strings name the SAME bet?

    Strips the parenthetical each surface decorates its label with
    ('(Jerry 85/100)', '(resolver STRONG)') and ignores punctuation and case,
    so 'Boston Red Sox ML (resolver STRONG)' matches 'Boston Red Sox ML'.
    An empty source label matches nothing, so the receipt is left alone
    rather than graded from a bet we cannot identify.
    """
    def _n(x):
        x = re.sub(r'\s*\([^)]*\)', '', str(x or '')).strip().lower()
        return re.sub(r'[^a-z0-9. ]', '', x).strip()
    a_, b_ = _n(receipt_label), _n(source_label)
    return bool(a_) and bool(b_) and a_ == b_


def sync_blob_sources(apply: bool) -> dict:
    """Grade card/POTD receipts from the places those grades actually live.

    Keyed exactly as the receipt stores it, so no guessing:
      * card receipts  source_id = '<market>:<game_id>'  e.g. 'rl:401909092'
      * POTD receipts  source_id = 'potd:<bet_date>'
    """
    stale, conflict, patched, drift = [], 0, 0, 0
    recs = [r for r in paged('public_receipts', {'select': '*'})
            if r.get('source_table') in BLOB_SOURCES
            or r.get('source_table') in KEYED_SOURCES
            or r.get('source_table') == 'daily_best_bet_history']
    # One fetch per distinct date, not per receipt.
    blobs: dict[str, dict] = {}
    potd: dict[str, str] = {}
    potd_lean: dict[str, str] = {}
    keyed: dict[str, dict] = {}
    for _st in {r.get('source_table') for r in recs} & set(KEYED_SOURCES):
        _kc, _pfx, _lbl = KEYED_SOURCES[_st]
        keyed[_st] = {}
        for _row in paged(_st, {'select': '*'}):
            _k = str(_row.get(_kc) or '')[:10] if _kc.endswith('date')                 else str(_row.get(_kc) or '')
            if _k:
                keyed[_st].setdefault(_k, _row)
    for r in recs:
        gd = str(r.get('game_date'))[:10]
        st = r.get('source_table')
        if st in BLOB_SOURCES and (key := f'{BLOB_SOURCES[st]}_{gd}') not in blobs:
            rows = paged('jerry_cache', {'select': 'data',
                                         'cache_key': f'eq.{key}'})
            blobs[key] = (rows[0].get('data') or {}) if rows else {}
    for r in recs:
        if r.get('source_table') in KEYED_SOURCES:
            st = r['source_table']
            kc, pfx, lblcol = KEYED_SOURCES[st]
            sid = str(r.get('source_id') or '')
            if pfx and sid.startswith(pfx):
                sid = sid[len(pfx):]
            if kc.endswith('date'):
                sid = sid[:10]
            srow = (keyed.get(st) or {}).get(sid) or {}
            src_result = srow.get('result')
            # No label column means no identity check is possible. That is
            # acceptable for daily_degen ONLY because its receipt is the
            # whole slip ('daily_degen (3 legs)') keyed by date - there is
            # one per day and nothing to confuse it with. daily_dawg DOES
            # carry `team`, so it gets checked.
            src_lean = srow.get(lblcol) if lblcol else r.get('pick_label')
        elif r.get('source_table') == 'daily_best_bet_history':
            sid = str(r.get('source_id') or '')
            day = sid.split(':', 1)[1] if ':' in sid else str(
                r.get('game_date'))[:10]
            if day not in potd:
                # select='*', not a named list. Naming 'graded_at' here
                # returned 400 on EVERY POTD fetch - that column does not
                # exist on this table - which is the exact trap the comment
                # above sync_source warns about, and I walked into it the
                # same day I read it.
                rows = paged('daily_best_bet_history',
                             {'select': '*', 'bet_date': f'eq.{day}'})
                potd[day] = (rows[0].get('result') if rows else None)
                potd_lean[day] = (rows[0].get('lean') if rows else None)
            src_result = potd.get(day)
            src_lean = potd_lean.get(day)
        else:
            src_lean = None
            key = f'{BLOB_SOURCES[r["source_table"]]}_{str(r.get("game_date"))[:10]}'
            blob = blobs.get(key) or {}
            # football_picks ONLY - see the stale-list note above.
            want = str(r.get('source_id') or '')
            src_result = None
            for x in (blob.get('football_picks') or []):
                if not isinstance(x, dict):
                    continue
                if f'{x.get("type")}:{x.get("game_id")}' == want:
                    src_result = x.get('result')
                    src_lean = x.get('label')
                    break
            if src_result is None:
                for x in (blob.get('top_8') or []):
                    if isinstance(x, dict) and str(x.get('label') or '') == \
                            str(r.get('pick_label') or ''):
                        src_result = x.get('result')
                        src_lean = x.get('label')
                        break
        exp = _RMAP.get(str(src_result or '').strip().lower())
        if not exp:
            continue
        # ══ 2026-10-22 · THE IDENTITY CHECK THIS PASS WAS MISSING ══
        # sync_source has `ident()` for exactly this and its comment says
        # why: "The source row no longer describes this bet, so its grade
        # says nothing about this receipt in either direction." I wrote this
        # pass without one and it reported DRIFT as CONFLICT.
        #
        # The POTD gets replaced during the day (that is what the
        # best_bet_<date>_<sport> archive rows are), and the receipt keeps
        # the original pick while daily_best_bet_history moves on. So the
        # two rows end up describing DIFFERENT BETS on the same date:
        #
        #   10-15  receipt 'Cleveland Guardians ML' = WIN
        #          source  'Los Angeles Dodgers ML' = LOSS
        #   10-05  receipt 'No Play (discipline pass)' = NO_ACTION
        #          source  'Freddy Peralta Over 13.5 Outs' = WIN
        #
        # Neither is wrong. They are not the same bet. Measured over all 267
        # POTD-sourced receipts: 244 agree, 20 drift, and only 3 are a real
        # same-bet disagreement - so without this guard two thirds of what it
        # called a conflict was noise, and the 3 that matter would have been
        # buried in it.
        if not _same_bet(r.get('pick_label'), src_lean):
            drift += 1
            continue
        have = _RMAP.get(str(r.get('result') or '').strip().lower())
        if have is None:
            stale.append((r, exp))
        elif have != exp:
            conflict += 1
            print(f'    ⚠ CONFLICT receipt {r["id"]} {r.get("game_date")} '
                  f'{r.get("source_table")} {r.get("pick_label")!r} '
                  f'receipt={have} source={exp} — NOT patched')
    print(f'  blob/POTD sources: {len(recs)} receipt(s) examined, '
          f'{len(stale)} stale, {conflict} conflict, {drift} drift '
          f'(receipt and source name DIFFERENT bets - the POTD was '
          f'replaced after the receipt was written; neither grade is wrong)')
    for r, exp in stale[-10:]:
        print(f'    {r["game_date"]} receipt={r["id"]} '
              f'{r.get("source_table")} {str(r.get("pick_label"))[:28]!r} '
              f'NULL -> {exp}')
    if apply:
        for r, exp in stale:
            pr = requests.patch(
                f'{SB}/rest/v1/public_receipts?id=eq.{r["id"]}',
                headers=H_WRITE, json={'result': exp}, timeout=30)
            if pr.status_code in (200, 204):
                patched += 1
            else:
                print(f'    ⚠ patch receipt {r["id"]} {pr.status_code}: '
                      f'{pr.text[:200]}')
        print(f'  patched {patched}/{len(stale)}')
    return {'stale': len(stale), 'conflict': conflict, 'patched': patched}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', choices=list(SOURCES) + ['all'], default='all')
    ap.add_argument('--apply', action='store_true',
                    help='without this the script only measures')
    a = ap.parse_args()

    targets = list(SOURCES) if a.source == 'all' else [a.source]
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.')
    tot = {'stale': 0, 'conflict': 0, 'patched': 0}
    for t in targets:
        r = sync_source(t, a.apply)
        for k in tot:
            tot[k] += r[k]
    # 2026-10-22: the blob/POTD sources, which SOURCES cannot express.
    # Runs on --source all only; naming one tabular source means you want
    # that source.
    if a.source == 'all':
        rb = sync_blob_sources(a.apply)
        for k in tot:
            tot[k] += rb[k]
    print(f'\nTOTAL  stale={tot["stale"]}  conflict={tot["conflict"]}  '
          f'patched={tot["patched"]}')
    if tot['conflict']:
        print('A CONFLICT means the receipt and its source carry DIFFERENT '
              'grades. Nothing was patched for those — resolve them by hand.')


if __name__ == '__main__':
    main()
