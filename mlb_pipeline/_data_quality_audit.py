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
    """Return list of (severity, message) tuples.

    severity ∈ {'critical', 'warn', 'info'}.
      critical — a real defect; block on it.
      warn     — worth a look before the card publishes.
      info     — expected-by-design state, reported so it stays visible
                 without pretending it is a problem. Never affects the
                 exit code (added 2026-09-19 with the vs-team floor fix).
    """
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

    # 2. vs-team sample floor (the 5/27 Matz class)
    #
    # 2026-09-19: this check was asserting a rule the pipeline RETIRED on
    # 2026-08-21. It flagged CRITICAL whenever vs-team ip < 15, which by
    # today is 15 of 16 games EVERY day — samples ran 4.3 to 14.3 IP and
    # every one went red. A check that fails almost every game daily
    # teaches everyone to ignore the whole audit, which costs more than
    # having no audit at all.
    #
    # What actually changed: game_context.py deliberately dropped the hard
    # null-gate at ip<15 (see its note ~line 1286) because it was
    # suppressing real signal — Cameron's 11 IP / 12 K vs DET lost his
    # ENTIRE vs-team read, including the K/9 rate that stabilises fast.
    # Current design surfaces the raw numbers with a mastery_reliable
    # flag, and the MASTERY LABEL gates downstream (jerry_model
    # mastery_ip_gate=15, cohort_features mastery cohort ip>=15) stop
    # "mastery" prose from firing on thin samples. The Matz risk is
    # handled at the label, not by hiding the data.
    #
    # So the audit now asserts the floor the pipeline itself enforces:
    # ip < 3 is unusable relief-appearance noise and should never have
    # been written — that is a genuine bug and stays CRITICAL. Between 3
    # and 15 IP is the intended state, reported once as INFO rather than
    # as a per-game emergency.
    #
    # NOTE: mastery_reliable is computed in-memory and never persisted to
    # mlb_game_context, so this audit cannot verify the label gate from
    # the DB. Catching mastery prose on a thin sample needs a read-text
    # check — out of scope here, logged rather than faked.
    VS_TEAM_FLOOR_IP = 3
    MASTERY_IP = 15
    for side, name in (("away", ap), ("home", hp)):
        if not name:
            continue
        era = row.get(f"{side}_pitcher_vs_team_era")
        avg = row.get(f"{side}_pitcher_vs_team_avg")
        ip = row.get(f"{side}_pitcher_vs_team_ip")
        if era is None or ip is None:
            continue
        if ip < VS_TEAM_FLOOR_IP:
            flags.append((
                "critical",
                f"{side.upper()} vs-team on {ip} IP ({name} vs opp) — below the "
                f"{VS_TEAM_FLOOR_IP}-IP floor game_context enforces; this row "
                f"should not exist. ERA={era}/AVG={avg}",
            ))
        elif ip < MASTERY_IP:
            flags.append((
                "info",
                f"{side.upper()} vs-team thin sample {ip} IP ({name} vs opp) — "
                f"expected; numbers surface, mastery label does not fire below "
                f"{MASTERY_IP} IP. ERA={era}/AVG={avg}",
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
    total_info = 0
    clean_games = 0
    pstats_cache = {}

    for g in games:
        flags = audit_row(g, mlb_probables, pstats_cache)
        c = sum(1 for s, _ in flags if s == "critical")
        w = sum(1 for s, _ in flags if s == "warn")
        i = sum(1 for s, _ in flags if s == "info")
        total_critical += c
        total_warn += w
        total_info += i
        # "Clean" means nothing actionable. Info notes are expected-by-
        # design, so a game carrying only those is still clean — otherwise
        # the headline reads 1/16 clean on a perfectly healthy slate.
        if not c and not w:
            clean_games += 1
            if not i:
                continue

        emoji = "🚨" if c else ("⚠️" if w else "ℹ️")
        status = "CRITICAL" if c else ("WARN" if w else "INFO")
        print(f"  {emoji}  [{status}] {g.get('away_team')} @ {g.get('home_team')}  "
              f"({c} critical, {w} warn, {i} info)")
        for sev, msg in flags:
            tag = ("    🚨" if sev == "critical"
                   else "    ⚠️" if sev == "warn" else "    ℹ️")
            print(f"{tag} {msg}")

    print()
    print("=" * 78)
    print(f"SUMMARY:  {clean_games}/{len(games)} games clean  •  "
          f"{total_critical} critical  •  {total_warn} warnings  •  "
          f"{total_info} info")
    print("=" * 78)

    if total_critical:
        print("\n🚨 CRITICAL ISSUES present — investigate before publishing the card.")
        sys.exit(1)

    print("\n✅ No critical issues. (Warnings are FYI; review if any appear on the card.)")
    sys.exit(0)


if __name__ == "__main__":
    main()
