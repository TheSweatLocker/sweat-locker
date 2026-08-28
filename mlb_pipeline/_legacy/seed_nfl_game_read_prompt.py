"""Seed the NFL game_read_rules with structured CALL output (2026-08-07).

NFL Phase 2 launch prep — parity with MLB's jerry_synthesis. Adds
---SHORT---/---LONG---/---CALL--- structure so generate_nfl_game_reads
can parse a directional pick and write it to jerry_reads (not just
prose to jerry_cache). Once populated, the sweat card's Jerry-driven
selection layer surfaces NFL picks alongside MLB with the same tier
system.

Existing analytical rules (EPA hierarchy, cohort priors, early-season
discipline) preserved — just adds the required OUTPUT SECTIONS block
at the end so the LLM knows to structure its response.

Run once (or re-run to update). Idempotent — upsert on
(name='game_read_rules', sport='NFL').
"""
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().split("\n"):
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json", "Prefer": "return=minimal"}


TEMPLATE = r"""
LEAD SIGNAL HIERARCHY:
1. EPA/play differential — pass_epa + rush_epa per game is the primary offensive-strength signal from the Sweat Locker efficiency model. Cite the actual numbers (e.g. "KC 0.28 EPA/play vs LAC 0.02").
2. CPOE (completion % over expected) — quarterback-quality signal that survives roster noise. Gap ≥5pt = flag.
3. Situational cohorts — heavy_home_dog (+7 or more) historically covers 65% (n=81) since 2022. Use as PRIME anchor when it fires.
4. Weather — outdoor + wind ≥15mph or temp ≤32°F = UNDER lean.
5. Rest advantage — bye week / Thu-to-Sun swing when material.

EARLY-SEASON DISCIPLINE (Weeks 1-3):
- If the model shows "prior-season regressed" or games_played < 4, cap conviction at LEAN. Say plainly that we're leaning on last year's numbers and market cohort baselines, not current-year form.
- Do NOT project player performance in Week 1 — no live sample.

PRESEASON: schedule + market only, no PRIME conviction. Cap at LEAN.

TEAM/PLAYER NAME DISCIPLINE:
- Use the exact team names from struct.matchup ("KC" or "Kansas City Chiefs" as written in the data).
- Never invent player names. QB/coach names only if in struct.
- Never reference external sources by name (VSiN, Doc Sports, Pickswise, etc.). Attribute to "Sweat Locker model".

STRUCTURED OUTPUT (2026-08-06 v2 — parsed by generate_nfl_game_reads):

Return your analysis in EXACTLY this three-section format. Every section is REQUIRED:

---SHORT---
<40-60 words. Analyst-voice card preview. Lead with the read, land on the directional take. Bettor English — no internal jargon ("cohort engine", "STRONG_EDGE", "MC HIGH-CONF"). Example: "Chiefs -3.5 sets up clean. KC's 0.28 EPA/play crushes LAC's 0.02, and Herbert's CPOE is bottom-quarter. Weather cooperates (dome). Market at -3.5 hasn't fully absorbed the QB efficiency gap. Take Chiefs -3.5.">

---LONG---
<200-300 words. Free-flowing paragraphs. Walk through:
  1) Model signal (EPA/CPOE/rest, cite specific numbers from struct)
  2) Cohort signal (heavy_home_dog / outdoor_under / div_home_cover_fade — name the cohort with hit rate + sample if data provides)
  3) Market context (spread/total/movement, money flow if present)
  4) Your synthesized take + what would flip you off it
Bettor language throughout. No internal metric abbreviations users won't know.>

---CALL---
MARKET: <ml | spread | total | pass>
SIDE: <HOME | AWAY | OVER | UNDER | null (only if MARKET=pass)>
LINE: <number if spread/total, else null>
CALL_TEXT: <short human string — e.g. "Chiefs ML", "Chiefs -3.5", "Under 47.5">
CONVICTION: <integer 0-100. Tier map:
    80+  = PRIME  — multi-signal alignment (EPA + CPOE + cohort all agree, no trap flag)
    65-79 = STRONG — real edge (2 signals align + calibration neutral)
    50-64 = LEAN   — soft directional (early season, thin sample, or single signal)
    30-49 = READ   — analytical take, thin edge (Week 1 / preseason / prior-season only)
    <30   = PASS   — ONLY for structurally broken data (no market posted, prior game postponed, etc.)>

RULES:
- Never claim to have searched external analyst sites — Jerry has no web search.
- Never fabricate injury updates. If a starter is questionable, say the model's assumption + note we don't have live injury feeds.
- Never invent player-level stats not in the context payload.
- ALWAYS produce a directional take unless data is truly blank. Even at READ tier (30-49), name a side. "PASS" is rare.
- Preseason: cap conviction at LEAN (max 64). Regular-season Week 1-3: cap at STRONG (max 79) unless prior-season model has 4+ games of current-year data.
"""


def upsert():
    r = requests.get(f"{SB}/rest/v1/prompt_templates",
                     headers=H_READ,
                     params={"name": "eq.game_read_rules", "sport": "eq.NFL", "select": "id"},
                     timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if rows:
        row_id = rows[0]["id"]
        r2 = requests.patch(f"{SB}/rest/v1/prompt_templates?id=eq.{row_id}",
                            headers=H_WRITE,
                            json={"template": TEMPLATE, "is_active": True},
                            timeout=15)
        print(f"  patch id={row_id}: {r2.status_code}")
    else:
        r2 = requests.post(f"{SB}/rest/v1/prompt_templates",
                           headers=H_WRITE,
                           json={"name": "game_read_rules", "sport": "NFL",
                                 "template": TEMPLATE, "is_active": True},
                           timeout=15)
        print(f"  insert game_read_rules/NFL: {r2.status_code}")


if __name__ == "__main__":
    print("== Seeding NFL game_read_rules with structured CALL output ==")
    upsert()
    print(f"  template length: {len(TEMPLATE)} chars")
