"""Daily ensemble health monitor — user-facing "how do I know" script.

For each of today's games, dumps: pick, tier, conviction, top-3 contributors
with per-chip % of total score. Flags any game where a single signal chip
is >20% of the score OR any class exceeds 40% of the score (the class-share
enforcement gate). If >2 such games in one day → CRITICAL.

Run daily post-recompute; reads primary_play._ensemble_sources JSON.

CLI:
    python ensemble_health_daily.py                 # today
    python ensemble_health_daily.py --date 2026-08-21
    python ensemble_health_daily.py --sport MLB     # scope
    python ensemble_health_daily.py --days 7        # rolling last 7 days summary
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX_TABLE = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
             'NCAAF': 'ncaaf_game_context', 'NCAAB': 'ncaab_game_context',
             'NBA': 'nba_game_context', 'NHL': 'nhl_game_context'}
SINGLE_CHIP_ALERT = 0.20   # any chip > 20% of score
CLASS_SHARE_ALERT = 0.45   # any class > 45% (buffer over 40% hard cap)


def audit_game(g: dict) -> dict:
    pp = g.get('primary_play') or {}
    sources = pp.get('_ensemble_sources') or []
    total = sum(c.get('contribution', 0) for c in sources if isinstance(c, dict))
    if total <= 0:
        return {'game_id': g.get('game_id'), 'flags': [], 'total': 0}

    per_class = defaultdict(float)
    per_chip = []
    for c in sources:
        if not isinstance(c, dict): continue
        contrib = c.get('contribution', 0)
        per_class[c.get('class', '?')] += contrib
        per_chip.append((c.get('signal_key', '?'), contrib, contrib / total))

    flags = []
    for k, contrib, share in per_chip:
        if share > SINGLE_CHIP_ALERT:
            flags.append(f'chip {k[:38]} = {share:.0%} ({contrib:.3f} of {total:.3f})')
    for cls, contrib in per_class.items():
        share = contrib / total
        if share > CLASS_SHARE_ALERT:
            flags.append(f'class {cls} = {share:.0%} ({contrib:.3f} of {total:.3f})')

    per_chip.sort(key=lambda x: -x[1])
    return {
        'game_id': g.get('game_id'),
        'matchup': f'{g.get("away_team","?")[:12]} @ {g.get("home_team","?")[:12]}',
        'pick': pp.get('label', '?'),
        'tier': pp.get('tier', '?'),
        'conviction': pp.get('conviction', 0),
        'total': total,
        'top3': per_chip[:3],
        'per_class': dict(per_class),
        'flags': flags,
    }


def audit_date(sport: str, date: str) -> list[dict]:
    tbl = CTX_TABLE.get(sport)
    if not tbl: return []
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers=H,
                     params={'game_date': f'eq.{date}',
                             'select': 'game_id,home_team,away_team,primary_play'},
                     timeout=30)
    if r.status_code != 200: return []
    games = r.json() if isinstance(r.json(), list) else []
    return [audit_game(g) for g in games if g.get('primary_play')]


def print_date(sport: str, date: str):
    audits = audit_date(sport, date)
    if not audits:
        print(f'  no games / no primary_play')
        return
    flagged = [a for a in audits if a['flags']]
    print(f'{sport} {date} — {len(audits)} games, {len(flagged)} flagged\n')
    for a in audits:
        marker = '🚨' if a['flags'] else '  '
        print(f'{marker} {a["matchup"]:32s} {a["tier"]:6s} conv={a["conviction"]:>3}  '
              f'{a["pick"][:32]:32s} score={a["total"]:.2f}')
        for k, c, s in a['top3']:
            print(f'    {k[:44]:44s} {c:.3f}  ({s:.0%} of score)')
        if a['flags']:
            for f in a['flags']:
                print(f'    🚨 {f}')
        print()

    status = 'CRITICAL' if len(flagged) >= 3 else 'WARNING' if len(flagged) >= 1 else 'HEALTHY'
    print(f'\nSTATUS: {status}  ({len(flagged)}/{len(audits)} flagged)')


def rolling_summary(sport: str, days: int):
    total_games = 0
    total_flagged = 0
    class_shares = defaultdict(list)
    for i in range(1, days + 1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
        audits = audit_date(sport, d)
        for a in audits:
            total_games += 1
            if a['flags']: total_flagged += 1
            if a['total'] > 0:
                for cls, contrib in a['per_class'].items():
                    class_shares[cls].append(contrib / a['total'])
    print(f'{sport} last {days}d — {total_games} games, {total_flagged} flagged ({100*total_flagged/max(1,total_games):.1f}%)\n')
    print(f'{"class":18s}  fires  avg %-of-score  max %-of-score')
    for cls in sorted(class_shares, key=lambda k: -sum(class_shares[k])/max(1, len(class_shares[k]))):
        shares = class_shares[cls]
        print(f'  {cls:18s}  {len(shares):4d}  {100*sum(shares)/len(shares):5.1f}%          '
              f'{100*max(shares):5.1f}%')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=list(CTX_TABLE.keys()))
    p.add_argument('--date')
    p.add_argument('--days', type=int, help='rolling summary N days')
    args = p.parse_args()
    if args.days:
        rolling_summary(args.sport, args.days)
    else:
        date = args.date or datetime.now(timezone.utc).date().isoformat()
        print_date(args.sport, date)


if __name__ == '__main__':
    main()
