"""freeze_closing_lines — snapshot the TRUE closing line at T-Xmin per game.

Distinguishes "close_total was overwritten by whatever pull ran most
recently" from "close_total is the actual line at first pitch minus X min."
Writes `close_locked_at` timestamp when the freeze succeeds; downstream
code can trust that a non-null close_locked_at means the line is truly
frozen. Sport-universal via line_movement_config.

Per-sport freeze offset:
  MLB    T-5min   (fast market, first pitch is exact)
  NFL    T-15min  (kickoff can slide, market thicker)
  NCAAF  T-15min
  NCAAB  T-10min
  NHL    T-10min
  UFC    T-30min  (main event start time drifts)

Runs on a fast cron (every 5-10 min) — cheap operation; loops per game
and only writes when the game's freeze window hits AND close_locked_at
is still null (idempotent).

CLI
  python freeze_closing_lines.py                    # all sports
  python freeze_closing_lines.py --sport MLB
  python freeze_closing_lines.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

from line_movement_config import get_config

SPORT_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    'NHL':   'nhl_game_context',
    # UFC — separate schema (per-fight); freeze handled by ufc_odds_pull directly
}

# Field name mapping per sport (schemas differ slightly)
# How far past kickoff a catch-up freeze is still worth doing. Inside this
# window, freezing now beats leaving the line live; outside it the pregame
# number is long gone and stamping close_locked_at would assert a guarantee we
# cannot make. The first unbounded dry run wanted to freeze 455 games, some
# 7.8 DAYS past kickoff — hence the bound.
LATE_CATCHUP_HRS = 12

SPORT_FIELDS = {
    'MLB':   {'commence_col': 'game_time_utc',
              'close_cols': ['close_total', 'close_spread', 'home_ml_close', 'away_ml_close']},
    'NFL':   {'commence_col': 'kickoff_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
    'NCAAF': {'commence_col': 'kickoff_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
    'NCAAB': {'commence_col': 'tipoff_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
    'NHL':   {'commence_col': 'puck_drop_utc',
              'close_cols': ['close_total', 'close_spread', 'close_home_ml', 'close_away_ml']},
}


def fetch_upcoming_games(sport: str, window_hrs: int = 2) -> list:
    """Return today's games not yet close-frozen and starting within `window_hrs`."""
    tbl = SPORT_TABLE.get(sport)
    if not tbl: return []
    fields = SPORT_FIELDS[sport]
    commence_col = fields['commence_col']
    close_cols   = fields['close_cols']

    now = datetime.now(timezone.utc)
    window_end = (now + timedelta(hours=window_hrs)).isoformat().replace('+', '%2B')
    now_q = now.isoformat().replace('+', '%2B')
    select_cols = ','.join(['game_id', commence_col, 'close_locked_at'] + close_cols)

    # ══ 2026-10-04 · A GAME THAT ALREADY STARTED COULD NEVER BE FROZEN ══
    # Andy, on an IND@WAS "edge" of +23.3 points: "that was surely a live
    # spread changing in game, we need to be able to differentiate that —
    # locking just pregame movement is the real story."
    #
    # He is right, and this filter was the cause. The lower bound
    # `commence >= now` meant only FUTURE games were candidates, so a game
    # whose kickoff passed before a freeze run was permanently unfreezable —
    # and close_spread kept absorbing LIVE in-game movement for as long as the
    # odds poller ran.
    #
    # Measured today. IND@WAS kicked 13:32 UTC; at 17:12 it read
    # open +1.5 -> "close" -16.5 with close_locked_at NULL, i.e. a live line on
    # a blowout recorded as the closing number. The 17:05 games WERE locked at
    # 17:00 — the mechanism works, it just skipped everything already underway.
    # ARI@NYG showed the same signature (open 7.0 -> -2.5, unlocked).
    #
    # That corrupts the displayed line, the model's edge-vs-market, and CLV.
    # Grading is unaffected because we grade at the line on the PICK, not this
    # one (grade_jerry_reads trusts the stored call_line).
    #
    # The lower bound is now dropped so a started-but-unlocked game is frozen
    # on the next run. Freezing late still records a contaminated number — the
    # pregame close is gone once overwritten — but it stops the bleeding at
    # minutes instead of hours, and such games are reported rather than
    # silently stamped. The durable fix is running the freeze often enough that
    # the catch-up never fires, which the count below makes visible.
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?select={select_cols}'
        f'&close_locked_at=is.null'
        f'&{commence_col}=lte.{window_end}',
        headers=H_READ, timeout=20)
    if r.status_code != 200:
        # Column might not exist on this sport yet — non-fatal
        return []
    return r.json() or []


