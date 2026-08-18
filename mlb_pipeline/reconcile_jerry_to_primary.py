"""Reconcile jerry_reads.call_side to mlb_game_context.primary_play (2026-08-18).

Fixes the UI mismatch where a game's tier pill above the analysis shows
"HOME ML" (from primary_play) but Jerry's prose below argues for the
opposite team (jerry_reads.call_side='AWAY'). Two separate scoring paths
(play_of_day ensemble vs Jerry synthesis LLM) diverged; the app renders
both without reconciliation and users see conflicting text.

Rule (ensemble_v2 authority):
  When primary_play._engine == 'ensemble_v2' AND primary_play.side is set,
  the ensemble is the DECISION authority. Jerry's job is to narrate that
  pick. If Jerry.call_side contradicts, we force Jerry.call_side to match
  primary_play.side and stamp audit_notes with the flip so history is
  transparent. Prose is left alone — the pre-publish audit's other gates
  (numeric integrity, Layer D scrub, trend-contradict) still run and
  will PASS or downgrade if Jerry's prose truly can't be reconciled with
  the flipped side.

Rule (legacy primary_play):
  When _engine != 'ensemble_v2', Jerry can differ (legacy has known
  weaknesses). No force-flip. Log a warning only.

Runs BETWEEN generate_jerry_synthesis.py (writes jerry_reads) and
jerry_pre_publish_audit.py (fails workflow on unhealthy reads). Positioned
so a reconciliation-flipped read still passes the audit's other gates.

CLI:
  python reconcile_jerry_to_primary.py                   # today MLB
  python reconcile_jerry_to_primary.py --dry-run
  python reconcile_jerry_to_primary.py --date 2026-08-18
"""
from __future__ import annotations
import argparse, os, sys, json
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _canonical_side(raw: str | None, home_team: str, away_team: str) -> str | None:
    """Coerce a Jerry call_side to HOME/AWAY. Accepts 'HOME'/'AWAY' verbatim
    or the literal team name."""
    if not raw: return None
    s = str(raw).strip()
    su = s.upper()
    if su in ('HOME', 'AWAY'): return su
    if home_team and s.lower() == home_team.lower(): return 'HOME'
    if away_team and s.lower() == away_team.lower(): return 'AWAY'
    return None


def run(game_date: str | None = None, sport: str = 'MLB', dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== reconcile_jerry_to_primary · {sport} · {gd}{" [DRY]" if dry_run else ""} ===')

    # 1. Load primary_play per game
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H_READ,
        params={'game_date': f'eq.{gd}',
                'select': 'game_id,home_team,away_team,primary_play'},
        timeout=20)
    games = r.json() if r.status_code == 200 else []
    if not games:
        print(f'  no games — abort'); return
    pp_by_gid = {g['game_id']: g for g in games}
    print(f'  {len(games)} games loaded')

    # 2. Load today's Jerry reads on side/ml markets
    r = requests.get(
        f'{SB}/rest/v1/jerry_reads',
        headers=H_READ,
        params={'game_date': f'eq.{gd}',
                'sport': f'eq.{sport}',
                'call_market': 'in.(side,ml,moneyline)',
                'select': 'id,game_id,call_market,call_side,conviction,audit_notes'},
        timeout=20)
    reads = r.json() if r.status_code == 200 else []
    print(f'  {len(reads)} jerry_reads to check')

    flips = mismatches_legacy = ok = missing_pp = 0
    for jr in reads:
        gid = jr.get('game_id')
        g = pp_by_gid.get(gid)
        if not g:
            missing_pp += 1; continue
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict):
            missing_pp += 1; continue
        pp_side = pp.get('side')  # 'HOME' | 'AWAY' | None
        engine = pp.get('_engine') or ''
        if not pp_side:
            # Legacy path with no side field — skip. The empty-side bug is
            # handled separately; force-flipping to nothing helps nobody.
            continue

        home = g.get('home_team') or ''
        away = g.get('away_team') or ''
        jerry_side = _canonical_side(jr.get('call_side'), home, away)
        if jerry_side is None:
            # Jerry wrote something we can't parse (a team we don't recognize).
            continue

        if jerry_side == pp_side.upper():
            ok += 1
            continue

        # Divergence. Only force-flip on ensemble_v2 authority.
        if engine != 'ensemble_v2':
            mismatches_legacy += 1
            print(f'  ~ legacy divergence (log only): {away}@{home} '
                  f'pp={pp_side} jerry={jerry_side} engine={engine or "?"}')
            continue

        # Flip Jerry to match ensemble authority.
        new_side = pp_side.upper()
        stamp = (f'[reconcile 2026-08-18 → forced call_side {jerry_side}→{new_side} '
                 f'to match primary_play (ensemble_v2 authority). '
                 f'Original Jerry call_side stored above.]')
        existing_notes = jr.get('audit_notes') or ''
        new_notes = (existing_notes + '\n' + stamp).strip()[:1500]

        payload = {'call_side': new_side, 'audit_notes': new_notes}
        if not dry_run:
            pr = requests.patch(
                f'{SB}/rest/v1/jerry_reads?id=eq.{jr["id"]}',
                headers=H_WRITE, json=payload, timeout=10)
            if pr.status_code not in (200, 204):
                print(f'  x patch id={jr["id"]} failed: {pr.status_code} {pr.text[:150]}')
                continue
        flips += 1
        print(f'  = flipped id={jr["id"]}: {away}@{home}  '
              f'{jerry_side} -> {new_side}  (ensemble pick)')

    print()
    print(f'  summary: aligned={ok}  flipped={flips}  '
          f'legacy-divergence={mismatches_legacy}  no-pp={missing_pp}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport', default='MLB')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, sport=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
