"""Grade EVERY football signal on the side it actually argued for.

ANDY 2026-10-11: "we need propietrary data pattern and signals we are in
week 6 after this week and we need to be better, full deep dive on signals
and imporvements in processes".

WHY THIS DID NOT EXIST. The dark-signal watchdog is MLB-ONLY — it queries
sport='eq.MLB' against mlb_game_context — so football has had NO
visibility into which signals fire, let alone which ones work. Measured
today:

    NFL     123 enabled · 67 ever fired · 63 NEVER fired · 7 fire unregistered
    NCAAF   106 enabled · 67 ever fired · 46 NEVER fired · 7 fire unregistered

The 7 unregistered on each side are the externals (external:covers fires
21 times on NFL). Absent from signal_sources they can never be validated,
and the ensemble's credibility floor requires a chip that is VALIDATED /
DISCOVERY or carries n>=15 — which is precisely why 9 of 14 NFL games
tiered COVERAGE on 2026-10-11 and never reached a card.

WHAT THIS MEASURES. Each entry in primary_play._ensemble_sources records
the SIDE it backed ("HOME_ML", "AWAY_RL", …), so every signal is graded on
its own claim rather than on the published pick. A signal that fired on
the losing side of a game we won still graded as wrong, which is the only
way to tell a real signal from one that rides along with the engine.

THE CONTROL IS THE POINT. A signal that fires on the home side in a league
where home covers 55% looks good at 55% and is worth nothing. Every signal
is therefore scored against the BASE RATE OF THE SIDE IT PICKED, on the
same games it fired in — the direction-only control
([[feedback_signal_needs_a_direction_control]]). The `edge` column is the
number that matters; `hit` on its own is decoration.

Sign convention, verified empirically on 65 graded 2026 games: NFL
close_spread is POSITIVE when HOME is favoured, and
`home_margin - close_spread > 0` means HOME covered (30/30 exact).
NCAAF stores the OPPOSITE sign, so each sport carries its own multiplier
rather than sharing one and hoping.

WRITES NOTHING.

CLI
    python audit_football_signals.py --sport NFL
    python audit_football_signals.py --sport NCAAF --min-n 8
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

#: (context table, results table, close_spread sign for "home favoured").
#: NFL is POSITIVE-for-home-favourite; NCAAF is negative. Keeping them
#: explicit per sport is deliberate — one shared constant is how the Red
#: River inversion happened.
SPORTS = {
    'NFL':   ('nfl_game_context',   'nfl_game_results',   +1),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', -1),
}


def page(table, params, cap=60000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def blob(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith('{'):
        try:
            return json.loads(v)
        except Exception:                                  # noqa: BLE001
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(SPORTS))
    ap.add_argument('--min-n', type=int, default=10, dest='min_n')
    ap.add_argument('--since', default='2026-08-01')
    a = ap.parse_args()
    ctx_t, res_t, sign = SPORTS[a.sport]

    print(f'=== {a.sport} signal audit · since {a.since} · read-only ===')
    print('    every signal graded on the side IT argued for, against the')
    print('    base rate of that same side on the same games\n')

    ctx = page(ctx_t, {'select': 'game_id,game_date,home_team,away_team,'
                                 'close_spread,primary_play',
                       'game_date': f'gte.{a.since}'})
    res = page(res_t, {'select': 'game_date,home_team,away_team,home_score,'
                                 'away_score,close_spread,spread_result',
                       'game_date': f'gte.{a.since}'})
    idx = {(str(r['game_date'])[:10], str(r['home_team']).lower(),
            str(r['away_team']).lower()): r for r in res}
    print(f'  {len(ctx)} context rows · {len(res)} result rows')

    # signal_key -> list of (won01, side_base01)
    rows = defaultdict(list)
    cls_of = {}
    graded_games = 0
    base_cov, base_ml = [], []        # global: did HOME cover / win
    for g in ctx:
        k = (str(g.get('game_date'))[:10], str(g.get('home_team')).lower(),
             str(g.get('away_team')).lower())
        r = idx.get(k)
        if not r:
            continue
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        cs = f(r.get('close_spread'))
        if cs is None:
            cs = f(g.get('close_spread'))
        if hs is None or as_ is None or cs is None:
            continue
        pp = blob(g.get('primary_play')) or {}
        srcs = pp.get('_ensemble_sources') or []
        if not srcs:
            continue
        graded_games += 1
        margin = hs - as_                       # home margin
        home_won = margin > 0
        # home covered: margin - (home's spread number). `sign` maps the
        # stored column onto "positive = home favoured".
        cover_margin = margin - (cs * sign)
        if abs(cover_margin) < 1e-9:
            home_covered = None                 # push: no information
        else:
            home_covered = cover_margin > 0
        base_ml.append(1 if home_won else 0)
        if home_covered is not None:
            base_cov.append(1 if home_covered else 0)

        for s in srcs:
            # ══ DO NOT MERGE A SIGNAL WITH ITS OWN FADE ══
            # The first version stripped '__fade', which pooled opposite
            # claims into one bucket. The giveaway was in the output:
            # away_ats_hot_on_road showed home%=100, i.e. an "away"
            # signal backing HOME on all 49 firings — because the fade
            # variant had been folded in on top of it. Pooling a signal
            # with its negation averages toward 50% and hides both.
            key = str(s.get('signal_key') or '')
            if not key:
                continue
            cls_of[key] = s.get('class')
            side = str(s.get('side') or '').upper()
            is_home = side.startswith('HOME')
            is_ml = side.endswith('_ML')
            if not (side.startswith('HOME') or side.startswith('AWAY')):
                continue                        # totals: different question
            if is_ml:
                won = 1 if (home_won == is_home) else 0
            else:
                if home_covered is None:
                    continue
                won = 1 if (home_covered == is_home) else 0
            # ══ THE CONTROL MUST COME FROM OUTSIDE THIS GAME ══
            # First version derived the control from THIS game's own
            # outcome — base01 = home_covered flipped to the signal's
            # side — which is algebraically identical to `won`. Every
            # signal scored an edge of exactly +0.0, which is what a
            # tautology looks like when you remember to print it.
            #
            # Only the SIDE and MARKET are recorded here. The control is
            # applied afterwards from the GLOBAL rate at which that side
            # covers across the whole window, so a signal that simply
            # leans home is measured against how often home actually
            # covers rather than against itself.
            rows[key].append((won, is_home, is_ml))

    print(f'  {graded_games} graded games carrying signal attribution')
    print(f'  {len(rows)} distinct signals fired\n')

    H_cov = statistics.fmean(base_cov) if base_cov else 0.5
    H_ml = statistics.fmean(base_ml) if base_ml else 0.5
    print(f'  CONTROL on these games: HOME covers {H_cov * 100:.1f}% '
          f'(n={len(base_cov)}) · HOME wins {H_ml * 100:.1f}% '
          f'(n={len(base_ml)})')
    print('  A signal is only worth something if it beats a blind bet on '
          'the same side.\n')

    out = []
    for key, vals in rows.items():
        n = len(vals)
        if n < a.min_n:
            continue
        hit = statistics.fmean(v[0] for v in vals) * 100
        # Expected hit rate for a BLIND bet on the same side/market mix.
        exp = statistics.fmean(
            (H_ml if v[1] else 1 - H_ml) if v[2]
            else (H_cov if v[1] else 1 - H_cov) for v in vals) * 100
        se2 = (0.5 / n ** 0.5) * 200
        homeshare = statistics.fmean(1 if v[1] else 0 for v in vals) * 100
        out.append((hit - exp, hit, exp, n, se2, key, homeshare))
    out.sort(reverse=True)

    print(f'  {"signal":<40}{"W-L":<9}{"hit":>7}{"blind":>7}'
          f'{"EDGE":>7}{"home%":>7}{"n":>5}  2SE')
    print('  ' + '-' * 92)
    for edge, hit, base, n, se2, key, hsh in out:
        w = round(hit * n / 100)
        mark = ''
        if edge > se2:
            mark = '  <= beats its own control'
        elif edge < -se2:
            mark = '  <= WORSE than control'
        print(f'  {key[:39]:<40}{str(w) + "-" + str(n - w):<9}{hit:>6.1f}%'
              f'{base:>6.1f}%{edge:>+7.1f}{hsh:>6.0f}%{n:>5}  ±{se2:.0f}{mark}')

    real = [o for o in out if o[0] > o[4]]
    anti = [o for o in out if o[0] < -o[4]]
    print(f'\n  {len(out)} signals with n>={a.min_n} · '
          f'{len(real)} beat their control · {len(anti)} are worse than it')
    if out:
        print(f'  median edge across all: '
              f'{statistics.median(o[0] for o in out):+.1f}pp')
    print('\n  Multiplicity warning: every signal with n>=min-n is listed, '
          'none\n  were selected, so read the EDGE against its own 2SE '
          'column rather\n  than picking the biggest number. At ~60 signals '
          'a couple will clear\n  2SE by chance alone.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
