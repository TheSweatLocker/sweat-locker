"""UFC jerry_reads grader (2026-08-09).

UFC calls in `jerry_reads` use:
  sport      = 'UFC'
  game_id    = 'ufc_YYYY-MM-DD_<fight_order>'
  call_market = 'fight' | 'pass'
  call_side  = 'A' (fighter_a wins) | 'B' (fighter_b wins)

Join key to `ufc_fight_results` is (event_date, FIGHTER NAMES) taken from
the read's own input_snapshot. `winner` in fight_results is 'A', 'B',
'draw' or null (no-contest / pending). We only grade decisive winners.

== 2026-10-19 - WHY THE JOIN IS BY NAME AND NOT BY fight_order ==
It used to be (event_date, fight_order) parsed out of the game_id, and
THE TWO FEEDS DO NOT NUMBER A CARD THE SAME WAY. Measured today over 95
joinable UFC reads, 16 disagree. Examples:

    2026-09-26 order 3   read: Alatengheili / Castaneda
                      results: Montel Jackson / Ricky Simon
    2026-10-10 order 8   read: Meerschaert / Julius Walker
                      results: Wellmaker / Tanzilovi

THE 09-22 NAME FIX COULD NOT CATCH THIS. That fix stopped comparing
call_side ('A'/'B') to results.winner ('a'/'b') because those letters
index different orderings, and graded by fighter NAME instead. But the
name it compared against came from whatever row fight_order returned -
so on a wrong row, the picked name is checked against the wrong fight's
winner and the comparison agrees with itself. A name check is only as
good as the row it reads.

COST: ufc_2026-10-10_8 is the read for Julius Walker. fight_order 8 in
the results table is Wellmaker vs Tanzilovi, so Walker was checked
against Tanzilovi and booked a LOSS. Walker won his actual fight. One
graded pick in nineteen, recorded backwards, and the published read
record was 12-7 when it was 13-6.

The 15 others were `pass` reads, which return NO_ACTION before any
lookup happens, so they were never affected.

Names have no ordering, and a fight is identified by WHO IS IN IT, so
this is the same lesson as the STL feed join: never join two feeds on a
raw index or a raw string either side can renumber. When the names
cannot resolve to exactly one fight the grader REFUSES and leaves the
row ungraded and visible, rather than falling back to the index that is
already known to be wrong.

Coverage audit 2026-08-09 found 11 ungraded UFC jerry_reads from 8/1
and 8/8 events, and no automated grader path — this fills that gap.

Usage:
  python grade_ufc_jerry_reads.py [--date YYYY-MM-DD] [--dry-run]
  python grade_ufc_jerry_reads.py --backfill-days 30
"""
from __future__ import annotations
import argparse, os, re, sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import json
import unicodedata

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

GAME_ID_RE = re.compile(r'^ufc_(\d{4}-\d{2}-\d{2})_(\d+)$')


def parse_gid(gid: str) -> tuple[str, int] | None:
    m = GAME_ID_RE.match(gid or '')
    if not m: return None
    return (m.group(1), int(m.group(2)))


def _norm_fighter(name: str) -> str:
    """Casefold and strip accents/punctuation. 'Uroš Medić' -> 'uros medic'."""
    n = unicodedata.normalize('NFKD', str(name or ''))
    n = ''.join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r'[^a-zA-Z ]', '', n).lower()
    return re.sub(r'\s+', ' ', n).strip()


