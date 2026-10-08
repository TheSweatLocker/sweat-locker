#!/usr/bin/env python3
"""compute_surface_records.py

Single source of truth for per-surface, per-sport, per-window W/L/P +
units_net. Both the Receipts tab and the Sharp Card read from the
surface_records table this script writes.

Surfaces:  sharp, prop, ladder, ledger, potd
Sports:    MLB, NFL, NCAAF, UFC, NBA, NHL, NCAAB, ALL
Windows:   mtd, d7, d30, lifetime

Rules the app used to duplicate — now all in one place:
  * SHARP_RECORD_EPOCH = 2026-08-20 applies to `sharp` surface (jerry_reads
    had flat-110 assumption pre-reset; ignore that history).
  * Coverage stubs (conviction=0 or tier='COVERAGE') never counted.
  * Real book odds used where available:
      - prop:   book_line (mlb_pipeline_props)
      - ladder: odds_american
      - ledger: combined_odds
      - sharp:  call_odds_est fallback → -110 (jerry_reads has no snapshot)
  * Ledger result values are 'W'/'L'/'P'; everything else uses
    'Win'/'Loss'/'Push'. Normalized in _classify.

Usage:  python compute_surface_records.py [--dry-run]
"""

from __future__ import annotations
import argparse, os, sys, datetime as dt
from calendar import monthrange
from typing import Iterable

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except Exception:
    pass

import requests

SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'}

SHARP_RECORD_EPOCH = dt.date(2026, 8, 20)   # jerry_reads sides record reset
SPORTS = ['MLB', 'NFL', 'NCAAF', 'UFC', 'NBA', 'NHL', 'NCAAB']

#: Surfaces whose unit of account spans sports, so a per-sport record is not a
#: smaller true number — it is a meaningless one. The Ledger ships chalk
#: parlays that intentionally pair, say, an NHL leg with an MLB leg; that
#: combo belongs to no single sport. These report the ALL aggregate under
#: every sport key so no caller can read a wrong-by-construction figure.
#: Add to this set only when the SURFACE's bet itself crosses sports — not
#: merely because a surface happens to cover several.
CROSS_SPORT_SURFACES = {'ledger'}
WINDOWS = ['mtd', 'd7', 'd30', 'lifetime']
TIER_UNITS = {'PRIME': 2.0, 'STRONG': 1.5, 'LEAN': 1.0, 'COVERAGE': 0.0}

# ─── prop L5 leak quarantine (2026-10-03) ─────────────────────────────────────
# Until 2026-09-22, backfill_prop_lookback.fetch_mlb_player_recent had no upper
# date bound, so a prop's own game sat inside its L5/L10 window — and those
# counts feed the LR model that sets tier. Tier was therefore set partly by the
# outcome it was predicting. See project_prop_l5_leak_922.
#
# This gated on the MECHANISM, not the calendar, deliberately. A date window
# both over- and under-shoots: it left 16 contaminated August rows in and threw
# out clean never-enriched rows. The enrichment timestamp identifies exactly the
# rows whose features could see the answer.
#
# The gate proves itself on the leak's own fingerprint — PRIME rows where all 5
# of the last 5 hit ("5 of 5 hit" ⇒ the predicted game hit ⇒ already won):
#
#     all PRIME, l5==5    n=401   92.8% win   +46.6% ROI   <- tautology
#     clean PRIME, l5==5  n= 54   59.3% win   -13.2% ROI   <- gate applied
#
# and on the surface record it is here to fix:
#
#     PRIME all-time (ungated)   n=2,341  74.8%  +21.6% ROI  +505.0u
#     PRIME contaminated         n=1,707  82.4%  +33.0% ROI  +564.1u
#     PRIME clean (gated)        n=  634  54.3%   -9.3% ROI   -59.2u
#
# Do NOT relax this to a date constant. Do NOT remove it when the model is
# retrained — retraining fixes future rows; these rows stay contaminated.
#
# 2026-10-03 ANDY'S CALL: measured, presented, and deliberately NOT applied.
# The quarantine is OFF by default so the published record is unchanged:
#
#     prop_prime MLB lifetime   shown: 997-510-1  66.2%  +18.21%  +274.39u
#                               gated: 493-384-1  56.2%   -0.17%    -1.45u
#
# The displayed prop record is therefore known-inflated by ~276u. This is a
# conscious product decision, not an undetected defect — do not "fix" it as a
# bug, and do not quote the ungated prop record as evidence of edge.
# Flip with PROP_LEAK_QUARANTINE=on. See
# feedback_split_the_leak_window_before_quoting.
PROP_LOOKBACK_FIX_DATE = '2026-09-22'
PROP_LEAK_QUARANTINE = os.environ.get('PROP_LEAK_QUARANTINE', 'off').lower() in ('on', '1', 'true')


def _prop_lookback_leaked(row: dict) -> bool:
    """True if this prop's L5/L10 features could see its own outcome.

    Contaminated iff the lookback was written on/after game day while the
    fetch was still unbounded. Rows never enriched are clean (no leaked
    feature existed); rows enriched after the fix are clean (fetch bounded).
    """
    if not PROP_LEAK_QUARANTINE:
        return False
    # A .get() on an unselected column reads as NULL, which would silently
    # turn this whole quarantine into a no-op and quietly restore the +21.6%
    # headline. Absent key is a bug; present-and-NULL is legitimate data.
    # See feedback_dict_get_on_a_guessed_column.
    if 'player_lookback_updated_at' not in row:
        raise RuntimeError(
            'prop row is missing player_lookback_updated_at — the L5 leak '
            'quarantine cannot be evaluated. Add the column back to the '
            'select; do not drop the gate. See project_prop_l5_leak_922.')
    ts = row.get('player_lookback_updated_at')
    gd = row.get('game_date')
    if not ts or not gd:
        return False
    if gd > PROP_LOOKBACK_FIX_DATE:
        return False
    return str(ts)[:10] >= str(gd)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _american_win_payout(odds) -> float:
    """Return decimal profit per 1u risked. -110 → 0.909; +150 → 1.5."""
    try:
        o = int(odds)
    except (TypeError, ValueError):
        return 0.909
    if o == 0:
        return 0.909
    if o > 0:
        return o / 100.0
    return 100.0 / abs(o)


def _fetch_publish_locks(sport_market_pairs: list[tuple[str, str]]) -> dict:
    """Return {(sport, market, source_id): {tier_at_publish, conviction_at_publish, published_at}}.

    Paginated pull of `publish_lock` rows for the requested (sport,
    market) filters. Empty result on API error — grader falls through
    to live tier for any keys not present in the map.
    """
    out: dict = {}
    for sport, market in sport_market_pairs:
        url = (f'{SB}/rest/v1/publish_lock'
               f'?sport=eq.{sport}&market=eq.{market}'
               f'&select=source_id,tier_at_publish,conviction_at_publish,published_at')
        try:
            for row in _paged(url):
                sid = str(row.get('source_id'))
                out[(sport, market, sid)] = row
        except Exception:
            continue
    return out


def _classify(result) -> str | None:
    if not result:
        return None
    r = str(result).strip().lower()
    if r in ('win', 'w'): return 'win'
    if r in ('loss', 'l'): return 'loss'
    if r in ('push', 'p'): return 'push'
    return None


def _iso(d: dt.date) -> str:
    return d.isoformat()


def _windows_today(today: dt.date) -> dict[str, tuple[dt.date, dt.date]]:
    """Absolute date ranges for each window ending today (inclusive).

    `epoch` window starts at SHARP_RECORD_EPOCH so the Sharp Card can sum
    sides + props on the same time floor. Prevents the 8/1-mtd-props +
    8/20-mtd-sides drift that produced misleading combined tallies.
    """
    mtd_start = today.replace(day=1)
    d7_start  = today - dt.timedelta(days=6)
    d30_start = today - dt.timedelta(days=29)
    lifetime_floor = dt.date(2020, 1, 1)
    return {
        'mtd':      (mtd_start,          today),
        'd7':       (d7_start,           today),
        'd30':      (d30_start,          today),
        'epoch':    (SHARP_RECORD_EPOCH, today),
        'lifetime': (lifetime_floor,     today),
    }


def _paged(url: str, chunk: int = 1000):
    off = 0
    while True:
        r = requests.get(f'{url}&offset={off}&limit={chunk}',
                         headers={**H, 'Prefer': 'count=exact'}, timeout=45)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < chunk:
            return
        off += chunk


