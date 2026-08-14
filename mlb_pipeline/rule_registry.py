"""Rule registry client library (2026-08-14).

Session C core module. Sport-universal helper that scripts use to check
whether a rule is active, log shadow fires, and record applied mutations.

Pattern for a repair script:

    from rule_registry import RuleRunner

    runner = RuleRunner('FORCE_PASS_JERRY_HALLUCINATION')

    for row in candidate_rows:
        proposal = compute_proposal(row)   # local decision — always compute
        if not proposal: continue

        # ONE call handles: check mode, log the fire, decide whether to apply
        if runner.fire(
            sport='MLB',
            game_date=row['game_date'],
            game_id=row.get('game_id'),
            target_table='prop_jerry_reads',
            target_id=str(row['id']),
            proposed_action='BACK→PASS',
            before_state={'verdict': row['call_verdict'], 'conviction': row['conviction']},
            after_state={'verdict': 'PASS', 'conviction': 30},
            context={'raw': raw, 'refit': refit},
        ):
            apply_mutation(row, proposal)     # only runs if mode='active'

Design:
  * runner.fire() ALWAYS logs (so backtest has the sample)
  * runner.fire() returns True only when mode='active' (so callers know
    whether to apply the mutation)
  * `off` mode → no log, no apply. Function returns False immediately.
  * mode cache refreshes every 5 min so promote_rule.py takes effect
    within one cron cycle.

The runner does NOT raise on Supabase errors — fail-open, same as the
data_quality.py library. Monitoring failure never breaks pick pipeline.
"""
from __future__ import annotations
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

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
_H_READ = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'} if _SB and _KEY else None
_H_WRITE = {**_H_READ, 'Content-Type': 'application/json',
            'Prefer': 'return=minimal'} if _H_READ else None

# Module-level cache: {rule_name: (mode, expires_at_unix)}
_MODE_CACHE: dict = {}
_MODE_CACHE_TTL = 300  # 5 min


def _load_mode(rule_name: str) -> str:
    """Fetch mode from rule_registry with 5-min cache. Falls back to
    'active' if the registry is unreachable — chose 'active' because
    breaking a rule silently (falling to 'off') would REGRESS behavior
    and hide bugs. If the DB is down, we prefer the pre-DB status quo."""
    now = time.time()
    cached = _MODE_CACHE.get(rule_name)
    if cached and cached[1] > now:
        return cached[0]
    if not _H_READ:
        return 'active'
    try:
        r = requests.get(f'{_SB}/rest/v1/rule_registry', headers=_H_READ,
            params={'rule_name': f'eq.{rule_name}', 'select': 'mode', 'limit': '1'},
            timeout=5)
        rows = r.json() if r.status_code == 200 else []
        if isinstance(rows, list) and rows:
            mode = rows[0].get('mode', 'active')
        else:
            mode = 'active'  # unregistered rules default to active for compatibility
    except Exception:
        mode = 'active'
    _MODE_CACHE[rule_name] = (mode, now + _MODE_CACHE_TTL)
    return mode


class RuleRunner:
    """One instance per rule, per script run. Centralizes mode check +
    shadow logging so callers write cleaner logic."""

    def __init__(self, rule_name: str):
        self.rule_name = rule_name
        self._session_fires = 0
        self._session_applied = 0

    @property
    def mode(self) -> str:
        return _load_mode(self.rule_name)

    @property
    def fires_this_session(self) -> int:
        return self._session_fires

    @property
    def applied_this_session(self) -> int:
        return self._session_applied

    def fire(self, *, sport: str, target_table: str, target_id: str,
             game_date: Optional[str] = None, game_id: Optional[str] = None,
             proposed_action: Optional[str] = None,
             before_state: Optional[dict] = None,
             after_state: Optional[dict] = None,
             context: Optional[dict] = None) -> bool:
        """Log a rule fire. Returns True iff caller should APPLY the mutation.

        Contract:
          mode='off'    → no log, returns False (caller does nothing)
          mode='shadow' → log with applied=False, returns False (caller does
                          not mutate)
          mode='active' → log with applied=True, returns True (caller mutates)
        """
        mode = self.mode
        if mode == 'off':
            return False

        applied = (mode == 'active')
        if applied:
            self._session_applied += 1
        self._session_fires += 1

        if not _H_WRITE:
            return applied  # log skipped, but still tell caller what to do

        try:
            payload = {
                'event_ts': datetime.now(timezone.utc).isoformat(),
                'sport': sport,
                'game_date': game_date,
                'game_id': game_id,
                'rule_name': self.rule_name,
                'rule_mode': mode,
                'target_table': target_table,
                'target_id': str(target_id),
                'proposed_action': proposed_action,
                'before_state': before_state or {},
                'after_state': after_state or {},
                'applied': applied,
                'context': context or {},
            }
            requests.post(f'{_SB}/rest/v1/rule_shadow_log',
                headers=_H_WRITE, json=payload, timeout=5)
        except Exception as e:
            print(f'  [RULE] {self.rule_name} log failed silently: {type(e).__name__}',
                  file=sys.stderr)
        return applied


# ─── Convenience helpers ─────────────────────────────────────────────

def get_rule_row(rule_name: str) -> Optional[dict]:
    """Fetch full rule_registry row for admin/CLI use."""
    if not _H_READ: return None
    try:
        r = requests.get(f'{_SB}/rest/v1/rule_registry', headers=_H_READ,
            params={'rule_name': f'eq.{rule_name}', 'select': '*', 'limit': '1'},
            timeout=5)
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if isinstance(rows, list) and rows else None
    except Exception:
        return None


def set_rule_mode(rule_name: str, mode: str, reason: str = '') -> bool:
    """Admin toggle. Used by promote_rule.py CLI."""
    if mode not in ('off', 'shadow', 'active'):
        raise ValueError(f'invalid mode: {mode}')
    if not _H_WRITE: return False
    updates = {'mode': mode, 'updated_at': datetime.now(timezone.utc).isoformat()}
    if mode == 'active':
        updates['activated_at'] = updates['updated_at']
    elif mode == 'off':
        updates['demoted_at'] = updates['updated_at']
        if reason: updates['disabled_reason'] = reason
    try:
        pr = requests.patch(f'{_SB}/rest/v1/rule_registry?rule_name=eq.{rule_name}',
            headers=_H_WRITE, json=updates, timeout=10)
        # Invalidate cache
        _MODE_CACHE.pop(rule_name, None)
        return pr.status_code in (200, 201, 204)
    except Exception:
        return False


def register_rule(rule_name: str, rule_class: str, description: str = '',
                  sport: Optional[str] = None, baseline_hit_rate: float = 50.0,
                  promotion_lift_pp: float = 2.0,
                  min_sample: int = 30, mode: str = 'shadow') -> bool:
    """Called by new rule authors. Upserts into rule_registry with mode=shadow
    by default — new rules start LOG-ONLY per the safety contract."""
    if not _H_WRITE: return False
    payload = {
        'rule_name': rule_name, 'rule_class': rule_class, 'sport': sport,
        'mode': mode, 'baseline_hit_rate': baseline_hit_rate,
        'promotion_lift_pp': promotion_lift_pp,
        'min_sample_for_promotion': min_sample,
        'description': description,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        pr = requests.post(
            f'{_SB}/rest/v1/rule_registry?on_conflict=rule_name',
            headers={**_H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json=payload, timeout=10)
        return pr.status_code in (200, 201, 204)
    except Exception:
        return False
