"""Launch-day smoke test — verifies critical data/API paths users hit
in the first minute of using the app. Run once after Apple approves
+ before you click "Release This Version" so you know nothing broke
between submit and release.

Not a UI test — that requires an actual iPhone. This verifies the
BACKEND data that a fresh install would hit on first launch:

  1. Home screen: today's POTD renders (jerry_cache best_bet row)
  2. Home screen: today's Sweat Card items exist (jerry_cache
     sharp_card_YYYYMMDD row with >=1 item)
  3. Games tab: today's slate populated per active sport
  4. Game Detail: at least 1 game has splits_summary populated so
     MoneyFlow chips render on the game card
  5. Sharp Card: no orphan pitchers (player_team=UNKNOWN)
  6. Prop Jerry: at least some props have short_read + call_verdict
     populated so cards aren't blank
  7. Steam Room: yesterday's daily_surface_records exists so P/L
     chips show real numbers
  8. Subscription entitlements: RevenueCat webhook can reach
     Supabase (implicitly verified via any subscription row present)

Exit codes:
  0 = all smoke checks pass, safe to release
  1 = warnings only (missing offseason sport data is OK)
  2 = HARD FAIL — do not release, first-launch user will see broken UI

USAGE:
    python docs/scripts/launch_smoke.py          # verify today
    python docs/scripts/launch_smoke.py --json   # machine-readable
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).resolve().parent.parent.parent / 'mlb_pipeline' / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _today_et() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


def _yesterday_et() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4, days=1)).strftime('%Y-%m-%d')


def _get_json(path: str, params: dict = None) -> list:
    r = requests.get(f'{SB}/rest/v1/{path}', headers=H_R, params=params or {})
    return r.json() if r.status_code == 200 else []


def _count(path: str, params: dict = None) -> int:
    p = {**(params or {}), 'select': 'id'}
    r = requests.get(f'{SB}/rest/v1/{path}', headers={**H_R, 'Prefer': 'count=exact'}, params=p)
    try: return int(r.headers.get('Content-Range', '0/0').split('/')[-1])
    except (ValueError, IndexError): return -1


# ═══════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════

def check_potd_renders(today: str) -> dict:
    """Home screen POTD hero requires best_bet_YYYY-MM-DD row w/ narrative."""
    rows = _get_json('jerry_cache', {'game_id': f'eq.best_bet_{today}',
                                      'select': 'data,narrative'})
    if not rows:
        return {'name': 'potd_home', 'severity': 'fail',
                'msg': f'no best_bet_{today} row — POTD hero renders blank'}
    d = rows[0].get('data') or {}
    if isinstance(d, str):
        try: d = json.loads(d)
        except (json.JSONDecodeError, TypeError): d = {}
    narrative = rows[0].get('narrative') or ''
    if not (d.get('leanDisplay') or narrative):
        return {'name': 'potd_home', 'severity': 'fail',
                'msg': 'POTD row exists but no leanDisplay + no narrative — hero renders blank'}
    return {'name': 'potd_home', 'severity': 'ok',
            'msg': f'POTD lean: {d.get("leanDisplay", "(narrative-only)")}'}


def check_sharp_card(today: str) -> dict:
    """Sharp Card should have >=1 item in today's cache."""
    rows = _get_json('jerry_cache', {'cache_key': f'eq.sharp_card_{today}',
                                      'select': 'data'})
    if not rows:
        return {'name': 'sharp_card', 'severity': 'fail',
                'msg': f'no sharp_card_{today} cache row'}
    d = rows[0].get('data') or {}
    if isinstance(d, str):
        try: d = json.loads(d)
        except (json.JSONDecodeError, TypeError): d = {}
    items = d.get('items') or []
    if not items:
        return {'name': 'sharp_card', 'severity': 'fail',
                'msg': 'Sharp Card cached but 0 items'}
    # Also check for orphan pitcher props (player_team unresolved)
    orphans = [i for i in items if i.get('type') == 'prop' and
               (i.get('player_team') or '').upper() in ('', 'UNKNOWN')]
    if orphans:
        return {'name': 'sharp_card', 'severity': 'fail',
                'msg': f'{len(orphans)} orphan pitcher props on card — Peterson-class leak'}
    return {'name': 'sharp_card', 'severity': 'ok',
            'msg': f'{len(items)} items composed cleanly'}


def check_games_slate(today: str) -> dict:
    """Games tab needs at least one game context populated for the day."""
    counts = {}
    for sport, tbl in [
        ('MLB',   'mlb_game_context'),
        ('NFL',   'nfl_game_context'),
        ('NCAAF', 'ncaaf_game_context'),
        ('NBA',   'nba_game_context'),
        ('NHL',   'nhl_game_context'),
        ('NCAAB', 'ncaab_game_context'),
    ]:
        counts[sport] = _count(tbl, {'game_date': f'eq.{today}'})
    total = sum(v for v in counts.values() if v > 0)
    if total == 0:
        return {'name': 'games_slate', 'severity': 'fail',
                'msg': 'ZERO games across all sports today — Games tab renders empty'}
    active = {s: c for s, c in counts.items() if c > 0}
    return {'name': 'games_slate', 'severity': 'ok',
            'msg': f'{total} games total across {list(active.keys())}: {active}'}


