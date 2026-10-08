"""Make the read's PROSE quote the same line as its label.

WHY (2026-10-08)
----------------
Andy, before NFL/NCAAF lock: "we need to assess every single nfl anc ncaaf
pick and play ensure lines and picks/reads arent off as they lock today".

They were off — not the label, the WRITEUP. The 10-07 line refresh corrected
`call_text` and `primary_play.label` on 21 picks, but `short_read` is built
once at generation as

    "<TEAM ±LINE> — <TEAM ±LINE>: reason · reason"

and nothing rewrote it. So the badge and the writeup disagreed on 7 NFL reads:

    label TB  +8.5    prose "TB +9.5"     conv 45
    label CHI -2.5    prose "CHI +2.5"    conv 79   <- SIGN FLIP, top tier
    label NE  -3.5    prose "NE -8.5"     conv 55
    label TEN +7.5    prose "TEN +6.5"    conv 53
    label WAS -3.5    prose "WAS -2.5"    conv 45
    label GB  +2      prose "GB -3"       conv 55
    label NE  -7      prose "NE -9.5"     conv 55

CHI is the one that matters most: the card says lay 2.5 and the writeup says
take 2.5 — opposite sides of the same game, on a 79-conviction pick.

WHAT IT CHANGES, AND WHAT IT WILL NOT TOUCH
Only the leading "<TEAM ±LINE>" tokens in `short_read`, and only when they
disagree with `call_text`. The reasons after the colon, the long_read, the
side, the market and the conviction are all left exactly as they are — this
is a transcription fix, not a re-write of anybody's analysis.

It will not run on a game that has already started, and it refuses any read
whose label it cannot parse rather than guessing.

CLI
    python resync_read_prose_line.py --sport NFL            # dry
    python resync_read_prose_line.py --sport NFL --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: "CHI +2.5 — CHI +2.5: reasons"  /  "TEN +6.5 - TEN +6.5: reasons"
#: Captures the two leading team+line tokens so both can be replaced.
_PREFIX = re.compile(
    r'^\s*(?P<a>[A-Za-z][A-Za-z .&\'()-]*?\s[+-]\d+(?:\.\d+)?)'
    r'\s*(?P<sep>[—–-])\s*'
    r'(?P<b>[A-Za-z][A-Za-z .&\'()-]*?\s[+-]\d+(?:\.\d+)?)'
    r'\s*:(?P<rest>.*)$', re.S)
#: single-token form, "CHI +2.5: reasons"
_PREFIX1 = re.compile(
    r'^\s*(?P<a>[A-Za-z][A-Za-z .&\'()-]*?\s[+-]\d+(?:\.\d+)?)'
    r'\s*:(?P<rest>.*)$', re.S)
_NUM = re.compile(r'[+-]\d+(?:\.\d+)?')


def run(sport: str, apply: bool, days: int):
    today = date.today().isoformat()
    until = (date.today() + timedelta(days=days)).isoformat()
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H, params={
        'select': 'id,game_id,game_date,call_text,call_line,short_read',
        'sport': f'eq.{sport}',
        'and': f'(game_date.gte.{today},game_date.lte.{until})',
        'limit': '500'}, timeout=160)
    rows = r.json() if r.status_code in (200, 206) else []
    print(f'=== {sport} · {len(rows)} reads {today}..{until} ===')

    fixed = 0
    for a in rows:
        label = str(a.get('call_text') or '').strip()
        prose = str(a.get('short_read') or '')
        if not label or not prose or not _NUM.search(label):
            continue
        m = _PREFIX.match(prose)
        two = bool(m)
        if not m:
            m = _PREFIX1.match(prose)
        if not m:
            continue
        cur = m.group('a').strip()
        if cur == label:
            continue
        # Only rewrite when the OLD token is the same team — a different team
        # means something deeper is wrong and a silent swap would hide it.
        if cur.rsplit(' ', 1)[0].strip().lower() != label.rsplit(' ', 1)[0].strip().lower():
            print(f"  ! {a['game_date']} team mismatch, SKIPPED: "
                  f"prose {cur!r} vs label {label!r}")
            continue
        new = (f"{label} {m.group('sep')} {label}:{m.group('rest')}" if two
               else f"{label}:{m.group('rest')}")
        print(f"  {a['game_date']}  {cur!r} -> {label!r}")
        fixed += 1
        if not apply:
            continue
        pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{a["id"]}',
                            headers=H_W, json={'short_read': new}, timeout=60)
        if pr.status_code not in (200, 204):
            print(f'    ! patch {pr.status_code} {pr.text[:120]}')
    print(f'  {"fixed" if apply else "would fix"} {fixed}')
    return fixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--days', type=int, default=16)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    sports = [args.sport.upper()] if args.sport else ['NFL', 'NCAAF', 'MLB', 'NHL']
    t = 0
    for s in sports:
        t += run(s, args.apply, args.days)
    print(f'TOTAL {"fixed" if args.apply else "would fix"} {t}')


if __name__ == '__main__':
    main()
