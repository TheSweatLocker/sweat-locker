"""Standalone pitcher-scratch sweeper — safety net.

Runs independently of generate_props.run(). Cross-references every
published MLB pitcher-prop against the current MLB Stats API probable
pitchers AND game_context's stored starters. Any prop whose player_name
isn't a valid starter for its game_id gets demoted to SCRATCHED so the
Sharp Card composer + user-facing card exclude it.

Why this exists:
    2026-09-03 Nick Martinez published as PRIME Ks OVER 3.5 for
    Rays @ Rangers. Real starters that day were Cal Quantrill (home) +
    Shane McClanahan (away). Martinez wasn't on either roster.
    generate_props.run()'s inline stale-cleanup exists (line 4644) but
    only runs when the props cron itself runs; if the later cron never
    fired or errored, the AM-cron stale prop stayed PRIME all day.

Fail-safe design:
    - Cross-checks BOTH game_context.home_pitcher/away_pitcher AND
      Stats API probablePitcher. A prop is valid if EITHER matches.
    - Loose name matching (case-insensitive, "Jr." stripped, initials
      collapsed) — the goal is to only kill DEFINITELY-wrong props.
    - On any API failure: does nothing (fails open). Better a false
      negative than a false positive that kills valid props.

Usage:
    python sweep_pitcher_scratches.py                # today, live
    python sweep_pitcher_scratches.py --date 2026-09-04
    python sweep_pitcher_scratches.py --dry-run      # log, no writes
"""
from __future__ import annotations
import argparse, os, sys, json, urllib.request, re
from datetime import date
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

# Prop types that are pitcher-anchored (batter props don't need this check)
PITCHER_PROP_TYPES = {
    'ks_over', 'ks_under',
    'outs_over', 'outs_under',
    'er_over', 'er_under',
    'ha_over', 'ha_under',    # hits+walks allowed
    'bb_over', 'bb_under',    # walks allowed
}


def _norm(name: str | None) -> str:
    """Loose normalize for name comparison — lower, strip punctuation, drop suffix."""
    if not name: return ''
    n = name.lower().strip()
    # drop common suffixes
    n = re.sub(r'\s+(jr|sr|iii|ii|iv)\.?$', '', n)
    # drop periods (e.g. "J.T. Realmuto" → "jt realmuto")
    n = n.replace('.', '')
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _fetch_stats_api_probables(gd: str) -> dict:
    """{(home_team, away_team): (home_probable_name, away_probable_name)}"""
    try:
        r = urllib.request.urlopen(
            f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={gd}&hydrate=probablePitcher',
            timeout=15)
        sched = json.loads(r.read())
    except Exception as e:
        print(f'  ⚠️ Stats API fetch failed: {type(e).__name__}: {e} — skipping API side')
        return {}
    out = {}
    for d in sched.get('dates', []):
        for g in d.get('games', []):
            t = g.get('teams') or {}
            ht = (t.get('home') or {}).get('team', {}).get('name', '')
            at = (t.get('away') or {}).get('team', {}).get('name', '')
            hp = ((t.get('home') or {}).get('probablePitcher') or {}).get('fullName')
            ap = ((t.get('away') or {}).get('probablePitcher') or {}).get('fullName')
            if ht and at:
                out[(ht, at)] = (hp, ap)
    return out


def _fetch_game_context(gd: str) -> dict:
    """{game_id: {'home_team','away_team','home_pitcher','away_pitcher'}}"""
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H_READ,
                     params={'select': 'game_id,home_team,away_team,home_pitcher,away_pitcher',
                             'game_date': f'eq.{gd}',
                             'limit': '100'},
                     timeout=15)
    if r.status_code != 200: return {}
    out = {}
    for row in r.json() or []:
        out[row['game_id']] = row
    return out


def _fetch_props(gd: str) -> list:
    """All non-COVERAGE non-SKIP pitcher props for this date."""
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'select': 'id,game_id,player_name,prop_type,tier,signals',
                             'game_date': f'eq.{gd}',
                             'tier': 'in.(PRIME,STRONG,LEAN)',
                             'prop_type': f'in.({",".join(PITCHER_PROP_TYPES)})',
                             'limit': '500'},
                     timeout=15)
    if r.status_code != 200: return []
    return r.json() or []


def _mark_scratched(prop_id: int, player_name: str, note: str, dry_run: bool) -> bool:
    if dry_run: return True
    r = requests.patch(
        f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{prop_id}',
        headers={**H_WRITE, 'Prefer': 'return=minimal'},
        json={'tier': 'SCRATCHED',
              'signals': {'_scratch_sweep_reason': note,
                          '_scratched_player': player_name}},
        timeout=10)
    return r.status_code < 300


def sweep(gd: str, dry_run: bool = False) -> tuple[int, int]:
    ctx = _fetch_game_context(gd)
    api = _fetch_stats_api_probables(gd)
    props = _fetch_props(gd)
    print(f'sweep {gd}: {len(ctx)} game_context rows, {len(api)} Stats API probables, {len(props)} pitcher props')

    scratched = 0
    checked = 0

    for p in props:
        gid = p.get('game_id')
        pname = p.get('player_name') or ''
        game = ctx.get(gid)
        if not game:
            # No game_context row → can't verify; skip (fail open)
            continue
        checked += 1
        # Build the valid-pitcher set for this game
        valid_names = set()
        for name in (game.get('home_pitcher'), game.get('away_pitcher')):
            if name: valid_names.add(_norm(name))
        api_probables = api.get((game.get('home_team'), game.get('away_team')))
        if api_probables:
            for name in api_probables:
                if name: valid_names.add(_norm(name))

        if not valid_names:
            # No verified starters at all → skip (fail open, don't kill props on a data gap)
            continue

        if _norm(pname) in valid_names:
            continue

        # Player is NOT a currently-valid starter for their game_id → scratch
        note = (f'Sweep {gd}: {pname} not a confirmed starter for '
                f'{game.get("away_team")} @ {game.get("home_team")} '
                f'(valid: {sorted(valid_names)})')
        marker = '[dry]' if dry_run else '✗'
        print(f'  {marker} SCRATCH id={p["id"]} {pname:22s} ({p["prop_type"]}) → {p["tier"]} → SCRATCHED')
        if _mark_scratched(p['id'], pname, note, dry_run):
            scratched += 1

    print(f'\nchecked: {checked}   scratched: {scratched}')
    return scratched, checked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='ISO date (default: today)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    gd = args.date or date.today().isoformat()
    sweep(gd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
