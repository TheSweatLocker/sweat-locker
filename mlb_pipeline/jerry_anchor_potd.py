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
from typing import Optional

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


# Sport → game context table registry (2026-08-03 sport-universalization).
# Add rows here as each sport's game_context table ships. Fallback path
# handles POTD winners from sports without a context table (best-effort;
# skips juice gate + context-details fetch, but still writes the POTD).
CONTEXT_TABLE_BY_SPORT = {
    "MLB": "mlb_game_context",
    "NFL": "nfl_game_context",        # migration applied 2026-08-03
    # "NBA": "nba_game_context",
    # "NCAAF": "ncaaf_game_context",
    # "NCAAB": "ncaab_game_context",
    # "UFC": "ufc_fight_context",
}


def _context_table(sport: str) -> Optional[str]:
    return CONTEXT_TABLE_BY_SPORT.get((sport or "").upper())


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

    # Pull Jerry reads across all eligible sports (POTD_SPORTS below).
    # UFC intentionally excluded per user 2026-07-31 — UFC stays a
    # standalone tab destination, not a cross-sport POTD candidate.
    # Add sports to POTD_SPORTS as their synthesizers ship.
    POTD_SPORTS = ["MLB", "NBA", "NFL", "NCAAF", "NCAAB"]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_reads",
        headers=H_READ,
        params={"sport": f"in.({','.join(POTD_SPORTS)})",
                "game_date": f"eq.{gd}",
                "select": "sport,game_id,call_text,call_market,call_side,call_line,"
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
        _write_no_play(gd, dry_run, reads)
        return

    # POTD JUICE GATE (2026-08-03 user directive): "-200 or juicier ML shouldn't
    # be POTD — they're often anti-consensus plays priced heavy by books."
    # Fetch ML for each eligible ML pick and filter out ones at -200+ juice.
    # Non-ML picks (total/rl/prop) unaffected.
    ml_picks = [r for r in eligible if (r.get("call_market") or "").lower() == "ml"]
    if ml_picks:
        # Route each ML pick to its sport-specific context table (2026-08-03
        # universalization). Skip juice gate for sports without a
        # context table registered — best-effort, don't block the POTD.
        ml_lookup = {}
        by_sport = {}
        for r in ml_picks:
            by_sport.setdefault((r.get("sport") or "MLB").upper(), []).append(r)
        for sport, sport_picks in by_sport.items():
            ctx_table = _context_table(sport)
            if not ctx_table: continue
            game_ids = list({p["game_id"] for p in sport_picks})
            in_str = ','.join(f'"{g}"' for g in game_ids)
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/{ctx_table}",
                headers=H_READ,
                params={"game_id": f"in.({in_str})", "game_date": f"eq.{gd}",
                        "select": "game_id,home_ml_close,away_ml_close"},
                timeout=15,
            )
            rows = resp.json() if resp.status_code == 200 else []
            for c in (rows if isinstance(rows, list) else []):
                ml_lookup[c["game_id"]] = c
        filtered = []
        skipped_juice = []
        for r in eligible:
            if (r.get("call_market") or "").lower() != "ml":
                filtered.append(r); continue
            ctx_row = ml_lookup.get(r["game_id"], {})
            side = (r.get("call_side") or "").upper()
            pick_ml = ctx_row.get("home_ml_close") if side == "HOME" else ctx_row.get("away_ml_close")
            if pick_ml is not None and pick_ml <= -200:
                ct = (r.get('call_text') or '?')[:30]
                skipped_juice.append(f"{ct} at {pick_ml}")
                continue
            filtered.append(r)
        if skipped_juice:
            print(f"  🚫 POTD juice gate: skipped {len(skipped_juice)} ML picks at -200+")
            for s in skipped_juice[:5]:
                print(f"      · {s}")
        eligible = filtered

    if not eligible:
        print(f"  ⚠ no eligible reads after juice gate — Jerry passing")
        _write_no_play(gd, dry_run, reads)
        return

    winner = eligible[0]
    gid = winner["game_id"]
    conv = winner["conviction"]
    call = winner.get("call_text") or "?"
    winner_sport = (winner.get("sport") or "MLB").upper()
    print(f"  🏆 POTD winner: {call} (conviction={conv}, game_id={gid}, sport={winner_sport})")

    # Route to sport-specific context table (2026-08-03 universalization).
    # Select fields available across sports; MLB-specific (venue, temperature,
    # nrfi_score, lineup_confirmed) may be null for non-MLB but PostgREST
    # returns them anyway.
    ctx_table = _context_table(winner_sport)
    if not ctx_table:
        print(f"  ⚠ no context table registered for sport={winner_sport} — aborting")
        return
    gc = requests.get(
        f"{SUPABASE_URL}/rest/v1/{ctx_table}",
        headers=H_READ,
        params={"game_id": f"eq.{gid}", "game_date": f"eq.{gd}",
                "select": "home_team,away_team"},
        timeout=10,
    )
    ctx_rows = gc.json() if gc.status_code == 200 else []
    if not ctx_rows:
        print("  ⚠ no game_context for winner — abort")
        return
    ctx = ctx_rows[0]

    # Build the payload — matches existing jerry_cache best_bet schema
    # 2026-08-02: include `matchup` explicitly so generate_sweat_card's
    # play_signature dedup can collapse POTD with the plain Jerry-anchored
    # ML slot on the same game (both are the same bet). Without this,
    # game field on POTD ended up None and dedup missed.
    matchup_str = f"{ctx['away_team']} @ {ctx['home_team']}"
    payload_data = {
        "sport": "MLB",
        "matchup": matchup_str,
        "game": {
            "away_team": ctx["away_team"],
            "home_team": ctx["home_team"],
            "matchup": matchup_str,
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

    # Upsert jerry_cache best_bet — actual unique constraint is on cache_key
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
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
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
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
