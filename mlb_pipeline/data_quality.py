"""Data quality assertion library (2026-08-14).

Session B core module. Sport-universal assertions that pipeline scripts
import + call. Every failure gets logged to `data_quality_events`;
recurring failures get promoted to `dashboard_alerts` by the daily
aggregator.

Design principles:
  * NEVER raise. Assertions log + return bool. Pipeline continues but
    with visibility. Would you rather have a broken value or a total
    outage? Both are bad; the log helps us pick the fix.
  * ONE-LINER API. Fewer excuses to skip validation.
  * CONTEXT-RICH LOG. Every trip records the sport, source, expected vs
    actual, and any relevant identifiers (pitcher_id, game_id, team).
  * FAIL-OPEN network. If Supabase is unreachable, we log to stderr and
    let the pipeline proceed. Monitoring failure shouldn't cascade into
    pick failure.

Usage inside a script:

    from data_quality import DQ

    dq = DQ(source='game_context.py')

    # Fetch shape
    if not dq.assert_non_empty(splits, 'pitcher_gamelog.splits',
                                context={'pitcher_id': pid}):
        return None

    # Range
    dq.assert_range(innings, 0, 9, 'pitcher_last_outing.innings',
                    context={'pitcher_id': pid, 'value': innings})

    # Ordering — this would have caught splits[0] bug day 1
    dq.assert_ordering_desc(dates, 'pitcher_gamelog.dates',
                            context={'pitcher_id': pid})

    # Cross-check
    dq.assert_close(mlb_api_ip, ctx_ip, tolerance=0.5,
                    'pitcher_last_outing_cross_check',
                    context={'pitcher': name, 'ctx': ctx_ip, 'api': mlb_api_ip})
"""
from __future__ import annotations
import os
import sys
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

_SB = os.environ.get('SUPABASE_URL')
_KEY = os.environ.get('SUPABASE_KEY')
_H_WRITE = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}',
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal'} if _SB and _KEY else None


