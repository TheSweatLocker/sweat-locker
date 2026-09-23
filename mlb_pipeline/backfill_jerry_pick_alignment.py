"""One-shot backfill: force every current jerry_reads row to align with
ensemble primary_play.

Runs the same rule as enforce_primary_play_alignment() from
jerry_reads_dual_write.py, applied AFTER-THE-FACT to rows already written.
Fixes the launch-weekend inventory: BAL@IND, BUF@HOU, and every other
soft-signal game where the badge and the prose disagreed.

CLI:
    python backfill_jerry_pick_alignment.py                # all sports, next 7 days
    python backfill_jerry_pick_alignment.py --sport NFL    # single sport
    python backfill_jerry_pick_alignment.py --dry-run
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

_CTX_TABLE = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA': 'nba_game_context',
    'NCAAB': 'ncaab_game_context',
    'NHL': 'nhl_game_context',
}
_VALID_MARKETS_BY_SPORT = {
    'MLB':   {'ml', 'rl', 'total', 'nrfi', 'yrfi'},
    'NFL':   {'ml', 'rl', 'spread', 'total'},
    'NCAAF': {'ml', 'rl', 'spread', 'total'},
    'NBA':   {'ml', 'rl', 'spread', 'total'},
    'NCAAB': {'ml', 'rl', 'spread', 'total'},
    'NHL':   {'ml', 'rl', 'puckline', 'total'},
    'UFC':   {'ml', 'fight'},
}


def load_ctx(sport: str, date_from: str, date_to: str) -> dict:
    tbl = _CTX_TABLE.get(sport)
    if not tbl: return {}
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
        params={'game_date': f'gte.{date_from}',
                'and': f'(game_date.lte.{date_to})',
                'select': 'game_id,home_team,away_team,primary_play',
                'limit': '600'}, timeout=25)
    out = {}
    for row in (r.json() if isinstance(r.json(), list) else []):
        if row.get('game_id'):
            out[row['game_id']] = row
    return out


def load_reads(sport: str, date_from: str, date_to: str) -> list:
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': f'eq.{sport}',
                'game_date': f'gte.{date_from}',
                'and': f'(game_date.lte.{date_to})',
                'select': 'id,game_id,game_date,call_market,call_side,call_line,call_text,conviction,short_read,long_read',
                'limit': '600'}, timeout=25)
    return r.json() if isinstance(r.json(), list) else []


_STARTED_STATES = {'Final', 'In Progress', 'Game Over',
                   'Completed Early', 'Suspended', 'Delayed'}
_STARTED_CACHE: dict = {}


def started_matchups(sport: str, date_str: str) -> set:
    """Matchups on this date whose game has already begun.

    2026-09-23 — B12. This script re-settles the CALL fields on an
    existing read. Nothing stopped it doing that to a game that had
    already been played, and on 09-23 that cost a real result:
    Washington @ Detroit carried "Under 7.5", finished 4-2 for a total
    of 6, and was rewritten to Pass/conviction 0 three hours after the
    final out. A pass grades NO_ACTION, so a winning read silently left
    the record.

    The publish lock does not cover this. It is keyed on time-of-day
    (MLB freezes at 12:00 ET), which happens to protect a finished game
    at 16:35 and protects nothing at all at 11:00 for a game that
    started at 10:05.

    Only MLB and NBA are wired here; other sports fall through and are
    unprotected until their schedule source is added. The function
    returns an empty set on any failure, which means alignment proceeds
    as before — a lookup outage must not block the normal path, it just
    loses the protection for that run, and the caller says so.
    """
    key = (sport, date_str)
    if key in _STARTED_CACHE:
        return _STARTED_CACHE[key]
    out = set()
    if sport == 'MLB':
        try:
            import urllib.request as _u
            raw = json.load(_u.urlopen(
                'https://statsapi.mlb.com/api/v1/schedule'
                f'?sportId=1&date={date_str}', timeout=20))
            for d in raw.get('dates', []):
                for g in d.get('games', []):
                    st = (g.get('status') or {}).get('detailedState', '')
                    if st in _STARTED_STATES:
                        out.add((g['teams']['away']['team']['name'],
                                 g['teams']['home']['team']['name']))
        except Exception as e:
            print(f'  ⚠ {sport} start-state lookup failed ({type(e).__name__}) '
                  f'— alignment NOT protected against started games this run')
            out = set()
    _STARTED_CACHE[key] = out
    return out


def align_row(sport: str, read_row: dict, pp: dict, ctx_home: str, ctx_away: str,
              line_only: bool = False) -> dict | None:
    """Return the PATCH payload or None if no change needed.

    line_only mode (2026-09-18 per Andy B choice on item #5):
      Only patch call_line + call_text IF the underlying side/market
      match. Rejects any full-pick change. Used post-odds-pull to keep
      jerry in sync with market line drift when week-lock blocks full
      alignment. Same side, same market, different line = fix line
      only. Different side/market = leave alone (that requires full
      alignment cycle, which respects week-lock).
    """
    if not isinstance(pp, dict) or not pp: return None
    market = str(pp.get('type') or '').lower()
    side = pp.get('side')
    label = pp.get('label')
    conviction = pp.get('conviction')
    line = pp.get('line')
    tier = str(pp.get('tier') or '').upper()
    valid_markets = _VALID_MARKETS_BY_SPORT.get(sport.upper(), set())

    if line_only:
        # Bail if pp is soft-tier or invalid market (nothing to align)
        if tier in ('COVERAGE', 'PASS', 'SKIP') or market not in valid_markets or not side or not label:
            return None
        # Require SAME side + SAME market — line-only is a line refresh,
        # never a pick change (those go through full-alignment path).
        cur_side = str(read_row.get('call_side') or '').upper()
        cur_market = str(read_row.get('call_market') or '').lower()
        target_side = str(side).upper()
        if cur_market != market or cur_side != target_side:
            return None  # side or market differs — needs full alignment, not line-only
        # If line + label already match, nothing to do
        patch = {}
        if read_row.get('call_line') != line:
            patch['call_line'] = line
        if read_row.get('call_text') != label:
            patch['call_text'] = label
        return patch or None

    # ── 2026-09-22: DELEGATE, don't duplicate ───────────────────────
    # This block used to reimplement enforce_primary_play_alignment()
    # inline — the docstring at the top of this file even says "runs the
    # same rule as". It was the same rule written twice, so a fix to one
    # copy left the other wrong.
    #
    # That is exactly what happened: the pass→play prose resync was added
    # to the shared function, this copy did not have it, and four MLB
    # games kept shipping a live PRIME badge above "Engine passed — no
    # publishable edge on this game."
    #
    # Now there is one implementation. Anything added there — prose
    # resync, future tier rules — applies here automatically, and the two
    # cannot drift again because the second copy no longer exists.
    from jerry_reads_dual_write import (enforce_primary_play_alignment,
                                        derive_short_read)

    _FIELDS = ('call_market', 'call_side', 'call_line', 'call_text',
               'conviction', 'short_read', 'long_read')
    parsed_in = {k: read_row.get(k) for k in _FIELDS}
    aligned = enforce_primary_play_alignment(
        sport, dict(parsed_in), {'primary_play': pp})

    # 2026-09-22: this path PATCHes jerry_reads directly, so it never hit
    # the short_read guard that upsert_jerry_read applies on the LLM
    # write path. Result was a card showing 52-64 characters of engine
    # output next to games carrying 280 characters of real analysis —
    # Andy: "pre analysis mlb reads not uniform". The full prose was
    # already in long_read on 5 of the 8 affected games; nothing needed
    # regenerating, it just needed reading.
    # NOT on a pass. enforce_primary_play_alignment now writes the
    # "engine passed" line itself, and long_read still argues FOR the
    # pick that was just killed — deriving from it would reinstate the
    # exact contradiction (NO PLAY above "Back the UNDER 8.") this is
    # meant to remove. Thin prose is only a problem on a live pick.
    if str(aligned.get('call_market') or '').lower() != 'pass':
        aligned['short_read'] = derive_short_read(
            aligned.get('short_read'), aligned.get('long_read'))

    patch = {}
    for k in _FIELDS:
        if k not in aligned:
            continue
        if read_row.get(k) != aligned[k]:
            patch[k] = aligned[k]
    return patch or None


def run(sport_filter: str | None, days_ahead: int, dry_run: bool, line_only: bool = False) -> None:
    from datetime import date as _date
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=days_ahead)).isoformat()
    print(f'=== jerry_reads alignment backfill · {date_from} → {date_to}{" · DRY" if dry_run else ""} ===')
    sports = [sport_filter] if sport_filter else ['MLB', 'NFL', 'NCAAF', 'NBA', 'NCAAB', 'NHL']
    total_updated = 0
    for sport in sports:
        # 2026-09-16 NFL week-lock guard. Once past Thursday 8am ET,
        # jerry_reads call_* fields are frozen for the week. Env
        # NFL_UNLOCK_WEEK=1 or --force flag override. Prevents mid-week
        # ensemble drift from flipping the pick out from under the
        # locked writeup (drift → writeup argues X, badge shows Y).
        # See generate_nfl_game_reads.nfl_week_write_locked() for shared
        # semantics; imported inline to keep this script standalone.
        # 2026-09-18 line_only mode bypasses the week-lock — line drift
        # post-lock (market moved, pp.label updated) needs jerry to
        # follow the line WITHOUT allowing side/market changes. The
        # line_only alignment function itself refuses side/market
        # changes, so this bypass is safe.
        if sport == 'NFL' and not line_only:
            try:
                from generate_nfl_game_reads import nfl_week_write_locked
                if nfl_week_write_locked():
                    print(f'  🔒 NFL: week-locked (post Thu 8am) — skipping alignment '
                          f'to preserve locked picks. NFL_UNLOCK_WEEK=1 or --line-only to override.')
                    continue
            except Exception as _e:
                print(f'  ⚠ NFL lock check failed ({_e}) — proceeding with alignment')
        ctx_by_gid = load_ctx(sport, date_from, date_to)
        reads = load_reads(sport, date_from, date_to)
        if not reads:
            print(f'  {sport}: no reads in window'); continue
        aligned = 0; unchanged = 0; skipped = 0; started_skips = 0
        for r in reads:
            gid = r.get('game_id')
            ctx = ctx_by_gid.get(gid) or {}
            pp = ctx.get('primary_play')
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except (TypeError, ValueError): pp = None
            if not isinstance(pp, dict):
                skipped += 1; continue
            patch = align_row(sport, r, pp, ctx.get('home_team',''), ctx.get('away_team',''), line_only=line_only)
            if not patch:
                unchanged += 1; continue
            # B12: never re-settle a game that has already been played.
            # See started_matchups() — on 09-23 this script rewrote a
            # winning read to Pass three hours after the final out.
            _started = started_matchups(sport, str(r.get('game_date'))[:10])
            if (ctx.get('away_team'), ctx.get('home_team')) in _started:
                started_skips += 1
                print(f"  ⏭  {sport} {ctx.get('away_team','?')}@{ctx.get('home_team','?')}: "
                      f"game already started — leaving "
                      f"{r.get('call_text','?')!r} as published")
                continue
            if dry_run:
                aligned += 1
                print(f"  [DRY] {sport} {ctx.get('away_team','?')}@{ctx.get('home_team','?')}: "
                      f"{r.get('call_market')}/{r.get('call_side')}={r.get('call_text')!r} → "
                      f"{patch.get('call_market', r.get('call_market'))}/"
                      f"{patch.get('call_side', r.get('call_side'))}={patch.get('call_text', r.get('call_text'))!r}")
                continue
            pr = requests.patch(f'{SB}/rest/v1/jerry_reads', headers=H_WRITE,
                params={'id': f'eq.{r["id"]}'}, json=patch, timeout=15)
            if pr.status_code in (200, 204):
                aligned += 1
                print(f"  ✓ {sport} {ctx.get('away_team','?')}@{ctx.get('home_team','?')}: "
                      f"{r.get('call_text','?')} → {patch.get('call_text', r.get('call_text'))}"
                      f" ({r.get('call_market')} → {patch.get('call_market', r.get('call_market'))})")
            else:
                skipped += 1
                print(f"  ✗ {sport} gid={gid[:12]}: {pr.status_code} {pr.text[:150]}")
        print(f'  {sport}: aligned={aligned} unchanged={unchanged} '
              f'skipped(no pp)={skipped} skipped(started)={started_skips}')
        total_updated += aligned
    print(f'\n=== total aligned: {total_updated} ===')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=['MLB','NFL','NCAAF','NBA','NCAAB','NHL'])
    p.add_argument('--days', type=int, default=14)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--line-only', action='store_true',
                   help='Line-only mode: patch jerry.call_line + call_text ONLY when '
                        'pp.side/market match jerry (line drifted). Bypasses NFL week-lock '
                        'since it never allows a pick change. Use post-odds-pull.')
    args = p.parse_args()
    run(args.sport, args.days, args.dry_run, line_only=args.line_only)


if __name__ == '__main__':
    main()
