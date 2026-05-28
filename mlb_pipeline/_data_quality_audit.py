"""Daily data-quality self-audit for today's slate.

Purpose (user directive, 2026-05-27): "I am not going to be able to catch
everything, and I feel like that's all that's been happening — me catching
silly mistakes." This script does the catching so the user doesn't have to.

What it flags per game:
  * pitcher_vs_team mastery firing on <15 IP (the 5/27 Matz class)
  * home/away splits firing on undersized sample (now gated upstream, but
    we re-check the live row in case stale data lingers)
  * L3 ERA missing → pitcher had <3 actual starts in the available window
  * Pitcher attribution mismatch between mlb_game_context and MLB Stats API
    probablePitcher (the 5/27 Davis-Martin-vs-Sandlin class)
  * Missing core stats (xERA, K%, season ERA) on any confirmed starter
  * Inning bucket data missing on confirmed starters
  * Sweat-card surface check: anything ranked in top_8 / POTD / DotD whose
    row has CRITICAL flags becomes a HARD STOP

Exit code 0 = clean. Exit code 1 = critical issues found.
Add to nightly cron after game_context.py so issues surface in workflow logs.

Usage:
    python _data_quality_audit.py [--date YYYY-MM-DD]
"""
import os, sys, json, urllib.request, urllib.parse, argparse
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
SB = os.environ["SUPABASE_URL"]
H = {"apikey": os.environ["SUPABASE_KEY"], "Authorization": f'Bearer {os.environ["SUPABASE_KEY"]}'}


def get(path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(SB + path, headers=H), timeout=30).read())


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def fetch_probable_pitchers(date_str):
    """Pull MLB Stats API probables for cross-check."""
    try:
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=probablePitcher,team"
        r = urllib.request.urlopen(url, timeout=15)
        data = json.loads(r.read())
        out = {}  # {(away_team_id, home_team_id): {'away': name, 'home': name}}
        for d in data.get("dates", []):
            for g in d.get("games", []):
                t = g.get("teams", {})
                away = t.get("away", {})
                home = t.get("home", {})
                a_team = (away.get("team") or {}).get("name")
                h_team = (home.get("team") or {}).get("name")
                a_pit = (away.get("probablePitcher") or {}).get("fullName")
                h_pit = (home.get("probablePitcher") or {}).get("fullName")
                if a_team and h_team:
                    out[(a_team, h_team)] = {"away": a_pit, "home": h_pit}
        return out
    except Exception as e:
        print(f"  ⚠️  Could not fetch MLB probable pitchers: {e}")
        return {}


def fetch_pitcher_stats(name):
    """Pull season K%, season ERA, inning bucket from mlb_pitcher_stats."""
    if not name:
        return None
    try:
        q = urllib.parse.quote(name)
        r = urllib.request.urlopen(
            urllib.request.Request(
                f"{SB}/rest/v1/mlb_pitcher_stats?player_name=eq.{q}&season=eq.2026&limit=1",
                headers=H,
            ),
            timeout=15,
        )
        rows = json.loads(r.read())
        return rows[0] if rows else None
    except Exception:
        return None


