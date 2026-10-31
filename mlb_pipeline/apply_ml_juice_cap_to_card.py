"""Apply the -300 ML juice cap we already wrote to the card and The Sharp.

== 2026-10-31 . A -425 MONEYLINE IS ON THE HOME SCREEN ===================
ANDY: "is eveything that us rfacing to user correct and we stand ebhine
right now thats the imporant part right onw"

jerry_cache.sweat_card_2026-10-31 top_8 carries

    Colorado Avalanche ML   odds=-385   tier=STRONG   conv=74

and nhl_game_context.close_home_ml for that game now reads -425. Breakeven
at -385 is 79.4%; at -425 it is 81.0%.

THIS IS NOT A NEW RULE. Three thresholds already exist in this repo and
-425 is past all of them:

  defensive_gates._ml_odds_too_juiced(ctx, side, max_juice=-300)
      "Return True if ML odds on the picked side are worse than max_juice.
       Default -300: anything at -301 or juicier (e.g., -500, -1000) fails."
  jerry_anchor_potd.py        POTD ML juice gate, -250 (Andy, 08-03,
                              loosened from -200 on 09-12)
  generate_sharp_card.SOLE_PICK_ML_JUICE_MAX = -300

WHY IT GOT THROUGH. _ml_odds_too_juiced has exactly ONE call site,
defensive_gates.py:1282, inside the LR ML override path -- it guards a
model override, not card composition. Nothing re-checks the price when the
composite picks a chalk ML directly. I checked the other possibility
first: the gate reads
`close_home_ml or home_ml_odds or home_ml_close`, and one of those names
resolves on all six sports (NHL has close_home_ml AND home_ml_close), so
this is NOT a guessed-column failure -- the gate works, it just is not
asked.

PRE-GAME ONLY. A pick is withdrawn only while the game has not started.
Pulling a play after it starts is not discipline, it is dodging, so a
started game is reported and left exactly as published.

Reads the price for the SIDE WE PICKED, from that sport's own column
names, and refuses to judge a sport whose column it cannot resolve rather
than treating a missing price as a pass (feedback_dict_get_on_a_guessed_column).

USAGE
  python apply_ml_juice_cap_to_card.py
  python apply_ml_juice_cap_to_card.py --apply
"""
from __future__ import annotations

import argparse
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

MAX_JUICE = -300                      # defensive_gates._ml_odds_too_juiced
from card_summaries import CARD_LISTS as BLOBS   # includes football_picks
VOIDS = ('VOID', 'VOIDED')
SPORTS = ('NCAAF', 'NFL', 'NBA', 'NHL', 'MLB', 'NCAAB')
#: the same fallback chain the gate uses, plus each table's own spelling
HOME_ML = ('close_home_ml', 'home_ml_odds', 'home_ml_close')
AWAY_ML = ('close_away_ml', 'away_ml_odds', 'away_ml_close')
KICK = ('kickoff_utc', 'commence_time', 'commence_time_utc', 'start_time_utc')


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


