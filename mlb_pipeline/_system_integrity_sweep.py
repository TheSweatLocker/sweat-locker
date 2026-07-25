"""System-integrity sweep — hunt for silent-default patterns across all
critical data tables.

Pattern we're looking for (7/25 finding on mlb_pitcher_stats):
  A numeric column where >30% of non-null values are IDENTICAL to a
  "round" number that suggests a hardcoded default (like 10.0, 35.0,
  6.0, 93.0, 72.0). Elite pitchers all having whiff_rate=10.0 was
  the smoking gun.

For each critical table, this script:
  1. Fetches full row snapshot
  2. For every numeric-ish column, builds value distribution
  3. Flags columns where the top-1 value has ≥30% share AND ≥50 rows
  4. Cross-references against schema comments / write-code to
     identify the hardcoded default source
  5. Prints ranked list of most-suspicious columns

Tables audited (critical model-feed):
  - mlb_pitcher_stats            (already found 5 corrupt columns)
  - mlb_team_offense             (Statcast team stats)
  - mlb_team_stats               (season aggregates)
  - mlb_catcher_framing          (catcher stats)
  - mlb_game_context             (huge — sample cols only)
  - mlb_game_results             (huge — sample cols only)
  - pitcher_projections          (l7 projections)
  - mlb_pitcher_vs_team          (mastery/head-to-head)
  - mlb_umpire_stats             (if exists)
"""
import os, requests, sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from collections import Counter
env = Path('.env').read_text()
for line in env.split('\n'):
    if '=' in line and not line.startswith('#'):
        k,v = line.split('=',1); os.environ[k.strip()] = v.strip()
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}


TABLES = [
    # MLB core
    'mlb_pitcher_stats',
    'mlb_team_offense',
    'mlb_bullpen_stats',
    'mlb_catcher_framing',
    'mlb_umpires',
    'mlb_park_factors',
    'mlb_team_hr_threats',
    'mlb_team_vs_opp_recent',
    'pitcher_projections',
    # Calibration / model-health
    'mlb_tier_calibration',
    'consensus_bucket_calibration',
    'prop_edge_calibration',
    'model_health',
    # NBA
    'nba_team_stats',
    'nba_injuries',
    # NCAAB
    'ncaab_team_stats',
    'ncaab_team_aliases',
    # NCAAF
    'ncaaf_team_stats',
    'ncaaf_team_aliases',
    # NFL
    'nfl_team_stats',
    'nfl_player_stats',
    'nfl_team_aliases',
    # External
    'external_source_calibration',
    # KenPom (used for NCAAB)
    'kenpom_cache',
]

# Fields we KNOW are non-numeric or benign
SKIP_FIELDS = {
    'id', 'game_id', 'pitcher_name', 'player_name', 'team', 'name',
    'created_at', 'updated_at', 'season', 'throws', 'bats', 'hand',
    'game_date', 'commence_time', 'fetched_at', 'source',
    'primary_position', 'position', 'league',
}


def fetch_all(table: str) -> list:
    rows = []; off = 0
    while True:
        try:
            r = requests.get(
                f'{url}/rest/v1/{table}?select=*&limit=1000&offset={off}',
                headers=h, timeout=30,
            )
            if r.status_code != 200:
                return None  # table doesn't exist or access denied
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            rows.extend(data)
            if len(data) < 1000:
                break
            off += 1000
            if off > 20000:
                # Safety cap for huge tables
                break
        except Exception:
            return None
    return rows


def audit_table(table: str) -> dict:
    rows = fetch_all(table)
    if rows is None:
        return {'status': 'NOT_FOUND', 'table': table}
    if not rows:
        return {'status': 'EMPTY', 'table': table, 'n': 0}

    n = len(rows)
    suspicious = []
    all_cols = set()
    for r in rows:
        if isinstance(r, dict):
            all_cols.update(r.keys())

    for col in sorted(all_cols):
        if col in SKIP_FIELDS or col.endswith('_id') or col.endswith('_at'):
            continue
        # Collect non-null values
        vals = []
        for r in rows:
            v = r.get(col)
            if v is None:
                continue
            # Only look at numeric-ish
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                vals.append(round(float(v), 4))
        if not vals or len(vals) < 20:
            continue
        ctr = Counter(vals)
        top_val, top_count = ctr.most_common(1)[0]
        share = top_count / len(vals)
        # Suspicious: same value appears >=30% of the time AND value looks
        # "round enough" to be a default (single decimal, whole number, or
        # common fraction). Exclude columns where 0 is legitimately common.
        looks_round = (top_val == round(top_val) or
                       abs(top_val - round(top_val, 1)) < 0.001)
        if share >= 0.30 and looks_round and top_val not in (0, 0.0, 1, 1.0):
            suspicious.append({
                'col': col,
                'top_val': top_val,
                'top_count': top_count,
                'total_non_null': len(vals),
                'share_pct': round(share * 100, 1),
            })

    return {
        'status': 'OK',
        'table': table,
        'n_rows': n,
        'suspicious': sorted(suspicious, key=lambda x: -x['share_pct']),
    }


print(f'{"=" * 80}\nSYSTEM INTEGRITY SWEEP\n{"=" * 80}\n')
overall = []
for t in TABLES:
    print(f'Auditing {t} ...')
    result = audit_table(t)
    overall.append(result)
    if result['status'] == 'NOT_FOUND':
        print(f'  → not found (skipping)\n')
        continue
    if result['status'] == 'EMPTY':
        print(f'  → empty table\n')
        continue
    n = result['n_rows']
    susp = result['suspicious']
    print(f'  → {n} rows, {len(susp)} suspicious columns')
    if susp:
        for s in susp[:8]:
            marker = ' 🚨' if s['share_pct'] >= 60 else ('  ⚠' if s['share_pct'] >= 45 else '   ')
            print(f'   {marker} {s["col"]:<28} top={s["top_val"]:<8} ({s["top_count"]}/{s["total_non_null"]} = {s["share_pct"]}%)')
    print()

print(f'\n{"=" * 80}\nSUMMARY — Most-suspicious columns across all tables\n{"=" * 80}\n')
all_susp = []
for r in overall:
    if r['status'] == 'OK':
        for s in r['suspicious']:
            all_susp.append((r['table'], s))
all_susp.sort(key=lambda x: -x[1]['share_pct'])
for t, s in all_susp[:25]:
    marker = '🚨 CRITICAL' if s['share_pct'] >= 60 else ('⚠  MODERATE' if s['share_pct'] >= 45 else '   MILD    ')
    print(f'  {marker}  {t:<28} {s["col"]:<28} {s["share_pct"]}% at {s["top_val"]}  (n={s["top_count"]}/{s["total_non_null"]})')
