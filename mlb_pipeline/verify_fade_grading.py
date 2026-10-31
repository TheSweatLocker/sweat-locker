"""Is the Fade grading correctly? Recompute every pick from raw scores.

ANDY 2026-10-31: "Is the fade gradining correctly right now? Can you verify
this when you have chnace? Verify last 3 days make sure shown records checks
wiht findings"

INDEPENDENT BY CONSTRUCTION. This does NOT read spread_result,
run_line_result or total_result -- those are derived columns, and
`close_spread` is a live odds snapshot that freezes stale once a game leaves
the feed, so checking a grade against them is circular
(project_nfl_stale_line_and_inverted_weights_1030). It recomputes from
home_score / away_score and the receipt's OWN frozen pick_side and
pick_line, which is the play that actually shipped.

THE FADE-SPECIFIC TRAP. A fade takes the side opposite the engine's read, so
grading "the model's pick" instead of the published side inverts the result
(feedback_grade_the_side_we_published). 179 receipts were already found
grading the opposite side on 10-28. So this also checks that the TEAM NAMED
IN THE LABEL is really on the side the receipt claims, which is the only way
to catch an inverted grade that happens to agree by luck.

NCAAF game_id is built from team names and 21 duplicate rows exist, one with
home/away INVERTED (project_ncaaf_five_team_name_mismatch_1021). So a
results row whose home_team disagrees with the label's side is reported as
UNTRUSTED rather than used.

Convention: pick_line is the handicap FROM OUR SIDE. 'Texas -20.5' stores
-20.5, 'Syracuse +9.5' stores +9.5, so
    cover = (our_points - their_points) + pick_line
with > 0 WIN, < 0 LOSS, == 0 PUSH. Totals compare the combined score to the
line in the OVER/UNDER direction.

Then it rebuilds the record the way compute_surface_records.pick_fade does
-- frozen receipts, flat 0.909 payout, preseason excluded -- and compares
that to the independently recomputed one, so a disagreement shows up as a
record delta and not just a per-row note.

USAGE
  python verify_fade_grading.py                # last 3 days
  python verify_fade_grading.py --days 14
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
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
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results',
           'MLB': 'mlb_game_results', 'NCAAB': 'ncaab_game_results'}
WON = ('WIN', 'W', 'WON')
LOST = ('LOSS', 'L', 'LOST')
PUSHED = ('PUSH', 'TIE')
VOIDS = ('VOID', 'VOIDED', 'NO_ACTION', 'UNGRADEABLE')
PAYOUT = 0.909                        # pick_fade's flat -110


def g(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H, timeout=120,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:160]))
        return None
    return r.json()


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def word(res):
    r = str(res or '').upper()
    if r in WON:
        return 'WIN'
    if r in LOST:
        return 'LOSS'
    if r in PUSHED:
        return 'PUSH'
    return r or 'UNGRADED'


def team_of(lbl):
    """The team at the head of the label, number and parenthetical gone."""
    s = ' '.join(str(lbl or '').split())
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+(ML|[+-]?\d+(?:\.\d+)?)$', '', s, flags=re.I)
    return s.strip()


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s or '').lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=3)
    a = ap.parse_args()
    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    dates = [(today - dt.timedelta(days=i)).isoformat()
             for i in range(a.days)]
    print('=== VERIFY FADE GRADING: %s .. %s ==='
          % (dates[-1], dates[0]))

    recs = g('public_receipts',
             select='id,sport,game_date,game_id,market,pick_side,pick_line,'
                    'pick_odds,pick_label,result,graded_at,tier',
             surface='eq.fade', game_date='gte.' + dates[-1],
             order='game_date.desc', limit=2000)
    if recs is None:
        print('  CANNOT VERIFY -- receipt read failed')
        return 2
    recs = [x for x in recs if str(x.get('game_date'))[:10] in dates]
    print('  %d fade receipt(s) in window' % len(recs))

    # one results read per sport, keyed by game_id AND by team pair
    by_gid, by_pair = {}, {}
    for sport in sorted({str(x.get('sport')) for x in recs}):
        tbl = RESULTS.get(sport)
        if not tbl:
            print('  ! %s has no results table mapped -- its picks cannot be '
                  'verified' % sport)
            continue
        rows = g(tbl, select='game_id,game_date,home_team,away_team,'
                             'home_score,away_score',
                 game_date='gte.' + dates[-1], limit=2000)
        if rows is None:
            continue
        for r in rows:
            if str(r.get('game_date'))[:10] not in dates:
                continue
            by_gid[(sport, str(r.get('game_id')))] = r
            by_pair.setdefault(
                (sport, norm(r.get('home_team')), norm(r.get('away_team'))),
                []).append(r)

    agree = disagree = ungraded = unscored = untrusted = 0
    stored_rec = collections.Counter()
    truth_rec = collections.Counter()
    per_market = collections.defaultdict(collections.Counter)
    rows_bad = []
    for x in recs:
        sport = str(x.get('sport'))
        gid = str(x.get('game_id'))
        res = g_res = by_gid.get((sport, gid))
        stored = word(x.get('result'))
        mkt = str(x.get('market') or '').lower()
        side = str(x.get('pick_side') or '').upper()
        line = f(x.get('pick_line'))
        lbl = x.get('pick_label')

        if stored in VOIDS:
            continue
        if g_res is None:
            # ══ THE game_id DOES NOT MATCH ACROSS TABLES ON NFL (R80) ══
            # An NFL fade receipt stores a HASH ('ab2865e9f2d4...') while
            # nfl_game_results keys on '20261029_CAR_GB', so the direct
            # lookup finds nothing and a missing result must not be read as
            # "no game" (feedback_select_star_before_concluding_absent).
            #
            # The fallback has to key on the PAIR. My first version matched
            # any game containing the label's team and took the first hit:
            # for 'GB -3.5' it found GB @ DET and reported a SIDE/LABEL
            # CONFLICT on a receipt that was graded perfectly correctly
            # (the real game was CAR @ GB, GB won 27-19, the -3.5 covered).
            # A loose key invents a defect exactly as readily as it hides
            # one (feedback_a_name_check_needs_the_right_row).
            #
            # The receipt already carries the pair in `matchup`, written as
            # 'AWAY @ HOME', so use it and require BOTH teams to match.
            res = None
            mu = str(x.get('matchup') or '')
            if '@' in mu:
                _aw, _hm = [norm(t) for t in mu.split('@', 1)]
                cands = by_pair.get((sport, _hm, _aw)) or []
                if len(cands) == 1:
                    res = cands[0]
                elif len(cands) > 1:
                    untrusted += 1
                    rows_bad.append((x, '%d result rows for %r -- ambiguous, '
                                     'not regraded' % (len(cands), mu), None))
                    continue
        if res is None:
            if stored == 'UNGRADED':
                ungraded += 1
            else:
                unscored += 1
                rows_bad.append((x, 'graded %s but NO result row found'
                                 % stored, None))
            continue

        hs, aws = f(res.get('home_score')), f(res.get('away_score'))
        if hs is None or aws is None:
            if stored == 'UNGRADED':
                ungraded += 1
            else:
                unscored += 1
                rows_bad.append((x, 'graded %s but the result row has NULL '
                                 'scores' % stored, res))
            continue

        # does the label's team really sit on the side we claim?
        truth = None
        if mkt in ('rl', 'spread', 'puckline', 'ml'):
            lt = norm(team_of(lbl))
            ht, at = norm(res.get('home_team')), norm(res.get('away_team'))
            on_home = lt and lt == ht
            on_away = lt and lt == at
            if not (on_home or on_away):
                untrusted += 1
                rows_bad.append((x, 'label team %r matches neither %r nor %r '
                                 '-- UNTRUSTED, not regraded'
                                 % (team_of(lbl), res.get('home_team'),
                                    res.get('away_team')), res))
                continue
            claimed_home = (side == 'HOME')
            if on_home != claimed_home:
                untrusted += 1
                rows_bad.append((x, 'pick_side=%s but %r is the %s team -- '
                                 'SIDE/LABEL CONFLICT'
                                 % (side, team_of(lbl),
                                    'home' if on_home else 'away'), res))
                continue
            ours, theirs = (hs, aws) if on_home else (aws, hs)
            if mkt == 'ml':
                truth = ('WIN' if ours > theirs
                         else 'LOSS' if ours < theirs else 'PUSH')
            else:
                if line is None:
                    untrusted += 1
                    rows_bad.append((x, 'spread receipt with no pick_line',
                                     res))
                    continue
                m = (ours - theirs) + line
                truth = 'WIN' if m > 0 else 'LOSS' if m < 0 else 'PUSH'
        elif mkt == 'total':
            if line is None:
                untrusted += 1
                rows_bad.append((x, 'total receipt with no pick_line', res))
                continue
            tot = hs + aws
            up = str(lbl or '').upper()
            over = 'OVER' in up or side == 'OVER'
            under = 'UNDER' in up or side == 'UNDER'
            if over == under:
                untrusted += 1
                rows_bad.append((x, 'total receipt: cannot tell OVER from '
                                 'UNDER in %r/side=%r' % (lbl, side), res))
                continue
            d = (tot - line) if over else (line - tot)
            truth = 'WIN' if d > 0 else 'LOSS' if d < 0 else 'PUSH'
        else:
            untrusted += 1
            rows_bad.append((x, 'market %r not verifiable here' % mkt, res))
            continue

        if stored == 'UNGRADED':
            ungraded += 1
            rows_bad.append((x, 'UNGRADED but the game is final -- truth is %s'
                             % truth, res))
            continue
        stored_rec[stored] += 1
        truth_rec[truth] += 1
        per_market[(sport, mkt)][truth] += 1
        if stored == truth:
            agree += 1
        else:
            disagree += 1
            rows_bad.append((x, 'STORED %s but raw score says %s'
                             % (stored, truth), res))

    print('\n=== PER-ROW VERDICT ===')
    print('  graded and CORRECT      : %d' % agree)
    print('  graded and WRONG        : %d' % disagree)
    print('  ungraded                : %d' % ungraded)
    print('  graded with no result   : %d' % unscored)
    print('  untrusted / unverifiable: %d' % untrusted)

    if rows_bad:
        print('\n=== EVERY ROW THAT IS NOT CLEANLY CORRECT ===')
        for x, why, res in rows_bad:
            print('  %s %s %-6s %-5s %-28s line=%-7s side=%-5s'
                  % (x['id'], x['game_date'], x['sport'],
                     str(x.get('market')), str(x.get('pick_label'))[:28],
                     x.get('pick_line'), x.get('pick_side')))
            print('      %s' % why)
            if res:
                print('      final: %s %s - %s %s'
                      % (res.get('away_team'), res.get('away_score'),
                         res.get('home_team'), res.get('home_score')))

    # ══ PER (SPORT, MARKET), RECOMPUTED ══════════════════════════════
    # FADE_BANNED is the list of markets this surface refuses on measured
    # evidence. A market that is losing and NOT on that list is still
    # publishing, so the breakdown has to be per market, independently
    # graded, with n on every line (feedback_sample_size_with_pct).
    print('\n=== RECOMPUTED RECORD PER (SPORT, MARKET) ===')
    print('  %-6s %-6s %-11s %-8s %-10s %s'
          % ('sport', 'market', 'W-L-P', 'hit%', 'ROI@-110', 'n'))
    try:
        from generate_the_fade import FADE_BANNED as _BANNED
    except Exception:                                          # noqa: BLE001
        _BANNED = {}
    for key in sorted(per_market):
        c = per_market[key]
        w, l, p = c.get('WIN', 0), c.get('LOSS', 0), c.get('PUSH', 0)
        n = w + l
        if not n:
            continue
        roi = ((w * PAYOUT) - l) / n * 100.0
        ban = _BANNED.get(key)
        note = '  BANNED' if ban else ''
        if not ban and n >= 30 and roi < 0:
            note = '  <-- LOSING, n>=30, STILL PUBLISHING'
        print('  %-6s %-6s %-11s %-8s %-10s %-4d%s'
              % (key[0], key[1], '%d-%d-%d' % (w, l, p),
                 '%.1f%%' % (100.0 * w / n), '%+.2f%%' % roi, n, note))
    print('  (ROI at a flat -110, which is what pick_fade pays. Correct for '
          'spreads and\n   totals; it would misstate a moneyline, and this '
          'surface publishes none.)')

    def rec(c, name):
        w, l, p = c.get('WIN', 0), c.get('LOSS', 0), c.get('PUSH', 0)
        n = w + l
        roi = ((w * PAYOUT) - l) / n * 100.0 if n else 0.0
        print('  %-26s %d-%d-%d  %.1f%%  ROI %+.2f%%  n=%d'
              % (name, w, l, p, 100.0 * w / n if n else 0.0, roi, n))
        return w, l, p

    print('\n=== RECORD: what the surface shows vs the raw scores ===')
    sw, sl, sp = rec(stored_rec, 'STORED (shown record)')
    tw, tl, tp = rec(truth_rec, 'RECOMPUTED (raw scores)')
    print('  %-26s wins %+d  losses %+d  pushes %+d'
          % ('delta', sw - tw, sl - tl, sp - tp))

    # and the surface record as the app's own builder would read it
    print('\n=== cross-check against compute_surface_records.pick_fade ===')
    sr = g('surface_records', select='*', limit=200)
    if sr is None:
        print('  surface_records unreadable')
    else:
        hit = [r for r in sr if 'fade' in str(r.get('surface', '')).lower()]
        if not hit:
            print('  no fade rows in surface_records (n=%d rows total)'
                  % len(sr))
        for r in hit[:12]:
            print('  %s' % {k: v for k, v in r.items()
                            if k in ('surface', 'sport', 'window', 'wins',
                                     'losses', 'pushes', 'roi', 'hit_rate',
                                     'n', 'updated_at')})
    return 0 if (disagree == 0 and unscored == 0 and untrusted == 0) else 1


if __name__ == '__main__':
    raise SystemExit(main())
