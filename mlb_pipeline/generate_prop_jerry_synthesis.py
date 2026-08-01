"""Prop Jerry synthesis (2026-07-31 · sport-universal).

Runs AFTER apply_prop_refit.py in the cron chain. For each unresolved
prop today (across all sports whose *_pipeline_props tables exist),
calls Claude with the sport-agnostic prop_jerry_synthesis/ALL prompt
and writes 40-60w take + BACK/FADE/PASS + conviction to prop_jerry_reads.

Sport registry:
    MLB → mlb_pipeline_props
Adding NBA/NFL/UFC = one line in PROPS_TABLE map + confirming the
prop-pipeline table schema matches (player_name, prop_type, direction,
signals, refit_conviction, book_over_odds, book_under_odds).

Usage:
    python generate_prop_jerry_synthesis.py [--force] [--date YYYY-MM-DD]
    python generate_prop_jerry_synthesis.py --sport MLB --limit 3
"""
import argparse, json, os, re, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

H_READ = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MODEL = 'claude-haiku-4-5-20251001'
PROMPT_VERSION = 'prop_synthesis_v1'

# Sport → table. Add sports as their prop pipelines ship.
PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    # 'NBA': 'nba_pipeline_props',
    # 'NFL': 'nfl_pipeline_props',
    # 'UFC': 'ufc_pipeline_props',
}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _implied_prob(odds):
    if odds is None: return None
    try: o = int(odds)
    except: return None
    return 100.0 / (o + 100) if o >= 0 else -o / (-o + 100.0)


def load_prompt() -> str | None:
    r = requests.get(f'{SUPABASE_URL}/rest/v1/prompt_templates',
                     headers=H_READ,
                     params={'name': 'eq.prop_jerry_synthesis', 'sport': 'eq.ALL',
                             'is_active': 'eq.true', 'select': 'template'},
                     timeout=15)
    rows = r.json() if r.status_code == 200 else []
    return rows[0]['template'] if rows else None


def render_prompt(template: str, prop: dict, sport: str) -> str:
    odds = prop.get('book_over_odds') if prop.get('direction') == 'over' else prop.get('book_under_odds')
    implied = _implied_prob(odds)
    sigs = prop.get('signals') or {}
    sig_lines = '\n'.join(f'  - {k}: {v}' for k, v in sigs.items() if not k.startswith('_'))[:2000]
    return (template
        .replace('{PLAYER}', prop.get('player_name') or '?')
        .replace('{PROP_TYPE}', prop.get('prop_type') or '?')
        .replace('{DIRECTION}', prop.get('direction') or '?')
        .replace('{PROP_LINE}', str(prop.get('prop_line') or '?'))
        .replace('{BOOK_ODDS}', str(odds or 'n/a'))
        .replace('{IMPLIED_PROB}', f'{implied:.1%}' if implied else 'n/a')
        .replace('{REFIT_CONVICTION}', str(prop.get('refit_conviction') or 'n/a'))
        .replace('{CONVICTION}', str(prop.get('conviction') or '?'))
        .replace('{SIGNALS}', sig_lines or '(no signals recorded)'))


def call_claude(prompt: str) -> str | None:
    if not ANTHROPIC_API_KEY:
        print('  ⚠ ANTHROPIC_API_KEY missing'); return None
    try:
        r = requests.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key': ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01',
                     'content-type': 'application/json'},
            json={'model': MODEL, 'max_tokens': 500,
                  'messages': [{'role': 'user', 'content': prompt}]},
            timeout=45)
        if r.status_code != 200:
            print(f'  ⚠ claude {r.status_code}: {r.text[:200]}'); return None
        return r.json()['content'][0]['text']
    except Exception as e:
        print(f'  ⚠ claude call failed: {e}'); return None


# Prop types with NO refit calibration data — legacy scorer runs uncalibrated
# so Jerry conviction over-extends on them. 7/31 audit: ha props 5-14 (26.3%)
# with high-conv BACKs the dominant loss driver. Cap conviction until refit
# weights include these prop types. Remove from set as each gets calibrated.
UNCALIBRATED_PROP_TYPES = {'ha', 'ha_over', 'ha_under'}
UNCALIBRATED_CONVICTION_CAP = 60   # LEAN tier ceiling

