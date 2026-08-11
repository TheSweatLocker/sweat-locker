"""Pitcher L3 trend gate (2026-08-11).

Answers "is this pitcher trending BETTER or WORSE than their season?"
per outing metric (IP, hits allowed, ER, walks, Ks).

Motivated by Aug 10 Christian Scott postmortem: refit-100 said UNDER
4.5 hits allowed was a lock, but Scott's L3 was 5H/5H/6H — trending
UP, not down. He allowed 7 hits (LOSS). Refit ignored the trend.

## How it works

For each pitcher we care about:
1. Fetch last 5 outings from MLB Stats API (cached daily)
2. Compute slope: is the metric increasing or decreasing across L5?
3. Compare direction of slope vs direction of pick
4. If trend AGAINST the pick with confidence:
   → Return a "trend_warning" that downstream tools can:
     - Downgrade conviction (LEAN cap)
     - Add transparency note to prose
     - Or flag on the pre-publish audit

## Called from

* `jerry_pre_publish_audit.py` as gate #11 (warning tier, not blocking)
* Can be called ad-hoc from any pick evaluation

## API

    from pitcher_trend_gate import check_trend
    r = check_trend(
        pitcher_name='Christian Scott',
        pick_metric='hits_allowed',   # or ip, er, ks, bb
        pick_direction='under',
    )
    # Returns:
    # {
    #   'trend': 'worsening' | 'improving' | 'flat',
    #   'l5': [4, 3, 5, 5, 6],
    #   'contradicts_pick': True,
    #   'severity': 'critical' | 'warning' | 'none',
    #   'note': '...'
    # }

Sport-universal shape but MLB-first (pitcher metrics). NFL/NCAAF/UFC
would use different metrics but the trend-slope framework transfers.
"""
from __future__ import annotations
import os, sys, json, re
from datetime import datetime, timedelta
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

# Reuse jerry_stat_verifier's cache infra to avoid double-hitting MLB API
CACHE_DIR = Path(__file__).parent / '.pitcher_cache'
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_HR = 12

MLB_SEARCH = 'https://statsapi.mlb.com/api/v1/people/search'
MLB_GAMELOG = 'https://statsapi.mlb.com/api/v1/people/{pid}/stats'

# Metric key → stat field in MLB API game log
METRIC_MAP = {
    'hits_allowed': 'hits',
    'ip': 'inningsPitched',
    'er': 'earnedRuns',
    'ks': 'strikeOuts',
    'bb': 'baseOnBalls',
}

# For each metric, HIGHER value means the pitcher is doing WORSE (going against)
# EXCEPT 'ks' where higher = better + 'ip' where higher = better
HIGHER_IS_WORSE = {
    'hits_allowed': True,
    'er': True,
    'bb': True,
    'ks': False,
    'ip': False,
}


def _lookup_pid(name: str) -> int | None:
    """Reuses the shared cache from jerry_stat_verifier."""
    cache = CACHE_DIR / 'name_pid_index.json'
    idx = {}
    if cache.exists():
        try: idx = json.loads(cache.read_text())
        except Exception: pass
    if name in idx: return idx[name]
    try:
        r = requests.get(MLB_SEARCH, params={'names': name, 'sportId': 1, 'active': True}, timeout=8)
        if r.status_code != 200: return None
        ppl = r.json().get('people', [])
        pitchers = [p for p in ppl if (p.get('primaryPosition') or {}).get('abbreviation') == 'P']
        if not pitchers: return None
        pid = pitchers[0]['id']
        idx[name] = pid
        try: cache.write_text(json.dumps(idx))
        except Exception: pass
        return pid
    except Exception:
        return None


def _fetch_l5_metric(pid: int, metric: str) -> list[float] | None:
    """Return last 5 game values for a specific metric, oldest-first."""
    stat_field = METRIC_MAP.get(metric)
    if not stat_field: return None
    try:
        r = requests.get(MLB_GAMELOG.format(pid=pid),
            params={'stats': 'gameLog', 'group': 'pitching',
                    'season': datetime.now().year, 'sportId': 1}, timeout=10)
        if r.status_code != 200: return None
        splits = r.json().get('stats', [{}])[0].get('splits', [])
        if len(splits) < 3: return None
        l5 = splits[-5:]
        vals = []
        for s in l5:
            v = s.get('stat', {}).get(stat_field)
            if v is None: continue
            try: vals.append(float(v))
            except (TypeError, ValueError): continue
        return vals if len(vals) >= 3 else None
    except Exception:
        return None


