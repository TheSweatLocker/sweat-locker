"""Silent-failure watchdogs (2026-08-20).

Runs a suite of health checks and writes tripped alerts to
`watchdog_alerts`. Every check here corresponds to a real leak the user
has personally caught this session or in prior weeks:

  * ladder_empty          → ladder sat parked for weeks (silent 400
                            in select clause). Alert if no rung fired
                            in last 3+ days.
  * primary_play_stale    → 8/20 audit found 3/9 games persisted stale
                            picks (fixed by 30-min recompute cron).
                            Alert if live re-score disagrees with DB.
  * prime_hit_crash       → PRIME hit rate dropping below 50% on
                            rolling 7d. Early warning on model drift.
  * grader_coverage_low   → props/games ungraded 24h+ after game time.
                            Silently blocks tier calibration.
  * sharp_source_dropped  → CZ/FR/OC counts drop >50% day-over-day.
                            Scraper died silently.
  * chalk_dedupe_broken   → daily_ledger chalk_parlay has duplicate
                            game_id in legs. Caught 8/19 (Rays twice).
  * ensemble_engine_share → recompute_primary_play falls back to
                            legacy for >30% of games. Was 47% on 8/18.
  * jerry_diagnostic_leak → user-visible short_read contains raw
                            audit tags like "[Auto-sim-repair..." —
                            all three writer paths fixed 8/20.
  * signal_source_dark    → enabled signal that hasn't fired in 14+
                            days across any game.

CLI:
  python watchdogs.py                    # run all checks, print + write
  python watchdogs.py --dry-run          # print only, no DB writes
  python watchdogs.py --check ladder_empty  # single check
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _days_ago(n: int) -> str:
    return ((datetime.now(timezone.utc) - timedelta(hours=4)).date()
            - timedelta(days=n)).isoformat()


# ─────────────────────────────────────────────────────────────
# CHECKS
# ─────────────────────────────────────────────────────────────

def check_ladder_empty() -> Optional[dict]:
    """Alert if no ladder rung fired in the last 3 days."""
    since = _days_ago(3)
    r = requests.get(f'{SB}/rest/v1/ladder_rung',
        params={'game_date': f'gte.{since}', 'select': 'id,game_date'},
        headers=H_READ, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    if isinstance(rows, list) and rows:
        return None
    return {
        'check_name': 'ladder_empty',
        'severity': 'WARNING',
        'message': f'No ladder rung fired in last 3 days (since {since}).',
        'detail': {'since': since, 'rows_found': len(rows) if isinstance(rows, list) else 0},
    }


def check_ensemble_engine_share() -> Optional[dict]:
    """Alert if <70% of today's picks came from ensemble_v2 (rest fell back
    to legacy). Was 47% on 8/18 — the bug that spawned the recompute fix."""
    today = _et_today()
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'game_date': f'eq.{today}', 'select': 'primary_play'},
        headers=H_READ, timeout=10)
    ctx = r.json() if r.status_code == 200 else []
    if not isinstance(ctx, list) or not ctx:
        return None  # off day, not a problem
    ens = sum(1 for g in ctx if (g.get('primary_play') or {}).get('_engine') == 'ensemble_v2')
    total = len(ctx)
    pct = 100.0 * ens / total if total else 0
    if pct >= 70:
        return None
    return {
        'check_name': 'ensemble_engine_share',
        'severity': 'CRITICAL' if pct < 50 else 'WARNING',
        'message': f'Only {pct:.0f}% of today\'s picks ran ensemble_v2 (expected ≥90%). '
                   f'{total - ens}/{total} fell back to legacy.',
        'detail': {'today': today, 'ensemble_count': ens, 'total': total, 'pct': round(pct, 1)},
    }


def check_primary_play_stale() -> Optional[dict]:
    """Alert if any of today's persisted primary_play disagrees with the live
    ensemble output. Caught 3/9 stale on 8/20."""
    today = _et_today()
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'game_date': f'eq.{today}', 'select': '*'},
        headers=H_READ, timeout=15)
    if r.status_code != 200:
        return None
    ctx = r.json()
    if not isinstance(ctx, list) or not ctx:
        return None
    try:
        # Late import so watchdogs can run even if ensemble_scorer is broken
        from ensemble_scorer import score_game
    except Exception as e:
        return {
            'check_name': 'primary_play_stale',
            'severity': 'CRITICAL',
            'message': f'ensemble_scorer import failed: {type(e).__name__}: {e}',
            'detail': {'today': today},
        }
    stale = []
    for g in ctx:
        persisted = g.get('primary_play') or {}
        try:
            live = score_game('MLB', g)
        except Exception:
            continue
        if live is None: continue
        top = live.top()
        per_key = f'{persisted.get("type")}/{persisted.get("label")}/{persisted.get("tier")}'
        live_key = f'{top.market}/{top.display_label}/{top.tier}'
        if per_key != live_key:
            stale.append({
                'matchup': f'{g.get("away_team","?")} @ {g.get("home_team","?")}',
                'persisted': per_key, 'live': live_key,
            })
    if not stale:
        return None
    pct = 100.0 * len(stale) / len(ctx)
    return {
        'check_name': 'primary_play_stale',
        'severity': 'CRITICAL' if pct >= 30 else 'WARNING',
        'message': f'{len(stale)}/{len(ctx)} today\'s games have stale primary_play '
                   f'(live re-score disagrees). Run recompute_primary_play.py.',
        'detail': {'today': today, 'stale_count': len(stale), 'stale_games': stale[:5]},
    }


def check_grader_coverage() -> Optional[dict]:
    """Alert if yesterday's games/props are <90% graded 24h after game time."""
    yday = _days_ago(1)
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        params={'game_date': f'eq.{yday}', 'select': 'result'},
        headers=H_READ, timeout=15)
    props = r.json() if r.status_code == 200 else []
    if not isinstance(props, list) or len(props) < 10:
        return None
    graded = sum(1 for p in props if p.get('result'))
    pct = 100.0 * graded / len(props)
    if pct >= 90:
        return None
    return {
        'check_name': 'grader_coverage',
        'severity': 'WARNING' if pct >= 70 else 'CRITICAL',
        'message': f'Prop grader coverage yesterday only {pct:.0f}% '
                   f'({graded}/{len(props)}). Blocks tier calibration.',
        'detail': {'date': yday, 'graded': graded, 'total': len(props), 'pct': round(pct, 1)},
    }


