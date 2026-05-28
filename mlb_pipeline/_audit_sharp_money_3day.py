"""Sharp-money effectiveness audit — multi-day breakdown.

User explicit ask (2026-05-27): "I am providing the public sharp data for
personal use not to ahng our hat on... What is the takeaways from it as we
have seen last couple days, is it effective, how does it breakdown."

This script answers three questions:
  1. If you blindly bet the sharp side ML every game, what's your record?
  2. Does the effect strengthen with bigger sharp diffs (≥15%)?
  3. When OUR model agrees with sharps, do we win more than when it fades?

AN data must be pasted manually below per slate date — Action Network does
not have a public/free API. Currently we have 5/24 data on hand.

Usage:
    python _audit_sharp_money_3day.py
"""
import os, sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
SB = os.environ["SUPABASE_URL"]
H = {"apikey": os.environ["SUPABASE_KEY"], "Authorization": f'Bearer {os.environ["SUPABASE_KEY"]}'}


def get(path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(SB + path, headers=H), timeout=30).read())


ABBR = {
    "Washington Nationals": "WSH", "Atlanta Braves": "ATL", "Texas Rangers": "TEX",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Milwaukee Brewers": "MIL",
    "Cleveland Guardians": "CLE", "Philadelphia Phillies": "PHI", "Colorado Rockies": "COL",
    "Arizona Diamondbacks": "ARI", "Detroit Tigers": "DET", "Baltimore Orioles": "BAL",
    "Chicago White Sox": "CWS", "San Francisco Giants": "SF", "Tampa Bay Rays": "TB",
    "New York Yankees": "NYY", "Minnesota Twins": "MIN", "Boston Red Sox": "BOS",
    "New York Mets": "NYM", "Miami Marlins": "MIA", "Seattle Mariners": "SEA",
    "Kansas City Royals": "KC", "St. Louis Cardinals": "STL", "Cincinnati Reds": "CIN",
    "Houston Astros": "HOU", "Chicago Cubs": "CHC", "Athletics": "ATH",
    "San Diego Padres": "SD", "Toronto Blue Jays": "TOR", "Pittsburgh Pirates": "PIT",
}

# AN sharp data — paste manually per slate date.
# Format: {(away_abbr, home_abbr): {'sharp_ml': team_abbr, 'diff': pct, 'note': str}}
AN_DATA = {
    "2026-05-24": {
        ("WSH", "ATL"): {"sharp_ml": "ATL", "diff": 6,  "note": "consensus heavy fav"},
        ("TEX", "LAA"): {"sharp_ml": "TEX", "diff": 24, "note": "HUGE sharp on TEX dog"},
        ("LAD", "MIL"): {"sharp_ml": "LAD", "diff": 1,  "note": "consensus LAD"},
        ("CLE", "PHI"): {"sharp_ml": "CLE", "diff": 11, "note": "sharp leans CLE harder"},
        ("COL", "ARI"): {"sharp_ml": "COL", "diff": 2,  "note": "slight sharp on dog COL"},
        ("DET", "BAL"): {"sharp_ml": "BAL", "diff": 6,  "note": "sharp on BAL"},
        ("CWS", "SF"):  {"sharp_ml": "SF",  "diff": 17, "note": "sharp money on SF"},
        ("TB", "NYY"):  {"sharp_ml": "TB",  "diff": 29, "note": "HUGE sharp fading NYY"},
        ("MIN", "BOS"): {"sharp_ml": "BOS", "diff": 1,  "note": "basically even"},
        ("NYM", "MIA"): {"sharp_ml": "MIA", "diff": 2,  "note": "slight sharp MIA"},
        ("SEA", "KC"):  {"sharp_ml": "KC",  "diff": 19, "note": "HEAVY sharp on dog KC"},
        ("STL", "CIN"): {"sharp_ml": "STL", "diff": 24, "note": "HUGE sharp on STL dog"},
        ("HOU", "CHC"): {"sharp_ml": "CHC", "diff": 1,  "note": "consensus CHC"},
        ("ATH", "SD"):  {"sharp_ml": "ATH", "diff": 8,  "note": "sharp on dog ATH"},
    },
    # "2026-05-25": {},  # paste AN data here when available
    # "2026-05-26": {},
}


def bucket_size(diff):
    if diff >= 15: return "HEAVY (>=15%)"
    if diff >= 6:  return "MEDIUM (6-14%)"
    return "SMALL (1-5%)"


