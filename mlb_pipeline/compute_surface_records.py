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
WINDOWS = ['mtd', 'd7', 'd30', 'lifetime']
TIER_UNITS = {'PRIME': 2.0, 'STRONG': 1.5, 'LEAN': 1.0, 'COVERAGE': 0.0}


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
    for tbl, sport in [('mlb_pipeline_props', 'MLB'), ('nfl_pipeline_props', 'NFL')]:
        url = (f'{SB}/rest/v1/{tbl}'
               f'?select=game_date,result,tier,conviction,direction,book_over_odds,book_under_odds'
               f'&result=not.is.null&tier=in.(PRIME,STRONG)'
               f'&order=game_date.desc')
        try:
            for r in _paged(url):
                cls = _classify(r.get('result'))
                if cls is None: continue
                if r.get('conviction') == 0: continue
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


SURFACES = {
    'sharp':  pick_sharp,
    'prop':   pick_prop,
    'ladder': pick_ladder,
    'ledger': pick_ledger,
    'potd':   pick_potd,
    'dawg':   pick_dawg,   # 2026-09-02: added per audit finding
    'ncaaf_sides': pick_ncaaf_sides,
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
    today = dt.date.today()
    windows = _windows_today(today)
    out_rows = []
    for surface_name, picker in SURFACES.items():
        rows = picker()
        print(f'  {surface_name}: {len(rows)} graded picks', file=sys.stderr)
        for sport in SPORTS + ['ALL']:
            for wname, wrange in windows.items():
                agg = _aggregate(rows, sport, wrange)
                if agg is None: continue
                out_rows.append({
                    'sport': sport, 'surface': surface_name, 'window_key': wname,
                    **agg,
                    'last_computed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                })
    return out_rows


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--json', action='store_true', help='print rows as JSON')
    args = ap.parse_args()

    print(f'compute_surface_records @ {dt.date.today()}', file=sys.stderr)
    rows = build_rows()
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
        return

    upsert(rows)
    print('surface_records upserted', file=sys.stderr)


if __name__ == '__main__':
    main()
