"""Grade yesterday's K projections vs actual strikeouts.

Reads content/ks_audit_YYYY-MM-DD.md, pulls actual K counts from MLB Stats
API per starter, and appends a grading block at the bottom of the file with
per-pitcher deltas + aggregate accuracy stats.

Usage:
    python _grade_ks_audit.py 2026-05-26
    python _grade_ks_audit.py             # defaults to yesterday ET

Designed to accumulate across many slates so we can answer:
- Mean absolute error of L7 K projection vs actual
- Bias direction (over or under-projecting)
- Per-band accuracy (proj >=6, 4-5, <3)
- Whether "Our line" floor (floor(proj)-0.5) is too conservative

Idempotent — if grading already exists in the file, re-running just
refreshes the numbers. Same file gets enriched, not duplicated.
"""
import os
import sys
import re
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_DIR = os.path.join(REPO_ROOT, "content")


def _yesterday_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4) - timedelta(days=1)
    return et.strftime("%Y-%m-%d")


def _resolve_date():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return _yesterday_et()


def _fetch_actual_ks(pitcher_name, game_date):
    """Pull starter's K count for a given date from MLB Stats API."""
    try:
        search = urllib.request.urlopen(
            f"https://statsapi.mlb.com/api/v1/people/search?names={urllib.parse.quote(pitcher_name)}",
            timeout=15,
        )
        people = json.loads(search.read()).get("people", [])
        if not people:
            return None
        pid = people[0]["id"]
        season = game_date.split("-")[0]
        r = urllib.request.urlopen(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats"
            f"?stats=gameLog&group=pitching&season={season}",
            timeout=15,
        )
        splits = (json.loads(r.read()).get("stats") or [{}])[0].get("splits", [])
        for sp in splits:
            if sp.get("date") == game_date:
                return int(sp.get("stat", {}).get("strikeOuts", 0) or 0)
        return None
    except Exception as e:
        print(f"  ⚠️ fetch_actual_ks failed for {pitcher_name}: {e}")
        return None


import urllib.parse  # imported here to keep _fetch_actual_ks self-contained


def grade(date_str):
    path = os.path.join(CONTENT_DIR, f"ks_audit_{date_str}.md")
    if not os.path.exists(path):
        print(f"No audit file at {path}")
        return

    text = open(path, encoding="utf-8").read()

    # Strip bold markdown wrappers (** **) from the table before parsing.
    # Earlier bug (Schlittler/Harrison missed grading on 5/26): rows where
    # the pitcher name AND numeric columns were both wrapped in **bold**
    # broke the numeric character classes in the row regex. We can't fix
    # that with optional-asterisk groups without making the regex unreadable.
    # Cleanest fix: collapse `**Cam Schlittler**` → `Cam Schlittler` and
    # `**6.43**` → `6.43` before the regex runs. Pure cosmetic strip; the
    # underlying data values are untouched.
    parse_text = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", text)

    # Parse the table rows: | Pitcher | Team | Opp | L7 K proj | Our line | Tier | Book guess | Actual Ks |
    row_re = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([A-Z]+)\s*\|\s*([A-Z]+)\s*\|\s*([\d.]+|—|None)\s*\|"
        r"\s*([\d.]+|—)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.+?)\s*\|\s*$",
        re.MULTILINE,
    )
    rows = []
    for m in row_re.finditer(parse_text):
        name = m.group(1).strip()
        team = m.group(2)
        proj = m.group(4)
        line = m.group(5)
        tier = m.group(6)
        if name.lower() == "pitcher":
            continue  # header
        rows.append({"name": name, "team": team, "proj": proj, "line": line, "tier": tier})

    if not rows:
        print(f"No starter rows parsed from {path}")
        return

    print(f"Grading {len(rows)} starters for {date_str}...")
    graded = []
    deltas = []
    for row in rows:
        actual = _fetch_actual_ks(row["name"], date_str)
        delta = None
        try:
            if actual is not None and row["proj"] not in ("—", "None"):
                delta = actual - float(row["proj"])
                deltas.append(delta)
        except Exception:
            pass
        graded.append({**row, "actual": actual, "delta": delta})
        time.sleep(0.15)

    # Build a grading block to append
    out = ["\n---\n\n## Grading — auto-filled\n"]
    out.append(f"\nRan on {datetime.now(timezone.utc).isoformat()}.\n\n")
    out.append("| Pitcher | Proj | Our line | Actual | Δ (act-proj) | Line hit? |\n")
    out.append("|---------|------|----------|--------|--------------|----------|\n")
    for g in graded:
        actual_str = str(g["actual"]) if g["actual"] is not None else "—"
        delta_str = f"{g['delta']:+.1f}" if g["delta"] is not None else "—"
        line_hit = "—"
        try:
            if g["actual"] is not None and g["line"] not in ("—", "None"):
                ln = float(g["line"])
                line_hit = "✓ over" if g["actual"] > ln else ("push" if g["actual"] == ln else "✗ under")
        except Exception:
            pass
        out.append(f"| {g['name']} | {g['proj']} | {g['line']} | {actual_str} | {delta_str} | {line_hit} |\n")

    if deltas:
        mae = sum(abs(d) for d in deltas) / len(deltas)
        bias = sum(deltas) / len(deltas)
        out.append(
            f"\n**Aggregate** (n={len(deltas)}): "
            f"MAE = {mae:.2f} K, bias = {bias:+.2f} "
            f"({'over-projecting' if bias < 0 else 'under-projecting'}).\n"
        )

    # Replace existing grading block if present (idempotent), else append.
    grading_marker = "\n---\n\n## Grading — auto-filled\n"
    if grading_marker in text:
        text = text.split(grading_marker)[0]
    text += "".join(out)

    open(path, "w", encoding="utf-8").write(text)
    print(f"Appended grading to {path}")


if __name__ == "__main__":
    grade(_resolve_date())
