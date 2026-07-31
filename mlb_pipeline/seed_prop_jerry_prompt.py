"""Seed the prop_jerry_synthesis prompt template — sport-agnostic.

Voice: same analytical Jerry as game synthesis. Produces a 40-60w take
per prop plus a parseable BACK/FADE/PASS + conviction. Sport-agnostic
prompt (no MLB-specific language) so NFL/NBA/UFC props can reuse the
same template as their pipelines land.
"""
import os, sys
from pathlib import Path
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

TEMPLATE = r"""You are Jerry — analytical prop synthesizer for The Sweat Locker. Read every signal we captured for this prop, weigh market implied probability against our refit model, and deliver ONE actionable take.

Voice: direct analyst. No "smash", "lock", "must play". Cite specific numbers from the struct.

Prop:
  Player: {PLAYER}
  Market: {PROP_TYPE} {DIRECTION} {PROP_LINE}
  Book odds: {BOOK_ODDS}  (implied prob: {IMPLIED_PROB})
  Model refit conviction: {REFIT_CONVICTION}/100
  Legacy hand-tune conviction: {CONVICTION}/100

Signals fired at pick time:
{SIGNALS}

Return EXACTLY this format:

---TAKE---
<40-60 words. Lead with BACK / FADE / PASS + one-line reason. Follow with 2-3 short sentences citing specific numbers from signals. Reference the market vs model gap if meaningful (e.g. "market says 58%, we see 71% — 13pp edge"). Never invent stats not in the struct. End with the flip trigger.>

---CALL---
VERDICT: <BACK | FADE | PASS>
CONVICTION: <integer 0-100. Use refit_conviction as anchor, adjust up/down based on: (a) market alignment, (b) signal quality, (c) prop-type historical performance. If refit is under 50 → CALL PASS.>
"""


def upsert(name, sport, template):
    r = requests.get(f'{SB}/rest/v1/prompt_templates',
                     headers=H_READ,
                     params={'name': f'eq.{name}', 'sport': f'eq.{sport}', 'select': 'id'},
                     timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if rows:
        rr = requests.patch(f'{SB}/rest/v1/prompt_templates?id=eq.{rows[0]["id"]}',
                            headers=H_WRITE,
                            json={'template': template, 'is_active': True}, timeout=15)
        print(f'  patch id={rows[0]["id"]} {name}/{sport}: {rr.status_code}')
    else:
        rr = requests.post(f'{SB}/rest/v1/prompt_templates',
                           headers=H_WRITE,
                           json={'name': name, 'sport': sport, 'template': template, 'is_active': True},
                           timeout=15)
        print(f'  insert {name}/{sport}: {rr.status_code}')


if __name__ == '__main__':
    print('== Seeding prop_jerry_synthesis prompt (sport-agnostic) ==')
    upsert('prop_jerry_synthesis', 'ALL', TEMPLATE)
    print(f'  template length: {len(TEMPLATE)} chars')
