"""Fix 'this season' prose bug in NCAAF signal_sources.

User caught 9/5: Iowa write-up said "Iowa covering 69.20% ATS this season
(9-4)" — impossible, 2026 season just started. Root cause: 9 signal_sources
rows have hardcoded 'this season' prose but the underlying stats are a
ROLLING window (last ~13 games spanning 2025 → early 2026).

Fix: change 'this season' → 'trailing L13' in all 9 templates. Data
labels the true window (rolling 13 games) instead of the false season
claim. Applies immediately — next context generation uses new prose.
"""
import os, sys, io, requests
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

for line in Path(__file__).parent.joinpath('.env').read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
HW = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
      'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

REPLACEMENTS = [
    ('home_covers_as_fav',
     '{home_team} covers as favorite {home_covers_as_fav_pct}% this season',
     '{home_team} covers as favorite {home_covers_as_fav_pct}% trailing L13'),
    ('away_covers_as_dog',
     '{away_team} covers as underdog {away_covers_as_dog_pct}% this season',
     '{away_team} covers as underdog {away_covers_as_dog_pct}% trailing L13'),
    ('home_team_ats_hot_season',
     '{home_team} covering {home_season_cover_pct}% ATS this season ({home_season_ats_wins}-{home_season_ats_losses})',
     '{home_team} covering {home_season_cover_pct}% ATS trailing L13 ({home_season_ats_wins}-{home_season_ats_losses})'),
    ('home_team_over_trend_season',
     '{home_team} games OVER {home_season_over_pct}% this season',
     '{home_team} games OVER {home_season_over_pct}% trailing L13'),
    ('home_team_under_trend_season',
     '{home_team} games UNDER {home_season_over_pct}% overs this season',
     '{home_team} games UNDER {home_season_over_pct}% overs trailing L13'),
    ('away_team_over_trend_season',
     '{away_team} games OVER {away_season_over_pct}% this season',
     '{away_team} games OVER {away_season_over_pct}% trailing L13'),
    ('away_team_under_trend_season',
     '{away_team} games UNDER {away_season_over_pct}% overs this season',
     '{away_team} games UNDER {away_season_over_pct}% overs trailing L13'),
    ('both_teams_over_trend_season',
     'both {home_team} + {away_team} trend OVER this season',
     'both {home_team} + {away_team} trend OVER trailing L13'),
    ('both_teams_under_trend_season',
     'both {home_team} + {away_team} trend UNDER this season',
     'both {home_team} + {away_team} trend UNDER trailing L13'),
]

for signal_key, _old, new_prose in REPLACEMENTS:
    r = requests.patch(
        f'{SB}/rest/v1/signal_sources?signal_key=eq.{signal_key}&sport=eq.NCAAF',
        headers=HW, json={'display_prose_template': new_prose}, timeout=10,
    )
    print(f'  {r.status_code}  {signal_key}')

print('\nDone. Next NCAAF context generation uses new prose.')