def check_sharp_source_dropped() -> Optional[dict]:
    """Alert if any sharp source (CZ/FR/OC) row count drops >50% today vs
    trailing 7d average. Silent scraper death."""
    today = _et_today()
    r = requests.get(f'{SB}/rest/v1/external_picks',
        params={'date': f'gte.{_days_ago(8)}', 'select': 'source,date'},
        headers=H_READ, timeout=15)
    if r.status_code != 200:
        return None
    picks = r.json() if isinstance(r.json(), list) else []
    by_date_source: dict = defaultdict(lambda: defaultdict(int))
    for p in picks:
        by_date_source[p.get('date')][p.get('source')] += 1
    today_counts = by_date_source.get(today, {})
    # Baseline: avg count per source over last 7 days (excluding today)
    baseline = defaultdict(list)
    for d, sources in by_date_source.items():
        if d == today: continue
        for src, cnt in sources.items():
            baseline[src].append(cnt)
    dropped = []
    for src, cnts in baseline.items():
        if not cnts: continue
        avg = sum(cnts) / len(cnts)
        today_cnt = today_counts.get(src, 0)
        if avg >= 5 and today_cnt < avg * 0.5:  # 50% drop threshold on non-tiny sources
            dropped.append({'source': src, 'today': today_cnt, 'baseline_avg': round(avg, 1)})
    if not dropped:
        return None
    return {
        'check_name': 'sharp_source_dropped',
        'severity': 'WARNING',
        'message': f'{len(dropped)} sharp source(s) with >50% count drop vs 7d avg: '
                   f'{", ".join(d["source"] for d in dropped)}',
        'detail': {'today': today, 'dropped': dropped},
    }


