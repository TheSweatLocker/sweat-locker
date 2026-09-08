"""audit_dark_signals — surface enabled signals that haven't fired in N days.

Mirrors the watchdogs.check_signal_source_dark logic but produces a
full actionable list (not just 10 examples) and can optionally disable
them (enabled=false in signal_sources).

Skip classes match the watchdog: external_pick / split / scenario /
prop_* are handler-driven or prop-surface-only — their absence from
primary_play._ensemble_sources is expected, not "dark."

Rare-conditional classes (weather / umpire / h2h / team_form_season)
use a 30d window since they only fire on specific game conditions.

CLI:
  python audit_dark_signals.py                    # preview list
  python audit_dark_signals.py --sport NCAAF      # filter to sport
  python audit_dark_signals.py --disable          # actually set enabled=false
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SPORT_TO_CTX = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context',
}

# Signal classes whose absence from primary_play._ensemble_sources is
# EXPECTED, not "dark". These route through separate handlers or prop
# surfaces. Same list as watchdogs.check_signal_source_dark.
SKIP_CLASSES = {'external_pick', 'split', 'scenario',
                'prop_trend', 'prop_form', 'prop_environment',
                'prop_matchup', 'prop_model'}
RARE_CONDITIONAL = {'weather', 'umpire', 'h2h', 'team_form_season'}


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


def find_dark(sport: str, window_days: int = 14) -> list[dict]:
    """Return enabled signals that haven't fired in window_days across
    any game (checked against sport's game_context table)."""
    ctx_table = SPORT_TO_CTX.get(sport.upper())
    if not ctx_table:
        return []
    since = _days_ago(window_days)
    since_rare = _days_ago(30)

    # Enabled signals for this sport (older than window)
    r = requests.get(
        f'{SB}/rest/v1/signal_sources',
        headers=H_READ,
        params={'sport': f'eq.{sport}', 'enabled': 'eq.true',
                'select': 'id,signal_key,class,created_at'},
        timeout=15,
    )
    sigs = r.json() if r.status_code == 200 else []
    if not isinstance(sigs, list):
        return []

    # Recent ensemble sources across all games in lookback window
    r = requests.get(
        f'{SB}/rest/v1/{ctx_table}',
        headers=H_READ,
        params={'game_date': f'gte.{since_rare}', 'select': 'primary_play,game_date'},
        timeout=30,
    )
    ctx = r.json() if r.status_code == 200 else []
    fired_14 = set(); fired_30 = set()
    for g in (ctx if isinstance(ctx, list) else []):
        pp = g.get('primary_play') or {}
        if isinstance(pp, str):
            try: import json as _j; pp = _j.loads(pp)
            except Exception: pp = {}
        gd = g.get('game_date') or ''
        for src in (pp.get('_ensemble_sources') or []):
            sk = src.get('signal_key')
            if sk:
                sk_clean = sk.replace('__fade', '')
                fired_30.add(sk_clean)
                if gd >= since:
                    fired_14.add(sk_clean)

    dark = []
    for s in sigs:
        cls = s.get('class')
        if cls in SKIP_CLASSES: continue
        # Fresh signals still ramping — skip
        created = (s.get('created_at') or '')[:10]
        cutoff = since_rare if cls in RARE_CONDITIONAL else since
        if created >= cutoff: continue
        # Check firings — rare classes use 30d, regular 14d
        fired_set = fired_30 if cls in RARE_CONDITIONAL else fired_14
        if s.get('signal_key') not in fired_set:
            dark.append(s)
    return dark


def disable(dark: list[dict]) -> int:
    """Set enabled=false on the passed signals."""
    disabled = 0
    for s in dark:
        r = requests.patch(
            f'{SB}/rest/v1/signal_sources?id=eq.{s["id"]}',
            headers=H_WRITE,
            json={'enabled': False},
            timeout=15,
        )
        if r.status_code in (200, 204):
            disabled += 1
            print(f'  ✓ disabled {s["signal_key"]} ({s["class"]})')
        else:
            print(f'  ✗ {s["signal_key"]}: {r.status_code} {r.text[:100]}')
    return disabled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--days', type=int, default=14,
                    help='Lookback window for regular classes (default 14)')
    ap.add_argument('--disable', action='store_true',
                    help='Actually set enabled=false. Without flag = preview only.')
    args = ap.parse_args()

    dark = find_dark(args.sport, window_days=args.days)
    print(f'== dark {args.sport} signals (no fires in {args.days}d) ==')
    print(f'  found: {len(dark)}')
    for s in dark:
        print(f'  · [{s.get("class"):20}] {s.get("signal_key")}')

    if args.disable:
        if not dark:
            print('  nothing to disable.')
            return 0
        print(f'\n== DISABLING {len(dark)} signals ==')
        n = disable(dark)
        print(f'\n✓ disabled {n}/{len(dark)}')
    else:
        print(f'\n  [PREVIEW] re-run with --disable to set enabled=false')
    return 0


if __name__ == '__main__':
    sys.exit(main())
