"""morning_audit — single command for your morning routine.

Runs consistency_watchdog + pipeline_smoke_test + prints the state of
every user-visible surface. Meant to run at ~8am ET before you open
the app for the day. If everything's green, you know the pipeline
worked overnight. If not, the output tells you WHAT to fix + HOW.

Prints one clean board. Exit 2 = something requires action, 1 = warnings,
0 = clean.

CLI:
  python morning_audit.py              # today
  python morning_audit.py --date YYYY-MM-DD
  python morning_audit.py --json       # machine-readable output
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
from pathlib import Path
import subprocess
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


def _run_script(path: Path, args: list = None) -> tuple[int, str]:
    """Run a script, return (exit_code, output). UTF-8 with replace to
    survive Windows cp1252 decode issues on emoji-heavy child output."""
    try:
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        r = subprocess.run(['python', str(path)] + (args or []),
                           capture_output=True, timeout=120,
                           cwd=str(path.parent), env=env)
        stdout = (r.stdout or b'').decode('utf-8', errors='replace')
        stderr = (r.stderr or b'').decode('utf-8', errors='replace')
        out = stdout + (('\n' + stderr) if stderr else '')
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, '(timed out)'
    except Exception as e:
        return -2, f'exception: {e}'


def _get_json(path: str, params: dict = None) -> object:
    """READ from Supabase."""
    r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=params or {}, timeout=15)
    if r.status_code != 200: return None
    try: return r.json()
    except Exception: return None


def _fmt_hit(w: int, l: int) -> str:
    n = w + l
    if n == 0: return '—'
    return f'{100*w/n:.1f}%'


def collect_surface_records(date: str) -> dict:
    """Return {sport: {surface: (wins, losses, pushes, units_net)}}."""
    rows = _get_json('surface_records',
                     {'window_key': 'eq.d30', 'select': 'sport,surface,wins,losses,pushes,units_net'})
    if not rows: return {}
    by_sport: dict = {}
    for row in rows:
        sp = row.get('sport', '?')
        s = row.get('surface', '?')
        by_sport.setdefault(sp, {})[s] = (
            row.get('wins', 0), row.get('losses', 0),
            row.get('pushes', 0), row.get('units_net', 0.0))
    return by_sport


def collect_today_headlines(date: str) -> dict:
    """Pull today's user-visible surface states in one go."""
    out = {}
    # POTD
    potd = _get_json('jerry_cache',
                     {'cache_key': f'eq.best_bet_{date}', 'select': 'data'})
    if potd:
        d = potd[0].get('data') or {}
        if isinstance(d, str):
            try: d = json.loads(d)
            except Exception: d = {}
        if d.get('noPlay'):
            out['POTD'] = 'noPlay (discipline pass)'
        elif d.get('noGames'):
            out['POTD'] = 'noGames'
        elif d.get('game'):
            out['POTD'] = d.get('leanDisplay', f"{d['game'].get('away_team','?')} @ {d['game'].get('home_team','?')}")
        else:
            out['POTD'] = 'empty payload'
    else:
        out['POTD'] = 'MISSING'
    # Sharp Card
    sc = _get_json('jerry_cache',
                   {'cache_key': f'eq.sharp_card_{date}', 'select': 'data,fetched_at'})
    if sc:
        blob = sc[0].get('data') or {}
        if isinstance(blob, str):
            try: blob = json.loads(blob)
            except Exception: blob = {}
        items = blob.get('items') or []
        out['Sharp Card'] = f'{len(items)} items (locked {sc[0]["fetched_at"][:19]})'
    else:
        out['Sharp Card'] = 'MISSING'
    # Dawg
    dawg = _get_json('daily_dawg',
                     {'game_date': f'eq.{date}',
                      'select': 'team,tier,conviction'})
    if dawg:
        d = dawg[0]
        out['Dawg'] = f"{d.get('team','?')} · {d.get('tier','?')} conv {d.get('conviction','?')}"
    else:
        out['Dawg'] = 'MISSING'
    # Daily Degen
    dd = _get_json('daily_degen',
                   {'game_date': f'eq.{date}', 'select': 'legs'})
    if dd:
        legs = dd[0].get('legs') or []
        out['Daily Degen'] = f'{len(legs) if isinstance(legs, list) else "?"} legs'
    else:
        out['Daily Degen'] = 'MISSING'
    # Slate size
    ctx = _get_json('mlb_game_context',
                    {'game_date': f'eq.{date}', 'select': 'count'})
    if ctx and isinstance(ctx, list):
        out['MLB Slate'] = f'{ctx[0].get("count", 0)} games'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    date = args.date or _et_today()
    here = Path(__file__).parent

    print(f'╔═══ MORNING AUDIT · {date} ═══╗\n')

    # Headlines — what the user will see today
    print('📱 TODAY (user surfaces):')
    for k, v in collect_today_headlines(date).items():
        icon = '✅' if 'MISSING' not in v else '🚨'
        print(f'  {icon} {k:14} {v}')

    # 30-day records — the receipts
    print('\n📊 30-DAY RECORDS:')
    records = collect_surface_records(date)
    for sport in ['ALL', 'MLB', 'NCAAF', 'NFL', 'NBA', 'NHL', 'NCAAB']:
        surfaces = records.get(sport)
        if not surfaces: continue
        print(f'  {sport}:')
        for s in sorted(surfaces):
            w, l, p, u = surfaces[s]
            print(f'    · {s:20} {w:4}-{l:4}-{p:3}  {u:+7.1f}u  hit {_fmt_hit(w,l)}')

    # Consistency watchdog
    print('\n🔍 CONSISTENCY WATCHDOG:')
    rc, out = _run_script(here / 'consistency_watchdog.py', ['--dry-run'])
    for line in (out or '').splitlines():
        s = line.strip()
        if s.startswith('✓') or s.startswith('🚨') or s.startswith('⚠️'):
            print(f'  {s}')
    if rc >= 2: print('  🚨 CRITICAL — see above')

    # Smoke test
    print('\n🩺 SMOKE TEST:')
    rc2, out2 = _run_script(here / 'pipeline_smoke_test.py')
    for line in (out2 or '').splitlines():
        s = line.strip()
        if s.startswith('✅') or s.startswith('🚨') or s.startswith('⚠️'):
            print(f'  {s}')
    if rc2 >= 2: print('  🚨 CRITICAL — see above')

    # Final status
    worst = max(rc, rc2)
    banner = ('🚨 ACTION REQUIRED' if worst >= 2
              else '⚠️ warnings present' if worst == 1
              else '✅ ALL GREEN')
    print(f'\n╚═══ {banner} ═══╝')
    sys.exit(worst)


if __name__ == '__main__':
    main()
