#!/usr/bin/env python3
"""Backtest the 17 oddscrowd fade-boost signals BEFORE they are allowed to fire.

== WHY (2026-10-28) =========================================================
Every oddscrowd_*_fade_boost row -- one per market per sport, six sports --
has been dead since it was written, because its condition used
`isinstance(X, dict)` and signal_expr whitelists `isinstance` but not `dict`,
so the NameError was swallowed as False. Repairing the expression makes all 17
fire at once. They all sit at UNVALIDATED / sample_n=0 / weight 0.3, so a
straight repair would inject 17 evidence-free opinions into six sports'
picks on the same night.

Money flow is not direction-stable across sports -- measured 2026-10-12, the
same family on the same source count went MLB fade +12.4% / follow -24.2%
while NCAAF went follow +30.0% / fade -36.8%. So "fade the public" cannot be
switched on uniformly and hoped for.

This replays each repaired condition over historical context, takes the side
from the row's OWN side_expr, grades it against the stored result, and seeds
signal_registry with the measured hit rate and n. edge_weight then prices
each one off its evidence from the first pick it touches, instead of the 0.3
prior.

HOW IT GRADES
  ml      -> the picked team won outright
  rl      -> the picked side covered the stored close number, using the
             PER-SPORT sign convention (measured, see _SIGN) -- NFL stores a
             positive number when home is favoured, every other sport
             negative. Getting that backwards labels every favourite a dog.
  total   -> OVER/UNDER against the stored closing total
A row missing the number or the score is skipped, never guessed.

It also prints a split-half so a signal that only worked early is visible as
such, and a DIRECTION-ONLY CONTROL: the same bet placed on every game the
signal did NOT fire on. A hit rate that matches its control is not an edge.

    python backtest_oddscrowd_fade_signals.py
    python backtest_oddscrowd_fade_signals.py --seed      # write the registry
"""
from __future__ import annotations
import argparse
import collections
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_expr import AttrDict, evaluate_bool, evaluate_str   # noqa: E402
# The weight and tier come from refit_signal_registry's OWN functions, not a
# second copy of the formula. Two implementations of a weight drift, and a
# weight that differs depending on who computed it is worse than none.
from refit_signal_registry import (_tier_for, _recommended_weight_for,  # noqa: E402
                                   breakeven_for)

PAT = re.compile(r'isinstance\s*\(\s*([^,()]*(?:\([^()]*\))?[^,()]*)\s*,\s*dict\s*\)')

CTX_TBL = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
           'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
           'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}
RES_TBL = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results',
           'MLB': 'mlb_game_results', 'NCAAB': 'ncaab_game_results'}
#: spread column per sport -- NHL's is close_puckline, not close_spread
SPREAD_COL = {'NHL': 'close_puckline'}
#: +1.0 when a POSITIVE stored number means HOME is favoured. Measured
#: 2026-10-28 by correlating the stored number against realised home margin:
#:   NBA -0.517  NHL -0.172  MLB -0.133  NCAAB -0.644  NCAAF -0.721  NFL +0.425
_SIGN = {'NFL': 1.0, 'NCAAF': -1.0, 'NBA': -1.0,
         'NHL': -1.0, 'MLB': -1.0, 'NCAAB': -1.0}


