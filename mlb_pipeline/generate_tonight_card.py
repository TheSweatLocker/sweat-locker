"""Auto-generate tonight's content card from pipeline output.

Pulls POTD, Daily Degen, props, Dawg, and game_context for today.
Writes a copy/paste-ready markdown file to content/{date}_card.md
covering multiple formats (TikTok short, FB long-form, parlay structure,
"what we skip" transparency block).

Designed to run after play_of_day.py in the daily pipeline so by the time
Andy wakes up + opens his content tools, the post is drafted.

Usage:
    python generate_tonight_card.py [date]    # date defaults to today ET
"""

import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  Supabase {table} error {e.code}: {e.read().decode()[:200]}")
        return []


def get_today_et():
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime("%Y-%m-%d")


def fetch_potd(date):
    rows = sb_get("jerry_cache", {"game_id": f"eq.best_bet_{date}", "select": "data"})
    if not rows:
        return None
    return rows[0].get("data") or None


def fetch_daily_degen(date):
    rows = sb_get("daily_degen", {"game_date": f"eq.{date}", "select": "legs,narrative"})
    if not rows:
        return None
    return rows[0]


def fetch_dawg(date):
    rows = sb_get("daily_dawg", {"game_date": f"eq.{date}", "select": "team,matchup,conviction,tier,signals,narrative"})
    if not rows:
        return None
    return rows[0]


def fetch_props(date):
    return sb_get("mlb_pipeline_props", {
        "game_date": f"eq.{date}",
        "select": "player_name,prop_type,prop_line,direction,conviction,signals,matchup",
        "order": "conviction.desc",
        "limit": "5",
    })


def fetch_games(date):
    return sb_get("mlb_game_context", {
        "game_date": f"eq.{date}",
        "select": "away_team,home_team,home_pitcher,away_pitcher,nrfi_score,signal_confluence_net,spread_delta,projected_total,close_total,home_bp_relievers_3d,away_bp_relievers_3d,primary_play",
    })


def fetch_tier_rates():
    """Pull live 30d audited rates from mlb_tier_calibration. Returns
    {tier: (rate_pct, total_n)} so card copy reflects the *current*
    cohort performance instead of stale hardcoded numbers.

    2026-05-22 fix: previously unbounded; 1300+ historical rows exceeded
    PostgREST's 1000-row default, randomly dropping cohorts. Filter to
    today's computed_date (one row per cohort = well under any limit).
    Same bug class as the YRFI '0% audited' issue in generate_sweat_card."""
    today = get_today_et()
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "eq.30d",
        "sport": "eq.mlb",
        "computed_date": f"eq.{today}",
        "select": "tier,hit_rate,total",
    }) or sb_get("mlb_tier_calibration", {
        "window_label": "eq.30d",
        "sport": "eq.mlb",
        "select": "tier,hit_rate,total,computed_date",
        "order": "computed_date.desc",
        "limit": "500",
    })
    out = {}
    for r in rows or []:
        tier = r.get("tier")
        rate = r.get("hit_rate")
        total = r.get("total")
        if tier and rate is not None and total:
            out[tier] = (round(float(rate) * 100, 1), int(total))
    return out


def fmt_rate(tier_rates, key, fallback="N/A"):
    """Format a tier as 'XX.X% on N games', or fallback when missing."""
    if key not in tier_rates:
        return fallback
    pct, n = tier_rates[key]
    return f"{pct}% on {n} games (30d)"


def format_potd(potd):
    if not potd:
        return "_(POTD not yet locked — check after 8am ET pipeline run)_"
    sport = potd.get("sport", "MLB")
    lean = potd.get("leanDisplay", "—")
    game = potd.get("game", {})
    matchup = f"{game.get('away_team', '?')} @ {game.get('home_team', '?')}"
    score = potd.get("score", {}).get("total", "?")
    confidence = potd.get("confidence", "standard")
    # Score label — 2026-07-31 Sweat Card swap: POTD is now Jerry-anchored
    # (see jerry_anchor_potd.py). We look for the anchor flag in the payload;
    # if present, show "Jerry" label; else fall back to legacy "Sweat Score".
    is_jerry = potd.get("anchor") == "jerry_synthesis_v1" \
               or (potd.get("score", {}) or {}).get("source") == "jerry_conviction" \
               or (potd.get("context", {}) or {}).get("jerry_anchored")
    score_label = "Jerry" if is_jerry else "Sweat Score"
    return f"**{sport}** — {matchup}\n**Pick:** {lean}\n**Tier:** {confidence.upper()} | {score_label} {score}/100"


