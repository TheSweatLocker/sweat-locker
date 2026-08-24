"""Server-side Play of the Day write-up generator.

Reads jerry_cache.best_bet_<today> (written by play_of_day.py with a
placeholder narrative + the POTD context block), builds an attribution-
safe prompt mirroring the one generate_mlb_game_reads.py uses for per-game
reads, calls Claude, writes the narrative back into both the row's
`narrative` column AND `data.narrative` (the app reads from data.narrative).

Eliminates the client-side narrative generation that ran in the app on
first POTD view — single source of truth on the server.

Trigger: 2026-05-28 CHC@PIT POTD wrote "Colin Rea's 5.17 xERA is getting
torched against a Cubs lineup" — Rea IS the Cubs pitcher; he faces the
Pirates. The app prompt was missing the "pitches FOR / FACES lineup"
attribution that generate_mlb_game_reads._pitcher_block already exposes.
Same attribution-bug class as the 5/16 Suarez/Luzardo + 5/8 Eovaldi/NYY
incidents — covered in [[feedback_verify_pitcher_attribution]].

Run order: AFTER play_of_day.py in both 6am + 2pm crons. Idempotent —
if play_of_day overrides the POTD later (afternoon line move), this
re-runs and replaces the narrative.

Usage:
    python generate_potd_narrative.py [--date YYYY-MM-DD] [--force]
"""
import os
import sys
import json
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json", "Prefer": "return=minimal"}
MODEL = "claude-haiku-4-5-20251001"

# Treated as "still the placeholder" so we re-generate. play_of_day.py
# always writes the short form 'Play of the Day: AWAY @ HOME | LEAN' to
# the narrative column. Anything matching that template OR a short
# (<60 char) string means we still need a real write-up.
PLACEHOLDER_PREFIX = "Play of the Day:"


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def is_placeholder(narrative):
    if not narrative:
        return True
    n = narrative.strip()
    if len(n) < 80:
        return True
    if n.startswith(PLACEHOLDER_PREFIX) and "\n" not in n:
        return True
    return False


def call_claude(prompt):
    if not ANTHROPIC_KEY:
        print("  ⚠️  ANTHROPIC_API_KEY missing — skipping narrative generation")
        return None
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    except Exception as e:
        print(f"  ⚠️  Claude call failed: {e}")
        return None


def build_mlb_prompt(potd):
    game = potd.get("game") or {}
    ctx = potd.get("context") or {}
    score = potd.get("score") or {}
    home = game.get("home_team") or "Home"
    away = game.get("away_team") or "Away"
    lean = potd.get("leanDisplay") or "Model Edge"

    # Pre-compute who-benefits framing so Claude can't reinvent it. Bad xERA
    # = the OPPOSING offense scores more. Good xERA = the OPPOSING offense
    # suppressed. We tell Claude the directional relationship explicitly.
    h_p = ctx.get("home_pitcher")
    a_p = ctx.get("away_pitcher")
    h_x = ctx.get("home_sp_xera")
    a_x = ctx.get("away_sp_xera")

    def _xera_frame(name, xera, own_team, opp_team):
        if name is None or xera is None:
            return None
        try:
            x = float(xera)
        except (ValueError, TypeError):
            return None
        if x >= 5.0:
            return f"{name} ({own_team}) has a SHAKY {x} xERA → the {opp_team} offense (which bats against {name}) will score on him."
        if x >= 4.0:
            return f"{name} ({own_team}) has an AVERAGE {x} xERA → the {opp_team} offense gets normal scoring chances against him."
        if x >= 3.0:
            return f"{name} ({own_team}) has a SOLID {x} xERA → he suppresses the {opp_team} offense."
        return f"{name} ({own_team}) has an ELITE {x} xERA → the {opp_team} offense gets shut down."

    home_frame = _xera_frame(h_p, h_x, home, away)
    away_frame = _xera_frame(a_p, a_x, away, home)

    parts = [
        "You are Jerry, a sharp sports analyst for The Sweat Locker app.",
        "Write 2-3 sentences (no more) explaining today's MLB Play of the Day. You have all the data you need below — do NOT hedge, do NOT say you lack information.",
        "",
        f"Game: {away} @ {home}",
        f"Play: {lean}",
        f"Sweat Score: {score.get('total', 'N/A')}/100",
    ]
    if score.get("isNRFI"):
        parts.append(f"PLAY TYPE: NRFI (No Run First Inning) — NRFI Score {score.get('nrfiScore')}/100")
    parts.append("")
    parts.append("PITCHER ↔ OFFENSE ATTRIBUTION (CRITICAL — DO NOT GET WRONG):")
    parts.append(f"- The {away} (away) offense BATS AGAINST {h_p or 'the home starter'} ({home}'s pitcher).")
    parts.append(f"- The {home} (home) offense BATS AGAINST {a_p or 'the away starter'} ({away}'s pitcher).")
    if home_frame: parts.append(f"- {home_frame}")
    if away_frame: parts.append(f"- {away_frame}")
    parts.append(f"DO NOT confuse this. {h_p or 'The home pitcher'} CANNOT give his own {home} teammates 'scoring chances' — only the {away} offense bats against him.")
    if ctx.get("projected_total") is not None:
        parts.append(f"Model projected total: {ctx['projected_total']}")
    if ctx.get("spread_delta") is not None:
        parts.append(f"Spread delta vs market: {ctx['spread_delta']} runs")
    if ctx.get("venue"):
        parts.append(f"Venue: {ctx['venue']}{(' · Temp ' + str(ctx.get('temperature')) + '°F') if ctx.get('temperature') is not None else ''}")
    if ctx.get("nrfi_score") is not None:
        parts.append(f"NRFI score: {ctx['nrfi_score']}")
    parts.append("")
    parts.append("Rules:")
    parts.append("- Lead with the play in **bold** ('**Play of the Day: <Away> @ <Home> <Lean>**')")
    parts.append("- 2-3 sentences explaining the edge using SPECIFIC numbers from above")
    parts.append("- When citing a pitcher's xERA, frame the impact as 'the [opposing offense] scores/gets suppressed' — never 'the pitcher's own team gets scoring chances'")
    parts.append("- End with a one-line conviction stamp like '**That's the play.**' or '**Model sits there.**'")
    parts.append("- NEVER say 'I don't have data', 'let me verify', or hedge")
    parts.append("- Sound like a sharp friend who already did the homework")
    return "\n".join(parts)


