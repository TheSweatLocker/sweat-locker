"""archive_public_splits — permanent snapshot of OC + Fadereport splits
side-by-side per (sport, game_id, market, side, ts).

The pattern miner joins THIS archive against game results to score
"public agreed / disagreed / one-loud" hypotheses over long windows.
Fadereport 14d retention DOES NOT AFFECT the archive — we snapshot at
each pipeline run and the archive holds forever.

Sport-universal. Runs after write_line_snapshot.py + write_line_history.py.

CLI
  python archive_public_splits.py                    # all sports
  python archive_public_splits.py --sport MLB
  python archive_public_splits.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

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

SUPPORTED_SPORTS = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL', 'UFC']


def latest_oc(sport: str, since_hrs: int = 6) -> dict:
    """Return {(game_id, market, side): row} latest OC snapshot per key."""
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hrs)).isoformat().replace('+', '%2B')
    # 2026-08-28: paginate — server caps 3000 client hint at 1000. With
    # order=snapshot_ts.desc + first-seen-wins the LATEST rows are the
    # ones we want, but truncation at 1000 means unique-key coverage
    # falls off for late-in-slate games we haven't seen a snapshot for
    # in the truncated head. Loop until short chunk.
    idx = {}
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/line_snapshot'
            f'?sport=eq.{sport}&source=eq.oddscrowd&snapshot_ts=gte.{since}'
            f'&select=game_id,market,pick_side,money_pct,bets_pct,divergence,line,snapshot_ts'
            f'&order=snapshot_ts.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not isinstance(chunk, list): break
        for row in chunk:
            key = (row.get('game_id'), (row.get('market') or '').lower(),
                   (row.get('pick_side') or '').upper())
            if key not in idx: idx[key] = row
        if len(chunk) < 1000: break
    return idx


def latest_fr(sport: str, since_hrs: int = 6) -> dict:
    """Read fadereport_signals with the REAL schema (bug fix 2026-08-15).

    Actual FR columns: money_side_pct / money_other_pct / bets_side_pct /
    bets_other_pct / sharp_side_norm / snapshot_date / fetched_at.
    (Previous version read handle_pct/bettors_pct which don't exist —
    caused every game to show 'no FR data' and killed cross-source gate.)

    Returns index keyed on (game_id, market, sharp_side) with normalized
    handle_pct/bettors_pct pass-through so downstream archive schema
    stays consistent. Market 'spread' aliased to 'rl' for join.
    """
    since_date = (datetime.now(timezone.utc) - timedelta(hours=since_hrs)).date().isoformat()
    # 2026-08-28: paginate — server caps 3000 client hint at 1000, same
    # bug as latest_oc above.
    idx = {}
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/fadereport_signals'
            f'?sport=eq.{sport}&snapshot_date=gte.{since_date}'
            f'&select=game_id,market,sharp_side_norm,money_side_pct,bets_side_pct,fetched_at'
            f'&order=fetched_at.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=15)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not isinstance(chunk, list): break
        for row in chunk:
            mkt = (row.get('market') or '').lower()
            if mkt == 'spread': mkt = 'rl'
            side = (row.get('sharp_side_norm') or '').upper()
            key = (row.get('game_id'), mkt, side)
            if key not in idx:
                idx[key] = {
                    'handle_pct':  row.get('money_side_pct'),
                    'bettors_pct': row.get('bets_side_pct'),
                }
        if len(chunk) < 1000: break
    return idx


def latest_ftp(sport: str, since_hrs: int = 6) -> dict:
    """Fade The Public Analytics — third money-flow source (2026-09-21).

    Added because OddsCrowd moved its splits table to client-side rendering
    and stopped being scrapable, leaving only fadereport. The sharp-money
    rule wants 2+ contrarian sources before it will FADE, which one feed can
    never satisfy. See project_oddscrowd_client_render_921.

    Same shape as latest_fr, keyed on (game_id, market, sharp_side) with
    'spread' aliased to 'rl' for the join. Filters on snapshot_date, which
    for this source is the GAME's date rather than the pull date — the feed
    serves whole recent slates, so a Monday pull carries Sunday's games.
    """
    since_date = (datetime.now(timezone.utc) - timedelta(hours=since_hrs)).date().isoformat()
    idx = {}
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/fadethepublic_signals'
            f'?sport=eq.{sport}&snapshot_date=gte.{since_date}'
            f'&select=game_id,market,sharp_side_norm,money_side_pct,bets_side_pct,'
            f'current_line,fetched_at'
            f'&order=fetched_at.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=15)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not isinstance(chunk, list): break
        for row in chunk:
            mkt = (row.get('market') or '').lower()
            if mkt == 'spread': mkt = 'rl'
            side = (row.get('sharp_side_norm') or '').upper()
            key = (row.get('game_id'), mkt, side)
            if key not in idx:
                money = row.get('money_side_pct')
                bets = row.get('bets_side_pct')
                div = None
                if money is not None and bets is not None:
                    div = float(money) - float(bets)
                idx[key] = {'money_pct': money, 'bets_pct': bets,
                            'divergence': div, 'line': row.get('current_line')}
        if len(chunk) < 1000: break
    return idx


def latest_cz(sport: str, since_hrs: int = 6) -> dict:
    """cleatz — money-flow source that has been running since 2026-08-15.

    2026-09-21: this reader did not exist. cleatz_signals was being written
    daily and healthily (47 NFL / 119 NCAAF / 9 MLB that day) but
    build_rows only ever merged OC + FR, so not one cleatz row reached
    public_splits_archive. The scraper worked; the plumbing didn't — the
    same silent-gap class as the resolvers nothing ever called.

    That mattered more than it looked: with OddsCrowd dead, the archive was
    down to a single live source (FR) while a second sat right there unused,
    and the sharp-money rule needs 2+ sources before it will FADE.
    """
    since_date = (datetime.now(timezone.utc) - timedelta(hours=since_hrs)).date().isoformat()
    idx = {}
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/cleatz_signals'
            f'?sport=eq.{sport}&snapshot_date=gte.{since_date}'
            f'&select=game_id,market,sharp_side_norm,sharp_handle_pct,sharp_bets_pct,'
            f'divergence,fetched_at'
            f'&order=fetched_at.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=15)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not isinstance(chunk, list): break
        for row in chunk:
            mkt = (row.get('market') or '').lower()
            if mkt == 'spread': mkt = 'rl'
            side = (row.get('sharp_side_norm') or '').upper()
            key = (row.get('game_id'), mkt, side)
            if key not in idx:
                idx[key] = {'money_pct': row.get('sharp_handle_pct'),
                            'bets_pct': row.get('sharp_bets_pct'),
                            'divergence': row.get('divergence')}
        if len(chunk) < 1000: break
    return idx


def build_rows(sport: str, oc_idx: dict, fr_idx: dict, ftp_idx: dict | None = None,
               cz_idx: dict | None = None) -> list:
    """Zip every source on (game, market, side) → archive rows. A key with
    only one source still gets archived so we can measure source coverage."""
    ftp_idx = ftp_idx or {}
    cz_idx = cz_idx or {}
    now_iso = datetime.now(timezone.utc).isoformat()
    all_keys = (set(oc_idx.keys()) | set(fr_idx.keys())
                | set(ftp_idx.keys()) | set(cz_idx.keys()))
    rows = []
    for key in all_keys:
        gid, market, side = key
        if not gid or not market or not side: continue
        oc = oc_idx.get(key) or {}
        fr = fr_idx.get(key) or {}
        ftp = ftp_idx.get(key) or {}
        cz = cz_idx.get(key) or {}
        rows.append({
            'sport':          sport,
            'game_id':        gid,
            'market':         market,
            'pick_side':      side,
            'oc_money_pct':   oc.get('money_pct'),
            'oc_bets_pct':    oc.get('bets_pct'),
            'oc_divergence':  oc.get('divergence'),
            'fr_handle_pct':  fr.get('handle_pct'),
            'fr_bettors_pct': fr.get('bettors_pct'),
            'ftp_money_pct':  ftp.get('money_pct'),
            'ftp_bets_pct':   ftp.get('bets_pct'),
            'ftp_divergence': ftp.get('divergence'),
            'cz_money_pct':   cz.get('money_pct'),
            'cz_bets_pct':    cz.get('bets_pct'),
            'cz_divergence':  cz.get('divergence'),
            # OC is gone for now, so it can no longer be the only line source.
            'current_line':   oc.get('line') or ftp.get('line'),
            'captured_at':    now_iso,
        })
    return rows


def run_sport(sport: str, dry_run: bool = False) -> int:
    oc = latest_oc(sport)
    fr = latest_fr(sport)
    ftp = latest_ftp(sport)
    cz = latest_cz(sport)
    rows = build_rows(sport, oc, fr, ftp, cz)
    live = sum(1 for n in (len(oc), len(fr), len(ftp), len(cz)) if n)
    print(f'  {sport}: OC={len(oc)}  FR={len(fr)}  FTP={len(ftp)}  CZ={len(cz)}  '
          f'→ {len(rows)} archive rows · {live} live source(s)')
    if not rows or dry_run:
        if dry_run and rows:
            print(f'    [DRY] sample: {rows[0]}')
        return len(rows) if dry_run else 0
    written = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i+200]
        r = requests.post(
            f'{SB}/rest/v1/public_splits_archive'
            f'?on_conflict=sport,game_id,market,pick_side,captured_at',
            headers=H_WRITE, json=chunk, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {r.status_code} {r.text[:150]}')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=SUPPORTED_SPORTS + ['ALL'], default='ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sports = SUPPORTED_SPORTS if args.sport == 'ALL' else [args.sport]
    print(f'=== archive_public_splits · {"/".join(sports)} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    total = 0
    for s in sports:
        total += run_sport(s, dry_run=args.dry_run)
    print(f'\n  ✓ {total} archive rows written')


if __name__ == '__main__':
    main()
