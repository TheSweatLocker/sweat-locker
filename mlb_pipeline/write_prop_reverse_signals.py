"""
write_prop_reverse_signals.py — Stamp prop_reverse_signal onto mlb_game_context.

Runs after generate_props.py in the cron. Reads all props for today, aggregates
per game via prop_reverse_signal.compute_slate_signals(), and stores the signal
struct on each game's mlb_game_context row.

App reads the signal off mlb_game_context.prop_reverse_signal (JSONB).
play_of_day's score_game can also read it as an additional dim driver.

CLI:
    python write_prop_reverse_signals.py            # today ET
    python write_prop_reverse_signals.py 2026-06-09  # specific date
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

import requests

from prop_reverse_signal import compute_slate_signals

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}


def today_et():
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')


def run(game_date=None):
    if not game_date:
        game_date = today_et()
    print(f"[prop_reverse] computing signals for {game_date}...")
    signals = compute_slate_signals(game_date, SUPABASE_URL, SUPABASE_KEY)
    print(f"  {len(signals)} matchups with signals")

    # Store as a single jerry_cache row keyed by date — same pattern cohort_signals
    # uses (no schema migration required). Consumers (cohort_signals.py-like
    # reader, play_of_day, Jerry reads, app) parse by matchup string.
    cache_key = f'prop_reverse_signals_{game_date}'
    payload = {
        'cache_key': cache_key,
        'game_id': cache_key,
        'sport': 'mlb',
        'narrative': '',
        'data': {
            'computed_at': datetime.now(timezone.utc).isoformat(),
            'game_date': game_date,
            'signals': signals,
        },
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key',
        headers={**HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload,
    )
    if r.status_code in (200, 201, 204):
        for matchup, signal in sorted(signals.items()):
            ts = signal['total_signal']
            ss = signal['side_signal']
            ts_dir = 'OVER' if ts > 0.1 else ('UNDER' if ts < -0.1 else '~')
            ss_dir = 'HOME' if ss > 0.1 else ('AWAY' if ss < -0.1 else '~')
            print(f"  ✓ {matchup[:40]:<42} {signal['confidence']:<8} tot={ts:+.2f}({ts_dir}) "
                  f"side={ss:+.2f}({ss_dir})  ({signal['evidence_count']} props)")
        print()
        print(f"[prop_reverse] wrote jerry_cache row '{cache_key}' with {len(signals)} matchups")
        return len(signals)
    else:
        print(f"  ✗ upsert failed {r.status_code}: {r.text[:300]}")
        return 0


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
