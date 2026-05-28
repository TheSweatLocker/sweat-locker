"""Re-fetch vs-team mastery stats for today's slate using the new 5-season
lookback (replaces the old 2-season window).

Trigger: 2026-05-27 Matz @ BAL incident. The 2-season window pulled only
9.7 IP of Matz vs Orioles data showing 0.93 ERA / .200 BAA — actual career
sample is 38.3 IP / 4.23 ERA / .276 BAA. We publicly cited the small-sample
hot streak as "mastery" and it failed badly. The function fix lives in
game_context.get_pitcher_vs_team; this script forces a backfill of TODAY'S
rows so the stale 2-season numbers stop being served right now, not on the
next nightly cron.

Usage:
    python _backfill_vs_team_today.py
"""
import os
import sys
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

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json", "Prefer": "return=minimal"}


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def lookup_pitcher_id(name):
    import urllib.parse as up
    try:
        r = urllib.request.urlopen(
            f"https://statsapi.mlb.com/api/v1/people/search?names={up.quote(name)}",
            timeout=15,
        )
        people = json.loads(r.read()).get("people", [])
        return people[0]["id"] if people else None
    except Exception:
        return None


def lookup_team_id_map():
    r = urllib.request.urlopen(
        "https://statsapi.mlb.com/api/v1/teams?sportId=1", timeout=15
    )
    out = {}
    for t in json.loads(r.read()).get("teams", []):
        if t.get("name"):
            out[t["name"]] = t["id"]
    return out


def get_pitcher_vs_team(pitcher_id, opp_team_id):
    """Same logic as game_context.get_pitcher_vs_team with the new 5-season
    lookback. Returns {era_vs_team, avg_vs_team, ip_vs_team, k_vs_team}."""
    if not pitcher_id or not opp_team_id:
        return None
    try:
        agg = {"er": 0, "ip": 0.0, "k": 0, "ab": 0, "hits": 0, "g": 0}
        for season in (2026, 2025, 2024, 2023, 2022):
            r = urllib.request.urlopen(
                f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
                f"?stats=gameLog&group=pitching&season={season}",
                timeout=15,
            )
            splits = (json.loads(r.read()).get("stats") or [{}])[0].get("splits", [])
            for sp in splits:
                if sp.get("opponent", {}).get("id") != opp_team_id:
                    continue
                stat = sp.get("stat", {})
                ip_str = str(stat.get("inningsPitched", "0"))
                ip = float((ip_str.replace(".1", ".333").replace(".2", ".667")) or "0")
                agg["ip"] += ip
                agg["er"] += int(stat.get("earnedRuns", 0) or 0)
                agg["k"] += int(stat.get("strikeOuts", 0) or 0)
                agg["ab"] += int(stat.get("atBats", 0) or 0)
                agg["hits"] += int(stat.get("hits", 0) or 0)
                agg["g"] += 1
            time.sleep(0.1)
        # 15-IP gate matches the source-side gate in game_context.get_pitcher_vs_team
        # (raised from 3 IP after the 5/27 Matz incident). Pitchers with <15 IP
        # vs opponent across 5 seasons have their fields cleared so no downstream
        # system can cite noise as mastery.
        if agg["ip"] < 15:
            return None
        era = round((agg["er"] * 9.0) / agg["ip"], 2)
        avg = round(agg["hits"] / agg["ab"], 3) if agg["ab"] > 0 else 0.0
        k_per_9 = round((agg["k"] * 9.0) / agg["ip"], 2)
        return {
            "era_vs_team": era,
            "avg_vs_team": avg,
            "ip_vs_team": round(agg["ip"], 1),
            "k_vs_team": agg["k"],
            "k_per_9_vs_team": k_per_9,
        }
    except Exception as e:
        print(f"  err fetching pitcher_vs_team: {e}")
        return None


def fetch_games_today():
    date = today_et()
    url = (
        f"{SB}/rest/v1/mlb_game_context?game_date=eq.{date}"
        f"&select=id,away_team,home_team,away_pitcher,home_pitcher,"
        f"away_pitcher_vs_team_era,away_pitcher_vs_team_avg,away_pitcher_vs_team_ip,"
        f"home_pitcher_vs_team_era,home_pitcher_vs_team_avg,home_pitcher_vs_team_ip"
    )
    r = urllib.request.urlopen(
        urllib.request.Request(url, headers=H_READ), timeout=20
    )
    return json.loads(r.read())