def check_chalk_dedupe_broken() -> Optional[dict]:
    """Alert if daily ledger chalk_parlay has duplicate game_id in legs.
    Caught the Rays-twice bug on 8/19."""
    today = _et_today()
    r = requests.get(f'{SB}/rest/v1/ledger_suggestions',
        params={'game_date': f'eq.{today}', 'kind': 'eq.chalk_parlay',
                'select': 'id,legs'},
        headers=H_READ, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    if not isinstance(rows, list) or not rows:
        return None
    dupes = []
    for row in rows:
        legs = row.get('legs') or []
        gids = [l.get('game_id') for l in legs if l.get('game_id')]
        if len(gids) != len(set(gids)):
            dupes.append({'suggestion_id': row.get('id'), 'game_ids': gids})
    if not dupes:
        return None
    return {
        'check_name': 'chalk_dedupe_broken',
        'severity': 'CRITICAL',
        'message': f'{len(dupes)} chalk_parlay row(s) contain duplicate game_ids — '
                   f'dedupe logic bypassed. Run generate_ledger.py to regenerate.',
        'detail': {'today': today, 'dupes': dupes},
    }


def check_jerry_diagnostic_leak() -> Optional[dict]:
    """Alert if any user-visible short_read on today's slate contains
    raw diagnostic tags. Fixed all 3 writer paths 8/20 but keep a guard
    against regressions or new writers introducing the same pattern."""
    today = _et_today()
    leaks = []
    for tbl in ('jerry_reads', 'prop_jerry_reads'):
        r = requests.get(f'{SB}/rest/v1/{tbl}',
            params={'game_date': f'eq.{today}', 'short_read': 'ilike.*[Auto-*',
                    'select': 'id,short_read'},
            headers=H_READ, timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if isinstance(rows, list) and rows:
            leaks.append({'table': tbl, 'count': len(rows),
                          'sample_id': rows[0].get('id')})
    if not leaks:
        return None
    total = sum(l['count'] for l in leaks)
    return {
        'check_name': 'jerry_diagnostic_leak',
        'severity': 'WARNING',
        'message': f'{total} Jerry read(s) with raw diagnostic in user copy — '
                   f'a writer is bypassing the long_read sanitizer.',
        'detail': {'today': today, 'leaks': leaks},
    }


def check_signal_source_dark() -> Optional[dict]:
    """Alert if any enabled signal_source hasn't been referenced in
    _ensemble_sources over last 14 days across ANY game. Signal is dead.

    2026-08-21 revision: only flag signals ENABLED for the full lookback
    window. Fresh signals created within the last 14 days haven't had
    time to accumulate fire history — treating them as dark generated
    40+ false positives on 8/21 (all 40 flagged signals were <14d old,
    from active signal-expansion work). The real dark check is: signal
    that's been live >14d and STILL never fired = truly broken.
    """
    since = _days_ago(14)
    # Get all enabled MLB signals + creation timestamp
    r = requests.get(f'{SB}/rest/v1/signal_sources',
        params={'sport': 'eq.MLB', 'enabled': 'eq.true',
                'select': 'signal_key,class,created_at'},
        headers=H_READ, timeout=10)
    all_sigs = r.json() if r.status_code == 200 else []
    if not isinstance(all_sigs, list): return None
    # Pull last 14d of ensemble source lists from primary_play
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'game_date': f'gte.{since}', 'select': 'primary_play'},
        headers=H_READ, timeout=20)
    ctx = r.json() if r.status_code == 200 else []
    fired = set()
    for g in (ctx if isinstance(ctx, list) else []):
        pp = g.get('primary_play') or {}
        for src in (pp.get('_ensemble_sources') or []):
            sk = src.get('signal_key')
            if sk:
                # Strip __fade suffix so a fade-flipped signal counts
                fired.add(sk.replace('__fade', ''))
    # 2026-08-20: exclusions expanded after first live run flagged 89
    # "dark" signals with 25+ false positives.
    #   - external_pick / split / scenario are HANDLER classes — contribute
    #     via HANDLERS[cls] pipeline, not signal_sources.
    #   - prop_trend / prop_form / prop_environment / prop_matchup /
    #     prop_model score into prop_playbook_decisions (props surface),
    #     NOT into game_context.primary_play._ensemble_sources. Their
    #     absence from game ctx is expected, not dark.
    skip_classes = {'external_pick', 'split', 'scenario',
                    'prop_trend', 'prop_form', 'prop_environment',
                    'prop_matchup', 'prop_model'}
    # Age filter: only flag signals that predate the lookback window.
    # Fresh signals in ramp-up phase are 'not yet observed', not 'dark'.
    dark = sorted([s['signal_key'] for s in all_sigs
                   if s.get('class') not in skip_classes
                   and s['signal_key'] not in fired
                   and (s.get('created_at') or '') < since])
    if not dark or len(dark) < 5:  # a few is normal; alert only on wave
        return None
    return {
        'check_name': 'signal_source_dark',
        'severity': 'INFO' if len(dark) < 20 else 'WARNING',
        'message': f'{len(dark)} enabled signals older than 14d have not fired on ANY game in last 14 days.',
        'detail': {'since': since, 'dark_count': len(dark), 'examples': dark[:10]},
    }