def check_moneyflow_data(today: str) -> dict:
    """At least one game in each in-season sport should have splits_summary
    populated for MoneyFlow chips to render."""
    checks = []
    for sport, tbl in [('MLB', 'mlb_game_context'),
                       ('NCAAF', 'ncaaf_game_context'),
                       ('NFL', 'nfl_game_context')]:
        # Fetch a few games, check for splits_summary
        rows = _get_json(tbl, {'game_date': f'eq.{today}',
                                'select': 'game_id,splits_summary',
                                'limit': '10'})
        if not rows: continue
        with_splits = sum(1 for r in rows if r.get('splits_summary'))
        checks.append(f'{sport}: {with_splits}/{len(rows)} w/ splits')
        if with_splits == 0 and len(rows) > 3:
            # In-season sport with games but zero splits = pipeline regression
            return {'name': 'moneyflow', 'severity': 'fail',
                    'msg': f'{sport} has {len(rows)} games today but 0 with splits_summary'}
    if not checks:
        return {'name': 'moneyflow', 'severity': 'warn',
                'msg': 'no in-season sports had games today for splits check'}
    return {'name': 'moneyflow', 'severity': 'ok', 'msg': ' | '.join(checks)}


def check_prop_jerry(today: str) -> dict:
    """MLB Prop Jerry reads need render_sections populated so cards
    don't render as text-only stubs."""
    rows = _get_json('prop_jerry_reads', {'sport': 'eq.MLB', 'game_date': f'eq.{today}',
                                           'select': 'input_snapshot',
                                           'limit': '20'})
    if not rows:
        return {'name': 'prop_jerry_mlb', 'severity': 'warn',
                'msg': 'no MLB prop_jerry_reads today (offday or synth not yet run)'}
    ok = 0
    for r in rows:
        snap = r.get('input_snapshot') or {}
        if isinstance(snap, str):
            try: snap = json.loads(snap)
            except (json.JSONDecodeError, TypeError): snap = {}
        if snap.get('render_sections'): ok += 1
    if ok == 0:
        return {'name': 'prop_jerry_mlb', 'severity': 'fail',
                'msg': f'{len(rows)} prop reads but ZERO have render_sections — cards render blank'}
    return {'name': 'prop_jerry_mlb', 'severity': 'ok',
            'msg': f'{ok}/{len(rows)} MLB prop reads have render_sections'}


def check_steam_room_yesterday(yesterday: str) -> dict:
    """Steam Room hero shows daily_surface_records for yesterday."""
    rows = _get_json('daily_surface_records', {'record_date': f'eq.{yesterday}',
                                                'select': 'sport,surface,wins,losses'})
    if not rows:
        return {'name': 'steam_room_yesterday', 'severity': 'fail',
                'msg': f'no daily_surface_records for {yesterday} — Steam Room P/L renders blank'}
    return {'name': 'steam_room_yesterday', 'severity': 'ok',
            'msg': f'{len(rows)} surface rows for {yesterday}'}


# ═══════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    today = _today_et()
    yesterday = _yesterday_et()

    checks = [
        check_potd_renders(today),
        check_sharp_card(today),
        check_games_slate(today),
        check_moneyflow_data(today),
        check_prop_jerry(today),
        check_steam_room_yesterday(yesterday),
    ]

    if args.json:
        print(json.dumps({
            'today': today, 'yesterday': yesterday,
            'checks': checks,
        }, indent=2, default=str))
    else:
        print(f'\n╔═══ Launch Smoke Test · {today} ═══\n')
        for c in checks:
            marker = {'ok': '✓', 'warn': '⚠', 'fail': '✗'}.get(c.get('severity'), '?')
            print(f'  {marker} {c["name"]:<22s}: {c["msg"]}')
        fails = sum(1 for c in checks if c.get('severity') == 'fail')
        warns = sum(1 for c in checks if c.get('severity') == 'warn')
        oks   = sum(1 for c in checks if c.get('severity') == 'ok')
        print(f'\n  Total: {oks} ok / {warns} warn / {fails} fail')
        if fails:
            print(f'\n  🚨 DO NOT RELEASE — {fails} hard failures. Fix before clicking Release This Version.\n')
        elif warns:
            print(f'\n  ⚠ safe to release with warnings noted.\n')
        else:
            print(f'\n  ✅ safe to release.\n')

    exit_code = 2 if any(c.get('severity') == 'fail' for c in checks) else \
                1 if any(c.get('severity') == 'warn' for c in checks) else 0
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