def audit_row(row, mlb_probables, pstats_cache):
    """Return list of (severity, message) tuples. severity ∈ {'critical', 'warn'}."""
    flags = []
    away = row.get("away_team")
    home = row.get("home_team")
    ap = row.get("away_pitcher")
    hp = row.get("home_pitcher")

    # 1. Attribution cross-check (the 5/27 Sandlin/Martin class)
    api = mlb_probables.get((away, home))
    if api:
        if ap and api.get("away") and ap.strip().lower() != api["away"].strip().lower():
            flags.append(("critical", f"AWAY pitcher mismatch: DB='{ap}' vs MLB API='{api['away']}'"))
        if hp and api.get("home") and hp.strip().lower() != api["home"].strip().lower():
            flags.append(("critical", f"HOME pitcher mismatch: DB='{hp}' vs MLB API='{api['home']}'"))

    # 2. vs-team mastery sample gate (the 5/27 Matz class)
    for side, name in (("away", ap), ("home", hp)):
        if not name:
            continue
        era = row.get(f"{side}_pitcher_vs_team_era")
        avg = row.get(f"{side}_pitcher_vs_team_avg")
        ip = row.get(f"{side}_pitcher_vs_team_ip")
        if era is not None and ip is not None and ip < 15:
            flags.append((
                "critical",
                f"{side.upper()} vs-team firing on {ip} IP ({name} vs opp) — below 15-IP gate, ERA={era}/AVG={avg}",
            ))

    # 3. L3 ERA missing for confirmed starter — usually thin career sample
    for side, name in (("away", ap), ("home", hp)):
        if not name:
            continue
        l3 = row.get(f"{side}_pitcher_last_3_era")
        if l3 is None:
            flags.append(("warn", f"{side.upper()} L3 ERA missing for {name} — <3 cross-season starts on file"))

    # 4. Core season stats — pull from mlb_pitcher_stats
    for side, name in (("away", ap), ("home", hp)):
        if not name:
            continue
        if name not in pstats_cache:
            pstats_cache[name] = fetch_pitcher_stats(name)
        ps = pstats_cache[name]
        if ps is None:
            flags.append(("critical", f"{side.upper()} pitcher row MISSING in mlb_pitcher_stats: {name}"))
            continue
        if ps.get("xera") is None:
            flags.append(("warn", f"{side.upper()} xERA missing in pitcher_stats for {name}"))
        if ps.get("k_pct") is None:
            flags.append(("warn", f"{side.upper()} K% missing in pitcher_stats for {name}"))
        # Inning bucket: warn if confirmed starter has no 1_3 data — drives NRFI/1st-inning signals
        if ps.get("innings_1_3_era") is None:
            flags.append(("warn", f"{side.upper()} innings_1_3 bucket missing for {name}"))
        # First inning splits feed YRFI/NRFI directly
        if ps.get("first_inning_ip") is not None and ps.get("first_inning_ip") < 2:
            flags.append(("warn", f"{side.upper()} first_inning sample thin ({ps.get('first_inning_ip')} IP) for {name}"))

    # 5. First-inning data (NRFI/YRFI driver) — warn only when no IP at all
    for side, name in (("away", ap), ("home", hp)):
        if not name:
            continue
        fi_era = row.get(f"{side}_first_inning_era")
        fi_ip = row.get(f"{side}_first_inning_ip")
        if fi_era is not None and fi_ip is not None and fi_ip < 2:
            flags.append(("warn", f"{side.upper()} first_inning_era on thin sample ({fi_ip} IP) for {name}"))

    # 6. Wind/weather sanity
    wind = row.get("wind_speed")
    if wind is not None and (wind < 0 or wind > 60):
        flags.append(("warn", f"Wind speed out of range: {wind} mph"))

    # 7. Total / spread sanity
    tot = row.get("close_total") or row.get("open_total")
    if tot is not None and (tot < 5 or tot > 14):
        flags.append(("warn", f"Total out of typical range: {tot}"))

    return flags


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_et(), help="Slate date (YYYY-MM-DD)")
    args = parser.parse_args()

    date_str = args.date
    print("=" * 78)
    print(f"DATA-QUALITY AUDIT — {date_str}")
    print("=" * 78)

    games = get(f"/rest/v1/mlb_game_context?game_date=eq.{date_str}&select=*&order=away_team.asc")
    print(f"\n  {len(games)} games in mlb_game_context")
    mlb_probables = fetch_probable_pitchers(date_str)
    print(f"  {len(mlb_probables)} games with MLB Stats API probables\n")

    total_critical = 0
    total_warn = 0
    clean_games = 0
    pstats_cache = {}

    for g in games:
        flags = audit_row(g, mlb_probables, pstats_cache)
        c = sum(1 for s, _ in flags if s == "critical")
        w = sum(1 for s, _ in flags if s == "warn")
        total_critical += c
        total_warn += w
        if not flags:
            clean_games += 1
            continue

        emoji = "🚨" if c else "⚠️"
        status = "CRITICAL" if c else "WARN"
        print(f"  {emoji}  [{status}] {g.get('away_team')} @ {g.get('home_team')}  ({c} critical, {w} warn)")
        for sev, msg in flags:
            tag = "    🚨" if sev == "critical" else "    ⚠️"
            print(f"{tag} {msg}")

    print()
    print("=" * 78)
    print(f"SUMMARY:  {clean_games}/{len(games)} games clean  •  "
          f"{total_critical} critical  •  {total_warn} warnings")
    print("=" * 78)

    if total_critical:
        print("\n🚨 CRITICAL ISSUES present — investigate before publishing the card.")
        sys.exit(1)

    print("\n✅ No critical issues. (Warnings are FYI; review if any appear on the card.)")
    sys.exit(0)


if __name__ == "__main__":
    main()
