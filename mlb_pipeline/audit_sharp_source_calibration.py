"""Sharp-source rolling calibration audit (2026-08-20).

Per project_per_source_tracker_moat_818: rolling per-source (FR/CZ/OC) +
agreement-bucket hit rates are the moat. This script grades every graded
game against each source's sharp-side call, aggregates hit rates over
7d/30d/lifetime windows per (sport, source, market), and writes to
sharp_source_calibration + sharp_agreement_calibration.

Runs nightly after resolve_game_results.py so finals are graded when we
audit. App reads these tables in "The Split" tab to show:
  - Per-source rolling hit rate track record (FR 47%, CZ 52%, OC 44% 7d)
  - Agreement-bucket badge ("3/3 AGREE hits 45%" vs "DISSENT_OC hits 60%")

CLI:
  python audit_sharp_source_calibration.py                # all sports
  python audit_sharp_source_calibration.py --sport MLB
  python audit_sharp_source_calibration.py --dry-run
"""
from __future__ import annotations
import argparse, os, re, sys, json
from collections import defaultdict, Counter
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

BREAKEVEN = 52.4  # -110 juice

WINDOWS = [
    ('7d', 7),
    ('30d', 30),
    ('lifetime', 365),
]

SPORT_TABLES = {
    'MLB':   ('mlb_game_results',   'mlb_game_context'),
    'NFL':   ('nfl_game_results',   'nfl_game_context'),
    'NCAAF': ('ncaaf_game_results', 'ncaaf_game_context'),
    'NHL':   ('nhl_game_results',   'nhl_game_context'),
    'NBA':   ('nba_game_results',   'nba_game_context'),
}


def _fetch_results(sport: str, days: int) -> list[dict]:
    """Grab finalized games in the window."""
    tbl = SPORT_TABLES.get(sport, (None, None))[0]
    if not tbl:
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for off in range(0, 6000, 1000):
        r = requests.get(f'{SB}/rest/v1/{tbl}'
                         f'?game_date=gte.{cutoff}&home_score=not.is.null'
                         f'&select=game_id,home_score,away_score,close_spread,close_total'
                         f'&limit=1000&offset={off}',
                         headers=H_READ, timeout=25)
        b = r.json() if r.status_code == 200 else []
        if not isinstance(b, list) or not b:
            break
        out.extend(b)
        if len(b) < 1000:
            break
    return out


def _fetch_source_snaps(gids: list[str], table: str, source_filter: str | None = None) -> dict:
    """Latest snapshot per (game_id, market) from source table."""
    if not gids:
        return {}
    out = {}
    for i in range(0, len(gids), 200):
        chunk = gids[i:i + 200]
        ids = ','.join(f'"{g}"' for g in chunk)
        url = f'{SB}/rest/v1/{table}?game_id=in.({ids})&limit=1000'
        if source_filter:
            url += f'&source=eq.{source_filter}'
        # Sort key varies by table
        if table == 'line_snapshot':
            sort_col = 'snapshot_ts'
        elif table == 'external_picks':
            sort_col = 'pulled_at'
        else:
            sort_col = 'fetched_at'
        url += f'&order={sort_col}.desc'
        r = requests.get(url, headers=H_READ, timeout=25)
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            # external_picks uses 'surface' rather than 'market' — normalize.
            market_key = row.get('market') or row.get('surface')
            k = (row.get('game_id'), market_key)
            if k not in out:
                out[k] = row
    return out


def _oc_sharp_side(row: dict | None) -> Optional[str]:
    """OC sharp side = pick_side if money > bets, else inverse."""
    if not row: return None
    pick = (row.get('pick_side') or '').lower()
    m = row.get('money_pct'); b = row.get('bets_pct')
    if m is None or b is None or not pick: return None
    if float(m) > float(b): return pick
    if row['market'] == 'total':
        return 'under' if pick == 'over' else 'over'
    return 'away' if pick == 'home' else 'home'


def _fr_sharp_side(row: dict | None) -> Optional[str]:
    return (row.get('sharp_side_norm') or '').lower() if row else None


