#!/usr/bin/env python3
"""How good is each model, individually, against the closing line?

WHY (2026-10-07)
----------------
Andy: "we should know how good Jerry model vs panel is" — and all the others,
tracked, not asserted.

Three problems had to be solved before any of this is honest.

1. LEAKAGE. `nfl_game_context.projected_spread` is MUTABLE and, until today,
   `updated_at` was never written — so a model number sitting on a week-2 row
   cannot be proven to predate the week-2 result. Grading off the context
   table would measure hindsight.

   So every model value here comes from `jerry_reads.input_snapshot.models`,
   which is written once when the read is generated and carries
   `generated_at`. Rows whose snapshot postdates kickoff are dropped.

2. THE BENCHMARK. A model is judged against the CLOSING LINE, not against
   nothing. "Beat the close" is the only standard that cannot be gamed by
   picking soft games: implied side = sign(model_margin - close_spread),
   graded against the actual ATS result.

3. THREE COLUMNS, ONE MODEL. projected_spread, projected_spread_v2 and
   projected_spread_raw are IDENTICAL on 15 of 15 week-5 games — same value
   to two decimals. They are one model in three hats, so they are scored once
   as `matchup`. The panel projection genuinely differs (median 1.54 pts, max
   8.0), so it is scored separately. Counting the same model three times is
   how "the models agree" became a meaningless signal.

WHAT IS SCORED
    jerry        the published call_side — what we actually told users
    matchup      input_snapshot.models.matchup.projected_spread
    panel        input_snapshot.models.panel home/away pts
    primary      the primary_play frozen in the snapshot
Each against the close, on games with a settled ATS result.

Breakeven at -110 is 52.38%. A model below that is costing money even when it
"wins more than it loses".

    python model_scorecard.py                 # all sports
    python model_scorecard.py --sport NFL
"""
from __future__ import annotations

import argparse
import collections
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

RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'MLB': 'mlb_game_results', 'NHL': 'nhl_game_results',
           'NBA': 'nba_game_results'}
CONTEXT = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
           'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context',
           'NBA': 'nba_game_context'}
#: the column holding the HOME team's own spread number, per sport
SPREAD_COL = {'NHL': 'close_puckline'}      # others use close_spread


