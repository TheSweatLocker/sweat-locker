"""Seed ncaaf_team_aliases — 134 FBS teams (Phase 1 bootstrap).

One-time seed. FBS composition changes occasionally (realignment) but
core map is stable. Odds API uses full names like "Alabama Crimson Tide"
or short "Alabama"; CFBD uses school-name canonical ("Alabama"). We
normalize on the CFBD short form as canonical.

Idempotent — on_conflict=canonical_name.

USAGE:
    python ncaaf_seed_aliases.py
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
     'Content-Type': 'application/json',
     'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

# 134 FBS teams (2026 season). Format:
# (canonical, full_name, location, nickname, conference, alt_names)
TEAMS = [
    # SEC (16 teams as of 2024 realignment)
    ('Alabama',        'Alabama Crimson Tide',        'Alabama',       'Crimson Tide',  'SEC', ['Bama']),
    ('Arkansas',       'Arkansas Razorbacks',         'Arkansas',      'Razorbacks',    'SEC', []),
    ('Auburn',         'Auburn Tigers',               'Auburn',        'Tigers',        'SEC', []),
    ('Florida',        'Florida Gators',              'Florida',       'Gators',        'SEC', []),
    ('Georgia',        'Georgia Bulldogs',            'Georgia',       'Bulldogs',      'SEC', ['UGA']),
    ('Kentucky',       'Kentucky Wildcats',           'Kentucky',      'Wildcats',      'SEC', []),
    ('LSU',            'LSU Tigers',                  'LSU',           'Tigers',        'SEC', []),
    ('Mississippi State','Mississippi State Bulldogs','Mississippi State','Bulldogs',   'SEC', ['Miss State']),
    ('Missouri',       'Missouri Tigers',             'Missouri',      'Tigers',        'SEC', ['Mizzou']),
    ('Oklahoma',       'Oklahoma Sooners',            'Oklahoma',      'Sooners',       'SEC', ['OU']),
    ('Ole Miss',       'Ole Miss Rebels',             'Ole Miss',      'Rebels',        'SEC', ['Mississippi']),
    ('South Carolina', 'South Carolina Gamecocks',    'South Carolina','Gamecocks',     'SEC', []),
    ('Tennessee',      'Tennessee Volunteers',        'Tennessee',     'Volunteers',    'SEC', ['Vols']),
    ('Texas',          'Texas Longhorns',             'Texas',         'Longhorns',     'SEC', []),
    ('Texas A&M',      'Texas A&M Aggies',            'Texas A&M',     'Aggies',        'SEC', ['TAMU']),
    ('Vanderbilt',     'Vanderbilt Commodores',       'Vanderbilt',    'Commodores',    'SEC', ['Vandy']),

    # Big Ten (18 teams as of 2024)
    ('Illinois',       'Illinois Fighting Illini',    'Illinois',      'Fighting Illini','Big Ten', []),
    ('Indiana',        'Indiana Hoosiers',            'Indiana',       'Hoosiers',      'Big Ten', []),
    ('Iowa',           'Iowa Hawkeyes',               'Iowa',          'Hawkeyes',      'Big Ten', []),
    ('Maryland',       'Maryland Terrapins',          'Maryland',      'Terrapins',     'Big Ten', ['Terps']),
    ('Michigan',       'Michigan Wolverines',         'Michigan',      'Wolverines',    'Big Ten', []),
    ('Michigan State', 'Michigan State Spartans',     'Michigan State','Spartans',      'Big Ten', ['MSU']),
    ('Minnesota',      'Minnesota Golden Gophers',    'Minnesota',     'Golden Gophers','Big Ten', ['Gophers']),
    ('Nebraska',       'Nebraska Cornhuskers',        'Nebraska',      'Cornhuskers',   'Big Ten', ['Huskers']),
    ('Northwestern',   'Northwestern Wildcats',       'Northwestern',  'Wildcats',      'Big Ten', []),
    ('Ohio State',     'Ohio State Buckeyes',         'Ohio State',    'Buckeyes',      'Big Ten', ['OSU']),
    ('Oregon',         'Oregon Ducks',                'Oregon',        'Ducks',         'Big Ten', []),
    ('Penn State',     'Penn State Nittany Lions',    'Penn State',    'Nittany Lions', 'Big Ten', ['PSU']),
    ('Purdue',         'Purdue Boilermakers',         'Purdue',        'Boilermakers',  'Big Ten', []),
    ('Rutgers',        'Rutgers Scarlet Knights',     'Rutgers',       'Scarlet Knights','Big Ten', []),
    ('UCLA',           'UCLA Bruins',                 'UCLA',          'Bruins',        'Big Ten', []),
    ('USC',            'USC Trojans',                 'USC',           'Trojans',       'Big Ten', ['Southern California','Southern Cal']),
    ('Washington',     'Washington Huskies',          'Washington',    'Huskies',       'Big Ten', ['UW']),
    ('Wisconsin',      'Wisconsin Badgers',           'Wisconsin',     'Badgers',       'Big Ten', []),

    # Big 12 (16 teams as of 2024)
    ('Arizona',        'Arizona Wildcats',            'Arizona',       'Wildcats',      'Big 12', []),
    ('Arizona State',  'Arizona State Sun Devils',    'Arizona State', 'Sun Devils',    'Big 12', ['ASU']),
    ('Baylor',         'Baylor Bears',                'Baylor',        'Bears',         'Big 12', []),
    ('BYU',            'BYU Cougars',                 'BYU',           'Cougars',       'Big 12', ['Brigham Young']),
    ('Cincinnati',     'Cincinnati Bearcats',         'Cincinnati',    'Bearcats',      'Big 12', []),
    ('Colorado',       'Colorado Buffaloes',          'Colorado',      'Buffaloes',     'Big 12', ['Buffs']),
    ('Houston',        'Houston Cougars',             'Houston',       'Cougars',       'Big 12', []),
    ('Iowa State',     'Iowa State Cyclones',         'Iowa State',    'Cyclones',      'Big 12', ['ISU']),
    ('Kansas',         'Kansas Jayhawks',             'Kansas',        'Jayhawks',      'Big 12', ['KU']),
    ('Kansas State',   'Kansas State Wildcats',       'Kansas State',  'Wildcats',      'Big 12', ['K-State']),
    ('Oklahoma State', 'Oklahoma State Cowboys',      'Oklahoma State','Cowboys',       'Big 12', ['OK State']),
    ('TCU',            'TCU Horned Frogs',            'TCU',           'Horned Frogs',  'Big 12', ['Texas Christian']),
    ('Texas Tech',     'Texas Tech Red Raiders',      'Texas Tech',    'Red Raiders',   'Big 12', ['TTU']),
    ('UCF',            'UCF Knights',                 'UCF',           'Knights',       'Big 12', ['Central Florida']),
    ('Utah',           'Utah Utes',                   'Utah',          'Utes',          'Big 12', []),
    ('West Virginia',  'West Virginia Mountaineers',  'West Virginia', 'Mountaineers',  'Big 12', ['WVU']),

    # ACC (17 teams as of 2024)
    ('Boston College', 'Boston College Eagles',       'Boston College','Eagles',        'ACC', ['BC']),
    ('California',     'California Golden Bears',     'California',    'Golden Bears',  'ACC', ['Cal']),
    ('Clemson',        'Clemson Tigers',              'Clemson',       'Tigers',        'ACC', []),
    ('Duke',           'Duke Blue Devils',            'Duke',          'Blue Devils',   'ACC', []),
    ('Florida State',  'Florida State Seminoles',     'Florida State', 'Seminoles',     'ACC', ['FSU','Noles']),
    ('Georgia Tech',   'Georgia Tech Yellow Jackets', 'Georgia Tech',  'Yellow Jackets','ACC', ['GT']),
    ('Louisville',     'Louisville Cardinals',        'Louisville',    'Cardinals',     'ACC', []),
    ('Miami',          'Miami Hurricanes',            'Miami',         'Hurricanes',    'ACC', ['Miami (FL)','The U']),
    ('NC State',       'NC State Wolfpack',           'NC State',      'Wolfpack',      'ACC', ['North Carolina State']),
    ('North Carolina', 'North Carolina Tar Heels',    'North Carolina','Tar Heels',     'ACC', ['UNC']),
    ('Notre Dame',     'Notre Dame Fighting Irish',   'Notre Dame',    'Fighting Irish','FBS Independents', ['ND']),  # Independent but often grouped with ACC scheduling
    ('Pittsburgh',     'Pittsburgh Panthers',         'Pittsburgh',    'Panthers',      'ACC', ['Pitt']),
    ('SMU',            'SMU Mustangs',                'SMU',           'Mustangs',      'ACC', ['Southern Methodist']),
    ('Stanford',       'Stanford Cardinal',           'Stanford',      'Cardinal',      'ACC', []),
    ('Syracuse',       'Syracuse Orange',             'Syracuse',      'Orange',        'ACC', []),
    ('Virginia',       'Virginia Cavaliers',          'Virginia',      'Cavaliers',     'ACC', ['UVA']),
    ('Virginia Tech',  'Virginia Tech Hokies',        'Virginia Tech', 'Hokies',        'ACC', ['VT']),
    ('Wake Forest',    'Wake Forest Demon Deacons',   'Wake Forest',   'Demon Deacons', 'ACC', []),

    # Pac-12 (2 teams remaining: Oregon State, Washington State)
    ('Oregon State',   'Oregon State Beavers',        'Oregon State',  'Beavers',       'Pac-12', ['OSU']),
    ('Washington State','Washington State Cougars',   'Washington State','Cougars',     'Pac-12', ['WSU']),

    # American (AAC) 14 teams
    ('Army',           'Army Black Knights',          'Army',          'Black Knights', 'American', []),
    ('Charlotte',      'Charlotte 49ers',             'Charlotte',     '49ers',         'American', []),
    ('East Carolina',  'East Carolina Pirates',       'East Carolina', 'Pirates',       'American', ['ECU']),
    ('Florida Atlantic','Florida Atlantic Owls',      'Florida Atlantic','Owls',        'American', ['FAU']),
    ('Memphis',        'Memphis Tigers',              'Memphis',       'Tigers',        'American', []),
    ('Navy',           'Navy Midshipmen',             'Navy',          'Midshipmen',    'American', []),
    ('North Texas',    'North Texas Mean Green',      'North Texas',   'Mean Green',    'American', ['UNT']),
    ('Rice',           'Rice Owls',                   'Rice',          'Owls',          'American', []),
    ('South Florida',  'South Florida Bulls',         'South Florida', 'Bulls',         'American', ['USF']),
    ('Temple',         'Temple Owls',                 'Temple',        'Owls',          'American', []),
    ('Tulane',         'Tulane Green Wave',           'Tulane',        'Green Wave',    'American', []),
    ('Tulsa',          'Tulsa Golden Hurricane',      'Tulsa',         'Golden Hurricane','American', []),
    ('UAB',            'UAB Blazers',                 'UAB',           'Blazers',       'American', ['Alabama-Birmingham']),
    ('UTSA',           'UTSA Roadrunners',            'UTSA',          'Roadrunners',   'American', ['Texas-San Antonio']),

    # Mountain West 12 teams
    ('Air Force',      'Air Force Falcons',           'Air Force',     'Falcons',       'Mountain West', []),
    ('Boise State',    'Boise State Broncos',         'Boise State',   'Broncos',       'Mountain West', ['BSU']),
    ('Colorado State', 'Colorado State Rams',         'Colorado State','Rams',          'Mountain West', ['CSU']),
    ('Fresno State',   'Fresno State Bulldogs',       'Fresno State',  'Bulldogs',      'Mountain West', []),
    ('Hawaii',         'Hawaii Rainbow Warriors',     'Hawaii',        'Rainbow Warriors','Mountain West', ['UH']),
    ('Nevada',         'Nevada Wolf Pack',            'Nevada',        'Wolf Pack',     'Mountain West', []),
    ('New Mexico',     'New Mexico Lobos',            'New Mexico',    'Lobos',         'Mountain West', ['UNM']),
    ('San Diego State','San Diego State Aztecs',      'San Diego State','Aztecs',       'Mountain West', ['SDSU']),
    ('San Jose State', 'San Jose State Spartans',     'San Jose State','Spartans',      'Mountain West', ['SJSU']),
    ('UNLV',           'UNLV Rebels',                 'UNLV',          'Rebels',        'Mountain West', ['Nevada-Las Vegas']),
    ('Utah State',     'Utah State Aggies',           'Utah State',    'Aggies',        'Mountain West', ['USU']),
    ('Wyoming',        'Wyoming Cowboys',             'Wyoming',       'Cowboys',       'Mountain West', []),

    # MAC 12 teams
    ('Akron',          'Akron Zips',                  'Akron',         'Zips',          'MAC', []),
    ('Ball State',     'Ball State Cardinals',        'Ball State',    'Cardinals',     'MAC', []),
    ('Bowling Green',  'Bowling Green Falcons',       'Bowling Green', 'Falcons',       'MAC', []),
    ('Buffalo',        'Buffalo Bulls',               'Buffalo',       'Bulls',         'MAC', []),
    ('Central Michigan','Central Michigan Chippewas', 'Central Michigan','Chippewas',   'MAC', ['CMU']),
    ('Eastern Michigan','Eastern Michigan Eagles',    'Eastern Michigan','Eagles',      'MAC', ['EMU']),
    ('Kent State',     'Kent State Golden Flashes',   'Kent State',    'Golden Flashes','MAC', []),
    ('Miami (OH)',     'Miami (OH) RedHawks',         'Miami (OH)',    'RedHawks',      'MAC', ['Miami Ohio']),
    ('Northern Illinois','Northern Illinois Huskies', 'Northern Illinois','Huskies',    'MAC', ['NIU']),
    ('Ohio',           'Ohio Bobcats',                'Ohio',          'Bobcats',       'MAC', []),
    ('Toledo',         'Toledo Rockets',              'Toledo',        'Rockets',       'MAC', []),
    ('Western Michigan','Western Michigan Broncos',   'Western Michigan','Broncos',     'MAC', ['WMU']),

    # Sun Belt 14 teams
    ('Appalachian State','Appalachian State Mountaineers','Appalachian State','Mountaineers','Sun Belt', ['App State']),
    ('Arkansas State', 'Arkansas State Red Wolves',   'Arkansas State','Red Wolves',    'Sun Belt', ['A-State']),
    ('Coastal Carolina','Coastal Carolina Chanticleers','Coastal Carolina','Chanticleers','Sun Belt', ['CCU']),
    ('Georgia Southern','Georgia Southern Eagles',    'Georgia Southern','Eagles',      'Sun Belt', []),
    ('Georgia State',  'Georgia State Panthers',      'Georgia State', 'Panthers',      'Sun Belt', ['GSU']),
    ('James Madison',  'James Madison Dukes',         'James Madison', 'Dukes',         'Sun Belt', ['JMU']),
    ('Louisiana',      'Louisiana Ragin\' Cajuns',    'Louisiana',     "Ragin' Cajuns", 'Sun Belt', ['UL Lafayette','ULL']),
    ('Louisiana Monroe','Louisiana Monroe Warhawks',  'Louisiana Monroe','Warhawks',    'Sun Belt', ['ULM']),
    ('Marshall',       'Marshall Thundering Herd',    'Marshall',      'Thundering Herd','Sun Belt', []),
    ('Old Dominion',   'Old Dominion Monarchs',       'Old Dominion',  'Monarchs',      'Sun Belt', ['ODU']),
    ('South Alabama',  'South Alabama Jaguars',       'South Alabama', 'Jaguars',       'Sun Belt', ['USA']),
    ('Southern Miss',  'Southern Miss Golden Eagles', 'Southern Miss', 'Golden Eagles', 'Sun Belt', ['USM']),
    ('Texas State',    'Texas State Bobcats',         'Texas State',   'Bobcats',       'Sun Belt', []),
    ('Troy',           'Troy Trojans',                'Troy',          'Trojans',       'Sun Belt', []),

    # Conference USA 10 teams
    ('FIU',            'FIU Panthers',                'FIU',           'Panthers',      'Conference USA', ['Florida International']),
    ('Jacksonville State','Jacksonville State Gamecocks','Jacksonville State','Gamecocks','Conference USA', ['Jax State']),
    ('Kennesaw State', 'Kennesaw State Owls',         'Kennesaw State','Owls',          'Conference USA', []),
    ('Liberty',        'Liberty Flames',              'Liberty',       'Flames',        'Conference USA', []),
    ('Louisiana Tech', 'Louisiana Tech Bulldogs',     'Louisiana Tech','Bulldogs',      'Conference USA', ['LA Tech']),
    ('Middle Tennessee','Middle Tennessee Blue Raiders','Middle Tennessee','Blue Raiders','Conference USA', ['MTSU']),
    ('New Mexico State','New Mexico State Aggies',    'New Mexico State','Aggies',      'Conference USA', ['NMSU']),
    ('Sam Houston',    'Sam Houston Bearkats',        'Sam Houston',   'Bearkats',      'Conference USA', []),
    ('UTEP',           'UTEP Miners',                 'UTEP',          'Miners',        'Conference USA', ['Texas-El Paso']),
    ('Western Kentucky','Western Kentucky Hilltoppers','Western Kentucky','Hilltoppers','Conference USA', ['WKU']),

    # Independents (Notre Dame handled above with ACC; others)
    ('Connecticut',    'UConn Huskies',               'Connecticut',   'Huskies',       'FBS Independents', ['UConn']),
    ('UMass',          'UMass Minutemen',             'UMass',         'Minutemen',     'FBS Independents', ['Massachusetts']),
]


def seed() -> int:
    rows = []
    for canon, full_name, loc, nick, conf, alts in TEAMS:
        rows.append({
            'canonical_name': canon,
            'full_name': full_name,
            'location': loc,
            'nickname': nick,
            'conference': conf,
            'division': 'FBS',
            'classification': 'FBS',
            'alt_names': alts,
        })
    r = requests.post(
        f'{SB}/rest/v1/ncaaf_team_aliases?on_conflict=canonical_name',
        headers=H, json=rows, timeout=30,
    )
    if r.status_code in (200, 201, 204):
        return len(rows)
    print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
    return 0


def main():
    print(f'=== NCAAF alias seed ({len(TEAMS)} teams) ===')
    n = seed()
    print(f'  ✅ Seeded {n} rows')


if __name__ == '__main__':
    main()