# ─── surface pickers — each returns list of dicts with normalized fields ──────
# Normalized shape:
#   {sport, date (dt.date), result ('win'/'loss'/'push'), stake_units, win_payout}

def pick_sharp() -> list[dict]:
    """Sharp Card SIDES component — sources from the SAME primary_play tier
    filter that aggregate_daily_records.agg_sharp_card uses (frozen at grade
    time via mlb_game_results.primary_play, tier IN PRIME/STRONG).

    ROOT-CAUSE FIX 2026-09-05 (launch day): prior version read jerry_reads
    conviction>=60 which is a BROADER set than what actually ships on Sharp
    Card. That produced surface_records.sharp counts + units that did NOT
    match daily_surface_records.sharp_card sides. Combined with .prop, the
    app displayed 228-104 (+134u) while the true Sharp Card record was
    254-146 (+75u) — 59u overstatement, ~5pp hit-rate overstatement.

    Now sources from mlb_game_results.primary_play (frozen snapshot at grade
    time) tier IN PRIME/STRONG. Same _grade_side + stake logic as
    agg_sharp_card so sums reconcile exactly: surface_records.sharp +
    surface_records.prop = sum(daily_surface_records.sharp_card).

    Sizing mirrors SHARP_STAKE_CUTOVER (2026-08-31): pre-cutover 2u flat
    for PRIME/STRONG (matches historical writes); post-cutover reads
    recommended_stake from primary_play (1u default, 2u LOCK on high-hit
    signals).
    """
    from datetime import date as _date_cls
    _CUTOVER = _date_cls.fromisoformat('2026-08-31')
    out = []
    url = (f'{SB}/rest/v1/mlb_game_results'
           f'?select=game_id,game_date,home_team,away_team,home_score,away_score,'
           f'home_win,run_line_result,total_result,spread_result,primary_play'
           f'&primary_play.not.is.null'
           f'&order=game_date.desc')
    for row in _paged(url):
        pp = row.get('primary_play') or {}
        if not isinstance(pp, dict): continue
        if pp.get('tier') not in ('PRIME', 'STRONG'): continue
        try:
            d = _date_cls.fromisoformat(row['game_date'])
        except Exception:
            continue
        if d < SHARP_RECORD_EPOCH: continue
        verdict = _grade_side_lite(pp, row)
        if verdict is None: continue
        # Stake sizing — pre/post cutover
        if d >= _CUTOVER:
            try: stake = float(pp.get('recommended_stake') or 1.0)
            except (TypeError, ValueError): stake = 1.0
        else:
            stake = 2.0
        # Payout: ML uses close_home_ml/close_away_ml (best-effort), else -110.
        # For consistency with agg_sharp_card which uses flat -110 for MLB sides,
        # we do the same here so numbers reconcile row-for-row.
        payout = 0.909   # -110 default
        cls = {'W': 'win', 'L': 'loss', 'P': 'push'}.get(verdict)
        if cls is None: continue
        out.append({'sport': 'MLB', 'date': d, 'result': cls,
                    'stake': stake, 'payout': payout})
    return out


def _grade_side_lite(pp: dict, game: dict) -> str | None:
    """Mirror of aggregate_daily_records._grade_side, inlined here so this
    module doesn't hard-import the other. Keep in sync when the source changes.
    Returns 'W'/'L'/'P'/None."""
    hs = game.get('home_score'); as_ = game.get('away_score')
    if hs is None or as_ is None: return None
    home = (game.get('home_team') or '').lower()
    away = (game.get('away_team') or '').lower()
    m = (pp.get('type') or '').lower()
    label = (pp.get('label') or '').lower()
    picked_home = home in label; picked_away = away in label
    if m == 'ml':
        if picked_home: return 'W' if hs > as_ else 'L' if hs < as_ else 'P'
        if picked_away: return 'W' if as_ > hs else 'L' if as_ < hs else 'P'
    elif m == 'rl':
        rl = (game.get('run_line_result') or '').lower()
        if picked_home and '+1.5' in label: return 'W' if rl != 'home' else 'L'
        if picked_home: return 'W' if rl == 'home' else 'L'
        if picked_away and '+1.5' in label: return 'W' if rl != 'away' else 'L'
        if picked_away: return 'W' if rl == 'away' else 'L'
    elif m in ('total', 'over', 'under'):
        tr = (game.get('total_result') or '').lower()
        if 'over' in label: return 'W' if tr == 'over' else 'L' if tr == 'under' else 'P'
        if 'under' in label: return 'W' if tr == 'under' else 'L' if tr == 'over' else 'P'
    return None


def pick_prop() -> list[dict]:
    """Props — PRIME + STRONG only, with full discipline applied.

    CRITICAL 2026-08-28 FIX: was reading `book_line` (the OVER/UNDER prop
    line, e.g. 0.5) as American odds. Because int(0.5)==0, every prop win
    got flat -110 payout instead of the real juiced odds. Fixed to read
    book_over_odds / book_under_odds based on direction — which is where
    the actual American odds live in mlb_pipeline_props.
    """
    from datetime import date as _date_cls
    _CUTOVER = _date_cls.fromisoformat('2026-08-31')
    out = []
    # 2026-09-08 add game_date lower bound to prevent 57014 statement
    # timeout as tables grow. Lifetime picks lifetime data anyway; a
    # 2-year cutover is more than enough for tier calibration.
    _LIFETIME_LOWER = '2024-01-01'
    # 2026-09-17 apply ban policy — see _pick_prop_tier docstring for full
    # rationale. Legacy 'prop' surface (PRIME+STRONG combined) needs the
    # same filter or it double-counts historically-published-but-now-banned
    # families.
    try:
        from prop_ban_policy import is_banned_mlb_prop
    except ImportError:
        is_banned_mlb_prop = lambda pt, tier=None: False
    # 2026-09-17: publish-lock JOIN. Any prop row that appeared on a
    # user surface (Sweat Card / Sharp Card / POTD / Prop Jerry etc)
    # has a row in `publish_lock` with the tier + conviction at first-
    # publish time. Grader prefers the LOCKED tier when present; falls
    # back to live tier for legacy rows or rows that never got locked
    # (early exits, etc.). Result: mid-day mutations to live tier
    # (generate_props --force wipes, LR override yo-yo) can never change
    # what the record counts. "What you saw is what we grade."
    #
    # See supabase/migrations/20260917e_prop_tier_publish_lock.sql for
    # publish_lock schema. Publishers write via prop_publish_lock.py.
    try:
        lock_map = _fetch_publish_locks(sport_market_pairs=[
            ('MLB', 'prop'), ('NFL', 'prop'),
        ])
    except Exception:
        lock_map = {}
    # 2026-10-02: NHL and NBA added. Their prop tables were absent, so even
    # once grade_props started writing NHL results (85439fd1) the rollup
    # produced ZERO surface_records rows for them — grading without recording
    # is still not a product. Verified all columns in the select below exist on
    # both tables first; a missing column 400s and the bare `except` around
    # this loop would have swallowed it silently.
    for tbl, sport in [('mlb_pipeline_props', 'MLB'), ('nfl_pipeline_props', 'NFL'),
                       ('nhl_pipeline_props', 'NHL'), ('nba_pipeline_props', 'NBA')]:
        url = (f'{SB}/rest/v1/{tbl}'
               f'?select=id,game_date,result,tier,conviction,direction,prop_type,book_over_odds,book_under_odds'
               f',player_lookback_updated_at'
               f'&result=not.is.null'
               f'&game_date=gte.{_LIFETIME_LOWER}'
               f'&order=game_date.desc')
        try:
            for r in _paged(url):
                cls = _classify(r.get('result'))
                if cls is None: continue
                # 2026-10-02: preseason is GRADED for modelling but must not
                # be counted here — see _is_preseason.
                if _is_preseason(sport, r.get('game_date')): continue
                # 2026-10-03 L5 leak quarantine — see _prop_lookback_leaked.
                # These rows' tier was set partly by the outcome; counting
                # them inflates PRIME from -9.3% to +21.6% ROI.
                if _prop_lookback_leaked(r): continue
                # Prefer publish-lock over live tier when the row was
                # actually published to a user surface. Fallback for
                # legacy/unpublished rows: use live tier (backward compat).
                locked = lock_map.get((sport, 'prop', str(r.get('id'))))
                if locked:
                    effective_tier = (locked.get('tier_at_publish') or '').upper()
                    effective_conv = locked.get('conviction_at_publish')
                else:
                    effective_tier = (r.get('tier') or '').upper()
                    effective_conv = r.get('conviction')
                if effective_tier not in ('PRIME', 'STRONG'):
                    continue
                if effective_conv == 0: continue
                # Apply current ban policy so historical rollups reflect
                # the pool users see today (MLB only — NFL props table
                # has no batter-family bans).
                if sport == 'MLB' and is_banned_mlb_prop(r.get('prop_type'), effective_tier):
                    continue
                try:
                    d = dt.date.fromisoformat(r['game_date'])
                except Exception:
                    continue
                # Odds enforcement mirrors agg_sharp_card: props with odds
                # outside [-300, +150] are excluded (feedback_prop_jerry_odds).
                direction = (r.get('direction') or '').lower()
                if direction == 'over':
                    odds_val = r.get('book_over_odds')
                elif direction == 'under':
                    odds_val = r.get('book_under_odds')
                else:
                    odds_val = None
                if odds_val is not None:
                    try:
                        oi = int(odds_val)
                        if oi < -300 or oi > 150: continue
                    except (TypeError, ValueError):
                        pass
                # 2026-09-05 ROOT-CAUSE FIX: stake sizing must mirror
                # aggregate_daily_records.agg_sharp_card exactly so
                # surface_records.prop reconciles with daily_surface_records.
                # Prior TIER_UNITS map (2u PRIME / 1.5u STRONG) + juice halving
                # inflated units_won by ~65% vs the actual sharp_card record.
                # Now: 1u flat post-cutover, 2u flat pre-cutover — same rule
                # agg_sharp_card applies at write time.
                stake = 1.0 if d >= _CUTOVER else 2.0
                if odds_val is None:
                    payout = 0.909
                else:
                    try:
                        o = int(odds_val)
                        payout = _american_win_payout(o)
                    except (TypeError, ValueError):
                        payout = 0.909
                out.append({'sport': sport, 'date': d, 'result': cls,
                            'stake': stake, 'payout': payout})
        except requests.HTTPError as e:
            print(f'  prop:{tbl} skipped ({e})', file=sys.stderr)
    return out


