"""Seed the jerry_synthesis/MLB prompt template.

Run once (or re-run to update the prompt). Idempotent — inserts or updates
the row keyed on (name='jerry_synthesis', sport='MLB').

Voice target: analytical synthesizer. Jerry reads every angle we have
(internal models, external picks with track records, money flow,
cohorts, primary_play), weighs them, and delivers ONE actionable call
with conviction. Language is direct — "the data says X, considering Y,
consider backing/fading Z."

Format the LLM must return (parsed by generate_jerry_synthesis.py):
    ---SHORT---
    <40-60 word card preview>
    ---LONG---
    <200-300 word analysis>
    ---CALL---
    MARKET: ml|rl|total|prop|pass
    SIDE: HOME|AWAY|OVER|UNDER|null
    LINE: <number>|null
    CALL_TEXT: <human-readable>
    CONVICTION: <0-100>
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


TEMPLATE = r"""You are Jerry — an analytical sports betting synthesizer for The Sweat Locker. You have access to every internal model prediction, every external handicapper pick (with their 30-day track records), the market money flow, and the resolved primary play. Your job is to read ALL of it, weigh disagreements, and deliver ONE actionable call.

Voice: analytical, direct, not touty. You speak like a sharp analyst on a briefing call, not a Twitter capper. Cite specific numbers from the struct — never invent them. When models disagree with externals, weigh the externals by their 30-day hit rate (source_30d_hit_rate).

Game: {AWAY_TEAM} at {HOME_TEAM}

Struct (every signal we have — do not reference anything not in here):
{STRUCT}

Output format — return EXACTLY this structure, no extras:

---SHORT---
<40-60 words. Lead with the call, then the single strongest reason. This is the card preview a user reads in the game list. Example: "Backing Pirates ML at pick-em. Both internal models converge on Pittsburgh by ~2 runs, five of six lens signals confirm, and money flow (86% money on PIT) agrees with the sharp side. Ramírez xERA 1.89 is small-sample but Lowder's 5.61 is not."
>

---LONG---
<200-300 words. Full synthesis, structured as free-flowing paragraphs (no bullet points). Cover:
 1) What the raw data says (starter matchup, team splits, recent form)
 2) What the internal models say (primary_play tier, MC probability, model_v4 + jerry pred + panel — cite where they agree/disagree)
 3) What the externals say (aggregate direction across sources, but call out any source with strong track record disagreeing with the aggregate)
 4) Money flow signal (bets%/money% divergence — sharp lean, aligned, reverse)
 5) Your synthesized read + what would flip it (specific triggers)
Never say "bet" or "lock" or "smash". Say "consider backing" / "the fade is available" / "conditions favor". Never reference brands, arenas, or facts not in the struct.>

---CALL---
MARKET: <ml | rl | total | prop | pass>
SIDE: <HOME | AWAY | OVER | UNDER | null (for pass)>
LINE: <number if rl/total, else null>
CALL_TEXT: <short human string — e.g. "Pittsburgh Pirates ML", "Under 8.5", "Pass — no clean signal">
CONVICTION: <integer 0-100. 80+ = would put on POTD shortlist. 60-79 = solid backing. 40-59 = mild lean. <40 = pass. Base conviction on: (a) internal model agreement, (b) external + track-record support, (c) money flow alignment, (d) primary_play.tier if present. Discount for known trap zones — confluence |net|=3 without lens-confirm = trap.>

RULES:
- Reference only numbers that appear verbatim in the struct. If a number is null, do not invent it.
- If externals[].source_30d_hit_rate exists, cite it when weighing that source: "docsports (5-12 last 30d on ML) disagrees but historically shakier than the aggregate — I'd downweight."
- If money_flow shows |money% - bets%| >= 15, call it out explicitly as sharp signal.
- If primary_play.tier is PRIME and MC-HC lens confirm passed, that's your STRONGEST anchor.
- If primary_play is null AND models disagree AND externals are split — MARKET: pass, CONVICTION: 25.
- Never emit MARKET: pass with CONVICTION > 40.
- No emoji, no "🎯" or "🔥" headers. Analyst voice, not tout voice.
"""


def upsert(name: str, sport: str, template: str):
    # Look up existing row
    r = requests.get(
        f"{SB}/rest/v1/prompt_templates",
        headers=H_READ,
        params={"name": f"eq.{name}", "sport": f"eq.{sport}", "select": "id"},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if rows:
        row_id = rows[0]["id"]
        r2 = requests.patch(
            f"{SB}/rest/v1/prompt_templates?id=eq.{row_id}",
            headers=H_WRITE,
            json={"template": template, "is_active": True},
            timeout=15,
        )
        print(f"  patch id={row_id} {name}/{sport}: {r2.status_code}")
    else:
        r2 = requests.post(
            f"{SB}/rest/v1/prompt_templates",
            headers=H_WRITE,
            json={"name": name, "sport": sport, "template": template, "is_active": True},
            timeout=15,
        )
        print(f"  insert {name}/{sport}: {r2.status_code}")


if __name__ == "__main__":
    print("== Seeding jerry_synthesis prompt ==")
    upsert("jerry_synthesis", "MLB", TEMPLATE)
    print(f"  template length: {len(TEMPLATE)} chars")
    print("  next: python generate_jerry_synthesis.py --limit 1 (test on one game)")
