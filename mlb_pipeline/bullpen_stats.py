import requests
import os
from dotenv import load_dotenv
import sys

# == 2026-10-29 . OFF-SEASON GUARD =========================================
# Andy: "with mlb not in season anymore ... do all the pitcher and stat
# pullers still run ... whats the cost?" They did. Measured over 14 days of
# start/end heartbeats, MLB workflows burn 255 of the 477 workflow minutes
# spent per day (53%) on a sport whose last game was 2026-10-28.
#
# The money (~$61 of $114/month) is the small half. The real cost is this
# script running GREEN AND EMPTY for four months, so by spring nobody knows
# whether it still works (feedback_green_step_that_writes_nothing). The skip
# is therefore loud.
#
# Fails OPEN by design: no credentials, a bad response or any exception all
# mean RUN, because a gate must never be the reason a live sport goes dark
# (feedback_weather_pullers_silent_noop cost NCAAF five weeks of weather).
if __name__ == '__main__' and not os.environ.get('MLB_IGNORE_SLATE_GATE'):
    try:
        import subprocess as _sp, pathlib as _pl
        _g = _pl.Path(__file__).resolve().parent / 'mlb_slate_gate.py'
        if _g.exists():
            _rc = _sp.run([sys.executable, str(_g), '--quiet']).returncode
            if _rc == 1:
                print(f'[off-season] no MLB slate - skipping {_pl.Path(__file__).name}. '
                      f'Set MLB_IGNORE_SLATE_GATE=1 to force.')
                sys.exit(0)
    except Exception as _e:                                    # noqa: BLE001
        print(f'[off-season] gate check failed ({type(_e).__name__}) - running anyway')

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def get_team_bullpen_stats(season=2026):
    """Fetch team bullpen ERA from MLB Stats API"""
    print(f"Fetching bullpen stats for {season} season...")
    try:
        # Get all teams
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        print(f"Found {len(teams)} teams")
        return teams
    except Exception as e:
        print(f"Error fetching teams: {e}")
        return []

