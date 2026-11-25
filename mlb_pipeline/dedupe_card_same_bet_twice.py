"""One bet must not occupy two slots on the home card.

== 2026-10-31 . MARSHALL IS ON THE SWEAT CARD TWICE ======================
ANDY: "is eveything that us rfacing to user correct and we stand ebhine
right now thats the imporant part right onw"

jerry_cache.sweat_card_2026-10-31 top_8 renders:

    1. Marshall ML (Jerry 81/100)   odds=-145  tier=PRIME   type=POTD
    4. Marshall ML                  odds=-142  tier=STRONG  type=ml

Same team, same market, same game (ncaaf_20261031_Marshall_Old Dominion).
The Sweat Card is "yesterday recap + POTD + Dawg + composite, top-8
curated", so the POTD and the composite are two different components -- and
when the composite independently lands on the POTD's game, one bet takes
two of the eight slots. A user reads that as two plays. Worse, both have
their own receipt:

    3982125  sweat_card  potd  PRIME   -145
    3982126  sweat_card  ml    STRONG  -142

so a single Marshall result moves the card record by TWO, in whichever
direction it lands. That is the same double-count class as the duplicate
receipts in repair_reconciled_card_item_identity, reached by a different
route: there the dedupe key was the wrong SHAPE, here there is no dedupe
between card components at all.

WHAT IT KEEPS. The POTD. It is the higher tier (PRIME over STRONG), it is
the flagship labelled play, and it has its own surface and its own
receipt lineage. The composite copy is the redundant one.

The play is NOT withdrawn: Marshall stays on the card, stays on The Sharp
(3982398, a separate surface with its own denominator), and stays in the
record once, at the POTD's price. Only the second copy of the same bet
goes.

KEYED ON game_id, NOT ON THE LABEL. "Marshall ML (Jerry 81/100)" and
"Marshall ML" are different strings, which is exactly why every
label-based duplicate check in this repo walked straight past this
(feedback_dedupe_key_shape_must_match). The anchoring pass in
repair_reconciled_card_item_identity put game_id back on these items,
which is what makes this check possible at all.

A label-keyed check would also have been WRONG in the other direction: on
10-03 two different games both carry the label 'under 7.0', with distinct
source_id hashes. Same text, different bets. game_id separates them.

USAGE
  python dedupe_card_same_bet_twice.py --date 2026-10-31
  python dedupe_card_same_bet_twice.py --date 2026-10-31 --apply
  python dedupe_card_same_bet_twice.py --days 30
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H_R = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
H_W = dict(H_R)
H_W.update({'Content-Type': 'application/json',
            'Prefer': 'return=representation'})

from card_summaries import CARD_LISTS as BLOBS   # includes football_picks
VOIDS = ('VOID', 'VOIDED')
#: higher wins the slot
TIER_RANK = {'PRIME': 5, 'STRONG': 4, 'LEAN': 3, 'COVERAGE': 2, 'PASS': 1}


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return None
    return v if isinstance(v, (dict, list)) else None


def get(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H_R, timeout=90,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:140]))
        return None
    return r.json()


def market(v):
    m = str(v or '').lower()
    return {'potd': 'ml', 'moneyline': 'ml', 'spread': 'rl'}.get(m, m)


#: == 2026-11-25 . 'potd' IS A CARD SLOT, NOT A MARKET ====================
#: `market()` maps 'potd' -> 'ml'. That was right for the 10-31 Marshall
#: case (an ML POTD beside an ML composite copy) and WRONG in general: it
#: makes a TOTALS potd key as a moneyline, so `Under 213.5` in slot 1 keyed
#: ('ml','under') while the identical `Under 213.5` in slot 3 keyed
#: ('total','under'). Two key spaces, never compared, and on 2026-11-24 the
#: card's only winner was shown twice and counted twice -- 2W-5L over 7
#: slots where the five distinct bets went 1W-4L.
#:
#: A slot name cannot carry a market. The market is read off the LABEL's
#: SHAPE instead, which is what the label actually commits to
#: ([[feedback_parse_a_pick_by_shape_not_vocabulary]],
#:  [[feedback_a_display_label_became_an_identity_key]]).
#: `market()` is left untouched for its other callers.
_SLOT_TYPES = ('potd', 'dotd', 'lock', 'dawg', 'best bet', 'bestbet')


def market_of(it):
    """The market this item actually bets, inferred by shape for slot items."""
    t = str(it.get('type') or '').strip().lower()
    lbl = norm_lbl(it.get('pick') or it.get('label'))
    lbl = re.sub(r'\s*\([^)]*\)\s*$', '', lbl).strip()
    if t in _SLOT_TYPES:
        if re.match(r'^(over|under)\b', lbl):
            return 'total'
        if re.search(r'\bml$', lbl):
            return 'ml'
        if re.search(r'[+-]\d+(\.\d+)?$', lbl):
            return 'rl'
        return market(t)          # unparseable: fall back to the old map
    return market(t)


def side(lbl):
    """The team/side at the head of the label, parenthetical stripped."""
    s = ' '.join(str(lbl or '').split())
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+(ML|[+-]?\d+(\.\d+)?)$', '', s)
    return s.strip().lower()


def rank(it):
    return TIER_RANK.get(str(it.get('tier') or '').upper(), 0)


def norm_lbl(v):
    return ' '.join(str(v or '').split()).lower()


def bet_key(it):
    """The identity of the bet, or None when it cannot be judged."""
    mk = market(it.get('type'))
    sd = side(it.get('pick') or it.get('label'))
    if mk in ('ml', 'rl') and sd and sd not in ('over', 'under'):
        return ('SIDE', str(it.get('sport') or ''), mk, sd)
    gid = it.get('game_id')
    return (str(gid), mk, sd) if gid else None


def run_date(date: str, apply: bool) -> int:
    key = 'sweat_card_%s' % date
    rows = get('jerry_cache', select='data', cache_key='eq.' + key)
    if rows is None:
        return 1
    if not rows:
        return 0
    blob = jd(rows[0].get('data'))
    if not isinstance(blob, dict):
        return 0

    dropped_labels = []
    changed = False
    for name in BLOBS:
        items = blob.get(name)
        if not isinstance(items, list):
            continue
        # == CANONICAL GAME KEY, RESOLVED ACROSS THE WHOLE BLOB ==========
        # Keying "game_id when present, else the matchup string" is NOT one
        # key space: if one copy of a bet carries a game_id and the other
        # only a matchup string, they key differently and the pair survives
        # -- the same defect this pass already had, reached again. (It cost
        # the unified_top_picks Jenkins pair on the first attempt at this
        # fix.)
        #
        # So the matchup string is resolved to a game_id FIRST, using the
        # items in this blob that carry both, and every item is then keyed
        # on that one id. Only a matchup no item can resolve falls back to
        # the string itself ([[feedback_join_on_resolved_names_not_a_composed_key]]).
        gid_of_game = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            g = norm_lbl(it.get('game'))
            gi = it.get('game_id')
            if g and gi:
                gid_of_game.setdefault(g, str(gi))

        groups = collections.defaultdict(list)
        for it in items:
            if not isinstance(it, dict):
                continue
            mk = market(it.get('type'))
            sd = side(it.get('pick') or it.get('label'))
            gid = it.get('game_id')
            # == WHY THE SIDE AND NOT THE game_id ========================
            # The POTD item and its receipt both carry game_id=None, so a
            # game_id-only key puts the POTD in its own group and walks
            # past the exact collision this script exists for. Within ONE
            # date, ONE sport and ONE market, a team is a team: 'Marshall
            # ML (Jerry 81/100)' and 'Marshall ML' are the same bet.
            #
            # Restricted to ml/rl with a team-shaped side, because on a
            # TOTAL the side is 'over'/'under', which two different games
            # share -- 10-03 carries two 'under 7.0' with distinct
            # source_id hashes: same text, different bets. Those keep the
            # game_id key, which separates them correctly.
            #
            # == 2026-11-25 . A TOTALS POTD MATCHED NEITHER BRANCH =======
            # ANDY: "What was the deal yesterday?"
            #
            # 2026-11-24 top_8 rendered `Under 213.5` TWICE -- slot 1 as the
            # POTD and slot 3 as a STRONG total, same game (Detroit Pistons
            # @ Orlando Magic), and it WON, so the card's one winner was
            # counted twice. The card read 2W-5L on 7 slots; the five
            # distinct bets went 1W-4L.
            #
            # The SIDE branch above is restricted to ml/rl for a correct
            # reason (two different games both carry 'under 7.0'), and the
            # game_id branch needs a game_id the POTD item does not have --
            # item 1's keys are conviction/game/icon/label/.../type with NO
            # game_id at all. So a totals POTD fell through BOTH branches
            # and was never put in a group, which is not a dedupe that
            # failed to match: it is an item the dedupe never looked at
            # ([[feedback_a_gate_reading_an_unselected_column_always_passes]]).
            #
            # The third branch keys those on the MATCHUP STRING, which both
            # copies carry identically. It preserves the 10-03 case the
            # SIDE branch was narrowed for -- two 'under 7.0' in different
            # games have different `game` strings and stay apart -- and it
            # only ever fires for an item with no game_id, so it cannot
            # change how any already-keyed item is grouped.
            # ONE KEY SPACE. The previous version had three branches, and
            # two copies of one bet landed in DIFFERENT branches -- slot 3
            # had a game_id so it keyed (gid, market, side) while the POTD
            # copy had none and keyed on the side -- so they were never
            # compared. A dedupe whose key SHAPE varies per item cannot
            # dedupe ([[feedback_dedupe_key_shape_must_match]],
            # [[feedback_two_tables_two_key_spaces]]).
            #
            # The game is identified by its MATCHUP STRING, which every
            # card item carries ('Detroit Pistons @ Orlando Magic' on both
            # copies), falling back to game_id when the string is absent.
            # This keeps the 10-03 case the old SIDE branch was narrowed
            # for: two 'under 7.0' in different games have different
            # matchup strings and stay apart. The doubleheader guard below
            # still reads game_id, so a team playing twice on one date is
            # still protected.
            mk = market_of(it)
            _g = norm_lbl(it.get('game'))
            gkey = (str(gid) if gid
                    else gid_of_game.get(_g) or _g)
            if not gkey:
                # == KEEP THE 10-31 SIDE FALLBACK ========================
                # Replacing the old three branches with one key space
                # silently removed the SIDE branch's coverage of items
                # carrying NEITHER a game_id NOR a matchup string. The
                # 10-03 card has exactly that: 'Chicago White Sox ML' was
                # grouped by the old code and became UNKEYABLE under the
                # new key. A fix that repairs totals by un-fixing
                # moneylines is not a fix
                # ([[feedback_fix_every_writer_to_the_table]]).
                #
                # So an ml/rl item with a team-shaped side still keys on
                # (sport, market, side), exactly as before. The narrowing
                # to team-shaped sides is kept for its original reason:
                # on a TOTAL the side is 'over'/'under', which two
                # different games share.
                if mk in ('ml', 'rl') and sd and sd not in ('over', 'under'):
                    groups[('SIDE', str(it.get('sport') or ''),
                            mk, sd)].append(it)
                else:
                    print('  %s [%s] UNKEYABLE %r -- no matchup string, no '
                          'game_id, and not a team-shaped side; left alone'
                          % (date, name, it.get('pick') or it.get('label')))
                continue
            groups[(str(it.get('sport') or ''), gkey, mk, sd)].append(it)
        drop = []
        for k, grp in groups.items():
            if len(grp) < 2:
                continue
            # DOUBLEHEADER GUARD. A team really can appear twice on one
            # date on the same market -- MLB plays two. If two copies BOTH
            # name a game_id and the ids differ, they are two bets, not
            # one, and this script must not touch them.
            gids = {str(x.get('game_id')) for x in grp if x.get('game_id')}
            if len(gids) > 1:
                print('  %s [%s] %s: %d copies across %d DIFFERENT game_ids '
                      '%s -- left alone, that is a doubleheader not a '
                      'duplicate' % (date, name, k[-1], len(grp), len(gids),
                                     sorted(gids)))
                continue
            # == THE SLOT COPY IS THE ONE THAT STAYS =====================
            # Sorting on tier alone dropped the POTD and kept the composite
            # copy on 2026-11-24 (POTD tier=LEAN, composite tier=STRONG),
            # which inverts this script's own stated policy: "WHAT IT
            # KEEPS. The POTD. It is the flagship labelled play, and it has
            # its own surface and its own receipt lineage. The composite
            # copy is the redundant one."
            #
            # The flagship slot must survive the dedupe or the card loses
            # its headline play, so a slot item outranks a non-slot item
            # regardless of tier, and tier only breaks ties within a group
            # of the same kind.
            grp.sort(key=lambda x: (
                str(x.get('type') or '').strip().lower() in _SLOT_TYPES,
                rank(x)), reverse=True)
            keep, rest = grp[0], grp[1:]
            print('  %s [%s] %s %s %s : %d copies of one bet'
                  % (date, name, k[0], k[1], k[2], len(grp)))
            print('      KEEP %r tier=%s odds=%s'
                  % (keep.get('pick') or keep.get('label'), keep.get('tier'),
                     keep.get('odds')))
            for r in rest:
                print('      DROP %r tier=%s odds=%s'
                      % (r.get('pick') or r.get('label'), r.get('tier'),
                         r.get('odds')))
                dropped_labels.append((k, norm_lbl(r.get('pick')
                                                   or r.get('label'))))
            drop.extend(id(r) for r in rest)
        if drop:
            blob[name] = [it for it in items
                          if not (isinstance(it, dict) and id(it) in drop)]
            changed = True

    # ══ 2026-10-31 · PURGE THE LOSING VARIANT FROM EVERY LIST ════════
    # The duplicate is ACROSS lists, not only within one. On 10-31 the POTD
    # 'Marshall ML (Jerry 81/100)' and the composite 'Marshall ML' both sat
    # in top_8, and the composite ALSO sat in football_picks. Deduping each
    # list independently leaves the loser in football_picks, which is a
    # RECEIPT SOURCE -- public_receipt.sweat_card_rows reads
    # ('top_8', 'football_picks'). So backfill_public_receipts re-mints its
    # receipt and the reconciler puts the pick back on the card. That round
    # trip took three hours on 10-31 and undid both of this morning's
    # withdrawals.
    #
    # Matched on the bet KEY *and* the losing label, not the label alone:
    # 10-03 carries two 'under 7.0' on different games, and a label-only
    # purge would take both. A total keys on game_id, so a different game
    # is a different key and cannot be caught by this.
    if dropped_labels:
        losers = set(dropped_labels)
        for name in BLOBS:
            items = blob.get(name)
            if not isinstance(items, list):
                continue
            keep = []
            for it in items:
                if isinstance(it, dict) and (
                        bet_key(it),
                        norm_lbl(it.get('pick') or it.get('label'))
                ) in losers:
                    continue
                keep.append(it)
            if len(keep) != len(items):
                print('  %s [%s] purged %d losing variant(s) this list still '
                      'carried' % (date, name, len(items) - len(keep)))
                blob[name] = keep
                changed = True

    if not changed:
        return 0
    # BOTH summaries recomputed from what is LEFT, or the card still
    # reports a double-count it no longer renders -- and the CARD RESULT
    # line under the visible list reads from unified_summary, not
    # top_8_summary (card_summaries).
    from card_summaries import recompute as _recompute
    for _k, _o, _n in _recompute(blob):
        print('  %s %s: %s -> %s' % (date, _k, json.dumps(_o),
                                     json.dumps(_n)))
    blob['_same_bet_deduped_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    if not apply:
        return 0

    pr = requests.patch(SB + '/rest/v1/jerry_cache', headers=H_W, timeout=60,
                        params={'cache_key': 'eq.' + key},
                        json={'data': blob})
    ok = False
    back = pr.json() if pr.status_code in (200, 201) else []
    if back:
        b = jd(back[0].get('data')) or {}
        from card_summaries import PAIRS as _PAIRS
        ok = all(b.get(_sk) == blob.get(_sk) for _lk, _sk in _PAIRS) and all(
            len(b.get(_lk) or []) == len(blob.get(_lk) or [])
            for _lk, _sk in _PAIRS)
    print('  %s HTTP %s %s' % (date, pr.status_code,
                               'CONFIRMED' if ok else 'NOT CONFIRMED'))
    if not ok:
        return 1

    # the dropped copy has its own receipt and would still grade. Void it,
    # identity-gated on the label we actually dropped.
    for lbl in dropped_labels:
        rc = get('public_receipts',
                 select='id,pick_label,market,tier,pick_odds,result,surface',
                 game_date='eq.' + date, surface='eq.sweat_card',
                 pick_label='eq.' + str(lbl)) or []
        for x in rc:
            if str(x.get('result') or '').upper() in VOIDS:
                continue
            if x.get('result'):
                print('      receipt %s %r already graded %r -- LEFT ALONE, '
                      'a decided result is not mine to erase'
                      % (x['id'], lbl, x['result']))
                continue
            pv = requests.patch(SB + '/rest/v1/public_receipts', headers=H_W,
                                timeout=60, params={'id': 'eq.%s' % x['id']},
                                json={'result': 'VOID',
                                      'graded_at': dt.datetime.now(
                                          dt.timezone.utc).isoformat()})
            bk = pv.json() if pv.status_code in (200, 201) else []
            vok = bool(bk) and str(bk[0].get('result')) == 'VOID'
            print('      receipt %s %r -> VOID  HTTP %s %s'
                  % (x['id'], lbl, pv.status_code,
                     'CONFIRMED' if vok else 'NOT CONFIRMED'))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    anchor = dt.date.fromisoformat(
        a.date or (dt.datetime.now(dt.timezone.utc)
                   - dt.timedelta(hours=4)).date().isoformat())
    print('[%s] one bet, one slot' % ('APPLY' if a.apply else 'DRY RUN'))
    bad = 0
    for i in range(a.days):
        bad += run_date((anchor - dt.timedelta(days=i)).isoformat(), a.apply)
    if not a.apply:
        print('\nRe-run with --apply to write.')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