def patch(row_id, payload):
    url = f"{SB}/rest/v1/mlb_game_context?id=eq.{row_id}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=H_WRITE, method="PATCH"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        print(f"    ⚠️ PATCH failed {e.code}: {e.read().decode()[:200]}")
        return False


def main():
    print(f"=== Backfill vs-team stats for {today_et()} (5-season lookback) ===")
    games = fetch_games_today()
    print(f"  {len(games)} games")
    team_ids = lookup_team_id_map()
    print(f"  {len(team_ids)} MLB teams cached")

    updated = 0
    for g in games:
        away = g.get("away_team")
        home = g.get("home_team")
        ap = g.get("away_pitcher")
        hp = g.get("home_pitcher")
        if not (away and home):
            continue
        payload = {}
        # Away pitcher faces home team
        if ap:
            pid = lookup_pitcher_id(ap)
            tid = team_ids.get(home)
            if pid and tid:
                old_era = g.get("away_pitcher_vs_team_era")
                old_ip = g.get("away_pitcher_vs_team_ip")
                vs = get_pitcher_vs_team(pid, tid)
                if vs:
                    payload["away_pitcher_vs_team_era"] = vs["era_vs_team"]
                    payload["away_pitcher_vs_team_avg"] = vs["avg_vs_team"]
                    payload["away_pitcher_vs_team_ip"] = vs["ip_vs_team"]
                    payload["away_pitcher_vs_team_k_per_9"] = vs["k_per_9_vs_team"]
                    if old_era != vs["era_vs_team"] or old_ip != vs["ip_vs_team"]:
                        print(
                            f"  {ap} vs {home}: "
                            f"OLD={old_era} ERA / {old_ip} IP  "
                            f"NEW={vs['era_vs_team']} ERA / {vs['ip_vs_team']} IP"
                        )
                else:
                    # New fetch came back insufficient (<15 IP). Clear any stale
                    # under-sampled data so it can't surface as a signal.
                    if old_era is not None or old_ip is not None:
                        payload["away_pitcher_vs_team_era"] = None
                        payload["away_pitcher_vs_team_avg"] = None
                        payload["away_pitcher_vs_team_ip"] = None
                        payload["away_pitcher_vs_team_k_per_9"] = None
                        print(f"  {ap} vs {home}: CLEARED — only {old_ip} IP across 5 seasons (was {old_era} ERA)")
        # Home pitcher faces away team
        if hp:
            pid = lookup_pitcher_id(hp)
            tid = team_ids.get(away)
            if pid and tid:
                old_era = g.get("home_pitcher_vs_team_era")
                old_ip = g.get("home_pitcher_vs_team_ip")
                vs = get_pitcher_vs_team(pid, tid)
                if vs:
                    payload["home_pitcher_vs_team_era"] = vs["era_vs_team"]
                    payload["home_pitcher_vs_team_avg"] = vs["avg_vs_team"]
                    payload["home_pitcher_vs_team_ip"] = vs["ip_vs_team"]
                    payload["home_pitcher_vs_team_k_per_9"] = vs["k_per_9_vs_team"]
                    if old_era != vs["era_vs_team"] or old_ip != vs["ip_vs_team"]:
                        print(
                            f"  {hp} vs {away}: "
                            f"OLD={old_era} ERA / {old_ip} IP  "
                            f"NEW={vs['era_vs_team']} ERA / {vs['ip_vs_team']} IP"
                        )
                else:
                    if old_era is not None or old_ip is not None:
                        payload["home_pitcher_vs_team_era"] = None
                        payload["home_pitcher_vs_team_avg"] = None
                        payload["home_pitcher_vs_team_ip"] = None
                        payload["home_pitcher_vs_team_k_per_9"] = None
                        print(f"  {hp} vs {away}: CLEARED — only {old_ip} IP across 5 seasons (was {old_era} ERA)")
        if payload:
            if patch(g["id"], payload):
                updated += 1
    print(f"  patched {updated} rows")


if __name__ == "__main__":
    main()
