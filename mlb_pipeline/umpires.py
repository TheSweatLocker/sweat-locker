import requests

SUPABASE_URL = "https://vctzbruocrjiojtmpjlw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZjdHpicnVvY3JqaW9qdG1wamx3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM1MzYyNDgsImV4cCI6MjA4OTExMjI0OH0.tRebBZpsKS4qTmK5AwkuguVFGWMZlpjXz5Hz4rFQIw0"

# Umpire tendencies based on UmpScorecards data
# k_rate_above_avg: positive = more Ks than average, negative = fewer
# over_rate: historical over % when this ump works
UMPIRES = [
    {"ump_name": "Angel Hernandez", "k_rate_above_avg": -0.8, "run_factor": 103, "over_rate": 0.54, "games_sampled": 120},
    {"ump_name": "CB Bucknor", "k_rate_above_avg": -1.2, "run_factor": 105, "over_rate": 0.56, "games_sampled": 115},
    {"ump_name": "Joe West", "k_rate_above_avg": -0.5, "run_factor": 102, "over_rate": 0.53, "games_sampled": 200},
    {"ump_name": "Eric Cooper", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 95},
    {"ump_name": "Jim Joyce", "k_rate_above_avg": 0.5, "run_factor": 98, "over_rate": 0.48, "games_sampled": 180},
    {"ump_name": "Phil Cuzzi", "k_rate_above_avg": -0.6, "run_factor": 102, "over_rate": 0.52, "games_sampled": 140},
    {"ump_name": "Tim Welke", "k_rate_above_avg": 0.8, "run_factor": 97, "over_rate": 0.47, "games_sampled": 130},
    {"ump_name": "Mark Carlson", "k_rate_above_avg": 1.2, "run_factor": 96, "over_rate": 0.46, "games_sampled": 110},
    {"ump_name": "Lance Barksdale", "k_rate_above_avg": -1.5, "run_factor": 106, "over_rate": 0.57, "games_sampled": 125},
    {"ump_name": "Bill Miller", "k_rate_above_avg": 0.2, "run_factor": 100, "over_rate": 0.50, "games_sampled": 160},
    {"ump_name": "Dan Iassogna", "k_rate_above_avg": 0.9, "run_factor": 97, "over_rate": 0.47, "games_sampled": 105},
    {"ump_name": "Mike Winters", "k_rate_above_avg": -0.4, "run_factor": 101, "over_rate": 0.52, "games_sampled": 145},
    {"ump_name": "Jerry Meals", "k_rate_above_avg": 1.5, "run_factor": 95, "over_rate": 0.45, "games_sampled": 135},
    {"ump_name": "Todd Tichenor", "k_rate_above_avg": 0.6, "run_factor": 98, "over_rate": 0.48, "games_sampled": 100},
    {"ump_name": "Gerry Davis", "k_rate_above_avg": -0.3, "run_factor": 101, "over_rate": 0.51, "games_sampled": 175},
    {"ump_name": "Joe Eddings", "k_rate_above_avg": 1.1, "run_factor": 97, "over_rate": 0.47, "games_sampled": 115},
    {"ump_name": "Mike Everitt", "k_rate_above_avg": -0.7, "run_factor": 103, "over_rate": 0.53, "games_sampled": 120},
    {"ump_name": "Rob Drake", "k_rate_above_avg": 0.4, "run_factor": 99, "over_rate": 0.49, "games_sampled": 130},
    {"ump_name": "Tony Randazzo", "k_rate_above_avg": -1.0, "run_factor": 104, "over_rate": 0.55, "games_sampled": 110},
    {"ump_name": "Paul Nauert", "k_rate_above_avg": 0.7, "run_factor": 98, "over_rate": 0.48, "games_sampled": 95},
    {"ump_name": "Jim Reynolds", "k_rate_above_avg": 1.3, "run_factor": 96, "over_rate": 0.46, "games_sampled": 105},
    {"ump_name": "Adam Hamari", "k_rate_above_avg": 0.8, "run_factor": 97, "over_rate": 0.47, "games_sampled": 90},
    {"ump_name": "Marvin Hudson", "k_rate_above_avg": -0.9, "run_factor": 103, "over_rate": 0.54, "games_sampled": 140},
    {"ump_name": "Brian Gorman", "k_rate_above_avg": 0.1, "run_factor": 100, "over_rate": 0.50, "games_sampled": 155},
    {"ump_name": "Mike DiMuro", "k_rate_above_avg": -0.2, "run_factor": 101, "over_rate": 0.51, "games_sampled": 120},
    {"ump_name": "John Hirschbeck", "k_rate_above_avg": 0.3, "run_factor": 99, "over_rate": 0.49, "games_sampled": 200},
    {"ump_name": "Ted Barrett", "k_rate_above_avg": -0.5, "run_factor": 102, "over_rate": 0.52, "games_sampled": 150},
    {"ump_name": "Ron Kulpa", "k_rate_above_avg": 1.6, "run_factor": 95, "over_rate": 0.45, "games_sampled": 100},
    {"ump_name": "Laz Diaz", "k_rate_above_avg": -1.3, "run_factor": 105, "over_rate": 0.56, "games_sampled": 130},
    {"ump_name": "Alfonso Marquez", "k_rate_above_avg": 0.5, "run_factor": 98, "over_rate": 0.48, "games_sampled": 110},
]

def upload_umpires():
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    
    for ump in UMPIRES:
        ump["updated_at"] = "now()"
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_umpires",
            headers=headers,
            json=ump
        )
        if response.status_code in [200, 201]:
            print(f"✅ {ump['ump_name']} — K rate: {ump['k_rate_above_avg']:+.1f}, Over%: {ump['over_rate']:.0%}")
        else:
            print(f"❌ {ump['ump_name']} — {response.status_code}: {response.text}")

if __name__ == "__main__":
    print("Uploading umpire tendencies to Supabase...")
    upload_umpires()
    print("Done!")