class DQ:
    """Per-source assertion helper. Instantiate once per script."""

    def __init__(self, source: str, sport: Optional[str] = None,
                 stderr_on_fail: bool = True):
        self.source = source
        self.default_sport = sport
        self.stderr_on_fail = stderr_on_fail
        self._trip_count = 0

    def _log(self, check_class: str, check_name: str, message: str,
             severity: str = 'warn', context: Optional[dict] = None,
             sport: Optional[str] = None) -> None:
        """Write one event row. Fail-open on any network issue."""
        self._trip_count += 1
        eff_sport = sport or self.default_sport
        if self.stderr_on_fail:
            print(f'  [DQ] {severity.upper():8} {self.source}: {check_name} — {message}',
                  file=sys.stderr)
        if not _H_WRITE:
            return
        try:
            payload = {
                'event_ts': datetime.now(timezone.utc).isoformat(),
                'sport': eff_sport,
                'source': self.source,
                'check_class': check_class,
                'check_name': check_name,
                'severity': severity,
                'message': message,
                'context': context or {},
            }
            requests.post(f'{_SB}/rest/v1/data_quality_events',
                headers=_H_WRITE, json=payload, timeout=5)
        except Exception as e:
            # Never raise from monitoring
            print(f'  [DQ] log failed silently: {type(e).__name__}',
                  file=sys.stderr)

    @property
    def trip_count(self) -> int:
        return self._trip_count

    # ─── FETCH assertions ────────────────────────────────────────────

    def assert_non_empty(self, value: Any, check_name: str,
                          severity: str = 'warn',
                          context: Optional[dict] = None,
                          sport: Optional[str] = None) -> bool:
        """Passes if value is a non-empty list/dict/string."""
        if value is None:
            self._log('fetch_shape', check_name, 'value is None',
                      severity=severity, context=context, sport=sport)
            return False
        if hasattr(value, '__len__') and len(value) == 0:
            self._log('fetch_shape', check_name, 'value is empty',
                      severity=severity, context=context, sport=sport)
            return False
        return True

    def assert_field_present(self, obj: Any, field: str, check_name: str,
                              severity: str = 'warn',
                              context: Optional[dict] = None,
                              sport: Optional[str] = None) -> bool:
        """Passes if obj is a dict and field is present + non-None."""
        if not isinstance(obj, dict):
            self._log('fetch_shape', check_name,
                      f'expected dict for field check, got {type(obj).__name__}',
                      severity=severity, context=context, sport=sport)
            return False
        if field not in obj:
            self._log('fetch_shape', check_name, f'field "{field}" missing',
                      severity=severity, context=context, sport=sport)
            return False
        if obj.get(field) is None:
            self._log('fetch_shape', check_name, f'field "{field}" is None',
                      severity='info', context=context, sport=sport)
            return False
        return True

    # ─── RANGE assertions ────────────────────────────────────────────

    def assert_range(self, value: Any, low: float, high: float,
                      check_name: str,
                      severity: str = 'warn',
                      context: Optional[dict] = None,
                      sport: Optional[str] = None) -> bool:
        """Passes if value is a number within [low, high]."""
        try:
            n = float(value)
        except (TypeError, ValueError):
            self._log('fetch_range', check_name,
                      f'value {value!r} not numeric',
                      severity=severity, context=context, sport=sport)
            return False
        if math.isnan(n) or math.isinf(n):
            self._log('transform_nan', check_name, f'NaN or Inf: {value}',
                      severity='critical', context=context, sport=sport)
            return False
        if not (low <= n <= high):
            self._log('fetch_range', check_name,
                      f'value {n} outside [{low}, {high}]',
                      severity=severity,
                      context={**(context or {}), 'value': n, 'low': low, 'high': high},
                      sport=sport)
            return False
        return True

    # ─── ORDERING assertions ────────────────────────────────────────
    # These are the assertions that would have caught splits[0] bug day 1.

    def assert_ordering_asc(self, values: Iterable, check_name: str,
                             severity: str = 'warn',
                             context: Optional[dict] = None,
                             sport: Optional[str] = None) -> bool:
        """Passes if values are non-strictly ascending. Empty passes."""
        vals = list(values)
        for i in range(1, len(vals)):
            if vals[i] < vals[i - 1]:
                self._log('fetch_ordering', check_name,
                          f'ordering broken at index {i}: {vals[i-1]!r} > {vals[i]!r}',
                          severity=severity,
                          context={**(context or {}), 'first': vals[0], 'last': vals[-1]},
                          sport=sport)
                return False
        return True

    def assert_ordering_desc(self, values: Iterable, check_name: str,
                              severity: str = 'warn',
                              context: Optional[dict] = None,
                              sport: Optional[str] = None) -> bool:
        vals = list(values)
        for i in range(1, len(vals)):
            if vals[i] > vals[i - 1]:
                self._log('fetch_ordering', check_name,
                          f'ordering broken at index {i}: {vals[i-1]!r} < {vals[i]!r}',
                          severity=severity,
                          context={**(context or {}), 'first': vals[0], 'last': vals[-1]},
                          sport=sport)
                return False
        return True

    # ─── FRESHNESS assertions (2026-08-14 · stat-date audit) ───────
    #
    # Motivated by Bassitt case on 8/14 slate: MLB API returned his L3
    # correctly (5.14 ERA · verified live), but his last outing was
    # 2026-06-03 — 72 days ago. Pipeline projected 5.60 hits allowed for
    # today's start based on that 72-day-old data with no warning.
    # Ordering assertions catch "wrong direction" bugs; freshness
    # assertions catch "correct direction but stale" bugs.

    def assert_freshness_days(self, latest_date: Any, max_days: int,
                               check_name: str,
                               severity: str = 'warn',
                               context: Optional[dict] = None,
                               sport: Optional[str] = None) -> bool:
        """Passes if latest_date is within max_days of now (ET).
        latest_date: str 'YYYY-MM-DD', datetime, or date object.
        Returns False + logs if stale beyond max_days."""
        try:
            if isinstance(latest_date, str):
                from datetime import datetime as _dt
                latest = _dt.strptime(latest_date[:10], '%Y-%m-%d').date()
            elif hasattr(latest_date, 'date'):
                latest = latest_date.date()
            else:
                latest = latest_date
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            now_et = (_dt.now(_tz.utc) - _td(hours=4)).date()
            days_old = (now_et - latest).days
        except (TypeError, ValueError, AttributeError) as e:
            self._log('freshness_parse_fail', check_name,
                      f'could not parse latest_date {latest_date!r}: {e}',
                      severity=severity, context=context, sport=sport)
            return False
        if days_old > max_days:
            self._log('stale_data', check_name,
                      f'latest={latest.isoformat()} is {days_old} days old (max {max_days})',
                      severity=severity,
                      context={**(context or {}), 'days_old': days_old,
                               'latest_date': latest.isoformat(),
                               'max_days': max_days},
                      sport=sport)
            return False
        return True

    # ─── CROSS-CHECK assertions ─────────────────────────────────────

    def assert_close(self, a: Any, b: Any, tolerance: float,
                      check_name: str,
                      severity: str = 'warn',
                      context: Optional[dict] = None,
                      sport: Optional[str] = None) -> bool:
        """Passes if |a - b| <= tolerance. Both must be numeric."""
        try:
            na = float(a); nb = float(b)
        except (TypeError, ValueError):
            self._log('cross_check_diff', check_name,
                      f'non-numeric values: {a!r}, {b!r}',
                      severity=severity, context=context, sport=sport)
            return False
        if abs(na - nb) > tolerance:
            self._log('cross_check_diff', check_name,
                      f'values diverge: {na} vs {nb} (diff {abs(na-nb):.2f} > tol {tolerance})',
                      severity=severity,
                      context={**(context or {}), 'a': na, 'b': nb},
                      sport=sport)
            return False
        return True

    # ─── WRITE assertions ───────────────────────────────────────────

    def assert_batch_count(self, n_written: int, expected_low: int,
                            expected_high: int, check_name: str,
                            severity: str = 'warn',
                            context: Optional[dict] = None,
                            sport: Optional[str] = None) -> bool:
        """Passes if today's write count is inside historical range."""
        if not (expected_low <= n_written <= expected_high):
            self._log('write_count_drop', check_name,
                      f'wrote {n_written}, expected [{expected_low}, {expected_high}]',
                      severity=severity,
                      context={**(context or {}), 'n_written': n_written,
                               'expected_low': expected_low, 'expected_high': expected_high},
                      sport=sport)
            return False
        return True


