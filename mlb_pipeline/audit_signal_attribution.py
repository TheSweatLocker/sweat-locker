"""Signal-attribution scanner — finds the signal interactions that are
costing us money.

For every PRIME/STRONG prop pick in the last 30d:
  1. Decompose which signals fired (from the `signals` JSON dict)
  2. Aggregate hit rate by individual signal
  3. Aggregate hit rate by signal PAIRS (the buried problem — single signals
     can look fine but combine badly with another)
  4. Surface findings:
     - Individual signals that hit < 50%
     - Pair combos that hit much worse than either alone
     - "Anti-correlation" pairs (presence drops hit rate significantly)

This is the "find a signal that leads to better performance" tool.
Single-signal cohort analysis (audit_tier_calibration.py) misses
interactions. This catches them.

Outputs:
  - Console report (run manually for now)
  - Optionally writes findings to signal_attribution_findings table
    for app surfacing (migration: 20260523_signal_attribution.sql)

Cron: hookup queued but not enabled yet — wants 2-3 weeks of data
post-deployment to avoid noisy findings on small sample.

Built 2026-05-23 in response to user noting model underperformance
(v4 ML 44% over 7d) and asking for retro-test infrastructure.
"""
import os, json, urllib.request, urllib.parse
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations

load_dotenv()
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# Tunable: only look at picks that actually made the publish bar
MIN_TIER = {"PRIME", "STRONG"}
# Minimum samples before we trust a single-signal rate
MIN_N_SIGNAL = 15
# Minimum samples before we trust a pair-combo rate
MIN_N_PAIR = 10
# Pair finding: only flag if pair hits >= 5pt worse than the BETTER individual
PAIR_DRAG_THRESHOLD_PCT = 5.0
# Single-signal failure: only flag if hit rate < 50% (below coin flip)
SINGLE_FAIL_THRESHOLD = 50.0


