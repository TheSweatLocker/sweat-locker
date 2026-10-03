"""Retry missing starters — detect games where MLB has since announced
a pitcher we didn't have + PATCH context + trigger downstream re-compute.

Runs after check_starter_changes.py (which only detects). This one acts.

Background
----------
7/24 SD@MIA + 7/25 NYY@PHI, ATH@MIN pattern: our 12pm cron pulls MLB
API for probable pitchers. Some teams announce the starter LATE (2-4pm
ET). Home_pitcher stays NULL on the mlb_game_context row → downstream
lenses (v4 model_pred_spread/total, MC probabilities) can't compute →
game shows up in the artifact with data gaps.

Fix
---
1. Query today's mlb_game_context for games with home_pitcher OR
   away_pitcher NULL.
2. Fetch MLB Stats API for updated probable pitchers.
3. For each affected game, if MLB now has the missing pitcher, PATCH
   the game_context row with the name.
4. Trigger patch_projected_ks.py to compute projected_er/outs + panel
   for the patched row (existing pipeline, already runs on all games
   but explicit trigger here ensures fresh recompute).
5. Log what was fixed for the daily audit trail.

Cron target
-----------
Add to mlb_pipeline.yml as a 5pm ET step (after 2pm cron, before
evening games). Also runs from workflow_dispatch for manual trigger
during a slate when we notice a gap.

Usage:
    python retry_missing_starters.py               # today
    python retry_missing_starters.py --date 2026-07-25
    python retry_missing_starters.py --dry-run
"""
import argparse
import io
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch_context_gaps(game_date: str) -> list:
    """Games with either pitcher NULL, OR a named starter with no projection.

    ══ 2026-10-03 · THE SAFETY NET WATCHED THE WRONG COLUMN ══
    Andy, from the app: "for LAD and Braves game i see Dod[d] as starter for
    braves but projection not in gamedetail and some models in model
    consensus missing numbers".

    This selected only `(home_pitcher.is.null, away_pitcher.is.null)` — a
    missing NAME. But the user-visible damage is a missing PROJECTION, and
    the two come apart: once the name lands by any other path (the 2pm ET
    context build, or a separate starter feed), this query stops returning
    the game and the net goes blind. Nothing else re-runs
    patch_projected_ks.

    Dylan Dodd was exactly that. `away_pitcher` = 'Dylan Dodd' was already
    populated while all five away_pitcher_projected_* were NULL and
    panel_implied_total was NULL. The 5pm ET and 8pm ET retries would both
    have found zero gaps and exited "✓ no gaps". Running patch_projected_ks
    by hand filled every one of them in a single pass off
    `[season_fallback]`, so the data had been available the whole time —
    nothing was ever going to ask for it.

    Now a game qualifies if a name is missing OR a named starter has no
    projected_ks. The name-patch loop in run() is unchanged and simply
    finds nothing to do on a projection-only gap; the recompute trigger is
    what those games need, and run() now fires it for them too.

    projected_ks is the sentinel because patch_projected_ks writes all five
    projections plus the panel in one pass, so any one of them standing in
    for the set is equivalent — and ks is the one with a documented
    incident behind it (the 5/27 DeGrom gap that created that script).
    """
    qs = urllib.parse.urlencode({
        'game_date': f'eq.{game_date}',
        'select': 'game_id,away_team,home_team,away_pitcher,home_pitcher,'
                  'away_pitcher_projected_ks,home_pitcher_projected_ks',
        'or': '(home_pitcher.is.null,away_pitcher.is.null,'
              'home_pitcher_projected_ks.is.null,'
              'away_pitcher_projected_ks.is.null)',
    })
    req = urllib.request.Request(f'{SB}/rest/v1/mlb_game_context?{qs}', headers=H_READ)
    with urllib.request.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read())
    return [g for g in rows if isinstance(g, dict)]


def _needs_projection(g: dict) -> bool:
    """A named starter whose projection never landed."""
    return any(g.get(f'{s}_pitcher') and g.get(f'{s}_pitcher_projected_ks') is None
               for s in ('home', 'away'))


def fetch_api_pitchers(game_date: str) -> dict:
    url = (f'https://statsapi.mlb.com/api/v1/schedule/games/?sportId=1'
           f'&date={game_date}&hydrate=probablePitcher')
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    out = {}
    for d in data.get('dates', []):
        for g in d.get('games', []):
            away = (g.get('teams', {}).get('away', {}).get('team', {}) or {}).get('name')
            home = (g.get('teams', {}).get('home', {}).get('team', {}) or {}).get('name')
            ap = (g.get('teams', {}).get('away', {}).get('probablePitcher') or {}).get('fullName')
            hp = (g.get('teams', {}).get('home', {}).get('probablePitcher') or {}).get('fullName')
            if away and home:
                out[(away, home)] = {'away_pitcher': ap, 'home_pitcher': hp}
    return out


