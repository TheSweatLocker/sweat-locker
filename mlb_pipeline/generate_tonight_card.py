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


def format_potd(potd):
    if not potd:
        return "_(POTD not yet locked — check after 8am ET pipeline run)_"
    sport = potd.get("sport", "MLB")
    lean = potd.get("leanDisplay", "—")
    game = potd.get("game", {})
    matchup = f"{game.get('away_team', '?')} @ {game.get('home_team', '?')}"
    score = potd.get("score", {}).get("total", "?")
    confidence = potd.get("confidence", "standard")
    return f"**{sport}** — {matchup}\n**Pick:** {lean}\n**Tier:** {confidence.upper()} | Sweat Score {score}/100"


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


def format_props(props):
    if not props:
        return "_(no qualifying props)_"
    out = []
    for p in props[:3]:
        name = p.get("player_name", "?")
        ptype = p.get("prop_type", "")
        line = p.get("prop_line", "")
        direction = p.get("direction", "over")
        tier = "PRIME" if (p.get("conviction") or 0) >= 80 else "STRONG" if (p.get("conviction") or 0) >= 70 else "LEAN"
        sig_obj = p.get("signals") or {}
        if isinstance(sig_obj, dict):
            first_sig = next(iter(sig_obj.values()), "") if sig_obj else ""
        elif isinstance(sig_obj, list):
            first_sig = sig_obj[0] if sig_obj else ""
        else:
            first_sig = ""
        out.append(f"- **[{tier}] {name} {direction.title()} {line} {ptype.replace('_',' ')}** — _{first_sig}_")
    return "\n".join(out)


def format_dawg(dawg):
    if not dawg:
        return "_(no Dawg of the Day)_"
    return f"**{dawg.get('team', '?')}** ({dawg.get('tier', '?')} {dawg.get('conviction', '?')}) — {dawg.get('matchup', '?')}"


def detect_bucket_plays(games):
    """Surface the biggest bucket-bet edges from game_context data."""
    plays = []
    for g in games:
        away = g.get("away_team", "")
        home = g.get("home_team", "")
        nrfi = g.get("nrfi_score") or 0
        # PRIME NRFI sweet spot
        if 90 <= nrfi <= 94:
            plays.append(f"- **{away} @ {home} NRFI** — score {nrfi}, audit-validated 90-94 PRIME tier (78.9% historical)")
        # YRFI strong lean
        elif nrfi <= 25 and nrfi != 0:
            plays.append(f"- **{away} @ {home} YRFI / 1-3 OVER** — NRFI score {nrfi}, both starters bleed early")
        # Gassed bullpen flag
        h3d = g.get("home_bp_relievers_3d") or 0
        a3d = g.get("away_bp_relievers_3d") or 0
        if int(h3d or 0) >= 12:
            plays.append(f"- **{away} 7-9 winner / +0.5 in 7-9** — {home} bullpen GASSED ({h3d} relievers used last 3d)")
        if int(a3d or 0) >= 12:
            plays.append(f"- **{home} 7-9 winner / +0.5 in 7-9** — {away} bullpen GASSED ({a3d} relievers used last 3d)")
    return plays[:5] or ["_(no high-conviction bucket plays surfaced — run scout_report.py for full bucket breakdown)_"]


def detect_skips(games):
    """Plays we DON'T recommend even though they look juicy — audit transparency."""
    skips = []
    for g in games:
        away = g.get("away_team", "")
        home = g.get("home_team", "")
        nrfi = g.get("nrfi_score") or 0
        if nrfi >= 95:
            skips.append(f"- **{away} @ {home} NRFI {nrfi}** — 95+ band hits 44.1% historically (volatile zone). Take the 1-3 inning bucket version of the same read instead.")
        # PRIME confluence with zero spread edge (chalk trap)
        conf = g.get("signal_confluence_net") or 0
        sd = g.get("spread_delta") or 0
        try:
            sd = float(sd)
        except (TypeError, ValueError):
            sd = 0
        if int(conf) >= 4 and abs(sd) < 1.0:
            skips.append(f"- **{away} @ {home} ML** — confluence +{conf} but spread_delta only {sd:.2f}, no real model edge over market. Chalk trap.")
    return skips[:3] or ["_(nothing flagged as a skip tonight)_"]


def render_card(date):
    potd = fetch_potd(date)
    degen = fetch_daily_degen(date)
    dawg = fetch_dawg(date)
    props = fetch_props(date)
    games = fetch_games(date)

    bucket_plays = detect_bucket_plays(games)
    skips = detect_skips(games)

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

> Tonight's Sweat Locker card.
>
> 🏆 **POTD:** {format_potd(potd).split(chr(10))[1].replace('**Pick:**', '').strip() if potd else 'TBA'}
> 📊 **Top Prop:** {(props[0].get('player_name') if props else 'TBA')} {(props[0].get('direction', 'over') if props else '')} {(props[0].get('prop_line') if props else '')} {(props[0].get('prop_type', '').replace('_',' ') if props else '')}
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
> Posting transparently. Tracking every play. Audited tier hit rates: PRIME NRFI 90-94 = 78.9% (352 games), PRIME confluence ML = ~71% backtest.
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
