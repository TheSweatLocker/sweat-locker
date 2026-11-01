"""What does the engine actually say about the hand-picked NBA spreads?

Andy hand-selected Cavs -9.5, Pelicans +4.5, Grizzlies -8.5 and reports a
sweep last night. Before any claim about those plays, verify every tuple
against the DB (CLAUDE.md hard rule 1):

  1. the GAME exists, on what date, with which teams home/away
  2. the LINE is one books actually post (line_history modal, with n books)
  3. what the ENGINE said: primary_play label/tier/conviction/_lr_p_home_win
  4. last night's receipts, graded, so "swept" is checked not repeated

USAGE
  python _nba_handpick_audit_1031.py
"""
from __future__ import annotations

import collections
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

HERE = Path(__file__).parent
_env = HERE / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}

NAMES = ('Cavaliers', 'Pelicans', 'Grizzlies')
DATES = ('2026-10-30', '2026-10-31', '2026-11-01')


def g(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H, timeout=120,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:200]))
        return None
    return r.json()


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return None
    return v if isinstance(v, (dict, list)) else None


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    # ── what columns does nba_game_context even have? ────────────────
    probe = g('nba_game_context', select='*', limit=1)
    if not probe:
        print('nba_game_context unreadable')
        return 1
    cols = sorted(probe[0].keys())
    print('=== nba_game_context columns (%d) ===' % len(cols))
    print('  ' + ', '.join(cols))

    want = [c for c in ('game_id', 'game_date', 'home_team', 'away_team',
                        'close_spread', 'close_total', 'home_ml_close',
                        'away_ml_close', 'primary_play', 'tip_off_utc',
                        'kickoff_utc', 'commence_time')
            if c in cols]
    sel = ','.join(want)

    print('\n=== NBA slate %s .. %s ===' % (DATES[0], DATES[-1]))
    rows = g('nba_game_context', select=sel,
             game_date='in.(%s)' % ','.join(DATES),
             order='game_date.asc', limit=200) or []
    print('  %d context rows' % len(rows))

    by_date = collections.defaultdict(list)
    for x in rows:
        by_date[str(x.get('game_date'))].append(x)

    for d in DATES:
        gs = by_date.get(d) or []
        print('\n  ---- %s : %d games ----' % (d, len(gs)))
        for x in gs:
            pp = jd(x.get('primary_play')) or {}
            lbl = pp.get('label') or pp.get('pick') or '-'
            tier = pp.get('tier') or '-'
            conv = pp.get('conviction')
            plr = pp.get('_lr_p_home_win')
            mkt = pp.get('market') or '-'
            print('    %-12s @ %-12s  cs %-6s  ml %s/%s'
                  % (str(x.get('away_team'))[:12], str(x.get('home_team'))[:12],
                     x.get('close_spread'),
                     x.get('home_ml_close'), x.get('away_ml_close')))
            print('        engine: %-26s %-9s conv %-5s lr_p %-7s mkt %s'
                  % (str(lbl)[:26], tier,
                     ('%g' % conv) if conv is not None else '-',
                     ('%.4f' % plr) if isinstance(plr, (int, float)) else '-',
                     mkt))

    # ── the three named teams: find their games + the posted line ────
    print('\n=== THE THREE HAND-PICKS: do the lines exist? ===')
    for nm in NAMES:
        hits = [x for x in rows
                if nm.lower() in str(x.get('home_team')).lower()
                or nm.lower() in str(x.get('away_team')).lower()]
        if not hits:
            print('\n  %s -- NO GAME in %s' % (nm, ', '.join(DATES)))
            continue
        for x in hits:
            home = str(x.get('home_team'))
            away = str(x.get('away_team'))
            is_home = nm.lower() in home.lower()
            print('\n  %s : %s @ %s on %s  (%s)'
                  % (nm, away, home, x.get('game_date'),
                     'HOME' if is_home else 'AWAY'))
            lh = g('line_history', game_id='eq.%s' % x.get('game_id'),
                   sport='eq.NBA', market='eq.spread',
                   select='book,side,line,price,captured_at',
                   order='captured_at.desc', limit=600) or []
            if not lh:
                print('      line_history: NO ROWS for game_id %s'
                      % x.get('game_id'))
                continue
            newest = max(str(h['captured_at']) for h in lh)
            cur = [h for h in lh if str(h['captured_at']) == newest]
            side = 'home' if is_home else 'away'
            mine = [h for h in cur
                    if str(h.get('side') or '').lower() == side
                    and h.get('line') is not None]
            print('      captured %s, %d book rows that capture'
                  % (newest[:16], len(cur)))
            if mine:
                cnt = collections.Counter(float(h['line']) for h in mine)
                for ln, n in cnt.most_common():
                    px = [fl(h.get('price')) for h in mine
                          if fl(h.get('line')) == ln and h.get('price')]
                    pxs = ('%+d..%+d' % (min(px), max(px))) if px else 'n/a'
                    print('        %s %+g  across %d books  price %s'
                          % (side, ln, n, pxs))
            else:
                print('        no %s-side rows at that capture' % side)

    # ── last night, graded ───────────────────────────────────────────
    print('\n=== 2026-10-30 NBA receipts (is "swept" true?) ===')
    recs = g('public_receipts',
             select='id,surface,sport,game_date,market,pick_label,pick_line,'
                    'pick_odds,result,matchup,tier,voided_at',
             sport='eq.NBA', game_date='eq.2026-10-30',
             order='surface.asc', limit=400) or []
    live = [x for x in recs if not x.get('voided_at')]
    print('  %d receipts (%d live, %d voided)'
          % (len(recs), len(live), len(recs) - len(live)))
    tally = collections.Counter()
    for x in live:
        tally[(str(x.get('surface')), str(x.get('result') or 'PENDING'))] += 1
        print('    %-12s %-8s %-26s %-7s %-6s %s'
              % (str(x.get('surface'))[:12], str(x.get('market'))[:8],
                 str(x.get('pick_label'))[:26],
                 str(x.get('pick_odds')), str(x.get('tier'))[:6],
                 x.get('result')))
    print('\n  by surface/result:')
    for k in sorted(tally):
        print('    %-14s %-8s %d' % (k[0], k[1], tally[k]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
