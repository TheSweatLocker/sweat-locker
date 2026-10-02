"""Settle prop receipts from the box score, so they never depend on a
mutable table again.

THE PROBLEM (measured 2026-09-25)
public_receipts is the immutable published-pick ledger, but prop receipts
inherit their result from prop_jerry_reads via source_id. Those source rows
are DELETED by cleanup_stale_coverage_props.py and dedupe_stale_prop_lines.py,
and mlb_pipeline_props is pruned the same way. On 2026-09-23 there were 229
hits_under receipts across 216 players and only 44 hits_under props left in the
source table.

So an immutable ledger inherits from a mutable, pruned one. When the source is
cleaned up the receipt is stranded at result=NULL forever — 618 of them across
09-22..09-24, of which 451 have no source row anywhere. Matching on a natural
key instead of source_id was tried first and recovered only 17%, because the
rows are gone, not renumbered.

THE FIX (Andy: "Option A")
A receipt already carries everything needed to settle itself: player_name,
prop_type, pick_line, pick_side, game_date. This grades from the MLB box score
and never consults prop_jerry_reads or mlb_pipeline_props at all. Once a
receipt is published it can always be settled, whatever gets pruned later.

Reuses grade_prop_jerry_reads' MLB API layer by import rather than copying it —
this repo has been bitten repeatedly by a second copy of a parser drifting from
the first.

THE FADE TRAP — found by --validate before anything was written.
A receipt's pick_side is NOT always the side we backed. On a FADE read we bet
the OPPOSITE side, but the receipt still records the prop's side: all 72 graded
FADE prop receipts since 09-15 display the prop side while their stored result
is for the faded side. So "Jose Quintana UNDER 10.5" carries the W/L of the
OVER. (That mislabelling is a separate receipts-integrity bug in the writer —
users are shown the wrong side — and is not fixed here.)

A settler therefore cannot assume pick_side is the backed side. But the source
read may be deleted, so the verdict is not always knowable. The guard:

  * Families that have NEVER been faded settle directly — pick_side is
    unambiguous. Measured over prop_jerry_reads history rather than hardcoded,
    because the fadeable set changes: hits_under has 0 FADE in 1,229 reads,
    while ha_over is 22.6% FADE.
  * Families that CAN be faded require the source read to confirm the verdict.
    If that row is gone, the receipt is skipped as fade_ambiguous rather than
    settled on a coin flip.

That keeps the 439 orphaned hits_under receipts settleable while refusing to
guess on the ~2% of families where a wrong guess would invert a published
result.

VALIDATE BEFORE TRUSTING
--validate re-settles receipts that ALREADY have a result and reports whether
this agrees with them. Run it first. A settler that disagrees with our own
published record is worse than no settler, and this is the only way to know
before writing anything.

    python settle_prop_receipts.py --validate --days 14
    python settle_prop_receipts.py --days 14              # dry run
    python settle_prop_receipts.py --days 14 --commit
"""
import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# The box-score layer, imported rather than duplicated.
from grade_prop_jerry_reads import (       # noqa: E402
    _MLB_STAT_MAP, _lookup_pid, _fetch_stat_for_date,
)

_BATTER_PROPS = {'hits_over', 'hits_under'}

# ══ 2026-10-02 · NFL WAS NEVER SETTLEABLE HERE ══
# settle() was MLB-only: every path ran through _MLB_STAT_MAP and the MLB
# player-id lookup, so an NFL receipt was settled as if it were an MLB pitcher
# prop and died at stat_unmapped / no_player_id / no_boxscore. Measured
# 2026-10-02: 88 ungraded NFL prop receipts, all 16 of their prop types mapping
# cleanly onto nfl_player_stats columns, all 88 carrying a line and a clean
# OVER/UNDER side. They were unreachable purely because no NFL branch existed.
#
# nfl_player_stats is nflverse box-score data and is keyed by (season, week),
# not game_date — so the date has to be resolved to a week first. That lookup
# EXCLUDES anything before 2026-09-09 because nfl_game_context also holds
# PRESEASON games reusing the same week numbers; without the filter, weeks 2-5
# resolve to August dates. Same trap as the NHL preseason record.
_NFL_STAT_MAP = {
    'reception_yds': 'receiving_yards',
    'receptions': 'receptions',
    'targets': 'targets',
    'reception_tds': 'receiving_tds',
    'rush_yds': 'rushing_yards',
    'rush_attempts': 'carries',
    'rush_tds': 'rushing_tds',
    'pass_yds': 'passing_yards',
    'pass_attempts': 'attempts',
    'pass_completions': 'completions',
    'pass_tds': 'passing_tds',
    'pass_interceptions': 'interceptions',
}
_NFL_SEASON_START = '2026-09-09'      # feedback_nfl_2026_week1_anchor
_nfl_week_by_date: dict | None = None
_nfl_stats_cache: dict = {}


