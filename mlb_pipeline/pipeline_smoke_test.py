"""pipeline_smoke_test — verify critical user-facing surfaces before launch.

Zero-risk additive check. READS only, never writes. Meant to run:
  1. Post-cron as a "did the pipeline actually produce what users see?"
  2. Pre-launch as a "green-check board" before hitting publish
  3. On-call — one command tells you if data is flowing to every surface

Categorized by launch criticality:
  🚨 CRITICAL — user-visible outage if this is empty/wrong
  ⚠  WARNING — degraded UX but app still works
  ℹ  INFO — nice-to-have data point

Exit codes: 2 = any CRITICAL fail, 1 = warnings only, 0 = clean.

CLI:
  python pipeline_smoke_test.py                    # today, all sports
  python pipeline_smoke_test.py --date 2026-09-08
  python pipeline_smoke_test.py --sport MLB        # single-sport
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


class Check:
    __slots__ = ('name', 'severity', 'status', 'message', 'detail')
    def __init__(self, name, severity, status, message, detail=None):
        self.name = name; self.severity = severity
        self.status = status; self.message = message; self.detail = detail or {}


def _get_count(table: str, filters: dict) -> int | None:
    params = {**filters, 'select': 'count'}
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=params, timeout=15)
    if r.status_code != 200: return None
    try:
        return r.json()[0]['count']
    except Exception:
        return None


def _get_first(table: str, filters: dict, select: str = '*') -> dict | None:
    params = {**filters, 'select': select, 'limit': '1'}
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=params, timeout=15)
    if r.status_code != 200: return None
    rows = r.json() if isinstance(r.json(), list) else []
    return rows[0] if rows else None


# ═══════════════════════════════════════════════════════════════════════
# CRITICAL CHECKS — user-visible outage if these fail
# ═══════════════════════════════════════════════════════════════════════

def check_potd_published_today(date: str) -> Check:
    row = _get_first('jerry_cache', {'cache_key': f'eq.best_bet_{date}'},
                     select='cache_key,data,narrative')
    if not row:
        return Check('potd_published', 'CRITICAL', False,
                     f'No best_bet_{date} in jerry_cache — home page will show "no play tonight"',
                     {'fix': 'python jerry_anchor_potd.py'})
    data = row.get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: data = {}
    if data.get('noGames'):
        return Check('potd_published', 'INFO', True,
                     f'POTD marked noGames (empty slate) — legit')
    if data.get('noPlay'):
        return Check('potd_published', 'INFO', True,
                     f'POTD marked noPlay (discipline pass) — legit')
    if not data.get('game'):
        return Check('potd_published', 'CRITICAL', False,
                     f'POTD row exists but data.game is empty — home page render will fail')
    game = data['game']
    return Check('potd_published', 'CRITICAL', True,
                 f'POTD: {game.get("away_team")} @ {game.get("home_team")} · {data.get("leanDisplay","?")}')


def check_sharp_card_published_today(date: str) -> Check:
    row = _get_first('jerry_cache', {'cache_key': f'eq.sharp_card_{date}'},
                     select='cache_key,data,fetched_at')
    if not row:
        return Check('sharp_card_published', 'CRITICAL', False,
                     f'No sharp_card_{date} in jerry_cache — Sharp Card tab will be empty',
                     {'fix': 'python generate_sharp_card.py'})
    data = row.get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: data = {}
    items = data.get('items') or []
    if len(items) == 0:
        return Check('sharp_card_published', 'CRITICAL', False,
                     f'sharp_card_{date} exists but items[] is empty')
    return Check('sharp_card_published', 'CRITICAL', True,
                 f'Sharp Card: {len(items)} items · fetched {(row.get("fetched_at") or "?")[:19]}')


def check_mlb_slate_loaded(date: str) -> Check:
    n = _get_count('mlb_game_context', {'game_date': f'eq.{date}'})
    if n is None or n == 0:
        return Check('mlb_slate_loaded', 'CRITICAL', False,
                     f'No MLB game_context rows for {date} — Games tab will be empty',
                     {'fix': 'python game_context.py --date ' + date})
    return Check('mlb_slate_loaded', 'CRITICAL', True, f'{n} MLB games loaded')


def check_mlb_jerry_reads_populated(date: str) -> Check:
    ctx_n = _get_count('mlb_game_context', {'game_date': f'eq.{date}'})
    if not ctx_n:
        return Check('mlb_jerry_reads', 'INFO', True, 'no MLB slate → nothing to check')
    reads_n = _get_count('jerry_reads', {'sport': 'eq.MLB', 'game_date': f'eq.{date}'})
    if reads_n is None or reads_n == 0:
        return Check('mlb_jerry_reads', 'CRITICAL', False,
                     f'MLB has {ctx_n} games but 0 jerry_reads — game detail will show "analysis pending"',
                     {'fix': 'python generate_jerry_synthesis.py --force'})
    pct = 100 * reads_n / ctx_n
    if pct < 80:
        return Check('mlb_jerry_reads', 'CRITICAL', False,
                     f'MLB jerry_reads coverage {reads_n}/{ctx_n} = {pct:.0f}% (<80%)')
    return Check('mlb_jerry_reads', 'CRITICAL', True,
                 f'jerry_reads: {reads_n}/{ctx_n} = {pct:.0f}%')


def check_mlb_props_generated(date: str) -> Check:
    ctx_n = _get_count('mlb_game_context', {'game_date': f'eq.{date}'})
    if not ctx_n:
        return Check('mlb_props', 'INFO', True, 'no MLB slate → nothing to check')
    props_n = _get_count('mlb_pipeline_props', {'game_date': f'eq.{date}'})
    if props_n is None or props_n == 0:
        return Check('mlb_props', 'CRITICAL', False,
                     f'MLB slate loaded but 0 props — Prop Jerry tab empty',
                     {'fix': 'python generate_props.py'})
    # Rough sanity: expect ~5-15 props per game
    per_game = props_n / ctx_n
    if per_game < 3:
        return Check('mlb_props', 'WARNING', True,
                     f'MLB props: {props_n} across {ctx_n} games (only {per_game:.1f}/game — thin)')
    return Check('mlb_props', 'CRITICAL', True,
                 f'{props_n} MLB props ({per_game:.1f}/game)')


# ═══════════════════════════════════════════════════════════════════════
# WARNING CHECKS — degraded UX but not broken
# ═══════════════════════════════════════════════════════════════════════

def check_prop_lr_shadow_coverage(date: str) -> Check:
    total = _get_count('mlb_pipeline_props', {'game_date': f'eq.{date}'})
    if not total:
        return Check('prop_lr_shadow', 'INFO', True, 'no props → skip')
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H,
                     params={'game_date': f'eq.{date}',
                             'select': 'signals', 'limit': '500'},
                     timeout=20)
    if r.status_code != 200:
        return Check('prop_lr_shadow', 'WARNING', False, f'fetch failed {r.status_code}')
    with_lr = 0
    for p in r.json() or []:
        sig = p.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except Exception: sig = {}
        if sig.get('_logreg_shadow'):
            with_lr += 1
    sample_n = len(r.json() or [])
    if sample_n == 0:
        return Check('prop_lr_shadow', 'INFO', True, 'no props sampled')
    pct = 100 * with_lr / sample_n
    if pct < 70:
        return Check('prop_lr_shadow', 'WARNING', False,
                     f'Prop LR shadow coverage {with_lr}/{sample_n} = {pct:.0f}% (<70%)',
                     {'fix': 'python mlb_prop_logreg_predict.py'})
    return Check('prop_lr_shadow', 'WARNING', True,
                 f'Prop LR shadow: {with_lr}/{sample_n} = {pct:.0f}%')


def check_prop_jerry_reads_coverage(date: str) -> Check:
    props_n = _get_count('mlb_pipeline_props', {'game_date': f'eq.{date}'})
    if not props_n:
        return Check('prop_jerry_reads', 'INFO', True, 'no props → skip')
    pjr_n = _get_count('prop_jerry_reads', {'sport': 'eq.MLB', 'game_date': f'eq.{date}'})
    if pjr_n is None or pjr_n == 0:
        return Check('prop_jerry_reads', 'WARNING', False,
                     f'{props_n} MLB props but 0 prop_jerry_reads — cards will render bullets, not graphs',
                     {'fix': 'python generate_prop_jerry_synthesis.py --sport MLB --force'})
    pct = 100 * pjr_n / props_n
    if pct < 50:
        return Check('prop_jerry_reads', 'WARNING', False,
                     f'prop_jerry_reads coverage {pjr_n}/{props_n} = {pct:.0f}%')
    return Check('prop_jerry_reads', 'WARNING', True,
                 f'prop_jerry_reads: {pjr_n}/{props_n} = {pct:.0f}%')


def check_dawg_of_day(date: str) -> Check:
    row = _get_first('daily_dawg', {'game_date': f'eq.{date}'}, select='team,game_id,odds')
    if not row:
        return Check('dawg_of_day', 'WARNING', False,
                     f'No daily_dawg for {date} — home page Dawg tile blank')
    return Check('dawg_of_day', 'WARNING', True,
                 f'Dawg: {row.get("team","?")} @ {row.get("odds","?")}')


def check_daily_degen(date: str) -> Check:
    row = _get_first('daily_degen', {'game_date': f'eq.{date}'}, select='legs')
    if not row:
        return Check('daily_degen', 'WARNING', False,
                     f'No daily_degen for {date} — home page Degen tile blank')
    legs = row.get('legs') or []
    return Check('daily_degen', 'WARNING', True,
                 f'Daily Degen: {len(legs) if isinstance(legs,list) else "?"} legs')


def check_watchdog_alerts_clean(date: str) -> Check:
    """Any unresolved critical alerts from other watchdogs?"""
    r = requests.get(f'{SB}/rest/v1/watchdog_alerts', headers=H,
                     params={'severity': 'eq.CRITICAL',
                             'resolved_at': 'is.null',
                             'run_date': f'eq.{date}',
                             'select': 'check_name,message', 'limit': '10'},
                     timeout=15)
    alerts = r.json() if r.status_code == 200 else []
    if not alerts:
        return Check('watchdogs_clean', 'WARNING', True, 'no unresolved CRITICAL alerts')
    names = ', '.join(a.get('check_name', '?') for a in alerts[:5])
    return Check('watchdogs_clean', 'WARNING', False,
                 f'{len(alerts)} unresolved CRITICAL alert(s): {names}',
                 {'alerts': alerts[:10]})


CHECKS = [
    check_potd_published_today,
    check_sharp_card_published_today,
    check_mlb_slate_loaded,
    check_mlb_jerry_reads_populated,
    check_mlb_props_generated,
    check_prop_lr_shadow_coverage,
    check_prop_jerry_reads_coverage,
    check_dawg_of_day,
    check_daily_degen,
    check_watchdog_alerts_clean,
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--sport', default='MLB')  # placeholder — checks are MLB-focused for now
    args = ap.parse_args()

    date = args.date or _et_today()
    print(f'=== pipeline_smoke_test · {date} · sport {args.sport} ===\n')

    critical_fail = 0
    warning_fail = 0
    passed = 0
    for fn in CHECKS:
        try:
            c = fn(date)
        except Exception as e:
            print(f'  ✗ {fn.__name__}: exception {type(e).__name__}: {e}')
            critical_fail += 1
            continue
        icon = '✅' if c.status else ('⚠️' if c.severity == 'WARNING' else '🚨')
        sev_tag = '' if c.status else f' [{c.severity}]'
        print(f'  {icon} {c.name:32}{sev_tag} {c.message}')
        if not c.status:
            fix = c.detail.get('fix')
            if fix:
                print(f'     → fix: {fix}')
            if c.severity == 'CRITICAL': critical_fail += 1
            else: warning_fail += 1
        else:
            passed += 1

    total = len(CHECKS)
    print(f'\n{"" if critical_fail == 0 else "🚨 "}'
          f'{passed}/{total} passed · {warning_fail} warnings · {critical_fail} critical')

    sys.exit(2 if critical_fail > 0 else (1 if warning_fail > 0 else 0))


if __name__ == '__main__':
    main()
