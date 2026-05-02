"""Watchdog: refresh lineups + umpires for games starting soon.

Runs every 30 minutes via cron (12pm-10pm ET). For each game with first
pitch in the next 30-90 minutes, refetches confirmed lineups and home-plate
umpire from MLB Stats API and PATCHes only those fields on the existing
mlb_game_context row. Then re-runs prop scoring so newly-confirmed lineups
produce hits/Under props that weren't possible at the 8am/2pm cron.

Why this exists: lineups confirm ~2 hours before first pitch and umpires
~1.5 hours. The fixed 8am/2pm crons miss every game starting after ~4pm ET.
This watchdog plugs the gap without requiring per-game scheduled jobs or
GitHub Actions API trickery.

Idempotent: skips games already final, already past first pitch, or with
both lineup_confirmed=True and umpire populated. Cheap to run on a 30-min
cadence — typical execution touches 1-3 games.

Usage: python refresh_imminent_games.py
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}
PATCH_HEADERS = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}

# Refresh window — games whose first pitch is N min away.
# MLB publishes lineups up to 3.5 hours before first pitch for early-slate
# games. Window of 30-240 catches the publish moment for every common game
# start time. Skip-if-already-confirmed makes wider window cheap.
WINDOW_MIN_AHEAD = 30
WINDOW_MAX_AHEAD = 240


def et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def et_today():
    return et_now().strftime("%Y-%m-%d")


def in_refresh_window(commence_time_iso):
    """True if first pitch is within [WINDOW_MIN_AHEAD, WINDOW_MAX_AHEAD] minutes."""
    if not commence_time_iso:
        return False
    try:
        ct = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
    except Exception:
        return False
    now = datetime.now(timezone.utc)
    minutes_until = (ct - now).total_seconds() / 60
    return WINDOW_MIN_AHEAD <= minutes_until <= WINDOW_MAX_AHEAD


def fetch_today_context_rows():
    """Pull all today's mlb_game_context rows. mlb_game_context has no
    commence_time column — we fetch first-pitch times from the MLB API
    schedule and zip them onto each row by team pair below."""
    today = et_today()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context",
        params={
            "game_date": f"eq.{today}",
            "select": "id,game_id,home_team,away_team,umpire,lineup_confirmed",
        },
        headers=HEADERS,
        timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def attach_commence_times(rows, schedule_dates):
    """Add commence_time to each row by matching team pair in MLB schedule."""
    for row in rows:
        for d in schedule_dates:
            for g in d.get("games", []):
                mh = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                ma = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                if (row["home_team"].lower() in mh.lower() or mh.lower() in row["home_team"].lower()) and \
                   (row["away_team"].lower() in ma.lower() or ma.lower() in row["away_team"].lower()):
                    row["commence_time"] = g.get("gameDate")
                    break
            if row.get("commence_time"):
                break
    return rows


def fetch_mlb_schedule_with_lineups_and_officials(game_date):
    """Single hydrated MLB API call covering both lineups and officials."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "lineups,officials,probablePitcher",
            },
            timeout=15,
        )
        return r.json().get("dates", [])
    except Exception as e:
        print(f"  schedule fetch error: {e}")
        return []


def _last_name(full):
    if not full:
        return ""
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
    parts = [p for p in full.strip().split() if p.lower().rstrip(".") not in suffixes]
    return parts[-1].lower() if parts else ""


def find_mlb_game_for_row(schedule_dates, row):
    """DH-aware match: team pair + commence_time proximity."""
    candidates = []
    for d in schedule_dates:
        for g in d.get("games", []):
            mh = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
            ma = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
            if (row["home_team"].lower() in mh.lower() or mh.lower() in row["home_team"].lower()) and \
               (row["away_team"].lower() in ma.lower() or ma.lower() in row["away_team"].lower()):
                candidates.append(g)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # DH disambiguation by commence_time proximity
    if not row.get("commence_time"):
        return candidates[0]
    try:
        target = datetime.fromisoformat(row["commence_time"].replace("Z", "+00:00"))
        return min(
            candidates,
            key=lambda g: abs(
                (datetime.fromisoformat(g.get("gameDate", "").replace("Z", "+00:00")) - target).total_seconds()
            ),
        )
    except Exception:
        return candidates[0]


def extract_lineup(mlb_game):
    home_lineup = mlb_game.get("lineups", {}).get("homePlayers", []) or []
    away_lineup = mlb_game.get("lineups", {}).get("awayPlayers", []) or []
    home_batters = [
        p.get("fullName", "")
        for p in home_lineup
        if p.get("primaryPosition", {}).get("abbreviation") != "P"
    ][:9]
    away_batters = [
        p.get("fullName", "")
        for p in away_lineup
        if p.get("primaryPosition", {}).get("abbreviation") != "P"
    ][:9]
    return home_batters, away_batters


def extract_umpire(mlb_game):
    officials = mlb_game.get("officials", []) or []
    return next(
        (
            o.get("official", {}).get("fullName")
            for o in officials
            if o.get("officialType") == "Home Plate"
        ),
        None,
    )


def fetch_umpire_stats(ump_name):
    """Pull season K rate / over rate / nrfi rate for the given umpire."""
    if not ump_name:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_umpires",
            params={"ump_name": f"eq.{ump_name}", "select": "k_rate_above_avg,over_rate,nrfi_rate"},
            headers=HEADERS,
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if rows else None
    except Exception:
        return None