# Sign-flip audit (7/31): bb_under refit weights have negative coefficients on
# clean_start (-0.254), book_recalibration (-0.134), aggressive_opp (-0.303).
# All three should be POSITIVE. Small n=75 training artifact. Result: high-conv
# BACKs (Griffin 94 · Suarez 82) both busted. Cap BACK conviction on prop types
# with known sign-flip issues until refit v2 trains on ≥200 rows per type.
SIGN_FLIP_SUSPECT_TYPES = {'bb_under', 'bb'}
SIGN_FLIP_BACK_CAP = 80   # STRONG max, never PRIME

# LLM overconfidence guard: Jerry's synthesis prompt naturally amplifies BACK
# convictions above the legacy/refit score because clean narratives read as
# stronger than they are. Global BACK cap at 85 = anything above needs the
# refit v2 to explicitly support it. FADEs unaffected — they historically
# beat BACKs 54.7% vs 43.4% on 7/31.
GLOBAL_BACK_CAP = 85


def parse_synthesis(raw: str, prop_type: str | None = None) -> dict:
    def _sec(name):
        m = re.search(rf'---{name}---\s*(.*?)(?=---[A-Z]+---|$)', raw, re.S)
        return m.group(1).strip() if m else None
    take = _sec('TAKE') or ''
    call = _sec('CALL') or ''
    # Parser hardening: Claude sometimes puts VERDICT: / CONVICTION: inside the
    # TAKE block instead of CALL (~1 in 5 today's slate). Search CALL first (canonical),
    # then fall back to the entire raw output so we never lose an actionable verdict.
    verdict_src = call if 'VERDICT' in call else raw
    verdict_m = re.search(r'VERDICT\s*:\s*(\w+)', verdict_src)
    verdict = verdict_m.group(1).upper() if verdict_m else None
    conv_src = call if 'CONVICTION' in call else raw
    conv_m = re.search(r'CONVICTION\s*:\s*(\d+)', conv_src)
    conv = max(0, min(100, int(conv_m.group(1)))) if conv_m else None
    # Uncalibrated prop cap — see UNCALIBRATED_PROP_TYPES comment above.
    if conv is not None and prop_type in UNCALIBRATED_PROP_TYPES:
        conv = min(conv, UNCALIBRATED_CONVICTION_CAP)
    # Sign-flip cap (BACKs only — FADEs unaffected)
    if conv is not None and verdict == 'BACK':
        if prop_type in SIGN_FLIP_SUSPECT_TYPES:
            conv = min(conv, SIGN_FLIP_BACK_CAP)
        conv = min(conv, GLOBAL_BACK_CAP)
    # If TAKE section missing, salvage from raw: use everything before first --- marker
    if not take:
        pre = re.split(r'---[A-Z]+---', raw, maxsplit=1)[0].strip()
        take = pre if len(pre) >= 20 else raw.strip()[:400]
    return {'short_read': take, 'call_verdict': verdict, 'conviction': conv}


def upsert_read(sport: str, prop: dict, parsed: dict, prompt: str, game_date: str) -> bool:
    payload = {
        'sport': sport,
        'game_id': prop.get('game_id'),
        'game_date': game_date,
        'player_name': prop.get('player_name'),
        'prop_type': prop.get('prop_type'),
        'direction': prop.get('direction'),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'prompt_version': PROMPT_VERSION,
        'prop_line': prop.get('prop_line'),
        'book_odds': prop.get('book_over_odds') if prop.get('direction') == 'over' else prop.get('book_under_odds'),
        'refit_conviction': prop.get('refit_conviction'),
        'input_snapshot': {'signals': prop.get('signals'), 'conviction': prop.get('conviction')},
        **parsed,
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/prop_jerry_reads?on_conflict=sport,game_id,player_name,prop_type,direction,game_date',
        headers=H_WRITE, json=payload, timeout=20,
    )
    if r.status_code in (200, 201, 204):
        return True
    print(f'  ⚠ upsert {r.status_code}: {r.text[:200]}')
    return False