def _cz_sharp_side(row: dict | None) -> Optional[str]:
    return (row.get('sharp_side_norm') or '').lower() if row else None


def _so_sharp_side(row: dict | None) -> Optional[str]:
    """SO sharp side — external_picks stores money/bets pcts inside the
    confidence string ('money 89% / bets 87% (div +2pp)'). Parse them
    back out and apply the same money>bets-else-inverse rule as OC.
    Also normalizes surface → market so keys match the calibrator's
    per-source dict shape.
    """
    if not row: return None
    pick = (row.get('pick_side') or '').lower()
    conf = row.get('confidence') or ''
    m_ = re.search(r'money\s*(\d+)%\s*/\s*bets\s*(\d+)%', conf, re.IGNORECASE)
    if not m_ or not pick:
        return None
    money_pct = int(m_.group(1))
    bets_pct = int(m_.group(2))
    surface = (row.get('surface') or row.get('market') or '').lower()
    if money_pct > bets_pct:
        return pick
    if surface in ('total', 'totals'):
        return 'under' if pick == 'over' else 'over'
    return 'away' if pick == 'home' else 'home'


def _grade(market: str, side: Optional[str], r_: dict) -> Optional[str]:
    """Grade a pick against a finalized game. Returns W/L/P or None."""
    if not side or not r_: return None
    hs = r_.get('home_score'); as_ = r_.get('away_score')
    if hs is None or as_ is None: return None
    s = side.lower()
    if market == 'ml':
        actual = 'home' if hs > as_ else 'away' if as_ > hs else 'push'
        if actual == 'push': return 'P'
        return 'W' if s == actual else 'L'
    if market in ('rl', 'spread'):
        sp = r_.get('close_spread')
        if sp is None: return None
        cov = (hs - as_) + float(sp)
        if abs(cov) < 0.01: return 'P'
        if s == 'home': return 'W' if cov > 0 else 'L'
        return 'W' if cov < 0 else 'L'
    if market in ('total', 'totals', 'over', 'under'):
        tot = r_.get('close_total')
        if tot is None: return None
        s_pts = hs + as_
        if abs(s_pts - float(tot)) < 0.01: return 'P'
        actual = 'over' if s_pts > float(tot) else 'under'
        return 'W' if s == actual else 'L'
    return None