def _compute_slope(values: list[float]) -> tuple[str, float]:
    """Simple linear-regression slope on the sequence.
    Returns ('worsening'|'improving'|'flat', slope_value).

    Slope interpretation depends on metric — caller applies HIGHER_IS_WORSE.
    Here we just return raw slope; direction interpretation is done above.
    """
    n = len(values)
    if n < 3: return ('flat', 0.0)
    # x = [0, 1, 2, ..., n-1]; y = values
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0
    # Materiality threshold — slope per outing must exceed noise
    # For hits (0-15 range) treat >0.4/game as material
    # For ERA-like (0-15) treat >0.3/game as material
    threshold = max(0.3, 0.05 * y_mean)  # ~5% of mean per outing
    if slope > threshold: return ('worsening_or_improving_up', slope)
    if slope < -threshold: return ('worsening_or_improving_down', slope)
    return ('flat', slope)


def check_trend(pitcher_name: str, pick_metric: str, pick_direction: str) -> dict:
    """Main entry — evaluate whether pitcher's L5 trend contradicts the pick.

    pick_metric: 'hits_allowed' / 'er' / 'ks' / 'bb' / 'ip'
    pick_direction: 'over' / 'under'

    Returns:
      trend: 'worsening'|'improving'|'flat' (relative to the PITCHER)
      l5: list of last-5 values
      contradicts_pick: bool
      severity: 'critical'|'warning'|'none'
      note: human-readable summary
    """
    default = {'trend': 'flat', 'l5': [], 'contradicts_pick': False,
               'severity': 'none', 'note': 'insufficient data'}
    pid = _lookup_pid(pitcher_name)
    if not pid: return default
    values = _fetch_l5_metric(pid, pick_metric)
    if not values or len(values) < 3: return default

    direction_str, slope = _compute_slope(values)

    # 2026-08-11 (v2): simplify — check raw metric direction vs pick direction.
    # Prior "pitcher perspective" abstraction inverted incorrectly for K props
    # (Ks decreasing ALIGNS with under pick, not contradicts).
    #
    # UNDER pick expects metric to be LOW → metric trending DOWN or flat aligns.
    #                                       Metric trending UP contradicts.
    # OVER pick expects metric to be HIGH → metric trending UP or flat aligns.
    #                                       Metric trending DOWN contradicts.
    metric_going_up = (direction_str == 'worsening_or_improving_up')
    metric_going_down = (direction_str == 'worsening_or_improving_down')

    contradicts = False
    if pick_direction == 'under' and metric_going_up:
        contradicts = True
    elif pick_direction == 'over' and metric_going_down:
        contradicts = True

    # Human-readable pitcher-perspective label (for logging only)
    higher_worse = HIGHER_IS_WORSE.get(pick_metric, True)
    if direction_str == 'flat': pitcher_trend = 'flat'
    elif metric_going_up: pitcher_trend = 'worsening' if higher_worse else 'improving'
    else: pitcher_trend = 'improving' if higher_worse else 'worsening'

    if contradicts:
        threshold = max(0.3, 0.05 * (sum(values) / len(values)))
        severity = 'critical' if abs(slope) > 2 * threshold else 'warning'
    else:
        severity = 'none'

    l5_str = ', '.join(str(int(v)) if v == int(v) else f'{v:.1f}' for v in values)
    note = (f'{pitcher_name} last {len(values)} {pick_metric}: {l5_str} '
            f'({pitcher_trend}, slope {slope:+.2f}/game). '
            f'{"CONTRADICTS" if contradicts else "aligns with"} {pick_direction.upper()} pick.')

    return {
        'trend': pitcher_trend,
        'l5': values,
        'slope': round(slope, 3),
        'contradicts_pick': contradicts,
        'severity': severity,
        'note': note,
    }


# CLI test
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--pitcher', required=True)
    ap.add_argument('--metric', default='hits_allowed')
    ap.add_argument('--direction', default='under')
    args = ap.parse_args()
    r = check_trend(args.pitcher, args.metric, args.direction)
    print(json.dumps(r, indent=2))
