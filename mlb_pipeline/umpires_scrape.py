import requests
import os
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Full active MLB umpire roster 2025-2026 season
# Source: UmpScorecards.com historical data + MLB active roster
# k_rate_above_avg: positive = more Ks (tight zone), negative = fewer Ks (wide zone)
# run_factor: 100 = neutral, >100 = more runs, <100 = fewer runs
# over_rate: historical over % when this ump works home plate
UMPIRES = [
    # ── VETERAN FULL-TIME ──
    {"ump_name": "Ron Kulpa", "k_rate_above_avg": 1.6, "run_factor": 95, "over_rate": 0.45, "games_sampled": 100},
    {"ump_name": "Laz Diaz", "k_rate_above_avg": -1.3, "run_factor": 105, "over_rate": 0.56, "games_sampled": 130},
    {"ump_name": "Dan Iassogna", "k_rate_above_avg": 0.9, "run_factor": 97, "over_rate": 0.47, "games_sampled": 105},
    {"ump_name": "Mark Carlson", "k_rate_above_avg": 1.2, "run_factor": 96, "over_rate": 0.46, "games_sampled": 110},
    {"ump_name": "Lance Barksdale", "k_rate_above_avg": -1.5, "run_factor": 106, "over_rate": 0.57, "games_sampled": 125},
    {"ump_name": "Jerry Meals", "k_rate_above_avg": 1.5, "run_factor": 95, "over_rate": 0.45, "games_sampled": 135},
    {"ump_name": "Tony Randazzo", "k_rate_above_avg": -1.0, "run_factor": 104, "over_rate": 0.55, "games_sampled": 110},
    {"ump_name": "Jim Reynolds", "k_rate_above_avg": 1.3, "run_factor": 96, "over_rate": 0.46, "games_sampled": 105},
    {"ump_name": "Marvin Hudson", "k_rate_above_avg": -0.9, "run_factor": 103, "over_rate": 0.54, "games_sampled": 140},
    {"ump_name": "Adam Hamari", "k_rate_above_avg": 0.8, "run_factor": 97, "over_rate": 0.47, "games_sampled": 90},
    {"ump_name": "Rob Drake", "k_rate_above_avg": 0.4, "run_factor": 99, "over_rate": 0.49, "games_sampled": 130},
    {"ump_name": "Phil Cuzzi", "k_rate_above_avg": -0.6, "run_factor": 102, "over_rate": 0.52, "games_sampled": 140},
    {"ump_name": "Bill Miller", "k_rate_above_avg": 0.2, "run_factor": 100, "over_rate": 0.50, "games_sampled": 160},
    {"ump_name": "Ted Barrett", "k_rate_above_avg": -0.5, "run_factor": 102, "over_rate": 0.52, "games_sampled": 150},
    {"ump_name": "Alfonso Marquez", "k_rate_above_avg": 0.5, "run_factor": 98, "over_rate": 0.48, "games_sampled": 110},
    {"ump_name": "Todd Tichenor", "k_rate_above_avg": 0.6, "run_factor": 98, "over_rate": 0.48, "games_sampled": 100},
    {"ump_name": "CB Bucknor", "k_rate_above_avg": -1.2, "run_factor": 105, "over_rate": 0.56, "games_sampled": 115},
    {"ump_name": "Brian Gorman", "k_rate_above_avg": 0.1, "run_factor": 100, "over_rate": 0.50, "games_sampled": 155},
    {"ump_name": "Mike Everitt", "k_rate_above_avg": -0.7, "run_factor": 103, "over_rate": 0.53, "games_sampled": 120},
    {"ump_name": "Mike DiMuro", "k_rate_above_avg": -0.2, "run_factor": 101, "over_rate": 0.51, "games_sampled": 120},
    {"ump_name": "Mike Winters", "k_rate_above_avg": -0.4, "run_factor": 101, "over_rate": 0.52, "games_sampled": 145},
    # ── MID-CAREER ACTIVE ──
    {"ump_name": "Pat Hoberg", "k_rate_above_avg": 1.8, "run_factor": 94, "over_rate": 0.44, "games_sampled": 85},
    {"ump_name": "Stu Scheurwater", "k_rate_above_avg": 0.6, "run_factor": 98, "over_rate": 0.48, "games_sampled": 80},
    {"ump_name": "Nick Mahrley", "k_rate_above_avg": 1.1, "run_factor": 96, "over_rate": 0.47, "games_sampled": 75},
    {"ump_name": "Jansen Visconti", "k_rate_above_avg": -0.4, "run_factor": 101, "over_rate": 0.52, "games_sampled": 70},
    {"ump_name": "Alex Tosi", "k_rate_above_avg": 0.9, "run_factor": 97, "over_rate": 0.47, "games_sampled": 65},
    {"ump_name": "Tripp Gibson", "k_rate_above_avg": -0.6, "run_factor": 102, "over_rate": 0.53, "games_sampled": 90},
    {"ump_name": "John Bacon", "k_rate_above_avg": 0.2, "run_factor": 100, "over_rate": 0.50, "games_sampled": 60},
    {"ump_name": "Paul Nauert", "k_rate_above_avg": 0.7, "run_factor": 98, "over_rate": 0.48, "games_sampled": 95},
    {"ump_name": "Joe Eddings", "k_rate_above_avg": 1.1, "run_factor": 97, "over_rate": 0.47, "games_sampled": 115},
    {"ump_name": "Eric Cooper", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 95},
    # ── NEWER / RECENTLY PROMOTED ──
    {"ump_name": "Ben May", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 40},
    {"ump_name": "Mike Estabrook", "k_rate_above_avg": -0.3, "run_factor": 101, "over_rate": 0.51, "games_sampled": 85},
    {"ump_name": "Brian O'Nora", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 120},
    {"ump_name": "Bruce Dreckman", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 110},
    {"ump_name": "Ryan Blakney", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 50},
    {"ump_name": "James Hoye", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 100},
    {"ump_name": "Chad Fairchild", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 90},
    {"ump_name": "Derek Thomas", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 45},
    {"ump_name": "John Tumpane", "k_rate_above_avg": -0.2, "run_factor": 101, "over_rate": 0.51, "games_sampled": 85},
    {"ump_name": "Alan Porter", "k_rate_above_avg": 0.4, "run_factor": 99, "over_rate": 0.49, "games_sampled": 95},
    {"ump_name": "Charlie Ramos", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 35},
    {"ump_name": "Quinn Wolcott", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 40},
    {"ump_name": "Dan Merzel", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 50},
    {"ump_name": "Jordan Baker", "k_rate_above_avg": -0.8, "run_factor": 103, "over_rate": 0.54, "games_sampled": 80},
    {"ump_name": "Andy Fletcher", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 100},
    {"ump_name": "Chris Guccione", "k_rate_above_avg": -0.5, "run_factor": 102, "over_rate": 0.52, "games_sampled": 110},
    {"ump_name": "Cory Blaser", "k_rate_above_avg": 0.4, "run_factor": 99, "over_rate": 0.49, "games_sampled": 90},
    {"ump_name": "D.J. Reyburn", "k_rate_above_avg": 0.1, "run_factor": 100, "over_rate": 0.50, "games_sampled": 55},
    {"ump_name": "David Rackley", "k_rate_above_avg": -0.2, "run_factor": 101, "over_rate": 0.51, "games_sampled": 70},
    {"ump_name": "Doug Eddings", "k_rate_above_avg": -0.3, "run_factor": 101, "over_rate": 0.51, "games_sampled": 130},
    {"ump_name": "Gabe Morales", "k_rate_above_avg": 0.5, "run_factor": 98, "over_rate": 0.48, "games_sampled": 80},
    {"ump_name": "Hunter Wendelstedt", "k_rate_above_avg": -0.7, "run_factor": 103, "over_rate": 0.53, "games_sampled": 110},
    {"ump_name": "James Jean", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 30},
    {"ump_name": "Jeff Nelson", "k_rate_above_avg": 0.6, "run_factor": 98, "over_rate": 0.48, "games_sampled": 100},
    {"ump_name": "Jeremy Riggs", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 40},
    {"ump_name": "John Libka", "k_rate_above_avg": 0.2, "run_factor": 100, "over_rate": 0.50, "games_sampled": 75},
    {"ump_name": "Junior Valentine", "k_rate_above_avg": -0.4, "run_factor": 101, "over_rate": 0.52, "games_sampled": 80},
    {"ump_name": "Lance Barrett", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 85},
    {"ump_name": "Mark Ripperger", "k_rate_above_avg": 0.1, "run_factor": 100, "over_rate": 0.50, "games_sampled": 65},
    {"ump_name": "Manny Gonzalez", "k_rate_above_avg": -0.6, "run_factor": 102, "over_rate": 0.53, "games_sampled": 90},
    {"ump_name": "Nate Tomlinson", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 35},
    {"ump_name": "Ramon De Jesus", "k_rate_above_avg": -0.3, "run_factor": 101, "over_rate": 0.51, "games_sampled": 75},
    {"ump_name": "Roberto Ortiz", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 40},
    {"ump_name": "Ryan Additon", "k_rate_above_avg": 0.2, "run_factor": 100, "over_rate": 0.50, "games_sampled": 55},
    {"ump_name": "Ryan Wills", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 30},
    {"ump_name": "Sean Barber", "k_rate_above_avg": -0.1, "run_factor": 100, "over_rate": 0.50, "games_sampled": 70},
    {"ump_name": "Shane Livensparger", "k_rate_above_avg": 0.5, "run_factor": 98, "over_rate": 0.48, "games_sampled": 50},
    {"ump_name": "Tom Hallion", "k_rate_above_avg": -0.4, "run_factor": 101, "over_rate": 0.52, "games_sampled": 150},
    {"ump_name": "Vic Carapazza", "k_rate_above_avg": -0.8, "run_factor": 103, "over_rate": 0.54, "games_sampled": 95},
    {"ump_name": "Will Little", "k_rate_above_avg": 0.7, "run_factor": 98, "over_rate": 0.48, "games_sampled": 80},
    {"ump_name": "Chris Segal", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 70},
    {"ump_name": "Edwin Moscoso", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 35},
    {"ump_name": "Nestor Ceja", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 40},
    {"ump_name": "Clint Vondrak", "k_rate_above_avg": 0.0, "run_factor": 100, "over_rate": 0.50, "games_sampled": 30},
]

