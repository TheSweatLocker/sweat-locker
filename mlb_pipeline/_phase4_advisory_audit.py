"""Run the Phase 4 number-attribution validator over tonight's Jerry
narratives and count how many would be flagged in ENFORCE mode.

If false-positive rate is low (< ~5% of narratives flagged), we can
safely flip NUMBER_VALIDATOR_ENFORCE = True. If high, tune thresholds
or expand the proximity n-pattern allow-list first."""
import os, json, requests
from dotenv import load_dotenv
load_dotenv()
SU = os.environ.get('SUPABASE_URL'); SK = os.environ.get('SUPABASE_KEY')
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

from generate_mlb_game_reads import (
    _detect_number_hallucinations,
    _detect_cross_dim_leakage,
)

# Pull tonight's game reads
r = requests.get(f'{SU}/rest/v1/jerry_cache',
    params={'cache_key':'like.game_read_*_2026-06-18',
            'select':'cache_key,data,narrative','limit':30},
    headers=H, timeout=15)
rows = r.json() if isinstance(r.json(), list) else []
print(f'Auditing {len(rows)} game_read rows from 6/18')
print()

total_narratives = 0
phase4_flags = 0
phase4_total_violations = 0
phase5_flags = 0
phase5_total_violations = 0
sample_violations = []

for row in rows:
    nar = row.get('narrative') or ''
    if not nar:
        continue
    total_narratives += 1
    struct = row.get('data') or {}
    if isinstance(struct, str):
        try: struct = json.loads(struct)
        except Exception: struct = {}

    # Phase 4: number-attribution
    num_errors = _detect_number_hallucinations(nar, struct)
    if num_errors:
        phase4_flags += 1
        phase4_total_violations += len(num_errors)
        if len(sample_violations) < 5:
            sample_violations.append({
                'matchup': struct.get('matchup', '?')[:40],
                'phase': 'Phase 4 (number)',
                'errors': num_errors[:3],
            })

    # Phase 5: cross-dim leakage
    cd_errors = _detect_cross_dim_leakage(nar, struct)
    if cd_errors:
        phase5_flags += 1
        phase5_total_violations += len(cd_errors)
        if len(sample_violations) < 10:
            sample_violations.append({
                'matchup': struct.get('matchup', '?')[:40],
                'phase': 'Phase 5 (cross-dim)',
                'errors': cd_errors[:3],
            })

print(f'=== Phase 4 (number-attribution) ===')
print(f'  Narratives flagged: {phase4_flags} / {total_narratives}')
print(f'  Total violations:   {phase4_total_violations}')
if total_narratives:
    print(f'  Flag rate:          {100*phase4_flags/total_narratives:.0f}%')
print()
print(f'=== Phase 5 (cross-dim) ===')
print(f'  Narratives flagged: {phase5_flags} / {total_narratives}')
print(f'  Total violations:   {phase5_total_violations}')
if total_narratives:
    print(f'  Flag rate:          {100*phase5_flags/total_narratives:.0f}%')
print()
print(f'=== Sample violations ===')
for s in sample_violations:
    print(f'  {s["matchup"]}  [{s["phase"]}]')
    for e in s['errors']:
        print(f'    - {e}')
