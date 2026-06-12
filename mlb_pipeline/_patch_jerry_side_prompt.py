"""Patch the MLB game_read_rules template to add SIDE RESOLVER section.
Run once 2026-06-12; safe to re-run (no-op on second call)."""
import sys, os, requests
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

url = os.environ.get('SUPABASE_URL'); key = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey':key,'Authorization':f'Bearer {key}','Content-Type':'application/json','Prefer':'return=minimal'}

r = requests.get(f'{url}/rest/v1/prompt_templates', headers=HEADERS,
    params={'select':'template','id':'eq.35801067-3cb5-4ee4-a2bd-80698898f89c'})
current = r.json()[0]['template']

SIDE_ADDITION = """


SIDE RESOLVER LANDING CALL (added 2026-06-12 — supplements the total resolver):
The struct ALSO contains a `resolver_side` block computed by signal_resolver.resolve_side(). It aggregates v3/v4/jerry SPREAD models + ML/RL cohort engine counts + confluence + prop reverse SIDE signal into ONE landing call for the GAME SIDE (which team covers/wins):

  resolver_side.direction: 'HOME' | 'AWAY' | None
  resolver_side.tier:      'ELITE' | 'STRONG' | 'LEAN' | 'LIGHT' | 'SKIP'
  resolver_side.team:      actual team name (e.g. 'Baltimore Orioles')
  resolver_side.ml_odds:   ML price for that team
  resolver_side.reason:    one-sentence explanation

SURFACE BOTH PICKS WHEN BOTH FIRE (NON-NEGOTIABLE):
If BOTH `resolver.tier` AND `resolver_side.tier` are in ('STRONG', 'ELITE'), The Play section MUST cite BOTH plays. Format example: "Two plays here — the model's total lands at OVER 9.5 (ELITE) and the side resolver lands on Baltimore ML (STRONG, -131) with all model spread votes and ML cohort net +9.8 above baseline. Both edges are real and independent."

DO NOT pick one and drop the other. The user gets BOTH lines on their card. Failing to surface a STRONG side play when the total play is the headline created public-vs-app contradictions on 6/12 (BAL ML posted publicly, Jerry only mentioned Over total) — never again.

SIDE-ONLY framing rules:
- ELITE / STRONG side: Cite the team + ML odds in The Play. Use resolver_side.reason verbatim.
- LEAN side: Cite as "the model also leans [team] ML" — secondary mention, not co-equal.
- LIGHT side: Mention only if total resolver is SKIP, never alongside a STRONG total.
- SKIP side: Do NOT mention. The play is the total.

The Where the Model Sits section should reference BOTH resolvers when both are non-SKIP. Format: "Total resolver lands OVER 9.5 (ELITE) ... Side resolver lands on Baltimore ML (STRONG) ..."
"""

anchor = "PROP REVERSE CITATION (added 2026-06-10):"
if anchor not in current:
    print('ANCHOR NOT FOUND - aborting')
    sys.exit(1)
if 'SIDE RESOLVER LANDING CALL' in current:
    print('Already patched - skipping')
    sys.exit(0)

new_template = current.replace(anchor, SIDE_ADDITION + '\n' + anchor)

r2 = requests.patch(
    f'{url}/rest/v1/prompt_templates',
    params={'id':'eq.35801067-3cb5-4ee4-a2bd-80698898f89c'},
    headers=HEADERS,
    json={'template': new_template, 'version': 7,
          'notes': '6/12 added SIDE RESOLVER LANDING CALL section. Fixes BAL ML public-vs-app contradiction.'},
    timeout=15,
)
print(f'Patch status: {r2.status_code}')
print(f'New length: {len(new_template)} chars (was {len(current)})')