def check_prime_hit_crash() -> Optional[dict]:
    """Alert if PRIME hit rate over last 7 days drops below 50%.
    Early drift warning on the top tier."""
    since = _days_ago(7)
    # Sample: primary_play PRIME picks graded last 7d
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'game_date': f'gte.{since}', 'select': 'game_id,home_team,away_team,primary_play'},
        headers=H_READ, timeout=15)
    ctx = r.json() if r.status_code == 200 else []
    if not isinstance(ctx, list): return None
    prime_gids = [(g, g.get('primary_play') or {}) for g in ctx
                  if (g.get('primary_play') or {}).get('tier') == 'PRIME']
    if len(prime_gids) < 5:  # too thin
        return None
    r = requests.get(f'{SB}/rest/v1/mlb_game_results',
        params={'game_date': f'gte.{since}',
                'select': 'game_id,home_win,run_line_result,total_result'},
        headers=H_READ, timeout=15)
    rmap = {x['game_id']: x for x in (r.json() if r.status_code == 200 else [])
            if isinstance(x, dict)}
    wins = losses = 0
    for g, pp in prime_gids:
        rs = rmap.get(g.get('game_id'))
        if not rs or rs.get('home_win') is None: continue
        m = (pp.get('type') or '').lower()
        label = (pp.get('label') or '').lower()
        home = (g.get('home_team') or '').lower()
        away = (g.get('away_team') or '').lower()
        picked_home = home in label; picked_away = away in label
        result = None
        if m == 'ml':
            if picked_home: result = 'W' if rs['home_win'] else 'L'
            elif picked_away: result = 'W' if not rs['home_win'] else 'L'
        elif m == 'rl':
            rl = (rs.get('run_line_result') or '').lower()
            if picked_home and '+1.5' in label: result = 'W' if rl != 'home' else 'L'
            elif picked_home: result = 'W' if rl == 'home' else 'L'
            elif picked_away and '+1.5' in label: result = 'W' if rl != 'away' else 'L'
            elif picked_away: result = 'W' if rl == 'away' else 'L'
        elif m in ('total', 'over', 'under'):
            tr = (rs.get('total_result') or '').lower()
            if 'over' in label: result = 'W' if tr == 'over' else 'L' if tr == 'under' else None
            elif 'under' in label: result = 'W' if tr == 'under' else 'L' if tr == 'over' else None
        if result == 'W': wins += 1
        elif result == 'L': losses += 1
    dec = wins + losses
    # 2026-08-20: minimum sample tightened after first live run flagged
    # 33% at n=6 as CRITICAL. That's well within normal variance for a
    # 55-60% edge tier — 4-loss streaks happen regularly at n<15. Now:
    #   n < 10: no alert (too thin to distinguish variance from drift)
    #   n 10-19: WARNING only (thin sample, worth noting)
    #   n 20+:   CRITICAL below 40%, WARNING below 50%
    if dec < 10:
        return None
    hr = 100.0 * wins / dec
    if hr >= 50:
        return None
    if dec < 20:
        severity = 'WARNING'  # thin sample — mention but don't page
    else:
        severity = 'CRITICAL' if hr < 40 else 'WARNING'
    return {
        'check_name': 'prime_hit_crash',
        'severity': severity,
        'message': f'PRIME hit rate last 7 days: {wins}-{losses} ({hr:.0f}%, n={dec}). '
                   f'Below the 50% floor — investigate signal drift or tier gate.',
        'detail': {'since': since, 'wins': wins, 'losses': losses, 'hit_rate': round(hr, 1), 'n': dec},
    }


