"""Jerry-anchored POTD (2026-07-31 · Sweat Card swap).

Runs AFTER generate_jerry_synthesis.py in the cron chain. Reads jerry_reads
for today, picks the highest-conviction call (>= threshold), and overwrites
best_bet_YYYY-MM-DD in jerry_cache with the Jerry-anchored payload.

Keeps the SAME jerry_cache schema so downstream (generate_tonight_card.py,
Sweat Card app render) continue reading without change — just now the pick
selection is based on jerry_reads.conviction instead of primary_play.tier
+ confluence composite.

Threshold policy (2026-07-31 user decision):
  - conviction >= 70 → eligible for POTD (POTD = MAX conviction across slate)
  - conviction 60-69 → no POTD (Jerry passes on the day, discipline preserved)
  - MARKET=pass rows never eligible

Sport support: MLB only for v1. NBA/NFL/NCAAF/NCAAB add when their Jerry
synthesizers ship.

Usage:
    python jerry_anchor_potd.py [--date YYYY-MM-DD] [--threshold 70] [--dry-run]
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

H_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json",
           "Prefer": "resolution=merge-duplicates,return=minimal"}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def _conviction_tier(conv: int) -> str:
    """Match downstream label shape (line 140 of generate_tonight_card.py:
       'Tier: {confidence.upper()} | Jerry {score}/100')."""
    if conv >= 80: return "prime"
    if conv >= 70: return "strong"
    if conv >= 60: return "solid"
    return "lean"


def run(game_date: str | None = None, threshold: int = 70,
        dry_run: bool = False) -> None:
    gd = game_date or today_et()
    print(f"=== jerry_anchor_potd · {gd} (threshold={threshold}) ===")

    # Pull all Jerry reads for the day, sorted by conviction DESC
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_reads",
        headers=H_READ,
        params={"sport": "eq.MLB", "game_date": f"eq.{gd}",
                "select": "game_id,call_text,call_market,call_side,call_line,"
                          "conviction,short_read,long_read,generated_at",
                "order": "conviction.desc"},
        timeout=15,
    )
    reads = r.json() if r.status_code == 200 else []
    print(f"  {len(reads)} jerry_reads on the slate")
    if not reads:
        print("  ⚠ no reads — POTD unchanged (falls back to whatever play_of_day picked)")
        return

    # Filter to eligible: conviction >= threshold, not a PASS
    eligible = [r for r in reads
                if (r.get("conviction") or 0) >= threshold
                and (r.get("call_market") or "").lower() != "pass"]
    if not eligible:
        print(f"  ⚠ no Jerry read at conviction >= {threshold} — Jerry passing on POTD today")
        # Write a "no play" payload
        _write_no_play(gd, dry_run, reads)
        return

    winner = eligible[0]
    gid = winner["game_id"]
    conv = winner["conviction"]
    call = winner.get("call_text") or "?"
    print(f"  🏆 POTD winner: {call} (conviction={conv}, game_id={gid})")

    # Fetch game context (team names, commence_time)
    gc = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context",
        headers=H_READ,
        params={"game_id": f"eq.{gid}", "game_date": f"eq.{gd}",
                "select": "home_team,away_team,venue,temperature,"
                          "nrfi_score,lineup_confirmed"},
        timeout=10,
    )
    ctx_rows = gc.json() if gc.status_code == 200 else []
    if not ctx_rows:
        print("  ⚠ no game_context for winner — abort")
        return
    ctx = ctx_rows[0]

    # Build the payload — matches existing jerry_cache best_bet schema
    payload_data = {
        "sport": "MLB",
        "game": {
            "away_team": ctx["away_team"],
            "home_team": ctx["home_team"],
            "commence_time": None,  # play_of_day carries this; Jerry doesn't need it
        },
        "leanDisplay": f"{call} (Jerry {conv}/100)",
        "score": {"total": conv, "source": "jerry_conviction"},
        "confidence": _conviction_tier(conv),
        "narrative": winner.get("short_read") or winner.get("long_read") or "",
        "context": {
            "venue": ctx.get("venue"),
            "temperature": ctx.get("temperature"),
            "nrfi_score": ctx.get("nrfi_score"),
            "lean_bet": winner.get("call_market"),
            "jerry_anchored": True,
            "conviction": conv,
        },
        "generatedAt": gd,
        "pipelineGenerated": True,
        "anchor": "jerry_synthesis_v1",
    }
    narrative_line = f"Play of the Day: {ctx['away_team']} @ {ctx['home_team']} | {call} (Jerry {conv})"

    if dry_run:
        print(f"  [DRY] would upsert best_bet_{gd} · {narrative_line}")
        return

    # Upsert jerry_cache best_bet
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
        headers=H_WRITE,
        json={
            "cache_key": f"best_bet_{gd}",
            "game_id": f"best_bet_{gd}",
            "sport": "MLB",
            "narrative": narrative_line,
            "data": payload_data,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠ jerry_cache upsert {r.status_code}: {r.text[:200]}")
        return
    print(f"  ✅ jerry_cache best_bet_{gd} anchored to Jerry")

    # Mirror to daily_best_bet_history
    try:
        hr = requests.post(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?on_conflict=bet_date",
            headers=H_WRITE,
            json={
                "bet_date": gd,
                "sport": "MLB",
                "game": f"{ctx['away_team']} @ {ctx['home_team']}",
                "lean": f"{call} (Jerry {conv}/100)",
                "sweat_score": conv,  # column name preserved; source is now Jerry
                "result": "Pending",
            },
            timeout=15,
        )
        if hr.status_code not in (200, 201, 204):
            print(f"  ⚠ history mirror {hr.status_code}: {hr.text[:200]}")
        else:
            print(f"  ✅ daily_best_bet_history mirrored")
    except Exception as e:
        print(f"  ⚠ history mirror failed: {e}")


def _write_no_play(game_date: str, dry_run: bool, reads: list) -> None:
    """Write a 'Jerry passing' payload when no read clears the threshold.
    Preserves the same shape play_of_day used for no-play days
    (top-level noPlay + reason keys)."""
    top_conv = max((r.get("conviction") or 0) for r in reads)
    reason = (f"Jerry's highest conviction today is {top_conv} — below the "
              f"POTD threshold. Full slate available; no headline play locked.")
    if dry_run:
        print(f"  [DRY] would upsert best_bet_{game_date} · noPlay=True · {reason}")
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
        headers=H_WRITE,
        json={
            "cache_key": f"best_bet_{game_date}",
            "game_id": f"best_bet_{game_date}",
            "sport": "MLB",
            "narrative": f"Jerry passing on POTD ({reason})",
            "data": {"noPlay": True, "reason": reason,
                     "anchor": "jerry_synthesis_v1", "generatedAt": game_date},
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"  ✅ POTD marked 'no play' by Jerry (top conv = {top_conv})")
    else:
        print(f"  ⚠ upsert {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--threshold", type=int, default=70)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(game_date=args.date, threshold=args.threshold, dry_run=args.dry_run)
