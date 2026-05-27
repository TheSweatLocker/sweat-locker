"""Verify mlb_game_context starter rows against MLB Stats API probablePitcher.

Why this exists: 3 attribution slips in 4 days (Yesavage 5/26, Davis Martin 5/27,
Lodolo conflation) — the pipeline's mlb_game_context.away_pitcher/home_pitcher
fields drifted from the actual announced starter, usually because:
  - The probable was updated by the team AFTER our last context upsert
  - A surprise debut starter wasn't yet in the prior pitcher_stats refresh
  - A scratched/swapped starter wasn't propagated to our DB

Every downstream signal that depends on a specific pitcher (xERA, L3 ERA, BAA
mastery, K/9 mastery, projections, scout reports, K Over props) becomes wrong
when the pitcher field is wrong. Voice doc rule #1 ("Jerry's mouth is bounded
by the struct") only protects when the struct itself is correct.

Strategy: query MLB Stats API schedule with probablePitcher hydration for the
target date, cross-reference each game's home/away probable against what's
stored in mlb_game_context, and patch any mismatches. Clear all derived
pitcher-specific fields when patching, so downstream code recomputes from the
new (correct) pitcher rather than serving stale data attached to the wrong name.

CLI:
    python verify_starters.py                  # today's slate
    python verify_starters.py 2026-05-27       # specific date
    python verify_starters.py --dry-run        # check only, no patches

Runs in seconds (one schedule API call + one Supabase fetch + a handful of
PATCHes). Designed to be the LAST step in the afternoon pipeline run so any
late starter changes are captured before the sweat card + props get generated
off bad data.
"""
import os
import sys
import json
import urllib.request
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

READ_H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_H = {**READ_H, "Content-Type": "application/json", "Prefer": "return=minimal"}

# Fields tied to a specific pitcher — cleared when we patch a mismatched name.
# Mastery dims (era / avg / k_per_9 / ip) are critical: keeping them attached
# to the WRONG pitcher silently poisons every downstream prop scorer.
_PITCHER_FIELDS = (
    "sp_xera",
    "pitcher_last_3_era",
    "pitcher_last_3_k_pct",
    "pitcher_vs_team_era",
    "pitcher_vs_team_avg",
    "pitcher_vs_team_k_per_9",
    "pitcher_vs_team_ip",
    "first_inning_era",
    "first_inning_whip",
    "first_inning_k",
    "first_inning_bb",
    "first_inning_hr",
    "first_inning_avg",
    "first_inning_ip",
    "last_ip",
    "last_pitch_count",
    "sp_days_rest",
    "sp_hand",
    "sp_k_pct",
    "sp_gb_pct",
    "sp_whiff_rate",
    "sp_last5_era",
    "sp_era",
)


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def _resolve_args():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    date = args[0] if args else _today_et()
    return date, dry_run


def _fetch_mlb_probables(date_str):
    """Return dict keyed by (away_team_name, home_team_name) → (away_p, home_p)."""
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={date_str}&hydrate=probablePitcher"
    )
    try:
        r = urllib.request.urlopen(url, timeout=15)
        data = json.loads(r.read())
    except Exception as e:
        print(f"  ⚠️ MLB API fetch failed: {e}")
        return {}

    out = {}
    for d in data.get("dates", []):
        for g in d.get("games", []):
            teams = g.get("teams", {})
            away = teams.get("away", {}).get("team", {}).get("name")
            home = teams.get("home", {}).get("team", {}).get("name")
            ap = teams.get("away", {}).get("probablePitcher", {}).get("fullName")
            hp = teams.get("home", {}).get("probablePitcher", {}).get("fullName")
            if away and home:
                out[(away, home)] = (ap, hp)
    return out


def _fetch_db_rows(date_str):
    url = (
        f"{SUPABASE_URL}/rest/v1/mlb_game_context"
        f"?game_date=eq.{date_str}"
        f"&select=id,game_id,away_team,home_team,away_pitcher,home_pitcher"
    )
    r = urllib.request.urlopen(urllib.request.Request(url, headers=READ_H), timeout=20)
    return json.loads(r.read())


def _patch_starter(row_id, side, new_name):
    """Patch the pitcher name on one side AND clear all derived fields on that
    side so downstream code recomputes from the correct pitcher rather than
    serving stale stats attached to the wrong name."""
    payload = {f"{side}_pitcher": new_name}
    for field in _PITCHER_FIELDS:
        payload[f"{side}_{field}"] = None
    # primary_play might have been computed off the wrong pitcher; clear it too
    payload["primary_play"] = None
    url = f"{SUPABASE_URL}/rest/v1/mlb_game_context?id=eq.{row_id}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=WRITE_H, method="PATCH"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        print(f"    ⚠️ PATCH failed {e.code}: {e.read().decode()[:200]}")
        return False


def run(date_str, dry_run=False):
    print(f"=== verify_starters {date_str}{' [DRY-RUN]' if dry_run else ''} ===")
    api_probables = _fetch_mlb_probables(date_str)
    if not api_probables:
        print("  No MLB schedule data returned — exiting.")
        return
    db_rows = _fetch_db_rows(date_str)
    if not db_rows:
        print("  No mlb_game_context rows for this date — exiting.")
        return

    print(f"  MLB API games: {len(api_probables)}   DB rows: {len(db_rows)}")
    mismatches = 0
    patches_applied = 0
    no_change = 0

    for row in db_rows:
        away = row.get("away_team")
        home = row.get("home_team")
        db_away_p = row.get("away_pitcher")
        db_home_p = row.get("home_pitcher")
        api = api_probables.get((away, home))
        if not api:
            print(f"  ⚠️ no MLB API match for {away} @ {home} — skip")
            continue
        api_away_p, api_home_p = api

        away_mismatch = api_away_p and db_away_p and db_away_p != api_away_p
        home_mismatch = api_home_p and db_home_p and db_home_p != api_home_p

        # Also catch cases where DB has NO pitcher but API now has one
        away_fill = (not db_away_p) and bool(api_away_p)
        home_fill = (not db_home_p) and bool(api_home_p)

        if not (away_mismatch or home_mismatch or away_fill or home_fill):
            no_change += 1
            continue

        mismatches += 1
        print(f"  🚨 {away} @ {home}")
        for side, db_p, api_p, mismatch, fill in (
            ("away", db_away_p, api_away_p, away_mismatch, away_fill),
            ("home", db_home_p, api_home_p, home_mismatch, home_fill),
        ):
            if mismatch:
                print(f"     {side}: DB={db_p!r}  →  API={api_p!r}  (MISMATCH)")
            elif fill:
                print(f"     {side}: DB=NULL  →  API={api_p!r}  (FILL)")
            else:
                continue
            if dry_run:
                continue
            if _patch_starter(row["id"], side, api_p):
                patches_applied += 1
                print(f"       ✓ patched + cleared derived fields")

    print()
    print(
        f"  {mismatches} game(s) with mismatch/fill   "
        f"{patches_applied} field patch(es) applied   "
        f"{no_change} clean"
    )


if __name__ == "__main__":
    date, dry_run = _resolve_args()
    run(date, dry_run)