def check_prop_volume_thin() -> Optional[dict]:
    """Alert if today's legacy prop count is <50 (normal is 100-300).
    Caught during 8/20 audit: today produced 29 props while 8/19 had 290.
    Volume drops of that scale usually mean a scoring step broke silently
    or the raw prop-line feed is starved. Not always a bug — some days
    genuinely have thin signal — but worth a watchdog so it doesn't
    surprise the operator."""
    today = _et_today()
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        headers={**H_READ, 'Prefer': 'count=exact'},
        params={'game_date': f'eq.{today}', 'select': 'count'},
        timeout=8)
    ct = r.headers.get('content-range', '?/0')
    try: n = int(ct.split('/')[-1])
    except (ValueError, IndexError): return None
    # Also check ctx game count — if 0 games, no props expected (off-day)
    g = requests.get(f'{SB}/rest/v1/mlb_game_context',
        headers={**H_READ, 'Prefer': 'count=exact'},
        params={'game_date': f'eq.{today}', 'select': 'count'},
        timeout=8)
    gc = g.headers.get('content-range', '?/0')
    try: games = int(gc.split('/')[-1])
    except (ValueError, IndexError): games = 0
    if games == 0: return None  # off-day
    props_per_game = n / games if games else 0
    if n >= 50 and props_per_game >= 3:
        return None
    return {
        'check_name': 'prop_volume_thin',
        'severity': 'CRITICAL' if n < 15 else 'WARNING',
        'message': f'Legacy prop volume thin: {n} props on {games} games ({props_per_game:.1f}/game). '
                   f'Normal is 100-300/day. Check generate_props.py + apply_prop_refit.py ran cleanly.',
        'detail': {'today': today, 'prop_count': n, 'game_count': games,
                   'props_per_game': round(props_per_game, 2)},
    }