def audit_day(date_str, an):
    games = get(
        f"/rest/v1/mlb_game_results?game_date=eq.{date_str}"
        f"&select=away_team,home_team,away_score,home_score,away_ml_close,home_ml_close,"
        f"projected_spread,total_result,home_win,nrfi_result"
        f"&order=away_team.asc&limit=50"
    )

    summary = {
        "total":   {"w": 0, "l": 0, "no_result": 0},
        "buckets": {"HEAVY (>=15%)": {"w": 0, "l": 0}, "MEDIUM (6-14%)": {"w": 0, "l": 0}, "SMALL (1-5%)": {"w": 0, "l": 0}},
        "model_alignment": {"agree": {"w": 0, "l": 0}, "fade": {"w": 0, "l": 0}, "no_model": {"w": 0, "l": 0}},
        "rows": [],
    }

    for row in games:
        a = ABBR.get(row["away_team"], row["away_team"][:3].upper())
        hm = ABBR.get(row["home_team"], row["home_team"][:3].upper())
        ankey = (a, hm)
        anpick = an.get(ankey)
        if not anpick:
            continue

        aw_sc = row.get("away_score")
        h_sc = row.get("home_score")
        if aw_sc is None or h_sc is None:
            summary["total"]["no_result"] += 1
            summary["rows"].append((a, hm, anpick, "PPD/NR", None))
            continue

        winner = hm if row.get("home_win") else a
        sharp_won = winner == anpick["sharp_ml"]
        bucket = bucket_size(anpick["diff"])

        # Tally sharp-blind record
        key = "w" if sharp_won else "l"
        summary["total"][key] += 1
        summary["buckets"][bucket][key] += 1

        # Tally model-alignment record
        model_ml = None
        ps = row.get("projected_spread")
        if ps is not None:
            model_ml = hm if ps < 0 else a
        if model_ml is None:
            align_bucket = "no_model"
        elif model_ml == anpick["sharp_ml"]:
            align_bucket = "agree"
        else:
            align_bucket = "fade"
        summary["model_alignment"][align_bucket][key] += 1

        summary["rows"].append((a, hm, anpick, f"{aw_sc}-{h_sc} ({winner})", "W" if sharp_won else "L"))

    return summary


def fmt(rec):
    w, l = rec["w"], rec["l"]
    n = w + l
    pct = f"{100 * w / n:.1f}%" if n else "—"
    return f"{w}-{l} ({pct}, n={n})"


def print_day(date_str, summary):
    print("=" * 78)
    print(f"SHARP MONEY AUDIT — {date_str}")
    print("=" * 78)
    print()
    for a, hm, an, score, result in summary["rows"]:
        sharp = an["sharp_ml"]
        diff = an["diff"]
        bucket = bucket_size(diff)
        flag = result or "—"
        print(f"  {a}@{hm}: sharp={sharp} +{diff}% [{bucket}]  →  {score}  [{flag}]")
    print()
    print(f"  BLIND SHARP RECORD:  {fmt(summary['total'])}")
    print(f"    HEAVY (>=15%):     {fmt(summary['buckets']['HEAVY (>=15%)'])}")
    print(f"    MEDIUM (6-14%):    {fmt(summary['buckets']['MEDIUM (6-14%)'])}")
    print(f"    SMALL (1-5%):      {fmt(summary['buckets']['SMALL (1-5%)'])}")
    print()
    print(f"  MODEL ALIGNMENT:")
    print(f"    Model AGREES with sharp:  {fmt(summary['model_alignment']['agree'])}")
    print(f"    Model FADES sharp:        {fmt(summary['model_alignment']['fade'])}")
    print(f"    No model lean:            {fmt(summary['model_alignment']['no_model'])}")
    print()


def main():
    print("\n" + "#" * 78)
    print("# SHARP MONEY EFFECTIVENESS — MULTI-DAY AUDIT")
    print("#" * 78)
    print()

    aggregate = {
        "total":   {"w": 0, "l": 0, "no_result": 0},
        "buckets": {"HEAVY (>=15%)": {"w": 0, "l": 0}, "MEDIUM (6-14%)": {"w": 0, "l": 0}, "SMALL (1-5%)": {"w": 0, "l": 0}},
        "model_alignment": {"agree": {"w": 0, "l": 0}, "fade": {"w": 0, "l": 0}, "no_model": {"w": 0, "l": 0}},
    }

    for date_str in sorted(AN_DATA.keys()):
        an = AN_DATA[date_str]
        if not an:
            print(f"[skip {date_str}: no AN data pasted]")
            continue
        s = audit_day(date_str, an)
        print_day(date_str, s)
        # Roll up
        for k in ("w", "l", "no_result"):
            aggregate["total"][k] += s["total"][k]
        for bk in aggregate["buckets"]:
            for k in ("w", "l"):
                aggregate["buckets"][bk][k] += s["buckets"][bk][k]
        for ak in aggregate["model_alignment"]:
            for k in ("w", "l"):
                aggregate["model_alignment"][ak][k] += s["model_alignment"][ak][k]

    print("=" * 78)
    print("MULTI-DAY ROLLUP")
    print("=" * 78)
    print()
    print(f"  BLIND SHARP RECORD:  {fmt(aggregate['total'])}")
    print(f"    HEAVY (>=15%):     {fmt(aggregate['buckets']['HEAVY (>=15%)'])}")
    print(f"    MEDIUM (6-14%):    {fmt(aggregate['buckets']['MEDIUM (6-14%)'])}")
    print(f"    SMALL (1-5%):      {fmt(aggregate['buckets']['SMALL (1-5%)'])}")
    print()
    print(f"  MODEL ALIGNMENT:")
    print(f"    Model AGREES with sharp:  {fmt(aggregate['model_alignment']['agree'])}")
    print(f"    Model FADES sharp:        {fmt(aggregate['model_alignment']['fade'])}")
    print(f"    No model lean:            {fmt(aggregate['model_alignment']['no_model'])}")
    print()
    # Verdict guidance
    print("VERDICT GUIDANCE:")
    print("  - Blind sharp record > 55% with healthy n suggests genuine edge.")
    print("  - HEAVY bucket > MEDIUM > SMALL slope = sharp size is signal.")
    print("    Flat or inverse slope = sharp diff isn't predictive on this sample.")
    print("  - Model AGREES > Model FADES means we're best when WE pair the signal.")
    print()


if __name__ == "__main__":
    main()
