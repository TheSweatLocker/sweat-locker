"""UFC card scraper v2 — ESPN API source.

Replaces ufc_card_scraper.py which hit a proof-of-work JS challenge on
ufcstats.com starting June 2026. ESPN's public scoreboard endpoint
returns the upcoming UFC event + all scheduled fights with structured
JSON — no HTML parsing, no challenge.

Endpoint:
    https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard

Returns:
    {event_name, event_date, venue, fights: [{a_name, b_name, weight_class}, ...]}

Limitations vs old scraper:
    - No fighter URLs (ESPN has IDs but they're internal)
    - No detailed records / DOB / stance (need a 2nd call per fighter)
    - But event + fight list IS reliably scraped daily

Run order in pipeline:
    1. fetch_upcoming_card() — pull from ESPN
    2. upsert_event + upsert_fights to ufc_picks table

Trigger: replace ufc_card_scraper.py call in the workflow with this module.
"""
import os
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

H_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_upcoming_card():
    """Hit ESPN's UFC scoreboard endpoint and return the next event + fights.

    Returns dict like:
        {
          "event_name": "UFC Fight Night: Muhammad vs. Bonfim",
          "event_date": "2026-06-06",
          "venue": "Meta APEX",
          "fights": [
            {"fight_order": 1, "fighter_a": "...", "fighter_b": "..."},
            ...
          ]
        }
    Returns None if no event is found or the call fails.
    """
    try:
        r = requests.get(ESPN_URL, headers=UA, timeout=15)
        if r.status_code != 200:
            print(f"  ESPN returned {r.status_code}")
            return None
        d = r.json()
    except Exception as e:
        print(f"  ESPN fetch failed: {e}")
        return None

    events = d.get("events", [])
    if not events:
        print("  ESPN: no upcoming events listed")
        return None

    # Take the first event — ESPN returns them in chronological order
    e = events[0]
    event_name = e.get("name", "UFC Event")
    event_date_full = e.get("date", "")
    event_date = event_date_full[:10] if event_date_full else None
    venue = "?"
    comps = e.get("competitions", []) or []
    if comps:
        v = comps[0].get("venue") or {}
        venue = v.get("fullName") or v.get("displayName") or "?"

    fights = []
    for i, comp in enumerate(comps):
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            continue
        a = competitors[0].get("athlete") or {}
        b = competitors[1].get("athlete") or {}
        a_name = a.get("displayName")
        b_name = b.get("displayName")
        if not a_name or not b_name:
            continue
        # Weight class (when ESPN exposes it)
        weight_class = None
        for note in (comp.get("notes") or []):
            headline = note.get("headline") or ""
            if "weight" in (note.get("type") or "").lower() or "weight" in headline.lower():
                weight_class = headline
                break
        # ESPN lists fights bottom-to-top by default; we'll preserve as-is and
        # let scoring decide order.
        fights.append({
            "fight_order": i + 1,
            "fighter_a": a_name,
            "fighter_b": b_name,
            "weight_class": weight_class,
        })

    if not fights:
        print(f"  ESPN: event found ({event_name}) but no fights parsed")
        return None

    return {
        "event_name": event_name,
        "event_date": event_date,
        "venue": venue,
        "fights": fights,
    }


def upsert_card_to_picks(card):
    """Write each fight to ufc_picks with placeholder fields. The downstream
    ufc_features + ufc_predict scripts will fill in fighter histories,
    probabilities, and recommendations."""
    if not card or not card.get("fights"):
        print("  Nothing to upsert.")
        return 0
    written = 0
    now = datetime.now(timezone.utc).isoformat()
    # No unique constraint on (event_date, fighter_a, fighter_b) so we do a
    # check-then-insert pattern rather than upsert. Skip rows that already
    # exist for the same event_date + fighter pair (downstream features /
    # predictions fill them in — don't overwrite predictions with empty rows).
    for f in card["fights"]:
        # Look up existing
        try:
            from urllib.parse import quote
            check_url = (
                f"{SUPABASE_URL}/rest/v1/ufc_picks?"
                f"event_date=eq.{card['event_date']}"
                f"&fighter_a=eq.{quote(f['fighter_a'])}"
                f"&fighter_b=eq.{quote(f['fighter_b'])}"
                f"&select=id"
            )
            existing = requests.get(check_url, headers=H_READ, timeout=10).json()
            if existing:
                continue  # already there
        except Exception:
            pass  # fall through and try the insert anyway

        payload = {
            "event_name": card["event_name"],
            "event_date": card["event_date"],
            "fighter_a": f["fighter_a"],
            "fighter_b": f["fighter_b"],
            "fight_order": f["fight_order"],
            "generated_at": now,
        }
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/ufc_picks",
                headers=H_WRITE,
                json=payload,
                timeout=10,
            )
            if r.status_code in (200, 201, 204):
                written += 1
            else:
                print(f"  insert {f['fighter_a']} vs {f['fighter_b']}: {r.status_code} {r.text[:120]}")
        except Exception as e:
            print(f"  insert failed for {f['fighter_a']} vs {f['fighter_b']}: {e}")
    return written


def run():
    print("=== UFC card scraper v2 (ESPN API) ===")
    card = fetch_upcoming_card()
    if not card:
        print("No event scraped; UFC pipeline will surface empty card.")
        return
    print(f"Event: {card['event_name']}  date: {card['event_date']}  venue: {card['venue']}")
    print(f"Fights ({len(card['fights'])}):")
    for f in card["fights"]:
        print(f"  {f['fight_order']:>2}. {f['fighter_a']} vs {f['fighter_b']}")
    n = upsert_card_to_picks(card)
    print(f"Upserted {n}/{len(card['fights'])} fights to ufc_picks.")


if __name__ == "__main__":
    run()