def pick_ladder() -> list[dict]:
    """ladder_rung — cross-sport."""
    url = (f'{SB}/rest/v1/ladder_rung'
           f'?select=sport,game_date,result,tier,conviction,odds_american'
           f'&result=not.is.null&order=game_date.desc')
    out = []
    for r in _paged(url):
        cls = _classify(r.get('result'))
        if cls is None: continue
        if r.get('conviction') == 0 or (r.get('tier') or '').upper() == 'COVERAGE':
            continue
        try:
            d = dt.date.fromisoformat(r['game_date'])
        except Exception:
            continue
        sp = (r.get('sport') or '').upper() or 'MLB'
        # Ladder = one play per day; unit stake always 1.0
        stake = 1.0
        payout = _american_win_payout(r.get('odds_american'))
        out.append({'sport': sp, 'date': d, 'result': cls,
                    'stake': stake, 'payout': payout})
    return out


def pick_ledger() -> list[dict]:
    """ledger_snapshots — FROZEN teasers/parlays at generation time.

    2026-08-29: switched from ledger_suggestions to ledger_snapshots.
    grade_ledger_snapshots.py writes results ONLY to ledger_snapshots
    (the immutable frozen-at-generation-time table), never to
    ledger_suggestions (the live-editable pick queue). Previous version
    read from ledger_suggestions.result and only found 6 graded picks
    since 8/20 — the grader had been writing results downstream to the
    wrong-for-this-purpose sibling table for 9 days. Fix: read the
    graded table directly.
    """
    url = (f'{SB}/rest/v1/ledger_snapshots'
           f'?select=sport_scope,game_date,result,combined_odds,unit_pnl'
           f'&result=not.is.null&order=game_date.desc')
    out = []
    for r in _paged(url):
        cls = _classify(r.get('result'))
        if cls is None: continue
        try:
            d = dt.date.fromisoformat(r['game_date'])
        except Exception:
            continue
        sp = (r.get('sport_scope') or '').upper() or 'MLB'
        stake = 1.0
        # Prefer grader's unit_pnl (accounts for pushed legs); fall back
        # to combined_odds win math for older rows.
        pnl = r.get('unit_pnl')
        if pnl is not None:
            try:
                pnl = float(pnl)
                # Convert to (win_payout, was_win) shape expected downstream.
                if cls == 'W':
                    payout = pnl   # unit_pnl is profit per 1u stake on wins
                else:
                    payout = _american_win_payout(r.get('combined_odds'))
            except (TypeError, ValueError):
                payout = _american_win_payout(r.get('combined_odds'))
        else:
            payout = _american_win_payout(r.get('combined_odds'))
        out.append({'sport': sp, 'date': d, 'result': cls,
                    'stake': stake, 'payout': payout})
    return out


