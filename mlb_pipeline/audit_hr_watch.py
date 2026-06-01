"""HR Watch audit — resolves past predictions against actual game results.

User direction 6/1: "Yes ship audit. Want to improve it and keep track on
how it's doing and predicting guys going yard."

Runs daily after games finish. For each mlb_hr_watch row from a past date
with resolved_at NULL, queries MLB Stats API for that batter's stats on
that date, sets hr_hit = (HRs >= 1) and resolved_at = now.

After resolution, prints a tier-band hit rate summary so we can see
whether the score is actually predictive.

Cohort report goes to console output (workflow log) AND a small upsert
into mlb_tier_calibration so the existing tier-integrity infra can roll
it into the daily audit dashboard.
"""
import os
import sys
import io
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import urllib.request
import json

from dotenv import load_dotenv

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
}


def _get(url):
    req = urllib.request.Request(url, headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _patch(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={**HEADERS, 'Prefer': 'return=minimal'}, method='PATCH',
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def _strip_accents(s):
    if not s:
        return s
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def fetch_batter_hr_on_date(player_name, game_date_str):
    """Returns (hr_count, pa_count) for this batter on this date, or (None, None)
    if we cannot resolve. Uses MLB Stats API gameLog filtered by date.

    Resolution can fail for several legitimate reasons (player wasnt in the
    lineup, name resolution missed, MLB API hiccup). Those rows stay
    unresolved and the next audit pass retries.
    """
    if not player_name:
        return None, None
    try:
        search = urllib.request.urlopen(
            f'https://statsapi.mlb.com/api/v1/people/search'
            f'?names={urllib.parse.quote(_strip_accents(player_name))}'
            f'&sportId=1&active=true',
            timeout=10,
        ).read()
        people = json.loads(search).get('people', [])
        if not people:
            return None, None
        pid = people[0]['id']
        season = game_date_str.split('-')[0]
        log = urllib.request.urlopen(
            f'https://statsapi.mlb.com/api/v1/people/{pid}/stats'
            f'?stats=gameLog&group=hitting&season={season}&sportId=1',
            timeout=10,
        ).read()
        splits = json.loads(log).get('stats', [{}])[0].get('splits', [])
        for sp in splits:
            if sp.get('date') == game_date_str:
                stat = sp.get('stat', {})
                hr = int(stat.get('homeRuns', 0) or 0)
                pa = int(stat.get('plateAppearances', 0) or 0)
                return hr, pa
        # Date not in game log → batter didn't play (rest day, scratched, IL)
        return 0, 0
    except Exception as e:
        print(f'  ⚠️  fetch_batter_hr_on_date failed for {player_name} on {game_date_str}: {e}')
        return None, None


def resolve_unfinished(lookback_days=14):
    """Walks unresolved HR Watch rows from the last `lookback_days` and tries
    to resolve them. Returns (n_resolved, n_unresolved_remaining)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    earliest = cutoff - timedelta(days=lookback_days)
    today_str = cutoff.strftime('%Y-%m-%d')
    earliest_str = earliest.strftime('%Y-%m-%d')

    qs = urlencode({
        'game_date': f'gte.{earliest_str}',
        'resolved_at': 'is.null',
        'select': 'id,game_date,player_name,team,score,projected_hr_prob',
        'order': 'game_date.asc',
        'limit': '500',
    })
    rows = _get(f'{SUPABASE_URL}/rest/v1/mlb_hr_watch?{qs}')
    # Skip today's games (not finished yet)
    rows = [r for r in rows if r.get('game_date') and r['game_date'] < today_str]
    print(f'Unresolved HR Watch rows from {earliest_str} to yesterday: {len(rows)}')

    resolved = 0
    failed = 0
    for r in rows:
        hr, pa = fetch_batter_hr_on_date(r.get('player_name'), r.get('game_date'))
        if hr is None:
            failed += 1
            time.sleep(0.15)
            continue
        payload = {
            'hr_hit': hr >= 1,
            'actual_pa': pa,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            _patch(f'{SUPABASE_URL}/rest/v1/mlb_hr_watch?id=eq.{r["id"]}', payload)
            resolved += 1
        except Exception as e:
            print(f'  ⚠️  PATCH failed for {r["player_name"]} ({r["game_date"]}): {e}')
            failed += 1
        time.sleep(0.12)  # be polite to MLB API
    print(f'Resolved: {resolved} | failed/skipped: {failed}')
    return resolved, failed


def print_cohort_report(lookback_days=30):
    """Hit rate by score band over the last N days. The core "are we right"
    question — does score 60+ actually correlate with going yard more often
    than score 30-40?"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    earliest = cutoff - timedelta(days=lookback_days)
    qs = urlencode({
        'game_date': f'gte.{earliest.strftime("%Y-%m-%d")}',
        'resolved_at': 'not.is.null',
        'select': 'score,hr_hit,projected_hr_prob,player_name,game_date',
        'order': 'score.desc',
    })
    rows = _get(f'{SUPABASE_URL}/rest/v1/mlb_hr_watch?{qs}')
    if not rows:
        print('No resolved HR Watch rows in lookback window — audit will populate over coming days.')
        return

    bands = [(0, 29, '<30'), (30, 39, '30-39'), (40, 49, '40-49'),
             (50, 59, '50-59'), (60, 69, '60-69'), (70, 100, '70+')]
    print(f'\n=== HR Watch hit rate by score band (last {lookback_days}d, n={len(rows)}) ===')
    print(f'{"band":>8s}  {"n":>5s}  {"hits":>5s}  {"rate":>7s}')
    overall_hits = sum(1 for r in rows if r.get('hr_hit'))
    overall_n = len(rows)
    print(f'{"OVERALL":>8s}  {overall_n:>5}  {overall_hits:>5}  {overall_hits/overall_n*100:>6.1f}%')
    print('-' * 32)
    for lo, hi, lbl in bands:
        band = [r for r in rows if r.get('score') is not None and lo <= r['score'] <= hi]
        n = len(band)
        hits = sum(1 for r in band if r.get('hr_hit'))
        rate = (hits / n * 100) if n else 0
        print(f'{lbl:>8s}  {n:>5}  {hits:>5}  {rate:>6.1f}%')

    print()
    print('=== Top resolved entries (by score) — sanity check ===')
    for r in rows[:8]:
        outcome = '✓ HR' if r.get('hr_hit') else '✗ no HR'
        prob = r.get('projected_hr_prob')
        prob_str = f'  proj {float(prob):.1%}' if prob is not None else ''
        print(f'  {r.get("game_date")}  score {r.get("score")}  {r.get("player_name"):24s}  {outcome}{prob_str}')

    # League-average HR rate per game is ~12% for a starting hitter (4 PA × 3.1% HR/PA).
    # Tier value: how much does score lift over baseline?
    if overall_n >= 30:
        baseline = overall_hits / overall_n
        top_band = [r for r in rows if r.get('score') is not None and r['score'] >= 60]
        if top_band:
            top_rate = sum(1 for r in top_band if r.get('hr_hit')) / len(top_band)
            lift = (top_rate - baseline) * 100
            print()
            print(f'Score >=60 lift over baseline: {lift:+.1f}pt '
                  f'({top_rate*100:.1f}% vs {baseline*100:.1f}%) — '
                  f'{"signal" if lift >= 3 else "noise (consider rescore)"}')


def upsert_tier_calibration():
    """Write the audit findings into mlb_tier_calibration so the daily
    tier-integrity dashboard picks them up alongside prop tiers."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    earliest_30 = cutoff - timedelta(days=30)
    earliest_7 = cutoff - timedelta(days=7)
    qs = urlencode({
        'game_date': f'gte.{earliest_30.strftime("%Y-%m-%d")}',
        'resolved_at': 'not.is.null',
        'select': 'score,hr_hit,game_date',
    })
    try:
        rows = _get(f'{SUPABASE_URL}/rest/v1/mlb_hr_watch?{qs}')
    except Exception:
        return

    bands = [(60, 100, 'hr_watch_score_ge60'),
             (50, 59, 'hr_watch_score_50_59'),
             (40, 49, 'hr_watch_score_40_49'),
             (30, 39, 'hr_watch_score_30_39')]

    payload = []
    for lo, hi, key in bands:
        for window_days, window_label in [(7, '7d'), (30, '30d')]:
            earliest = cutoff - timedelta(days=window_days)
            window_rows = [r for r in rows
                           if r.get('game_date') and r['game_date'] >= earliest.strftime('%Y-%m-%d')
                           and r.get('score') is not None
                           and lo <= r['score'] <= hi]
            wins = sum(1 for r in window_rows if r.get('hr_hit'))
            n = len(window_rows)
            losses = n - wins
            payload.append({
                'tier_key': key,
                'window': window_label,
                'wins': wins,
                'losses': losses,
                'hit_rate': round(wins / n, 4) if n else None,
                'sample_size': n,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            })

    if not payload:
        return
    try:
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/mlb_tier_calibration',
            data=json.dumps(payload).encode('utf-8'),
            headers={**HEADERS, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=20)
        print(f'\n✓ Wrote {len(payload)} HR Watch tier-calibration rows')
    except Exception as e:
        print(f'⚠️  Tier-calibration upsert failed (non-fatal): {e}')


def main():
    print('=' * 70)
    print(f'HR WATCH AUDIT — {datetime.now(timezone.utc).isoformat()}')
    print('=' * 70)
    resolve_unfinished(lookback_days=14)
    print()
    print_cohort_report(lookback_days=30)
    upsert_tier_calibration()


if __name__ == '__main__':
    main()
