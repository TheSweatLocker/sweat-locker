"""Decoupled MLB line poller.

Polls the Odds API every run, stores the full line state for every
event in mlb_line_history, and updates derived current_* fields on
mlb_game_context. Runs independent of the main cron (separate schedule).

Designed to run every 15 minutes throughout the day. At ~9 events/day and
~30 polls in the active window, that's ~270 API calls/day per event
endpoint — well within most Odds API tier limits.

When a game transitions to "In Progress" status, the most recent
pre-game line is locked as close_total. Prevents the "close_total NULL"
problem the morning/afternoon cron has when it skips live games.
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SB_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
SB_WRITE = {**SB_READ, "Content-Type": "application/json", "Prefer": "return=minimal"}


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def fetch_slate_odds():
    """Pull all upcoming MLB events WITH totals/spreads/h2h markets in ONE
    API call instead of N per-event calls.

    Returns the full odds response (list of events, each with bookmakers
    embedded). 6/5 swap: was using events listing + per-event odds calls
    (~6-8 calls per poll). Slate endpoint returns the same data in 1 call,
    cutting Odds API cost 6-8x. Same regions / markets / format params.
    """
    if not ODDS_API_KEY:
        print("  ⚠️ ODDS_API_KEY missing")
        return []
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us,us2",
                "markets": "totals,spreads,h2h",
                "oddsFormat": "american",
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  ⚠️ slate odds fetch HTTP {r.status_code}: {r.text[:200]}")
            return []
        return r.json() or []
    except Exception as e:
        print(f"  ⚠️ slate odds fetch failed: {e}")
        return []


def fetch_event_odds(event_id):
    """Pull totals + spreads + h2h for a single event. Median across books."""
    if not ODDS_API_KEY:
        return None
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us,us2",
                "markets": "totals,spreads,h2h",
                "oddsFormat": "american",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def median_across_books(odds_data):
    """Aggregate market values across bookmakers — take the median.

    Returns dict with total_line, over_odds, under_odds, spread,
    spread_home_odds, spread_away_odds, home_ml, away_ml, source_book."""
    totals = []      # list of (line, over_odds, under_odds, book_title)
    spreads = []     # list of (line, home_odds, away_odds, book_title)
    mls = []         # list of (home_ml, away_ml, book_title)
    home_team = odds_data.get("home_team")
    away_team = odds_data.get("away_team")
    for bm in odds_data.get("bookmakers", []) or []:
        book = bm.get("title") or bm.get("key")
        for mkt in bm.get("markets", []) or []:
            key = mkt.get("key")
            if key == "totals":
                # outcomes: [{name:'Over',point:X,price:Y},{name:'Under',point:X,price:Y}]
                over_o = under_o = line = None
                for o in mkt.get("outcomes", []) or []:
                    if o.get("name") == "Over":
                        over_o = o.get("price"); line = o.get("point")
                    elif o.get("name") == "Under":
                        under_o = o.get("price")
                if line is not None and over_o and under_o:
                    totals.append((float(line), int(over_o), int(under_o), book))
            elif key == "spreads":
                # outcomes named by team
                home_pt = home_o = away_o = None
                for o in mkt.get("outcomes", []) or []:
                    if o.get("name") == home_team:
                        home_pt = o.get("point"); home_o = o.get("price")
                    elif o.get("name") == away_team:
                        away_o = o.get("price")
                if home_pt is not None and home_o and away_o:
                    spreads.append((float(home_pt), int(home_o), int(away_o), book))
            elif key == "h2h":
                home_ml = away_ml = None
                for o in mkt.get("outcomes", []) or []:
                    if o.get("name") == home_team:
                        home_ml = o.get("price")
                    elif o.get("name") == away_team:
                        away_ml = o.get("price")
                if home_ml and away_ml:
                    mls.append((int(home_ml), int(away_ml), book))

    def median(values):
        if not values: return None
        s = sorted(values)
        return s[len(s) // 2]

    out = {}
    if totals:
        # Median by line first; match odds to the median-line entry
        lines = sorted(t[0] for t in totals)
        median_line = lines[len(lines) // 2]
        matches = [t for t in totals if t[0] == median_line]
        match = matches[len(matches) // 2]
        out["total_line"] = match[0]
        out["over_odds"] = match[1]
        out["under_odds"] = match[2]
        out["source_book"] = match[3]
    if spreads:
        spread_lines = sorted(s[0] for s in spreads)
        median_spread = spread_lines[len(spread_lines) // 2]
        sp_matches = [s for s in spreads if s[0] == median_spread]
        sp_match = sp_matches[len(sp_matches) // 2]
        out["spread"] = sp_match[0]
        out["spread_home_odds"] = sp_match[1]
        out["spread_away_odds"] = sp_match[2]
    if mls:
        out["home_ml"] = median(m[0] for m in mls)
        out["away_ml"] = median(m[1] for m in mls)
    return out if out else None


def write_line_history(game_id, game_date, odds_snapshot):
    """Insert a row in mlb_line_history."""
    if not odds_snapshot: return False
    row = {
        "game_id": game_id,
        "game_date": game_date,
        **odds_snapshot,
    }
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_line_history",
            headers=SB_WRITE,
            json=row,
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def update_game_context(game_id, game_date, snapshot, set_close=False):
    """Update current_total/current_total_at/line_movement/line_movements_count
    on mlb_game_context. Optionally set close_total when set_close=True
    (called when game transitions to In Progress)."""
    if not snapshot or snapshot.get("total_line") is None:
        return
    # Read current state to compute movement and increment counter
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context"
            f"?game_id=eq.{game_id}&game_date=eq.{game_date}"
            f"&select=open_total,current_total,line_movements_count,close_total",
            headers=SB_READ, timeout=10,
        )
        if r.status_code != 200 or not r.json():
            return
        row = r.json()[0]
    except Exception:
        return

    new_line = float(snapshot["total_line"])
    prev_current = row.get("current_total")
    open_t = row.get("open_total")
    moves = row.get("line_movements_count") or 0
    if prev_current is None or float(prev_current) != new_line:
        moves += 1
    payload = {
        "current_total": new_line,
        "current_total_at": datetime.now(timezone.utc).isoformat(),
        "line_movements_count": moves,
    }
    if open_t is not None:
        payload["line_movement"] = round(new_line - float(open_t), 2)
    if set_close and not row.get("close_total"):
        payload["close_total"] = new_line
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context"
            f"?game_id=eq.{game_id}&game_date=eq.{game_date}",
            headers=SB_WRITE, json=payload, timeout=10,
        )
    except Exception:
        pass


def map_event_to_game(event, ctx_rows):
    """Match an Odds API event to a mlb_game_context row by (home, away).
    Returns (game_id, game_date) or (None, None)."""
    eh = (event.get("home_team") or "").strip()
    ea = (event.get("away_team") or "").strip()
    for ctx in ctx_rows:
        if (ctx.get("home_team") or "").strip() == eh and (ctx.get("away_team") or "").strip() == ea:
            return ctx.get("game_id"), ctx.get("game_date")
    return None, None


def is_game_starting_soon(event):
    """True when commence_time is within the next 10 minutes — use this
    to trigger close_total locking."""
    ct = event.get("commence_time")
    if not ct: return False
    try:
        t = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
        return 0 < (t - datetime.now(timezone.utc)).total_seconds() <= 600
    except Exception:
        return False


def is_pre_game(event):
    """True when commence_time is in the future. Skip in-progress and
    finished games — their Odds API "total_line" can include alt lines
    or live remaining-runs lines that aren't comparable to the pre-game
    full-game total (SFG @ MIL 6/4: 18.5 line read while game was
    6-3 in progress)."""
    ct = event.get("commence_time")
    if not ct: return False
    try:
        t = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
        return (t - datetime.now(timezone.utc)).total_seconds() > 0
    except Exception:
        return False


def run():
    print(f"=== Line poller {today_et()} {datetime.now(timezone.utc).isoformat()[:19]}Z ===")
    if not ODDS_API_KEY:
        print("  ⚠️ no ODDS_API_KEY — abort")
        return
    # Pull today's context rows for matching
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context"
            f"?game_date=eq.{today_et()}&select=game_id,game_date,home_team,away_team",
            headers=SB_READ, timeout=15,
        )
        ctx_rows = r.json() if r.status_code == 200 else []
    except Exception:
        ctx_rows = []
    print(f"  {len(ctx_rows)} context rows on slate")

    # 6/5: switched from events-list + per-event-odds (N+1 calls per poll)
    # to single slate-odds endpoint (1 call per poll). Each event in the
    # response carries its bookmakers/markets inline — same data shape as
    # the per-event endpoint, just batched.
    slate = fetch_slate_odds()
    print(f"  {len(slate)} upcoming events from slate endpoint (1 API call)")

    # Per-book writer for sharp/public divergence signal (2026-08-07).
    # Runs alongside the median-aggregate flow, writing to book_lines
    # only when a book's price CHANGES vs its most recent stored value.
    # Silently no-ops if the book_lines migration hasn't been applied.
    try:
        from book_lines_writer import write_book_lines
        _BL_AVAILABLE = True
    except ImportError:
        _BL_AVAILABLE = False

    polled = 0
    closed = 0
    skipped_inprog = 0
    bl_rows_written = 0
    bl_books_scanned = 0
    for ev in slate:
        game_id, game_date = map_event_to_game(ev, ctx_rows)
        if not game_id: continue
        if not is_pre_game(ev):
            skipped_inprog += 1
            continue
        # ev is already the odds response — no per-event API call needed
        snap = median_across_books(ev)
        if not snap: continue
        write_line_history(game_id, game_date, snap)
        set_close = is_game_starting_soon(ev)
        update_game_context(game_id, game_date, snap, set_close=set_close)
        if set_close: closed += 1
        polled += 1
        # Per-book path (parallel, non-blocking on failure)
        if _BL_AVAILABLE:
            try:
                bl_stats = write_book_lines(ev, sport='MLB',
                                            game_id=game_id, game_date=game_date)
                bl_rows_written += bl_stats.get('rows_written', 0)
                bl_books_scanned += bl_stats.get('books_scanned', 0)
            except Exception as e:
                print(f"    ⚠ book_lines writer error on {game_id[:10]}: {e}")

    print(f"  ✓ polled {polled} events, locked close_total on {closed}, skipped {skipped_inprog} in-progress")
    if _BL_AVAILABLE:
        print(f"  📚 book_lines: {bl_rows_written} rows written (change-only) across {bl_books_scanned} book-events scanned")


if __name__ == "__main__":
    run()