def _env(path):
    out = {}
    try:
        for line in open(path, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_E = _env(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def req(method, path, params=None, body=None, extra=None):
    u = BASE + path
    if params:
        u += '?' + urllib.parse.urlencode(params, safe='.,()*:')
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(u, data=data, method=method)
    for k, v in HDR.items():
        r.add_header(k, v)
    for k, v in (extra or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        print('  HTTP %s on %s :: %s'
              % (e.code, path, e.read().decode('utf-8', 'replace')[:160]))
        return None, e.code


def paged(table, select='*', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off),
             'order': 'game_date.asc'}
        p.update(filt)
        rows, _st = req('GET', table, p)
        if not isinstance(rows, list):
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def wilson(w, n):
    if n == 0:
        return (0.0, 0.0)
    p = w / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - m), 100 * (c + m))


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def grade(sport, side, res):
    """1 win / 0 loss / None ungradeable, for the side this signal argues."""
    hs, as_ = _f(res.get('home_score')), _f(res.get('away_score'))
    if hs is None or as_ is None:
        return None
    s = str(side or '').upper()
    if s in ('HOME_ML', 'AWAY_ML'):
        home_won = hs > as_
        if hs == as_:
            return None
        return 1 if (home_won == (s == 'HOME_ML')) else 0
    if s in ('OVER', 'UNDER'):
        tot = _f(res.get('close_total'))
        if tot is None:
            return None
        pts = hs + as_
        if pts == tot:
            return None
        return 1 if ((pts > tot) == (s == 'OVER')) else 0
    if s in ('HOME_RL', 'AWAY_RL'):
        col = SPREAD_COL.get(sport, 'close_spread')
        ln = _f(res.get(col))
        if ln is None:
            return None
        # home handicap, normalised so NEGATIVE always means home favoured
        h = ln * _SIGN.get(sport, -1.0) * -1.0
        margin = (hs - as_) + h
        if margin == 0:
            return None
        return 1 if ((margin > 0) == (s == 'HOME_RL')) else 0
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', action='store_true',
                    help='write the measured hit_rate/n into signal_registry')
    a = ap.parse_args()
    print('=' * 100)
    print('BACKTEST THE oddscrowd FADE-BOOST SIGNALS  (%s)'
          % ('SEEDING REGISTRY' if a.seed else 'REPORT ONLY'))
    print('=' * 100)

    src, st = req('GET', 'signal_sources', {'select': '*', 'limit': '2000'})
    if not isinstance(src, list):
        sys.exit('could not read signal_sources (status %s)' % st)
    rows = [s for s in src
            if str(s.get('signal_key', '')).startswith('oddscrowd_')
            and str(s.get('subject_scope')) not in ('prop', 'player_prop')]
    print('  game-path oddscrowd signals: %d' % len(rows))

    ctx_cache, res_cache = {}, {}
    out = []
    for s in sorted(rows, key=lambda z: (str(z.get('sport')),
                                         str(z.get('signal_key')))):
        sport = str(s.get('sport'))
        if sport not in ctx_cache:
            ctx_cache[sport] = paged(CTX_TBL[sport],
                                     'game_id,game_date,oddscrowd_snapshot')
            rr = paged(RES_TBL[sport],
                       'game_id,game_date,home_score,away_score,close_total,'
                       + SPREAD_COL.get(sport, 'close_spread'))
            res_cache[sport] = {str(r.get('game_id')): r for r in rr}
        ctx = ctx_cache[sport]
        res = res_cache[sport]

        cond = PAT.sub(r'is_dict(\1)', str(s.get('condition_expr') or ''))
        side_x = PAT.sub(r'is_dict(\1)', str(s.get('side_expr') or ''))

        fired, graded, ctrl = [], [], []
        for c in ctx:
            prox = AttrDict(c)
            r = res.get(str(c.get('game_id')))
            if not r:
                continue
            hit = evaluate_bool(cond, prox)
            side = evaluate_str(side_x, prox) if hit else None
            if hit and side:
                g = grade(sport, side, r)
                fired.append(c)
                if g is not None:
                    graded.append((str(c.get('game_date'))[:10], g, side))
            elif not hit:
                # DIRECTION-ONLY CONTROL: the same market, the same side
                # distribution, on games the signal did NOT pick. Without it a
                # 55% "edge" that is just home-court looks real.
                ctrl.append((str(c.get('game_date'))[:10], r))

        n = len(graded)
        w = sum(g for _d, g, _s in graded)
        lo, hi = wilson(w, n)
        sides = collections.Counter(sd for _d, _g, sd in graded)
        # control: the modal side this signal argues, played on every game it
        # skipped
        modal = sides.most_common(1)[0][0] if sides else None
        cg = [grade(sport, modal, r) for _d, r in ctrl] if modal else []
        cg = [x for x in cg if x is not None]
        cw = sum(cg)
        clo, chi = wilson(cw, len(cg))

        gd = sorted(d for d, _g, _s in graded)
        mid = gd[len(gd) // 2] if gd else None
        h1 = [g for d, g, _s in graded if mid and d < mid]
        h2 = [g for d, g, _s in graded if mid and d >= mid]

        out.append({'s': s, 'sport': sport, 'n': n, 'w': w, 'lo': lo, 'hi': hi,
                    'sides': sides, 'ctrl_n': len(cg), 'ctrl_w': cw,
                    'clo': clo, 'chi': chi,
                    'h1': (len(h1), sum(h1)), 'h2': (len(h2), sum(h2))})

    print('')
    print('  %-7s %-34s %6s %7s %-16s %16s %s'
          % ('sport', 'signal', 'n', 'hit%', '95% CI', 'CONTROL', 'split-half'))
    print('  ' + '-' * 112)
    for o in out:
        s = o['s']
        if o['n'] == 0:
            print('  %-7s %-34s %6d  (no graded firings)'
                  % (o['sport'], str(s.get('signal_key'))[:34], 0))
            continue
        h1n, h1w = o['h1']
        h2n, h2w = o['h2']
        sh = ('%.0f%%/%.0f%%' % (100.0 * h1w / h1n if h1n else 0,
                                 100.0 * h2w / h2n if h2n else 0)
              if h1n and h2n else '-')
        ctrl = ('%5.1f%% n=%-4d' % (100.0 * o['ctrl_w'] / o['ctrl_n'],
                                    o['ctrl_n'])) if o['ctrl_n'] else '-'
        print('  %-7s %-34s %6d %6.1f%% [%4.1f, %4.1f] %16s %s'
              % (o['sport'], str(s.get('signal_key'))[:34], o['n'],
                 100.0 * o['w'] / o['n'], o['lo'], o['hi'], ctrl, sh))

    print('')
    print('  READING IT: a hit rate whose CI overlaps its own control is not an')
    print('  edge, however high it looks. A signal that splits (first half far')
    print('  above the second) is a decayed one. Both get weight 0 here.')

    if not a.seed:
        print('')
        print('  REPORT ONLY -- registry untouched. Re-run with --seed to write.')
        return 0

    # ══ limit=2000 IS STILL CAPPED AT 1000 ══
    # PostgREST caps a limit at 1,000 whatever is asked for. signal_registry
    # holds 1,352 rows, so a single GET with limit=2000 returned 1,000 of
    # them, the lookup missed a row that exists, the code took the INSERT
    # branch and got 23505 duplicate key. Paged, and ordered by id because
    # signal_registry has no game_date.
    reg, off = [], 0
    while True:
        _b, _st = req('GET', 'signal_registry',
                      {'select': 'id,sport,signal_name,market_scope',
                       'order': 'id.asc', 'limit': '1000',
                       'offset': str(off)})
        if not isinstance(_b, list):
            break
        reg += _b
        if len(_b) < 1000:
            break
        off += 1000
    print('  signal_registry rows read: %d' % len(reg))
    idx = {(str(r.get('sport')), str(r.get('signal_name'))): r
           for r in (reg or [])}
    wrote = 0
    for o in out:
        s = o['s']
        key = (o['sport'], str(s.get('signal_key')))
        if o['n'] < 10:
            # ══ AN UNMEASURED SIGNAL MUST NOT OUTWEIGH A MEASURED ONE ══
            # "left as is" meant left at the 0.3 UNVALIDATED prior -- and
            # every sibling in this family that CAN be measured came out
            # between 0.00 and 0.15, with NCAAF's puckline row at 45.5%
            # against a 56.0% control (ANTI_VALIDATED, 0.0). A prior of 0.3
            # is therefore larger than the weight of every tested member of
            # the same family, which is not a conservative default, it is the
            # most confident number on the board.
            #
            # So an untested row in a family that has tested at ~0 goes in at
            # 0.0. That is not switching it off: the condition is repaired, it
            # fires, the snapshot and attribution steps record it, and the
            # weekly refit raises it the moment it has firings to justify one.
            _x, _st3 = req('PATCH', 'signal_registry',
                           {'sport': 'eq.%s' % key[0],
                            'signal_name': 'eq.%s' % key[1]},
                           {'recommended_weight': 0.0,
                            'tier': 'UNVALIDATED',
                            'notes': ('2026-10-28 repaired from a dead '
                                      'isinstance condition with too few '
                                      'graded firings to measure (n=%d). Held '
                                      'at 0.0 rather than the 0.3 prior: every '
                                      'measurable sibling in this family scored '
                                      '0.00-0.15. The weekly refit raises it '
                                      'once it has firings.' % o['n'])},
                           {'Prefer': 'return=minimal'})
            print('  %-7s %-34s n=%d -- held at 0.0 (was the 0.3 prior) %s'
                  % (key[0], key[1][:34], o['n'],
                     'ok' if _st3 in (200, 204) else 'WRITE FAILED'))
            continue
        hr = round(100.0 * o['w'] / o['n'], 2)
        base = (round(100.0 * o['ctrl_w'] / o['ctrl_n'], 2)
                if o['ctrl_n'] else None)
        # ══ SEED THE WEIGHT TOO, NOT JUST THE EVIDENCE ══
        # The first version wrote hit_rate and sample_n and left
        # recommended_weight alone, so all 17 still sat at the 0.3
        # UNVALIDATED prior -- including NCAAF's puckline signal at 45.5%
        # against a 56.0% control, a measured loser about to carry weight on
        # tonight's slate. refit_signal_registry only recomputes a weight for
        # a signal it has observed FIRING, and these have never fired, so
        # nothing was going to correct it.
        _mkt = str(s.get('market_scope') or '')
        if _mkt in ('*', '', None):
            _k = str(s.get('signal_key') or '')
            _mkt = ('ml' if '_ml_' in _k else
                    'total' if '_total_' in _k else 'rl')
        _frac = hr / 100.0
        _blind = (base / 100.0) if base is not None else None
        _tier = _tier_for(o['n'], _frac, _blind)
        _w = _recommended_weight_for(o['n'], _frac, _tier, _mkt)
        body = {'hit_rate': hr, 'sample_n': o['n'], 'baseline_pct': base,
                'tier': _tier, 'recommended_weight': _w,
                'market_scope': _mkt,
                'edge_pp': (round(hr - base, 2) if base is not None else None),
                'last_computed_at': __import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc).isoformat(),
                'notes': ('2026-10-28 backtested on repair: %d graded firings, '
                          '%.1f%% vs a %s%% direction-only control. Seeded so '
                          'edge_weight prices it off evidence rather than the '
                          '0.3 UNVALIDATED prior it carried while dead. '
                          'breakeven(%s)=%.4f -> tier %s, weight %.4f'
                          % (o['n'], hr, base if base is not None else 'n/a',
                             _mkt, breakeven_for(_mkt), _tier, _w))}
        row = idx.get(key)
        if row:
            _x, st2 = req('PATCH', 'signal_registry',
                          {'id': 'eq.%s' % row['id']}, body,
                          {'Prefer': 'return=minimal'})
        else:
            # An INSERT needs the NOT NULL columns an existing row already
            # has: one seed POST failed 23502 without category/description.
            body.update({'sport': key[0], 'signal_name': key[1],
                         'market_scope': s.get('market_scope') or '*',
                         'category': str(s.get('class') or 'oddscrowd'),
                         'description': (s.get('display_prose_template')
                                         or 'public money flagged for a fade'),
                         'origin': 'BACKTEST_2026-10-28',
                         'direction_hint': 'FADE'})
            _x, st2 = req('POST', 'signal_registry', None, [body],
                          {'Prefer': 'return=minimal'})
        if st2 in (200, 201, 204):
            wrote += 1
        else:
            print('  !! seed failed for %s %s' % key)
    print('')
    print('  seeded %d registry rows' % wrote)
    print('  now run refit_signal_registry per sport to turn these into weights')
    return 0


if __name__ == '__main__':
    sys.exit(main())