def format_top_legs(degen):
    if not degen or not degen.get("legs"):
        return "_(no Daily Degen output)_"
    out = []
    for leg in degen.get("legs", []):
        pick = leg.get("pick", "?")
        tier = leg.get("tier", "")
        ltype = leg.get("type", "")
        matchup = leg.get("matchup", "")
        sigs = leg.get("signals") or []
        first_sig = (sigs[0] if isinstance(sigs, list) and sigs else "")
        out.append(f"- **[{tier}] {ltype}: {pick}** — {matchup}\n  _{first_sig}_")
    return "\n".join(out)


def _prop_label(p):
    """Render the prop line. K props show expected Ks from signals (the
    point estimate) instead of the raw audit threshold (always 5.1)."""
    name = p.get("player_name", "?")
    ptype = p.get("prop_type", "")
    direction = (p.get("direction") or "over").title()
    sig_obj = p.get("signals") or {}
    if isinstance(sig_obj, dict) and ptype in ("ks_over", "ks_under"):
        proj = sig_obj.get("_projected_ks")
        if proj is not None:
            return f"{name} — {proj} expected Ks ({direction.lower()})"
    line = p.get("prop_line", "")
    return f"{name} {direction} {line} {ptype.replace('_',' ')}"


def format_props(props):
    if not props:
        return "_(no qualifying props)_"
    out = []
    for p in props[:3]:
        tier = "PRIME" if (p.get("conviction") or 0) >= 80 else "STRONG" if (p.get("conviction") or 0) >= 70 else "LEAN"
        sig_obj = p.get("signals") or {}
        if isinstance(sig_obj, dict):
            first_sig = next(iter(sig_obj.values()), "") if sig_obj else ""
        elif isinstance(sig_obj, list):
            first_sig = sig_obj[0] if sig_obj else ""
        else:
            first_sig = ""
        out.append(f"- **[{tier}] {_prop_label(p)}** — _{first_sig}_")
    return "\n".join(out)


def format_dawg(dawg):
    if not dawg:
        return "_(no Dawg of the Day)_"
    return f"**{dawg.get('team', '?')}** ({dawg.get('tier', '?')} {dawg.get('conviction', '?')}) — {dawg.get('matchup', '?')}"


def detect_bucket_plays(games, tier_rates):
    """Surface the biggest bucket-bet edges from game_context data."""
    prime_rate = fmt_rate(tier_rates, "nrfi_prime_90_94", "audit pending")
    yrfi_rate = fmt_rate(tier_rates, "yrfi_lean_le40", "audit pending")
    plays = []
    for g in games:
        away = g.get("away_team", "")
        home = g.get("home_team", "")
        nrfi = g.get("nrfi_score") or 0
        # PRIME NRFI sweet spot
        if 90 <= nrfi <= 94:
            plays.append(f"- **{away} @ {home} NRFI** — score {nrfi}, audit-validated 90-94 PRIME tier ({prime_rate})")
        # YRFI lean (gated 2026-05-18: only post when 1st-inn ERA in 6-8 sweet
        # spot; extreme fragility ≥8 is small-sample noise at 29% YRFI rate)
        elif nrfi <= 25 and nrfi != 0:
            h1 = g.get("home_first_inning_era") or 0
            a1 = g.get("away_first_inning_era") or 0
            max_fi = max(float(h1), float(a1))
            if 6.0 <= max_fi < 8.0:
                plays.append(f"- **{away} @ {home} YRFI** — NRFI {nrfi} + 1st-inn ERA {max_fi:.1f} (audit sweet spot)")
            else:
                plays.append(f"- **{away} @ {home} YRFI lean** — NRFI {nrfi}, but 1st-inn ERA {max_fi:.1f} outside audit sweet spot — small sample")
        # Gassed bullpen flag was surfacing as "X 7-9 winner / +0.5 in 7-9"
        # but (1) that market isn't widely offered and (2) we don't have
        # cohort audit data on whether gassed-pen actually predicts late-
        # inning run scoring. Pulled 2026-05-07 pending audit.
        # See bullpen_gassed_game_over cohort in audit_tier_calibration.py.
    return plays[:5] or ["_(no high-conviction bucket plays surfaced — run scout_report.py for full bucket breakdown)_"]


