"""Weekly sharp-money digest (2026-08-08).

Emits a Slack/console-ready weekly trend report showing:
  - Current bucket hit rates + week-over-week deltas
  - Phase 2A cap-active status
  - Recency kill-switch trips
  - P&L sim if we'd faded blindly at div ≥ 15/20/30
  - Weekly recommendation: strengthen cap, relax, or hold

Run weekly (Sunday) via GHA cron. Output stored to jerry_cache
under key `sharp_money_weekly_digest_YYYY-WW`.

Usage:
    python sharp_money_weekly_digest.py [--week YYYY-WW]
"""
from __future__ import annotations
import argparse, json, os, sys
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

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _load_snaps():
    r = requests.get(f'{SB}/rest/v1/mlb_game_context_snapshots', headers=H,
        params=[('oddscrowd_snapshot','not.is.null'),
                ('select','game_id,snapshot_date,oddscrowd_snapshot,close_total'),
                ('limit','10000')], timeout=30)
    snaps = r.json() if isinstance(r.json(), list) else []
    by_gid = {}
    for s in snaps:
        oc = s.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except Exception: oc = None
        if not isinstance(oc, dict): continue
        s['oddscrowd_snapshot'] = oc
        prev = by_gid.get(s['game_id'])
        if not prev or s['snapshot_date'] > prev['snapshot_date']:
            by_gid[s['game_id']] = s
    return list(by_gid.values())


def _load_results(gids):
    results = {}
    for i in range(0, len(gids), 100):
        chunk = gids[i:i+100]
        rr = requests.get(f'{SB}/rest/v1/mlb_game_results', headers=H,
            params=[('game_id',f'in.({",".join(chunk)})'),
                    ('select','game_id,home_score,away_score,total_result,game_date')],
            timeout=15)
        for row in (rr.json() if isinstance(rr.json(),list) else []):
            h, a = row.get('home_score'), row.get('away_score')
            if h is None or a is None: continue
            row['ml_winner'] = 'HOME' if h > a else ('AWAY' if a > h else 'PUSH')
            results[row['game_id']] = row
    return results


def _bucket(div: int) -> str:
    if div >= 30: return 'sharp_30+'
    if div >= 20: return 'sharp_20+'
    if div >= 10: return 'sharp_10+'
    if div <= -10: return 'public_10+'
    return 'aligned'


def _pnl_sim(records, mkt, div_threshold):
    n = wins = losses = 0; net = 0.0
    for r in records:
        pick = r.get(f'{mkt}_sharp_pick'); div = r.get(f'{mkt}_sharp_div')
        if div is None or div < div_threshold or pick is None: continue
        actual = r.get('ml_actual') if mkt == 'ml' else r.get('total_actual')
        if not actual or actual == 'PUSH': continue
        n += 1
        fade = 'HOME' if pick=='AWAY' else ('AWAY' if pick=='HOME' else
                ('UNDER' if pick=='OVER' else ('OVER' if pick=='UNDER' else None)))
        if fade == actual:
            wins += 1; net += 0.909
        else:
            losses += 1; net -= 1.0
    return {'n': n, 'wins': wins, 'losses': losses,
            'net_units': round(net, 2),
            'roi_pct': round((net / max(n, 1)) * 100, 1)}


