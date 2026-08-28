#!/usr/bin/env python3
"""jerry_pick_scrub — force jerry_reads.call_* to match ctx.primary_play.

The Jerry LLM writes prose + emits its own call_side / call_text at
Jerry-generation time. If ctx.primary_play is later recomputed (MC
refresh, close-line update, defensive gate flip), Jerry's stored
call_side can diverge from the mechanical ensemble pick. Result: badge
shows one team, analysis shows another.

This scrub sweeps today's jerry_reads for any game whose primary_play
disagrees with the stored jerry_reads.call_side/market/line and
overwrites the CALL fields deterministically. The LLM prose
(short_read / long_read) is left as-is — it may argue for a stale
pick, but the badge + Sharp Card grading tie to primary_play so
downstream displays are consistent.

Sport-universal via SPORT_CONFIG.

CLI:
    python jerry_pick_scrub.py                    # today, all sports
    python jerry_pick_scrub.py --sport MLB
    python jerry_pick_scrub.py --date 2026-08-27
    python jerry_pick_scrub.py --games GID1,GID2  # regen these specifically
    python jerry_pick_scrub.py --dry-run

Runs in cron AFTER recompute_primary_play so any pick flip cascades to
Jerry's display fields on the same run. Also runs standalone in the
rescue block as a belt-and-suspenders check.
"""

from __future__ import annotations
import argparse, os, sys, datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

SPORT_CONFIG = {
    'MLB':   {'ctx': 'mlb_game_context'},
    'NFL':   {'ctx': 'nfl_game_context'},
    'NCAAF': {'ctx': 'ncaaf_game_context'},
    'NBA':   {'ctx': 'nba_game_context'},
    'NHL':   {'ctx': 'nhl_game_context'},
    'NCAAB': {'ctx': 'ncaab_game_context'},
}


def _derive_call_text(pp: dict, home_team: str, away_team: str) -> str | None:
    """Deterministic call_text from primary_play + team names."""
    ptype = (pp.get('type') or '').lower()
    side  = (pp.get('side') or '').upper()
    line  = pp.get('line')
    label = pp.get('label')
    if label and label.strip():
        return label.strip()
    if ptype == 'ml':
        if side == 'HOME': return f'{home_team} ML'
        if side == 'AWAY': return f'{away_team} ML'
    if ptype == 'total' and side in ('OVER', 'UNDER'):
        return f'{side.capitalize()}' + (f' {line}' if line is not None else '')
    if ptype == 'rl' and side in ('HOME', 'AWAY'):
        team = home_team if side == 'HOME' else away_team
        return f'{team} RL' + (f' {line}' if line is not None else '')
    return None


def scrub_sport(sport: str, gd: str, game_ids: list[str] | None = None,
                dry_run: bool = False) -> tuple[int, int]:
    """Return (checked, fixed)."""
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        return (0, 0)
    # Fetch ctx rows for today (or specified games)
    ctx_filter = f'game_date=eq.{gd}&primary_play=not.is.null'
    if game_ids:
        ids = ','.join(f'"{g}"' for g in game_ids)
        ctx_filter = f'game_id=in.({ids})'
    r = requests.get(
        f'{SB}/rest/v1/{cfg["ctx"]}?{ctx_filter}'
        '&select=game_id,home_team,away_team,primary_play',
        headers=H_READ, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: ctx fetch failed {r.status_code}')
        return (0, 0)
    ctx_rows = r.json() or []
    if not ctx_rows:
        print(f'  {sport} {gd}: no ctx rows with primary_play')
        return (0, 0)
    ctx_by_gid = {c['game_id']: c for c in ctx_rows}

    # Fetch jerry_reads for those games
    ids = ','.join(f'"{g}"' for g in ctx_by_gid.keys())
    r = requests.get(
        f'{SB}/rest/v1/jerry_reads?sport=eq.{sport}&game_id=in.({ids})'
        '&select=id,game_id,call_market,call_side,call_line,call_text',
        headers=H_READ, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: jerry_reads fetch failed {r.status_code}')
        return (0, 0)
    jerry_rows = r.json() or []

    fixed = 0
    for j in jerry_rows:
        c = ctx_by_gid.get(j['game_id'])
        if not c: continue
        pp = c.get('primary_play') or {}
        pp_type = (pp.get('type') or '').lower()
        pp_side = (pp.get('side') or '').upper()
        pp_line = pp.get('line')
        j_market = (j.get('call_market') or '').lower()
        j_side   = (j.get('call_side') or '').upper()
        j_line   = j.get('call_line')

        # Skip when primary_play is a non-standard market (nrfi/yrfi/fight)
        # that jerry_reads' schema also accepts — those tables agree already.
        if pp_type not in ('ml', 'rl', 'total'): continue

        # Detect drift
        drift = (j_market != pp_type or j_side != pp_side or
                 (pp_line is not None and j_line != pp_line))
        if not drift: continue

        new_text = _derive_call_text(pp, c['home_team'], c['away_team'])
        payload = {
            'call_market': pp_type,
            'call_side':   pp_side,
            'call_line':   pp_line,
        }
        if new_text:
            payload['call_text'] = new_text

        matchup = f'{c["away_team"][:14]:14s} @ {c["home_team"][:14]:14s}'
        print(f'  DRIFT {matchup}  '
              f'{j_market}/{j_side} {j.get("call_text","?")[:20]} -> '
              f'{pp_type}/{pp_side} {new_text}')

        if dry_run:
            fixed += 1
            continue

        pr = requests.patch(
            f'{SB}/rest/v1/jerry_reads?id=eq.{j["id"]}',
            headers=H_WRITE, json=payload, timeout=15,
        )
        if pr.status_code in (200, 204):
            fixed += 1
        else:
            print(f'    patch failed {pr.status_code}: {pr.text[:150]}')
    return (len(jerry_rows), fixed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['ALL'] + list(SPORT_CONFIG.keys()),
                    default='ALL')
    ap.add_argument('--date', help='YYYY-MM-DD (default today ET)')
    ap.add_argument('--games', help='Comma-separated game_ids to scrub (overrides --date)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    gd = args.date or dt.date.today().isoformat()
    game_ids = args.games.split(',') if args.games else None
    sports = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]

    print(f'=== jerry_pick_scrub · {gd} · {"/".join(sports)}{" [DRY]" if args.dry_run else ""} ===')
    total_c = total_f = 0
    for sp in sports:
        c, f = scrub_sport(sp, gd, game_ids, dry_run=args.dry_run)
        if c or f:
            print(f'  {sp}: checked {c}, fixed {f}')
        total_c += c; total_f += f
    print(f'DONE - checked {total_c}, fixed {total_f}')


if __name__ == '__main__':
    main()
