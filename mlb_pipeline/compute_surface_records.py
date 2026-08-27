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
    """Absolute date ranges for each window ending today (inclusive)."""
    mtd_start = today.replace(day=1)
    d7_start  = today - dt.timedelta(days=6)
    d30_start = today - dt.timedelta(days=29)
    epoch     = dt.date(2020, 1, 1)  # lifetime floor for non-sharp surfaces
    return {
        'mtd':      (mtd_start, today),
        'd7':       (d7_start,  today),
        'd30':      (d30_start, today),
        'lifetime': (epoch,     today),
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
    """jerry_reads — sides picks, cross-sport.

    jerry_reads doesn't store `tier` or a real book snapshot, so the Sharp
    Card falls back to flat 1u @ -110. We match that here — otherwise the
    numbers diverge again.
    """
    url = (f'{SB}/rest/v1/jerry_reads'
           f'?select=sport,game_date,result,conviction,call_odds_est'
           f'&result=not.is.null&order=game_date.desc')
    out = []
    for r in _paged(url):
        cls = _classify(r.get('result'))
        if cls is None: continue
        # Coverage stub gate — conviction=0 means un-scored sweep stub
        if r.get('conviction') == 0: continue
        try:
            d = dt.date.fromisoformat(r['game_date'])
        except Exception:
            continue
        if d < SHARP_RECORD_EPOCH: continue
        sp = (r.get('sport') or '').upper() or 'MLB'
        out.append({'sport': sp, 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': 0.909})
    return out


def pick_prop() -> list[dict]:
    """Props — PRIME + STRONG tiers only, matching Sharp Card filter.

    College props N/A per user (books don't carry them at scale).
    """
    out = []
    for tbl, sport in [('mlb_pipeline_props', 'MLB'), ('nfl_pipeline_props', 'NFL')]:
        url = (f'{SB}/rest/v1/{tbl}'
               f'?select=game_date,result,tier,conviction,book_line'
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
                stake = TIER_UNITS.get((r.get('tier') or '').upper(), 1.0)
                payout = _american_win_payout(r.get('book_line'))
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
    """ledger_suggestions — teasers/parlays, cross-sport."""
    url = (f'{SB}/rest/v1/ledger_suggestions'
           f'?select=sport_scope,game_date,result,combined_odds,rank'
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
        stake = 1.0   # ledger stakes vary; using unit for now
        payout = _american_win_payout(r.get('combined_odds'))
        out.append({'sport': sp, 'date': d, 'result': cls,
                    'stake': stake, 'payout': payout})
    return out


def pick_potd() -> list[dict]:
    """daily_best_bet_history — Play of the Day, cross-sport.

    No per-pick odds captured historically, so flat 1u @ -110 for now.
    v1.2 target: snapshot odds_american when POTD is written so ROI is
    real, not assumed.
    """
    url = (f'{SB}/rest/v1/daily_best_bet_history'
           f'?select=bet_date,result,sport'
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
        out.append({'sport': sp, 'date': d, 'result': cls,
                    'stake': 1.0, 'payout': 0.909})
    return out


SURFACES = {
    'sharp':  pick_sharp,
    'prop':   pick_prop,
    'ladder': pick_ladder,
    'ledger': pick_ledger,
    'potd':   pick_potd,
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
                    'sport': sport, 'surface': surface_name, 'window': wname,
                    **agg,
                    'last_computed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                })
    return out_rows


def upsert(rows: list[dict]):
    """Upsert into surface_records; PostgREST needs on-conflict spec."""
    if not rows: return
    # PostgREST resolves ON CONFLICT via the composite PK when we set the header
    r = requests.post(
        f'{SB}/rest/v1/surface_records?on_conflict=sport,surface,window',
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
            print(f'  {r["sport"]:6s} {r["surface"]:6s} {r["window"]:8s}  '
                  f'{r["wins"]}-{r["losses"]}-{r["pushes"]}  '
                  f'{r["units_net"]:+.2f}u  hit={r["hit_rate"]}', file=sys.stderr)
        return

    upsert(rows)
    print('surface_records upserted', file=sys.stderr)


if __name__ == '__main__':
    main()