def patch_context_pitcher(game_id: str, field: str, value: str, dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] would PATCH {game_id} · {field}={value}')
        return True
    req = urllib.request.Request(
        f'{SB}/rest/v1/mlb_game_context?game_id=eq.{game_id}',
        data=json.dumps({field: value}).encode(),
        headers=H_WRITE, method='PATCH',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 204)
    except Exception as e:
        print(f'  ⚠ PATCH failed: {e}')
        return False


def trigger_projections_recompute(game_date: str | None = None) -> bool:
    """Run patch_projected_ks.py to compute projected_er + panel for the
    patched games. patch_projected_ks handles all games on the slate
    so we don't need to target — it's idempotent + fast (~10s)."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'patch_projected_ks.py')
    if not os.path.exists(script):
        print(f'  ⚠ patch_projected_ks.py not found — skip downstream trigger')
        return False
    try:
        # 2026-10-03: pass the date through. This ran patch_projected_ks
        # with no --date, so it always patched TODAY no matter which date
        # the retry was invoked for -- a manual `--date 2026-09-23` backfill
        # would silently repair the wrong slate and report success.
        cmd = [sys.executable, script]
        if game_date:
            cmd += ['--date', game_date]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # Print the "panel: margin=X total=Y" lines so we can see what
        # got backfilled by the retry
        if r.stdout:
            panel_lines = [ln for ln in r.stdout.splitlines() if 'panel:' in ln]
            if panel_lines:
                print(f'  ✓ patch_projected_ks landed panel on {len(panel_lines)} game(s):')
                for ln in panel_lines[:6]:
                    print(f'      {ln.strip()}')
            print(r.stdout.splitlines()[-1] if r.stdout.splitlines() else '')
        return r.returncode == 0
    except Exception as e:
        print(f'  ⚠ downstream trigger failed: {e}')
        return False


def run(date_str: str, dry_run: bool = False) -> None:
    print(f'=== retry_missing_starters · {date_str} ===')
    gaps = fetch_context_gaps(date_str)
    proj_only = [g for g in gaps if _needs_projection(g)]
    name_gaps = [g for g in gaps
                 if g.get('home_pitcher') is None or g.get('away_pitcher') is None]
    print(f'  games w/ a gap: {len(gaps)}  '
          f'(missing name {len(name_gaps)} · named-but-unprojected '
          f'{len(proj_only)})')
    for g in proj_only:
        _side = ('away' if (g.get('away_pitcher')
                 and g.get('away_pitcher_projected_ks') is None) else 'home')
        print(f"  ⚠ {str(g.get('away_team'))[:20]:<20} @ "
              f"{str(g.get('home_team'))[:20]:<20}  {_side}="
              f"{g.get(_side + '_pitcher')} named but NO projection")
    if not gaps:
        print('  ✓ no gaps — exiting')
        return

    api = fetch_api_pitchers(date_str)
    patched = 0
    still_missing = 0

    for g in gaps:
        key = (g['away_team'], g['home_team'])
        api_entry = api.get(key, {})
        for side in ('home', 'away'):
            field = f'{side}_pitcher'
            current = g.get(field)
            new = api_entry.get(field)
            if current is None and new:
                if patch_context_pitcher(g['game_id'], field, new, dry_run):
                    patched += 1
                    print(f'  ✓ {key[0][:20]:<20} @ {key[1][:20]:<20}  {side}={new} (added)')
                else:
                    still_missing += 1
            elif current is None and not new:
                still_missing += 1
                print(f'  ⋯ {key[0][:20]:<20} @ {key[1][:20]:<20}  {side}=NULL (still TBD per MLB)')

    print(f'\n  patched: {patched}  · still-missing: {still_missing}')

    if patched > 0 and not dry_run:
        print('\n  Triggering downstream recompute (patch_projected_ks) ...')
        trigger_projections_recompute(date_str)

    if patched > 0:
        print('\n  Note: v4/MC probability recompute requires full game_context.py '
              'run — not triggered here (too heavy for a retry step). Next scheduled '
              'cron will pick up.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(args.date or _today_et(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