def _same_fighter(a: str, b: str) -> bool:
    """True when two spellings name the same fighter.

    Surname match is allowed because the card and the results table
    disagree on given names often enough to matter (Uros vs Uroš,
    'Donte Johnson' vs 'Donte Johnson Jr.'), but only when the surname
    is distinctive enough to be worth trusting.
    """
    na, nb = _norm_fighter(a), _norm_fighter(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = na.split(), nb.split()
    return bool(sa and sb and len(sa[-1]) >= 4 and sa[-1] == sb[-1])


def _snapshot_names(read: dict) -> tuple[str, str] | None:
    """The two fighters this read is actually about, from its own snapshot."""
    snap = read.get('input_snapshot') or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            snap = {}
    a, b = snap.get('fighter_a'), snap.get('fighter_b')
    return (a, b) if a and b else None


def resolve_fight(read: dict, fights: list) -> tuple[dict | None, str]:
    """Find the fight a read is about BY NAME. Returns (fight, how).

    `how` is one of 'name', 'order-fallback' or a refusal reason, so the
    caller can report which path a grade came from instead of this being
    invisible.
    """
    names = _snapshot_names(read)
    if names:
        a, b = names
        hits = [f for f in fights
                if (_same_fighter(a, f.get('fighter_a'))
                    and _same_fighter(b, f.get('fighter_b')))
                or (_same_fighter(a, f.get('fighter_b'))
                    and _same_fighter(b, f.get('fighter_a')))]
        if len(hits) == 1:
            return hits[0], 'name'
        if len(hits) > 1:
            return None, f'ambiguous: {a} vs {b} matched {len(hits)} fights'
        return None, f'no fight on this card matches {a} vs {b}'
    # No names to match on. Only then fall back to the index, and say so.
    parsed = parse_gid(read.get('game_id', ''))
    if not parsed:
        return None, 'no snapshot names and an unparseable game_id'
    for f in fights:
        if (f.get('event_date'), f.get('fight_order')) == parsed:
            return f, 'order-fallback'
    return None, 'no snapshot names and fight_order not on this card'


def grade_one(read: dict, fight: dict | None) -> str | None:
    market = (read.get('call_market') or '').lower()
    if market == 'pass':
        # Before any lookup: a pass has no side, so a wrong-row join can
        # never reach it.
        return 'NO_ACTION'
    if market != 'fight':
        return None
    if not fight:
        return None
    winner = (fight.get('winner') or '').strip().upper()
    if not winner: return None  # not yet resolved
    if winner in ('DRAW', 'NC', 'NO_CONTEST'):
        return 'Void'

    # ── 2026-09-22 ROOT-CAUSE FIX — grade by NAME, never by letter ──
    #
    # This used to be:
    #
    #     return 'Win' if winner == side else 'Loss'
    #
    # comparing jerry_reads.call_side ('A'/'B') to
    # ufc_fight_results.winner ('a'/'b'). Those letters index DIFFERENT
    # orderings. ufc_fight_results is stored winner-first — 1,118 rows
    # say 'a' against 70 that say 'b' — while call_side follows the
    # card's ordering. Any fight where the card's B is the results
    # table's A graded backwards.
    #
    # Measured: 7 of 15 graded UFC calls were wrong, almost all of them
    # a win recorded as a loss. Stored record 5-10; corrected 10-5. The
    # published UFC record was inverted by a letter comparison.
    #
    # Names do not have an ordering, so they cannot disagree about one.
    snap = read.get('input_snapshot') or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            snap = {}
    side = (read.get('call_side') or '').strip().upper()
    # ── 2026-10-19 · GRADE THE SIDE WE PUBLISHED, NOT THE MODEL'S PICK ──
    #
    # This preferred snap['model_pick_fighter'], which is ALWAYS the fighter
    # at ufc_picks.recommended_side. But upsert_read encodes the verdict in
    # call_side: BACK keeps the model's side, FADE sets the OPPOSITE. So on
    # every FADE read the grader scored the model's fighter while the app had
    # published the other one.
    #
    # Measured over all 19 graded UFC reads: 4 are FADEs, and ALL FOUR were
    # stored backwards. The 15 BACK reads were all correct, which is why this
    # hid for so long - and the error flattered us, because a fade usually
    # opposes a favourite and the favourite usually wins.
    #
    #     stored 13-6  ->  11-8 graded on the side we actually published
    #
    # The backed fighter is therefore the snapshot's fighter AT call_side.
    # model_pick_fighter is only a fallback for a snapshot with no names, and
    # only when the call was a BACK - otherwise it names the wrong corner.
    picked = (snap.get('fighter_a') if side == 'A' else
              snap.get('fighter_b') if side == 'B' else None)
    if not picked and side and side == str(snap.get('model_pick_side') or '').upper():
        picked = snap.get('model_pick_fighter')
    won_name = fight.get('fighter_a') if winner == 'A' else                fight.get('fighter_b') if winner == 'B' else None
    if picked and won_name:
        return 'Win' if _same_fighter(picked, won_name) else 'Loss'

    # No names on either side — refuse rather than guess. A letter
    # comparison is what produced the inverted record; returning None
    # leaves the row ungraded and visible instead of confidently wrong.
    print(f'  ⚠ ungradeable (no fighter names): game_id={read.get("game_id")} '
          f'side={side or "?"}')
    return None


def run(game_date: str | None = None, backfill_days: int = 60,
        dry_run: bool = False, regrade: bool = False) -> int:
    if game_date:
        dates = [game_date]
    else:
        base = (datetime.now(timezone.utc) - timedelta(hours=28)).date()
        dates = [(base - timedelta(days=i)).isoformat() for i in range(backfill_days + 1)]

    total = 0
    for gd in dates:
        r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
            params={'sport': 'eq.UFC', 'game_date': f'eq.{gd}',
                    # --regrade re-settles rows that already carry a
                    # result. Needed once: the letter-comparison bug
                    # fixed 2026-09-22 left 7 of 15 graded calls
                    # inverted, and they will never be revisited by
                    # the normal `result IS NULL` sweep.
                    **({} if regrade else {'result': 'is.null'}),
                    'select': 'id,game_id,call_market,call_side,input_snapshot,result'}, timeout=15)
        reads = r.json() if r.status_code == 200 else []
        if not reads: continue

        rr = requests.get(f'{SB}/rest/v1/ufc_fight_results', headers=H_READ,
            params={'event_date': f'eq.{gd}',
                    'select': 'event_date,fight_order,fighter_a,fighter_b,winner,method'},
            timeout=15)
        fights = rr.json() if rr.status_code == 200 else []

        graded = 0
        for read in reads:
            market = (read.get('call_market') or '').lower()
            fight, how = resolve_fight(read, fights)
            if market == 'fight' and not fight:
                print(f'  ! {gd} id={read["id"]} ungraded - {how}')
            # Make a renumbered card VISIBLE rather than silent: the index
            # in the game_id is what the old join trusted.
            if fight is not None and how == 'name':
                parsed = parse_gid(read.get('game_id', ''))
                if parsed and parsed[1] != fight.get('fight_order'):
                    print(f'  ~ {gd} id={read["id"]} game_id says fight '
                          f'{parsed[1]}, results say {fight.get("fight_order")} '
                          f'({fight.get("fighter_a")} vs {fight.get("fighter_b")}) '
                          f'- graded by name')
            result = grade_one(read, fight)
            if not result: continue
            actual = {
                'winner': (fight or {}).get('winner'),
                'method': (fight or {}).get('method'),
                'fighter_a': (fight or {}).get('fighter_a'),
                'fighter_b': (fight or {}).get('fighter_b'),
            } if fight else None
            prior = str(read.get('result') or '')
            # ── 2026-10-19 · A VOID IS A JUDGEMENT, NOT A PENDING GRADE ──
            # A row is voided because the BET should not count - here, a
            # duplicate read minted when a re-scrape renumbered the card
            # (see _existing_gid_for_fight in generate_ufc_fight_synthesis).
            # The fight still has a winner, so grade_one will happily settle
            # it again and --regrade would quietly restore the loss that was
            # deliberately removed. Automation does not get to overrule a
            # void; clearing result back to NULL is how you re-open one.
            if prior.upper() == 'VOID':
                print(f'  = {gd} id={read["id"]} left VOID (not re-graded)')
                continue
            if regrade and prior and prior == result:
                continue                      # already correct, leave it
            if regrade and prior and prior != result:
                print(f'  ↻ {gd} id={read["id"]} {prior} -> {result}')
            if dry_run:
                print(f'  [DRY] {gd} id={read["id"]} side={read.get("call_side")} winner={actual and actual.get("winner")} → {result}')
                graded += 1
                continue
            pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{read["id"]}',
                                headers=H_WRITE,
                                json={'result': result,
                                      'actual_outcome': actual,
                                      'resolved_at': datetime.now(timezone.utc).isoformat()},
                                timeout=15)
            if pr.status_code in (200, 204):
                graded += 1
            else:
                print(f'  patch {read["id"]}: {pr.status_code} {pr.text[:120]}')
        if graded or reads:
            print(f'  [{gd}] graded {graded}/{len(reads)} UFC jerry_reads')
        total += graded
    print(f'\n=== graded {total} UFC jerry_reads across {len(dates)} date(s) ===')
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--backfill-days', type=int, default=60)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--regrade', action='store_true',
                   help='re-settle rows that already have a result')
    args = p.parse_args()
    run(game_date=args.date, backfill_days=args.backfill_days,
        dry_run=args.dry_run, regrade=args.regrade)


if __name__ == '__main__':
    main()