def build_digest(week_label: str = None):
    print('=' * 70)
    print('SHARP MONEY WEEKLY DIGEST')
    print('=' * 70)
    snaps = _load_snaps()
    gids = [s['game_id'] for s in snaps]
    results = _load_results(gids)

    # Records
    records = []
    for s in snaps:
        res = results.get(s['game_id'])
        if not res: continue
        oc = s['oddscrowd_snapshot']
        r = {'game_id': s['game_id'], 'snap_date': s['snapshot_date'],
             'game_date': res.get('game_date'),
             'ml_actual': res.get('ml_winner'),
             'total_actual': (res.get('total_result') or '').upper()}
        for mkt in ('ml', 'total'):
            b = oc.get(mkt) or {}
            r[f'{mkt}_sharp_pick'] = b.get('pick')
            r[f'{mkt}_sharp_div'] = b.get('div')
        records.append(r)

    # Bucket splits
    def _stats(recs):
        out = {}
        for r in recs:
            for mkt in ('ml', 'total'):
                pick = r.get(f'{mkt}_sharp_pick'); div = r.get(f'{mkt}_sharp_div')
                if pick is None or div is None or div == -1: continue
                actual = r.get('ml_actual') if mkt == 'ml' else r.get('total_actual')
                if not actual or actual == 'PUSH': continue
                b = _bucket(div)
                key = (mkt, b)
                slot = out.setdefault(key, {'n': 0, 'sharp_hits': 0})
                slot['n'] += 1
                if pick == actual: slot['sharp_hits'] += 1
        return out

    # Split: this week vs prior 7 days
    now = datetime.now(timezone.utc).date()
    week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    two_weeks_ago = (now - timedelta(days=14)).strftime('%Y-%m-%d')
    this_week = [r for r in records if r.get('game_date') and r['game_date'] >= week_ago]
    prior_week = [r for r in records if r.get('game_date') and two_weeks_ago <= r['game_date'] < week_ago]
    all_time = records

    stats_now = _stats(this_week)
    stats_prior = _stats(prior_week)
    stats_all = _stats(all_time)

    print(f'\nRecords: {len(records)} lifetime · {len(this_week)} this week · {len(prior_week)} prior week\n')

    # Bucket table
    print(f'{"MARKET":8s} {"BUCKET":12s} {"WEEK":>8s}  {"PRIOR":>8s}  {"LIFE":>10s}  DELTA')
    print('-' * 68)
    for mkt in ('ml', 'total'):
        for b in ('sharp_30+', 'sharp_20+', 'sharp_10+', 'aligned', 'public_10+'):
            k = (mkt, b)
            n_w = stats_now.get(k, {}).get('n', 0)
            h_w = stats_now.get(k, {}).get('sharp_hits', 0)
            n_p = stats_prior.get(k, {}).get('n', 0)
            h_p = stats_prior.get(k, {}).get('sharp_hits', 0)
            n_a = stats_all.get(k, {}).get('n', 0)
            h_a = stats_all.get(k, {}).get('sharp_hits', 0)
            if n_a == 0: continue
            pct_w = round(100 * h_w / max(n_w, 1), 1) if n_w else None
            pct_p = round(100 * h_p / max(n_p, 1), 1) if n_p else None
            pct_a = round(100 * h_a / max(n_a, 1), 1)
            delta = f'{pct_w - pct_p:+.1f}pp' if (pct_w is not None and pct_p is not None) else '—'
            wcol = f'{h_w}/{n_w}={pct_w}%' if pct_w is not None else '—'
            pcol = f'{h_p}/{n_p}={pct_p}%' if pct_p is not None else '—'
            acol = f'{h_a}/{n_a}={pct_a}%'
            print(f'{mkt.upper():8s} {b:12s} {wcol:>8s}  {pcol:>8s}  {acol:>10s}  {delta}')

    # P&L week
    print(f'\n=== BLIND FADE P&L THIS WEEK (n={len(this_week)}) ===')
    for mkt in ('ml', 'total'):
        for t in (15, 20, 30):
            p = _pnl_sim(this_week, mkt, t)
            if p['n']:
                print(f'  {mkt.upper()} fade div>={t}:  {p["wins"]}-{p["losses"]}  {p["net_units"]:+.2f}u  ROI {p["roi_pct"]:+.1f}%')

    # ===== 2026-08-09: PER-RULE WEEK-OVER-WEEK TRENDS =====
    print(f'\n=== PER-RULE TRENDS (from sharp_fade_audit_trail) ===')
    # Pull audit rows for this week + prior week
    ar = requests.get(f'{SB}/rest/v1/sharp_fade_audit_trail', headers=H,
        params=[('game_date', f'gte.{two_weeks_ago}'),
                ('resolved_at', 'not.is.null'),
                ('select', 'game_date,rules_triggered,pick_won,cap_directive,cap_was_correct'),
                ('limit', '5000')], timeout=30)
    audit_rows = ar.json() if isinstance(ar.json(), list) else []
    if audit_rows:
        rule_week = {}   # rule -> {n_thisweek, hits_thisweek, n_prior, hits_prior}
        for row in audit_rows:
            pw = row.get('pick_won')
            if pw is None: continue
            triggers = row.get('rules_triggered') or []
            if isinstance(triggers, str):
                try: triggers = json.loads(triggers)
                except: triggers = []
            in_week = row.get('game_date','') >= week_ago
            for t in triggers:
                rn = t.get('rule')
                if not rn: continue
                slot = rule_week.setdefault(rn, {'n_w':0,'h_w':0,'n_p':0,'h_p':0})
                if in_week:
                    slot['n_w'] += 1
                    if pw: slot['h_w'] += 1
                else:
                    slot['n_p'] += 1
                    if pw: slot['h_p'] += 1
        print(f'  {"RULE":30s} {"THIS WEEK":>12s}  {"PRIOR":>10s}  {"DELTA":>7s}')
        print('  ' + '-'*65)
        for rn in sorted(rule_week.keys()):
            s = rule_week[rn]
            w = f'{s["h_w"]}/{s["n_w"]}={round(100*s["h_w"]/max(s["n_w"],1),1)}%' if s['n_w'] else '—'
            p = f'{s["h_p"]}/{s["n_p"]}={round(100*s["h_p"]/max(s["n_p"],1),1)}%' if s['n_p'] else '—'
            delta = ''
            if s['n_w'] and s['n_p']:
                delta = f'{round(100*s["h_w"]/s["n_w"] - 100*s["h_p"]/s["n_p"],1):+.1f}pp'
            print(f'  {rn:30s} {w:>12s}  {p:>10s}  {delta:>7s}')

        # Cap-accuracy check
        capped = [r for r in audit_rows if r.get('cap_directive')]
        if capped:
            correct = sum(1 for r in capped if r.get('cap_was_correct') is True)
            wrong = sum(1 for r in capped if r.get('cap_was_correct') is False)
            print(f'\n=== CAP ACCURACY (audit trail last 14d) ===')
            print(f'  Games capped: {len(capped)}')
            print(f'  Cap was correct (pick lost, cap saved us): {correct}')
            print(f'  Cap was wrong (pick won, cap cost us): {wrong}')
            if correct + wrong:
                print(f'  → Cap accuracy: {round(100*correct/(correct+wrong),1)}%')

    # Recommendation
    print('\n=== RECOMMENDATION ===')
    # Check cap-active buckets for reversal
    kill_switches = []
    for k, s in stats_now.items():
        mkt, bucket = k
        if bucket not in ('sharp_20+', 'sharp_30+'): continue
        if s['n'] < 5: continue
        pct = 100 * s['sharp_hits'] / s['n']
        if pct >= 55:
            kill_switches.append(f'{mkt.upper()}/{bucket} sharp {round(pct,1)}% this week (n={s["n"]}) — RECENCY KILL')
    if kill_switches:
        print('  ⚠️ REVIEW / CONSIDER DISABLING CAP:')
        for k in kill_switches:
            print(f'    - {k}')
    else:
        print('  ✓ HOLD: no cap-active bucket showing recency reversal')

    week_label = week_label or now.strftime('%Y-W%V')
    cache_key = f'sharp_money_weekly_digest_{week_label}'
    digest = {
        'week': week_label, 'computed_at': datetime.now(timezone.utc).isoformat(),
        'records_all_time': len(records), 'records_this_week': len(this_week),
        'buckets_this_week': {f'{m}/{b}': v for (m,b), v in stats_now.items()},
        'buckets_prior_week': {f'{m}/{b}': v for (m,b), v in stats_prior.items()},
        'buckets_all_time': {f'{m}/{b}': v for (m,b), v in stats_all.items()},
        'recency_kill_candidates': kill_switches,
    }
    # jerry_cache write
    requests.delete(f'{SB}/rest/v1/jerry_cache?cache_key=eq.{cache_key}&game_id=eq.GLOBAL',
                    headers=H_W, timeout=10)
    wr = requests.post(f'{SB}/rest/v1/jerry_cache',
                       headers=H_W,
                       data=json.dumps({'cache_key': cache_key, 'game_id': 'GLOBAL',
                                         'sport': 'MLB',
                                         'narrative': f'Weekly sharp digest {week_label}',
                                         'data': digest,
                                         'fetched_at': datetime.now(timezone.utc).isoformat()},
                                        default=str), timeout=15)
    if wr.status_code in (200, 201, 204):
        print(f'\n✓ digest stored under jerry_cache["{cache_key}"]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--week', default=None)
    args = ap.parse_args()
    build_digest(week_label=args.week)


if __name__ == '__main__':
    main()
