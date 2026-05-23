"""Tier-label integrity check.

Runs nightly. Compares the 30d hit rate of every (prop_type, direction)
across PRIME vs STRONG vs LEAN. Flags drift cases where a "higher"
tier hits *worse* than a "lower" tier — that's a labeling failure,
not a variance miss, and it means the conviction-score gate is
including signals that don't predict.

Output:
  - prints a summary to stdout
  - upserts findings into `tier_integrity_findings` table so the
    backside can surface a warning in the app receipts dashboard
  - exits non-zero when severity == 'critical' (delta >= 5pt) so the
    GH Actions cron flags as red rather than silently passing

Cron: 2026-05-23 docket. Wired into the same nightly job as
audit_tier_calibration.

Why this matters: PRIME hits-under was at 55.7% (n=97) over the last
30d while STRONG hits-under was 58.0% (n=119). PRIME-by-name was
mis-labeling: the "highest conviction" tier was the worst-hitting
tier. Without this audit we don't catch that until results pile up.
"""
import os, json, urllib.request, urllib.parse
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime, timedelta, timezone

load_dotenv()
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# Severity thresholds (hit-rate delta where lower tier > higher tier)
SEVERITY_WARN = 2.0      # >=2pt = warn
SEVERITY_CRITICAL = 5.0  # >=5pt = critical (exit non-zero)
MIN_N_PER_TIER = 10      # below this, skip — too noisy to flag

# Standard tier hierarchy (highest conviction → lowest)
TIER_RANK = {"PRIME": 0, "STRONG": 1, "LEAN": 2}


def get(path, **q):
    qs = urllib.parse.urlencode(q, safe="=.,*()")
    u = f"{URL}/rest/v1/{path}?{qs}"
    req = urllib.request.Request(u, headers=H)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def upsert(path, rows, on_conflict):
    qs = urllib.parse.urlencode({"on_conflict": on_conflict})
    u = f"{URL}/rest/v1/{path}?{qs}"
    headers = {**H, "Prefer": "resolution=merge-duplicates,return=minimal"}
    req = urllib.request.Request(u, headers=headers, data=json.dumps(rows).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status


def fetch_30d_props():
    """Pull every graded prop from the last 30 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = get("mlb_pipeline_props",
               select="game_date,prop_type,direction,tier,result",
               game_date=f"gte.{cutoff}",
               result="not.is.null",
               limit="5000")
    return rows


def rollup(rows):
    """Aggregate hit_rate by (prop_type, direction, tier). Returns nested dict."""
    bucket = defaultdict(lambda: defaultdict(lambda: {"W": 0, "L": 0, "P": 0}))
    for r in rows:
        ptype = r.get("prop_type") or "?"
        direction = (r.get("direction") or "").lower() or "?"
        tier = (r.get("tier") or "").upper() or "(none)"
        res = (r.get("result") or "").upper()
        if res == "WIN":
            bucket[(ptype, direction)][tier]["W"] += 1
        elif res == "LOSS":
            bucket[(ptype, direction)][tier]["L"] += 1
        elif res == "PUSH":
            bucket[(ptype, direction)][tier]["P"] += 1
    return bucket


def find_drift(bucket):
    """For each (prop_type, direction) find tier pairs where lower tier
    out-hits higher tier by SEVERITY_WARN or more."""
    findings = []
    for (ptype, direction), tier_stats in bucket.items():
        # Compute hit rate per tier
        rates = {}
        for tier, s in tier_stats.items():
            n = s["W"] + s["L"]  # pushes don't count
            if n < MIN_N_PER_TIER:
                continue
            rates[tier] = {"rate": s["W"] / n, "n": n, "W": s["W"], "L": s["L"]}

        # Compare every higher-rank tier to every lower-rank tier
        tiers_sorted = sorted(rates.keys(), key=lambda t: TIER_RANK.get(t, 99))
        for i in range(len(tiers_sorted)):
            higher = tiers_sorted[i]
            for j in range(i + 1, len(tiers_sorted)):
                lower = tiers_sorted[j]
                delta = (rates[lower]["rate"] - rates[higher]["rate"]) * 100
                if delta >= SEVERITY_WARN:
                    severity = "critical" if delta >= SEVERITY_CRITICAL else "warn"
                    findings.append({
                        "prop_type": ptype,
                        "direction": direction,
                        "higher_tier": higher,
                        "lower_tier": lower,
                        "higher_rate": round(rates[higher]["rate"], 4),
                        "higher_n": rates[higher]["n"],
                        "lower_rate": round(rates[lower]["rate"], 4),
                        "lower_n": rates[lower]["n"],
                        "delta_pct": round(delta, 2),
                        "severity": severity,
                    })
    return findings


def main():
    print(f"=== TIER INTEGRITY AUDIT — {datetime.now(timezone.utc).isoformat()} ===")
    rows = fetch_30d_props()
    print(f"Graded props (30d): {len(rows)}")

    bucket = rollup(rows)
    findings = find_drift(bucket)

    if not findings:
        print("✅ No tier drift detected. PRIME > STRONG > LEAN intact across all cohorts.")
        return 0

    findings.sort(key=lambda f: -f["delta_pct"])

    critical_count = sum(1 for f in findings if f["severity"] == "critical")
    warn_count = sum(1 for f in findings if f["severity"] == "warn")

    print(f"\n⚠️  TIER LABEL DRIFT DETECTED")
    print(f"   critical: {critical_count}  |  warn: {warn_count}")
    print()
    for f in findings:
        marker = "🚨" if f["severity"] == "critical" else "⚠️ "
        print(f"  {marker} {f['prop_type']:14} {f['direction']:5} | "
              f"{f['lower_tier']:6} {f['lower_rate']*100:5.1f}% (n={f['lower_n']:3}) "
              f"BEATS {f['higher_tier']:6} {f['higher_rate']*100:5.1f}% (n={f['higher_n']:3}) "
              f"by {f['delta_pct']:+.1f}pt")

    # Write findings to tier_integrity_findings (best effort — table may
    # not exist yet on first run; the next migration creates it).
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
    upsert_rows = [
        {
            "computed_date": today,
            "prop_type": f["prop_type"],
            "direction": f["direction"],
            "higher_tier": f["higher_tier"],
            "lower_tier": f["lower_tier"],
            "higher_rate": f["higher_rate"],
            "higher_n": f["higher_n"],
            "lower_rate": f["lower_rate"],
            "lower_n": f["lower_n"],
            "delta_pct": f["delta_pct"],
            "severity": f["severity"],
        }
        for f in findings
    ]
    try:
        upsert("tier_integrity_findings", upsert_rows,
               on_conflict="computed_date,prop_type,direction,higher_tier,lower_tier")
        print(f"\n  ✅ Wrote {len(upsert_rows)} findings to tier_integrity_findings")
    except Exception as e:
        print(f"\n  ⚠️  Could not write findings table: {e}")
        print(f"  (run supabase migration 20260523_tier_integrity_findings.sql first)")

    # Non-zero exit only on critical — warn-level shouldn't fail nightly cron
    return 1 if critical_count > 0 else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
