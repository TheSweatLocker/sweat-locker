"""Scrape every fighter's fight-by-fight history from UFCStats and store
to ufc_fighter_history. Required for pre-fight snapshot feature pipeline.

Pulls every unique fighter URL referenced in ufc_fight_results, fetches
their UFCStats page, parses the fight history table, and upserts rows
keyed on (fighter_url, fight_date, opponent_url).

Usage:
  python ufc_scrape_fighter_history.py                  # all unique fighters
  python ufc_scrape_fighter_history.py --limit 10       # test
  python ufc_scrape_fighter_history.py --skip-existing  # only new fighters
"""
import os
import sys
import re
import argparse
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}


def get_unique_fighters_from_results():
    """Pull unique fighter URLs from ufc_fight_results."""
    urls = {}
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ufc_fight_results"
            f"?select=fighter_a,fighter_b,fighter_a_url,fighter_b_url"
            f"&limit=1000&offset={offset}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        batch = r.json()
        if not batch or not isinstance(batch, list):
            break
        for f in batch:
            if f.get("fighter_a_url"):
                urls[f["fighter_a_url"]] = f["fighter_a"]
            if f.get("fighter_b_url"):
                urls[f["fighter_b_url"]] = f["fighter_b"]
        if len(batch) < 1000:
            break
        offset += 1000
    return urls


def get_existing_fighter_urls():
    """Pull urls already in ufc_fighter_history (for skip-existing mode)."""
    seen = set()
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ufc_fighter_history?select=fighter_url&limit=1000&offset={offset}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        batch = r.json()
        if not batch or not isinstance(batch, list):
            break
        for r_ in batch:
            seen.add(r_["fighter_url"])
        if len(batch) < 1000:
            break
        offset += 1000
    return seen


def parse_history_date(date_str):
    """UFCStats: 'May. 02, 2026' or 'May 02, 2026' → '2026-05-02'."""
    if not date_str:
        return None
    s = date_str.strip().replace(".", "")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_fighter_history(url, fighter_name):
    """Return list of fight dicts for this fighter's UFCStats career page."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.content, "html.parser")
    except Exception as e:
        print(f"  ❌ Fetch failed: {e}")
        return []

    fights = []
    rows = soup.find_all("tr", class_="b-fight-details__table-row")
    for row in rows:
        cells = row.find_all("td", class_="b-fight-details__table-col")
        if len(cells) < 9:
            continue

        # Column 0: W/L flag
        wl_text = cells[0].get_text(" ", strip=True).lower()
        if wl_text == "win":
            result = "win"
        elif wl_text == "loss":
            result = "loss"
        elif "draw" in wl_text:
            result = "draw"
        elif "nc" in wl_text:
            result = "no_contest"
        elif "next" in wl_text:
            result = "next"  # upcoming fight
        else:
            continue  # header row or unknown

        # Column 1: fighter (self) and opponent
        fighter_p_tags = cells[1].find_all("p", class_="b-fight-details__table-text")
        opponent_name = None
        opponent_url = None
        if len(fighter_p_tags) >= 2:
            opp_link = fighter_p_tags[1].find("a", class_="b-link")
            if opp_link:
                opponent_name = opp_link.get_text(" ", strip=True)
                opponent_url = opp_link.get("href")
            else:
                opponent_name = fighter_p_tags[1].get_text(" ", strip=True)

        # Strikes / TDs (cells 2-5 typically)
        sig_str = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
        td_str = cells[4].get_text(" ", strip=True) if len(cells) > 4 else ""

        # Method (cell 7) — 'KO/TKO\nPunches' format
        method_text = cells[7].get_text(" ", strip=True).upper() if len(cells) > 7 else ""
        if "KO/TKO" in method_text or "TKO" in method_text:
            method = "KO/TKO"
        elif "SUB" in method_text:
            method = "SUB"
        elif "U-DEC" in method_text:
            method = "U-DEC"
        elif "S-DEC" in method_text:
            method = "S-DEC"
        elif "M-DEC" in method_text:
            method = "M-DEC"
        elif "DQ" in method_text:
            method = "DQ"
        else:
            method = None

        # Round + time (cells 8-9)
        round_text = cells[8].get_text(" ", strip=True) if len(cells) > 8 else ""
        round_num = None
        if round_text.isdigit():
            round_num = int(round_text)
        time_text = cells[9].get_text(" ", strip=True) if len(cells) > 9 else None

        # Event + date (cell 6 typically: "Event Name\nMay. 02, 2026")
        event_cell = cells[6] if len(cells) > 6 else None
        event_name = None
        fight_date = None
        if event_cell:
            event_p_tags = event_cell.find_all("p", class_="b-fight-details__table-text")
            if event_p_tags:
                event_name = event_p_tags[0].get_text(" ", strip=True)
                if len(event_p_tags) > 1:
                    fight_date = parse_history_date(event_p_tags[1].get_text(" ", strip=True))

        if not fight_date and result != "next":
            # Skip rows we can't date (except upcoming)
            continue

        # Parse stat strings like "84 of 200" → landed/attempted
        def parse_x_of_y(s):
            m = re.match(r"(\d+)\s+of\s+(\d+)", s)
            return (int(m.group(1)), int(m.group(2))) if m else (None, None)

        sig_l, sig_a = parse_x_of_y(sig_str)
        td_l, td_a = parse_x_of_y(td_str)

        fights.append({
            "fighter_url": url,
            "fighter_name": fighter_name,
            "fight_date": fight_date,
            "event_name": event_name,
            "opponent_name": opponent_name,
            "opponent_url": opponent_url,
            "result": result,
            "method": method,
            "round": round_num,
            "time": time_text,
            "sig_strikes_landed": sig_l,
            "sig_strikes_attempted": sig_a,
            "takedowns_landed": td_l,
            "takedowns_attempted": td_a,
        })
    return fights


def upload_history(fights):
    if not fights:
        return 0
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ufc_fighter_history?on_conflict=fighter_url,fight_date,opponent_url",
        headers=SUPABASE_HEADERS,
        json=fights,
    )
    return len(fights) if r.status_code in (200, 201, 204) else 0


def run(limit=None, skip_existing=False):
    print("Fetching unique fighter URLs from ufc_fight_results...")
    fighters = get_unique_fighters_from_results()
    print(f"Found {len(fighters)} unique fighters")

    if skip_existing:
        existing = get_existing_fighter_urls()
        print(f"  {len(existing)} already scraped — skipping")
        fighters = {u: n for u, n in fighters.items() if u not in existing}
        print(f"  {len(fighters)} fighters remaining")

    items = list(fighters.items())
    if limit:
        items = items[:limit]

    total_fights = 0
    fighters_done = 0
    fighters_failed = 0
    for i, (url, name) in enumerate(items):
        fights = parse_fighter_history(url, name)
        if not fights:
            fighters_failed += 1
            print(f"  [{i+1}/{len(items)}] {name} — no fights parsed")
        else:
            uploaded = upload_history(fights)
            total_fights += uploaded
            fighters_done += 1
            if (i + 1) % 25 == 0 or i == 0:
                print(f"  [{i+1}/{len(items)}] {name} — {uploaded} fights | running total: {total_fights}")
        time.sleep(0.4)  # politeness

    print(f"\nDone! ✅ {fighters_done} fighters / {total_fights} fights stored, ❌ {fighters_failed} failed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="Cap fighter count (testing)")
    p.add_argument("--skip-existing", action="store_true", help="Only scrape fighters not yet in ufc_fighter_history")
    args = p.parse_args()
    run(limit=args.limit, skip_existing=args.skip_existing)