def detect_skips(games, tier_rates):
    """Plays we DON'T recommend even though they look juicy — audit transparency."""
    volatile_rate = fmt_rate(tier_rates, "nrfi_volatile_95plus", "below baseline")
    skips = []
    for g in games:
        away = g.get("away_team", "")
        home = g.get("home_team", "")
        nrfi = g.get("nrfi_score") or 0
        if nrfi >= 95:
            skips.append(f"- **{away} @ {home} NRFI {nrfi}** — 95+ volatile band, {volatile_rate}. Take the 1-3 inning bucket version of the same read instead.")
        # PRIME confluence ML "trap" filter — retuned 2026-05-19 after audit.
        # OLD threshold flagged abs(sd) < 1.0 as a chalk trap. Wrong — that
        # band audits 70.8% on n=24 (winners, not traps). The real losing
        # zone is the 1.0-1.5 delta band at 46.2% (n=13).
        # Audit data via _audit_chalk_trap.py on 250 resolved games:
        #   PRIME conf + delta <1.0   → 70.8% (17-7)   was being mislabeled
        #   PRIME conf + delta 1.0-1.5 → 46.2% (6-7)   actual trap
        #   PRIME conf + delta ≥3.0   → 72.7% (8-3)
        # Net effect: card now correctly recommends PRIME + tiny-delta ML
        # plays (they hit) and skips PRIME + 1.0-1.5 delta plays (they don't).
        conf = g.get("signal_confluence_net") or 0
        sd = g.get("spread_delta") or 0
        try:
            sd = float(sd)
        except (TypeError, ValueError):
            sd = 0
        if int(conf) >= 4 and 1.0 <= abs(sd) < 1.5:
            skips.append(
                f"- **{away} @ {home} ML** — confluence +{conf} but spread_delta "
                f"{abs(sd):.2f} sits in the 1.0-1.5 audit dead-zone (46% lifetime "
                f"on n=13). Take a different angle on this game."
            )
    return skips[:3] or ["_(nothing flagged as a skip tonight)_"]


def render_card(date):
    potd = fetch_potd(date)
    degen = fetch_daily_degen(date)
    dawg = fetch_dawg(date)
    props = fetch_props(date)
    games = fetch_games(date)
    tier_rates = fetch_tier_rates()

    bucket_plays = detect_bucket_plays(games, tier_rates)
    skips = detect_skips(games, tier_rates)

    # Pretty date for display
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        pretty_date = d.strftime("%A, %B %-d") if sys.platform != "win32" else d.strftime("%A, %B %#d")
    except Exception:
        pretty_date = date

    md = f"""# Tonight's Card — {pretty_date}

_Auto-generated from {date} pipeline output. Edit prose to taste before posting._

---

## 🏆 PLAY OF THE DAY
{format_potd(potd)}

## 🎯 DAILY DEGEN — Top conviction legs
{format_top_legs(degen)}

## 🐕 DAWG OF THE DAY
{format_dawg(dawg)}

## ⚡ PROPS — Top-3 by conviction
{format_props(props)}

## 📊 BUCKET BET ANGLES
{chr(10).join(bucket_plays)}

## 🚫 SKIP — Plays the model rejects (audit transparency)
{chr(10).join(skips)}

---

## 📱 TIKTOK / IG SHORT (copy/paste, ~30 sec read)

> Tonight's Sweat Locker card — 🧠 Powered by Jerry.
>
> 🏆 **POTD:** {format_potd(potd).split(chr(10))[1].replace('**Pick:**', '').strip() if potd else 'TBA'}
> 📊 **Top Prop:** {_prop_label(props[0]) if props else 'TBA'}
> 🐕 **Dawg:** {dawg.get('team') if dawg else 'TBA'}
>
> Skipping NRFI 95+ tonight (volatile zone, hits 44%). {skips[0].split(' — ')[0].replace('- **', '').replace('**', '') if skips and 'NRFI' in (skips[0] if skips else '') else ''}
>
> Tracking everything. Receipts in the morning.

---

## 📘 FACEBOOK / LONG-FORM (copy/paste, ~150 words)

> **Tonight's data-driven card from The Sweat Locker model:**
>
> 🏆 **PLAY OF THE DAY:** {format_potd(potd).replace(chr(10), ' | ') if potd else 'TBA'}
>
> 🎯 **PROPS WITH MODEL EDGE:**
{chr(10).join('> ' + p for p in format_props(props).split(chr(10)))}
>
> 📊 **BUCKET BETS** _(the inning-level edges most cappers don't track):_
{chr(10).join('> ' + b for b in bucket_plays)}
>
> 🚫 **WHAT WE'RE NOT BETTING (and why):**
{chr(10).join('> ' + s for s in skips)}
>
> Posting transparently. Tracking every play. Live audit (rolling 30d): PRIME NRFI 90-94 = {fmt_rate(tier_rates, 'nrfi_prime_90_94', 'pending')} | YRFI ≤40 = {fmt_rate(tier_rates, 'yrfi_lean_le40', 'pending')} | PRIME confluence ML = {fmt_rate(tier_rates, 'confluence_prime_ge4', 'pending')}.
"""
    return md


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Missing SUPABASE env vars. Run from mlb_pipeline/ with .env present.")
        sys.exit(1)
    date = sys.argv[1] if len(sys.argv) > 1 else get_today_et()
    print(f"Generating content card for {date}...")
    md = render_card(date)
    # Write to content/{date}_card.md
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "content")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{date}_card.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n✅ Card written to: {out_path}")
    print(f"\n--- Full card ---\n{md}")


if __name__ == "__main__":
    main()
