"""Locked-pick drift audit — 2026-09-12.

Andy directive: legitimacy safety net. Sharp Card + POTD hard-lock at
11am ET refuses republish, but backend pipeline (jerry_pick_scrub,
recompute_primary_play, tier calibration, LR overrides) can still
mutate jerry_reads / prop_jerry_reads after 11am. If that happens,
what users see on the app diverges from what the DB has "current".

Grader reads jerry_cache.sharp_card_{date}.data.items as source of
truth for the record, so Ledger integrity is preserved. But if the
DIVERGENCE is large or spans many picks, that's a signal something
is wrong upstream (e.g., a pipeline job re-firing when it shouldn't
be past lock time).

This script compares locked snapshots vs live state and reports:
  - Sharp Card items where live jerry_reads.call_* differs from
    the snapshot pick
  - Prop Jerry items where live prop_jerry_reads has changed since
    snapshot
  - POTD where live pick differs from jerry_cache.best_bet_{date}

Runs post-lock (default: after 12pm ET). Emits findings to console
+ appends to docs/daily/{date}.md if drifts found.

Non-mutating — READ-ONLY audit. Does NOT correct drift; that's a
separate decision (usually: leave locked snapshot alone since
that's what users saw; investigate WHY pipeline mutated for future
prevention).

USAGE:
    python audit_locked_pick_drift.py                # today
    python audit_locked_pick_drift.py --date 2026-09-13
    python audit_locked_pick_drift.py --quiet        # summary only
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
K = os.environ.get('SUPABASE_KEY')
H = {'apikey': K, 'Authorization': f'Bearer {K}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def load_sharp_card_snapshot(date: str) -> tuple[list[dict], str | None]:
    """Returns (items, fetched_at_iso) or ([], None) if snapshot missing."""
    r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H,
        params={'cache_key': f'eq.sharp_card_{date}',
                'select': 'data,fetched_at'}, timeout=15)
    if r.status_code != 200 or not r.json(): return [], None
    row = r.json()[0]
    data = row.get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: return [], None
    return (data.get('items') or []), row.get('fetched_at')


def audit_sharp_card_sides(items: list[dict], date: str) -> list[dict]:
    """For each snapshot ITEM that's a game side (ml/spread/rl/total),
    look up live jerry_reads and diff the pick fields."""
    drifts = []
    side_items = [it for it in items
                  if isinstance(it, dict)
                  and str(it.get('type','')).lower() in ('ml','rl','spread','total')]
    for it in side_items:
        sport = it.get('sport'); matchup = it.get('matchup','')
        snap_pick = it.get('pick') or it.get('label')
        snap_tier = it.get('tier')
        snap_type = str(it.get('type','')).lower()
        # matchup format: "Away @ Home"
        try:
            away, home = [s.strip() for s in matchup.split(' @ ')]
        except ValueError:
            continue
        # Find live jerry_reads by (sport, game_date, away, home)
        r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
            params={'sport': f'eq.{sport}', 'game_date': f'eq.{date}',
                    'away_team': f'eq.{away}', 'home_team': f'eq.{home}',
                    'select': 'call_market,call_side,call_line,call_text,conviction,generated_at'},
            timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list) or not rows: continue
        live = rows[0]
        live_pick = live.get('call_text') or ''
        live_type = str(live.get('call_market','')).lower()
        # Drift: pick text differs OR market flipped
        if snap_pick and live_pick and snap_pick.strip() != live_pick.strip():
            drifts.append({
                'kind': 'sharp_card_side',
                'sport': sport, 'matchup': matchup, 'tier': snap_tier,
                'snapshot_pick': snap_pick,
                'live_pick': live_pick,
                'snapshot_market': snap_type, 'live_market': live_type,
                'live_gen': (live.get('generated_at') or '')[:19],
            })
    return drifts


def audit_sharp_card_props(items: list[dict], date: str) -> list[dict]:
    """Same but for prop-type snapshot items — look up prop_jerry_reads."""
    drifts = []
    prop_items = [it for it in items
                  if isinstance(it, dict)
                  and str(it.get('type','')).lower() == 'prop']
    for it in prop_items:
        sport = it.get('sport'); source_key = it.get('source_key','')
        snap_pick = it.get('pick') or it.get('label')
        # source_key format: "Player Name|prop_type|prop_line"
        parts = source_key.split('|')
        if len(parts) < 3: continue
        player, prop_type, prop_line = parts[0], parts[1], parts[2]
        direction = 'over' if 'over' in prop_type else 'under'
        r = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H,
            params={'sport': f'eq.{sport}', 'game_date': f'eq.{date}',
                    'player_name': f'eq.{player}',
                    'prop_type': f'eq.{prop_type}', 'direction': f'eq.{direction}',
                    'select': 'call_verdict,conviction,short_read,generated_at,input_snapshot'},
            timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list) or not rows:
            drifts.append({
                'kind': 'sharp_card_prop_missing',
                'sport': sport, 'player': player, 'prop_type': prop_type,
                'snapshot_pick': snap_pick,
                'note': 'no live prop_jerry_reads row',
            })
            continue
        live = rows[0]
        live_verdict = live.get('call_verdict')
        snap_tier = str(it.get('tier','')).upper()
        # Verdict PRIME→PASS or STRONG→PASS between snap + live is drift
        if snap_tier in ('PRIME','STRONG') and str(live_verdict or '').upper() == 'PASS':
            drifts.append({
                'kind': 'sharp_card_prop_downgraded',
                'sport': sport, 'player': player, 'prop_type': prop_type,
                'snapshot_tier': snap_tier, 'live_verdict': live_verdict,
                'snapshot_pick': snap_pick,
                'live_gen': (live.get('generated_at') or '')[:19],
            })
    return drifts


def audit_potd(date: str) -> list[dict]:
    """POTD locked at jerry_cache.best_bet_{date} vs live top pick."""
    r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H,
        params={'cache_key': f'eq.best_bet_{date}',
                'select': 'data,fetched_at'}, timeout=10)
    if r.status_code != 200 or not r.json(): return []
    data = (r.json()[0]).get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: return []
    snap_gid = data.get('game_id')
    snap_pick = data.get('pick_text') or data.get('call_text') or data.get('label')
    if not snap_gid or not snap_pick: return []
    # Compare vs live jerry_reads for that game_id
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
        params={'game_id': f'eq.{snap_gid}', 'game_date': f'eq.{date}',
                'select': 'call_text,call_market,call_side,generated_at'}, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    if not isinstance(rows, list) or not rows: return []
    live_pick = rows[0].get('call_text') or ''
    if snap_pick.strip() != live_pick.strip():
        return [{
            'kind': 'potd',
            'snapshot_pick': snap_pick, 'live_pick': live_pick,
            'game_id': snap_gid,
            'live_gen': (rows[0].get('generated_at') or '')[:19],
        }]
    return []


def append_to_daily_log(date: str, drifts: list[dict]) -> None:
    """Append drift findings to docs/daily/{date}.md so morning audit
    can see any post-lock movement without hunting through logs."""
    log_path = Path(__file__).parent.parent / 'docs' / 'daily' / f'{date}.md'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        f'\n## Locked-pick drift audit — {now[:19]} UTC',
        f'',
        f'Post-lock scan found **{len(drifts)}** drifts between locked '
        f'jerry_cache snapshots and live jerry_reads/prop_jerry_reads.',
        f'',
    ]
    if drifts:
        lines.append('| Kind | Sport | Item | Snapshot | Live | Live gen |')
        lines.append('|---|---|---|---|---|---|')
        for d in drifts[:20]:
            item = d.get('matchup') or d.get('player') or d.get('game_id','?')
            snap = d.get('snapshot_pick') or d.get('snapshot_tier') or '?'
            live = d.get('live_pick') or d.get('live_verdict') or d.get('note','?')
            lines.append(
                f'| {d["kind"]} | {d.get("sport","")} | {item} | '
                f'{snap} | {live} | {d.get("live_gen","")} |'
            )
    else:
        lines.append('_All locked snapshots match live state. No pipeline mutation past lock._')
    lines.append('')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def run(date: str, quiet: bool = False, no_log: bool = False) -> int:
    print(f'=== locked-pick drift audit · {date} ===')
    items, fetched = load_sharp_card_snapshot(date)
    if not items:
        print(f'  no sharp_card_{date} snapshot — skip')
        return 0
    print(f'  snapshot fetched at {fetched}  ·  {len(items)} items')
    drifts = []
    drifts.extend(audit_sharp_card_sides(items, date))
    drifts.extend(audit_sharp_card_props(items, date))
    drifts.extend(audit_potd(date))
    if drifts:
        print(f'\n  ⚠ {len(drifts)} drift(s) found:')
        for d in drifts[:20]:
            item = d.get('matchup') or d.get('player') or d.get('game_id','?')
            snap = d.get('snapshot_pick') or d.get('snapshot_tier') or '?'
            live = d.get('live_pick') or d.get('live_verdict') or d.get('note','?')
            print(f'    [{d["kind"]:26s}] {d.get("sport",""):6s} {item:34s}  '
                  f'snap="{snap[:20]}"  live="{live[:20]}"')
    else:
        print('  ✓ all locked snapshots match live state')
    if not no_log:
        append_to_daily_log(date, drifts)
        print(f'  logged to docs/daily/{date}.md')
    return len(drifts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='ET date (default: today)')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--no-log', action='store_true',
                    help='Skip daily log append')
    args = ap.parse_args()
    n = run(args.date or _et_today(), quiet=args.quiet, no_log=args.no_log)
    sys.exit(0 if n == 0 else 0)  # never non-zero; audit-only


if __name__ == '__main__':
    main()