def umpire_note(ump_name, ump_stats):
    """Build short human-readable note for the row."""
    if not ump_name:
        return None
    if not ump_stats:
        return f"{ump_name} — not in database"
    pieces = [ump_name]
    over = ump_stats.get("over_rate")
    k = ump_stats.get("k_rate_above_avg")
    if over is not None:
        try:
            o = float(over)
            if o >= 0.55:
                pieces.append(f"hitter-friendly zone, {int(o*100)}% over rate")
            elif o <= 0.45:
                pieces.append(f"pitcher-friendly zone, {int(o*100)}% over rate")
            else:
                pieces.append(f"neutral zone, {int(o*100)}% over rate")
        except Exception:
            pass
    if k is not None:
        try:
            kf = float(k)
            if kf >= 0.4:
                pieces.append("K-friendly")
            elif kf <= -0.4:
                pieces.append("K-suppressing")
        except Exception:
            pass
    return " — ".join(pieces) if len(pieces) > 1 else f"{ump_name} — neutral"


def patch_row(row_id, payload):
    if not payload:
        return True
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context",
        params={"id": f"eq.{row_id}"},
        headers=PATCH_HEADERS,
        json=payload,
        timeout=15,
    )
    return r.status_code in (200, 204)


def run():
    now = et_now()
    print(f"Imminent-games refresh — {now.strftime('%Y-%m-%d %H:%M ET')}")

    rows = fetch_today_context_rows()
    if not rows:
        print("  No mlb_game_context rows for today — nothing to refresh.")
        return

    # Pull MLB schedule first (lineups + officials + probable pitchers, single call)
    today = et_today()
    schedule_dates = fetch_mlb_schedule_with_lineups_and_officials(today)
    if not schedule_dates:
        print("  ⚠️ MLB schedule fetch returned nothing — aborting refresh.")
        return

    # Enrich rows with commence_time from schedule (mlb_game_context lacks the col)
    rows = attach_commence_times(rows, schedule_dates)

    imminent = [r for r in rows if in_refresh_window(r.get("commence_time"))]
    if not imminent:
        # Show what's coming up so manual runs are debuggable
        upcoming = []
        for r in rows:
            ct = r.get("commence_time")
            if not ct:
                continue
            try:
                ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                mins = (ct_dt - datetime.now(timezone.utc)).total_seconds() / 60
                if 0 < mins <= 360:
                    upcoming.append((mins, r))
            except Exception:
                pass
        upcoming.sort(key=lambda x: x[0])
        print(f"  No games in {WINDOW_MIN_AHEAD}-{WINDOW_MAX_AHEAD} min window. {len(rows)} games scanned.")
        if upcoming:
            print(f"  Next 3 imminent (will refresh on future cron):")
            for mins, r in upcoming[:3]:
                print(f"    +{mins:.0f}m  {r['away_team']} @ {r['home_team']}")
        return

    print(f"  {len(imminent)} game(s) in refresh window:")
    for r in imminent:
        ct = (r.get("commence_time") or "")[:16]
        print(f"    {r['away_team']} @ {r['home_team']} — first pitch {ct} (lineup={r.get('lineup_confirmed')}, ump={'✓' if r.get('umpire') else '—'})")

    refreshed = 0
    for row in imminent:
        mlb_game = find_mlb_game_for_row(schedule_dates, row)
        if not mlb_game:
            print(f"  ⚠️ {row['away_team']} @ {row['home_team']}: no MLB API match")
            continue

        payload = {}

        # Lineup refresh — only if not yet confirmed
        if not row.get("lineup_confirmed"):
            home_batters, away_batters = extract_lineup(mlb_game)
            if home_batters or away_batters:
                payload["home_lineup"] = ", ".join(home_batters)
                payload["away_lineup"] = ", ".join(away_batters)
                payload["lineup_confirmed"] = True
                print(f"    ✅ Lineup confirmed: {row['away_team']} @ {row['home_team']}")

        # Umpire refresh — only if not yet populated
        if not row.get("umpire"):
            ump = extract_umpire(mlb_game)
            if ump:
                ump_stats = fetch_umpire_stats(ump)
                payload["umpire"] = ump
                payload["umpire_note"] = umpire_note(ump, ump_stats)
                print(f"    ✅ Umpire confirmed: {ump} ({row['away_team']} @ {row['home_team']})")

        if payload:
            if patch_row(row["id"], payload):
                refreshed += 1
            else:
                print(f"    ⚠️ patch failed for {row['away_team']} @ {row['home_team']}")
        else:
            print(f"    — {row['away_team']} @ {row['home_team']}: nothing to refresh")

    print(f"\n  Refreshed {refreshed} row(s).")

    # If any lineups got confirmed this run, regenerate props so the new
    # lineup-dependent picks (hits Over/Under) surface for users.
    lineup_changes = sum(1 for r in imminent if not r.get("lineup_confirmed"))
    if lineup_changes and refreshed:
        print("\n  Lineups changed — regenerating props...")
        try:
            import generate_props
            generate_props.run()
        except Exception as e:
            print(f"  ⚠️ prop regen failed: {e}")


if __name__ == "__main__":
    run()