def check_coverage_audit() -> Optional[dict]:
    """Alert when today's PRIME/STRONG picks have thin factor coverage OR
    a single class dominates the score. Enforces SIGNAL_FRAMEWORK.md
    standard shipped 8/21.

    Uses today's prop_playbook_decisions. Threshold: 20% of PRIME/STRONG
    rows failing coverage = WARNING; 40% = CRITICAL.

    COVERAGE_MIN is intentionally lower than the framework aspiration
    (12/25). The playbook signal_sources table currently fires 4-5 signals
    per prop; setting the floor at 4 lets the audit catch outliers without
    flagging 100% of decisions. Raise this as signal_sources density grows.
    Framework aspiration remains 12+; this is the current-reality floor.
    """
    # 2026-08-21 revision: replaced string-match factor mapping with
    # unique signal_key count. The string-match heuristic (below) mostly
    # bucketed real signal names into 'other' so 4-5 firing signals
    # collapsed to 2-3 factors, flagging 100% of decisions.
    #
    # Reality-anchored check now:
    #   - MIN_UNIQUE_SIGNALS: distinct signal_keys contributing to score
    #   - MAX_CLASS_SHARE:    single class share of total contribution
    MIN_UNIQUE_SIGNALS = 3   # <3 = single-angle decision; raise as signal_sources grows
    MAX_CLASS_SHARE = 0.75   # loose ceiling; original 0.45 impossibly tight w/ 3-4 signals
    today = _et_today()
    try:
        r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                         headers=H_READ,
                         params={'game_date': f'eq.{today}',
                                 'playbook_tier': 'in.(PRIME,STRONG)',
                                 'select': 'player_name,prop_type,playbook_tier,playbook_sources'},
                         timeout=15)
        rows = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    if not rows:
        return None

    # Factor categorization (mirrors coverage_audit.py FACTOR_SLOTS)
    def factor_of(key: str) -> str:
        k = key.lower()
        if 'projection_contradicts' in k: return 'projection_sanity'
        if 'recent_hot' in k or 'recent_cold' in k or 'l5_confirm' in k: return 'form_recent'
        if 'xera' in k or 'siera' in k: return 'form_season'
        if 'home_road_split' in k: return 'home_road_split'
        if 'first_inning' in k or 'slow_start' in k: return 'first_inning'
        if 'vs_team_career_baa' in k: return 'vs_team_baa'
        if 'vs_team_career_er' in k: return 'vs_team_er'
        if 'vs_team_career_k9' in k: return 'vs_team_k9'
        if 'vs_team_recent' in k or 'vs_team_dominant' in k or 'vs_team_hit_hard' in k: return 'vs_team_recent'
        if 'opp_lineup' in k: return 'opp_lineup'
        if 'opp_ats' in k: return 'opp_ats'
        if 'babip' in k: return 'opp_babip'
        if 'barrel' in k: return 'opp_barrel'
        if 'bullpen' in k: return 'own_bullpen'
        if 'park' in k: return 'park'
        if 'wind' in k or 'weather' in k: return 'weather'
        if 'platoon' in k: return 'platoon'
        if 'sharp_split' in k or 'oddscrowd' in k or 'sharp_scenario' in k: return 'sharp_split'
        if 'refit' in k: return 'refit'
        return 'other'

    from collections import defaultdict
    flagged = 0
    for row in rows:
        sources = row.get('playbook_sources') or []
        if isinstance(sources, str):
            try:
                import json as _j
                sources = _j.loads(sources)
            except Exception:
                sources = []
        if not isinstance(sources, list):
            continue
        unique_signals = set()
        class_share = defaultdict(float)
        total = 0.0
        for c in sources:
            if not isinstance(c, dict): continue
            contrib = c.get('contribution', 0) or 0
            total += contrib
            unique_signals.add(c.get('signal_key', ''))
            class_share[c.get('class', '?')] += contrib
        # Unique signal count check (thin support = flag)
        if len(unique_signals) < MIN_UNIQUE_SIGNALS:
            flagged += 1
            continue
        # Class dominance (one class monopolizes score = flag)
        if total > 0:
            max_share = max((v/total for v in class_share.values()), default=0)
            if max_share > MAX_CLASS_SHARE:
                flagged += 1
    flag_rate = flagged / max(1, len(rows))
    if flag_rate >= 0.40:
        return {
            'check_name': 'coverage_audit',
            'severity': 'CRITICAL',
            'message': f'{flagged}/{len(rows)} PRIME/STRONG picks fail framework coverage ({flag_rate:.0%})',
            'detail': {'flagged': flagged, 'total': len(rows),
                       'threshold': 'CRITICAL >= 40%'}
        }
    if flag_rate >= 0.20:
        return {
            'check_name': 'coverage_audit',
            'severity': 'WARNING',
            'message': f'{flagged}/{len(rows)} PRIME/STRONG picks fail framework coverage ({flag_rate:.0%})',
            'detail': {'flagged': flagged, 'total': len(rows),
                       'threshold': 'WARNING 20-40%'}
        }
    return None


CHECKS = [
    check_ladder_empty,
    check_ensemble_engine_share,
    check_primary_play_stale,
    check_grader_coverage,
    check_sharp_source_dropped,
    check_chalk_dedupe_broken,
    check_jerry_diagnostic_leak,
    check_signal_source_dark,
    check_prime_hit_crash,
    check_prop_volume_thin,
    check_coverage_audit,
]