def _nfl_norm(name: str) -> str:
    import re
    import unicodedata as ud
    n = ud.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    n = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b\.?', '', n.lower())
    n = re.sub(r'[^a-z0-9\s]', '', n)
    return re.sub(r'\s+', ' ', n).strip()


def _nfl_week_for_date(gd: str):
    """(season, week) for an NFL game date, or (None, None)."""
    global _nfl_week_by_date
    if _nfl_week_by_date is None:
        _nfl_week_by_date = {}
        for row in page('nfl_game_context',
                        {'select': 'game_date,week,season',
                         'game_date': f'gte.{_NFL_SEASON_START}'}):
            d = str(row.get('game_date') or '')[:10]
            if d and row.get('week') is not None:
                _nfl_week_by_date[d] = (row.get('season'), row['week'])
    return _nfl_week_by_date.get(gd, (None, None))


def _nfl_actual(name: str, gd: str, col: str):
    """The player's actual value for `col` in the game on `gd`, or None."""
    season, week = _nfl_week_for_date(gd)
    if week is None:
        return None
    key = (season, week)
    if key not in _nfl_stats_cache:
        idx = {}
        for row in page('nfl_player_stats',
                        {'select': '*', 'season': f'eq.{season}',
                         'week': f'eq.{week}'}):
            idx.setdefault(_nfl_norm(row.get('player_name')), row)
        _nfl_stats_cache[key] = idx
    row = _nfl_stats_cache[key].get(_nfl_norm(name))
    if not row:
        return None
    v = row.get(col)
    return None if v is None else float(v)


def fadeable_families(sport: str, since: str) -> set:
    """Prop families that have EVER carried a FADE verdict.

    Derived from history rather than hardcoded — the fadeable set moves as
    discipline rules change, and a stale constant here would silently start
    mis-settling a family the day it becomes fadeable.
    """
    rows = page('prop_jerry_reads', {
        'select': 'prop_type,call_verdict', 'sport': f'eq.{sport}',
        'game_date': f'gte.{since}', 'call_verdict': 'eq.FADE'})
    return {str(x.get('prop_type') or '').lower() for x in rows}


