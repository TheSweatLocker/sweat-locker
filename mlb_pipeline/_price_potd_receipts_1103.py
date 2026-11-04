"""Put a price on every POTD receipt that has none.

== WHY ====================================================================
Andy, 2026-11-03: "113 priceless POTD receipts? We need to fix that - if you
cant find the prices preferably just make -115."

Without `pick_odds` a receipt cannot contribute to ROI at all, so the POTD
surface's ROI is computed off whichever rows happen to carry a price.

Measured first. 357 POTD receipts, 295 with no price -- but 182 of those are
the two 91-row duplicate storms (2026-07-21 and 2026-10-16), every one VOID.
The real population is 113, one per date from 2026-04-10 to 2026-10-29:

    TOTAL 36 . NRFI 36 . ML 30 . NO-PLAY/No Games 7 . OTHER 2 . SPREAD 2
    MLB 108 . NBA 3 . NFL 2
    results: WIN 57 . LOSS 44 . PUSH 5 . NO_ACTION 7

== RECOVER BEFORE DEFAULTING =============================================
A real stored price beats an assumed one, and three sources were checked:

    daily_best_bet_history.odds_american   NULL on all 113 of these dates
    jerry_cache best_bet_* blobs           no price field at all
                                           (context.odds on 1 of 201 rows)
    mlb_game_context                       PRUNED to 15 rows -- history gone

The one that works is `mlb_game_results`, which retains home_ml_close /
away_ml_close / home_ml_open / away_ml_open over 4,838 rows back to
2025-03-27. 102 of the 113 receipts resolve to a game there on
(game_date, away_team, home_team) taken from the receipt's own `matchup`.
`mlb_line_history` adds over_odds / under_odds / spread_*_odds, but only
covers 25 of the 113.

== THE FALLBACK, AND WHY -115 IS THE CONSERVATIVE CHOICE =================
Andy's -115 is not arbitrary: it is the exact median of our OWN 33 priced ML
POTD receipts (range -202..+105). For totals our 15 priced rows median -110,
so -115 reads slightly MORE juice than reality -- i.e. any ROI computed off
an assumed price comes out a little worse than the truth, never better.
That is the right direction for a number we cannot verify
(feedback_flat_110_inflates_every_ml_figure is about the opposite mistake).

Every assumed price is STAMPED in `audit.price_assumed_1103` with the reason,
so ROI work can separate measured prices from assumed ones instead of having
to trust that the whole column is real.

== NOT PRICED ============================================================
The 7 NO_ACTION rows ('No Play (discipline pass)', 'No Games'). A price on a
bet that was never made is a fabricated denominator, and these are the rows
whose entire point is that nothing was wagered.

    python _price_potd_receipts_1103.py            # dry run
    python _price_potd_receipts_1103.py --apply
"""
import collections
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv
STORMS = {'2026-07-21', '2026-10-16'}
FALLBACK = -115
#: results that mean no wager was placed -- never priced
NO_WAGER = {'NO_ACTION'}


def page(table: str, select: str, **extra) -> list:
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': 1000, 'offset': off}
        p.update(extra)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120,
                         params=p)
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


#: "Yoshinobu Yamamoto Over 17.5 Outs" is a PLAYER PROP, not a game total.
#: My first pass classified both as TOTAL and priced the props off the GAME's
#: over_odds -- a real price from the WRONG MARKET, which is worse than no
#: price at all. A prop carries a player name before Over/Under and a stat
#: word after the number; a game total carries neither.
_PROP = re.compile(
    r"^(?P<player>[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+)+)\s+"
    r"(?P<dir>Over|Under)\s+(?P<line>[\d.]+)\s+"
    r"(?P<stat>Outs|Hits Allowed|Hits|KS|Ks|Strikeouts|ER|Earned Runs|BB|"
    r"Walks|Total Bases|TB)\b", re.I)


def shape(label: str) -> str:
    s = str(label or '')
    if re.search(r'\bNRFI\b', s, re.I):
        return 'NRFI'
    if re.search(r'\bYRFI\b', s, re.I):
        return 'YRFI'
    if re.search(r'\bML\b', s):
        return 'ML'
    if _PROP.match(s.strip()):
        return 'PROP'
    if re.search(r'\b(Over|Under)\s+[\d.]+', s, re.I):
        return 'TOTAL'
    if re.search(r'\bSKIP\b|No Play|No Games', s, re.I):
        return 'NO-PLAY'
    if re.search(r'[+-]\d+(\.\d+)?\b', s):
        return 'SPREAD'
    return 'OTHER'


