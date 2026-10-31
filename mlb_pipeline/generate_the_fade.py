#!/usr/bin/env python3
"""The Fade — engine plays that go AGAINST the public, with a running record.

== 2026-10-19 · WHY THIS REPLACES THE SPLIT ==
ANDY, 10-01: "The Split needs to be reworked or changed, its just not meeting
the mark, the record on the split doesn't make any sense its not providing any
useful data for users."
ANDY, 10-19: "we discussed changin to the fade and strictly being plays passed
through a filter of certain cirteria that is against the public, almost like
the sharp set up wise but play that are agisnt the public and a running record
like the sharp."

The Split had three defects and all three are server/client drift, not layout:
agg_split published ONE bucket while the client queried three; agg_split is
MLB-ONLY (`if not mlb_gids: return []`) so it went BLANK when baseball ended -
surface_records holds zero split surfaces today; and split_antipublic was
computed daily and never read.

So this is not a repair of the Split. It is the product Andy described, built
on the one filter that survived every control.

== THE EVIDENCE THIS IS BUILT ON ==
Fade the public ON SPREADS AND TOTALS, from the OddsCrowd money/bets feed in
line_snapshot. Threshold chosen on an EARLY date half, reported on a LATE
holdout that had no hand in choosing it:

                        EARLY            LATE (holdout)
    fade bets >= 65   +5.5% n=181      +1.3% n=115
    fade bets >= 70   +8.6% n=116      +2.8% n=65
    fade bets >= 75   +7.2% n=73      +12.3% n=34

    full sample, football spreads, bets >= 65:
        63-44   58.9%   CI[49.4,67.7]   ROI +12.4%   n=107

Same sign in both halves and monotonic in the threshold - the only thing
measured in this codebase that held its direction out of sample.

Independently, flipping our OWN curated football spread picks returns 65.3%
(+24.7%, n=75), and it works ONLY on the picks that agreed with the public
(60.0% vs 49.6%). So "flip our spread" and "fade the public" are ONE
mechanism. This expresses it as the public filter because that has 331
football rows instead of 135 and does not require us to make a bad pick first.

== WHY MONEYLINES ARE EXCLUDED, DELIBERATELY ==
On a moneyline, heavy public means a short favourite, so the breakeven RISES
with the public percentage: follow bets>=80 hits 70.1% against a 68.1%
breakeven, which is -0.2% ROI, and the fade side loses 9-11%. On a spread or
total both sides are about -110 and breakeven is a FLAT 52.38%, so the same
56% is a real edge. Measuring a public-money effect on the moneyline answers
a different question, and I got that wrong twice before catching it.

== HONEST SAMPLE, STATED ON THE SURFACE ==
107 graded football-spread plays at the chosen threshold, 38 of them in the
holdout - two weekends. The direction is consistent; the magnitude is not
settled. The payload carries `confidence: 'tracking'` until the record clears
TRACKING_MIN_N, so the tab can say so rather than implying a settled edge.

USAGE
    python generate_the_fade.py --dry-run
    python generate_the_fade.py
    python generate_the_fade.py --date 2026-10-20
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

#: Public TICKET share on a side before we will fade it. 65 is where the
#: measured edge starts and holds in both halves; below it the effect thins
#: toward the 52.38% breakeven. Env-overridable so it can be tuned on
#: evidence rather than by editing code.
FADE_MIN_PUBLIC = float(os.environ.get('FADE_MIN_PUBLIC', '65'))
# ══ 2026-10-31 · A FADE ON A STALE SPLIT IS A FADE ON NOTHING (R100) ══
# Measured BY RECENCY, NOT BY AGE. The obvious guard -- refuse a snapshot
# older than N hours -- would block this script every morning: the pull
# cadence has a median gap of 1.50h but a p90 of 8.58h and a max of 29.15h
# (n=669 consecutive pulls), because the feed goes quiet overnight. Today's
# newest pull before the 10:56 fade run was 03:27, so an absolute 6h bound
# would have refused the entire slate on a feed that was working perfectly.
#
# What actually signals staleness is a game falling BEHIND THE FEED: the
# puller refreshed other games in the latest cycle and not this one. So the
# bound is the lag between this game/market's newest snapshot and the newest
# snapshot anywhere in the loaded set. Overnight everything is equally old
# and the lag is ~0, which is the correct answer -- the data is current, the
# market is just closed (feedback_verify_freshness_by_recency_not_date).
#
# On the 10-31 slate every one of 170 game/markets sat within 0.6h of the
# feed max, so this blocks nothing in normal operation. It exists for the
# 10-29 failure mode, where compute_align_status defaulted to TODAY on a
# weekly sport and left line_snapshot dark on 0 of 310 forward games.
FADE_MAX_LAG_H = float(os.environ.get('FADE_MAX_LAG_H', '6'))

#: Markets allowed. Spreads and totals only - see the moneyline note above.
FADE_MARKETS = ('rl', 'spread', 'total')

#: Below this many graded plays the surface labels itself 'tracking'.
TRACKING_MIN_N = 150

#: Verified per-sport orientation of close_spread as a HOME HANDICAP, measured
#: against line_history on games captured within 2h of kickoff (NFL 93% exact,
#: NCAAF 89%). A sport absent here cannot have its spread graded or displayed,
#: so it is skipped rather than guessed at.
#:
#: ══ 2026-10-20 · B97 · "The Fade shoudl be elginble in every sports also" ══
#: Both signs above INDEPENDENTLY CONFIRMED today by two further methods on the
#: *_game_results tables, which is worth recording because the sign is the one
#: thing here that can invert a published pick:
#:
#:   method 1, lower MAE wins (expected margin = -cs vs +cs)
#:   method 2, home win% split by the sign of cs
#:
#:     sport    n      MAE -cs / +cs     home win% cs<0 / cs>0   convention
#:     MLB    2132     3.52 / 3.89       58.0% / 45.5%           HANDICAP  (+1)
#:     NFL    7338    14.60 / 10.26      35.4% / 67.4%           HOME-MARGIN(-1)
#:     NCAAF  6546    11.97 / 28.73      78.7% / 29.4%           HANDICAP  (+1)
#:     NBA    2398    10.85 / 17.51      68.8% / 33.1%           HANDICAP  (+1)
#:
#: Both methods agree with each other and with the line_history verification
#: above. NFL genuinely stores the OPPOSITE convention to every other sport -
#: project_close_spread_sign_bug_914 called this out and it is still true.
#:
#: SO WHY IS MLB/NBA/NHL STILL NOT HERE. The sign is no longer the blocker; the
#: DIRECTION is. Tonight's money-flow grading (grade_money_flow.py, 2,869
#: graded splits) measured the fade direction per sport AND per market:
#:
#:     MLB   spread  FADE    54-55%, both halves clear   <- the only candidate
#:     MLB   total   FOLLOW  55-58% FOLLOWING the money  <- opposite direction
#:     NCAAF spread  FADE    already live
#:     NCAAF total   nothing at or under breakeven
#:     NHL/NBA       n < 30, not gradeable at all
#:
#: Adding MLB TOTALS to a surface called The Fade would publish the side the
#: data says to take the other way round. That is not a widening, it is an
#: inversion with a friendly name. And NHL/NBA cannot be graded yet at all.
#:
#: MLB SPREAD (the runline) is the one legitimate addition, and it is blocked on
#: something mundane: the Fade reads *_game_context, and mlb_game_context
#: retains only ~28 rows, so its sign cannot be verified on the table this
#: script actually reads. The results-table sign above is NOT transferable - the
#: whole reason this constant exists is that two tables in one sport can
#: disagree. When MLB context retention is fixed, verify there and add
#: 'MLB': 1.0.
#:
#: DO NOT add a sport here from the results-table column alone.
#: ══ 2026-10-30 · NBA ORIENTATION DERIVED, AS THIS BLOCK DEMANDS ══
#: Derived the way the note above requires — on the table THIS script reads
#: (nba_game_context, not nba_game_results, because two tables in one sport
#: can disagree and that is the whole reason this map exists):
#:
#:     corr(nba_game_context.close_spread, actual home margin) = -0.5135
#:     n = 78 context rows that still have a final score
#:     ctx.close_spread == results.close_spread on 75 of 77 overlapping rows
#:
#: Negative corr means a negative home number goes with a positive home
#: margin — an ordinary betting spread — so home_handicap = +close_spread,
#: the same sign as NCAAF, and the independent results-table check agrees
#: (ml-confirmed favourite on 2,436 of 2,445 graded NBA games, 6 contradicted).
#: n=78 is thin for a hit rate but this is an ORIENTATION, not an edge claim.
#:
#: Adding the sign only makes NBA spreads expressible. It does NOT claim an
#: edge: ('NBA','rl') is absent from FADE_VALIDATED, so every NBA fade
#: publishes as `tracking`, which is how NBA/NHL totals are already handled.
#: Measured on the deduped publish-time snapshot: NBA rl fade 53.73% n=67,
#: i.e. unproven, not disproven.
#:
#: NHL is deliberately still absent, and now for a measured reason rather
#: than an unverified one — see FADE_BANNED.
_HOME_HANDICAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0, 'NBA': 1.0}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context',
       'NBA': 'nba_game_context'}
OPP = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}


# ══ 2026-10-23 · B151 · PUBLISH THE MARKET WE MEASURED ══
# This surface was built on ONE measurement: fade heavy public on FOOTBALL
# SPREADS (project_the_fade_is_the_one_real_edge_1019, +12.4% ROI on n=107).
# Totals appear nowhere in that work, and 26 of the 27 receipts this surface
# has written are totals. So the market we validated is not the market we
# ship.
#
# Measured today on the same feed, deduped to the LAST snapshot per
# (sport, game_id, market) exactly as build_plays does, graded off
# *_game_results, fade = the side opposite the public, at the live >= 65%
# threshold:
#
#   bucket              record      hit     ROI       n     verdict
#   NCAAF/NFL spread    57-39      59.4%   +13.4%     96    the edge, reproduced
#   MLB    total       130-151-15  46.3%   -11.7%    281    BANNED
#   NCAAF  total        86-84      50.6%    -3.4%    170    BANNED
#   NBA    total         3-3       50.0%      --       6    tracking
#   NHL    total         5-5-1     50.0%      --      10    tracking
#
# Totals as ONE bucket read -8.4% on n=467, but that is MLB and NCAAF
# carrying it, and money flow inverts by sport
# (project_split_flags_opposite_by_sport_1012) so the pooled number is not a
# verdict on any sport. Split out:
#
#   * MLB and NCAAF totals are MEASURED and NEGATIVE on real n. Banned. The
#     threshold sweep is negative at every level from 50 to 80, and the early
#     half is -18.3% against a late half of +1.1%, so there is no threshold
#     and no window where this is a play.
#   * NBA and NHL totals are UNMEASURED (n=6 and n=10), not disproven. Both
#     leagues opened this month. Banning a market on n=6 would be the mirror
#     of publishing one on n=6. They stay, labelled `tracking`, and B158's
#     weekly check covers them.
#
# This deliberately does NOT add NBA/NHL spread orientation to
# _HOME_HANDICAP_SIGN. Nine such spreads were dropped today as "orientation
# unverified". Filling that map would hand this surface a third unvalidated
# market, which is the exact complaint B151 opened with. The sign must be
# DERIVED from results first (corr of close_spread against actual home
# margin: NFL +0.324 so home_handicap = -close_spread, NCAAF -0.806 so +1 --
# which independently reproduces the map below). NBA had only 22 final games
# today, too few to call, and nhl_game_results carries no close_spread at
# all.
#
# (sport, market) -> reason it is refused. Measured, with n, or it does not
# belong here.
FADE_BANNED = {
    ('MLB', 'total'): 'fade 46.3%, ROI -11.7%, n=281',
    ('NCAAF', 'total'): 'fade 50.6%, ROI -3.4%, n=170',
    # ══ 2026-10-30 · THE NHL PUCK LINE FADE IS INVERTED, MEASURED ══
    # Deduped to one row per (game, market) at the EARLIEST snapshot — the
    # one we would actually hold at publish time — and graded off
    # nhl_game_results:
    #     fade NHL rl   20-35   36.36%  n=55   and WORSE in the late half
    #                                          (44.4% early -> 28.6% late)
    #     follow        35-20   63.64%
    # The fade is not merely unproven here, it is pointed the wrong way, which
    # is project_split_flags_opposite_by_sport_1012 showing up again.
    #
    # Separately, the NHL puck line must never be scored at a flat -110. The
    # dog +1.5 closes around -195 (median of 200 rows with a real price), so
    # "the dog covers 63.5%" is -4.66% ROI against a 64.57% breakeven, not the
    # +21% a -110 assumption prints. Both sides lose to the vig; this market is
    # efficiently priced and has no fade side.
    ('NHL', 'rl'): 'fade 36.4% n=55, inverted — following is 63.6%',
    # ══ 2026-10-30 · NBA AND NHL TOTALS ARE NO LONGER UNMEASURED ══
    # The B151 note above left these as `tracking` on n=6 and n=10, which was
    # right at the time. Both leagues have now played enough. Deduped to the
    # earliest snapshot per (game, market) and graded off *_game_results, at
    # the live >= 65% threshold:
    #     NBA total fade  15-18  45.45%  ROI -13.22%  n=33
    #     NHL total fade  16-18  47.06%  ROI -10.16%  n=34
    # And NBA gets WORSE as the threshold rises (>=70: 37.50%, -28.41%, n=24),
    # which is the opposite of what an edge does. Both clear the n>=30 gate in
    # the wrong direction, so they are refused on evidence like MLB and NCAAF
    # totals before them. This is what retired the five NBA totals that would
    # otherwise have been tonight's padding.
    ('NBA', 'total'): 'fade 45.5%, ROI -13.2%, n=33 (worse at 70: 37.5%)',
    ('NHL', 'total'): 'fade 47.1%, ROI -10.2%, n=34',
}

#: (sport, market) the 10-19 backtest actually covers. Everything else that
#: survives the filters publishes as `tracking` -- it has no measured edge
#: behind it and the payload says so, rather than borrowing the football
#: spread number's credibility.
FADE_VALIDATED = {('NCAAF', 'rl'), ('NCAAF', 'spread'),
                  ('NFL', 'rl'), ('NFL', 'spread')}


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def today_et() -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


def page(t, p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=240,
                         params=dict(p, limit='1000', offset=str(off)))
        if r.status_code not in (200, 206):
            print(f'  !! {t} HTTP {r.status_code} {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def latest_public_splits(date: str) -> dict:
    """{(sport, game_id, market): split} — the LAST snapshot per game/market.

    The last one is the settled public number. Earlier snapshots on the same
    game are the same figure re-observed, so taking the max timestamp avoids
    counting one game several times.
    """
    out = {}
    # odds_pick was missing from this SELECT, so it read as absent even
    # though line_snapshot carries it -- the explicit-SELECT
    # silent-blank trap. It is the PUBLIC side's price, so it is NOT
    # our price (see price_faded_side below), but it is the other half
    # of any 'was the public getting a worse number' question.
    for s in page('line_snapshot', {'select': 'sport,game_id,market,pick_side,'
                                             'bets_pct,money_pct,line,'
                                             'odds_pick,snapshot_ts'}):
        mkt = str(s.get('market') or '').lower()
        if mkt not in FADE_MARKETS:
            continue
        k = (str(s.get('sport')), str(s.get('game_id')), mkt)
        ts = str(s.get('snapshot_ts'))
        if k not in out or ts > str(out[k].get('snapshot_ts')):
            out[k] = s
    return out



#: Sides as line_history stores them, lowercased, per market.
_LH_SIDE = {'HOME': 'home', 'AWAY': 'away', 'OVER': 'over', 'UNDER': 'under'}


def lh_game_id(sport: str, game_id: str, date: str, away: str,
               home: str) -> str:
    """The id line_history keys this game under (R80).

    NFL is the ONE sport whose line_history id disagrees with its
    {sport}_game_context id. line_history stores '20261101_MIN_DET', i.e.
    {YYYYMMDD}_{away}_{home}, while nfl_game_context stores a 32-char
    hash, so a join on game_id returns 0 of 12 NFL games. NCAAF, NBA and
    NHL share one id across both tables and need no translation:

        line_history   NFL 20261109_BUF_MIN   NCAAF ncaaf_20261107_...
                       NBA 401909101          NHL   2026020231
        game_context   NFL c1d3fcec25aaeb...  NCAAF ncaaf_20261024_...
                       NBA 401909089          NHL   2026020055

    THE CONSEQUENCE WAS NOT A MISSING COLUMN, IT WAS AN EMPTY SURFACE.
    price_faded_side found no rows, returned (None, 0), and
    receipt_invariants rule (1) REFUSES a priceless play on a play
    surface -- 'rl' is in PRICE_REQUIRED_MARKETS and 'fade' is in
    PLAY_SURFACES. So every NFL fade was refused at capture from the
    moment those invariants moved to the chokepoint on 10-30, which is
    why exactly ONE NFL fade receipt exists in the table (3980420,
    10-29, itself unpriced) and none since.

    Found the night before an NFL Sunday carrying 7 validated fade
    candidates. A guard that silently empties the one surface we have
    evidence for is worse than the defect it was guarding against.
    """
    if str(sport or '').upper() != 'NFL':
        return game_id
    if not (date and away and home):
        return game_id            # cannot build it; let the join miss
    return '%s_%s_%s' % (str(date).replace('-', ''), away, home)


def price_faded_side(sport: str, game_id: str, market: str, side: str,
                     line) -> tuple:
    """(price, books) for OUR side at OUR line, from posted two-sided quotes.

    ══ WHY NOT odds_pick ══════════════════════════════════════════════
    line_snapshot.odds_pick is the price of the side the PUBLIC is on. The
    Fade takes the other side, so quoting it would publish the price of a bet
    we did not make -- the same defect as pricing a flipped football spread
    off `_spread_prices`, which carries the price of the side the read TOOK.

    So this reads line_history, which stores one row per (book, side, line)
    with a real posted price, and takes the MEDIAN across books at our exact
    line. Median rather than best, because best-of-N flatters the record and
    reconstructed prices have already inflated ROI once
    (project_reconstructed_prices_inflate_roi_1009).

    Returns (None, 0) when fewer than 2 books post our number -- better an
    honestly unpriced leg than a fabricated price. 22 of 49 Fade receipts are
    currently unpriced, which makes 45% of our one measured edge W/L-only and
    its published ROI a partial number presented as a whole.
    """
    want = _LH_SIDE.get(str(side or '').upper())
    if want is None or line is None:
        return None, 0
    mkt = 'total' if str(market).lower() == 'total' else 'spread'
    rows = page('line_history',
                {'select': 'side,line,price,book,captured_at',
                 'sport': f'eq.{sport}', 'game_id': f'eq.{game_id}',
                 'market': f'eq.{mkt}'})
    # ══ ONE QUOTE PER BOOK, THE LATEST ══════════════════════════════════
    # line_history stores a row per (book, side, line) PER CAPTURE, so the
    # first cut of this medianed 2,367 rows for a single game and called it
    # "2367 books". That is a median across DAYS, blending a price from
    # Tuesday with tonight's, and it would read as a huge consensus while
    # actually being a time-average. Collapse to the newest quote per book
    # first, then median across books.
    latest = {}
    for r in rows or []:
        if str(r.get('side') or '').lower() != want:
            continue
        lv, pv = f(r.get('line')), f(r.get('price'))
        if lv is None or pv is None or abs(lv - float(line)) > 0.01:
            continue
        bk = str(r.get('book') or '?')
        ts = str(r.get('captured_at') or '')
        if bk not in latest or ts > latest[bk][0]:
            latest[bk] = (ts, pv)
    prices = [v for _, v in latest.values()]
    if len(prices) < 2:
        return None, len(prices)
    # ══ AMERICAN ODDS CANNOT BE AVERAGED ARITHMETICALLY ═════════════════
    # They are discontinuous at +/-100: +100 and -102 are adjacent prices,
    # but their arithmetic mean is -1, which is not a price at all. The first
    # cut of this published exactly that for UCF +7.
    # So median the IMPLIED PROBABILITY -- which is continuous and monotonic
    # in the price -- and convert the median back.
    def to_prob(a):
        return (100.0 / (a + 100.0)) if a > 0 else (-a / (-a + 100.0))

    def to_american(pr):
        if pr <= 0 or pr >= 1:
            return None
        return (-(pr / (1.0 - pr)) * 100.0 if pr >= 0.5
                else ((1.0 - pr) / pr) * 100.0)

    probs = sorted(to_prob(v) for v in prices)
    mid = len(probs) // 2
    med_p = (probs[mid] if len(probs) % 2
             else (probs[mid - 1] + probs[mid]) / 2.0)
    am = to_american(med_p)
    if am is None:
        return None, len(prices)
    # a price between -100 and +100 does not exist; nudge to the nearer edge
    am = int(round(am))
    if -100 < am < 100:
        am = -100 if med_p >= 0.5 else 100
    return am, len(prices)


def build_plays(date: str) -> list[dict]:
    """Today's fade plays. One per (game, market) at most."""
    splits = latest_public_splits(date)
    print(f'  public splits available: {len(splits)}')
    # context for the slate, per sport, so a play can carry teams + the line
    ctx = {}
    for sport, tbl in CTX.items():
        for c in page(tbl, {'select': 'game_id,game_date,away_team,home_team,'
                                      'close_spread,close_total',
                            'game_date': f'eq.{date}'}):
            ctx[(sport, str(c.get('game_id')))] = c
    print(f'  context rows for {date}: {len(ctx)}')

    plays = []
    skipped = defaultdict(int)
    # ══ PER SPORT, NOT GLOBALLY ══════════════════════════════════════
    # The first version of this took the newest snapshot across ALL sports
    # as the reference, and that is wrong because the puller refreshes the
    # DAY'S slate: a sport that is not playing today is legitimately hours
    # or days behind one that is. Measured 2026-10-31 19:49Z:
    #
    #     NHL    newest 19:49      lag  0.0h
    #     NBA    newest 18:18      lag  1.5h
    #     NCAAF  newest 18:00      lag  1.8h
    #     NFL    newest 10-30 03:59   lag 39.8h   <- Sunday's slate
    #     MLB    newest 10-28 23:42   lag 68.1h   <- season over
    #
    # A global reference would therefore have refused every NFL fade on an
    # NFL Sunday, on data that is perfectly current for a Saturday night.
    # The question the guard has to ask is "did the puller refresh this
    # sport and skip this game", which is a within-sport comparison.
    _feed_max_by_sport: dict = {}
    for (_sp, _g, _m), _z in splits.items():
        _t = str(_z.get('snapshot_ts') or '')
        if _t > _feed_max_by_sport.get(_sp, ''):
            _feed_max_by_sport[_sp] = _t
    _feed_max = max(_feed_max_by_sport.values(), default='')
    _lags = []

    def _lag_hours(ts: str, sport: str):
        """How far behind this SPORT's newest snapshot this one sits."""
        ref = _feed_max_by_sport.get(sport, '')
        if not ts or not ref:
            return None
        try:
            a = dt.datetime.fromisoformat(str(ref).replace('Z', '+00:00'))
            b = dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None
        return (a - b).total_seconds() / 3600.0

    for (sport, gid, mkt), s in splits.items():
        c = ctx.get((sport, gid))
        if not c:
            skipped['not on this slate'] += 1
            continue
        _lag = _lag_hours(s.get('snapshot_ts'), sport)
        if _lag is None:
            skipped['snapshot has no usable timestamp'] += 1
            continue
        _lags.append(_lag)
        if _lag > FADE_MAX_LAG_H:
            # the puller refreshed the feed and skipped this game
            skipped[f'split {_lag:.1f}h behind the feed '
                    f'(cap {FADE_MAX_LAG_H:g}h)'] += 1
            continue
        _ban = FADE_BANNED.get((sport, mkt))
        if _ban:
            skipped[f'{sport} {mkt}: refused on evidence ({_ban})'] += 1
            continue
        bets = f(s.get('bets_pct'))
        if bets is None:
            skipped['no public split'] += 1
            continue
        ps = str(s.get('pick_side') or '').upper()
        if ps not in OPP:
            skipped['no side'] += 1
            continue
        # which side is the public on, and how heavy?
        if bets >= 50:
            public, pub_pct = ps, bets
        else:
            public, pub_pct = OPP[ps], 100.0 - bets
        if pub_pct < FADE_MIN_PUBLIC:
            skipped[f'public under {FADE_MIN_PUBLIC:g}%'] += 1
            continue
        fade = OPP[public]
        away, home = str(c.get('away_team')), str(c.get('home_team'))

        if mkt in ('rl', 'spread'):
            sgn = _HOME_HANDICAP_SIGN.get(sport)
            cs = f(c.get('close_spread'))
            if sgn is None:
                skipped[f'{sport} spread orientation unverified'] += 1
                continue
            if cs is None:
                skipped['no close_spread'] += 1
                continue
            home_handicap = sgn * cs
            line = home_handicap if fade == 'HOME' else -home_handicap
            team = home if fade == 'HOME' else away
            label = f'{team} {line:+g}'
        else:
            ct = f(c.get('close_total'))
            if ct is None:
                skipped['no close_total'] += 1
                continue
            line = ct
            label = f'{fade.title()} {ct:g}'

        _our_px, _our_bk = price_faded_side(
            sport, lh_game_id(sport, gid, date, away, home),
            mkt, fade, line)
        plays.append({
            'sport': sport, 'game_id': gid, 'game_date': date,
            'market': 'total' if mkt == 'total' else 'rl',
            'matchup': f'{away} @ {home}',
            'side': fade, 'line': line, 'label': label,
            'public_side': public, 'public_pct': round(pub_pct, 1),
            'public_money_pct': f(s.get('money_pct')),
            # OUR side's posted price, median across books at our exact
            # number. None when fewer than 2 books post it -- an honestly
            # unpriced leg beats a fabricated one.
            'our_price': _our_px, 'our_price_books': _our_bk,
            # the public side's price, for the 'were they getting a worse
            # number' question. NOT our price.
            'public_odds': f(s.get('odds_pick')),
            # What stands behind THIS play, per (sport, market). A tracking
            # play is published because it is unmeasured, not because it is
            # proven, and the tab must be able to say which.
            'evidence': ('validated' if (sport, mkt) in FADE_VALIDATED
                         else 'tracking'),
            # the reason string a user reads - the whole point of the tab
            'reason': (f'{pub_pct:.0f}% of tickets are on '
                       f'{(home if public == "HOME" else away) if public in ("HOME","AWAY") else public.title()}'
                       f' — this fades them'),
        })
    plays.sort(key=lambda p: -p['public_pct'])
    # Printed every run, not only on a breach. A guard whose normal readings
    # are invisible is a guard nobody can tell has gone wrong
    # (feedback_green_step_that_writes_nothing).
    if _lags:
        _ls = sorted(_lags)
        print(f'  split freshness: newest per sport '
              f'{ {k: v[:16] for k, v in sorted(_feed_max_by_sport.items())} } '
              f'· lag within sport median {_ls[len(_ls) // 2]:.1f}h '
              f'max {_ls[-1]:.1f}h · cap {FADE_MAX_LAG_H:g}h · '
              f'{sum(1 for x in _ls if x > FADE_MAX_LAG_H)} of {len(_ls)} '
              f'over')
    else:
        print('  split freshness: NO snapshots loaded -- wiring error, '
              'not an empty slate')
    if skipped:
        print(f'  filtered out: {dict(skipped)}')
    return plays