def build_prompt(potd):
    sport = (potd.get("sport") or "MLB").upper()
    if sport == "MLB":
        return build_mlb_prompt(potd)
    # Non-MLB POTDs: fall back to a minimal sport-tagged prompt
    game = potd.get("game") or {}
    home = game.get("home_team") or "Home"
    away = game.get("away_team") or "Away"
    lean = potd.get("leanDisplay") or "Model Edge"
    score = potd.get("score") or {}
    return (
        f"You are Jerry, sports analyst for The Sweat Locker app.\n"
        f"Write 2-3 sentences explaining the Play of the Day.\n\n"
        f"Sport: {sport}\nGame: {away} @ {home}\nPlay: {lean}\n"
        f"Sweat Score: {score.get('total', 'N/A')}/100\n\n"
        f"Rules: Use sport-specific metrics only. Lead with bold play title. "
        f"End with a one-line conviction stamp."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_et(), help="Slate date (default today ET)")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if a non-placeholder narrative is already present")
    args = parser.parse_args()

    date_str = args.date
    print(f"=== Generating POTD narrative for {date_str} ===")

    rows = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            f"{SB}/rest/v1/jerry_cache?cache_key=eq.best_bet_{date_str}&select=data,narrative",
            headers=H_READ,
        ), timeout=15
    ).read())
    if not rows:
        print(f"  No best_bet_{date_str} row — nothing to do")
        return

    row = rows[0]
    potd = row.get("data") or {}
    if isinstance(potd, str):
        potd = json.loads(potd)
    if potd.get("noGames"):
        print("  noGames flag set — skipping")
        return

    # noPlay handling (fixed 2026-07-02).
    # When POTD selector returns noPlay (all tier-gate candidates rejected),
    # the game/context/score blocks are empty. Prior behavior sent Claude an
    # empty template and got back a broken "you didn't fill in the data"
    # narrative that shipped to production. Now we generate a proper REST DAY
    # narrative explaining why the gate rejected today's slate.
    if potd.get("noPlay"):
        reason = potd.get("reason") or "no_model_supported_lean"
        reason_text = {
            "no_model_supported_lean": (
                "No game on tonight's slate passed the tier discipline gate. "
                "Every candidate had contested signals across model, cohort, "
                "and Panel readings — none qualified as a Play of the Day."
            ),
        }.get(reason, "No qualified play surfaced on tonight's slate.")
        narrative = (
            f"REST DAY — no Play of the Day tonight.\n\n"
            f"{reason_text} "
            f"The gate exists to protect against forcing a play when the model, "
            f"cohort engine, and Panel don't converge. When they don't align, "
            f"we sit out and wait for a cleaner setup rather than chasing content."
        )
        potd["narrative"] = narrative
        req = urllib.request.Request(
            f"{SB}/rest/v1/jerry_cache?cache_key=eq.best_bet_{date_str}",
            data=json.dumps({"narrative": narrative, "data": potd}).encode(),
            headers=H_WRITE, method="PATCH"
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            print(f"  ✅ Wrote noPlay REST DAY narrative ({len(narrative)} chars, status {resp.status})")
            print()
            print(narrative)
        except urllib.error.HTTPError as e:
            print(f"  ⚠️  PATCH failed {e.code}: {e.read().decode()[:200]}")
        return

    existing_inner = (potd or {}).get("narrative")
    existing_col = row.get("narrative")
    if not args.force and not is_placeholder(existing_inner):
        print(f"  Real narrative already present ({len(existing_inner)} chars) — skip (use --force to overwrite)")
        return

    # 2026-08-24 SELF-HYDRATION FIX
    # play_of_day.py wrote a POTD context with all pitcher/venue/xERA fields
    # null (Phillies@Mariners 8/24 shipped context={"venue": null,
    # "temperature": null, "conviction": 93, ...}). Prompt fed to Claude had
    # no numbers → Claude correctly refused with "I need actual pitcher names"
    # → that refusal shipped to the app as the POTD narrative → home screen
    # rendered "" (see [[feedback_verify_pitcher_attribution]] +
    # [[project_jerry_attribution_validator]]).
    #
    # Instead of chasing every upstream context-write path, hydrate from
    # mlb_game_context here as a defensive backfill. That table has
    # home_pitcher / away_pitcher / home_sp_xera / away_sp_xera / venue /
    # projected_total / spread_delta populated by nightly cron. If the
    # incoming context is thin, we top it up. Silent no-op for non-MLB or
    # when ctx is already rich.
    if (potd.get("sport") or "MLB").upper() == "MLB":
        game = potd.get("game") or {}
        ctx = potd.get("context") or {}
        thin = not (ctx.get("home_pitcher") and ctx.get("away_pitcher"))
        if thin and game.get("home_team") and game.get("away_team"):
            try:
                qh = urllib.parse.quote(game["home_team"], safe="")
                qa = urllib.parse.quote(game["away_team"], safe="")
                mgc_r = urllib.request.Request(
                    f"{SB}/rest/v1/mlb_game_context"
                    f"?game_date=eq.{date_str}"
                    f"&home_team=eq.{qh}&away_team=eq.{qa}"
                    f"&select=home_pitcher,away_pitcher,home_sp_xera,"
                    f"away_sp_xera,venue,temperature,projected_total,"
                    f"spread_delta,close_spread,close_total",
                    headers=H_READ,
                )
                mgc_rows = json.loads(urllib.request.urlopen(mgc_r, timeout=10).read())
                if mgc_rows:
                    hydrated = mgc_rows[0]
                    # Fill only NULL/missing keys — never overwrite
                    filled = []
                    for k, v in hydrated.items():
                        if v is not None and ctx.get(k) is None:
                            ctx[k] = v
                            filled.append(k)
                    if filled:
                        print(f"  🔧 hydrated context from mlb_game_context: {', '.join(filled)}")
                        potd["context"] = ctx
            except Exception as e:
                print(f"  ⚠️  hydration lookup failed (non-fatal): {e}")

    prompt = build_prompt(potd)
    narrative = call_claude(prompt)
    if not narrative:
        print("  No narrative returned")
        return

    # 2026-08-24 REFUSAL GUARD
    # If Claude refuses despite our hydration (rare — usually because a stat
    # is still missing or the model gets stubborn), DON'T ship the refusal
    # to production. Detect known refusal-prefix patterns and reject.
    # Leaves the placeholder in place; app renders a graceful fallback
    # instead of the "I appreciate the detailed setup but..." text.
    REFUSAL_PREFIXES = [
        "i appreciate", "i need to flag", "i can't", "i cannot",
        "unfortunately", "to deliver what", "i don't have", "let me flag",
    ]
    n_low = narrative.lower().lstrip()[:40]
    if any(n_low.startswith(p) for p in REFUSAL_PREFIXES):
        print(f"  🚫 refusal detected — NOT writing to DB. First 100ch: {narrative[:100]}")
        print(f"     App fallback will render instead. Fix upstream context data.")
        return

    # Write narrative into BOTH the data.narrative JSONB field (app reads from
    # there) and the narrative column (legacy + audit). Single update.
    potd["narrative"] = narrative
    req = urllib.request.Request(
        f"{SB}/rest/v1/jerry_cache?cache_key=eq.best_bet_{date_str}",
        data=json.dumps({"narrative": narrative, "data": potd}).encode(),
        headers=H_WRITE, method="PATCH"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        print(f"  ✅ Wrote narrative ({len(narrative)} chars, status {resp.status})")
        print()
        print(narrative)
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  PATCH failed {e.code}: {e.read().decode()[:200]}")


if __name__ == "__main__":
    main()