def freeze_game(sport: str, game: dict, dry_run: bool = False) -> bool:
    """Stamp close_locked_at (the current close_* values are the freeze)."""
    tbl = SPORT_TABLE[sport]
    gid = game['game_id']
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {'close_locked_at': now_iso}
    if dry_run:
        return True
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
        headers=H_WRITE, json=payload, timeout=15)
    return r.status_code in (200, 204)


def run_sport(sport: str, dry_run: bool = False) -> tuple:
    if sport not in SPORT_TABLE:
        print(f'  {sport}: skipped (no game_context table)')
        return (0, 0)
    cfg = get_config(sport)
    close_offset_min = cfg['close_offset_min']

    commence_col = SPORT_FIELDS[sport]['commence_col']
    now = datetime.now(timezone.utc)

    # Look 2h ahead — a game due to start in <= close_offset_min is ready to freeze
    games = fetch_upcoming_games(sport, window_hrs=2)
    frozen = 0
    considered = 0
    stale: list = []      # games frozen AFTER kickoff — reported below
    abandoned = 0         # too far past kickoff to bless; see LATE_CATCHUP_HRS
    for game in games:
        commence_str = game.get(commence_col)
        if not commence_str: continue
        try:
            commence = datetime.fromisoformat(commence_str.replace('Z', '+00:00'))
        except ValueError:
            continue
        # Freeze if we're within `close_offset_min` of first pitch/kickoff/tip
        mins_until = (commence - now).total_seconds() / 60.0
        if mins_until > close_offset_min:
            continue  # too early
        # 2026-10-04 · DO NOT ABANDON A STARTED GAME. This read
        # `if mins_until < -30: continue  # missed window`, which together with
        # the removed `commence >= now` filter meant a game that got past the
        # freeze window was never frozen AT ALL — so close_spread went on
        # absorbing live in-game movement indefinitely. IND@WAS sat at
        # open +1.5 -> "close" -16.5, unlocked, 3.5 hours after kickoff.
        #
        # Freezing late cannot recover the pregame number, which is gone the
        # moment the poller overwrites it. But leaving it unfrozen guarantees it
        # keeps getting worse, so catch it up and SAY SO — a silent late freeze
        # would hide the same problem one layer down.
        # BUT BOUND THE CATCH-UP. Dropping the lower bound outright swept in
        # 455 games on the first dry run, including NCAAF kickoffs 7.8 DAYS
        # past. close_locked_at is a guarantee — downstream reads it as "this
        # is the TRUE close" — and stamping it on hundreds of lines nobody
        # verified is worse than the bug it fixes.
        #
        # So: catch up anything inside LATE_CATCHUP_HRS (a game from this
        # slate, where freezing now still beats leaving it live), and refuse
        # older ones. Those are permanently unverifiable and are counted
        # separately rather than quietly blessed.
        if mins_until < -(LATE_CATCHUP_HRS * 60):
            abandoned += 1
            continue
        late = mins_until < -30
        if late:
            stale.append((game.get('game_id'), round(-mins_until)))
        considered += 1
        # Snapshot: leave existing close_* alone (they're set by latest odds pull);
        # just stamp close_locked_at so downstream knows this is the TRUE close.
        if freeze_game(sport, game, dry_run=dry_run):
            frozen += 1
            if dry_run:
                print(f'    [DRY] {sport} {game["game_id"][:10]} · '
                      f'{mins_until:5.1f}min pre → freeze')
    print(f'  {sport}: {frozen}/{considered} in-window freezes')
    if abandoned:
        print(f'  ⓘ {sport}: {abandoned} unlocked game(s) older than '
              f'{LATE_CATCHUP_HRS}h — left unfrozen; their close is '
              f'unverifiable either way')
    if stale:
        # Loud on purpose. Every entry is a game whose stored "closing" line
        # already absorbed live in-game movement, so its CLV and its
        # model-vs-market edge are wrong for that game and cannot be repaired.
        # A non-empty list means the freeze cron is not running often enough
        # for the slate — that is the thing to fix, not this message.
        print(f'  ⚠ {sport}: {len(stale)} game(s) frozen AFTER kickoff — '
              f'close_* already contains live movement:')
        for gid, mins in sorted(stale, key=lambda z: -z[1])[:10]:
            print(f'      {str(gid)[:16]:16s} {mins:>5} min past kickoff')
    return (frozen, considered)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport',
                   choices=list(SPORT_TABLE.keys()) + ['ALL'], default='ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sports = list(SPORT_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    print(f'=== freeze_closing_lines · {"/".join(sports)} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    tf = tc = 0
    for s in sports:
        f, c = run_sport(s, dry_run=args.dry_run); tf += f; tc += c
    print(f'\n  ✓ {tf}/{tc} closes frozen')


if __name__ == '__main__':
    main()