# ─── Common range priors (sport-scoped) ─────────────────────────────
# Import + reuse instead of scattering magic numbers.
RANGES = {
    'MLB': {
        'pitcher_ip_single_game':    (0.0, 9.5),
        'pitcher_pitches_single_game': (0, 140),
        'pitcher_era_season':        (0.0, 20.0),
        'pitcher_xera_season':       (0.5, 10.0),
        'pitcher_k_pct':             (0.0, 60.0),
        'team_wrc_plus':             (-30, 250),
        'team_ops':                  (0.400, 1.100),
        'game_total_projected':      (5.0, 18.0),
        'game_spread_projected':     (-15.0, 15.0),
    },
    'NFL': {
        'game_total_projected':      (20.0, 75.0),
        'game_spread_projected':     (-30.0, 30.0),
        'team_off_epa_per_play':     (-0.5, 0.5),
        'team_def_epa_per_play':     (-0.5, 0.5),
        'rest_days':                 (3, 15),
        'wind_mph':                  (0, 45),
        'temp_f':                    (-10, 115),
    },
    'NBA': {
        'game_total_projected':      (180.0, 270.0),
        'game_spread_projected':     (-25.0, 25.0),
        'team_off_rtg':              (95, 130),
        'team_def_rtg':              (95, 130),
    },
    'NHL': {
        'game_total_projected':      (3.5, 10.5),
        'game_spread_projected':     (-3.5, 3.5),
        'team_goals_per_game':       (1.5, 5.0),
    },
    'NCAAB': {
        'game_total_projected':      (100.0, 200.0),
        'team_kenpom_adj_o':         (80, 130),
    },
    'NCAAF': {
        'game_total_projected':      (20.0, 100.0),
        'game_spread_projected':     (-50.0, 50.0),
    },
}


def get_range(sport: str, key: str) -> Optional[tuple]:
    """Look up a sport-specific range prior. None if not defined."""
    return RANGES.get(sport, {}).get(key)
