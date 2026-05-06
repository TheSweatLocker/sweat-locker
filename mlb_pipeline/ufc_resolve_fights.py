"""UFC fight resolver — scrape completed events for outcomes and store
to ufc_fight_results for training data + audit calibration.

Runs after each UFC card (Sunday cron post-Saturday). Pulls the most-
recently-completed event from UFCStats, parses each fight's result,
upserts to ufc_fight_results keyed by (event_name, fighter_a, fighter_b).

Schema: see supabase/migrations/20260506_ufc_fight_results.sql

Usage:
  python ufc_resolve_fights.py                     # latest completed event
  python ufc_resolve_fights.py --event-url <url>   # specific event by URL
"""
import os
import sys
import re
import argparse
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

BASE_URL = "http://www.ufcstats.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}


def get_latest_completed_event_url():
    """Fetch the most recent completed UFC event from UFCStats."""
    r = requests.get(f"{BASE_URL}/statistics/events/completed", headers=HEADERS, timeout=15)
    soup = BeautifulSoup(r.content, "html.parser")
    table = soup.find("table", class_="b-statistics__table-events")
    if not table:
        return None, None, None
    rows = table.find_all("tr", class_="b-statistics__table-row")
    for row in rows:
        link = row.find("a", class_="b-link")
        if link and link.get("href"):
            event_name = link.text.strip()
            event_url = link["href"]
            date_cell = row.find("span", class_="b-statistics__date")
            event_date = date_cell.text.strip() if date_cell else None
            return event_name, event_date, event_url
    return None, None, None


def parse_record(record_str):
    """Extract wins-losses-draws from '15-0-0' format. Strips '(N NC)' if present."""
    if not record_str:
        return None, None, None
    record_str = record_str.strip().split("(")[0].strip()
    parts = record_str.split("-")
    if len(parts) >= 3:
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            pass
    return None, None, None