def _upsert(table: str, rows: list[dict], dry_run: bool) -> int:
    if dry_run or not rows:
        return len(rows)
    written = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        conflict = ('sport,source,market,window_label' if table == 'sharp_source_calibration'
                    else 'sport,bucket,market,window_label')
        pr = requests.post(f'{SB}/rest/v1/{table}?on_conflict={conflict}',
                           headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  x upsert {table} {i}: {pr.status_code} {pr.text[:150]}')
    return written


def run_sport(sport: str, dry_run: bool = False) -> None:
    print(f'\n=== audit_sharp_source_calibration · {sport} ===')

    # Widest window pulls all data; we'll bucket per window at aggregate time.
    max_days = max(d for _, d in WINDOWS)
    games = _fetch_results(sport, max_days)
    print(f'  games in {max_days}d: {len(games)}')
    if not games:
        return

    gid_to_result = {g['game_id']: g for g in games}
    gids = list(gid_to_result.keys())

    oc = _fetch_source_snaps(gids, 'line_snapshot', source_filter='oddscrowd')
    fr = _fetch_source_snaps(gids, 'fadereport_signals')
    cz = _fetch_source_snaps(gids, 'cleatz_signals')
    # 2026-08-25: SO lives in external_picks (surface col, not market).
    # Normalize the key shape to (game_id, market) so it slots into the
    # same per-source dict pattern as OC/FR/CZ.
    so_raw = _fetch_source_snaps(gids, 'external_picks', source_filter='scoresandodds')
    so = {}
    for (gid, surface), row in so_raw.items():
        # external_picks uses 'surface' — some rows may map through as market=None.
        market = (row.get('market') or row.get('surface') or surface or '').lower()
        if market in ('rl', 'spread'):
            market = 'rl'
        elif market in ('total', 'totals'):
            market = 'total'
        elif market in ('ml', 'h2h', 'moneyline'):
            market = 'ml'
        else:
            continue
        so[(gid, market)] = {**row, 'market': market}
    print(f'  OC snaps: {len(oc)} · FR snaps: {len(fr)} · CZ snaps: {len(cz)} · SO snaps: {len(so)}')

    # Build a per-(game, market, source) sharp-side call
    sport_lower = sport.lower()
    def _game_date(gid):
        # results table doesn't ship game_date in our select; MLB has it in ctx.
        # For simplicity we treat "which window" via lookup on the result row's
        # index in original games list — but simpler: re-fetch with dates? no.
        # Instead: use the last known result row date. We DID select game_id
        # only. Let's re-fetch dates for these games:
        return None

    # Re-fetch with dates so we can window
    date_map = {}
    tbl = SPORT_TABLES[sport][0]
    for i in range(0, len(gids), 200):
        chunk = gids[i:i + 200]
        ids = ','.join(f'"{g}"' for g in chunk)
        r = requests.get(f'{SB}/rest/v1/{tbl}?game_id=in.({ids})&select=game_id,game_date',
                         headers=H_READ, timeout=15)
        for row in (r.json() if r.status_code == 200 else []):
            if isinstance(row, dict):
                date_map[row['game_id']] = row.get('game_date')

    # Aggregate: for each (source, market, window), grade all applicable picks
    today = date.today()
    per_source = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})
    per_agreement = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})

    def _in_window(gd_str, days):
        if not gd_str: return False
        try:
            gd = date.fromisoformat(gd_str[:10])
        except Exception:
            return False
        return (today - gd).days <= days

    for gid, r_ in gid_to_result.items():
        gd_str = date_map.get(gid)
        for market in ('ml', 'rl', 'total'):
            oc_row = oc.get((gid, market))
            fr_row = fr.get((gid, market))
            cz_row = cz.get((gid, market))
            so_row = so.get((gid, market))
            oc_side = _oc_sharp_side(oc_row)
            fr_side = _fr_sharp_side(fr_row)
            cz_side = _cz_sharp_side(cz_row)
            so_side = _so_sharp_side(so_row)

            # Per-source grade
            for src, side in (('FR', fr_side), ('CZ', cz_side), ('OC', oc_side), ('SO', so_side)):
                if not side: continue
                out = _grade(market, side, r_)
                if not out: continue
                for wlabel, wdays in WINDOWS:
                    if _in_window(gd_str, wdays):
                        per_source[(src, market, wlabel)][out] += 1
                        per_source[(src, 'ALL', wlabel)][out] += 1

            # Agreement bucket grade — 4 sources now (was 3). Buckets:
            #   4_of_4_AGREE / 3_of_4_AGREE / 2_of_4_AGREE (split rooms)
            #   DISSENT_<source> when exactly 1 source out of 4 dissents from
            #                    the other 3 (still directional signal).
            sides = [s for s in (fr_side, cz_side, oc_side, so_side) if s]
            if len(sides) < 2: continue
            unique = set(sides)
            bucket = None; grade_target = None
            if len(unique) == 1:
                # All agreeing sources unanimous — bucket name reflects the count.
                bucket = {2: '2_of_2_AGREE', 3: '3_of_3_AGREE', 4: '4_of_4_AGREE'}.get(len(sides))
                grade_target = sides[0]
            elif len(sides) >= 3 and len(unique) == 2:
                c = Counter(sides)
                # Look for a clean N-1 majority (e.g. 3 of 4 agree, 1 dissents).
                counts = sorted(c.values(), reverse=True)
                if counts[0] == len(sides) - 1:
                    majority = [s for s, n in c.items() if n == counts[0]][0]
                    minority = [s for s, n in c.items() if n == 1][0]
                    # Identify which source is the dissenter.
                    odd_src = None
                    for src, side in (('FR', fr_side), ('CZ', cz_side),
                                      ('OC', oc_side), ('SO', so_side)):
                        if side == minority:
                            odd_src = src; break
                    if odd_src:
                        for b_name, side_used in ((f'MAJ_when_{odd_src}_dissents', majority),
                                                  (f'DISSENT_{odd_src}', minority)):
                            out = _grade(market, side_used, r_)
                            if not out: continue
                            for wlabel, wdays in WINDOWS:
                                if _in_window(gd_str, wdays):
                                    per_agreement[(b_name, market, wlabel)][out] += 1
                                    per_agreement[(b_name, 'ALL', wlabel)][out] += 1
                        continue  # skip the single-bucket grade below
            if bucket and grade_target:
                out = _grade(market, grade_target, r_)
                if not out: continue
                for wlabel, wdays in WINDOWS:
                    if _in_window(gd_str, wdays):
                        per_agreement[(bucket, market, wlabel)][out] += 1
                        per_agreement[(bucket, 'ALL', wlabel)][out] += 1

    # Build upsert rows
    now_iso = datetime.now(timezone.utc).isoformat()

    def _row(sport, src_or_bucket, market, wlabel, tally, is_bucket):
        n = tally['W'] + tally['L']
        hr = round(100 * tally['W'] / n, 2) if n else None
        edge = round(hr - BREAKEVEN, 2) if hr is not None else None
        base = {
            'sport': sport, 'market': market, 'window_label': wlabel,
            'wins': tally['W'], 'losses': tally['L'], 'pushes': tally['P'],
            'hit_rate': hr, 'sample_n': n, 'edge_pp': edge, 'computed_at': now_iso,
        }
        if is_bucket:
            base['bucket'] = src_or_bucket
        else:
            base['source'] = src_or_bucket
        return base

    src_rows = [_row(sport, s, m, w, t, False) for (s, m, w), t in per_source.items()]
    bkt_rows = [_row(sport, b, m, w, t, True) for (b, m, w), t in per_agreement.items()]

    print(f'  per-source rows: {len(src_rows)}  per-agreement rows: {len(bkt_rows)}')

    # Print summary
    print(f'\n  === SOURCE hit rates ({sport}, 30d) ===')
    for src in ('FR', 'CZ', 'OC'):
        t = per_source.get((src, 'ALL', '30d'), {'W': 0, 'L': 0, 'P': 0})
        n = t['W'] + t['L']
        hr = round(100 * t['W'] / n, 1) if n else 0
        edge = round(hr - BREAKEVEN, 1) if n else 0
        print(f'    {src}: {t["W"]}-{t["L"]}-{t["P"]}  n={n}  hit={hr}%  edge={edge:+.1f}pp')

    print(f'\n  === AGREEMENT bucket hit rates ({sport}, 30d) ===')
    for b in ('3_of_3_AGREE', 'DISSENT_OC', 'DISSENT_FR', 'DISSENT_CZ',
              'MAJ_when_OC_dissents', 'MAJ_when_FR_dissents', 'MAJ_when_CZ_dissents'):
        t = per_agreement.get((b, 'ALL', '30d'), {'W': 0, 'L': 0, 'P': 0})
        n = t['W'] + t['L']
        hr = round(100 * t['W'] / n, 1) if n else 0
        edge = round(hr - BREAKEVEN, 1) if n else 0
        print(f'    {b:<28} {t["W"]}-{t["L"]}-{t["P"]}  n={n}  hit={hr}%  edge={edge:+.1f}pp')

    w1 = _upsert('sharp_source_calibration', src_rows, dry_run)
    w2 = _upsert('sharp_agreement_calibration', bkt_rows, dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n  {prefix}source rows written: {w1}/{len(src_rows)}  agreement: {w2}/{len(bkt_rows)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=list(SPORT_TABLES.keys()) + ['ALL'], default='ALL')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sports = list(SPORT_TABLES.keys()) if args.sport == 'ALL' else [args.sport]
    for s in sports:
        try:
            run_sport(s, dry_run=args.dry_run)
        except Exception as e:
            print(f'  ✗ {s}: {type(e).__name__}: {e}')


if __name__ == '__main__':
    main()
