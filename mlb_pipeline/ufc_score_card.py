"""UFC v1 — score the upcoming card and persist to ufc_picks table.

Runs after ufc_card_scraper.py on Thursday cron. Pulls latest event from
ufc_upcoming_event, runs all 4 models per fight, computes conviction
tier + edge calls, upserts to ufc_picks. App reads via
get_todays_ufc_picks() RPC.
"""
import os
import sys
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# UTF-8 stdout on Windows so unicode fighter names / event names (Medić,
# Rębecki, Milošević, etc.) don't crash the print statements. cp1252 default
# on Windows Python — no-op on Linux cron.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
SUPABASE_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ufc_predict import predict_fight


# Conviction tiering for winner pick
def winner_tier(p_winner):
    """Return (conviction 0-100, tier_label, picked_side)."""
    edge = abs(p_winner - 0.5)
    side = "a" if p_winner >= 0.5 else "b"
    p_picked = max(p_winner, 1 - p_winner)
    # Conviction scaled so 50% = 0, 70% = 70, 80%+ = 90
    conviction = int(min(100, max(0, (p_picked - 0.5) * 200)))
    if p_picked >= 0.70:
        tier = "PRIME"
    elif p_picked >= 0.62:
        tier = "STRONG"
    elif p_picked >= 0.55:
        tier = "LEAN"
    else:
        tier = None
    return conviction, tier, side


def edge_method(p_ko, p_sub, p_dec):
    """Return edge label if any method exceeds threshold above its base rate.
    Base rates from training data: KO 30.5%, SUB 16%, DEC 53.5%.
    Threshold: 1.4x base rate."""
    if p_ko >= 0.43:
        return "KO/TKO"
    if p_sub >= 0.30:
        return "SUB"
    if p_dec >= 0.65:
        return "DEC"
    return None


def edge_distance(p_distance):
    """Return OVER/UNDER if model has strong distance signal."""
    if p_distance >= 0.65:
        return "OVER"
    if p_distance <= 0.35:
        return "UNDER"
    return None


def fetch_upcoming_event():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/ufc_upcoming_event?select=*&order=updated_at.desc&limit=1",
        headers=HEADERS, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


def fetch_fighter_url(name):
    """Look up fighter_url via ufc_fighter_stats.

    2026-07-27 fix: fighter names in ufc_fighter_stats are stored
    accent-stripped (Uros Medic, not Uroš Medić) but ESPN's scraper
    feeds us accented names. Plain ILIKE with accented input misses
    accent-stripped rows. Try three lookups in order:
      1. Exact name as-given (in case DB has both variants)
      2. Accent-stripped version (catches Medić → Medic etc.)
      3. Last-name-only fallback (last-ditch fuzzy)
    """
    import unicodedata
    def _strip(s):
        return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')

    tries = [name, _strip(name)]
    # Only add last-name if it's a plausibly-unique last name (5+ chars)
    stripped_last = _strip(name).split()[-1] if name.split() else ''
    if len(stripped_last) >= 5:
        tries.append(stripped_last)

    seen = set()
    for candidate in tries:
        if candidate in seen or not candidate: continue
        seen.add(candidate)
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ufc_fighter_stats",
            headers=HEADERS,
            params={"fighter_name": f"ilike.{candidate}", "select": "fighter_url", "limit": "1"},
            timeout=10,
        )
        d = r.json() if r.status_code == 200 else []
        if d:
            return d[0]["fighter_url"]
    return None


def parse_event_iso(date_str):
    if not date_str:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str.replace(".", "").strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def upload_pick(pick):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ufc_picks?on_conflict=event_name,fighter_a,fighter_b",
        headers=SUPABASE_HEADERS,
        json=pick,
        timeout=15,
    )
    return r.status_code in (200, 201, 204)


def run():
    event = fetch_upcoming_event()
    if not event:
        print("No upcoming event")
        return
    print(f"=== Scoring {event['event_name']} ({event.get('event_date')}) ===")

    fight_card = event.get("fight_card")
    if isinstance(fight_card, str):
        fight_card = json.loads(fight_card)

    iso_date = parse_event_iso(event.get("event_date"))
    if not iso_date:
        print(f"  ⚠️ Could not parse event date: {event.get('event_date')}")
        return

    # Map fighter names → URLs
    name_to_url = {}
    for f in fight_card:
        for nm in (f.get("fighter1"), f.get("fighter2")):
            if nm and nm not in name_to_url:
                url = fetch_fighter_url(nm)
                if url:
                    name_to_url[nm] = url

    success = 0
    skipped = 0
    for i, f in enumerate(fight_card):
        a_name = f.get("fighter1") or "?"
        b_name = f.get("fighter2") or "?"
        a_url = name_to_url.get(a_name)
        b_url = name_to_url.get(b_name)
        if not (a_url and b_url):
            print(f"  ⚠️ Missing URLs: {a_name} vs {b_name}")
            skipped += 1
            continue

        rounds_sched = 5 if i == 0 else 3
        pred = predict_fight(a_url, b_url, iso_date, rounds_sched)
        if "error" in pred:
            print(f"  ⚠️ {a_name} vs {b_name}: {pred['error']}")
            skipped += 1
            continue

        p_winner_a = pred.get("p_winner_a", 0.5)
        conv, tier, side = winner_tier(p_winner_a)
        em = edge_method(pred.get("p_method_ko", 0), pred.get("p_method_sub", 0), pred.get("p_method_dec", 0))
        ed = edge_distance(pred.get("p_distance", 0.5))

        pick = {
            "event_name": event["event_name"],
            "event_date": iso_date,
            "fight_order": i + 1,
            "fighter_a": a_name,
            "fighter_b": b_name,
            "fighter_a_url": a_url,
            "fighter_b_url": b_url,
            "p_winner_a": round(p_winner_a, 4),
            "p_method_ko": round(pred.get("p_method_ko", 0), 4),
            "p_method_sub": round(pred.get("p_method_sub", 0), 4),
            "p_method_dec": round(pred.get("p_method_dec", 0), 4),
            "p_distance": round(pred.get("p_distance", 0), 4),
            "p_round_1": round(pred.get("p_round_1", 0), 4),
            "p_round_2": round(pred.get("p_round_2", 0), 4),
            "p_round_3": round(pred.get("p_round_3", 0), 4),
            "p_round_4": round(pred.get("p_round_4", 0), 4),
            "p_round_5": round(pred.get("p_round_5", 0), 4),
            "conviction_winner": conv,
            "tier_winner": tier,
            "recommended_side": side,
            "edge_method": em,
            "edge_distance": ed,
        }
        if upload_pick(pick):
            success += 1
            picked_name = a_name if side == "a" else b_name
            extra = []
            if em: extra.append(f"method={em}")
            if ed: extra.append(f"distance={ed}")
            extra_str = " | ".join(extra)
            tier_str = f"({tier})" if tier else ""
            win_prob = p_winner_a if side == "a" else (1 - p_winner_a)
            print(f"  ✅ {a_name} vs {b_name} → {picked_name} win {win_prob:.1%} (conv {conv}) {tier_str} {extra_str}")
        else:
            skipped += 1
            print(f"  ❌ Upload failed: {a_name} vs {b_name}")

    print(f"\nDone! ✅ {success} picks stored, ⚠️ {skipped} skipped")


if __name__ == "__main__":
    run()
