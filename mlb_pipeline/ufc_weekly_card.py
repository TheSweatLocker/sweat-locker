"""UFC weekly card payload — assemble the Saturday card as a structured
JSON blob for app rendering + social copy.

Reads ufc_picks for the target date, groups by tier (PRIME/STRONG/LEAN),
computes lock-of-card (highest EV PRIME), and outputs a JSON payload
ready to feed the app + a markdown-styled social version.

USAGE:
  python ufc_weekly_card.py                     # next Saturday
  python ufc_weekly_card.py --event-date 2026-08-01
  python ufc_weekly_card.py --format markdown   # print MD instead of JSON
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}


def decimal_to_american(dec: float) -> str:
    """Convert 1.5 decimal → +100 american, 2.4 → +140, 1.3 → -333."""
    if dec is None: return '—'
    if dec >= 2.0:
        return f'+{int(round((dec - 1) * 100))}'
    return f'{int(round(-100 / (dec - 1)))}'


def load_card(event_date: str) -> list:
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks',
        params={
            'event_date': f'eq.{event_date}',
            'select': '*',
            'order': 'fight_order.desc',
        },
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def build_payload(event_date: str) -> dict:
    picks = load_card(event_date)
    if not picks:
        return {'event_date': event_date, 'fights': [], 'note': 'no picks'}

    event_name = picks[0].get('event_name') or 'UFC Event'

    fights_out = []
    for p in picks:
        rec = p.get('ev_recommended_side')
        pick_fighter = None
        pick_odds = None
        if rec == 'a':
            pick_fighter = p['fighter_a']
            pick_odds = p.get('odds_a_median')
        elif rec == 'b':
            pick_fighter = p['fighter_b']
            pick_odds = p.get('odds_b_median')

        # Method insight — highest-prob method
        methods = {
            'KO/TKO': p.get('p_method_ko') or 0,
            'SUB':    p.get('p_method_sub') or 0,
            'DEC':    p.get('p_method_dec') or 0,
        }
        method_top = max(methods, key=methods.get)
        method_top_prob = methods[method_top]

        # Round insight — highest-prob finish round (excluding decision path)
        rounds = {i: p.get(f'p_round_{i}') or 0 for i in range(1, 6)}
        round_top = max(rounds, key=rounds.get)

        fights_out.append({
            'fight_order': p['fight_order'],
            'fighter_a': p['fighter_a'],
            'fighter_b': p['fighter_b'],
            'ev_tier': p.get('ev_tier'),
            'ev_recommended_side': rec,
            'pick_fighter': pick_fighter,
            'pick_odds_decimal': pick_odds,
            'pick_odds_american': decimal_to_american(pick_odds) if pick_odds else None,
            'ev_pct': p.get('ev_side_a') if rec == 'a' else (p.get('ev_side_b') if rec == 'b' else None),
            'model_p_winner_a': p.get('p_winner_a'),
            'conviction_winner': p.get('conviction_winner'),
            'method_top': method_top,
            'method_top_prob': round(method_top_prob, 3),
            'distance_prob': round(p.get('p_distance') or 0, 3),
            'round_top': round_top,
            'book_count': p.get('odds_book_count'),
            'p_method_ko': p.get('p_method_ko'),
            'p_method_sub': p.get('p_method_sub'),
            'p_method_dec': p.get('p_method_dec'),
        })

    # Sort by tier (PRIME → STRONG → LEAN → SKIP) then by EV desc
    tier_order = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2, 'SKIP': 3, None: 4}
    fights_out.sort(key=lambda f: (tier_order.get(f['ev_tier'], 5),
                                   -(f['ev_pct'] or -999)))

    plays = [f for f in fights_out if f['ev_tier'] in ('PRIME', 'STRONG', 'LEAN')]
    lock = plays[0] if plays else None

    return {
        'event_name': event_name,
        'event_date': event_date,
        'total_fights': len(picks),
        'plays_count': len(plays),
        'tier_counts': {
            'PRIME': sum(1 for f in fights_out if f['ev_tier'] == 'PRIME'),
            'STRONG': sum(1 for f in fights_out if f['ev_tier'] == 'STRONG'),
            'LEAN': sum(1 for f in fights_out if f['ev_tier'] == 'LEAN'),
            'SKIP': sum(1 for f in fights_out if f['ev_tier'] == 'SKIP'),
        },
        'lock_of_card': lock,
        'fights': fights_out,
    }


def format_markdown(payload: dict) -> str:
    lines = [f'# {payload["event_name"]}',
             f'*{payload["event_date"]} · {payload["total_fights"]} fights · {payload["plays_count"]} plays*',
             '']
    tc = payload.get('tier_counts', {})
    lines.append(f'**Tiers:** PRIME {tc.get("PRIME",0)} · STRONG {tc.get("STRONG",0)} · '
                 f'LEAN {tc.get("LEAN",0)} · SKIP {tc.get("SKIP",0)}')
    lines.append('')

    lock = payload.get('lock_of_card')
    if lock:
        lines.append('## 🔒 LOCK OF THE CARD')
        lines.append(f'**{lock["pick_fighter"]}** ML {lock["pick_odds_american"]} '
                     f'· EV +{lock["ev_pct"]:.1f}/$100 · {lock["ev_tier"]}')
        lines.append(f'({lock["fighter_a"]} vs {lock["fighter_b"]} · conv {lock["conviction_winner"]})')
        lines.append(f'Model method lean: **{lock["method_top"]}** '
                     f'({int(lock["method_top_prob"]*100)}%) · '
                     f'goes distance {int(lock["distance_prob"]*100)}%')
        lines.append('')

    lines.append('## All Plays')
    for f in payload['fights']:
        if f['ev_tier'] not in ('PRIME','STRONG','LEAN'):
            continue
        tier_emoji = {'PRIME':'🔒','STRONG':'⚡','LEAN':'📊'}.get(f['ev_tier'], '·')
        lines.append(f'- {tier_emoji} **{f["ev_tier"]}** — '
                     f'{f["pick_fighter"]} ML {f["pick_odds_american"]} '
                     f'(EV +{f["ev_pct"]:.1f}, conv {f["conviction_winner"]}) '
                     f'· method lean {f["method_top"]} {int(f["method_top_prob"]*100)}%')
    lines.append('')

    return '\n'.join(lines)


def run(event_date: str | None = None, fmt: str = 'json'):
    if event_date is None:
        today = datetime.now(timezone.utc).date()
        days_until_sat = (5 - today.weekday()) % 7
        event_date = (today + timedelta(days=days_until_sat)).isoformat()
    payload = build_payload(event_date)
    if fmt == 'markdown':
        print(format_markdown(payload))
    else:
        print(json.dumps(payload, indent=2, default=str))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--event-date', help='YYYY-MM-DD (defaults to next Saturday)')
    p.add_argument('--format', choices=['json', 'markdown'], default='markdown')
    args = p.parse_args()
    run(event_date=args.event_date, fmt=args.format)