def graded_record() -> dict:
    """The running record of this surface, from its own receipts."""
    rows = page('public_receipts', {'select': 'result,pick_odds,game_date',
                                    'surface': 'eq.fade'})
    w = l = p = 0
    for r in rows:
        res = str(r.get('result') or '').upper()
        if res in ('WIN', 'W'):
            w += 1
        elif res in ('LOSS', 'L'):
            l += 1
        elif res in ('PUSH', 'P'):
            p += 1
    n = w + l
    units = (w * (100 / 110.0)) - l
    return {'wins': w, 'losses': l, 'pushes': p, 'graded': n,
            'units': round(units, 2),
            'hit_rate': (round(100 * w / n, 1) if n else None),
            'roi_pct': (round(100 * units / n, 1) if n else None),
            'confidence': ('tracking' if n < TRACKING_MIN_N else 'established'),
            'tracking_min_n': TRACKING_MIN_N}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    date = a.date or today_et()
    print(f'=== generate_the_fade · {date} · '
          f'public >= {FADE_MIN_PUBLIC:g}% · '
          f'{"DRY RUN" if a.dry_run else "WRITING"} ===')

    plays = build_plays(date)
    _ev = {}
    for _p in plays:
        _ev[_p.get('evidence')] = _ev.get(_p.get('evidence'), 0) + 1
    print(f'\n  FADE PLAYS: {len(plays)}  evidence: {_ev or "{}"}')
    for p in plays:
        print(f"    {p['sport']:<6}{p['market']:<6}{p['label'][:30]:<31}"
              f"public {p['public_pct']:>5.1f}% on {p['public_side']:<6}"
              f"{p['matchup'][:34]}")

    rec = graded_record()
    print(f"\n  RUNNING RECORD: {rec['wins']}-{rec['losses']}-{rec['pushes']}  "
          f"hit {rec['hit_rate']}%  units {rec['units']:+}  "
          f"ROI {rec['roi_pct']}%  [{rec['confidence']}]")

    payload = {'date': date, 'plays': plays, 'record': rec,
               'filter': {'min_public_pct': FADE_MIN_PUBLIC,
                          'markets': list(FADE_MARKETS),
                          'moneyline_excluded_because':
                              'breakeven moves with the public price'},
               # ══ 2026-10-19 · THE BACKTEST IS NOT THE RECORD ══
               # Receipts are NOT backfilled for past dates. Doing that would
               # insert plays we never published and grade them instantly,
               # presenting a retroactive record as if we had made the calls -
               # the same class of error as reconstructed prices inflating
               # ROI, and this surface exists to be trustworthy. So `record`
               # starts at 0-0 and only counts plays published from today
               # forward, while the measurement that justified building it
               # rides alongside, clearly labelled as a backtest.
               'backtest': {
                   'what': ('fade the public on football spreads, '
                            'public tickets >= 65%'),
                   'record': '63-44', 'hit_rate': 58.9,
                   'roi_pct': 12.4, 'n': 107,
                   'ci95': [49.4, 67.7],
                   'holdout': ('threshold chosen on an early date half; the '
                               'late holdout returned +1.3% at >=65, +2.8% at '
                               '>=70, +12.3% at >=75 - same sign in both '
                               'halves'),
                   'caveat': ('CI includes the 52.38% breakeven and the '
                              'holdout is 38 plays. Direction is consistent; '
                              'magnitude is not settled.'),
                   'measured_on': '2026-10-19',
                   # ══ 2026-10-23 · B151 ══ the same feed, re-measured per
                   # (sport, market), because the surface was shipping 26 of
                   # 27 picks in a market this backtest never covered.
                   'by_market_2026_10_23': {
                       'football_spread': {'record': '57-39', 'hit': 59.4,
                                           'roi_pct': 13.4, 'n': 96,
                                           'status': 'validated'},
                       'mlb_total': {'record': '130-151-15', 'hit': 46.3,
                                     'roi_pct': -11.7, 'n': 281,
                                     'status': 'banned'},
                       'ncaaf_total': {'record': '86-84', 'hit': 50.6,
                                       'roi_pct': -3.4, 'n': 170,
                                       'status': 'banned'},
                       'nba_total': {'record': '3-3', 'n': 6,
                                     'status': 'tracking'},
                       'nhl_total': {'record': '5-5-1', 'n': 10,
                                     'status': 'tracking'},
                       'note': ('totals pooled read -8.4% on n=467, but MLB '
                                'and NCAAF carry that and money flow inverts '
                                'by sport, so the pooled figure is not a '
                                'verdict on NBA or NHL'),
                   },
               },
               'generated_at': dt.datetime.now(dt.timezone.utc).isoformat()}

    if a.dry_run:
        print('\n  DRY RUN — nothing written')
        print(f'  payload preview: {json.dumps(payload)[:300]}...')
        return 0

    # 1. the cache row the tab renders
    cache_key = f'fade_{date}'
    r = requests.post(f'{SB}/rest/v1/jerry_cache', headers=H_WRITE,
                      params={'on_conflict': 'cache_key'},
                      data=json.dumps({'cache_key': cache_key,
                                       'game_id': cache_key,
                                       'sport': 'MULTI',
                                       'narrative': f'The Fade · {date}',
                                       'data': payload,
                                       'fetched_at': dt.datetime.now(
                                           dt.timezone.utc).isoformat()}),
                      timeout=60)
    print(f'\n  cache write: HTTP {r.status_code}')

    # 2. receipts, so the record grades itself the way every other surface does
    if plays:
        try:
            from public_receipt import capture
            rows = [{
                'sport': p['sport'], 'surface': 'fade',
                'game_date': p['game_date'], 'game_id': p['game_id'],
                'market': p['market'], 'matchup': p['matchup'],
                'pick_side': p['side'], 'pick_line': p['line'],
                'pick_label': p['label'],
                'pick_odds': p.get('our_price'),
                'source_table': 'line_snapshot',
                'source_id': f"fade:{p['sport']}:{p['game_id']}:{p['market']}",
                'audit': {'public_pct': p['public_pct'],
                          'public_side': p['public_side'],
                          'public_money_pct': p['public_money_pct'],
                          'filter': f'public >= {FADE_MIN_PUBLIC:g}%',
                          # so a later sweep can ask whether a higher
                          # threshold is better WITHOUT needing new data
                          'evidence': p.get('evidence'),
                          'public_odds': p.get('public_odds'),
                          'our_price_books': p.get('our_price_books')},
            } for p in plays]
            n = capture(rows, surface='fade')
            print(f'  receipts captured: {n}/{len(rows)}')
        except Exception as e:                                 # noqa: BLE001
            print(f'  ⚠ receipt capture failed (not fatal): '
                  f'{type(e).__name__}: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
