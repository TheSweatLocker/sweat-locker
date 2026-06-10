"""One-shot: append resolver-first rules to MLB game_read_rules → v6."""
from dotenv import load_dotenv
load_dotenv()
import os, requests, sys
sys.stdout.reconfigure(encoding='utf-8')

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': key,
    'Authorization': f'Bearer {key}',
    'Content-Type': 'application/json',
    'Prefer': 'return=representation',
}

r = requests.get(
    f'{url}/rest/v1/prompt_templates',
    headers={'apikey': key, 'Authorization': f'Bearer {key}'},
    params=[('select', '*'), ('name', 'eq.game_read_rules'), ('sport', 'eq.MLB')],
)
row = r.json()[0]
old = row['template']

addendum = '''

RESOLVER LANDING CALL (added 2026-06-10 — supersedes raw signal listing in Where the Model Sits):
The struct now contains a `resolver` block computed by signal_resolver.resolve_total(). It aggregates v3+v4+jerry models, cohort engine net STRONG_EDGE+ count, and prop reverse signal into ONE landing call:

  resolver.direction: 'OVER' | 'UNDER' | None
  resolver.tier:      'ELITE' | 'STRONG' | 'LEAN' | 'LIGHT' | 'SKIP'
  resolver.reason:    one-sentence explanation
  resolver.dissent:   signals that disagree with the landing call
  resolver.signals:   raw per-source classifications

LEAD with this in `Where the Model Sits`. Do NOT list raw model votes + cohort counts + prop signals as a wall to the user. Cite the resolver's landing call and its reason directly.

Tier-aware framing rules (NON-NEGOTIABLE):

- ELITE / STRONG → Cite as the play. Reference the resolver reason verbatim. Example: "All three models, the cohort engine (+12 net STRONG_EDGE), and the prop pipeline all point OVER 8.5. That's an aligned read — math models and lineup-level prop signals agree."

- LEAN → Cite as a moderate edge. Acknowledge if any signal dissents. Example: "Model majority leans UNDER, cohort engine confirms with +6 net. A lean, not a hammer."

- LIGHT → CITE WITH RESERVATION. The play exists but only ONE signal class supports it. Example: "Cohort engine alone points UNDER while models are split. Worth a flag, not a stake."

- SKIP → DO NOT pitch a directional play in The Play section. Frame as: "Signals contradict — models say UNDER but cohort engine says OVER with an 11-cohort gap. No clean read here." Then close with: "Pass." or "We're sitting this one out."

DISSENT HANDLING:
- If resolver.dissent is non-empty, mention the dissenting signal in ONE clause max, not as a competing thesis. Example: "...even though Jerry alone projects 9.8 (the model's outlier)."
- Never write "but other signals disagree" without naming WHICH signal and WHY it doesn't override.
- Never list 3+ conflicting signal counts — that's the wall-of-signals failure the resolver was built to prevent.

OLDER RULES (still apply on top of the resolver):
- SPREAD ATTRIBUTION rules (added 6/9) — verify ML direction before writing "[Team] -X.X" or "[Team] +X.X"
- CONFIDENCE GATING (added 6/9) — "HIGH conviction" forbidden unless all 3 models agree directionally (now subsumed by ELITE/STRONG tier framing)
- COHORT BALANCE (added 6/9) — when citing a cohort that supports the lean, the resolver's dissent list already encodes the opposite-direction cohort count — use that, don't recompute

PROP REVERSE CITATION (added 2026-06-10):
When resolver.signals.props.direction matches the landing call AND strength is 'LOUD', cite it as one line: "Player-prop pipeline confirms — X PRIME props aligned [direction]." Don't list which props; just the count and direction.
'''

new_template = old + addendum
new_version = (row.get('version') or 0) + 1
patch = requests.patch(
    f'{url}/rest/v1/prompt_templates?id=eq.{row["id"]}',
    headers=HEADERS,
    json={'template': new_template, 'version': new_version},
)
print(f'PATCH status: {patch.status_code}')
if patch.status_code == 200:
    upd = patch.json()[0]
    print(f'Updated MLB game_read_rules to v{upd["version"]} (was v{row["version"]})')
    print(f'New length: {len(new_template)} (was {len(old)})')
else:
    print(f'  body: {patch.text[:300]}')
