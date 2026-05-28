"""Sharp-money effectiveness audit — multi-day, multi-market.

User explicit ask (2026-05-27): "I am providing the public sharp data for
personal use not to ahng our hat on... What is the takeaways from it as we
have seen last couple days, is it effective, how does it breakdown."

Audits 5/24 + 5/25 + 5/26 across ML, Total, and Spread (run line) where
data is on hand. Breaks down by sharp-diff magnitude so we can see whether
the BIGGER sharp signals are actually more predictive (the test).

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

# AN data per slate.
# Per game: optional ml / total / spread dicts.
#   ml.side     = team abbr the sharp money is on
#   total.side  = 'OVER' or 'UNDER'
#   spread.side = team abbr the sharp money is on (the run line side: +1.5 or -1.5)
#   diff        = sharp - public % gap
AN_DATA = {
    "2026-05-24": {
        ("WSH","ATL"): {"ml":{"side":"ATL","diff":6}},
        ("TEX","LAA"): {"ml":{"side":"TEX","diff":24}},
        ("LAD","MIL"): {"ml":{"side":"LAD","diff":1}},
        ("CLE","PHI"): {"ml":{"side":"CLE","diff":11}},
        ("COL","ARI"): {"ml":{"side":"COL","diff":2}},
        ("DET","BAL"): {"ml":{"side":"BAL","diff":6}},
        ("CWS","SF"):  {"ml":{"side":"SF","diff":17}},
        ("TB","NYY"):  {"ml":{"side":"TB","diff":29}},
        ("MIN","BOS"): {"ml":{"side":"BOS","diff":1}},
        ("NYM","MIA"): {"ml":{"side":"MIA","diff":2}},
        ("SEA","KC"):  {"ml":{"side":"KC","diff":19}},
        ("STL","CIN"): {"ml":{"side":"STL","diff":24}},
        ("HOU","CHC"): {"ml":{"side":"CHC","diff":1}},
        ("ATH","SD"):  {"ml":{"side":"ATH","diff":8}},
    },
    "2026-05-25": {
        ("TB","BAL"):  {"ml":{"side":"TB","diff":8},  "total":{"side":"OVER","diff":21}, "spread":{"side":"TB","diff":4}},
        ("MIA","TOR"): {"ml":{"side":"TOR","diff":2}, "total":{"side":"OVER","diff":1}},
        ("COL","LAD"): {"ml":{"side":"COL","diff":8}, "total":{"side":"OVER","diff":6}},
        ("PHI","SD"):  {"ml":{"side":"SD","diff":15}, "spread":{"side":"SD","diff":4}},
        ("HOU","TEX"): {"ml":{"side":"HOU","diff":7}, "total":{"side":"OVER","diff":42}, "spread":{"side":"HOU","diff":23}},
        ("CHC","PIT"): {"ml":{"side":"PIT","diff":10}, "total":{"side":"OVER","diff":10}},
        ("ARI","SF"):  {"ml":{"side":"ARI","diff":24}, "total":{"side":"OVER","diff":46}},
        ("WSH","CLE"): {"ml":{"side":"WSH","diff":9},  "total":{"side":"OVER","diff":38}},
        ("MIN","CWS"): {"ml":{"side":"MIN","diff":5},  "total":{"side":"OVER","diff":27}},
        ("SEA","ATH"): {"ml":{"side":"ATH","diff":18}, "spread":{"side":"SEA","diff":12}},
        ("NYY","KC"):  {"ml":{"side":"KC","diff":3},   "total":{"side":"OVER","diff":5},  "spread":{"side":"NYY","diff":5}},
        ("STL","MIL"): {"ml":{"side":"STL","diff":4},  "total":{"side":"OVER","diff":29}, "spread":{"side":"MIL","diff":7}},
        ("CIN","NYM"): {"ml":{"side":"CIN","diff":16}, "total":{"side":"OVER","diff":58}},
    },
    "2026-05-26": {
        ("MIN","CWS"): {"ml":{"side":"MIN","diff":20}, "total":{"side":"OVER","diff":1},  "spread":{"side":"MIN","diff":15}},
        ("MIA","TOR"): {"ml":{"side":"MIA","diff":19}, "total":{"side":"UNDER","diff":9}, "spread":{"side":"TOR","diff":18}},
        ("WSH","CLE"): {"ml":{"side":"WSH","diff":18}, "total":{"side":"UNDER","diff":3}, "spread":{"side":"CLE","diff":2}},
        ("HOU","TEX"): {"ml":{"side":"HOU","diff":16}, "total":{"side":"UNDER","diff":1}, "spread":{"side":"TEX","diff":4}},
        ("ATL","BOS"): {"ml":{"side":"ATL","diff":14}, "total":{"side":"UNDER","diff":6}, "spread":{"side":"ATL","diff":15}},
        ("TB","BAL"):  {"ml":{"side":"TB","diff":12},  "spread":{"side":"TB","diff":9}},
        ("NYY","KC"):  {"ml":{"side":"NYY","diff":3},  "total":{"side":"OVER","diff":5}},
        ("STL","MIL"): {"ml":{"side":"MIL","diff":3},  "total":{"side":"UNDER","diff":4}},
        ("PHI","SD"):  {"ml":{"side":"PHI","diff":3},  "total":{"side":"UNDER","diff":1}},
        ("LAA","DET"): {"spread":{"side":"DET","diff":9}},
        ("SEA","ATH"): {"ml":{"side":"ATH","diff":9},  "total":{"side":"UNDER","diff":2}, "spread":{"side":"SEA","diff":9}},
        ("COL","LAD"): {"ml":{"side":"COL","diff":9},  "total":{"side":"OVER","diff":4}},
        ("CIN","NYM"): {"ml":{"side":"CIN","diff":10}, "total":{"side":"UNDER","diff":2}, "spread":{"side":"CIN","diff":11}},
        ("ARI","SF"):  {"ml":{"side":"ARI","diff":10}, "total":{"side":"UNDER","diff":1}},
        ("CHC","PIT"): {"ml":{"side":"CHC","diff":9},  "total":{"side":"UNDER","diff":8}, "spread":{"side":"PIT","diff":19}},
    },
}


def bucket(diff):
    if diff >= 15: return "HEAVY"
    if diff >= 6:  return "MEDIUM"
    return "SMALL"


def fetch_results(date_str):
    return get(
        f"/rest/v1/mlb_game_results?game_date=eq.{date_str}"
        f"&select=away_team,home_team,away_score,home_score,home_win,total_result,"
        f"spread_result,open_total,close_total,open_spread,close_spread"
        f"&order=away_team.asc&limit=50"
    )


def empty_row():
    return {"HEAVY": {"w": 0, "l": 0, "p": 0}, "MEDIUM": {"w": 0, "l": 0, "p": 0}, "SMALL": {"w": 0, "l": 0, "p": 0}}


def tally(rec, b, outcome):
    rec[b][outcome] = rec[b].get(outcome, 0) + 1


def fmt(rec):
    w, l, p = rec.get("w", 0), rec.get("l", 0), rec.get("p", 0)
    n = w + l + p
    decisive = w + l
    pct = f"{100 * w / decisive:.1f}%" if decisive else "—"
    pstr = f" {p}P" if p else ""
    return f"{w}-{l}{pstr} ({pct}, n={n})"


def fmt_market(market_dict):
    total = {"w": 0, "l": 0, "p": 0}
    out_lines = []
    for b in ("HEAVY", "MEDIUM", "SMALL"):
        rec = market_dict[b]
        for k in ("w", "l", "p"):
            total[k] += rec.get(k, 0)
        out_lines.append(f"     {b:8s} (Δ {'>=15%' if b == 'HEAVY' else '6-14%' if b == 'MEDIUM' else '1-5%':<7}):  {fmt(rec)}")
    return total, out_lines


def main():
    print("\n" + "#" * 86)
    print("# SHARP MONEY EFFECTIVENESS — 3-DAY MULTI-MARKET AUDIT")
    print("#" * 86)
    print()

    grand = {"ml": empty_row(), "total": empty_row(), "spread": empty_row()}

    for date_str in sorted(AN_DATA.keys()):
        an_day = AN_DATA[date_str]
        try:
            games = fetch_results(date_str)
        except Exception as e:
            print(f"  ⚠️  could not fetch results for {date_str}: {e}")
            continue
        by_match = {(ABBR.get(g["away_team"], g["away_team"][:3].upper()),
                     ABBR.get(g["home_team"], g["home_team"][:3].upper())): g for g in games}

        print("=" * 86)
        print(f"SHARP AUDIT — {date_str}  ({len(an_day)} games with AN data)")
        print("=" * 86)
        day = {"ml": empty_row(), "total": empty_row(), "spread": empty_row()}

        for (a, hm), picks in sorted(an_day.items()):
            g = by_match.get((a, hm))
            if not g:
                print(f"  {a}@{hm}  → no result row")
                continue
            aw = g.get("away_score")
            hs = g.get("home_score")
            if aw is None or hs is None:
                print(f"  {a}@{hm}  → PPD / no score")
                continue
            winner = hm if g.get("home_win") else a
            line_parts = [f"  {a}@{hm}  {aw}-{hs}  ({winner} W)"]

            # ML
            if "ml" in picks:
                p = picks["ml"]
                outcome = "w" if winner == p["side"] else "l"
                b = bucket(p["diff"])
                tally(day["ml"], b, outcome)
                tally(grand["ml"], b, outcome)
                line_parts.append(f"  ML[{p['side']}+{p['diff']}%/{b}]={outcome.upper()}")
            # Total
            if "total" in picks:
                p = picks["total"]
                tot_res = (g.get("total_result") or "").upper()
                if tot_res in ("OVER", "UNDER"):
                    outcome = "w" if tot_res == p["side"] else "l"
                elif tot_res == "PUSH":
                    outcome = "p"
                else:
                    outcome = None
                if outcome:
                    b = bucket(p["diff"])
                    tally(day["total"], b, outcome)
                    tally(grand["total"], b, outcome)
                    line_parts.append(f"  TOT[{p['side']}+{p['diff']}%/{b}]={outcome.upper()}")
                else:
                    line_parts.append(f"  TOT[{p['side']}+{p['diff']}%]=?")
            # Spread (uses spread_result column: 'home_covered' / 'away_covered' / 'push')
            if "spread" in picks:
                p = picks["spread"]
                spr_res = (g.get("spread_result") or "").lower()
                if spr_res in ("home_covered", "away_covered"):
                    sharp_is_home = p["side"] == hm
                    home_covered = spr_res == "home_covered"
                    sharp_won = home_covered if sharp_is_home else not home_covered
                    outcome = "w" if sharp_won else "l"
                    b = bucket(p["diff"])
                    tally(day["spread"], b, outcome)
                    tally(grand["spread"], b, outcome)
                    line_parts.append(f"  SPR[{p['side']}+{p['diff']}%/{b}]={outcome.upper()}")
                elif spr_res == "push":
                    b = bucket(p["diff"])
                    tally(day["spread"], b, "p")
                    tally(grand["spread"], b, "p")
                    line_parts.append(f"  SPR[{p['side']}+{p['diff']}%/{b}]=PUSH")
                else:
                    line_parts.append(f"  SPR[{p['side']}+{p['diff']}%]=?")

            print(" ".join(line_parts))

        print()
        for label, market in (("ML", "ml"), ("TOTAL", "total"), ("SPREAD", "spread")):
            total_rec, lines = fmt_market(day[market])
            print(f"  {label:<7}  TOTAL: {fmt(total_rec)}")
            for line in lines:
                print(line)
        print()

    # Grand total
    print("=" * 86)
    print("MULTI-DAY ROLLUP (all dates with AN data)")
    print("=" * 86)
    for label, market in (("ML", "ml"), ("TOTAL", "total"), ("SPREAD", "spread")):
        total_rec, lines = fmt_market(grand[market])
        print(f"  {label:<7}  TOTAL: {fmt(total_rec)}")
        for line in lines:
            print(line)
    print()
    print("VERDICT GUIDANCE:")
    print("  - >55% with healthy n at any bucket = genuine signal worth a card slot")
    print("  - HEAVY > MEDIUM > SMALL slope = sharp size IS predictive (the test)")
    print("  - Flat or inverted slope = sharp diff is noise; can't use as primary justification")


if __name__ == "__main__":
    main()