def run_for_sport(sport: str, game_date: str, template: str, force: bool = False, limit: int | None = None):
    table = PROPS_TABLE.get(sport)
    if not table:
        print(f'  [{sport}] no props table registered — skip'); return 0
    # Fetch today's props for this sport
    r = requests.get(f'{SUPABASE_URL}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': 'game_id,player_name,prop_type,direction,prop_line,'
                                       'signals,conviction,refit_conviction,book_over_odds,book_under_odds,tier',
                             'limit': 300},
                     timeout=30)
    props = r.json() if r.status_code == 200 else []
    # Skip SKIP-tier props (Phase-2 attach failed)
    props = [p for p in props if p.get('tier') != 'SKIP']

    # Edge gate: COVERAGE stubs (from sweep_prop_coverage) only pass if they
    # carry a meaningful projection delta or opp K% extreme. Non-COVERAGE tiers
    # already earned Jerry's attention via legacy scorer. Keeps Jerry-take
    # volume manageable (~30–60/day) even when coverage stubs push the raw
    # props table to 250+ rows.
    def _has_edge(p: dict) -> bool:
        if (p.get('tier') or '').upper() != 'COVERAGE':
            return True
        sig = p.get('signals') or {}
        edge = sig.get('_edge_pct')
        if edge is not None:
            try:
                if abs(float(edge)) >= 0.10: return True   # projection ≥10% off line
            except (TypeError, ValueError): pass
        # opp K% extremes as fallback signal — high-K opponent → K under edge, etc.
        opp_k = sig.get('opp_k_rate')
        if isinstance(opp_k, str) and any(c.isdigit() for c in opp_k):
            try:
                num = float(''.join(c for c in opp_k if c.isdigit() or c == '.'))
                if p.get('prop_type') == 'ks' and (num >= 26 or num <= 19): return True
            except ValueError: pass
        return False

    before = len(props)
    props = [p for p in props if _has_edge(p)]
    print(f'  [{sport}] {len(props)}/{before} eligible after edge gate')

    done = 0
    for prop in props:
        if limit and done >= limit: break
        if not force:
            check = requests.get(f'{SUPABASE_URL}/rest/v1/prop_jerry_reads',
                headers=H_READ, params={
                    'sport': f'eq.{sport}', 'game_id': f'eq.{prop["game_id"]}',
                    'player_name': f'eq.{prop["player_name"]}',
                    'prop_type': f'eq.{prop["prop_type"]}',
                    'direction': f'eq.{prop["direction"]}',
                    'game_date': f'eq.{game_date}', 'select': 'id',
                }, timeout=10)
            if check.status_code == 200 and check.json():
                continue
        prompt = render_prompt(template, prop, sport)
        raw = call_claude(prompt)
        if not raw: continue
        parsed = parse_synthesis(raw, prop.get('prop_type'))
        if not parsed.get('short_read'):
            print(f'  ⚠ parse missing take for {prop["player_name"]}'); continue
        if upsert_read(sport, prop, parsed, prompt, game_date):
            verdict = parsed.get('call_verdict') or '?'; conv = parsed.get('conviction') or '?'
            print(f'  ✓ {prop["player_name"][:20]:<20} {prop["prop_type"]:<12} {prop["direction"]:<5} → {verdict} {conv}')
            done += 1
    return done


def main(force: bool = False, sport: str | None = None,
         game_date: str | None = None, limit: int | None = None):
    gd = game_date or today_et()
    print(f'=== generate_prop_jerry_synthesis · {gd} ===')
    template = load_prompt()
    if not template:
        print('  ⛔ no prop_jerry_synthesis prompt — run seed_prop_jerry_prompt.py first'); return
    sports = [sport] if sport else list(PROPS_TABLE.keys())
    total = 0
    for s in sports:
        total += run_for_sport(s, gd, template, force=force, limit=limit)
    print(f'\n=== wrote {total} prop_jerry_reads ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--force', action='store_true')
    p.add_argument('--sport', help='MLB only for now; others when their pipelines ship')
    p.add_argument('--date')
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    main(force=args.force, sport=args.sport, game_date=args.date, limit=args.limit)
