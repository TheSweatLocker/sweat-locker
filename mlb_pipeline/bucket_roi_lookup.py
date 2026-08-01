"""Bucket ROI lookup helper (2026-08-01 · R-4).

Shared by generate_jerry_synthesis (game-level) and
generate_prop_jerry_synthesis (prop-level). Loads both bucket_roi tables
at start of run, provides fast in-memory lookup for per-prop/per-game
injection into Jerry's prompt.

Usage:
    from bucket_roi_lookup import load_prop_buckets, load_game_buckets, lookup_prop, lookup_game

    prop_buckets = load_prop_buckets(sport='MLB')
    game_buckets = load_game_buckets(sport='MLB')

    hit = lookup_prop(prop_buckets, tier='STRONG', prop_type='bb_under', direction='under')
    # → {'hit_rate': 69.4, 'roi_pct': 17.4, 'sample_n': 62, 'jerry_hint': 'BACK', 'hint_confidence': 67}
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Same family normalization as compute_prop_bucket_roi
FAMILY_MAP = {
    'ks_over': 'ks', 'ks_under': 'ks', 'ks': 'ks',
    'bb_over': 'bb', 'bb_under': 'bb', 'bb': 'bb',
    'er_over': 'er', 'er_under': 'er', 'er': 'er',
    'ha_over': 'ha', 'ha_under': 'ha', 'ha': 'ha',
    'outs_over': 'outs', 'outs_under': 'outs', 'outs': 'outs',
    'hits_over': 'hits', 'hits_under': 'hits', 'hits': 'hits',
}


def load_prop_buckets(sport: str = 'MLB', bucket_window: str = 'lifetime') -> dict:
    """Return {(tier, prop_family, direction): bucket_stats}"""
    url = os.environ.get('SUPABASE_URL'); key = os.environ.get('SUPABASE_KEY')
    if not url or not key: return {}
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    r = requests.get(f'{url}/rest/v1/prop_bucket_roi',
                     headers=h,
                     params={'sport': f'eq.{sport}',
                             'bucket_window': f'eq.{bucket_window}',
                             'select': '*', 'limit': '500'}, timeout=15)
    if r.status_code != 200: return {}
    data = r.json() if isinstance(r.json(), list) else []
    return {(row['tier'], row['prop_type'], row['direction']): row for row in data}


def load_game_buckets(sport: str = 'MLB', bucket_window: str = 'lifetime') -> dict:
    """Return {(tier, market, direction): bucket_stats}"""
    url = os.environ.get('SUPABASE_URL'); key = os.environ.get('SUPABASE_KEY')
    if not url or not key: return {}
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    r = requests.get(f'{url}/rest/v1/game_bucket_roi',
                     headers=h,
                     params={'sport': f'eq.{sport}',
                             'bucket_window': f'eq.{bucket_window}',
                             'select': '*', 'limit': '200'}, timeout=15)
    if r.status_code != 200: return {}
    data = r.json() if isinstance(r.json(), list) else []
    return {(row['tier'], row['market'], row['direction']): row for row in data}


def lookup_prop(buckets: dict, tier: str, prop_type: str, direction: str) -> dict | None:
    """Fuzzy lookup — normalizes prop_type to family."""
    if not tier or not prop_type or not direction: return None
    family = FAMILY_MAP.get(prop_type, prop_type.split('_')[0] if '_' in prop_type else prop_type)
    return buckets.get((tier, family, direction.lower()))


def lookup_game(buckets: dict, tier: str, market: str, direction: str) -> dict | None:
    if not tier or not market or not direction: return None
    return buckets.get((tier, market.upper(), direction.upper()))


# Smart-injection gates (2026-08-01 Path B). Prevents signal dilution: only
# inject bucket_hint into Jerry prompt when the bucket has MEANINGFUL edge.
# Otherwise Jerry sees "no historical bucket data" (short + skippable).
MIN_ROI_ABS_PCT = 5.0          # abs(ROI) must clear this to inject
MIN_HINT_CONF   = 45           # hint_confidence floor (guards against weak hints)
MIN_SAMPLE_N    = 25           # too-small samples always suppressed


def _is_worth_injecting(bucket: dict) -> bool:
    n = bucket.get('sample_n') or 0
    if n < MIN_SAMPLE_N: return False
    roi = bucket.get('roi_pct')
    hint_conf = bucket.get('hint_confidence') or 0
    if roi is not None and abs(float(roi)) >= MIN_ROI_ABS_PCT: return True
    if hint_conf >= MIN_HINT_CONF: return True
    return False


def format_prop_hint(bucket: dict | None) -> str:
    """One-line summary for Jerry prompt injection. Only surfaces buckets with
    real edge (|ROI|≥5% or conf≥45) at n≥25. Weak buckets return a terse
    string Jerry can safely ignore — prevents attention dilution across 60+
    prop reads per slate."""
    if not bucket or not _is_worth_injecting(bucket):
        return 'No meaningful historical bucket edge (bucket too neutral or too thin).'
    hint = bucket.get('jerry_hint') or 'PASS'
    roi = bucket.get('roi_pct')
    hit = bucket.get('hit_rate')
    n = bucket.get('sample_n')
    roi_s = f'{roi:+.1f}%' if roi is not None else 'ROI unavailable'
    return (f'HISTORICAL BUCKET ({bucket.get("tier")}, {bucket.get("prop_type")}, '
            f'{bucket.get("direction")}): hit {hit}%, ROI {roi_s}, n={n} → hint: {hint} '
            f'(conf {bucket.get("hint_confidence")})')


def format_game_hint(bucket: dict | None) -> str:
    if not bucket or not _is_worth_injecting(bucket):
        return 'No meaningful historical game-bucket edge.'
    hint = bucket.get('jerry_hint') or 'PASS'
    roi = bucket.get('roi_pct')
    hit = bucket.get('hit_rate')
    n = bucket.get('sample_n')
    roi_s = f'{roi:+.1f}%' if roi is not None else 'ROI unavailable'
    return (f'HISTORICAL BUCKET ({bucket.get("tier")}, {bucket.get("market")}, '
            f'{bucket.get("direction")}): hit {hit}%, ROI {roi_s}, n={n} → hint: {hint}')
