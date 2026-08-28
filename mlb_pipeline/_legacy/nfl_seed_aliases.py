"""Seed nfl_team_aliases — 32 NFL teams with name variants (Phase 1 prep).

One-time bootstrap. NFL team names are stable across years (unlike MLB where
"Athletics" recently dropped "Oakland", or "Cleveland Guardians"). Manual seed
covers all 32 + alt-spelling cases ("Washington Football Team" / "Washington
Commanders" historical name evolution, "Oakland Raiders" → "Las Vegas Raiders",
etc).

canonical_name = nflverse abbreviation (KC, PHI, NE, NYG, NYJ, etc) —
matches the schedule/stats files. Odds API returns full names ("Kansas City
Chiefs"), ESPN sometimes uses location only.

Idempotent — uses on_conflict=canonical_name.

Usage:
    python nfl_seed_aliases.py
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}

# Authoritative 32-team seed.
# canonical_name = nflverse abbreviation, full_name = Odds API standard,
# alt_names = historical/casual variants encountered in the wild.
TEAMS = [
    # AFC East
    ("BUF", "Buffalo Bills",         "Buffalo",       "Bills",        "AFC", "AFC East",
        ["Buffalo"]),
    ("MIA", "Miami Dolphins",        "Miami",         "Dolphins",     "AFC", "AFC East",
        []),
    ("NE",  "New England Patriots",  "New England",   "Patriots",     "AFC", "AFC East",
        ["NWE", "Pats"]),
    ("NYJ", "New York Jets",         "New York",      "Jets",         "AFC", "AFC East",
        ["N.Y. Jets"]),
    # AFC North
    ("BAL", "Baltimore Ravens",      "Baltimore",     "Ravens",       "AFC", "AFC North",
        []),
    ("CIN", "Cincinnati Bengals",    "Cincinnati",    "Bengals",      "AFC", "AFC North",
        []),
    ("CLE", "Cleveland Browns",      "Cleveland",     "Browns",       "AFC", "AFC North",
        []),
    ("PIT", "Pittsburgh Steelers",   "Pittsburgh",    "Steelers",     "AFC", "AFC North",
        []),
    # AFC South
    ("HOU", "Houston Texans",        "Houston",       "Texans",       "AFC", "AFC South",
        []),
    ("IND", "Indianapolis Colts",    "Indianapolis",  "Colts",        "AFC", "AFC South",
        []),
    ("JAX", "Jacksonville Jaguars",  "Jacksonville",  "Jaguars",      "AFC", "AFC South",
        ["JAC", "Jags"]),
    ("TEN", "Tennessee Titans",      "Tennessee",     "Titans",       "AFC", "AFC South",
        []),
    # AFC West
    ("DEN", "Denver Broncos",        "Denver",        "Broncos",      "AFC", "AFC West",
        []),
    ("KC",  "Kansas City Chiefs",    "Kansas City",   "Chiefs",       "AFC", "AFC West",
        ["KAN"]),
    ("LV",  "Las Vegas Raiders",     "Las Vegas",     "Raiders",      "AFC", "AFC West",
        ["LVR", "OAK", "Oakland Raiders"]),  # OAK historical, common in older data
    ("LAC", "Los Angeles Chargers",  "Los Angeles",   "Chargers",     "AFC", "AFC West",
        ["SDG", "San Diego Chargers"]),       # SD historical
    # NFC East
    ("DAL", "Dallas Cowboys",        "Dallas",        "Cowboys",      "NFC", "NFC East",
        []),
    ("NYG", "New York Giants",       "New York",      "Giants",       "NFC", "NFC East",
        ["N.Y. Giants"]),
    ("PHI", "Philadelphia Eagles",   "Philadelphia",  "Eagles",       "NFC", "NFC East",
        []),
    ("WAS", "Washington Commanders", "Washington",    "Commanders",   "NFC", "NFC East",
        ["WSH", "Washington Football Team", "Washington Redskins"]),  # historical
    # NFC North
    ("CHI", "Chicago Bears",         "Chicago",       "Bears",        "NFC", "NFC North",
        []),
    ("DET", "Detroit Lions",         "Detroit",       "Lions",        "NFC", "NFC North",
        []),
    ("GB",  "Green Bay Packers",     "Green Bay",     "Packers",      "NFC", "NFC North",
        ["GNB"]),
    ("MIN", "Minnesota Vikings",     "Minnesota",     "Vikings",      "NFC", "NFC North",
        []),
    # NFC South
    ("ATL", "Atlanta Falcons",       "Atlanta",       "Falcons",      "NFC", "NFC South",
        []),
    ("CAR", "Carolina Panthers",     "Carolina",      "Panthers",     "NFC", "NFC South",
        []),
    ("NO",  "New Orleans Saints",    "New Orleans",   "Saints",       "NFC", "NFC South",
        ["NOR"]),
    ("TB",  "Tampa Bay Buccaneers",  "Tampa Bay",     "Buccaneers",   "NFC", "NFC South",
        ["TAM", "Bucs"]),
    # NFC West
    ("ARI", "Arizona Cardinals",     "Arizona",       "Cardinals",    "NFC", "NFC West",
        ["ARZ"]),
    ("LA",  "Los Angeles Rams",      "Los Angeles",   "Rams",         "NFC", "NFC West",
        ["LAR", "STL", "St. Louis Rams"]),    # STL historical
    ("SF",  "San Francisco 49ers",   "San Francisco", "49ers",        "NFC", "NFC West",
        ["SFO", "Niners"]),
    ("SEA", "Seattle Seahawks",      "Seattle",       "Seahawks",     "NFC", "NFC West",
        []),
]


def upsert_aliases(rows):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/nfl_team_aliases?on_conflict=canonical_name",
        headers=WRITE_HEADERS, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  Upsert error {r.status_code}: {r.text[:300]}")
        return False
    return True


def run():
    print(f"=== NFL alias seed ({len(TEAMS)} teams) ===")
    rows = []
    for abbr, full, city, mascot, conf, div, alts in TEAMS:
        rows.append({
            "canonical_name": abbr,
            "full_name": full,
            "city": city,
            "mascot": mascot,
            "odds_api_name": full,           # Odds API uses full team name
            "espn_name": full,               # ESPN matches full in most contexts
            "conference": conf,
            "division": div,
            "alt_names": alts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    if upsert_aliases(rows):
        print(f"  ✅ Seeded {len(rows)} alias rows")
    else:
        print("  ❌ Upsert failed")


if __name__ == "__main__":
    run()
