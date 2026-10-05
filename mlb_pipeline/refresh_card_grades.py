#!/usr/bin/env python3
"""Re-resolve past Sweat Card rows so their stored grades stop lying.

WHY (2026-10-04)
----------------
Andy, pointing at two numbers on the same screen: the Home strip read
"YESTERDAY 3-2" while the recap below it read 5-2.

Both were ours, and the 3-2 was the stale one. `jerry_cache.sweat_card_{date}`
stores `top_8` with a per-pick `result` and a `top_8_summary` rollup, both
FROZEN AT BUILD TIME. Yesterday's row still said:

    top_8_summary  {"wins":3, "losses":2, "pending":2, "resolved":5}
    Shohei Ohtani Under 1.5 Hits -> Pending
    Ha-Seong Kim  Under 0.5 Hits -> Pending

Both of those props won. Today's card recomputes a `yesterday_recap` and got
5-2 right, but nothing ever went back and fixed the 10-03 ROW. So the historical
record on the home screen is permanently whatever happened to have resolved by
the time the card was built.

That is not one bad day. app/components/RecapStrip.tsx builds BOTH the
yesterday figure AND the "CARD L30D" rollup by summing `top_8_summary` across
the last 30 `sweat_card_%` rows — so every pick that resolved after its own
card was built is missing from the 30-day number too.

WHAT THIS DOES
--------------
For each card row in the window, re-resolves every top_8 pick against the
graded prop tables and rewrites `result` + `top_8_summary`. Nothing else in the
blob is touched.

Resolution order, all read-only against graded sources:
  1. exact (player, prop_type) from the sport's prop table
  2. THE COMPLEMENT — a published `hits_under` settles off the stored
     `hits_over` row, inverted. At one line the two directions are exact
     opposites, and this is precisely why Ohtani and Kim sat Pending: the card
     published the under while only the over row existed.
  3. public_receipts, which is append-only and survives a pruned prop row.

A pick that cannot be resolved stays exactly as it was. This never invents a
grade and never overwrites a result that is already decided — it only fills
Pending.

    python refresh_card_grades.py --days 35
    python refresh_card_grades.py --days 35 --apply
"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

PROP_TABLES = ('mlb_pipeline_props', 'nfl_pipeline_props',
               'nhl_pipeline_props', 'nba_pipeline_props')
_PENDING = ('', 'pending', 'none', 'null')
_FLIP = {'win': 'Loss', 'loss': 'Win', 'push': 'Push'}


def _page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params); q.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q, timeout=90)
        if r.status_code != 200:
            return out
        chunk = r.json()
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def build_lookup(game_date: str) -> dict:
    """{(player_lower, prop_type_lower): 'Win'|'Loss'|'Push'} for one date."""
    look: dict = {}
    for tbl in PROP_TABLES:
        for p in _page(tbl, {'select': 'player_name,prop_type,result',
                             'game_date': f'eq.{game_date}'}):
            res = p.get('result')
            if not res:
                continue
            nm = str(p.get('player_name') or '').strip().lower()
            ty = str(p.get('prop_type') or '').strip().lower()
            if not nm or not ty:
                continue
            look.setdefault((nm, ty), str(res).title())
            # The complement — see the module docstring.
            if '_' in ty:
                stem, direction = ty.rsplit('_', 1)
                opp = {'over': 'under', 'under': 'over'}.get(direction)
                inv = _FLIP.get(str(res).strip().lower())
                if opp and inv:
                    look.setdefault((nm, f'{stem}_{opp}'), inv)
    # Receipts last: append-only, survives a pruned prop row.
    for rc in _page('public_receipts', {'select': 'player_name,prop_type,result',
                                        'game_date': f'eq.{game_date}',
                                        'result': 'not.is.null',
                                        'prop_type': 'not.is.null'}):
        nm = str(rc.get('player_name') or '').strip().lower()
        ty = str(rc.get('prop_type') or '').strip().lower()
        res = str(rc.get('result') or '').strip()
        if nm and ty and res:
            look.setdefault((nm, ty), res.title())
    return look


# Market words carried in a label but not part of the pick's identity. The
# SAME play is written "BUF ATS -7" on the card and "BUF RL -7" on its
# receipt, so the market word has to come out before the two can be compared.
_MARKET_WORDS = re.compile(
    r'\b(ml|rl|ats|spread|puck\s*line|run\s*line|moneyline)\b', re.I)


def _norm_label(s) -> str:
    s = re.sub(r'\s*\([^)]*\)\s*$', '', str(s or ''))   # drop "(Jerry 70/100)"
    s = _MARKET_WORDS.sub(' ', s)
    s = re.sub(r'[^a-z0-9+\-. ]', '', s.lower())
    return re.sub(r'\s+', ' ', s).strip()


def _norm_game(s) -> str:
    s = re.sub(r'[^a-z0-9 @]', '', str(s or '').lower())
    return re.sub(r'\s+', ' ', s).strip()


_AMBIGUOUS = object()


def build_side_lookup(game_date: str) -> dict:
    """Graded SIDE results for one date, read from public_receipts.

    Card side picks (ML / RL / ATS / total / POTD) settle in
    resolve_game_results._resolve_sweat_card_top8, which is bounded at
    yesterday — so today's card sat 'Pending' hours after BUF ATS -7 and
    PHI +3 had both lost and their receipts had graded. public_receipts is
    the immutable, already-verified record, so settling sides from it needs
    no second box-score read and covers every sport at once.

    Keyed two ways: (sport, game, label) first, then (sport, label) for cards
    whose `game` string differs from the receipt's matchup. A key that two
    receipts disagree on is marked ambiguous and refused rather than guessed.
    """
    look: dict = {}

    def _put(key, res):
        if key in look and look[key] != res:
            look[key] = _AMBIGUOUS
        else:
            look.setdefault(key, res)

    for rc in _page('public_receipts', {
            'select': 'sport,matchup,pick_label,result,prop_type',
            'game_date': f'eq.{game_date}',
            'result': 'not.is.null',
            'prop_type': 'is.null'}):
        res = str(rc.get('result') or '').strip()
        if res.upper() not in ('WIN', 'LOSS', 'PUSH'):
            continue           # NO_ACTION / VOID decide nothing
        sport = str(rc.get('sport') or '').upper()
        lbl = _norm_label(rc.get('pick_label'))
        if not lbl:
            continue
        _put((sport, _norm_game(rc.get('matchup')), lbl), res.title())
        _put((sport, lbl), res.title())
    return look


def resolve_pick(pick: dict, look: dict, side_look: dict | None = None) -> str | None:
    """New result for a pending pick, or None to leave it alone."""
    cur = str(pick.get('result') or '').strip()
    if cur.lower() not in _PENDING:
        return None                      # already decided; never overwrite
    label = str(pick.get('label') or '')
    ptype = str(pick.get('type') or '')
    if not ptype.startswith('prop_'):
        if side_look is None:
            return None                  # sides settle elsewhere
        sport = str(pick.get('sport') or '').upper()
        lbl = _norm_label(label)
        if not lbl:
            return None
        for key in ((sport, _norm_game(pick.get('game')), lbl), (sport, lbl)):
            hit = side_look.get(key)
            if hit is _AMBIGUOUS:
                return None              # two receipts disagree — never guess
            if hit:
                return hit
        return None
    stat = ptype[len('prop_'):].lower()
    # "Shohei Ohtani Under 1.5 Hits" -> player is everything before the side
    low = label.lower()
    cut = min((low.find(w) for w in (' under ', ' over ') if low.find(w) > 0),
              default=-1)
    if cut <= 0:
        return None
    player = label[:cut].strip().lower()
    return look.get((player, stat))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=35)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    today = date.today()
    changed_rows = 0
    changed_picks = 0
    print(f'=== refresh card grades · last {args.days} days ===\n')
    # 2026-10-04: the range started at 1, so TODAY's card was never
    # refreshed — `--days 1` looked only at yesterday. Today's card showed
    # BUF ATS -7 and PHI +3 as 'Pending' hours after both had lost and the
    # receipts had graded them. Starting at 0 is safe: a play whose receipt
    # is still ungraded has no entry in the lookup and so is left Pending.
    for i in range(0, args.days):
        d = (today - timedelta(days=i)).isoformat()
        key = f'sweat_card_{d}'
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H, timeout=60,
                         params={'select': 'cache_key,data', 'cache_key': f'eq.{key}'})
        if r.status_code != 200 or not r.json():
            continue
        blob = r.json()[0]['data']
        if isinstance(blob, str):
            blob = json.loads(blob)
        picks = blob.get('top_8') or []
        # football_picks items carry NO result key at all, so the football
        # side of the card was never graded in the payload. They settle the
        # same way; only top_8 feeds top_8_summary, so the rollup below
        # still counts `picks` alone.
        fb_picks = [p for p in (blob.get('football_picks') or [])
                    if isinstance(p, dict)]
        if not picks and not fb_picks:
            continue
        pending = [p for p in picks + fb_picks
                   if str(p.get('result') or '').strip().lower() in _PENDING]
        if not pending:
            continue
        look = build_lookup(d)
        side_look = build_side_lookup(d)
        fixed = []
        for p in picks + fb_picks:
            nv = resolve_pick(p, look, side_look)
            if nv:
                fixed.append((p.get('label'), p.get('result'), nv))
                p['result'] = nv
        if not fixed:
            print(f'{d}: {len(pending)} pending, none resolvable')
            continue
        w = sum(1 for p in picks if str(p.get('result')).lower() == 'win')
        l = sum(1 for p in picks if str(p.get('result')).lower() == 'loss')
        pu = sum(1 for p in picks if str(p.get('result')).lower() == 'push')
        still = sum(1 for p in picks
                    if str(p.get('result') or '').strip().lower() in _PENDING)
        old = blob.get('top_8_summary') or {}
        new = {'wins': w, 'losses': l, 'pushes': pu, 'pending': still,
               'resolved': w + l + pu}
        print(f'{d}: {old.get("wins")}-{old.get("losses")} '
              f'({old.get("pending")}P) -> {w}-{l} ({still}P)')
        for lbl, was, now in fixed:
            print(f'    {str(lbl)[:44]:44s} {was} -> {now}')
        blob['top_8_summary'] = new
        changed_rows += 1
        changed_picks += len(fixed)
        if args.apply:
            pr = requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W, timeout=60,
                                params={'cache_key': f'eq.{key}'},
                                data=json.dumps({'data': blob}))
            body = pr.json() if pr.content else []
            ok = (pr.status_code in (200, 204) and body
                  and (body[0]['data'].get('top_8_summary') or {}).get('wins') == w)
            print(f'    {"written" if ok else "WRITE FAILED " + str(pr.status_code)}')
    print(f'\n{changed_picks} pick(s) across {changed_rows} card(s) '
          f'{"updated" if args.apply else "would update"}')
    if not args.apply and changed_rows:
        print('re-run with --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
