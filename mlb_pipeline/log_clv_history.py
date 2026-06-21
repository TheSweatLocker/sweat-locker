"""Daily CLV history logger — persists per-tier CLV to mlb_tier_calibration.

Background
----------
clv_audit.py (commit 3c6debc) computes CLV per (tier × pick_type × window)
on-demand. That gives a snapshot but no decay tracking. For "are we
maintaining sharpness over time?" we need nightly persistence.

This script reuses the clv_audit grading logic and writes per-tier rows
into mlb_tier_calibration. Tier-bucket naming follows the established
"<metric>_<dimension>_<tier>" pattern so the existing audit/dashboard
infra can read it alongside other calibration entries.

Two records per (pick_type × tier × window) pair:
  clv_<pick_type>_<tier>_pos      hit_rate = % of picks with positive CLV
  clv_<pick_type>_<tier>_winrate  hit_rate = % W/L (already-graded)

Mean CLV (in pts for spreads, in implied-prob for ML) is logged to console
each run so we can see the magnitude alongside the persisted rates. If we
ever want to dashboard mean CLV directly, add a `mean_value` column to
mlb_tier_calibration and start writing it.

Cron: run nightly at ~2am ET (after the day's results have all graded),
once per session, idempotent on (tier, window_label, computed_date).
"""

import io
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from dotenv import load_dotenv

# Reuse the grader from clv_audit so we never drift in CLV math.
import clv_audit

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {
    'apikey': SK,
    'Authorization': f'Bearer {SK}',
    'Content-Type': 'application/json',
}

WINDOWS = [('30d', 30), ('60d', 60), ('90d', 90)]
PICK_TYPES = ['total', 'side', 'ml']
# Tiers we care about tracking. Keep aligned with parse_tier in clv_audit.
TIERS = ['ELITE', 'PRIME', 'STRONG', 'LEAN']


def _post(rows):
    """Upsert via PostgREST. Resolves duplicates on (tier, window_label,
    computed_date) so re-running the logger same-day is idempotent."""
    if not rows:
        return
    headers = {**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    body = clv_audit.json.dumps(rows).encode('utf-8') if hasattr(clv_audit, 'json') else None
    if body is None:
        import json as _json
        body = _json.dumps(rows).encode('utf-8')
    url = f'{SU}/rest/v1/mlb_tier_calibration?on_conflict=tier,window_label,computed_date'
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def compute_window(games, pick_type, window_label):
    """Returns list of (tier, hits, total, mean_clv, pct_positive) tuples
    for each tier in TIERS that has graded picks in the window."""
    by_tier = defaultdict(list)
    for g in games:
        rec = clv_audit.grade_pick(g, pick_type)
        if rec is not None:
            by_tier[rec['tier']].append(rec)
    out = []
    for tier in TIERS:
        recs = by_tier.get(tier, [])
        if not recs:
            continue
        wins = sum(1 for r in recs if r['won'] is True)
        losses = sum(1 for r in recs if r['won'] is False)
        n_graded = wins + losses
        clvs = [r['clv'] for r in recs if r['clv'] is not None]
        if not clvs:
            continue
        mean_clv = sum(clvs) / len(clvs)
        pos = sum(1 for c in clvs if c > 0)
        out.append({
            'tier': tier,
            'n_picks': len(clvs),
            'wins': wins,
            'losses': losses,
            'n_graded': n_graded,
            'mean_clv': mean_clv,
            'pos_clv_n': pos,
            'pos_clv_pct': pos / len(clvs),
            'window_label': window_label,
        })
    return out


def main():
    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f'CLV history logger — {now_iso}')
    print('=' * 70)
    upsert_rows = []
    for window_label, window_days in WINDOWS:
        games = clv_audit.pull_window(days=window_days)
        print(f'\n[{window_label}] joined games: {len(games)}')
        for pick_type in PICK_TYPES:
            tiers_out = compute_window(games, pick_type, window_label)
            for t in tiers_out:
                # Persist two rows per tier: a CLV-positivity rate and a win
                # rate. Both flow into mlb_tier_calibration so the daily
                # dashboard surfaces them next to the other calibration keys.
                tier_pos = f'clv_{pick_type}_{t["tier"]}_pos'
                tier_win = f'clv_{pick_type}_{t["tier"]}_winrate'
                # CLV positivity row
                upsert_rows.append({
                    'tier': tier_pos,
                    'window_label': window_label,
                    'computed_date': today,
                    'hits': t['pos_clv_n'],
                    'total': t['n_picks'],
                    'hit_rate': round(t['pos_clv_pct'], 4),
                    'sport': 'mlb',
                    'updated_at': now_iso,
                })
                # Win rate row
                if t['n_graded'] > 0:
                    upsert_rows.append({
                        'tier': tier_win,
                        'window_label': window_label,
                        'computed_date': today,
                        'hits': t['wins'],
                        'total': t['n_graded'],
                        'hit_rate': round(t['wins'] / t['n_graded'], 4),
                        'sport': 'mlb',
                        'updated_at': now_iso,
                    })
                # Console line — magnitude of mean CLV is the actually-loud
                # number, but we don't have a column for it yet. Logged so
                # nightly cron output stays informative.
                unit = 'pts' if pick_type in ('total', 'side') else 'prob'
                print(f'  {pick_type:>5s} {t["tier"]:>7s} n={t["n_picks"]:>3d}  '
                      f'+CLV {100*t["pos_clv_pct"]:>4.0f}%  '
                      f'mean {t["mean_clv"]:+.3f} {unit:<4s}  '
                      f'W/L {t["wins"]}-{t["losses"]} ({100*t["wins"]/max(1,t["n_graded"]):.0f}%)')

    print(f'\nWriting {len(upsert_rows)} calibration rows...')
    try:
        _post(upsert_rows)
        print('✅ CLV calibration upsert OK')
    except Exception as e:
        print(f'⚠️  upsert failed (non-fatal): {e}')


if __name__ == '__main__':
    main()
