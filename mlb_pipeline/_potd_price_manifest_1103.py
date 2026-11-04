"""Record WHICH POTD prices are measured and which are assumed.

== WHY THIS EXISTS AS A FILE AND NOT A COLUMN ============================
_price_potd_receipts_1103.py filled 106 priceless POTD receipts: 34 with a
real stored price and 72 with Andy's -115 fallback. It also tried to stamp
each assumed row in `public_receipts.audit`.

That stamp silently did nothing. `audit` is FROZEN: a PATCH returns **204
with the field unchanged**, which is indistinguishable from success, and my
apply verified `pick_odds` and not the stamp -- so it reported 106 of 106
confirmed while half the write had been dropped
(feedback_204_is_not_a_write). `pick_odds` itself persisted only because it
is set-once and these rows were null.

Probed directly to be sure:

    PATCH audit {..., '_probe_1103': ...}  -> 204
    readback                               -> field unchanged, probe absent

So the provenance lives here instead, in the repo, where it is versioned and
cannot be silently refused. `-115` alone is NOT a usable marker: 74 POTD rows
now sit at exactly -115 and two of those are REAL recovered prices.

This regenerates the manifest by replaying the same recovery against the same
sources and recording, per receipt id, whether a measured price exists. Run
with --write to refresh docs/potd_price_provenance_1103.json.
"""
import collections
import json
import os
import re
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from _price_potd_receipts_1103 import (          # noqa: E402
    STORMS, FALLBACK, shape, _PROP,
)

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
OUT = (Path(__file__).parent.parent / 'docs'
       / 'potd_price_provenance_1103.json')


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


def sane(v):
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


def main() -> int:
    rec = page('public_receipts',
               'id,surface,sport,game_date,result,pick_odds,pick_label,matchup',
               surface='eq.potd')
    pool = [r for r in rec if str(r.get('game_date')) not in STORMS]
    results = page('mlb_game_results',
                   'game_id,game_date,home_team,away_team,home_ml_close,'
                   'away_ml_close,home_ml_open,away_ml_open')
    gidx = {(str(x['game_date']), str(x['away_team']), str(x['home_team'])): x
            for x in results}
    lh = page('mlb_line_history',
              'game_id,over_odds,under_odds,spread_home_odds,spread_away_odds')
    by_gid = collections.defaultdict(list)
    for x in lh:
        by_gid[str(x['game_id'])].append(x)
    props = page('mlb_pipeline_props',
                 'game_date,player_name,prop_line,direction,'
                 'close_over_odds,close_under_odds,book_over_odds,'
                 'book_under_odds')

    entries, n_meas, n_asm, n_nowager = [], 0, 0, 0
    for r in pool:
        sh = shape(r.get('pick_label'))
        if str(r.get('result')) == 'NO_ACTION' or sh == 'NO-PLAY':
            n_nowager += 1
            entries.append({'id': r['id'], 'game_date': r['game_date'],
                            'shape': sh, 'provenance': 'not_priced',
                            'reason': 'no wager was placed'})
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
            if mm:
                want = float(mm.group('line'))
                cand = [x for x in props
                        if str(x.get('game_date')) == str(r['game_date'])
                        and str(x.get('player_name') or '').strip().lower()
                        == mm.group('player').strip().lower()
                        and str(x.get('direction') or '').lower()
                        == mm.group('dir').lower()
                        and x.get('prop_line') is not None
                        and abs(float(x['prop_line']) - want) < 1e-6]
                over = mm.group('dir').lower() == 'over'
                for col in (('close_over_odds', 'book_over_odds') if over
                            else ('close_under_odds', 'book_under_odds')):
                    v = med([x.get(col) for x in cand])
                    if v is not None:
                        price, src = v, f'mlb_pipeline_props.{col}'
                        break
        if price is None and g and sh in ('TOTAL', 'SPREAD'):
            rl = by_gid.get(str(g['game_id']), [])
            if rl:
                if sh == 'TOTAL':
                    col = ('over_odds'
                           if re.search(r'\bOver\b',
                                        str(r.get('pick_label')), re.I)
                           else 'under_odds')
                else:
                    col = 'spread_home_odds'
                v = med([x.get(col) for x in rl])
                if v is not None:
                    price, src = v, f'mlb_line_history.{col}'
        e = {'id': r['id'], 'game_date': r['game_date'], 'sport': r.get('sport'),
             'shape': sh, 'stored_odds': r.get('pick_odds'),
             'pick_label': str(r.get('pick_label'))[:70]}
        if price is not None:
            n_meas += 1
            e.update({'provenance': 'measured', 'source': src,
                      'recovered_odds': price})
        else:
            n_asm += 1
            e.update({'provenance': 'assumed', 'assumed_odds': FALLBACK,
                      'reason': 'no stored price in daily_best_bet_history, '
                                'the best_bet blob, mlb_game_results, '
                                'mlb_pipeline_props or mlb_line_history'})
        entries.append(e)

    print(f'POTD receipts (excluding the two storms): {len(pool)}')
    print(f'  measured price available : {n_meas}')
    print(f'  assumed ({FALLBACK})          : {n_asm}')
    print(f'  not priced (no wager)    : {n_nowager}')
    byshape = collections.Counter((e['shape'], e['provenance'])
                                  for e in entries)
    for k, v in sorted(byshape.items()):
        print(f'     {k[0]:8} {k[1]:10} {v}')
    mism = [e for e in entries if e.get('provenance') == 'measured'
            and e.get('stored_odds') not in (None, '')
            and int(e['stored_odds']) != int(e['recovered_odds'])]
    print(f'  stored != recovered on measured rows: {len(mism)}')
    for e in mism[:5]:
        print(f"     {e['game_date']} stored {e['stored_odds']} vs "
              f"recovered {e['recovered_odds']}")

    if '--write' in sys.argv:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(
            {'generated_for': '2026-11-03',
             'why': ('public_receipts.audit is FROZEN (PATCH returns 204 and '
                     'changes nothing), so assumed-price provenance cannot '
                     'live in the row. -115 alone is not a marker: 74 POTD '
                     'rows sit at -115 and two of those are real prices.'),
             'fallback_odds': FALLBACK,
             'counts': {'measured': n_meas, 'assumed': n_asm,
                        'not_priced': n_nowager},
             'assumed_ids': sorted(e['id'] for e in entries
                                   if e.get('provenance') == 'assumed'),
             'entries': entries}, indent=1), encoding='utf-8')
        print(f'\nwrote {OUT}')
    else:
        print('\n(pass --write to refresh the manifest)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