def page(t, p):
    out, off = [], 0
    while True:
        q = dict(p)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code}: {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000


def jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grade(side, spread_result):
    """WIN / LOSS / PUSH for a HOME-or-AWAY side against an ATS result."""
    sr = str(spread_result or '')
    if sr == 'push':
        return 'PUSH'
    if sr not in ('home_covered', 'away_covered'):
        return None
    won = (sr == 'home_covered') if side == 'HOME' else (sr == 'away_covered')
    return 'WIN' if won else 'LOSS'


def margin_to_beat(sport, spread_val):
    """How much the HOME team must win by for HOME to cover.

    Sign conventions differ and getting this backwards inverts every record.
    NFL stores a POSITIVE close_spread as a HOME FAVOURITE, so the home
    team's own line is the negative of it. MLB / NCAAF / NBA / NHL store the
    home handicap directly (verified 2026-10-06: MLB close_spread matched the
    HOME line in line_history on 4 of 4 games).
    """
    if spread_val is None:
        return None
    home_line = -spread_val if sport == 'NFL' else spread_val
    return -home_line


def extract_models(sport, snap):
    """{model_name: home_minus_away_margin} from a FROZEN snapshot.

    Each sport writes a different shape, so each is read explicitly rather
    than guessed at. MLB keeps the most: jerry, panel, model_v4 and the Monte
    Carlo are all stored separately, which is the comparison Andy asked for.
    """
    out = {}
    if not isinstance(snap, dict):
        return out
    if sport == 'MLB':
        fm = snap.get('full_models') or {}
        j = (fm.get('jerry') or {})
        if f(j.get('pred_spread')) is not None:
            out['jerry_model'] = f(j['pred_spread'])
        p = (fm.get('panel') or {})
        if f(p.get('implied_margin')) is not None:
            out['panel'] = f(p['implied_margin'])
        v4 = (fm.get('model_v4') or {})
        if f(v4.get('pred_spread')) is not None:
            out['model_v4'] = f(v4['pred_spread'])
        mc = ((fm.get('monte_carlo') or {}).get('probs') or {})
        em = f(mc.get('mc_expected_margin'))
        if em is not None:
            out['monte_carlo'] = em
    elif sport == 'NCAAF':
        m = snap.get('model') or {}
        ps = f(m.get('projected_spread'))
        if ps is None:
            hp, ap_ = f(m.get('model_pred_home_points')), f(m.get('model_pred_away_points'))
            ps = (hp - ap_) if None not in (hp, ap_) else None
        if ps is not None:
            out['matchup'] = ps
    else:   # NFL / NHL / NBA
        ms = snap.get('models') or {}
        mm = ms.get('matchup') or {}
        ps = f(mm.get('projected_spread'))
        if ps is None:
            hp, ap_ = f(mm.get('home_pts')), f(mm.get('away_pts'))
            ps = (hp - ap_) if None not in (hp, ap_) else None
        if ps is not None:
            out['matchup'] = ps
        pn = ms.get('panel') or {}
        ph, pa = f(pn.get('home_pts')), f(pn.get('away_pts'))
        if None not in (ph, pa):
            out['panel'] = ph - pa
    return out


MODEL_ORDER = ['jerry', 'jerry_model', 'panel', 'model_v4', 'monte_carlo',
               'matchup', 'primary_play']


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--since', default='2026-08-01')
    ap.add_argument('--apply', action='store_true',
                    help='write each model record to surface_records')
    args = ap.parse_args()
    sports = [args.sport.upper()] if args.sport else list(RESULTS)

    print(f'=== model_scorecard · since {args.since} · point-in-time only ===')
    print()
    grand = collections.defaultdict(lambda: collections.Counter())
    per_sport = {}

    for sport in sports:
        tbl, ctbl = RESULTS.get(sport), CONTEXT.get(sport)
        if not tbl:
            continue
        scol = SPREAD_COL.get(sport, 'close_spread')

        # ── results, indexed BOTH ways ──────────────────────────────
        # NFL keys jerry_reads by a 32-char hash while nfl_game_results uses
        # season_week_away_home, with ZERO overlap — the same mismatch that
        # made the Fade look like it had no NFL data. nfl_game_context holds
        # both, so it bridges. Every sport goes through the same path so a
        # silent zero can never be mistaken for "no signal" again.
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
        if ctbl:
            for z in page(ctbl, {'select': 'game_id,game_date,home_team,away_team',
                                 'game_date': f'gte.{args.since}'}):
                bridge[str(z.get('game_id'))] = (
                    str(z.get('game_date'))[:10], str(z.get('home_team')),
                    str(z.get('away_team')))

        reads = page('jerry_reads',
                     {'select': 'game_id,game_date,generated_at,call_side,'
                                'input_snapshot',
                      'sport': f'eq.{sport}',
                      'game_date': f'gte.{args.since}'})

        tally = collections.defaultdict(lambda: collections.Counter())
        used = no_snap = no_res = no_line = leaked = 0
        how = collections.Counter()
        for a in reads:
            gid = str(a.get('game_id'))
            g = by_gid.get(gid)
            how['direct' if g else ''] += 1 if g else 0
            if g is None:
                k = bridge.get(gid)
                g = by_key.get(k) if k else None
                if g:
                    how['bridged'] += 1
            if g is None:
                no_res += 1
                continue
            # ══ DROP ANY SNAPSHOT WRITTEN AFTER THE GAME ══
            # input_snapshot is NOT covered by the published-call freeze, so a
            # regenerated read can overwrite it with post-game values. Measured
            # 2026-10-07: MLB has 75 of 786 reads (9.5%) generated after their
            # own game date; NCAAF/NFL/NHL have none. Grading those would be
            # scoring hindsight and would flatter every model.
            _gd = str(a.get('game_date') or '')[:10]
            _ga = str(a.get('generated_at') or '')[:10]
            if _gd and _ga and _ga > _gd:
                leaked += 1
                continue

            need = margin_to_beat(sport, f(g.get(scol)))
            if need is None:
                no_line += 1
                continue
            snap = jl(a.get('input_snapshot'))
            models = extract_models(sport, snap)
            if not models and not a.get('call_side'):
                no_snap += 1
                continue
            used += 1
            sr = g['spread_result']

            js = str(a.get('call_side') or '').upper()
            if js in ('HOME', 'AWAY'):
                r = grade(js, sr)
                if r:
                    tally['jerry'][r] += 1

            for name, margin in models.items():
                if abs(margin - need) < 0.5:
                    continue        # no opinion worth grading
                r = grade('HOME' if margin > need else 'AWAY', sr)
                if r:
                    tally[name][r] += 1

            ppf = (snap.get('primary_play') or {}) if isinstance(snap, dict) else {}
            ps_ = str(ppf.get('side') or '').upper()
            if ps_ in ('HOME', 'AWAY'):
                r = grade(ps_, sr)
                if r:
                    tally['primary_play'][r] += 1

        per_sport[sport] = tally
        print(f'{sport}  — {used} graded reads   '
              f'(joined: {how["direct"]} direct + {how["bridged"]} bridged; '
              f'dropped {no_res} no-result, {no_line} no-line, '
              f'{no_snap} no-models, {leaked} POST-GAME snapshot)')
        if not used:
            print()
            continue
        print(f"   {'model':<14}{'W-L-P':>12}{'hit':>8}{'vs 52.4%':>10}{'n':>7}")
        for m in MODEL_ORDER:
            c = tally.get(m)
            if not c:
                continue
            w, l, p = c['WIN'], c['LOSS'], c['PUSH']
            n = w + l
            for k, v in c.items():
                grand[m][k] += v
            if n == 0:
                continue
            hit = w / n * 100
            flag = '  (n<30)' if n < 30 else ''
            print(f'   {m:<14}{f"{w}-{l}-{p}":>12}{hit:>7.1f}%'
                  f'{hit-52.38:>+9.1f}{n:>7}{flag}')
        print()

    print('=' * 62)
    print('ALL SPORTS POOLED')
    print(f"   {'model':<14}{'W-L-P':>12}{'hit':>8}{'vs 52.4%':>10}{'n':>7}")
    for m in MODEL_ORDER:
        c = grand.get(m)
        if not c:
            continue
        w, l, p = c['WIN'], c['LOSS'], c['PUSH']
        n = w + l
        if n == 0:
            continue
        hit = w / n * 100
        print(f'   {m:<14}{f"{w}-{l}-{p}":>12}{hit:>7.1f}%'
              f'{hit-52.38:>+9.1f}{n:>7}')
    print()
    print('  Breakeven at -110 is 52.38%. Every model value is the one frozen')
    print('  in the read BEFORE kickoff, graded against the closing line.')

    if not args.apply:
        print()
        print('  (report only — re-run with --apply to record these)')
        return 0

    # ── persist, so "which model is right" becomes a tracked record ──
    # One row per (sport, model). surface_records is the same table the app
    # already reads for every other record, so this needs no new plumbing and
    # the numbers can be shown next to the picks they explain.
    import datetime as _dt
    rows = []
    for sport, tally in per_sport.items():
        for m, c in tally.items():
            w, l, p = c['WIN'], c['LOSS'], c['PUSH']
            n = w + l
            if n == 0:
                continue
            # Scored flat at -110 because a model's SIDE is what is being
            # judged, not the price we happened to take. 0.909 per win.
            units = w * (100 / 110) - l
            rows.append({
                'sport': sport,
                'surface': f'model_{m}',
                'window_key': 'ats_vs_close',
                'wins': w, 'losses': l, 'pushes': p,
                'picks_count': n,
                'hit_rate': round(w / n * 100, 2),
                'units_net': round(units, 2),
                'roi_pct': round(units / (n + p) * 100, 2) if (n + p) else None,
                'last_computed_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),
            })
    if not rows:
        print()
        print('  nothing to write')
        return 0
    r = requests.post(f'{SB}/rest/v1/surface_records'
                      f'?on_conflict=sport,surface,window_key',
                      headers={**H, 'Content-Type': 'application/json',
                               'Prefer': 'resolution=merge-duplicates,'
                                         'return=representation'},
                      data=json.dumps(rows), timeout=60)
    if r.status_code not in (200, 201, 204):
        print()
        print(f'  write failed {r.status_code}: {r.text[:300]}')
        return 1
    back = r.json() if r.content else []
    print()
    print(f'  wrote {len(rows)} model record(s); '
          f'{len(back) if isinstance(back, list) else "?"} returned')
    return 0



if __name__ == '__main__':
    raise SystemExit(main())
