"""set_pipeline_note — write today's tab note from REAL pipeline state.

2026-09-22.

WHY THIS EXISTS. Every sport's in-tab note came from sport_registry and
was editable without an app release — except MLB, whose banner was
hardcoded in app/index.tsx. It picked between two baked-in strings using
a clock:

    const etHour = ...;
    const preWindow = etHour < 11;
    const isRunning = preWindow || noContext;

Two problems with that, and the second is the real one:

  1. The copy could not be changed without an App Store build. MLB was
     the one sport whose note Andy could not edit.
  2. `etHour < 11` is a GUESS about the pipeline, not a reading of it.
     At 11:01 the app claimed "MLB MODEL ACTIVE" whether or not the
     pipeline had finished, and at 10:59 it claimed the opposite even
     when the slate had been ready for an hour.

This asks the database instead: does today's slate actually have scored
context rows? That is the thing the banner is trying to describe.

IDEMPOTENT — only PATCHes when the note actually changes, so it is safe
to run at the end of every pipeline pass. Same shape as
auto_flip_sport_state.py.

CLI
    python set_pipeline_note.py                 # all configured sports
    python set_pipeline_note.py --sport MLB
    python set_pipeline_note.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
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

# Copy lives here rather than in the client so it can move without a
# build. Worth keeping short — this renders directly above the slate.
SPORTS = {
    'MLB': {
        'ctx_table': 'mlb_game_context',
        'running': ("Pitcher matchups, NRFI scores and Sweat Scores are still "
                    "landing. Market lines below are live; model-derived fields "
                    "fill in once today's pipeline completes."),
        'ready':   ("Pipeline updates twice daily. Lineups confirm 2-3hrs before "
                    "first pitch, umpires post overnight — check back after 4pm "
                    "for the full confirmed slate."),
    },
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def slate_is_scored(ctx_table: str, game_date: str) -> tuple[bool, int, int]:
    """(ready, scored_rows, total_rows) for the date.

    Ready means the slate exists AND has been scored — a context row with
    no primary_play is a row the pipeline created but has not finished
    with, which is exactly the state the 'running' copy describes.
    """
    r = requests.get(f'{SB}/rest/v1/{ctx_table}', headers=H_READ, timeout=25,
                     params={'select': 'game_id,primary_play',
                             'game_date': f'eq.{game_date}', 'limit': '400'})
    if r.status_code != 200:
        # Unknown, not "running". Returning ready=True on an API failure
        # would be a lie; returning False would flip a correct banner to
        # "still landing" because of a transient 500. Caller skips.
        raise RuntimeError(f'{ctx_table} read failed: {r.status_code} {r.text[:120]}')
    rows = r.json()
    if not isinstance(rows, list):
        raise RuntimeError(f'{ctx_table} returned non-list')
    scored = sum(1 for x in rows if x.get('primary_play'))
    return (scored > 0, scored, len(rows))


def run(sport_filter: str | None, dry_run: bool) -> int:
    today = _et_today()
    print(f'=== set_pipeline_note · {today}{" · DRY" if dry_run else ""} ===')
    changed = 0
    for sport, cfg in SPORTS.items():
        if sport_filter and sport != sport_filter:
            continue
        try:
            ready, scored, total = slate_is_scored(cfg['ctx_table'], today)
        except RuntimeError as e:
            print(f'  ⚠ {sport}: {e} — leaving note untouched')
            continue
        if total == 0:
            # No slate today (off-day). Clear rather than assert a state.
            want = None
            why = 'no games on the slate'
        else:
            want = cfg['ready'] if ready else cfg['running']
            why = f'{scored}/{total} rows scored'

        r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H_READ, timeout=20,
                         params={'select': 'sport,today_note', 'sport': f'eq.{sport}'})
        if r.status_code != 200 or not r.json():
            print(f'  ⚠ {sport}: registry read failed ({r.status_code}) — skipping')
            continue
        current = (r.json()[0] or {}).get('today_note')
        if current == want:
            print(f'  · {sport}: unchanged ({why})')
            continue
        state = 'READY' if want == cfg.get('ready') else ('RUNNING' if want else 'CLEARED')
        print(f'  → {sport}: {state} — {why}')
        if dry_run:
            changed += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/sport_registry?sport=eq.{sport}',
                            headers=H_WRITE, json={'today_note': want}, timeout=20)
        if pr.status_code in (200, 204):
            changed += 1
        else:
            print(f'    ⚠ patch failed {pr.status_code}: {pr.text[:140]}')
    print(f'  {"would change" if dry_run else "changed"}: {changed}')
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS.keys()))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    run(a.sport, a.dry_run)


if __name__ == '__main__':
    main()
