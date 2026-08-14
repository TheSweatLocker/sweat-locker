"""Rule backtest + auto-promotion (2026-08-14).

Session C part 3. Runs nightly. For every rule in rule_registry:

  1. Backfill actual_outcome on rule_shadow_log rows where the underlying
     pick has since graded.
  2. Compute rolling hit rates per rule (30d, 90d).
  3. Update rule_registry.current_hit_rate + current_sample_n.
  4. For shadow-mode rules that hit the promotion gate — auto-promote to
     active. For active-mode rules that regress below baseline — write a
     dashboard_alert (does NOT auto-demote; humans review).

Promotion gate (for shadow → active):
  * current_sample_n >= min_sample_for_promotion (default 30)
  * current_hit_rate >= baseline_hit_rate + promotion_lift_pp
  * NO critical data-quality events tied to the rule in last 7 days
  * mode is currently 'shadow'

Regression alert (for active → warning):
  * current_sample_n >= min_sample_for_promotion
  * current_hit_rate < baseline_hit_rate - 5pp
  * mode is currently 'active'

Backfill outcome logic: shadow log rows have (target_table, target_id).
We look up the current row and use its `result` column when available.
For jerry_reads / prop_jerry_reads / mlb_pipeline_props / nfl_pipeline_props
this is the standard field.

CLI:
    python backtest_rules.py [--dry-run] [--rule RULE_NAME]
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional

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

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

BACKTEST_WINDOW_DAYS = 30
REGRESSION_PP = 5.0   # active rule dropping >5pp below baseline → warn alert


def _paginate(url: str, params: dict, page: int = 1000) -> list:
    out = []; offset = 0
    while offset < 5000:
        p = {**params, 'limit': str(page), 'offset': str(offset)}
        r = requests.get(url, headers=H_READ, params=p, timeout=30)
        if r.status_code != 200: return out
        rows = r.json()
        if not isinstance(rows, list) or not rows: break
        out.extend(rows)
        if len(rows) < page: break
        offset += page
    return out


def load_pending_outcomes(rule_name: Optional[str] = None) -> list:
    """Return shadow_log rows in the last 60 days with NULL actual_outcome."""
    since = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
    params = {
        'actual_outcome': 'is.null',
        'game_date': f'gte.{since}',
        'select': 'id,game_date,rule_name,target_table,target_id,before_state,after_state',
    }
    if rule_name: params['rule_name'] = f'eq.{rule_name}'
    return _paginate(f'{SB}/rest/v1/rule_shadow_log', params)


def fetch_outcome(target_table: str, target_id: str) -> Optional[str]:
    """Look up the pick's `result` column from the underlying table."""
    if not target_table: return None
    try:
        r = requests.get(f'{SB}/rest/v1/{target_table}', headers=H_READ,
            params={'id': f'eq.{target_id}', 'select': 'result', 'limit': '1'},
            timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if isinstance(rows, list) and rows:
            return rows[0].get('result')
    except Exception:
        pass
    return None


def would_have_hit(before_state: dict, after_state: dict,
                    actual_outcome: str) -> Optional[bool]:
    """Did the rule's proposed action have net-positive impact?

    Semantics:
      * If the rule converts a pick to PASS and the original would have
        LOST — rule helped (True).
      * If the rule converts to PASS and the original would have WON —
        rule hurt (False).
      * If the rule FLIPS the verdict — check if the flipped verdict is
        aligned with the outcome. Grader semantics: BACK Win = pick won;
        FADE Win = pick faded a losing side (fade cashed).
      * PUSH / anything else → None (unresolvable)
    """
    if actual_outcome not in ('Win', 'Loss'):
        return None
    before_v = (before_state or {}).get('verdict', '').upper()
    after_v = (after_state or {}).get('verdict', '').upper()
    # Simple case: only verdict changed and outcome maps directly
    # The `actual_outcome` on prop_jerry_reads is already grader-adjusted
    # (Win means the row's CURRENT verdict cashed). We compare what the
    # rule DID vs the outcome:
    #   after=applied verdict; actual_outcome reflects the applied state
    #   was 'Win' or 'Loss'.
    # For shadow-mode rows the rule DIDN'T apply → actual_outcome reflects
    # the ORIGINAL verdict. We can infer whether the rule's proposal
    # would have been correct.
    if before_v == after_v:
        return None   # rule proposed no change (rare — LEAN cap only?)
    if after_v == 'PASS':
        # Rule proposes: don't bet. Would have helped if original picked
        # a loser.
        return actual_outcome == 'Loss'
    # Verdict flip (BACK↔FADE). Rule was right if flipped side matches
    # winning direction.
    return actual_outcome == 'Win'


def backfill_outcomes(pending: list, dry_run: bool = False) -> int:
    backfilled = 0
    for row in pending:
        outcome = fetch_outcome(row['target_table'], row['target_id'])
        if not outcome: continue
        hit = would_have_hit(row.get('before_state'), row.get('after_state'), outcome)
        if dry_run:
            print(f'  [DRY] backfill id={row["id"]} rule={row["rule_name"]} → '
                  f'outcome={outcome} would_hit={hit}')
            backfilled += 1
            continue
        payload = {
            'actual_outcome': outcome,
            'would_have_hit': hit,
            'outcome_backfilled_at': datetime.now(timezone.utc).isoformat(),
        }
        pr = requests.patch(f'{SB}/rest/v1/rule_shadow_log?id=eq.{row["id"]}',
            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 201, 204):
            backfilled += 1
    return backfilled


def compute_rule_hit_rates(dry_run: bool = False) -> dict:
    """Aggregate would_have_hit per rule over the backtest window."""
    since = (datetime.now(timezone.utc) - timedelta(days=BACKTEST_WINDOW_DAYS)).date().isoformat()
    rows = _paginate(f'{SB}/rest/v1/rule_shadow_log',
        {'actual_outcome': 'not.is.null',
         'game_date': f'gte.{since}',
         'select': 'rule_name,would_have_hit'})
    stats = defaultdict(lambda: [0, 0])   # [helped, hurt]
    for row in rows:
        h = row.get('would_have_hit')
        if h is True: stats[row['rule_name']][0] += 1
        elif h is False: stats[row['rule_name']][1] += 1
    return {name: {'n': h+t, 'hit_rate': round(100*h/(h+t), 2) if (h+t) else None}
            for name, (h, t) in stats.items()}


def load_registry() -> list:
    return _paginate(f'{SB}/rest/v1/rule_registry', {'select': '*'})


def load_recent_dq_critical(rule_name: str) -> int:
    """How many critical data-quality events cite this rule in last 7d?"""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    r = requests.get(f'{SB}/rest/v1/data_quality_events', headers=H_READ,
        params={'severity': 'eq.critical',
                'event_ts': f'gte.{since}',
                'check_name': f'like.*{rule_name}*',
                'select': 'id', 'limit': '10'},
        timeout=10)
    if r.status_code != 200: return 0
    return len(r.json())


def evaluate_gates(registry: list, hit_rates: dict, dry_run: bool = False) -> None:
    """Apply promotion + regression rules. Writes to rule_registry + dashboard_alerts."""
    from datetime import date as _d
    today = _d.today()

    for rule in registry:
        name = rule['rule_name']
        stats = hit_rates.get(name)
        current_hit = stats['hit_rate'] if stats else None
        current_n = stats['n'] if stats else 0
        mode = rule['mode']
        baseline = float(rule.get('baseline_hit_rate') or 50)
        lift = float(rule.get('promotion_lift_pp') or 2)
        min_n = int(rule.get('min_sample_for_promotion') or 30)

        # Update current metrics on registry (informational)
        registry_updates = {
            'current_hit_rate': current_hit,
            'current_sample_n': current_n,
            'last_backtested_at': datetime.now(timezone.utc).isoformat(),
        }

        # PROMOTION path
        if mode == 'shadow' and current_hit is not None and current_n >= min_n:
            if current_hit >= baseline + lift:
                # Check for blocking critical DQ events
                dq_blocks = load_recent_dq_critical(name)
                if dq_blocks == 0:
                    if dry_run:
                        print(f'  [DRY] PROMOTE {name}: {current_hit}% (baseline+lift {baseline+lift}%) n={current_n} · shadow → active')
                    else:
                        registry_updates['mode'] = 'active'
                        registry_updates['activated_at'] = registry_updates['last_backtested_at']
                        _write_alert({
                            'alert_date': today.isoformat(),
                            'severity': 'info', 'category': 'rule_promotion',
                            'rule_name': name,
                            'message': f'Rule {name} promoted shadow→active: '
                                       f'{current_hit}% ({current_n} fires) beat baseline+lift '
                                       f'{baseline+lift}%',
                            'metric_current': current_hit, 'metric_baseline': baseline+lift,
                            'metric_delta': current_hit - (baseline+lift),
                        })
                        print(f'  ✓ PROMOTED {name} → active')
                else:
                    print(f'  ⏸ {name} met hit-rate gate but has {dq_blocks} critical DQ events — holding shadow')

        # REGRESSION path (active drifting below baseline)
        if mode == 'active' and current_hit is not None and current_n >= min_n:
            if current_hit < baseline - REGRESSION_PP:
                if dry_run:
                    print(f'  [DRY] REGRESSION WARN {name}: {current_hit}% vs baseline {baseline}% n={current_n}')
                else:
                    _write_alert({
                        'alert_date': today.isoformat(),
                        'severity': 'critical' if current_hit < baseline - 15 else 'warn',
                        'category': 'rule_regression',
                        'rule_name': name,
                        'message': f'Active rule {name} regressed to {current_hit}% '
                                   f'({current_n} fires) — {baseline - current_hit:.1f}pp below baseline. '
                                   f'Consider promoting review + potentially demoting via promote_rule.py.',
                        'metric_current': current_hit, 'metric_baseline': baseline,
                        'metric_delta': current_hit - baseline,
                    })

        # Persist registry updates
        if not dry_run and current_hit is not None:
            requests.patch(f'{SB}/rest/v1/rule_registry?rule_name=eq.{name}',
                headers=H_WRITE, json=registry_updates, timeout=10)


def _alert_exists(alert_date: date, category: str, rule_name: str) -> bool:
    r = requests.get(f'{SB}/rest/v1/dashboard_alerts', headers=H_READ,
        params={'alert_date': f'eq.{alert_date.isoformat()}',
                'category': f'eq.{category}',
                'rule_name': f'eq.{rule_name}',
                'select': 'id', 'limit': '1'}, timeout=10)
    return r.status_code == 200 and bool(r.json())


def _write_alert(payload: dict) -> None:
    if _alert_exists(date.fromisoformat(payload['alert_date']),
                     payload['category'], payload.get('rule_name') or ''):
        return
    requests.post(f'{SB}/rest/v1/dashboard_alerts',
        headers=H_WRITE, json=payload, timeout=10)


def run(dry_run: bool = False, rule_filter: Optional[str] = None):
    print('=== backtest_rules ===')
    pending = load_pending_outcomes(rule_filter)
    print(f'  {len(pending)} shadow log rows pending outcome backfill')
    n_back = backfill_outcomes(pending, dry_run=dry_run)
    print(f'  backfilled {n_back} outcomes')

    hit_rates = compute_rule_hit_rates(dry_run=dry_run)
    print(f'  computed hit rates for {len(hit_rates)} rules')
    for name, s in sorted(hit_rates.items(), key=lambda z: z[1]['n'], reverse=True):
        print(f'    {name:35} n={s["n"]:4} hit_rate={s["hit_rate"]}%')

    registry = load_registry()
    evaluate_gates(registry, hit_rates, dry_run=dry_run)
    print('  done')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--rule', help='Filter to one rule name')
    args = p.parse_args()
    run(dry_run=args.dry_run, rule_filter=args.rule)


if __name__ == '__main__':
    main()
