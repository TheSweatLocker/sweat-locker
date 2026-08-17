"""Verify ensemble wiring — safety net for silent bugs (2026-08-16).

Runs a battery of checks that would have caught today's two silent bugs:
  * external_source_track_record aggregator writes zero rows
  * ensemble_scorer._load_track_records asks for a nonexistent column

Both were 0-exception situations where the ensemble kept "working" but
with degraded inputs. This script alerts loudly when the plumbing is
sick even if nothing crashes.

Checks (each returns OK / WARN / FAIL + count/detail):

  [SOURCES]   signal_sources rows loaded for target sport?
  [REGISTRY]  signal_registry populated (VALIDATED/DISCOVERY tiers >0)?
  [TRACKREC]  external_source_track_record has non-zero n_wins?
  [SCENARIO]  sharp_scenario_game_matches has recent rows?
  [FLAGS]     line_movement_flags has recent rows?
  [HEALTH]    ensemble_health has today's row?
  [WEIGHTS]   sample scorer run — % of signals loading with real weights?
  [PICKS]     sample scorer run — top-pick coverage on today's slate?

Exit codes: 0 = all OK/WARN, 1 = any FAIL.

CLI:
  python verify_ensemble_wiring.py --sport MLB
  python verify_ensemble_wiring.py --sport MLB --verbose
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, timedelta
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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

FAIL, WARN, OK = 'FAIL', 'WARN', 'OK'


def check(label: str, status: str, detail: str, verbose: bool = False):
    icon = {'OK': '✓', 'WARN': '⚠', 'FAIL': '✗'}[status]
    color = ''
    print(f'  {icon}  [{label:<10}] {status:<5} — {detail}')


def check_sources(sport: str, verbose: bool) -> str:
    r = requests.get(f'{SB}/rest/v1/signal_sources'
                     f'?sport=eq.{sport}&enabled=eq.true&select=class',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    n = len(rows)
    if n == 0:
        check('SOURCES', FAIL, f'0 signal_sources for {sport} — scorer will produce nothing', verbose)
        return FAIL
    classes = len({row.get('class') for row in rows})
    if classes < 3:
        check('SOURCES', WARN, f'{n} rows across only {classes} classes — thin coverage', verbose)
        return WARN
    check('SOURCES', OK, f'{n} rows across {classes} classes', verbose)
    return OK


def check_registry(sport: str, verbose: bool) -> str:
    r = requests.get(f'{SB}/rest/v1/signal_registry'
                     '?select=tier',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    validated = sum(1 for r_ in rows if r_.get('tier') == 'VALIDATED')
    discovery = sum(1 for r_ in rows if r_.get('tier') == 'DISCOVERY')
    anti = sum(1 for r_ in rows if r_.get('tier') == 'ANTI_VALIDATED')
    n = len(rows)
    if n == 0:
        check('REGISTRY', FAIL, '0 rows — no tier data, scorer falls to floor weights', verbose)
        return FAIL
    if validated + discovery == 0:
        check('REGISTRY', WARN, f'{n} rows but 0 VALIDATED/DISCOVERY — nothing has proven edge yet', verbose)
        return WARN
    check('REGISTRY', OK, f'{n} rows · VALIDATED={validated} DISCOVERY={discovery} ANTI={anti}', verbose)
    return OK


def check_trackrec(sport: str, verbose: bool) -> str:
    r = requests.get(f'{SB}/rest/v1/external_source_track_record'
                     f'?sport=eq.{sport}&window_days=eq.30'
                     '&n_wins=gt.0&select=source',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    n = len(rows)
    if n == 0:
        check('TRACKREC', FAIL, f'0 non-zero-win rows for {sport} 30d — external handicappers at floor weight', verbose)
        return FAIL
    check('TRACKREC', OK, f'{n} handicapper × surface rows have real records', verbose)
    return OK


def check_scenarios(sport: str, verbose: bool) -> str:
    today = date.today().isoformat()
    r = requests.get(f'{SB}/rest/v1/sharp_scenario_game_matches'
                     f'?game_date=eq.{today}&select=game_id',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    n = len(rows)
    if n == 0:
        check('SCENARIO', WARN, f'0 scenario matches for today ({today}) — quiet slate or scenario job hasn\'t run', verbose)
        return WARN
    check('SCENARIO', OK, f'{n} scenario matches on today\'s slate', verbose)
    return OK


def check_flags(sport: str, verbose: bool) -> str:
    cutoff = (date.today() - timedelta(days=2)).isoformat()
    r = requests.get(f'{SB}/rest/v1/line_movement_flags'
                     f'?last_seen_at=gte.{cutoff}&select=classification',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    n = len(rows)
    if n == 0:
        check('FLAGS', WARN, 'no line_movement_flags in last 2d — pipeline may be quiet or broken', verbose)
        return WARN
    confirmed = sum(1 for r_ in rows if '_CONFIRMED' in (r_.get('classification') or ''))
    check('FLAGS', OK, f'{n} flags recent · {confirmed} CONFIRMED-tier', verbose)
    return OK


def check_health(sport: str, verbose: bool) -> str:
    today = date.today().isoformat()
    r = requests.get(f'{SB}/rest/v1/ensemble_health'
                     f'?sport=eq.{sport}&computed_date=eq.{today}'
                     '&select=status_flag,cold_streak_days,hit_rate,roi_pct',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        check('HEALTH', WARN, f'no ensemble_health row for today ({today}) — monitor hasn\'t run', verbose)
        return WARN
    row = rows[0]
    status = row.get('status_flag')
    cold = row.get('cold_streak_days')
    hr = row.get('hit_rate')
    roi = row.get('roi_pct')
    if status == 'hard_suppress':
        check('HEALTH', FAIL, f'HARD_SUPPRESS · {cold}d cold streak · HR={hr}% ROI={roi}% — ensemble disabled', verbose)
        return FAIL
    if status == 'soft_tighten':
        check('HEALTH', WARN, f'SOFT_TIGHTEN · {cold}d cold · HR={hr}% ROI={roi}% — thresholds raised', verbose)
        return WARN
    check('HEALTH', OK, f'{status} · HR={hr}% ROI={roi}%', verbose)
    return OK


def check_weights(sport: str, verbose: bool) -> str:
    """Sample scorer run against one game — verify weights are non-zero on
    the VALIDATED/DISCOVERY tier of signals."""
    today = date.today().isoformat()
    table = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
             'NCAAF': 'ncaaf_game_context'}.get(sport, 'mlb_game_context')
    r = requests.get(f'{SB}/rest/v1/{table}?game_date=eq.{today}&limit=1&select=*',
                     headers=H, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        check('WEIGHTS', WARN, f'no games on {today} to test on', verbose)
        return WARN
    try:
        from ensemble_scorer import gather_opinions
        ops = gather_opinions(sport, rows[0])
    except Exception as e:
        check('WEIGHTS', FAIL, f'scorer crashed: {e}', verbose)
        return FAIL
    if not ops:
        check('WEIGHTS', WARN, 'scorer produced 0 opinions on sample game', verbose)
        return WARN
    with_real_hr = sum(1 for o in ops if o.hit_rate is not None)
    validated = sum(1 for o in ops if o.tier == 'VALIDATED')
    n = len(ops)
    if with_real_hr == 0:
        check('WEIGHTS', FAIL, f'{n} opinions but ZERO have real hit_rate — track record wiring broken', verbose)
        return FAIL
    if with_real_hr < n * 0.3:
        check('WEIGHTS', WARN, f'{n} opinions · only {with_real_hr} have real hit_rate ({100*with_real_hr//n}%)', verbose)
        return WARN
    check('WEIGHTS', OK, f'{n} opinions · {with_real_hr} with real hit_rate · {validated} VALIDATED', verbose)
    return OK


def check_picks(sport: str, verbose: bool) -> str:
    """Sample scorer run — coverage rate across today's slate."""
    today = date.today().isoformat()
    table = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
             'NCAAF': 'ncaaf_game_context'}.get(sport, 'mlb_game_context')
    r = requests.get(f'{SB}/rest/v1/{table}?game_date=eq.{today}&select=*&limit=30',
                     headers=H, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        check('PICKS', WARN, f'no games on {today}', verbose)
        return WARN
    try:
        from ensemble_scorer import score_game
        picks = 0
        for ctx in rows:
            d = score_game(sport, ctx)
            if d is not None and d.top().pick is not None: picks += 1
    except Exception as e:
        check('PICKS', FAIL, f'scorer crashed: {e}', verbose)
        return FAIL
    n = len(rows)
    pct = round(100 * picks / n, 0) if n else 0
    if pct < 30:
        check('PICKS', WARN, f'{picks}/{n} games got a pick ({pct}%) — coverage low, thresholds may be too tight', verbose)
        return WARN
    check('PICKS', OK, f'{picks}/{n} games got a pick ({pct}%)', verbose)
    return OK


def run(sport: str = 'MLB', verbose: bool = False) -> int:
    print(f'=== ensemble wiring verification · {sport} ===\n')
    statuses = []
    for fn in (check_sources, check_registry, check_trackrec, check_scenarios,
               check_flags, check_health, check_weights, check_picks):
        try: statuses.append(fn(sport, verbose))
        except Exception as e:
            print(f'  ✗  [{fn.__name__:<10}] FAIL — {e}')
            statuses.append(FAIL)

    n_fail = statuses.count(FAIL)
    n_warn = statuses.count(WARN)
    n_ok = statuses.count(OK)
    print(f'\n  summary: OK={n_ok}  WARN={n_warn}  FAIL={n_fail}')
    return 1 if n_fail else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()
    sys.exit(run(sport=args.sport, verbose=args.verbose))


if __name__ == '__main__':
    main()