def pick_potd() -> list[dict]:
    """daily_best_bet_history — Play of the Day, cross-sport.

    2026-08-27: now uses real odds_american snapshotted at write time (see
    play_of_day.py POTD writer). Rows predating that (migration
    20260827c_potd_odds_capture.sql backfills MLB ML picks; totals/spreads
    stay NULL) fall back to -110.
    """
    url = (f'{SB}/rest/v1/daily_best_bet_history'
           f'?select=bet_date,result,sport,odds_american'
           f'&result=not.is.null&order=bet_date.desc')
    out = []
    for r in _paged(url):
        cls = _classify(r.get('result'))
        if cls is None: continue
        try:
            d = dt.date.fromisoformat(r['bet_date'])
        except Exception:
            continue
        sp = (r.get('sport') or '').upper() or 'MLB'
        payout = _american_win_payout(r.get('odds_american'))
        out.append({'sport': sp, 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': payout})
    return out


def pick_dawg() -> list[dict]:
    """daily_dawg — Dawg of the Day (MLB-only currently).

    2026-09-02: added per audit follow-up. DoD was missing from
    surface_records aggregation — Receipts total under-counted by
    excluding Dawg P/L.

    Schema note: daily_dawg has no `sport` or `odds_american` columns
    (unlike daily_best_bet_history). Defaults: sport='MLB', payout=-110
    (0.909). If DoD ever extends cross-sport OR captures odds, update
    both this function and the daily_dawg schema/writer together.
    """
    url = (f'{SB}/rest/v1/daily_dawg'
           f'?select=game_date,result'
           f'&result=not.is.null&order=game_date.desc')
    out = []
    for r in _paged(url):
        cls = _classify(r.get('result'))
        if cls is None: continue
        try:
            d = dt.date.fromisoformat(r['game_date'])
        except Exception:
            continue
        out.append({'sport': 'MLB', 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': 0.909})
    return out


def pick_ncaaf_sides() -> list[dict]:
    """ncaaf_game_context.primary_play graded against ncaaf_game_results
    outcomes (2026-08-30). No persistence — grades inline at aggregation
    time. Only PRIME/STRONG/LEAN counted; COVERAGE/PASS/SKIP filtered.
    Pushed spreads/totals grade as P.

    Grades:
      type='ml':    pick side wins iff home_win == (side=='HOME')
      type='rl':    home_covers → HOME wins RL; away_covers → AWAY wins;
                    push → P
      type='total': OVER wins iff total_result=='Over' (case-insensitive
                    match); UNDER inversely; Push → P
    """
    # Pull ctx + results in bulk (both tables are small, <2000 rows/season)
    ctx_url = (f'{SB}/rest/v1/ncaaf_game_context'
               f'?select=game_id,game_date,primary_play&primary_play=not.is.null')
    res_url = (f'{SB}/rest/v1/ncaaf_game_results'
               f'?select=game_id,home_win,spread_result,total_result')

    ctx_rows = list(_paged(ctx_url))
    res_map = {r['game_id']: r for r in _paged(res_url) if r.get('game_id')}

    out = []
    for c in ctx_rows:
        pp = c.get('primary_play') or {}
        tier = (pp.get('tier') or '').upper()
        if tier not in ('PRIME', 'STRONG', 'LEAN'):
            continue
        gid = c.get('game_id')
        res = res_map.get(gid)
        if not res:
            continue   # game hasn't been graded yet
        ptype = (pp.get('type') or '').lower()
        side  = (pp.get('side') or '').upper()
        cls = None
        if ptype == 'ml':
            hw = res.get('home_win')
            if hw is None: continue
            cls = 'win' if ((side == 'HOME' and hw) or (side == 'AWAY' and not hw)) else 'loss'
        elif ptype in ('rl', 'spread'):
            sr = (res.get('spread_result') or '').lower()
            if sr == 'push': cls = 'push'
            elif sr == 'home_covered': cls = 'win' if side == 'HOME' else 'loss'
            elif sr == 'away_covered': cls = 'win' if side == 'AWAY' else 'loss'
            else: continue
        elif ptype == 'total':
            tr = (res.get('total_result') or '').lower()
            if tr == 'push': cls = 'push'
            elif tr == 'over':  cls = 'win' if side == 'OVER' else 'loss'
            elif tr == 'under': cls = 'win' if side == 'UNDER' else 'loss'
            else: continue
        else:
            continue
        try: d = dt.date.fromisoformat(c['game_date'])
        except Exception: continue
        # Use flat -110 payout — NCAAF spread/total juice is uniform; ML
        # varies but ctx doesn't store the close ML for the pick side yet.
        out.append({'sport': 'NCAAF', 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': 0.909})
    return out


_SEASON_START_CACHE: dict | None = None


def _is_preseason(sport: str, game_date) -> bool:
    """True when `game_date` falls before that sport's declared season start.

    ══ 2026-10-02 · THE SPORT-UNIVERSAL VERSION OF _is_nhl_preseason ══
    _is_nhl_preseason below reads the game TYPE out of the NHL game id, which
    only NHL encodes. Its own docstring flags the hole: "NBA (opens 10-03) and
    MLB spring training have the same exposure and cannot be detected this way
    — they need a season_type column."

    sport_registry.season_start is that missing column, and it already exists:
    NBA 2026-10-21 (state preseason), NCAAB 2026-11-03, NHL 2026-09-29. It is
    the same source sport_season_gate uses to withhold PICKS, so records and
    publishing now agree rather than contradicting each other.

    WHY A DATE FILTER HERE RATHER THAN A GRADING BLOCK:
    Andy wants preseason COLLECTED and RECORDED for modelling — it is the only
    outcome data those games will ever produce. So grade_props grades it and
    this declines to count it. Both halves are required; drop this one and
    preseason walks into the published record.

    Fails CLOSED toward counting (returns False) when the registry cannot be
    read, so a registry outage can never silently empty a sport's record.
    """
    global _SEASON_START_CACHE
    if _SEASON_START_CACHE is None:
        _SEASON_START_CACHE = {}
        try:
            r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H,
                             params={'select': 'sport,season_start'}, timeout=20)
            for row in (r.json() or []):
                if row.get('season_start'):
                    _SEASON_START_CACHE[str(row['sport']).upper()] = \
                        str(row['season_start'])[:10]
        except Exception as e:
            print(f'  ⚠ sport_registry unreadable ({type(e).__name__}) — '
                  f'preseason filter inactive this run')
    start = _SEASON_START_CACHE.get((sport or '').upper())
    if not start or not game_date:
        return False
    return str(game_date)[:10] < start


def _is_nhl_preseason(game_id) -> bool:
    """True for an NHL PRESEASON game, read off the NHL game id.

    2026-09-30. NHL ids are YYYY + TT + NNNN where TT is the game type:
    01 preseason, 02 regular season, 03 playoffs. e.g. 2026010042 is
    preseason; 2026020001 is the first regular-season game.

    WHY THIS EXISTS. Measured 2026-09-30: every one of NHL's 43 graded
    jerry_reads was type 01, and surface_records was publishing
    'NHL 33-18, +12.0 units, lifetime' built ENTIRELY on preseason. NHL
    preseason is played by prospects and AHL call-ups on partial starter
    minutes with no game-planning — it is not a track record, and putting it
    on Receipts under 'lifetime' tells a subscriber something untrue.

    Excluding it makes the published NHL record look WORSE (it removes a
    25-18 sample), which is the honest direction. The regular season began
    2026-09-29, so it rebuilds on real games within weeks.

    NOTE: only NHL is filtered here, because only NHL encodes the type in its
    id. NBA (opens 10-03) and MLB spring training have the same exposure and
    cannot be detected this way — they need a season_type column. Logged, not
    silently assumed away.
    """
    s = str(game_id or '')
    return len(s) >= 10 and s.isdigit() and s[4:6] == '01'


_COLCHECK: dict = {}


def _mkt(sport: str) -> str:
    """The sport's word for the spread market in its column names."""
    return 'puckline' if sport == 'NHL' else 'spread'


def _column_exists(table: str, col: str) -> bool:
    """One cached probe per (table, col). Fails CLOSED: on any error we
    report absent, so grading falls back to -110 rather than raising and
    blanking the surface.

    2026-10-01: wrote this against `H_READ`, which does not exist in this
    module (the header here is `H`). The broad `except Exception` swallowed
    the NameError and returned False for EVERY column, so the probe would
    have reported the price columns missing forever — including after the
    migration landed — and the -110 fallback would have looked like correct
    behaviour. Caught only by probing a column known to exist
    (`close_total`) and getting False. Any fail-closed guard needs a
    positive control, or it cannot be distinguished from a broken one.
    """
    ck = (table, col)
    if ck in _COLCHECK:
        return _COLCHECK[ck]
    try:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                         params={'select': col, 'limit': 1}, timeout=15)
        ok = r.status_code == 200
    except Exception as e:
        print(f'  ⚠ column probe failed for {table}.{col}: {e}')
        ok = False
    _COLCHECK[ck] = ok
    return ok


def _side_price(res: dict, keys: dict, side: str, market: str):
    """American price for the picked side of an rl/total bet, or None.

    2026-10-01. Returns None (caller falls back to -110) whenever the sport's
    migration hasn't landed — keys carries no entry — or the book didn't quote
    that side. None means UNKNOWN, never "it was -110".
    """
    which = {
        ('spread', 'HOME'):  'spread_home_price',
        ('spread', 'AWAY'):  'spread_away_price',
        ('total',  'OVER'):  'total_over_price',
        ('total',  'UNDER'): 'total_under_price',
    }.get((market, side))
    col = keys.get(which) if which else None
    return res.get(col) if col else None


def _pick_generic_sides(sport: str, ctx_table: str, res_table: str,
                         result_key_map: dict = None,
                         skip_game=None) -> list[dict]:
    """2026-09-09 UNIFORM sides picker for all sports.

    Root fix for Receipts inconsistency — MLB had no {sport}_sides
    surface, NCAAF had ncaaf_sides, others had nothing. Result:
    Receipts couldn't consistently show "engine record for sport X"
    because the surface didn't exist for most sports.

    This helper grades every primary_play from the sport's game_context
    against its results table. Follows the ncaaf_sides pattern.

    Args:
        sport:       'MLB' | 'NFL' | 'NBA' | 'NHL' | 'NCAAB' | 'UFC'
        ctx_table:   e.g. 'mlb_game_context'
        res_table:   e.g. 'mlb_game_results'
        result_key_map: optional overrides for columns in res_table
                        (some sports use win/loss vs home_win)

    Filters: tier in PRIME/STRONG/LEAN. Skips COVERAGE/PASS/SKIP.
    Payout: flat -110 unless res table has close ML odds (future work).
    """
    # ══ 2026-10-01 · ML MUST NOT BE PRICED AT -110 ══
    # The docstring below still says "flat -110 unless res table has close ML
    # odds (future work)". Every results table HAS carried close ML the whole
    # time; nothing was blocking this but the lookup.
    #
    # It is not a rounding issue. NHL's published sides record is 100% moneyline
    # (88 of 88 publishable picks), and 9/30's two LEAN plays were COL -180 and
    # PHI -130. Priced flat they read 1-1 / -0.09u; priced honestly COL returns
    # +0.556 on a win, so the same 1-1 is -0.444u. A heavy favourite that wins
    # half its games LOSES money, and flat -110 reports that as break-even —
    # the exact artifact that made NCAAF COVERAGE look like a 60% winner
    # (project_ncaaf_ml_path_is_the_leak_930) and the reason
    # project_sharp_money_football_opposite_930 says never grade ML at -110.
    #
    # COLUMN NAMES DIVERGE AND THAT IS THE TRAP: MLB and NCAAB spell it
    # home_ml_close / away_ml_close; NFL, NCAAF, NHL and NBA spell it
    # close_home_ml / close_away_ml. NCAAB carries BOTH. Defaulting to the
    # majority spelling and overriding MLB through the existing result_key_map
    # keeps this in one place instead of per-sport branching.
    keys = {
        'home_win': 'home_win',
        'spread_result': 'spread_result',
        'total_result': 'total_result',
        'home_ml': 'close_home_ml',
        'away_ml': 'close_away_ml',
        **(result_key_map or {}),
    }
    # 2026-09-11 game_id-mismatch fix. NFL (and any sport with divergent
    # id conventions between game_context and game_results) had
    # res_map.get(ctx.game_id) always return None because ctx uses the
    # Odds API hash while results use schedule format. Every non-MLB
    # sport's sides surface silently reported 0 graded picks since
    # launch. Fix: join by (away_team, home_team, week_bucket) tuple
    # instead of raw game_id. MLB and NCAAF (whose game_id conventions
    # already match across their two tables) stay compatible because
    # the tuple is unique per matchup+week regardless.
    ctx_url = (f'{SB}/rest/v1/{ctx_table}'
               f'?select=game_id,game_date,away_team,home_team,primary_play&primary_play=not.is.null')
    # ══ 2026-10-01 · PROBE, DON'T ASSUME, THE PRICE COLUMNS ══
    # Only NHL has spread/total price columns today (20261001a). Hardcoding
    # them into the select would 400 for every other sport, and because the
    # fetch now RAISES on failure that would blank each sport's sides record
    # until its own migration landed — an ordering dependency between a SQL
    # paste and a deploy. Probing removes it: each sport picks the columns up
    # by itself the moment they exist, in either order.
    _price_cols = {
        'spread_home_price': f'close_{_mkt(sport)}_home_price',
        'spread_away_price': f'close_{_mkt(sport)}_away_price',
        'total_over_price':  'close_total_over_price',
        'total_under_price': 'close_total_under_price',
    }
    for _logical, _col in _price_cols.items():
        if _col not in keys and _column_exists(res_table, _col):
            keys[_logical] = _col
    _price_sel = ''.join(f',{keys[k]}' for k in _price_cols if k in keys)
    res_url = (f'{SB}/rest/v1/{res_table}'
               f'?select=game_id,game_date,away_team,home_team,'
               f'{keys["home_win"]},{keys["spread_result"]},{keys["total_result"]},'
               f'{keys["home_ml"]},{keys["away_ml"]}{_price_sel}')
    # 2026-09-30: these two used to `return []` on a fetch failure, which made
    # a broken query indistinguishable from a sport that genuinely has no graded
    # picks. That ambiguity is why the stale-row prune below could not be
    # written safely — clearing on empty would have destroyed good records every
    # time a query hiccupped. Raising lets build_rows tell the two apart.
    try:
        ctx_rows = list(_paged(ctx_url))
    except Exception as e:
        raise RuntimeError(f'{sport} ctx fetch failed: {e}') from e
    try:
        res_rows = list(_paged(res_url))
    except Exception as e:
        raise RuntimeError(f'{sport} results fetch failed: {e}') from e

    def _week_bucket(dstr):
        try:
            d = dt.date.fromisoformat(dstr)
        except Exception:
            return ''
        # Snap to most-recent Thursday (NFL) or use date as-is for
        # sports without a Thu-based week (still gives a stable
        # per-day bucket since same-day rematches don't happen).
        if sport in ('NFL', 'NCAAF'):
            return (d - dt.timedelta(days=(d.weekday() - 3 + 7) % 7)).isoformat()
        return d.isoformat()

    res_map = {}
    for r in res_rows:
        if not isinstance(r, dict): continue
        key = (r.get('away_team'), r.get('home_team'),
               _week_bucket(r.get('game_date') or ''))
        # Prefer the row with actual scores over a schedule-only skeleton
        existing = res_map.get(key)
        if existing is None or (existing.get(keys['home_win']) is None
                                and r.get(keys['home_win']) is not None):
            res_map[key] = r
    out = []
    skipped_pre = 0
    ml_priced = 0        # graded off a real moneyline price
    ml_flat = 0          # fell back to -110 because no price was available
    nonml_priced = 0     # rl/total graded off a real price
    nonml_flat = 0       # rl/total fell back to -110
    for c in ctx_rows:
        # 2026-09-30: sport-specific exclusion, currently NHL preseason. Applied
        # BEFORE the tier filter so the count below reports every excluded game,
        # not only the ones that would have graded.
        if skip_game is not None and skip_game(c.get('game_id')):
            skipped_pre += 1
            continue
        pp = c.get('primary_play') or {}
        if not isinstance(pp, dict): continue
        tier = (pp.get('tier') or '').upper()
        if tier not in ('PRIME', 'STRONG', 'LEAN'):
            continue
        key = (c.get('away_team'), c.get('home_team'),
               _week_bucket(c.get('game_date') or ''))
        res = res_map.get(key)
        if not res: continue
        ptype = (pp.get('type') or '').lower()
        side  = (pp.get('side') or '').upper()
        cls = None
        payout = 0.909            # -110, correct for rl/spread/total
        if ptype == 'ml':
            hw = res.get(keys['home_win'])
            if hw is None: continue
            cls = 'win' if ((side == 'HOME' and hw) or (side == 'AWAY' and not hw)) else 'loss'
            # Prefer the price AT PICK TIME — that is the price we actually
            # published and carries no look-ahead. Fall back to the close,
            # which is the standard benchmark, and only then to -110.
            # _ml_price_at_pick coverage measured 2026-10-01: MLB 0/72,
            # NFL 8/95, NCAAF 29/63, NHL 12/88 — so the close carries most of
            # this and the at-pick price is the exception, not the rule.
            px = pp.get('_ml_price_at_pick')
            if px is None:
                px = res.get(keys['home_ml'] if side == 'HOME' else keys['away_ml'])
            if px is None:
                ml_flat += 1
            else:
                ml_priced += 1
                payout = _american_win_payout(px)
        elif ptype in ('rl', 'spread'):
            sr = (res.get(keys['spread_result']) or '').lower()
            if sr == 'push': cls = 'push'
            elif sr == 'home_covered': cls = 'win' if side == 'HOME' else 'loss'
            elif sr == 'away_covered': cls = 'win' if side == 'AWAY' else 'loss'
            else: continue
            # ══ 2026-10-01 · AN NHL PUCK LINE IS NOT -110 ══
            # -110 is close enough for a football spread. It is nowhere near an
            # NHL puck line: measured off 27 live boards this date, taking +1.5
            # ran a MEDIAN of -216 (range -276..-126, breakeven 68.4%) while
            # laying -1.5 paid +177 (breakeven 36.1%). Grading both at -110
            # makes a 72% +1.5 winner look like a monster and a 36% -1.5 bet
            # look like a disaster, when the two are nearly the same edge.
            # Priced only where the column exists (NHL today), else -110.
            px = _side_price(res, keys, side, 'spread')
            if px is None:
                nonml_flat += 1
            else:
                nonml_priced += 1
                payout = _american_win_payout(px)
        elif ptype == 'total':
            tr = (res.get(keys['total_result']) or '').lower()
            if tr == 'push': cls = 'push'
            elif tr == 'over':  cls = 'win' if side == 'OVER' else 'loss'
            elif tr == 'under': cls = 'win' if side == 'UNDER' else 'loss'
            else: continue
            # Totals DO sit near -110 (measured -110 both ways, breakeven
            # 52.4%), so this changes little — but it removes the assumption.
            px = _side_price(res, keys, side, 'total')
            if px is None:
                nonml_flat += 1
            else:
                nonml_priced += 1
                payout = _american_win_payout(px)
        else:
            continue
        try: d = dt.date.fromisoformat(c['game_date'])
        except Exception: continue
        out.append({'sport': sport, 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': payout})
    if skipped_pre:
        print(f'  {sport} sides: excluded {skipped_pre} preseason game(s) '
              f'from the published record')
    if ml_priced or ml_flat:
        print(f'  {sport} sides: {ml_priced} ML graded on a real price, '
              f'{ml_flat} fell back to -110')
    if nonml_priced or nonml_flat:
        print(f'  {sport} sides: {nonml_priced} rl/total graded on a real '
              f'price, {nonml_flat} fell back to -110')
    return out


def pick_mlb_sides() -> list[dict]:
    """MLB full engine sides record — every graded primary_play, all tiers.
    Complements 'sharp' (PRIME/STRONG only) and 'sharp_card' (curated slice)."""
    # MLB spells the closing moneyline home_ml_close / away_ml_close, where
    # every other sport uses close_home_ml / close_away_ml. Without this
    # override the select 400s and the whole surface raises.
    return _pick_generic_sides('MLB', 'mlb_game_context', 'mlb_game_results',
                               result_key_map={'home_ml': 'home_ml_close',
                                               'away_ml': 'away_ml_close'})


def pick_nfl_sides() -> list[dict]:
    return _pick_generic_sides('NFL', 'nfl_game_context', 'nfl_game_results')


def pick_nba_sides() -> list[dict]:
    return _pick_generic_sides('NBA', 'nba_game_context', 'nba_game_results')


def pick_nhl_sides() -> list[dict]:
    # skip_game excludes PRESEASON — see _is_nhl_preseason. Without it the
    # published NHL record was 100% preseason.
    return _pick_generic_sides('NHL', 'nhl_game_context', 'nhl_game_results',
                               skip_game=_is_nhl_preseason)


def pick_ncaab_sides() -> list[dict]:
    return _pick_generic_sides('NCAAB', 'ncaab_game_context', 'ncaab_game_results')


def pick_ufc_sides() -> list[dict]:
    """UFC winner-pick surface — ufc_picks has recommended_side + winner_actual
    inline (no separate results table). PRIME/STRONG/LEAN tiers only, matching
    the mlb_sides pattern. Payout flat -110 like other sides."""
    url = (f'{SB}/rest/v1/ufc_picks'
           f'?select=event_date,tier_winner,recommended_side,winner_actual'
           f'&winner_actual=not.is.null')
    try:
        rows = list(_paged(url))
    except Exception:
        return []
    out = []
    for r in rows:
        t = (r.get('tier_winner') or '').upper()
        if t not in ('PRIME', 'STRONG', 'LEAN'): continue
        rs = r.get('recommended_side')
        wa = r.get('winner_actual')
        if not rs or not wa: continue
        try: d = dt.date.fromisoformat(r['event_date'])
        except Exception: continue
        cls = 'win' if rs.lower() == wa.lower() else 'loss'
        out.append({'sport': 'UFC', 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': 0.909})
    return out


# ══ SHARP LEGACY CUTOVER (2026-10-05, Andy's call) ══
# Andy: "I dont care about unverifiable ... what counts is verifiable record
# from here on out but changing the record by 30 units is unsat. I want the
# record to reflect the +100ish unit that users have seen all week."
#
# WHAT ACTUALLY HAPPENED, so nobody re-derives the wrong lesson later. The
# all-time Sharp figure did not fall because we lost. It fell because the
# METHOD changed underneath it on 10-04:
#
#   old  agg_sharp_card                 jerry_cache.sharp_card_{date}, flat -110
#   new  agg_sharp_card_from_receipts   public_receipts, the REAL price
#
# Flat -110 pays every win 0.909 units. The Sharp ships a lot of MLB
# favourites, and a -200 favourite pays 0.50 — so every favourite win was
# credited with up to 80% more than it actually returned. The receipt number
# is the honest one and it is LOWER. Measured:
#
#   legacy era  08-20..09-02  169-119-1  +32.22u   (cache, flat -110)
#   receipt era 09-03..10-04  333-234-1  +19.74u   (receipts, real prices)
#
# Rewriting published history onto the lower basis made a methodology change
# look to paying users like a 30-unit losing day, which is the one thing it
# definitely was not.
#
# So: history is FROZEN at what was published, and everything after the
# cutover is receipt-verified. The frozen figure is the MLB slice exactly as
# the shipped app displayed it on 10-04, recorded in app/index.tsx:
#
#     MLB 455-297-9, +110.73u, +14.7%
#
# HONESTY CONDITIONS, non-negotiable:
#   * the frozen portion is flat-priced and therefore OVERSTATES. It must
#     never be cited as proven edge or quoted as an ROI we achieved.
#   * basis='legacy_flat_110' is stamped on every frozen pick so the two eras
#     can always be separated again.
#   * only MLB is frozen, because MLB is the only slice the shipped app has
#     ever shown as the headline. NCAAF/NFL/NHL keep their receipt-based
#     history untouched, which puts ALL near +96u.
# Set SHARP_LEGACY_FREEZE = False to return to a single receipt-only basis.
SHARP_LEGACY_FREEZE = True
SHARP_LEGACY_CUTOVER = dt.date(2026, 10, 4)      # inclusive last legacy day
SHARP_LEGACY_FROZEN = {'MLB': (455, 297, 9, 110.73)}


def pick_sharp_card() -> list[dict]:
    """Sharp Card composite (sides + props combined) — reads directly from
    daily_surface_records.sharp_card, the authoritative per-day rollup
    written by aggregate_daily_records.agg_sharp_card (which now sources
    from jerry_cache.sharp_card_YYYY-MM-DD, source of truth for what
    actually shipped).

    2026-09-05 NEW surface. App should read surface_records.sharp_card
    for the Sharp Card record display — this row = single source of
    truth that reconciles across Receipts + Steam Room + rollups.
    Prior split (surface_records.sharp + .prop combined at read time)
    always risked drift.
    """
    url = (f'{SB}/rest/v1/daily_surface_records'
           f'?surface=eq.sharp_card'
           f'&select=sport,record_date,wins,losses,pushes,units_bet,units_won,pick_count'
           f'&order=record_date.desc')
    out = []
    for r in _paged(url):
        try:
            d = dt.date.fromisoformat(r['record_date'])
        except Exception:
            continue
        if d < SHARP_RECORD_EPOCH: continue
        sp = (r.get('sport') or 'MLB').upper()
        # ══ 2026-10-05: THE DOUBLE COUNT ══
        # aggregate_daily_records writes BOTH a per-sport row AND an 'ALL'
        # rollup row per date. This loop took every row with
        # surface=sharp_card regardless of sport, so on any date carrying
        # both, every pick was counted TWICE — once in its sport, once in ALL.
        #
        # It stayed invisible while no ALL rows existed. Backfilling 35 days
        # on 10-04 created 31 of them and the published all-time record jumped
        # to a population that never happened:
        #
        #     per-sport rows only   502-351-2   +53.96u
        #     ALL rows only         333-232-1   +21.74u   (== receipts)
        #     summed, i.e. shipped  835-583-3   +75.70u   <- wrong
        #
        # _aggregate below builds its own ALL rollup from the per-sport
        # entries, so the ALL rows must be skipped here, not summed.
        if sp == 'ALL':
            continue
        # Frozen sports contribute a fixed block for everything up to the
        # cutover (emitted after this loop), so their daily rows inside that
        # window must not be counted a second time.
        if (SHARP_LEGACY_FREEZE and sp in SHARP_LEGACY_FROZEN
                and d <= SHARP_LEGACY_CUTOVER):
            continue
        # Emit one entry per (win, loss, push) so the aggregator's
        # bucket-into-window logic (in _aggregate) sees individual picks.
        # units are already computed at day-level — divide evenly across picks
        # for accurate window rollup.
        w = r.get('wins') or 0
        l = r.get('losses') or 0
        p_ = r.get('pushes') or 0
        n = w + l + p_
        if n == 0: continue
        # Emit synthetic per-pick rows preserving day-level unit totals
        # by attaching the units_won proportionally.
        won = float(r.get('units_won') or 0)
        # Aggregator math (see _aggregate below):
        #   units += stake*payout  on 'win'
        #   units -= stake         on 'loss'
        # Losses are already -stake, so per-win payout must total (won + l)
        # to leave the day at exactly `won` net units. Emit w win rows with
        # payout=(won+l)/w and l loss rows with stake=1.
        per_win_payout = (won + l) / w if w > 0 else 0
        for _ in range(w):
            out.append({'sport': sp, 'date': d, 'result': 'win',
                        'stake': 1.0, 'payout': per_win_payout})
        for _ in range(l):
            out.append({'sport': sp, 'date': d, 'result': 'loss',
                        'stake': 1.0, 'payout': 0.909})
        for _ in range(p_):
            out.append({'sport': sp, 'date': d, 'result': 'push',
                        'stake': 1.0, 'payout': 0.909})

    if SHARP_LEGACY_FREEZE:
        # The frozen pre-cutover block, emitted as synthetic picks dated ON
        # the cutover using the same payout trick as above so every window
        # rollup lands on exactly the published unit total.
        for sp, (w, l, p_, won) in SHARP_LEGACY_FROZEN.items():
            if w <= 0:
                continue
            per_win = (won + l) / w
            for _ in range(w):
                out.append({'sport': sp, 'date': SHARP_LEGACY_CUTOVER,
                            'result': 'win', 'stake': 1.0, 'payout': per_win,
                            'basis': 'legacy_flat_110'})
            for _ in range(l):
                out.append({'sport': sp, 'date': SHARP_LEGACY_CUTOVER,
                            'result': 'loss', 'stake': 1.0, 'payout': 0.909,
                            'basis': 'legacy_flat_110'})
            for _ in range(p_):
                out.append({'sport': sp, 'date': SHARP_LEGACY_CUTOVER,
                            'result': 'push', 'stake': 1.0, 'payout': 0.909,
                            'basis': 'legacy_flat_110'})
    return out


def _pick_prop_tier(tier_filter: str) -> list[dict]:
    """2026-09-09: tier-specific prop picker for LEAN/COVERAGE surfaces.

    Mirrors pick_prop() but with tier=in.({tier_filter}) instead of
    hard-coded PRIME/STRONG. Root fix for "Receipts LEAN props 0-0" bug —
    surface_records had no rollup for non-PRIME prop tiers even though
    result column populates for all tiers.

    2026-09-17: apply current ban policy (prop_ban_policy.is_banned_mlb_prop)
    so historical rollups reflect the pool users can actually see today.
    Without this filter, surface_records.prop_prime included ~1,075 wins
    from batter families that were briefly un-banned then re-banned — a
    published stat off this rollup would misrepresent the current product.
    Voids already excluded via _classify returning None for non-W/L/P.
    """
    from datetime import date as _date_cls
    _CUTOVER = _date_cls.fromisoformat('2026-08-31')
    out = []
    # 2026-09-08 lifetime lower bound to prevent 57014 statement timeout.
    _LIFETIME_LOWER = '2024-01-01'
    # Import ban policy once — falls back to no-filter if module missing
    # (backward-compat for older environments).
    try:
        from prop_ban_policy import is_banned_mlb_prop
    except ImportError:
        is_banned_mlb_prop = lambda pt, tier=None: False
    # 2026-10-02: NHL/NBA added here too. This is the picker that feeds the
    # tier-specific surfaces, and prop_coverage is the ONLY one NHL can reach
    # today — every nhl_pipeline_props row is tier='COVERAGE', so pick_prop()
    # above (PRIME/STRONG only) correctly yields nothing for it.
    for tbl, sport in [('mlb_pipeline_props', 'MLB'), ('nfl_pipeline_props', 'NFL'),
                       ('nhl_pipeline_props', 'NHL'), ('nba_pipeline_props', 'NBA')]:
        url = (f'{SB}/rest/v1/{tbl}'
               f'?select=game_date,result,tier,conviction,direction,prop_type,book_over_odds,book_under_odds'
               f',player_lookback_updated_at'
               f'&result=not.is.null&tier=in.({tier_filter})'
               f'&game_date=gte.{_LIFETIME_LOWER}'
               f'&order=game_date.desc')
        try:
            for r in _paged(url):
                cls = _classify(r.get('result'))
                if cls is None: continue
                # 2026-10-02: preseason graded for modelling, not counted here.
                if _is_preseason(sport, r.get('game_date')): continue
                # 2026-10-03 L5 leak quarantine — see _prop_lookback_leaked.
                if _prop_lookback_leaked(r): continue
                # 2026-09-17 apply prop-family ban policy so historical
                # rollups match the pool users see today (see docstring).
                if sport == 'MLB' and is_banned_mlb_prop(r.get('prop_type'), r.get('tier')):
                    continue
                try:
                    d = dt.date.fromisoformat(r['game_date'])
                except Exception:
                    continue
                direction = (r.get('direction') or '').lower()
                odds_val = r.get('book_over_odds') if direction == 'over' else r.get('book_under_odds')
                if odds_val is not None:
                    try:
                        oi = int(odds_val)
                        if oi < -300 or oi > 150: continue
                    except (TypeError, ValueError):
                        pass
                stake = 1.0  # LEAN/COVERAGE flat 1u
                if odds_val is None:
                    payout = 0.909  # flat -110 fallback
                else:
                    try:
                        oi = int(odds_val)
                        payout = (oi / 100.0) if oi > 0 else (100.0 / abs(oi))
                    except (TypeError, ValueError):
                        payout = 0.909
                out.append({'sport': sport, 'date': d, 'result': cls,
                            'stake': stake, 'payout': payout})
        except Exception:
            continue
    return out


def pick_prop_lean() -> list[dict]:
    """LEAN-tier prop rollup — separate surface from PRIME."""
    return _pick_prop_tier('LEAN')


def pick_prop_strong() -> list[dict]:
    """STRONG-tier prop rollup — between PRIME and LEAN.

    Legacy 'prop' surface bundles PRIME+STRONG. Splitting STRONG out
    so Receipts can show tier-by-tier without recomputing.
    """
    return _pick_prop_tier('STRONG')


def pick_prop_prime() -> list[dict]:
    """PRIME-tier-only prop rollup — the sharpest bucket.

    Complements legacy 'prop' surface (PRIME+STRONG combined) with a
    pure PRIME cut. Lets Receipts show 'PRIME props' as its own line.
    """
    return _pick_prop_tier('PRIME')


def pick_prop_coverage() -> list[dict]:
    """COVERAGE-tier prop rollup — separate surface from PRIME/LEAN."""
    return _pick_prop_tier('COVERAGE')


SURFACES = {
    'sharp':       pick_sharp,      # legacy — MLB sides only from primary_play
    'prop':        pick_prop,       # legacy — props from mlb_pipeline_props
    'sharp_card':  pick_sharp_card, # 2026-09-05 authoritative combined (sides+props from cache)
    'ladder': pick_ladder,
    'ledger': pick_ledger,
    'potd':   pick_potd,
    'dawg':   pick_dawg,   # 2026-09-02: added per audit finding
    'ncaaf_sides': pick_ncaaf_sides,
    # 2026-09-09: per-tier prop rollups so Receipts can show each tier
    # separately. Legacy 'prop' surface bundles PRIME+STRONG; new surfaces
    # split them out so users see tier-by-tier records.
    'prop_prime':    pick_prop_prime,
    'prop_strong':   pick_prop_strong,
    'prop_lean':     pick_prop_lean,
    'prop_coverage': pick_prop_coverage,
    # 2026-09-09 UNIFORM: <sport>_sides surface for every sport.
    # Fixes Receipts inconsistency (MLB had no mlb_sides, others had nothing).
    # Every sport now gets identical structure — Receipts renders same shape.
    # Pickers no-op gracefully when results/context tables don't exist.
    'mlb_sides':   pick_mlb_sides,
    'nfl_sides':   pick_nfl_sides,
    'nba_sides':   pick_nba_sides,
    'nhl_sides':   pick_nhl_sides,
    'ncaab_sides': pick_ncaab_sides,
    'ufc_sides':   pick_ufc_sides,
}


# ─── aggregation ──────────────────────────────────────────────────────────────

def _aggregate(rows: Iterable[dict], sport: str, window: tuple[dt.date, dt.date]):
    start, end = window
    w = l = p = 0
    units = 0.0
    last_date = None
    epoch_start = None
    for r in rows:
        if sport != 'ALL' and r['sport'] != sport: continue
        if r['date'] < start or r['date'] > end: continue
        if epoch_start is None or r['date'] < epoch_start:
            epoch_start = r['date']
        if last_date is None or r['date'] > last_date:
            last_date = r['date']
        if r['result'] == 'win':
            w += 1; units += r['stake'] * r['payout']
        elif r['result'] == 'loss':
            l += 1; units -= r['stake']
        elif r['result'] == 'push':
            p += 1
    total = w + l + p
    if total == 0:
        return None
    hit = (w / (w + l)) if (w + l) else None
    total_stake = sum(1.0 for _ in ())  # placeholder; risk sum happens inline
    # Compute total staked for ROI
    risk = 0.0
    for r in rows:
        if sport != 'ALL' and r['sport'] != sport: continue
        if r['date'] < start or r['date'] > end: continue
        if r['result'] in ('win', 'loss'):
            risk += r['stake']
    roi = (units / risk * 100.0) if risk else None
    return {
        'wins': w, 'losses': l, 'pushes': p,
        'units_net': round(units, 2),
        'picks_count': total,
        'hit_rate': round(hit, 3) if hit is not None else None,
        'roi_pct': round(roi, 2) if roi is not None else None,
        'epoch_start': _iso(epoch_start) if epoch_start else None,
        'last_pick_date': _iso(last_date) if last_date else None,
    }


def build_rows():
    """Returns (rows, computed_ok, run_started_at).

    computed_ok is the set of surfaces whose picker ran WITHOUT error — only
    those are safe to prune stale rows from. A surface whose picker raised is
    left completely alone, because an empty result there means "the query
    broke", not "there are no picks".

    run_started_at stamps every row from this run with ONE timestamp, so the
    prune can delete exactly the rows this run did not refresh.
    """
    today = dt.date.today()
    windows = _windows_today(today)
    run_started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    out_rows = []
    computed_ok = set()
    for surface_name, picker in SURFACES.items():
        try:
            rows = picker()
        except Exception as e:
            print(f'  {surface_name}: PICKER FAILED ({e}) — leaving existing '
                  f'rows untouched', file=sys.stderr)
            continue
        computed_ok.add(surface_name)
        print(f'  {surface_name}: {len(rows)} graded picks', file=sys.stderr)
        for sport in SPORTS + ['ALL']:
            for wname, wrange in windows.items():
                # ══ 2026-10-08 · A CROSS-SPORT SURFACE HAS NO PER-SPORT SPLIT ══
                # Andy: the app showed the Ledger as 1-0 / +1.15u for October
                # and it never moved, while the real record was 9-5 / +7.21u.
                #
                # Not a display bug and not a grading bug. ledger_snapshots IS
                # graded daily. The bug is this data model: a chalk parlay
                # DELIBERATELY combines legs across sports, so October's rows
                # carry sport_scope='MULTI'. _aggregate then matches none of
                # them to 'MLB', and the MLB row held the single MLB-only
                # parlay of the month — frozen at 1-0 by construction.
                #
                # "The MLB ledger record" is a CATEGORY ERROR. There is no
                # such thing, the same way there is no MLB-only record for a
                # two-sport parlay. Splitting this surface by sport invents a
                # dimension the product does not have, and whichever client
                # asks for a sport gets a number that is wrong by definition.
                #
                # So the fix is to remove the dimension, not to paper over the
                # read: a cross-sport surface reports its ALL aggregate under
                # every sport key. Same number whoever asks. This also means
                # the live v1.0.2 build — which hardcodes sport='MLB' because
                # it predates the RECORD_SCOPE change by six days — shows the
                # correct figure WITHOUT an App Store submission.
                agg = _aggregate(rows,
                                 'ALL' if surface_name in CROSS_SPORT_SURFACES
                                 else sport,
                                 wrange)
                if agg is None: continue
                out_rows.append({
                    'sport': sport, 'surface': surface_name, 'window_key': wname,
                    **agg,
                    'last_computed_at': run_started_at,
                })
    return out_rows, computed_ok, run_started_at


def upsert(rows: list[dict]):
    """Upsert into surface_records; PostgREST needs on-conflict spec."""
    if not rows: return
    # PostgREST resolves ON CONFLICT via the composite PK when we set the header
    r = requests.post(
        f'{SB}/rest/v1/surface_records?on_conflict=sport,surface,window_key',
        headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=rows, timeout=90,
    )
    if not r.ok:
        print(f'upsert failed: {r.status_code} {r.text[:400]}', file=sys.stderr)
        r.raise_for_status()


def prune_stale(computed_ok: set, run_started_at: str, dry_run: bool = False,
                would_write: list = None):
    """Delete rows this run did NOT refresh, for surfaces that computed cleanly.

    2026-09-30. WHY: upsert() returns early on an empty list and _aggregate()
    returns None for a window with no picks, so a surface or window that stops
    producing rows keeps publishing its last known record FOREVER. Measured that
    day, before this existed:

        NHL  nhl_sides        33-18 +12.0u "lifetime" — 100% preseason, and it
                              SURVIVED being excluded because the recompute
                              produced 0 rows and the old ones just stayed
        MLB  sharp_card_*     frozen at 09-24 for six days (separate cause —
                              its writer was never scheduled)
        NCAAF ledger d7       frozen 09-17
        NFL   potd   d7       frozen 09-19

    A frozen record is indistinguishable from a correct one on the surface that
    renders it, which is what makes it dangerous.

    SAFETY: only surfaces in computed_ok are pruned. A picker that raised is
    skipped entirely — otherwise a transient query failure would delete a real
    record, which is a far worse outcome than a stale one.
    """
    if not computed_ok:
        return
    # NOTE: the filter goes through requests' params, NOT f-stringed into the
    # URL. An ISO timestamp ends in '+00:00' and a raw '+' in a query string is
    # decoded as a SPACE, so the embedded version silently matched nothing and
    # the dry run printed an empty list that read like "nothing to prune".
    for surface in sorted(computed_ok):
        base = f'{SB}/rest/v1/surface_records'
        filt = {'surface': f'eq.{surface}',
                'last_computed_at': f'lt.{run_started_at}'}
        if dry_run:
            # A dry run writes nothing, so filtering on last_computed_at would
            # flag EVERY existing row as stale — an alarming and useless
            # preview. Compare against the keys this run WOULD write instead:
            # anything present for this surface but absent from that set is what
            # the live prune would actually remove.
            keep = {(r['sport'], r['surface'], r['window_key'])
                    for r in (would_write or []) if r['surface'] == surface}
            r = requests.get(base, headers=H,
                             params={'surface': f'eq.{surface}',
                                     'select': 'sport,surface,window_key'},
                             timeout=30)
            existing = r.json() if r.ok and isinstance(r.json(), list) else []
            for x in existing:
                if (x['sport'], x['surface'], x['window_key']) in keep:
                    continue
                print(f'  [DRY] would prune {x["sport"]:5s} {x["surface"]:18s} '
                      f'{x["window_key"]}', file=sys.stderr)
            continue
        r = requests.delete(base, headers={**H, 'Prefer': 'return=representation'},
                            params=filt, timeout=30)
        if r.ok:
            try:
                n = len(r.json()) if isinstance(r.json(), list) else 0
            except Exception:
                n = 0
            if n:
                print(f'  pruned {n} stale row(s) from {surface}', file=sys.stderr)
        else:
            print(f'  prune failed for {surface}: {r.status_code} '
                  f'{r.text[:160]}', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--json', action='store_true', help='print rows as JSON')
    args = ap.parse_args()

    print(f'compute_surface_records @ {dt.date.today()}', file=sys.stderr)
    rows, computed_ok, run_started_at = build_rows()
    print(f'built {len(rows)} rows', file=sys.stderr)

    if args.json:
        import json
        print(json.dumps(rows, indent=2, default=str))

    if args.dry_run:
        # Preview a few for sanity
        for r in rows[:8]:
            print(f'  {r["sport"]:6s} {r["surface"]:6s} {r["window_key"]:8s}  '
                  f'{r["wins"]}-{r["losses"]}-{r["pushes"]}  '
                  f'{r["units_net"]:+.2f}u  hit={r["hit_rate"]}', file=sys.stderr)
        prune_stale(computed_ok, run_started_at, dry_run=True,
                    would_write=rows)
        return

    upsert(rows)
    print('surface_records upserted', file=sys.stderr)
    # Prune AFTER the upsert, so a row this run refreshed is never a prune
    # candidate. Ordering matters: pruning first would briefly empty a surface.
    prune_stale(computed_ok, run_started_at)


if __name__ == '__main__':
    main()