def parse_event_results(event_url):
    """Scrape an event page and return list of fight result dicts."""
    r = requests.get(event_url, headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.content, "html.parser")

    # Event name + date
    title = soup.find("span", class_="b-content__title-highlight")
    event_name = title.text.strip() if title else None
    date_box = soup.find("li", class_="b-list__box-list-item")
    event_date_str = None
    if date_box:
        all_items = soup.find_all("li", class_="b-list__box-list-item")
        for it in all_items:
            txt = it.text.strip()
            if "Date:" in txt:
                event_date_str = txt.replace("Date:", "").strip()
                break

    # Parse each fight row
    fights = []
    fight_rows = soup.find_all("tr", class_="b-fight-details__table-row")
    fight_order = 0
    for row in fight_rows:
        # Skip header row (no clickable fighter links)
        fighter_links = []
        for a in row.find_all("a", class_="b-link"):
            href = a.get("href", "")
            if "/fighter-details/" in href:
                fighter_links.append({"name": a.text.strip(), "url": href})
        if len(fighter_links) < 2:
            continue
        fight_order += 1

        cells = row.find_all("td", class_="b-fight-details__table-col")

        # Determine winner from W/L flag column
        # Column 0 typically has W/L marker (text "win" / "loss")
        winner = None
        if cells:
            wl_text = cells[0].get_text(" ", strip=True).lower()
            if wl_text == "win":
                # Whichever fighter is on top of column = winner
                fighter_p_tags = cells[1].find_all("p", class_="b-fight-details__table-text") if len(cells) > 1 else []
                if fighter_p_tags:
                    winning_name = fighter_p_tags[0].text.strip()
                    if winning_name == fighter_links[0]["name"]:
                        winner = "a"
                    elif winning_name == fighter_links[1]["name"]:
                        winner = "b"
            elif "draw" in wl_text:
                winner = "draw"
            elif "nc" in wl_text or "no contest" in wl_text:
                winner = "no_contest"

        # Weight class (column for it varies by table version — try to find label)
        weight_class = None
        for c in cells:
            txt = c.get_text(" ", strip=True)
            for wc in ("Heavyweight", "Light Heavyweight", "Middleweight", "Welterweight",
                      "Lightweight", "Featherweight", "Bantamweight", "Flyweight",
                      "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
                      "Women's Featherweight", "Catch Weight", "Open Weight"):
                if wc in txt:
                    weight_class = wc
                    break
            if weight_class:
                break

        # Method, round, time — last 3 cells of row (typical layout)
        method = None
        method_detail = None
        round_num = None
        time_str = None
        if len(cells) >= 3:
            # Find METHOD col by looking for one with KO/TKO/SUB/DEC/etc
            for c in cells:
                t = c.get_text(" ", strip=True).upper()
                if "KO/TKO" in t or "TKO" in t:
                    method = "KO/TKO"
                    p_tags = c.find_all("p")
                    if len(p_tags) >= 2:
                        method_detail = p_tags[1].get_text(" ", strip=True) or None
                    break
                elif "SUB" in t and len(t) < 30:
                    method = "SUB"
                    p_tags = c.find_all("p")
                    if len(p_tags) >= 2:
                        method_detail = p_tags[1].get_text(" ", strip=True) or None
                    break
                elif "U-DEC" in t:
                    method = "U-DEC"; break
                elif "S-DEC" in t:
                    method = "S-DEC"; break
                elif "M-DEC" in t:
                    method = "M-DEC"; break
                elif " DQ" in t or t == "DQ":
                    method = "DQ"; break

            # Round: look for 1-digit cell
            for c in cells:
                t = c.get_text(" ", strip=True)
                if re.fullmatch(r"\d", t):
                    try:
                        round_num = int(t)
                    except ValueError:
                        pass
                    break

            # Time: mm:ss pattern
            for c in cells:
                t = c.get_text(" ", strip=True)
                m = re.fullmatch(r"\d{1,2}:\d{2}", t)
                if m:
                    time_str = t
                    break

        went_distance = method in ("U-DEC", "S-DEC", "M-DEC")

        fights.append({
            "event_name": event_name,
            "event_url": event_url,
            "fight_order": fight_order,
            "fighter_a": fighter_links[0]["name"],
            "fighter_b": fighter_links[1]["name"],
            "fighter_a_url": fighter_links[0]["url"],
            "fighter_b_url": fighter_links[1]["url"],
            "weight_class": weight_class,
            "winner": winner,
            "method": method,
            "method_detail": method_detail,
            "round": round_num,
            "time": time_str,
            "went_distance": went_distance,
        })

    return event_name, event_date_str, fights


def upload_fight(event_date_iso, fight):
    payload = dict(fight)
    payload["event_date"] = event_date_iso
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ufc_fight_results?on_conflict=event_name,fighter_a,fighter_b",
        headers=SUPABASE_HEADERS,
        json=payload,
        timeout=15,
    )
    return r.status_code in (200, 201, 204)


def parse_event_date(date_str):
    """Convert 'May 03, 2026' to '2026-05-03'."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def run(event_url=None):
    if event_url is None:
        print("Fetching latest completed event...")
        event_name, event_date, event_url = get_latest_completed_event_url()
        if not event_url:
            print("No completed event found")
            return
        print(f"Latest completed: {event_name} ({event_date})")

    name, date_str, fights = parse_event_results(event_url)
    iso_date = parse_event_date(date_str)
    print(f"Parsed: {name} ({date_str} -> {iso_date})  |  {len(fights)} fights")

    success = 0
    errors = 0
    for f in fights:
        if upload_fight(iso_date, f):
            success += 1
            method_str = f.get("method") or "?"
            r_str = f"R{f['round']}" if f.get('round') else "—"
            t_str = f.get("time") or "—"
            winner_name = f.get("fighter_a") if f.get("winner") == "a" else f.get("fighter_b") if f.get("winner") == "b" else f.get("winner")
            print(f"  ✅ {f['fighter_a']} vs {f['fighter_b']} — {winner_name} via {method_str} ({r_str} {t_str})")
        else:
            errors += 1
            print(f"  ❌ Upload failed: {f['fighter_a']} vs {f['fighter_b']}")

    print(f"\nDone! ✅ {success} fights stored, ❌ {errors} errors")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--event-url", help="Specific UFCStats event URL to resolve")
    args = p.parse_args()
    run(event_url=args.event_url)