def source_verdicts(recs: list) -> dict:
    """source_id -> (call_verdict, direction) for rows that still exist."""
    # Numeric ids only. Sharp-card receipts carry a composite string source_id
    # ("prop:Arizona Diamondbacks @ Colorado Rockies|Merrill Kelly Over 15.5
    # outs_over|15.5"), and feeding that to a bigint `id=in.()` filter 400s the
    # whole batch — taking down grading for every receipt in it, not just that
    # row.
    ids = sorted({str(r['source_id']) for r in recs
                  if r.get('source_id') and str(r['source_id']).isdigit()})
    out = {}
    for i in range(0, len(ids), 80):
        batch = ids[i:i + 80]
        for x in page('prop_jerry_reads',
                      {'select': 'id,call_verdict,direction',
                       'id': f'in.({",".join(batch)})'}):
            out[str(x['id'])] = ((x.get('call_verdict') or '').upper(),
                                 (x.get('direction') or '').lower())
    return out


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ERROR {r.status_code}: {(r.text or "")[:200]}')
            sys.exit(2)
        b = r.json()
        if not isinstance(b, list):
            print(f'  ERROR: {str(b)[:200]}')
            sys.exit(2)
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def settle(rec: dict, fadeable: set, verdicts: dict) -> tuple:
    """Return (result, actual, reason). result is None when unsettleable.

    Settles the side we actually BACKED, which is pick_side except on a FADE,
    where the receipt recorded the prop's side and we bet the other one.
    """
    pt = (rec.get('prop_type') or '').lower()
    side = (rec.get('pick_side') or '').strip().lower()

    # ══ 2026-10-02 · THE VERDICT IS ON THE RECEIPT ══
    # This used to resolve the verdict only from the source read, and refuse
    # when that row was gone. But the receipt carries audit.call_verdict
    # itself: measured today, ALL 184 ungraded fadeable-family MLB receipts
    # have it, and they were being held pending as "unknowable" while the
    # answer sat in their own audit blob. That is the same mistake the module
    # was written to fix — depending on a mutable table for something the
    # immutable record already holds.
    #
    # SOURCE first, receipt second — the opposite of my first cut, and the
    # validator caught it. Of 21 NFL receipts stored NO_ACTION that a
    # receipt-first rule would have graded Win/Loss, 14 had a receipt verdict
    # of LEAN/STRONG while the source read said PASS. The receipt's audit blob
    # is stamped at publish time and can predate the final verdict, so it is
    # the weaker witness. It is still the ONLY witness once the source row is
    # pruned, which is the case this whole function exists for.
    v = verdicts.get(str(rec.get('source_id')))
    verdict = v[0] if v and v[0] else None
    if verdict is None:
        _audit = rec.get('audit') if isinstance(rec.get('audit'), dict) else {}
        verdict = str(_audit.get('call_verdict') or '').upper() or None

    # PASS was never a bet. 153 of those 184 carry PASS, and grading them
    # Win/Loss would invent a wagered position; NO_ACTION is what the graded
    # rows for PASS reads already use.
    if verdict == 'PASS':
        return 'NO_ACTION', None, 'ok'

    # A FADE flips the backed side whatever the family's history says. The
    # `fadeable` set is derived from past verdicts, so it answers "could a
    # missing verdict have been a FADE?" — it must NOT gate the flip itself,
    # or a FADE in a family with no prior FADEs would settle the wrong side.
    if verdict == 'FADE':
        side = 'over' if side == 'under' else 'under'
    elif verdict is None and pt in fadeable:
        # Family can be faded and no verdict anywhere — the backed side is
        # genuinely unknowable. Refuse rather than settle a coin flip.
        return None, None, 'fade_ambiguous_source_gone'
    line = rec.get('pick_line')
    name = rec.get('player_name') or ''
    gd = str(rec.get('game_date') or '')[:10]
    if line is None:
        return None, None, 'no_line'
    try:
        line = float(line)
    except (TypeError, ValueError):
        return None, None, 'bad_line'
    if side not in ('over', 'under'):
        return None, None, f'side_unmapped:{side or "none"}'

    sport = str(rec.get('sport') or 'MLB').upper()
    if sport == 'NFL':
        # prop_type is <family>_<side>; the family carries the stat.
        fam = pt.rsplit('_', 1)[0] if pt.endswith(('_over', '_under')) else pt
        col = _NFL_STAT_MAP.get(fam)
        if not col:
            return None, None, f'stat_unmapped:{pt}'
        actual = _nfl_actual(name, gd, col)
        if actual is None:
            # Absent from the week's box score — did not play, or the name does
            # not normalise onto an nflverse row. Either way, not settleable.
            return None, None, 'no_boxscore'
    elif pt in _BATTER_PROPS:
        pid = _lookup_pid(name, is_pitcher=False)
        if not pid:
            return None, None, 'no_player_id'
        actual = _fetch_stat_for_date(pid, 'hits', gd, group='hitting')
        if actual is None:
            return None, None, 'no_boxscore'
    else:
        stat = _MLB_STAT_MAP.get(pt)
        if not stat:
            return None, None, f'stat_unmapped:{pt}'
        pid = _lookup_pid(name, is_pitcher=True)
        if not pid:
            return None, None, 'no_player_id'
        actual = _fetch_stat_for_date(pid, stat, gd, group='pitching')
        if actual is None:
            # Did not play / scratched / API had nothing for that date.
            return None, None, 'no_boxscore'

    if actual == line:
        return 'Push', actual, 'ok'
    hit = (actual > line) if side == 'over' else (actual < line)
    return ('Win' if hit else 'Loss'), actual, 'ok'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--validate', action='store_true',
                    help='re-settle ALREADY-GRADED receipts and report agreement')
    ap.add_argument('--limit', type=int, default=0, help='cap rows (0 = all)')
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()

    hi = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    lo = hi - timedelta(days=args.days)
    # source_id MUST be selected — the FADE guard looks it up, and PostgREST
    # returns no error for a column simply left out of the select, so omitting
    # it made every receipt look like its source was deleted and skipped the
    # whole pitcher set as fade_ambiguous.
    # `audit` is REQUIRED, not optional: settle() falls back to
    # audit.call_verdict when the source read is pruned, which is the only way
    # a fadeable-family receipt is settleable at all. Leaving it out of this
    # select made rec.get('audit') return None for every row, so the fallback
    # silently did nothing and 182 MLB receipts still came back
    # fade_ambiguous_source_gone. A missing column does not error here — it
    # just reads as absent, which is the whole shape of that trap.
    q = {'select': 'id,sport,game_date,player_name,prop_type,pick_side,'
                   'pick_line,result,source_id,audit',
         'surface': 'eq.prop_jerry', 'sport': f'eq.{args.sport}',
         'game_date': f'gte.{lo}', 'order': 'game_date.asc'}
    q['result'] = 'not.is.null' if args.validate else 'is.null'
    recs = [r for r in page('public_receipts', q)
            if str(r.get('game_date') or '')[:10] <= hi.isoformat()]
    if args.limit:
        recs = recs[:args.limit]

    mode = 'VALIDATE' if args.validate else ('APPLY' if args.commit else 'DRY')
    print(f'=== settle_prop_receipts · {args.sport} · {lo}..{hi} · {mode} ===')
    print(f'  {len(recs)} receipt(s)')
    if not recs:
        return

    fadeable = fadeable_families(args.sport, (hi - timedelta(days=120)).isoformat())
    verdicts = source_verdicts(recs)
    print(f'  fadeable families: {sorted(fadeable) or "none"}')
    print(f'  source reads still present: {len(verdicts)}/{len(recs)}')

    agree = Counter()
    reasons = Counter()
    patches = []
    t0 = time.time()
    for i, r in enumerate(recs, 1):
        res, actual, why = settle(r, fadeable, verdicts)
        reasons[why] += 1
        if args.validate:
            if res is None:
                continue
            stored = str(r.get('result') or '')
            # NO_ACTION/Void are bookkeeping outcomes this settler does not
            # produce; compare only where the stored value is a real outcome.
            if stored.lower() in ('win', 'loss', 'push'):
                agree['agree' if stored.lower() == res.lower() else 'DISAGREE'] += 1
                if stored.lower() != res.lower() and agree['DISAGREE'] <= 8:
                    print(f'    DISAGREE id={r["id"]} {r["player_name"]} '
                          f'{r["prop_type"]} {r["pick_side"]} {r["pick_line"]} '
                          f'-> stored={stored} settled={res} (actual={actual})')
            else:
                agree[f'stored_{stored[:12]}'] += 1
        elif res is not None:
            patches.append((r['id'], res))
        if i % 100 == 0:
            print(f'    …{i}/{len(recs)}  ({time.time()-t0:.0f}s)')

    print(f'\n  outcomes: {dict(reasons)}')
    if args.validate:
        a, d = agree.get('agree', 0), agree.get('DISAGREE', 0)
        n = a + d
        print(f'\n  AGREEMENT vs our published record: {a}/{n}'
              + (f'  ({a/n*100:.1f}%)' if n else ''))
        for k, v in agree.items():
            if k.startswith('stored_'):
                print(f'    {k}: {v}')
        if n and d == 0:
            print('\n  ✓ settler reproduces every graded receipt. Safe to apply.')
        elif d:
            print(f'\n  ⚠ {d} disagreement(s). DO NOT APPLY until explained — a '
                  f'settler that contradicts the published record is worse than '
                  f'none.')
        return

    print(f'  settleable: {len(patches)} of {len(recs)}')
    if not args.commit:
        print('\n  DRY RUN — add --commit to write.')
        return
    ok = 0
    for rid, res in patches:
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, params={'id': f'eq.{rid}'},
                            json={'result': res})
        ok += pr.status_code in (200, 204)
    print(f'  patched {ok}/{len(patches)}')


if __name__ == '__main__':
    main()