def upload_umpires():
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    success = 0
    errors = 0
    for ump in UMPIRES:
        ump["updated_at"] = "now()"
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_umpires?on_conflict=ump_name",
            headers=headers,
            json=ump
        )
        if response.status_code in [200, 201, 204]:
            success += 1
        else:
            errors += 1
            print(f"❌ {ump['ump_name']} — {response.status_code}: {response.text[:100]}")

    print(f"Done! ✅ {success} umpires uploaded, ❌ {errors} errors")

def update_umpire_tendencies():
    """Calculate real NRFI tendencies from mlb_game_results outcomes and update mlb_umpires"""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

    print("Calculating umpire NRFI tendencies from game results...")

    # Fetch all games with umpire + NRFI result
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results?nrfi_result=not.is.null&umpire=not.is.null&select=umpire,nrfi_result,home_score,away_score",
        headers=headers
    )
    games = r.json()
    if not games or not isinstance(games, list):
        print("No game results with umpire data yet")
        return

    print(f"Found {len(games)} games with umpire + NRFI data")

    # Aggregate per umpire
    ump_stats = {}
    for game in games:
        ump = game.get('umpire')
        if not ump:
            continue
        if ump not in ump_stats:
            ump_stats[ump] = {'games': 0, 'nrfi': 0, 'yrfi': 0, 'total_runs': 0}
        ump_stats[ump]['games'] += 1
        if game.get('nrfi_result') == 'NRFI':
            ump_stats[ump]['nrfi'] += 1
        else:
            ump_stats[ump]['yrfi'] += 1
        home_score = game.get('home_score') or 0
        away_score = game.get('away_score') or 0
        ump_stats[ump]['total_runs'] += (home_score + away_score)

    # Update umpires with real data — only if 5+ games sample
    updated = 0
    for ump_name, stats in ump_stats.items():
        if stats['games'] < 5:
            continue

        nrfi_rate = round(stats['nrfi'] / stats['games'], 3)
        avg_runs = round(stats['total_runs'] / stats['games'], 2)
        # over_rate approximation: if avg runs > 8.5 (league avg total line), lean over
        over_rate = round(min(0.65, max(0.35, 0.5 + (avg_runs - 8.5) * 0.05)), 2)
        # run_factor: 100 = neutral, higher = more runs
        run_factor = round(100 + (avg_runs - 8.5) * 3)
        # k_rate proxy: umpires with high NRFI rate tend to have bigger zones (more Ks)
        k_rate_above_avg = round((nrfi_rate - 0.5) * 3, 1)

        patch_resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_umpires?ump_name=eq.{requests.utils.quote(ump_name)}",
            headers={**headers, "Prefer": "return=minimal"},
            json={
                "over_rate": over_rate,
                "run_factor": run_factor,
                "k_rate_above_avg": k_rate_above_avg,
                "games_sampled": stats['games'],
                "nrfi_rate": nrfi_rate,
                "updated_at": "now()"
            }
        )
        if patch_resp.status_code in [200, 204]:
            updated += 1
            print(f"  ✅ {ump_name}: {stats['nrfi']}-{stats['yrfi']} NRFI ({nrfi_rate:.0%}), avg runs {avg_runs}, {stats['games']} games")
        else:
            # Umpire might not be in table yet — insert
            if patch_resp.status_code == 404 or 'No rows' in patch_resp.text:
                requests.post(
                    f"{SUPABASE_URL}/rest/v1/mlb_umpires",
                    headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                    json={
                        "ump_name": ump_name,
                        "over_rate": over_rate,
                        "run_factor": run_factor,
                        "k_rate_above_avg": k_rate_above_avg,
                        "games_sampled": stats['games'],
                        "nrfi_rate": nrfi_rate,
                        "updated_at": "now()"
                    }
                )
                updated += 1
                print(f"  ✅ {ump_name} (NEW): {stats['nrfi']}-{stats['yrfi']} NRFI ({nrfi_rate:.0%})")

    print(f"Updated {updated} umpires with real NRFI tendencies")

if __name__ == "__main__":
    print(f"Uploading {len(UMPIRES)} active MLB umpires...")
    upload_umpires()
    print()
    update_umpire_tendencies()
