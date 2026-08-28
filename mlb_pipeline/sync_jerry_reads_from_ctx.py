#!/usr/bin/env python3
"""sync_jerry_reads_from_ctx — bridge script for sports where the Jerry
LLM writes prose to jerry_cache but never populates jerry_reads.

Root cause (2026-08-28): NCAAF/NHL/NBA/UFC prompt templates emit narrative
prose only — no structured CALL block (MARKET/SIDE/LINE/CALL_TEXT). So
parse_synthesis returns empty and dual-write to jerry_reads never fires.
App reads jerry_reads for the badge and shows placeholder text.

This script bridges the gap:
  - For each game with `primary_play` on ctx
  - Read the narrative from jerry_cache (`game_read_{gid}_{date}`) if exists
  - UPSERT jerry_reads with call_market/side/line from primary_play (mechanical
    truth) + short_read/long_read from jerry_cache narrative (LLM prose)

Aligns with the Jerry-as-narrator architecture: ensemble is the pick,
Jerry writes prose about it. This is the deterministic version.

Sport-universal. CLI:
  python sync_jerry_reads_from_ctx.py                     # today, all sports
  python sync_jerry_reads_from_ctx.py --sport NCAAF
  python sync_jerry_reads_from_ctx.py --sport NCAAF --date 2026-08-29
  python sync_jerry_reads_from_ctx.py --dry-run
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
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW  = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

SPORT_CONFIG = {
    'NCAAF': {'ctx': 'ncaaf_game_context'},
    'NFL':   {'ctx': 'nfl_game_context'},
    'NCAAB': {'ctx': 'ncaab_game_context'},
    'NBA':   {'ctx': 'nba_game_context'},
    'NHL':   {'ctx': 'nhl_game_context'},
}


def _derive_call_text(pp: dict, home: str, away: str) -> str | None:
    if not pp: return None
    ptype = (pp.get('type') or '').lower()
    side  = (pp.get('side') or '').upper()
    line  = pp.get('line')
    label = pp.get('label')
    if label: return label
    if ptype == 'ml':
        if side == 'HOME': return f'{home} ML'
        if side == 'AWAY': return f'{away} ML'
    if ptype == 'total' and side in ('OVER','UNDER'):
        return f'{side.capitalize()}' + (f' {line}' if line is not None else '')
    if ptype in ('rl','spread') and side in ('HOME','AWAY'):
        team = home if side == 'HOME' else away
        return f'{team} ' + (f'{line}' if line is not None else '')
    return None


def _short_from_narrative(narrative: str, max_chars: int = 200) -> str | None:
    if not narrative: return None
    txt = narrative.strip()
    # First sentence, or first N chars if no period
    end = txt.find('. ')
    if 0 < end < max_chars: return txt[:end+1]
    return txt[:max_chars].rstrip() + ('…' if len(txt) > max_chars else '')


def sync_sport(sport: str, gd: str, dry: bool = False) -> tuple[int, int, int]:
    cfg = SPORT_CONFIG[sport]
    r = requests.get(
        f'{SB}/rest/v1/{cfg["ctx"]}',
        params={'game_date': f'eq.{gd}', 'primary_play': 'not.is.null',
                'select': 'game_id,home_team,away_team,primary_play'},
        headers=H, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: ctx fetch failed {r.status_code}')
        return (0, 0, 0)
    ctx_rows = r.json() or []
    if not ctx_rows:
        print(f'  {sport} {gd}: no ctx rows with primary_play')
        return (0, 0, 0)

    checked = wrote = skipped = 0
    for c in ctx_rows:
        checked += 1
        gid = c['game_id']
        home = c.get('home_team', ''); away = c.get('away_team', '')
        pp = c.get('primary_play') or {}
        ptype = (pp.get('type') or '').lower()
        side  = (pp.get('side') or '').upper()
        if ptype not in ('ml', 'spread', 'rl', 'total'):
            skipped += 1
            continue

        # Try to find narrative from jerry_cache. cache_key format is
        # game_read_{game_id}_{generation_date} — generation_date != game_date
        # (LLM often runs day-of-writing not game-day). Match by prefix and
        # take the most recent narrative for this game_id.
        prefix = f'game_read_{gid}_'
        cr = requests.get(
            f'{SB}/rest/v1/jerry_cache',
            params={'cache_key': f'like.{prefix}%', 'sport': f'eq.{sport}',
                    'select': 'narrative,fetched_at',
                    'order': 'fetched_at.desc', 'limit': '1'},
            headers=H, timeout=15,
        )
        narrative = None
        if cr.status_code == 200 and cr.json():
            narrative = cr.json()[0].get('narrative')

        call_text = _derive_call_text(pp, home, away)
        short_read = _short_from_narrative(narrative) if narrative else \
                     'Analysis pending — Jerry is reviewing the tape.'

        payload = {
            'sport': sport,
            'game_id': gid,
            'game_date': gd,
            'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'prompt_version': f'{sport.lower()}_ctx_bridge_v1',
            'call_market': ptype,
            'call_side':   side or None,
            'call_line':   pp.get('line'),
            'call_text':   call_text,
            'short_read':  short_read,
            'long_read':   narrative,
            'conviction':  int(pp.get('conviction') or 0) if pp.get('conviction') else None,
        }
        matchup = f'{away[:14]} @ {home[:14]}'
        if dry:
            print(f'  DRY {matchup}: {ptype}/{side} "{call_text}"  narrative={bool(narrative)}')
            wrote += 1
            continue
        r = requests.post(
            f'{SB}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
            headers=HW, json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            wrote += 1
            print(f'  ✓ {matchup}: {call_text}  ({sport})')
        else:
            print(f'  ✗ {matchup}: {r.status_code} {r.text[:150]}')
    return (checked, wrote, skipped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['ALL'] + list(SPORT_CONFIG),
                    default='ALL')
    ap.add_argument('--date', help='YYYY-MM-DD (default: today ET). Use --window N to sweep N days ahead too.')
    ap.add_argument('--window', type=int, default=1,
                    help='Sweep from --date forward N days (default 1 = just that date)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = args.date or (dt.datetime.utcnow() - dt.timedelta(hours=4)).date().isoformat()
    y,m,d = (int(x) for x in base.split('-'))
    base_d = dt.date(y,m,d)
    dates = [(base_d + dt.timedelta(days=i)).isoformat() for i in range(args.window)]
    sports = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]

    print(f'=== sync_jerry_reads_from_ctx · {sports} · {dates[0]}..{dates[-1]}{" [DRY]" if args.dry_run else ""} ===')
    for gd in dates:
        for sp in sports:
            c, w, s = sync_sport(sp, gd, dry=args.dry_run)
            if c or w:
                print(f'  {sp} {gd}: checked={c} wrote={w} skipped={s}')


if __name__ == '__main__':
    main()
