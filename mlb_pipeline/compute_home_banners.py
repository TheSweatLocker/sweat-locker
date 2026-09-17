"""compute_home_banners — recompute auto Home tab hot-streak banners.

Runs post-pipeline (or on any manual invocation). Reads surface_records
and daily_surface_records, detects real hot streaks, UPSERTs a formatted
row into public.home_banners per class. Client (HomeStreakBanner
component) reads the table, sorts by priority, rotates through the top-N.

Andy 9/17 directive: "server side for all these notes and specific
notes for sports as needed." This script + the home_banners table are
what un-hardcodes the previously client-computed banners.

Classes emitted (highest → lowest priority):
  100 · Prime Props L{7|3}D per sport   — kind=prime_props_l7d_<SPORT>
   90 · Sharp last-3d hot per sport     — kind=sharp_3d_hot_<SPORT>
   80 · Sport 7d run per sport          — kind=sport_7d_run_<SPORT>
   70 · Daily Degen consecutive wins    — kind=daily_degen_streak
   60 · Ledger green streak (Nd)        — kind=ledger_green_streak

Each row expires in 24h — cron rerun replaces (ON CONFLICT kind,origin).
If a class no longer qualifies on the next run, its row lapses naturally
when expires_at passes.

Admin can INSERT rows with origin='admin' at any time — those don't
collide with cron rows (unique index is scoped to kind IS NOT NULL,
which admin rows can skip by leaving kind=NULL).

CLI:
  python compute_home_banners.py                # write live
  python compute_home_banners.py --dry-run      # print only, no writes
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB  = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

BANNER_TTL_HOURS = 24

SPORTS = ('MLB', 'NFL', 'NCAAF')


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires() -> str:
    return (_now() + timedelta(hours=BANNER_TTL_HOURS)).isoformat()


def _get_record(sport: str, surface: str, window: str) -> Optional[dict]:
    r = requests.get(
        f'{SB}/rest/v1/surface_records',
        headers=H_READ,
        params={'sport': f'eq.{sport}', 'surface': f'eq.{surface}',
                'window_key': f'eq.{window}', 'select': 'wins,losses,units_net,picks_count'},
        timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json()
    return rows[0] if isinstance(rows, list) and rows else None


def _upsert_banner(icon: str, message: str, deep_link: Optional[str],
                   sport: Optional[str], priority: int, kind: str,
                   dry_run: bool = False) -> bool:
    payload = {
        'icon': icon,
        'message': message,
        'deep_link': deep_link,
        'sport': sport,
        'route': 'home',
        'priority': priority,
        'starts_at': _now().isoformat(),
        'expires_at': _expires(),
        'origin': 'auto',
        'kind': kind,
        'dismissible': False,
    }
    label = f'[{priority}] {icon} {message}'
    if dry_run:
        print(f'  DRY  {kind:32} {label}')
        return True
    r = requests.post(
        f'{SB}/rest/v1/home_banners?on_conflict=kind,origin',
        headers=H_WRITE, json=[payload], timeout=15,
    )
    ok = r.status_code in (200, 201, 204)
    tag = 'OK ' if ok else f'FAIL {r.status_code}'
    print(f'  {tag} {kind:32} {label}')
    if not ok:
        print(f'      -> {r.text[:200]}')
    return ok


def _clear_stale(dry_run: bool = False) -> int:
    """Delete auto-origin banners whose kind is not in the current fired
    set. Otherwise a class that was hot yesterday but cold today would
    linger until its 24h TTL — expires_at handles that eventually, but
    active-row deletion keeps the table clean for humans reading it."""
    # No-op for now — TTL handles this. Placeholder for future cleanup.
    return 0


def compute(dry_run: bool = False) -> dict:
    fired: list[str] = []

    print('=== compute_home_banners ===')
    print()

    # 100 — PRIME props L7D per sport
    for sp in SPORTS:
        rec = _get_record(sp, 'prop_prime', 'd7')
        if not rec: continue
        w = rec.get('wins') or 0
        l = rec.get('losses') or 0
        total = w + l
        un = float(rec.get('units_net') or 0)
        if total < 30: continue
        pct = 100 * w / total
        if pct < 70 or un <= 0: continue
        msg = f'{sp} Prime Props L7D: {w}-{l} ({int(round(pct))}%), +{un:.1f}u'
        kind = f'prime_props_l7d_{sp}'
        if _upsert_banner('🎯', msg, 'jerry', None, 100, kind, dry_run):
            fired.append(kind)

    # 90 — Sharp Card last-3d hot per sport
    #   Sums daily_surface_records last 3 dates, uses units_won (net).
    cutoff = (_now() - timedelta(days=3)).date().isoformat()
    for sp in ('ALL', 'MLB', 'NFL', 'NCAAF'):
        r = requests.get(
            f'{SB}/rest/v1/daily_surface_records',
            headers=H_READ,
            params={'surface': 'eq.sharp_card', 'sport': f'eq.{sp}',
                    'record_date': f'gte.{cutoff}',
                    'select': 'wins,losses,units_won'},
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list) or not rows: continue
        w = sum(row.get('wins') or 0 for row in rows)
        l = sum(row.get('losses') or 0 for row in rows)
        pnl = sum(float(row.get('units_won') or 0) for row in rows)
        total = w + l
        if total < 5: continue
        pct = 100 * w / total
        if pct < 60 or pnl < 5: continue
        # ALL sport → sport=null (show universally). Per-sport → scoped.
        sp_label = 'The Sharp' if sp == 'ALL' else f'{sp} Sharp'
        msg = f'{sp_label} last 3d: {w}-{l} ({int(round(pct))}%), +{pnl:.1f}u'
        kind = f'sharp_3d_hot_{sp}'
        scope = None if sp == 'ALL' else sp
        if _upsert_banner('🔥', msg, 'sharp', scope, 90, kind, dry_run):
            fired.append(kind)

    # 80 — Sport 7d run (65%+ hit + n>=10 + net positive)
    for sp in SPORTS:
        rec = _get_record(sp, 'sharp_card', 'd7')
        if not rec: continue
        w = rec.get('wins') or 0
        l = rec.get('losses') or 0
        total = w + l
        un = float(rec.get('units_net') or 0)
        if total < 10: continue
        pct = 100 * w / total
        if pct < 65 or un <= 0: continue
        icon = {'MLB': '⚾', 'NFL': '🏈', 'NCAAF': '🎓'}.get(sp, '🎯')
        msg = f'{sp} 7d: {w}-{l} ({int(round(pct))}%), +{un:.1f}u'
        kind = f'sport_7d_run_{sp}'
        if _upsert_banner(icon, msg, 'sharp', sp, 80, kind, dry_run):
            fired.append(kind)

    # 70 — Daily Degen consecutive wins (2+ in a row)
    r = requests.get(
        f'{SB}/rest/v1/daily_surface_records',
        headers=H_READ,
        params={'surface': 'eq.daily_degen', 'order': 'record_date.desc',
                'select': 'record_date,wins,losses', 'limit': '10'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    streak = 0
    if isinstance(rows, list):
        for row in rows:
            if (row.get('wins') or 0) >= 1 and (row.get('losses') or 0) == 0:
                streak += 1
            else:
                break
    if streak >= 2:
        msg = f'Daily Degen {streak} in a row — going for {streak + 1} tonight'
        if _upsert_banner('🎯', msg, 'daily_degen', None, 70, 'daily_degen_streak', dry_run):
            fired.append('daily_degen_streak')

    # 60 — Ledger green streak
    r = requests.get(
        f'{SB}/rest/v1/daily_surface_records',
        headers=H_READ,
        params={'surface': 'in.(chalk_parlay,prime_teased_single,ledger)',
                'order': 'record_date.desc',
                'select': 'record_date,units_won', 'limit': '30'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    day_pnls: dict = {}
    for row in (rows if isinstance(rows, list) else []):
        d = row.get('record_date')
        pnl = float(row.get('units_won') or 0)
        day_pnls[d] = day_pnls.get(d, 0) + pnl
    green_streak = 0
    streak_pnl = 0.0
    for d in sorted(day_pnls.keys(), reverse=True):
        if day_pnls[d] > 0:
            green_streak += 1
            streak_pnl += day_pnls[d]
        else:
            break
    if green_streak >= 3:
        msg = f'Ledger {green_streak}-day green streak — +{streak_pnl:.1f}u'
        if _upsert_banner('📈', msg, 'ledger', None, 60, 'ledger_green_streak', dry_run):
            fired.append('ledger_green_streak')

    if not fired:
        print('  (nothing qualified — silent hide)')

    _clear_stale(dry_run=dry_run)
    return {'fired': len(fired), 'kinds': fired}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    result = compute(dry_run=args.dry_run)
    print()
    print(f'  {"[DRY] " if args.dry_run else ""}fired {result["fired"]} banner(s)')


if __name__ == '__main__':
    main()
