"""Sharp-fade flag helper (2026-08-08).

Phase 1: LOG-ONLY. Reads jerry_cache['sharp_divergence_stats'] +
per-game oddscrowd_snapshot, returns a flag string that describes
whether the pick aligns with a fade-signaled sharp side.

Used by:
  - tier_discipline_gate (log-only; does NOT cap or veto yet)
  - confluence_audit (surface in operator view)
  - future Phase 2 (cap-active) — read the same helper, act on it

Interface:
    from sharp_fade_flag import compute_sharp_fade_flag
    flag = compute_sharp_fade_flag(oddscrowd_snapshot, pick_market, pick_side)
    # flag ∈ (None, 'ALIGNS_WITH_FADE_SIDE', 'OPPOSES_FADE_SIDE')

Phase 1 policy: any bucket with n<200 stays in "log only" mode; the
flag is informational, downstream systems should not gate on it yet.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from typing import Optional

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL'); KEY = os.environ.get('SUPABASE_KEY')
_H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else None

_STATS_CACHE = {'data': None, 'fetched': 0}
_CACHE_TTL_S = 900   # 15 min


def _load_stats():
    """Fetch current sharp_divergence_stats from jerry_cache (cached 15m)."""
    import time
    now = time.time()
    if _STATS_CACHE['data'] and now - _STATS_CACHE['fetched'] < _CACHE_TTL_S:
        return _STATS_CACHE['data']
    if not _H: return None
    try:
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=_H,
                         params={'cache_key': 'eq.sharp_divergence_stats',
                                 'game_id': 'eq.GLOBAL',
                                 'select': 'data'},
                         timeout=10)
        rows = r.json() if isinstance(r.json(), list) else []
        data = rows[0]['data'] if rows else None
    except Exception:
        data = None
    _STATS_CACHE['data'] = data
    _STATS_CACHE['fetched'] = now
    return data


def _bucket(div: int) -> str:
    if div >= 30: return 'sharp_30+'
    if div >= 20: return 'sharp_20+'
    if div >= 15: return 'sharp_15+'
    if div >= 10: return 'sharp_10+'
    if div <= -10: return 'public_10+'
    return 'aligned'


def compute_sharp_fade_flag(oddscrowd_snapshot, pick_market: str,
                             pick_side: str) -> Optional[dict]:
    """Return flag dict describing sharp-money alignment/opposition, or None.

    Args:
        oddscrowd_snapshot: dict with keys 'ml','total' each holding
            {pick, div, money, bets}
        pick_market: 'ml' or 'total'
        pick_side: 'HOME'/'AWAY' for ML; 'OVER'/'UNDER' for total

    Returns:
        {
          'sharp_side': str,
          'div': int,
          'bucket': str,
          'sample_n': int,
          'sample_hit_pct': float,
          'flag': 'ALIGNS_WITH_FADE_SIDE' | 'OPPOSES_FADE_SIDE' | 'NEUTRAL',
          'phase': 'PHASE_1_LOG_ONLY' | 'PHASE_2_CAP_ACTIVE',
          'reason': str,
        }
        Or None if oddscrowd data missing.
    """
    if not oddscrowd_snapshot or not isinstance(oddscrowd_snapshot, dict):
        return None
    if isinstance(oddscrowd_snapshot, str):
        try: oddscrowd_snapshot = json.loads(oddscrowd_snapshot)
        except Exception: return None
    mkt_key = (pick_market or '').lower()
    if mkt_key not in ('ml', 'total'): return None
    blob = oddscrowd_snapshot.get(mkt_key) or {}
    div = blob.get('div'); sharp_pick = blob.get('pick')
    if div is None or div == -1 or not sharp_pick: return None
    bucket = _bucket(div)
    stats = _load_stats() or {}
    bstat = ((stats.get('buckets') or {}).get(mkt_key) or {}).get(bucket) or {}
    n = bstat.get('n', 0)
    hit_pct = bstat.get('sharp_hit_pct')
    phase_2 = bstat.get('phase_2_ready', False)

    # Only sharp_* buckets carry a "fade" verdict (public_10+ = boost, aligned = neutral)
    if bucket.startswith('sharp_') and hit_pct is not None and hit_pct < 45:
        aligns = (sharp_pick == (pick_side or '').upper())
        # 2026-08-08 Phase 2A: cap-active flag from tracker payload
        cap_active = bstat.get('cap_active', False)
        recency_kill = bstat.get('recency_kill_switch_tripped', False)
        # cap_directive: only apply when Phase 2A conditions met AND pick aligns
        cap_directive = None
        if cap_active and aligns:
            cap_directive = 'CAP_TO_LEAN_55'  # tier gate reads this
        return {
            'sharp_side': sharp_pick,
            'div': div,
            'bucket': bucket,
            'sample_n': n,
            'sample_hit_pct': hit_pct,
            'recent_n': bstat.get('recent_n', 0),
            'recent_sharp_hit_pct': bstat.get('recent_sharp_hit_pct'),
            'flag': 'ALIGNS_WITH_FADE_SIDE' if aligns else 'OPPOSES_FADE_SIDE',
            'phase': 'PHASE_2A_CAP_ACTIVE' if cap_active else 'PHASE_1_LOG_ONLY',
            'cap_directive': cap_directive,
            'recency_kill_switch': recency_kill,
            'reason': (f'Sharp on {sharp_pick} at {div:+d}pp div ({bucket} bucket): '
                       f'{hit_pct}% lifetime hit (n={n})'
                       + (f' · recent {bstat.get("recent_sharp_hit_pct")}% (n={bstat.get("recent_n")})' if bstat.get('recent_n') else '')
                       + '. '
                       + ('Pick agrees with fade side — ' + ('CAP TO LEAN' if cap_directive else 'reconsider (log-only)')
                          if aligns else 'Pick opposes fade side — validated.')),
        }
    if bucket == 'public_10+' and hit_pct is not None:
        aligns = (sharp_pick == (pick_side or '').upper())
        return {
            'sharp_side': sharp_pick,
            'div': div,
            'bucket': bucket,
            'sample_n': n,
            'sample_hit_pct': hit_pct,
            'flag': 'ALIGNS_WITH_BOOST_SIDE' if aligns else 'OPPOSES_BOOST_SIDE',
            'phase': 'PHASE_2_CAP_ACTIVE' if phase_2 else 'PHASE_1_LOG_ONLY',
            'reason': (f'Public leaning {sharp_pick} at {div:+d}pp div (public > money), '
                       f'historically {hit_pct}% hit (n={n}). '
                       f'{"Pick aligns — modest boost signal." if aligns else "Pick opposes public-lean signal."}'),
        }
    return None


if __name__ == '__main__':
    # Smoke test
    demo = {'ml': {'pick': 'HOME', 'div': 33, 'money': 78, 'bets': 45},
            'total': {'pick': 'UNDER', 'div': 14, 'money': 65, 'bets': 51}}
    print(compute_sharp_fade_flag(demo, 'ml', 'HOME'))
    print(compute_sharp_fade_flag(demo, 'total', 'UNDER'))