def get(path, **q):
    qs = urllib.parse.urlencode(q, safe="=.,*()")
    u = f"{URL}/rest/v1/{path}?{qs}"
    req = urllib.request.Request(u, headers=H)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def fetch_resolved_picks(days_back=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    rows = get("mlb_pipeline_props",
               select="game_date,player_name,prop_type,direction,tier,conviction,signals,result",
               game_date=f"gte.{cutoff}",
               result="not.is.null",
               limit="5000")
    return rows


def signal_keys(prop):
    """Extract just the keys of the signals dict (those are the signal IDs)."""
    s = prop.get("signals") or {}
    if not isinstance(s, dict):
        return []
    # Filter out internal/non-signal keys (those starting with _)
    return sorted([k for k in s.keys() if not k.startswith("_")])


def aggregate_by_signal(picks):
    """For each signal name, accumulate W/L across all picks where it fired."""
    agg = defaultdict(lambda: {"W": 0, "L": 0})
    for p in picks:
        res = (p.get("result") or "").upper()
        if res not in ("WIN", "LOSS"):
            continue
        for sig in signal_keys(p):
            if res == "WIN":
                agg[sig]["W"] += 1
            else:
                agg[sig]["L"] += 1
    return agg


def aggregate_by_pair(picks):
    """For each signal pair, accumulate W/L across all picks where BOTH fired."""
    agg = defaultdict(lambda: {"W": 0, "L": 0})
    for p in picks:
        res = (p.get("result") or "").upper()
        if res not in ("WIN", "LOSS"):
            continue
        sigs = signal_keys(p)
        for pair in combinations(sigs, 2):
            if res == "WIN":
                agg[pair]["W"] += 1
            else:
                agg[pair]["L"] += 1
    return agg


def hit_rate(stats):
    n = stats["W"] + stats["L"]
    if n == 0:
        return None
    return (stats["W"] / n) * 100


def main():
    print("=" * 78)
    print(f"SIGNAL-ATTRIBUTION SCAN — last 30 days, PRIME+STRONG only")
    print("=" * 78)

    rows = fetch_resolved_picks(30)
    picks = [r for r in rows if (r.get("tier") or "").upper() in MIN_TIER]
    print(f"Resolved PRIME+STRONG picks: {len(picks)}")
    if not picks:
        return 0

    # By individual signal
    sig_agg = aggregate_by_signal(picks)
    print(f"\nDistinct signals seen: {len(sig_agg)}")

    print("\n--- SINGLE-SIGNAL HIT RATES (n>=15, sorted by rate desc) ---")
    sig_rows = []
    for sig, stats in sig_agg.items():
        n = stats["W"] + stats["L"]
        if n < MIN_N_SIGNAL:
            continue
        rate = hit_rate(stats)
        sig_rows.append((sig, rate, n, stats["W"], stats["L"]))
    sig_rows.sort(key=lambda x: -x[1])

    for sig, rate, n, w, l in sig_rows:
        flag = " ⚠️" if rate < SINGLE_FAIL_THRESHOLD else (" ✅" if rate >= 60 else "")
        print(f"  {sig:30}  {w:>3}-{l:<3}  {rate:5.1f}%  (n={n}){flag}")

    # Below-coinflip individual signals
    fail_sigs = [(s, r, n) for s, r, n, _, _ in sig_rows if r < SINGLE_FAIL_THRESHOLD]
    if fail_sigs:
        print(f"\n⚠️  {len(fail_sigs)} single signals below 50% — likely noise or inverse predictors")

    # By pair
    pair_agg = aggregate_by_pair(picks)
    print(f"\n--- TROUBLE PAIRS (both signals fire, pair hits {PAIR_DRAG_THRESHOLD_PCT}+pt worse than better alone) ---")
    pair_findings = []
    for pair, stats in pair_agg.items():
        n = stats["W"] + stats["L"]
        if n < MIN_N_PAIR:
            continue
        pair_rate = hit_rate(stats)
        sig_a_rate = hit_rate(sig_agg[pair[0]])
        sig_b_rate = hit_rate(sig_agg[pair[1]])
        if sig_a_rate is None or sig_b_rate is None:
            continue
        better_alone = max(sig_a_rate, sig_b_rate)
        drag = better_alone - pair_rate
        if drag >= PAIR_DRAG_THRESHOLD_PCT:
            pair_findings.append({
                "pair": pair,
                "pair_rate": pair_rate,
                "pair_n": n,
                "sig_a_rate": sig_a_rate,
                "sig_b_rate": sig_b_rate,
                "drag": drag,
            })
    pair_findings.sort(key=lambda f: -f["drag"])

    if not pair_findings:
        print("  No trouble pairs detected at current thresholds.")
    else:
        for f in pair_findings[:15]:
            a, b = f["pair"]
            print(f"  {a} + {b}:")
            print(f"    pair: {f['pair_rate']:5.1f}% (n={f['pair_n']})  |  "
                  f"{a} alone: {f['sig_a_rate']:5.1f}%  |  {b} alone: {f['sig_b_rate']:5.1f}%  |  "
                  f"DRAG: -{f['drag']:.1f}pt")

    # By tier x signal — see which signals are over-promoting to PRIME
    print(f"\n--- BY-TIER COMPARISON (where same signal hits worse in PRIME vs STRONG) ---")
    prime_picks = [p for p in picks if (p.get("tier") or "").upper() == "PRIME"]
    strong_picks = [p for p in picks if (p.get("tier") or "").upper() == "STRONG"]
    prime_sig = aggregate_by_signal(prime_picks)
    strong_sig = aggregate_by_signal(strong_picks)

    overpromote = []
    for sig in prime_sig:
        if sig not in strong_sig:
            continue
        p_rate = hit_rate(prime_sig[sig])
        s_rate = hit_rate(strong_sig[sig])
        p_n = prime_sig[sig]["W"] + prime_sig[sig]["L"]
        s_n = strong_sig[sig]["W"] + strong_sig[sig]["L"]
        if p_n < 10 or s_n < 10:
            continue
        if p_rate is None or s_rate is None:
            continue
        if s_rate - p_rate >= 3:  # STRONG hits 3+pt better than PRIME with same signal
            overpromote.append((sig, p_rate, p_n, s_rate, s_n))
    overpromote.sort(key=lambda x: -(x[3] - x[1]))
    if not overpromote:
        print("  No signal over-promotion detected (PRIME consistently >= STRONG).")
    else:
        for sig, pr, pn, sr, sn in overpromote:
            print(f"  {sig:30}  PRIME {pr:5.1f}% (n={pn:3}) vs STRONG {sr:5.1f}% (n={sn:3})  | STRONG +{sr-pr:.1f}pt")

    print()
    print("=" * 78)
    print("RECOMMENDED ACTIONS:")
    print("=" * 78)
    if fail_sigs:
        worst = sorted(fail_sigs, key=lambda x: x[1])[:3]
        for s, r, n in worst:
            print(f"  · Consider downweighting `{s}` (hits {r:.1f}% on n={n}, likely noise)")
    if pair_findings:
        for f in pair_findings[:3]:
            a, b = f["pair"]
            print(f"  · Consider anti-correlation gate: when `{a}` AND `{b}` both fire, demote tier")
            print(f"    (combo: {f['pair_rate']:.1f}% vs better-alone {max(f['sig_a_rate'], f['sig_b_rate']):.1f}%)")
    if overpromote:
        for s, pr, pn, sr, sn in overpromote[:3]:
            print(f"  · `{s}` is over-promoting to PRIME — drop it from PRIME-promoting signal set")
    if not (fail_sigs or pair_findings or overpromote):
        print("  · No actionable findings at current thresholds. Sample may be too small.")


if __name__ == "__main__":
    main()
