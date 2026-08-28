"""Seed ncaab_team_aliases from existing kenpom_cache (Phase 1 prep).

One-time bootstrap. Reads the 365 D1 teams currently in kenpom_cache (the
client-side cache that ncaab_pipeline.py will eventually replace), creates
one alias row per team with:

  - canonical_name = KenPom name (initial seed; Odds API name TBD when
    NCAAB season starts in November and games appear in /sports/ncaab)
  - kenpom_name    = same as canonical
  - bart_name      = same (Barttorvik mostly tracks KenPom naming)
  - odds_api_name  = NULL until we can scrape the Odds API team list
  - alt_names      = pre-filled common spelling variants for known cases

Idempotent — uses on_conflict=canonical_name to upsert, so re-running
just refreshes timestamps.

Usage:
    python ncaab_seed_aliases.py
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

# Reconfigure stdout for UTF-8 on Windows (cp1252 default crashes on emoji)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}


# Pre-mapped alt-spelling variants. Keys are the CANONICAL name (which
# matches KenPom's spelling); values are alt spellings used by Odds API,
# ESPN, casual references, etc. Anything not in this map gets an empty
# alt_names array and can be filled in later as mismatches surface.
#
# KenPom convention: "Saint X" not "St. X", formal names like
# "Connecticut"/"Mississippi"/"Pittsburgh" rather than UConn/Ole Miss/Pitt.
KNOWN_ALT_NAMES = {
    # Saint vs St. family — KenPom always uses "Saint", everyone else "St."
    "Saint Mary's":       ["St. Mary's", "Saint Mary's (CA)", "St Mary's"],
    "Saint John's":       ["St. John's", "St Johns"],
    "Saint Bonaventure":  ["St. Bonaventure"],
    "Saint Joseph's":     ["St. Joseph's", "St Joseph's"],
    "Saint Peter's":      ["St. Peter's"],
    "Saint Francis":      ["St. Francis", "St. Francis (PA)", "Saint Francis (PA)"],
    "Saint Louis":        ["St. Louis"],
    "Mount St. Mary's":   ["Mt. St. Mary's", "Mount Saint Mary's"],
    # Casual nicknames — KenPom uses formal name
    "Connecticut":        ["UConn"],
    "Mississippi":        ["Ole Miss"],
    "Pittsburgh":         ["Pitt"],
    "Pennsylvania":       ["Penn"],
    "Massachusetts":      ["UMass", "U Mass"],
    "Massachusetts Lowell": ["UMass Lowell", "Massachusetts-Lowell"],
    "Brigham Young":      ["BYU"],
    "Louisiana State":    ["LSU"],
    "Southern Methodist": ["SMU"],
    "Texas Christian":    ["TCU"],
    "Virginia Commonwealth": ["VCU"],
    "Central Florida":    ["UCF"],
    "Florida Atlantic":   ["FAU"],
    "Florida International": ["FIU"],
    "Southern California": ["USC", "Southern Cal"],
    "Nevada Las Vegas":   ["UNLV", "Nevada-Las Vegas"],
    "UC Santa Barbara":   ["UCSB"],
    "Texas San Antonio":  ["UTSA", "UT San Antonio", "Texas-San Antonio"],
    "UNC Greensboro":     ["UNCG", "North Carolina Greensboro"],
    "UNC Wilmington":     ["UNCW", "North Carolina Wilmington"],
    "East Tennessee St.": ["ETSU", "East Tennessee State"],
    "Alabama Birmingham": ["UAB", "Alabama-Birmingham"],
    # Odds API typically uses "X State" not "X St."
    "Penn St.":           ["Penn State", "Pennsylvania St."],
    "Ohio St.":           ["Ohio State"],
    "Michigan St.":       ["Michigan State"],
    "Mississippi St.":    ["Mississippi State"],
    "Florida St.":        ["Florida State"],
    "Iowa St.":           ["Iowa State"],
    "Kansas St.":         ["Kansas State"],
    "Oklahoma St.":       ["Oklahoma State"],
    "Oregon St.":         ["Oregon State"],
    "Arizona St.":        ["Arizona State"],
    "Washington St.":     ["Washington State"],
    "Colorado St.":       ["Colorado State"],
    "San Diego St.":      ["San Diego State"],
    "Utah St.":           ["Utah State"],
    "Boise St.":          ["Boise State"],
    "Fresno St.":         ["Fresno State"],
    "Wichita St.":        ["Wichita State"],
    "Kent St.":           ["Kent State"],
    "Wright St.":         ["Wright State"],
    "Cleveland St.":      ["Cleveland State"],
    "Murray St.":         ["Murray State"],
    "Morehead St.":       ["Morehead State"],
    "Ball St.":           ["Ball State"],
    "Indiana St.":        ["Indiana State"],
    "Illinois St.":       ["Illinois State"],
    "Wichita St.":        ["Wichita State"],
    "Youngstown St.":     ["Youngstown State"],
    "North Carolina St.": ["NC State", "North Carolina State"],
}


def fetch_kenpom_cache():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/kenpom_cache?cache_key=eq.kenpom_ratings_ff_2026&select=data,fetched_at",
        headers=HEADERS, timeout=15,
    )
    if r.status_code != 200:
        print(f"  Cache read error {r.status_code}: {r.text[:200]}")
        return []
    rows = r.json()
    if not rows:
        print("  ⚠️ kenpom_cache empty — run ncaab_pipeline.py first or check client cache")
        return []
    blob = rows[0].get("data") or []
    return blob if isinstance(blob, list) else []


def upsert_aliases(rows):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ncaab_team_aliases?on_conflict=canonical_name",
        headers=WRITE_HEADERS, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  Upsert error {r.status_code}: {r.text[:300]}")
        return False
    return True


def run():
    print("=== NCAAB alias seed ===")
    teams = fetch_kenpom_cache()
    if not teams:
        return
    print(f"  Pulling {len(teams)} teams from kenpom_cache...")

    rows = []
    for t in teams:
        kp_name = t.get("team")
        if not kp_name:
            continue
        rows.append({
            "canonical_name": kp_name,
            "kenpom_name": kp_name,
            "bart_name": kp_name,
            "odds_api_name": None,
            "alt_names": KNOWN_ALT_NAMES.get(kp_name, []),
            "conference": t.get("conf") or None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    # Batch upsert (Supabase handles 1000+ rows fine)
    if upsert_aliases(rows):
        print(f"  ✅ Seeded {len(rows)} alias rows")
        print(f"  ℹ️  odds_api_name is NULL — fill in when NCAAB season starts (Nov)")
    else:
        print(f"  ❌ Upsert failed")


if __name__ == "__main__":
    run()