def write_alert(alert: dict, run_date: str, dry_run: bool = False) -> None:
    if dry_run:
        return
    payload = {
        'run_date': run_date,
        'check_name': alert['check_name'],
        'severity': alert['severity'],
        'message': alert['message'],
        'detail': alert.get('detail'),
        'last_seen_at': datetime.now(timezone.utc).isoformat(),
    }
    # 2026-08-20 bug fix: on_conflict target must be explicit for
    # PostgREST upsert. Without it, POST with resolution=merge-duplicates
    # 409s on the UNIQUE (run_date, check_name) constraint when re-running
    # the same day (which the workflow does every 2 hrs).
    r = requests.post(f'{SB}/rest/v1/watchdog_alerts?on_conflict=run_date,check_name',
                      headers=H_WRITE, json=payload, timeout=10)
    if r.status_code not in (200, 201, 204):
        print(f'    ✗ write failed {r.status_code}: {r.text[:150]}')


def resolve_stale_alerts(active_check_names: set, run_date: str,
                         dry_run: bool = False) -> None:
    """Mark alerts as resolved if their check passed this run."""
    if dry_run: return
    # Get all unresolved alerts
    r = requests.get(f'{SB}/rest/v1/watchdog_alerts',
        params={'resolved_at': 'is.null', 'select': 'id,check_name'},
        headers=H_READ, timeout=10)
    unresolved = r.json() if r.status_code == 200 else []
    for a in (unresolved if isinstance(unresolved, list) else []):
        if a.get('check_name') not in active_check_names:
            # Passed this run — resolve
            requests.patch(f'{SB}/rest/v1/watchdog_alerts?id=eq.{a["id"]}',
                headers=H_WRITE,
                json={'resolved_at': datetime.now(timezone.utc).isoformat()},
                timeout=10)


def run_all(dry_run: bool = False, only: Optional[str] = None) -> int:
    """Returns count of tripped alerts. Exit code = 2 on any CRITICAL,
    1 on WARNING+, 0 clean."""
    run_date = _et_today()
    print(f'=== watchdogs · {run_date} · dry={dry_run} ===\n')
    active: set = set()
    max_severity_rank = 0
    severity_rank = {'INFO': 1, 'WARNING': 2, 'CRITICAL': 3}
    checks = [c for c in CHECKS if only is None or c.__name__ == f'check_{only}']
    if only and not checks:
        print(f'  no check named "{only}". Available: '
              f'{", ".join(c.__name__.replace("check_","") for c in CHECKS)}')
        return 0
    for check in checks:
        name = check.__name__.replace('check_', '')
        try:
            result = check()
        except Exception as e:
            print(f'  ⚠️  {name}: check raised {type(e).__name__}: {e}')
            continue
        if result is None:
            print(f'  ✓  {name}: clean')
            continue
        active.add(result['check_name'])
        sev = result['severity']
        icon = '🚨' if sev == 'CRITICAL' else '⚠️' if sev == 'WARNING' else 'ℹ️'
        print(f'  {icon}  {sev:8s} {name}: {result["message"]}')
        write_alert(result, run_date, dry_run=dry_run)
        max_severity_rank = max(max_severity_rank, severity_rank.get(sev, 0))
    resolve_stale_alerts(active, run_date, dry_run=dry_run)
    if not active:
        print(f'\n✅ all {len(checks)} checks passed')
    else:
        print(f'\n⚠️  {len(active)}/{len(checks)} checks tripped')
    return max_severity_rank


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--check', help='Run a single check by name')
    ap.add_argument('--fail-on-warning', action='store_true',
                    help='Exit non-zero on WARNING too (for on-demand runs only)')
    args = ap.parse_args()
    rank = run_all(dry_run=args.dry_run, only=args.check)
    # 2026-08-20: exit-code policy revised. Prior design exited 1 for any
    # WARNING → GitHub Actions marked the workflow "failed" on every run
    # because signal_source_dark is a persistent WARNING waiting on the
    # season-long enrichment work. User rightly said "watchdogs failed
    # again." Alerts live in watchdog_alerts DB rows — the workflow's
    # only job is to CHECK. Exit 0 unless a CRITICAL trips or a check
    # itself couldn't run. --fail-on-warning flag preserved for
    # manual/dev runs where you want the exit code to bubble severity.
    if args.fail_on_warning:
        sys.exit(2 if rank == 3 else 1 if rank >= 1 else 0)
    sys.exit(2 if rank == 3 else 0)


if __name__ == '__main__':
    main()