def team_of(lbl):
    s = ' '.join(str(lbl or '').split())
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+ML$', '', s, flags=re.I)
    return s.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    date = a.date or (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=4)).date().isoformat()
    now = dt.datetime.now(dt.timezone.utc)
    print('[%s] ML juice cap %+d on %s'
          % ('APPLY' if a.apply else 'DRY RUN', MAX_JUICE, date))

    # one read per sport: team -> (price for that team, kickoff)
    priced = {}
    for s in SPORTS:
        rows = get('%s_game_context' % s.lower(), select='*',
                   game_date='eq.' + date, limit=400)
        if rows is None:
            print('  %s: context unreadable -- its picks cannot be judged'
                  % s)
            continue
        for x in rows:
            ko = None
            for k in KICK:
                if x.get(k):
                    try:
                        ko = dt.datetime.fromisoformat(
                            str(x[k]).replace('Z', '+00:00'))
                    except Exception:                          # noqa: BLE001
                        ko = None
                    break
            for cols, tkey in ((HOME_ML, 'home_team'), (AWAY_ML, 'away_team')):
                val = next((x[c] for c in cols if x.get(c) is not None), None)
                if x.get(tkey):
                    priced[(s, str(x[tkey]).strip().lower())] = (val, ko)

    hits = []
    for key, surf in (('sweat_card_%s' % date, 'Sweat Card'),
                      ('sharp_card_%s' % date, 'The Sharp')):
        rows = get('jerry_cache', select='data', cache_key='eq.' + key)
        if rows is None or not rows:
            continue
        blob = jd(rows[0].get('data'))
        if not isinstance(blob, dict):
            continue
        lists = BLOBS if surf == 'Sweat Card' else ('items',)
        drop_ids, dropped = set(), []
        for name in lists:
            items = blob.get(name)
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                typ = str(it.get('type') or '').lower()
                lbl = str(it.get('pick') or it.get('label') or '')
                if typ not in ('ml', 'potd') and ' ml' not in lbl.lower():
                    continue
                sport = str(it.get('sport') or '').upper()
                tm = team_of(lbl).lower()
                if (sport, tm) not in priced:
                    print('  ? %s [%s] %r: no price row for %r in %s -- '
                          'NOT judged' % (surf, name, lbl, team_of(lbl),
                                          sport or '(no sport)'))
                    continue
                val, ko = priced[(sport, tm)]
                if val is None:
                    print('  ? %s [%s] %r: context has no ML price -- '
                          'NOT judged' % (surf, name, lbl))
                    continue
                try:
                    o = int(float(val))
                except (TypeError, ValueError):
                    continue
                if o >= MAX_JUICE:
                    continue
                be = 100.0 * abs(o) / (abs(o) + 100.0)
                started = bool(ko and ko <= now)
                print('\n  %s [%s] %r' % (surf, name, lbl))
                print('      published odds %s | context now %+d | '
                      'breakeven %.1f%% | cap %+d'
                      % (it.get('odds'), o, be, MAX_JUICE))
                if started:
                    print('      game already started (%s) -- LEFT AS '
                          'PUBLISHED. Pulling a live play is dodging, not '
                          'discipline.' % (str(ko)[:16] if ko else '?'))
                    continue
                print('      starts %s, %d min away -- WITHDRAWN'
                      % (str(ko)[:16] if ko else '?',
                         int((ko - now).total_seconds() // 60) if ko else -1))
                drop_ids.add(id(it))
                dropped.append(lbl)
                hits.append((surf, lbl, o))
        if not drop_ids:
            continue
        for name in lists:
            items = blob.get(name)
            if isinstance(items, list):
                blob[name] = [it for it in items
                              if not (isinstance(it, dict)
                                      and id(it) in drop_ids)]
        if surf == 'Sweat Card':
            # both summaries, from the lists as they now stand
            from card_summaries import recompute as _recompute
            for _k, _o, _n in _recompute(blob):
                print('      %s: %s -> %s' % (_k, json.dumps(_o),
                                              json.dumps(_n)))
        blob['_ml_juice_capped_at'] = now.isoformat()
        if not a.apply:
            continue
        pr = requests.patch(SB + '/rest/v1/jerry_cache', headers=H_W,
                            timeout=60, params={'cache_key': 'eq.' + key},
                            json={'data': blob})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = False
        if back:
            b = jd(back[0].get('data')) or {}
            ok = all(norm_lbl not in [
                str(it.get('pick') or it.get('label') or '')
                for nm in lists for it in (b.get(nm) or [])
                if isinstance(it, dict)] for norm_lbl in dropped)
        print('      %s HTTP %s %s' % (key, pr.status_code,
                                       'CONFIRMED' if ok else
                                       'NOT CONFIRMED'))
        if not ok:
            continue
        # ONLY the surface we actually withdrew from. Filtering on the label
        # alone would also void 3982347, the game_read for the same team --
        # and a READ is not a card play. Withdrawing a pick from the card
        # does not retract our reading of the game.
        surf_key = 'sweat_card' if surf == 'Sweat Card' else 'sharp_card'
        for lbl in dropped:
            rc = get('public_receipts', select='id,pick_label,result,surface',
                     game_date='eq.' + date, surface='eq.' + surf_key,
                     pick_label='eq.' + lbl) or []
            for x in rc:
                if str(x.get('result') or '').upper() in VOIDS or x.get('result'):
                    continue
                pv = requests.patch(SB + '/rest/v1/public_receipts',
                                    headers=H_W, timeout=60,
                                    params={'id': 'eq.%s' % x['id']},
                                    json={'result': 'VOID',
                                          'graded_at': now.isoformat()})
                bk = pv.json() if pv.status_code in (200, 201) else []
                vok = bool(bk) and str(bk[0].get('result')) == 'VOID'
                print('      receipt %s [%s] %r -> VOID  HTTP %s %s'
                      % (x['id'], x.get('surface'), lbl, pv.status_code,
                         'CONFIRMED' if vok else 'NOT CONFIRMED'))

    if not hits:
        print('\n  nothing past the cap')
    if not a.apply:
        print('\nRe-run with --apply to write.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
