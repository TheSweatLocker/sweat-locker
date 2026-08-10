"""Nightly per-rule hit-rate computer (2026-08-09).

For each rule in sharp_fade_rules.ALL_RULES, replay it against every
historical snapshot × result → compute:
  - Lifetime n
  - Lifetime sharp hit % (fade % = 100 - hit %)
  - Recent-7d n
  - Recent-7d sharp hit %

Writes to jerry_cache['sharp_fade_rules_stats'] (GLOBAL row).
sharp_fade_rules._get_rule_mode() reads this to make cap-mode decisions.

Runs nightly after sharp_divergence_tracker.
"""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone
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
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=minimal'}

sys.path.insert(0, str(Path(__file__).parent))
from sharp_fade_rules import ALL_RULES


def load_snaps_and_results():
    r = requests.get(f'{SB}/rest/v1/mlb_game_context_snapshots', headers=H,
        params=[('oddscrowd_snapshot','not.is.null'),
                ('select','*'), ('limit','10000')], timeout=30)
    snaps = r.json() if isinstance(r.json(), list) else []
    by_gid = {}
    for s in snaps:
        oc = s.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except: oc = None
        if not isinstance(oc, dict): continue
        s['oddscrowd_snapshot'] = oc
        prev = by_gid.get(s['game_id'])
        if not prev or s['snapshot_date'] > prev['snapshot_date']:
            by_gid[s['game_id']] = s
    snaps = list(by_gid.values())
    gids = [s['game_id'] for s in snaps]
    results = {}
    for i in range(0, len(gids), 100):
        chunk = gids[i:i+100]
        rr = requests.get(f'{SB}/rest/v1/mlb_game_results', headers=H,
            params=[('game_id',f'in.({",".join(chunk)})'),
                    ('select','game_id,home_team,away_team,home_score,away_score,total_result')],
            timeout=15)
        for row in (rr.json() if isinstance(rr.json(),list) else []):
            h, a = row.get('home_score'), row.get('away_score')
            if h is None or a is None: continue
            row['ml_winner'] = 'HOME' if h > a else ('AWAY' if a > h else 'PUSH')
            results[row['game_id']] = row
    return snaps, results


def compute():
    snaps, results = load_snaps_and_results()
    print(f'  {len(snaps)} snaps · {len(results)} resolved games')

    # Recency cutoff = last 7 snapshot_dates
    dates = sorted({s['snapshot_date'] for s in snaps}, reverse=True)
    recent_cutoff = dates[6] if len(dates) > 6 else (dates[-1] if dates else '')

    # For each rule, replay against every (snap, market, sharp_pick)
    # The rule is "if we HAD taken sharp_pick as our pick, would rule flag it?"
    # Then check: did sharp win or fade win?
    rule_stats = {}  # rule_name -> {n, hits, recent_n, recent_hits}

    for s in snaps:
        res = results.get(s['game_id'])
        if not res: continue
        is_recent = s['snapshot_date'] >= recent_cutoff if recent_cutoff else False

        # Build ctx from snapshot row
        ctx = dict(s)
        ctx['home_team'] = res.get('home_team')
        ctx['away_team'] = res.get('away_team')

        oc = s['oddscrowd_snapshot']
        for mkt in ('ml', 'total'):
            b = oc.get(mkt) or {}
            sharp_pick = b.get('pick')
            div = b.get('div')
            if not sharp_pick or div is None or div == -1: continue
            actual = res.get('ml_winner') if mkt == 'ml' else (res.get('total_result','') or '').upper()
            if not actual or actual == 'PUSH': continue

            sharp_won = (sharp_pick == actual)

            # For each rule, does it fire when pick=sharp_pick?
            for rule_fn in ALL_RULES:
                try:
                    t = rule_fn(ctx, mkt, sharp_pick)
                except Exception:
                    continue
                if not t: continue
                rname = t['rule']
                slot = rule_stats.setdefault(rname, {'n': 0, 'hits': 0, 'recent_n': 0, 'recent_hits': 0})
                slot['n'] += 1
                if sharp_won: slot['hits'] += 1
                if is_recent:
                    slot['recent_n'] += 1
                    if sharp_won: slot['recent_hits'] += 1

    # Format for cache
    payload = {}
    print('\n=== PER-RULE STATS ===')
    print(f'{"RULE":32s} {"N":>4s}  {"LIFE%":>6s}  {"REC N":>5s}  {"REC%":>6s}')
    print('-' * 65)
    for rname in sorted(rule_stats.keys()):
        s = rule_stats[rname]
        n = s['n']; h = s['hits']
        rn = s['recent_n']; rh = s['recent_hits']
        life_pct = round(100 * h / max(n, 1), 1)
        recent_pct = round(100 * rh / max(rn, 1), 1) if rn else None
        payload[rname] = {'n': n, 'hits': h,
                          'lifetime_hit_pct': life_pct,
                          'recent_n': rn, 'recent_hits': rh,
                          'recent_hit_pct': recent_pct}
        rec_str = f'{recent_pct:5.1f}%' if recent_pct is not None else '   —'
        print(f'  {rname:30s} {n:>4d}  {life_pct:>5.1f}%  {rn:>5d}  {rec_str}')

    # Write to jerry_cache. jerry_cache uniqueness is on (game_id, sport)
    # not (game_id, sport, cache_key), so use a distinct game_id per key.
    GAME_ID = 'GLOBAL_RULES'
    requests.delete(f'{SB}/rest/v1/jerry_cache?cache_key=eq.sharp_fade_rules_stats&game_id=eq.{GAME_ID}',
                    headers=H_W, timeout=10)
    wr = requests.post(f'{SB}/rest/v1/jerry_cache', headers=H_W,
        data=json.dumps({'cache_key': 'sharp_fade_rules_stats',
                          'game_id': GAME_ID, 'sport': 'MLB',
                          'narrative': f'Per-rule sharp fade stats ({len(payload)} rules tracked)',
                          'data': payload,
                          'fetched_at': datetime.now(timezone.utc).isoformat()},
                         default=str), timeout=15)
    if wr.status_code in (200, 201, 204):
        print(f'\n✓ written to jerry_cache["sharp_fade_rules_stats"]')
    else:
        print(f'\n⚠ write failed {wr.status_code}: {wr.text[:200]}')


if __name__ == '__main__':
    compute()
