"""Rule mode admin CLI (2026-08-14).

Session C helper. Human toggle for rule_registry.mode. Use for:
  * Emergency demote of a bad active rule ('rule is losing money now')
  * Manual promotion of a shadow rule you've backtested by hand
  * Retirement ('off') of rules superseded by better logic

Also has a list mode for quick eyeball of current registry state.

Usage:
    python promote_rule.py list
    python promote_rule.py show RULE_NAME
    python promote_rule.py set RULE_NAME MODE [--reason "text"]
        MODE = active | shadow | off

Examples:
    python promote_rule.py list
    python promote_rule.py set FORCE_FADE_TRAP off --reason "30d hit 12%"
    python promote_rule.py set NEW_TREND_RULE active --reason "backtest 62% n=45"
"""
from __future__ import annotations
import argparse, os, sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

from rule_registry import get_rule_row, set_rule_mode
import requests

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def cmd_list():
    r = requests.get(f'{SB}/rest/v1/rule_registry', headers=H_READ,
        params={'select': '*', 'order': 'mode.asc,rule_name.asc'}, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        print('  no rules registered'); return
    print(f'{"mode":8} {"class":22} {"rule_name":36} {"n":>5} {"hit%":>6} {"baseline":>8}')
    print('-' * 92)
    for row in rows:
        n = row.get('current_sample_n') or 0
        h = row.get('current_hit_rate')
        h_str = f'{h}%' if h is not None else '  —'
        base = float(row.get('baseline_hit_rate') or 50)
        lift = float(row.get('promotion_lift_pp') or 2)
        gate = f'{base+lift:.0f}%' if row['mode'] == 'shadow' else f'{base:.0f}%'
        print(f'{row["mode"]:8} {(row.get("rule_class") or "-"):22} {row["rule_name"]:36} '
              f'{n:>5} {h_str:>6} {gate:>8}')


def cmd_show(rule_name: str):
    row = get_rule_row(rule_name)
    if not row:
        print(f'  no rule: {rule_name}'); return
    import json
    print(json.dumps(row, indent=2, default=str))


def cmd_set(rule_name: str, mode: str, reason: str):
    row = get_rule_row(rule_name)
    if not row:
        print(f'  no rule: {rule_name}. Use register_rule() to create it first.')
        sys.exit(1)
    if row['mode'] == mode:
        print(f'  {rule_name} already at mode={mode}. No change.')
        return
    ok = set_rule_mode(rule_name, mode, reason=reason)
    if ok:
        print(f'  ✓ {rule_name}: {row["mode"]} → {mode}')
        if reason: print(f'    reason: {reason}')
    else:
        print(f'  ✗ update failed'); sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('rule_name')
    setp = sub.add_parser('set')
    setp.add_argument('rule_name')
    setp.add_argument('mode', choices=['active', 'shadow', 'off'])
    setp.add_argument('--reason', default='')
    args = p.parse_args()

    if args.cmd == 'list': cmd_list()
    elif args.cmd == 'show': cmd_show(args.rule_name)
    elif args.cmd == 'set': cmd_set(args.rule_name, args.mode, args.reason)


if __name__ == '__main__':
    main()