def get_bullpen_era(team_id, team_name, season=2026):
    """Get bullpen ERA for a specific team"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={
                "stats": "season",
                "group": "pitching",
                "season": season,
                "playerPool": "qualifier"
            }
        )
        data = r.json()
        stats = data.get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None
        
        # Get team totals
        team_stats = stats[0]["splits"][0]["stat"]
        era = float(team_stats.get("era", 4.50))
        saves = int(team_stats.get("saves", 0))
        blown_saves = int(team_stats.get("blownSaves", 0))
        holds = int(team_stats.get("holds", 0))
        
        return {
            "team": team_name,
            "bullpen_era": era,
            "saves": saves,
            "blown_saves": blown_saves,
            "holds": holds,
            "save_pct": round(saves / (saves + blown_saves) * 100, 1) if (saves + blown_saves) > 0 else 0,
            "season": str(season),
            "updated_at": "now()"
        }
    except Exception as e:
        return None

def get_pitching_inning_buckets(team_id, season=2026):
    """Fetch team pitching by inning bucket (1-3, 4-6, 7-9).
    Note: 7-9 bucket (ig07) is effectively bullpen since starters rarely pitch past 6.
    1-3 and 4-6 are TEAM pitching (starter + reliever blended)."""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={
                "stats": "statSplits",
                "group": "pitching",
                "season": season,
                "sitCodes": "i01,i02,i03,i04,i05,i06,i07,i08,i09,ig07"
            },
            timeout=15
        )
        data = r.json().get("stats", [])
        if not data or not data[0].get("splits"):
            return None
        rows = {}
        for s in data[0]["splits"]:
            code = s.get("split", {}).get("code")
            if code:
                rows[code] = s.get("stat", {})

        def ip_to_decimal(ip_raw):
            try:
                if "." in str(ip_raw):
                    whole, frac = str(ip_raw).split(".")
                    return int(whole) + (int(frac) / 3)
                return float(ip_raw)
            except (ValueError, TypeError):
                return 0.0

        def aggregate(codes):
            ip = 0.0
            er = 0
            k = 0
            bb = 0
            h = 0
            hr = 0
            bf = 0
            for code in codes:
                if code not in rows:
                    continue
                stat = rows[code]
                ip += ip_to_decimal(stat.get("inningsPitched", "0") or "0")
                er += int(stat.get("earnedRuns", 0) or 0)
                k += int(stat.get("strikeOuts", 0) or 0)
                bb += int(stat.get("baseOnBalls", 0) or 0)
                h += int(stat.get("hits", 0) or 0)
                hr += int(stat.get("homeRuns", 0) or 0)
                bf += int(stat.get("battersFaced", 0) or 0)
            if ip < 1:
                return None
            return {
                "era": round((er * 9) / ip, 2),
                "whip": round((bb + h) / ip, 2),
                "k_pct": round((k / bf) * 100, 1) if bf else None,
                "bb_pct": round((bb / bf) * 100, 1) if bf else None,
                "hr_per_9": round((hr * 9) / ip, 2),
                "ip": round(ip, 1),
                "bf": bf,
            }

        bucket_1_3 = aggregate(["i01", "i02", "i03"])
        bucket_4_6 = aggregate(["i04", "i05", "i06"])
        bucket_7_9 = aggregate(["ig07"]) if "ig07" in rows else aggregate(["i07", "i08", "i09"])
        return {
            "innings_1_3": bucket_1_3,
            "innings_4_6": bucket_4_6,
            "innings_7_9": bucket_7_9,
        }
    except Exception as e:
        print(f"  Inning bucket pitching error for team {team_id}: {e}")
        return None


def upload_bullpen(data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_bullpen_stats?on_conflict=team,season",
        headers=headers,
        json=data
    )
    if r.status_code not in [200, 201, 204]:
        print(f"  Upload error {r.status_code}: {r.text[:200]}")
        return False
    return True

def run():
    teams = get_team_bullpen_stats(2026)
    if not teams:
        print("Trying 2025...")
        teams = get_team_bullpen_stats(2025)
    
    if not teams:
        print("No teams found")
        return

    success = 0
    errors = 0

    for team in teams:
        team_id = team.get("id")
        team_name = team.get("name")
        if not team_id or not team_name:
            continue
        
        season = 2026
        stats = get_bullpen_era(team_id, team_name, season)
        if not stats:
            season = 2025
            stats = get_bullpen_era(team_id, team_name, season)
        
        if stats:
            # Layer in inning bucket pitching splits (1-3, 4-6, 7-9 buckets)
            buckets = get_pitching_inning_buckets(team_id, season)
            if buckets:
                for label in ("innings_1_3", "innings_4_6", "innings_7_9"):
                    b = buckets.get(label)
                    if not b:
                        continue
                    stats[f"pitching_{label.split('_', 1)[1]}_era"] = b["era"]
                    stats[f"pitching_{label.split('_', 1)[1]}_whip"] = b["whip"]
                    stats[f"pitching_{label.split('_', 1)[1]}_k_pct"] = b["k_pct"]
                    stats[f"pitching_{label.split('_', 1)[1]}_bb_pct"] = b["bb_pct"]
                    stats[f"pitching_{label.split('_', 1)[1]}_hr_per_9"] = b["hr_per_9"]
                    stats[f"pitching_{label.split('_', 1)[1]}_ip"] = b["ip"]
                    stats[f"pitching_{label.split('_', 1)[1]}_bf"] = b["bf"]

            if upload_bullpen(stats):
                pen79 = stats.get("pitching_7_9_era", "—")
                print(f"✅ {team_name} — Bullpen ERA: {stats['bullpen_era']}, 7-9 ERA: {pen79}")
                success += 1
            else:
                print(f"❌ {team_name} — upload failed")
                errors += 1
        else:
            print(f"⚠️ {team_name} — no stats available")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()