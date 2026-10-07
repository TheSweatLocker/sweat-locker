#!/usr/bin/env python3
"""Who is on the game, how often is each of them right, and does AGREEMENT win?

WHY (2026-10-07)
----------------
Andy, after I narrowed too far onto the models alone:

  "there should be an overall vote so if it was 3/5 models on NYY that would
   be NYY, then a broader consensus panel should be tracked — are the sharps
   on the Yankees, engine overall, external handicappers, team stats."

So two levels, and both get graded.

  LEVEL 1  the models vote among themselves  -> one 'models' voter
  LEVEL 2  that vote sits on a panel beside the other sources:

      models        majority of panel / jerry_model / model_v4 / MC / matchup
      engine        primary_play — what the ensemble concluded
      jerry         the published call — what the user was actually told
      sharp         money% above bets% — where the money is vs the tickets
      externals     handicappers, WEIGHTED by their own graded record
      stats         confluence breakdown (xERA, L3 ERA, OPS heat, ...)
      situational   ATS / cover-rate edge

Then the question that actually matters, which no scorecard so far has
answered: WHEN MORE OF THEM AGREE, DOES IT WIN MORE? That is the difference
between a panel and a pile of opinions, and it is bucketed at the bottom.

POINT-IN-TIME, OR IT IS WORTHLESS
Everything is read from `jerry_reads.input_snapshot`, written once when the
read is generated. MLB freezes the whole panel in there — money_flow,
externals (with each source's 30d n), confluence, team_snapshot, situational.
Reads generated after their own game date are dropped (MLB: 70 of 786, 9.5%).
The mutable context tables are never consulted.

    python consensus_scorecard.py
    python consensus_scorecard.py --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from model_scorecard import (RESULTS, CONTEXT, SPREAD_COL, page, jl, f,
                             grade, margin_to_beat, extract_models)

VOTERS = ['models', 'engine', 'jerry', 'sharp', 'handicappers', 'public',
          'stats', 'situational']


def opposite(s):
    return 'AWAY' if s == 'HOME' else ('HOME' if s == 'AWAY' else None)


def vote_models(sport, snap, need):
    """Majority side across the individual models. Ties -> no vote."""
    sides = []
    for _n, margin in extract_models(sport, snap).items():
        if abs(margin - need) < 0.5:
            continue
        sides.append('HOME' if margin > need else 'AWAY')
    if not sides:
        return None, 0, 0
    c = collections.Counter(sides)
    top, n = c.most_common(1)[0]
    if len(c) > 1 and c.most_common()[1][1] == n:
        return None, n, len(sides)          # genuine tie
    return top, n, len(sides)


def vote_sharp(snap):
    """Side the MONEY is on relative to the TICKETS, on the side markets.

    money_pct above bets_pct means the average wager on that side is bigger
    than the average wager on the other — the standard sharp tell. Totals are
    excluded here because this panel votes on sides.
    """
    mf = (snap.get('money_flow') or {}) if isinstance(snap, dict) else {}
    if not mf:
        mk = (snap.get('market') or {}) if isinstance(snap, dict) else {}
        mf = mk.get('money_flow') or {}
    best, side = 0.0, None
    for market in ('ml', 'rl'):
        d = mf.get(market) or {}
        pick = str(d.get('pick') or d.get('side') or '').upper()
        if pick not in ('HOME', 'AWAY'):
            continue
        div = f(d.get('div'))
        if div is None:
            b, m = f(d.get('bets')), f(d.get('money'))
            div = (m - b) if None not in (b, m) else None
        if div is None:
            continue
        if abs(div) > abs(best):
            best, side = div, pick
    if side is None or abs(best) < 5:
        return None
    return side if best > 0 else opposite(side)


def _is_flow(x):
    """Is this 'external' a handicapper's PICK, or a public bet% report?

    They are stored in the same list and mean opposite things. Measured on
    6,935 MLB snapshot entries: 4,863 (70%) are money-flow reports —
    "Action bet%: Pirates 75% / Reds 25%", side = the PUBLIC side — and only
    2,072 are an actual pick ("Betfirm (Brandon Lee): Pirates -126").

    Voting them together makes 'externals' mostly a public-money voter, which
    is why it graded 45.0%: that is the fade signal upside-down, not a verdict
    on handicappers. Same trap as the split-source columns in The Fade.
    """
    rt = str(x.get('raw_text') or '').lower()
    cf = str(x.get('confidence') or '').lower()
    return ('bet%' in rt or 'bets' in rt or 'public' in cf or '%' in cf)


def _ext_sides(snap, flow):
    ex = (snap.get('externals') or []) if isinstance(snap, dict) else []
    tal = collections.defaultdict(float)
    for x in ex:
        if not isinstance(x, dict):
            continue
        if _is_flow(x) != flow:
            continue
        if str(x.get('surface')) not in ('ml', 'rl', 'spread'):
            continue
        sd = str(x.get('side') or '').upper()
        if sd not in ('HOME', 'AWAY'):
            continue
        n = f(x.get('source_30d_n')) or 0
        tal[sd] += (min(n, 300) / 100.0) if n else 0.25
    if not tal:
        return None
    top = max(tal, key=tal.get)
    other = max((v for k, v in tal.items() if k != top), default=0.0)
    return top if tal[top] > other else None


def vote_handicappers(snap):
    """Only the sources that actually make a pick, weighted by their 30d n."""
    return _ext_sides(snap, flow=False)


def vote_public(snap):
    """Where the PUBLIC is, from the bet% reports. Tracked as its own voter
    precisely because it is expected to be a fade, not a follow."""
    return _ext_sides(snap, flow=True)


def vote_stats(snap):
    """Confluence breakdown — each stat signal names a side; majority wins."""
    cf = (snap.get('confluence') or {}) if isinstance(snap, dict) else {}
    bd = cf.get('breakdown') or {}
    sides = [str(v).upper() for v in bd.values()
             if str(v).upper() in ('HOME', 'AWAY')]
    if not sides:
        return None
    c = collections.Counter(sides)
    top, n = c.most_common(1)[0]
    if len(c) > 1 and c.most_common()[1][1] == n:
        return None
    return top


def vote_situational(snap):
    """ATS / cover-rate edge, when the snapshot carries one."""
    st = (snap.get('situational') or {}) if isinstance(snap, dict) else {}
    if not isinstance(st, dict):
        return None
    hc, ac = f(st.get('home_cover_pct')), f(st.get('away_cover_pct'))
    if None not in (hc, ac) and abs(hc - ac) >= 15:
        return 'HOME' if hc > ac else 'AWAY'
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--since', default='2026-08-01')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    sport = args.sport.upper()
    tbl, ctbl = RESULTS[sport], CONTEXT[sport]
    scol = SPREAD_COL.get(sport, 'close_spread')

    by_gid, by_key = {}, {}
    for g in page(tbl, {'select': f'game_id,game_date,home_team,away_team,'
                                  f'spread_result,{scol}',
                        'game_date': f'gte.{args.since}'}):
        if not g.get('spread_result'):
            continue
        by_gid[str(g.get('game_id'))] = g
        by_key[(str(g.get('game_date'))[:10], str(g.get('home_team')),
                str(g.get('away_team')))] = g
    bridge = {}
    for z in page(ctbl, {'select': 'game_id,game_date,home_team,away_team',
                         'game_date': f'gte.{args.since}'}):
        bridge[str(z.get('game_id'))] = (str(z.get('game_date'))[:10],
                                         str(z.get('home_team')),
                                         str(z.get('away_team')))

    reads = page('jerry_reads',
                 {'select': 'game_id,game_date,generated_at,call_side,'
                            'input_snapshot',
                  'sport': f'eq.{sport}', 'game_date': f'gte.{args.since}'})

    per = collections.defaultdict(lambda: collections.Counter())
    by_agree = collections.defaultdict(lambda: collections.Counter())
    model_vote_depth = collections.defaultdict(lambda: collections.Counter())
    used = 0
    for a in reads:
        gd, ga = str(a.get('game_date'))[:10], str(a.get('generated_at'))[:10]
        if gd and ga and ga > gd:
            continue
        g = by_gid.get(str(a.get('game_id')))
        if g is None:
            k = bridge.get(str(a.get('game_id')))
            g = by_key.get(k) if k else None
        if g is None:
            continue
        need = margin_to_beat(sport, f(g.get(scol)))
        if need is None:
            continue
        snap = jl(a.get('input_snapshot'))
        if not isinstance(snap, dict):
            continue
        sr = g['spread_result']

        mv, m_for, m_tot = vote_models(sport, snap, need)
        # MLB nests primary_play inside `confluence`; the other sports put it
        # at the top level. Looked up in both rather than assumed, because a
        # missing voter silently reads as "the engine had no opinion".
        pp = (snap.get('primary_play')
              or (snap.get('confluence') or {}).get('primary_play')
              or {})
        votes = {
            'models': mv,
            'engine': (str(pp.get('side') or '').upper() or None),
            'jerry': (str(a.get('call_side') or '').upper() or None),
            'sharp': vote_sharp(snap),
            'handicappers': vote_handicappers(snap),
            'public': vote_public(snap),
            'stats': vote_stats(snap),
            'situational': vote_situational(snap),
        }
        votes = {k: v for k, v in votes.items() if v in ('HOME', 'AWAY')}
        if not votes:
            continue
        used += 1

        for k, v in votes.items():
            r = grade(v, sr)
            if r:
                per[k][r] += 1
        if mv and m_tot:
            r = grade(mv, sr)
            if r:
                model_vote_depth[f'{m_for}/{m_tot}'][r] += 1

        c = collections.Counter(votes.values())
        lean, n_for = c.most_common(1)[0]
        if len(c) > 1 and c.most_common()[1][1] == n_for:
            continue                      # panel split evenly, no consensus
        r = grade(lean, sr)
        if r:
            by_agree[f'{n_for} of {len(votes)}'][r] += 1
            by_agree['ANY consensus'][r] += 1

    def line(label, c, pad=16):
        w, l, p = c['WIN'], c['LOSS'], c['PUSH']
        n = w + l
        if n == 0:
            return
        hit = w / n * 100
        flag = '  (n<30)' if n < 30 else ''
        print(f'   {label:<{pad}}{f"{w}-{l}-{p}":>12}{hit:>7.1f}%'
              f'{hit-52.38:>+9.1f}{n:>7}{flag}')

    print(f'=== consensus_scorecard · {sport} · {used} graded games ===')
    print()
    print(f"   {'voter':<16}{'W-L-P':>12}{'hit':>8}{'vs 52.4%':>10}{'n':>7}")
    for v in VOTERS:
        if per.get(v):
            line(v, per[v])
    print()
    print('   MODEL VOTE BY DEPTH (how many of the models agreed)')
    for k in sorted(model_vote_depth):
        line(k, model_vote_depth[k])
    print()
    print('   PANEL CONSENSUS BY AGREEMENT LEVEL')
    for k in sorted(by_agree, key=lambda s: (s == 'ANY consensus', s)):
        line(k, by_agree[k])
    print()
    print('   Breakeven at -110 is 52.38%. If agreement means anything, the')
    print('   deeper buckets should beat the shallower ones.')

    if not args.apply:
        print()
        print('   (report only — re-run with --apply to record)')
        return 0

    rows = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for v, c in per.items():
        w, l, p = c['WIN'], c['LOSS'], c['PUSH']
        n = w + l
        if n == 0:
            continue
        u = w * (100 / 110) - l
        rows.append({'sport': sport, 'surface': f'voter_{v}',
                     'window_key': 'ats_vs_close', 'wins': w, 'losses': l,
                     'pushes': p, 'picks_count': n,
                     'hit_rate': round(w / n * 100, 2),
                     'units_net': round(u, 2),
                     'roi_pct': round(u / (n + p) * 100, 2) if n + p else None,
                     'last_computed_at': now})
    for k, c in by_agree.items():
        w, l, p = c['WIN'], c['LOSS'], c['PUSH']
        n = w + l
        if n == 0:
            continue
        u = w * (100 / 110) - l
        rows.append({'sport': sport, 'surface': 'panel_consensus',
                     'window_key': k.replace(' ', '_'), 'wins': w, 'losses': l,
                     'pushes': p, 'picks_count': n,
                     'hit_rate': round(w / n * 100, 2),
                     'units_net': round(u, 2),
                     'roi_pct': round(u / (n + p) * 100, 2) if n + p else None,
                     'last_computed_at': now})
    r = requests.post(f'{SB}/rest/v1/surface_records'
                      f'?on_conflict=sport,surface,window_key',
                      headers={**H, 'Content-Type': 'application/json',
                               'Prefer': 'resolution=merge-duplicates,'
                                         'return=representation'},
                      data=json.dumps(rows), timeout=60)
    ok = r.status_code in (200, 201, 204)
    print()
    print(f'   wrote {len(rows)} row(s): '
          f'{"ok" if ok else f"FAILED {r.status_code} {r.text[:200]}"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
