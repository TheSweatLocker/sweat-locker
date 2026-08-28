"""UFC fight history backfill — scrape completed events back N days
and store all fight outcomes to ufc_fight_results.

Default: last 730 days (~2 years, ~120-135 events, ~1500 fights). Enough
training data to ship v1 fight-outcome model right away instead of
accumulating week-by-week.

Usage:
  python ufc_backfill_history.py                  # default last 730 days
  python ufc_backfill_history.py --days 365       # last year only
  python ufc_backfill_history.py --since 2024-01-01
"""
import os
import sys
import time
import argparse
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Reuse the parser from the resolver
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ufc_resolve_fights import parse_event_results, parse_event_date, upload_fight, BASE_URL, HEADERS

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def list_completed_events(cutoff_date):
    """Iterate UFCStats /statistics/events/completed and yield (name, date_str, url)
    for events ON OR AFTER cutoff_date (datetime.date)."""
    page = 1
    while True:
        url = f"{BASE_URL}/statistics/events/completed?page={page}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.content, "html.parser")
        table = soup.find("table", class_="b-statistics__table-events")
        if not table:
            break
        rows = table.find_all("tr", class_="b-statistics__table-row")
        if not rows:
            break

        any_in_range = False
        for row in rows:
            link = row.find("a", class_="b-link")
            if not link or not link.get("href"):
                continue
            event_name = link.text.strip()
            date_cell = row.find("span", class_="b-statistics__date")
            event_date_str = date_cell.text.strip() if date_cell else None
            event_iso = parse_event_date(event_date_str)
            if not event_iso:
                continue
            try:
                event_dt = datetime.strptime(event_iso, "%Y-%m-%d").date()
            except ValueError:
                continue
            if event_dt < cutoff_date:
                # Past the cutoff; stop iterating
                return
            any_in_range = True
            yield event_name, event_date_str, link["href"]

        # If no events on this page were in range, we're done
        if not any_in_range:
            return
        page += 1
        time.sleep(0.5)  # be polite


def run(days=730, since=None, max_events=None):
    if since:
        cutoff = datetime.strptime(since, "%Y-%m-%d").date()
    else:
        cutoff = (datetime.now() - timedelta(days=days)).date()
    print(f"Backfilling UFC events since {cutoff}...")

    events = list(list_completed_events(cutoff))
    if max_events:
        events = events[:max_events]
    print(f"Found {len(events)} events in range")

    total_fights = 0
    total_uploaded = 0
    total_errors = 0
    for i, (name, date_str, url) in enumerate(events):
        ename, edate, fights = parse_event_results(url)
        iso = parse_event_date(edate or date_str)
        ev_uploaded = 0
        for f in fights:
            if upload_fight(iso, f):
                ev_uploaded += 1
                total_uploaded += 1
            else:
                total_errors += 1
        total_fights += len(fights)
        print(f"  [{i+1}/{len(events)}] {name} ({date_str}) — {ev_uploaded}/{len(fights)} stored")
        time.sleep(0.5)  # politeness

    print(f"\nDone! ✅ {total_uploaded}/{total_fights} fights stored, ❌ {total_errors} errors")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=730, help="Days back to backfill (default 730)")
    p.add_argument("--since", help="Backfill since YYYY-MM-DD (overrides --days)")
    p.add_argument("--max-events", type=int, help="Cap event count (testing)")
    args = p.parse_args()
    run(days=args.days, since=args.since, max_events=args.max_events)