def main() -> int:
    rec = page('public_receipts',
               'id,surface,sport,game_date,result,pick_odds,pick_label,'
               'matchup,audit')
    potd = [r for r in rec if str(r.get('surface')) == 'potd']
    todo = [r for r in potd
            if r.get('pick_odds') in (None, '')
            and str(r.get('game_date')) not in STORMS]
    print(f'POTD receipts {len(potd)}; priceless (excluding the two storms) '
          f'{len(todo)}')

    results = page('mlb_game_results',
                   'game_id,game_date,home_team,away_team,home_ml_close,'
                   'away_ml_close,home_ml_open,away_ml_open')
    gidx = {(str(x['game_date']), str(x['away_team']), str(x['home_team'])): x
            for x in results}
    lh = page('mlb_line_history',
              'game_id,over_odds,under_odds,spread_home_odds,'
              'spread_away_odds')
    by_gid = collections.defaultdict(list)
    for x in lh:
        by_gid[str(x['game_id'])].append(x)
    props = page('mlb_pipeline_props',
                 'game_date,player_name,prop_type,prop_line,direction,'
                 'close_over_odds,close_under_odds,book_over_odds,'
                 'book_under_odds')
    print(f'  mlb_game_results index {len(gidx)}; '
          f'mlb_line_history game_ids {len(by_gid)}; '
          f'mlb_pipeline_props rows {len(props)}')

    def sane(v):
        """American odds, or None. 0 is not a price and |odds| < 100 is
        impossible -- my first pass took a median of a 2-book sample and
        recovered '+0', which it would have written as a real price."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f == 0 or abs(f) < 100 or abs(f) > 2000:
            return None
        return int(round(f))

    def med(vals):
        good = [float(v) for v in vals if sane(v) is not None]
        return sane(statistics.median(good)) if good else None

    plan, skipped = [], []
    for r in todo:
        sh = shape(r.get('pick_label'))
        if str(r.get('result')) in NO_WAGER or sh == 'NO-PLAY':
            skipped.append((r, 'no wager was placed'))
            continue
        m = str(r.get('matchup') or '')
        g = None
        if ' @ ' in m:
            a, h = (x.strip() for x in m.split(' @ ', 1))
            g = gidx.get((str(r['game_date']), a, h))
        price, src = None, None
        if g and sh == 'ML':
            team = re.sub(r'\s+ML\b.*$', '', str(r.get('pick_label'))).strip()
            side = ('home' if team == str(g['home_team'])
                    else 'away' if team == str(g['away_team']) else None)
            if side:
                for col in (f'{side}_ml_close', f'{side}_ml_open'):
                    v = sane(g.get(col))
                    if v is not None:
                        price, src = v, f'mlb_game_results.{col}'
                        break
        if price is None and sh == 'PROP':
            mm = _PROP.match(str(r.get('pick_label')).strip())
            want_line = float(mm.group('line'))
            cand = [x for x in props
                    if str(x.get('game_date')) == str(r['game_date'])
                    and str(x.get('player_name') or '').strip().lower()
                    == mm.group('player').strip().lower()
                    and str(x.get('direction') or '').lower()
                    == mm.group('dir').lower()
                    and x.get('prop_line') is not None
                    and abs(float(x['prop_line']) - want_line) < 1e-6]
            over = mm.group('dir').lower() == 'over'
            cols = (('close_over_odds', 'book_over_odds') if over
                    else ('close_under_odds', 'book_under_odds'))
            for col in cols:
                v = med([x.get(col) for x in cand])
                if v is not None:
                    price = v
                    src = f'mlb_pipeline_props.{col} ({len(cand)} row(s))'
                    break
        if price is None and g and sh in ('TOTAL', 'SPREAD'):
            rowsl = by_gid.get(str(g['game_id']), [])
            if rowsl:
                if sh == 'TOTAL':
                    over = bool(re.search(r'\bOver\b', str(r.get('pick_label')),
                                          re.I))
                    col = 'over_odds' if over else 'under_odds'
                else:
                    col = 'spread_home_odds'
                v = med([x.get(col) for x in rowsl])
                if v is not None:
                    price, src = v, f'mlb_line_history.{col} (median of ' \
                                    f'{len(rowsl)} books)'
        if price is None:
            price, src = FALLBACK, 'assumed'
        plan.append((r, sh, price, src))

    print()
    print(f'  to price : {len(plan)}')
    print(f'  skipped  : {len(skipped)}  (no wager placed)')
    rc_src = collections.Counter(
        ('RECOVERED' if s != 'assumed' else 'assumed') for _r, _s, _p, s in plan)
    print(f'  {dict(rc_src)}')
    byshape = collections.Counter()
    for _r, sh, _p, s in plan:
        byshape[(sh, 'recovered' if s != 'assumed' else 'assumed')] += 1
    for k, v in sorted(byshape.items()):
        print(f'     {k[0]:8} {k[1]:10} {v}')
    print()
    for r, sh, price, src in sorted(plan, key=lambda x: str(x[0]['game_date'])):
        if src != 'assumed':
            print(f"  REC  {r['game_date']} {sh:6} {price:+5}  "
                  f"{str(r.get('pick_label'))[:34]:34} {src}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    rc = 0
    stamp = datetime.now(timezone.utc).isoformat()
    ok = 0
    for r, sh, price, src in plan:
        body = {'pick_odds': price}
        if src == 'assumed':
            audit = r.get('audit')
            audit = audit if isinstance(audit, dict) else {}
            audit['price_assumed_1103'] = {
                'odds': price, 'at': stamp, 'shape': sh,
                'why': ('no stored price in daily_best_bet_history, the '
                        'best_bet blob, mlb_game_results or '
                        'mlb_line_history; -115 is the median of our own '
                        'priced ML POTD receipts'),
            }
            body['audit'] = audit
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, params={'id': f"eq.{r['id']}"},
                            data=json.dumps(body))
        if pr.status_code not in (200, 204):
            print(f"  !! {r['id']} PATCH {pr.status_code}: {pr.text[:140]}")
            rc = 1
            continue
        rb = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                          timeout=30,
                          params={'select': 'pick_odds',
                                  'id': f"eq.{r['id']}"})
        got = (rb.json() or [{}])[0].get('pick_odds')
        if got in (None, '') or int(got) != int(price):
            print(f"  !! {r['id']} NOT CONFIRMED — readback {got!r}")
            rc = 1
        else:
            ok += 1
    print(f'  priced and confirmed: {ok} of {len(plan)}')

    after = [r for r in page('public_receipts', 'surface,game_date,pick_odds',
                             surface='eq.potd')
             if r.get('pick_odds') in (None, '')]
    left = [r for r in after if str(r.get('game_date')) not in STORMS]
    print(f'  POTD still priceless: {len(after)} total, {len(left)} outside '
          f'the storms (expected {len(skipped)})')
    if len(left) != len(skipped):
        print('  !! readback disagrees with the plan')
        rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
