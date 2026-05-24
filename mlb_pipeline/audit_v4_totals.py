"""v4 totals model audit — quantify direction accuracy + bias magnitude.

Yesterday (5/23) the v4 total direction went 2-9 (18%). One bad day or
systematic? This script answers by walking the last 7/14/30 days and
breaking down:

  - Direction accuracy (did v4 pick the correct side of the line)
  - Magnitude bias (avg of v4_total - actual_total — positive = model
    over-projects runs)
  - Hit rate split by:
      * v4 says OVER vs v4 says UNDER (catches one-sided bias)
      * v4-market disagreement size (small edges vs large edges)
      * Park factor band (hitter park vs pitcher park)

Built 2026-05-24 — yesterday's totals miss was loud enough to warrant
a real diagnosis, not a guess. Used by signal_attribution to recommend
specific adjustments to projected_total or v4 throttling.
"""
import os, json, urllib.request, urllib.parse
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}


def get(path, **q):
    qs = urllib.parse.urlencode(q, safe="=.,*()")
    u = f"{URL}/rest/v1/{path}?{qs}"
    with urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=25) as r:
        return json.loads(r.read())


def pull_window(days_back):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back) - timedelta(hours=4)).strftime("%Y-%m-%d")
    rows = get("mlb_game_results",
               select="game_date,away_team,home_team,total_runs,close_total,total_result,model_pred_total,projected_total,park_run_factor",
               game_date=f"gte.{cutoff}")
    return rows


def analyze(rows, label):
    v4_w = v4_l = 0
    v4_over_w = v4_over_l = 0  # when v4 picks over
    v4_under_w = v4_under_l = 0  # when v4 picks under
    bias_sum = 0
    bias_n = 0
    big_edge_w = big_edge_l = 0
    small_edge_w = small_edge_l = 0
    hitter_park_w = hitter_park_l = 0
    pitcher_park_w = pitcher_park_l = 0
    v3_w = v3_l = 0  # for comparison

    for g in rows:
        tr = (g.get("total_result") or "").lower()
        if tr not in ("over", "under"):
            continue
        actual = g.get("total_runs")
        ct = g.get("close_total")
        v4 = g.get("model_pred_total")
        v3 = g.get("projected_total")
        park = g.get("park_run_factor") or 100

        if v4 is not None and ct is not None:
            v4_picks_over = float(v4) > float(ct)
            won = (tr == "over") == v4_picks_over
            if won: v4_w += 1
            else: v4_l += 1
            if v4_picks_over:
                if won: v4_over_w += 1
                else: v4_over_l += 1
            else:
                if won: v4_under_w += 1
                else: v4_under_l += 1
            # Bias: v4 - actual (positive = model over-projects)
            if actual is not None:
                bias_sum += float(v4) - float(actual)
                bias_n += 1
            # Edge size
            edge = abs(float(v4) - float(ct))
            if edge >= 1.5:
                if won: big_edge_w += 1
                else: big_edge_l += 1
            elif edge >= 0.5:
                if won: small_edge_w += 1
                else: small_edge_l += 1
            # Park split
            if park >= 105:
                if won: hitter_park_w += 1
                else: hitter_park_l += 1
            elif park <= 95:
                if won: pitcher_park_w += 1
                else: pitcher_park_l += 1

        if v3 is not None and ct is not None:
            v3_picks_over = float(v3) > float(ct)
            v3_won = (tr == "over") == v3_picks_over
            if v3_won: v3_w += 1
            else: v3_l += 1

    n = v4_w + v4_l
    if n == 0:
        print(f"{label}: no graded totals")
        return
    print(f"\n=== {label} ===")
    print(f"  v4 direction:  {v4_w}-{v4_l}  ({v4_w/n*100:.1f}%)")
    print(f"  v3 direction:  {v3_w}-{v3_l}  ({v3_w/max(v3_w+v3_l,1)*100:.1f}%)")
    if bias_n:
        avg_bias = bias_sum / bias_n
        print(f"  Avg bias:      v4 projects {avg_bias:+.2f} runs vs actual (positive = over-projects)")
    print(f"  When v4 picks OVER:  {v4_over_w}-{v4_over_l}  ({v4_over_w/max(v4_over_w+v4_over_l,1)*100:.1f}%) — n={v4_over_w+v4_over_l}")
    print(f"  When v4 picks UNDER: {v4_under_w}-{v4_under_l}  ({v4_under_w/max(v4_under_w+v4_under_l,1)*100:.1f}%) — n={v4_under_w+v4_under_l}")
    print(f"  Large edge (|v4-line| >= 1.5): {big_edge_w}-{big_edge_l} ({big_edge_w/max(big_edge_w+big_edge_l,1)*100:.1f}%)")
    print(f"  Small edge (0.5-1.5):          {small_edge_w}-{small_edge_l} ({small_edge_w/max(small_edge_w+small_edge_l,1)*100:.1f}%)")
    if hitter_park_w + hitter_park_l > 0:
        print(f"  Hitter parks (PF>=105): {hitter_park_w}-{hitter_park_l} ({hitter_park_w/(hitter_park_w+hitter_park_l)*100:.1f}%)")
    if pitcher_park_w + pitcher_park_l > 0:
        print(f"  Pitcher parks (PF<=95): {pitcher_park_w}-{pitcher_park_l} ({pitcher_park_w/(pitcher_park_w+pitcher_park_l)*100:.1f}%)")


print("=" * 78)
print("v4 TOTALS DIAGNOSTIC")
print("=" * 78)
for d, label in [(3, "Last 3 days"), (7, "Last 7 days"), (14, "Last 14 days"), (30, "Last 30 days")]:
    rows = pull_window(d)
    analyze(rows, label)

# Also check the day-by-day for the trailing 7 days to see drift
print("\n=== DAY-BY-DAY (last 7 days) ===")
for days in range(1, 8):
    date = (datetime.now(timezone.utc) - timedelta(days=days, hours=4)).strftime("%Y-%m-%d")
    rows = get("mlb_game_results",
               select="total_runs,close_total,total_result,model_pred_total",
               game_date=f"eq.{date}")
    w = l = 0
    bias_sum = 0
    bias_n = 0
    for g in rows:
        tr = (g.get("total_result") or "").lower()
        v4 = g.get("model_pred_total")
        ct = g.get("close_total")
        actual = g.get("total_runs")
        if tr not in ("over", "under") or v4 is None or ct is None:
            continue
        v4_picks_over = float(v4) > float(ct)
        if (tr == "over") == v4_picks_over: w += 1
        else: l += 1
        if actual is not None:
            bias_sum += float(v4) - float(actual)
            bias_n += 1
    total = w + l
    bias = (bias_sum / bias_n) if bias_n else 0
    if total > 0:
        print(f"  {date}: v4 totals {w:>2}-{l:<2} ({w/total*100:>5.1f}%) | bias {bias:+.2f}")
