"""
Validation report: what cohort rules fire on today's slate.

Phase 1 read-only check. Runs every game in mlb_game_context for today
through cohort_signals.evaluate_game_for_play() and prints the top
matched rules per play type. Lets the user eyeball signal strength
before Phase 2 wires conviction adjustments.

USAGE:
    python _today_cohort_matches.py
    python _today_cohort_matches.py --date 2026-06-08
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def main():
    date = today_et()
    if "--date" in sys.argv:
        try: date = sys.argv[sys.argv.index("--date") + 1]
        except (IndexError, ValueError): pass

    print(f"=== Cohort matches for {date} ===\n")

    games = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context"
        f"?game_date=eq.{date}&select=*&order=sweat_score.desc",
        headers=HEADERS, timeout=20,
    ).json()
    if not games:
        print(f"  No games found for {date}")
        return

    from cohort_signals import evaluate_game_for_play, summarize_for_struct

    plays = [
        ("v3_tot", "v3 totals"),
        ("v4_tot", "v4 totals"),
        ("jerry_tot", "Jerry totals"),
        ("v3_ml", "v3 ML"),
        ("v4_ml", "v4 ML"),
        ("jerry_ml", "Jerry ML"),
        ("conf_ml", "Conf ML"),
        ("v3_rl", "v3 RL"),
        ("v4_rl", "v4 RL"),
        ("conf_rl", "Conf RL"),
    ]

    total_locks = 0
    total_fades = 0

    for g in games:
        away = g.get("away_team") or "?"
        home = g.get("home_team") or "?"
        sweat = g.get("sweat_score") or 0
        tier = g.get("sweat_tier") or "?"
        print(f"\n{'='*120}")
        print(f"  {away} @ {home}   sweat {sweat}/{tier}   conf={g.get('signal_confluence_net'):+d}")
        print(f"  SP: {g.get('away_pitcher')} (away) vs {g.get('home_pitcher')} (home)")
        print(f"  v3T={g.get('projected_total')} v4T={g.get('model_pred_total')} JT={g.get('jerry_pred_total')} "
              f"| line={g.get('close_total') or g.get('open_total')}")
        print(f"{'='*120}")

        any_match = False
        for play, label in plays:
            matches = evaluate_game_for_play(g, play)
            locks = [m for m in matches if m["tier"] == "LOCK"]
            strongs = [m for m in matches if m["tier"] == "STRONG_EDGE"]
            fades = [m for m in matches if m["tier"] in ("FADE", "HARD_FADE")]
            soft_fades = [m for m in matches if m["tier"] == "SOFT_FADE"]

            if not (locks or strongs or fades):
                continue
            any_match = True

            total_locks += len(locks)
            total_fades += len(fades)

            print(f"\n  [{label}]")
            for m in (locks + strongs)[:3]:
                tier_disp = "LOCK" if m["tier"] == "LOCK" else "STRONG"
                print(f"     [{tier_disp:<7}] {m['matches_if_raw']:<46} "
                      f"shrunk {m['shrunken_pct']}%  raw {m['raw_pct']}%/{m['raw_n']}  Δ{m['conviction_delta']:+d}  "
                      f"({m.get('direction', 'any')})")
            for m in fades[:2]:
                print(f"     [FADE   ] {m['matches_if_raw']:<46} "
                      f"shrunk {m['shrunken_pct']}%  raw {m['raw_pct']}%/{m['raw_n']}  Δ{m['conviction_delta']:+d}  "
                      f"({m.get('direction', 'any')})")
            for m in soft_fades[:1]:
                print(f"     [soft   ] {m['matches_if_raw']:<46} "
                      f"shrunk {m['shrunken_pct']}%  raw {m['raw_pct']}%/{m['raw_n']}  Δ{m['conviction_delta']:+d}  "
                      f"({m.get('direction', 'any')})")
        if not any_match:
            print("  (no LOCK / STRONG_EDGE / FADE matches today)")

    print(f"\n\n{'='*120}")
    print(f"SLATE SUMMARY:  {total_locks} LOCK matches, {total_fades} hard-fade matches across {len(games)} games")
    print(f"{'='*120}")


if __name__ == "__main__":
    main()
