"""UFC per-fight Jerry synthesis (2026-08-01 · Week 1 P1).

Parallels generate_jerry_synthesis.py (MLB game-level) but for UFC fights.
Reads ufc_picks (model prediction per fight), builds a fight struct,
sends to Claude, parses short/long/CALL, writes to jerry_reads with
sport='UFC'.

Fills the gap surfaced today: pipeline had Rakic 74% / Stirling 78%
model predictions but no Jerry voice reads. Users see raw probabilities
in the app but no analytical narrative.

Runs during fight week (Wed-Sat before card). Idempotent on rerun via
force flag.

Usage:
    python generate_ufc_fight_synthesis.py [--date YYYY-MM-DD] [--force]
"""
import argparse, os, re, sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-haiku-4-5-20251001"
PROMPT_VERSION = 'ufc_fight_v1'

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

H_READ = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def fetch_upcoming_fights(game_date: str | None = None):
    """Pull fights from ufc_picks for the specified date (or upcoming week)."""
    target = game_date or today_et()
    horizon = (datetime.strptime(target, '%Y-%m-%d') + timedelta(days=7)).strftime('%Y-%m-%d')
    params = {
        'event_date': f'gte.{target}',
        'select': 'id,event_name,event_date,fight_order,fighter_a,fighter_b,'
                  'p_winner_a,p_method_ko,p_method_sub,p_method_dec,p_distance,'
                  'p_round_1,p_round_2,p_round_3,p_round_4,p_round_5,'
                  'conviction_winner,tier_winner,recommended_side,'
                  'edge_method,edge_distance,odds_a_median,odds_b_median,'
                  'odds_a_best,odds_b_best,ev_recommended_side,ev_tier',
        'order': 'event_date.asc,fight_order.asc',
        'limit': '100',
    }
    # PostgREST doesn't support lte in same query directly with same key
    # so we cap horizon via or filter
    params['and'] = f'(event_date.gte.{target},event_date.lte.{horizon})'
    del params['event_date']
    r = requests.get(f'{SUPABASE_URL}/rest/v1/ufc_picks',
                     headers=H_READ, params=params, timeout=20)
    return r.json() if r.status_code == 200 else []


# Fields from ufc_fighter_stats that are RELIABLE for prompt use.
# Excludes wins_by_ko / wins_by_sub / wins_by_dec / finishing_rate —
# discovered 2026-08-08 that 94% of fighter rows have zero across all
# three finish-method columns (scraper populated total_wins but failed
# on method breakdown; UFC stats site now behind JS anti-scraper).
# Including these fields caused "Gamrot has no finishes in 25 fights"
# hallucination class. Solution: only pass fields that are actually
# populated correctly.
_RELIABLE_FIGHTER_FIELDS = [
    'record',       # e.g. "25-4-0"
    'height', 'weight', 'reach', 'stance',
    'slpm', 'str_acc', 'sapm', 'str_def',       # striking
    'td_avg', 'td_acc', 'td_def', 'sub_avg',    # grappling
]


def _fetch_fighter_stats_bulk(names: list) -> dict:
    """Bulk-fetch fighter_stats for a list of names.
    Returns {lowered_name: {reliable_field: value, ...}}."""
    if not names: return {}
    # PostgREST IN filter with quoted names
    in_list = ','.join(f'"{n}"' for n in names)
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/ufc_fighter_stats',
        headers=H_READ,
        params={'fighter_name': f'in.({in_list})',
                'select': 'fighter_name,' + ','.join(_RELIABLE_FIGHTER_FIELDS)},
        timeout=15,
    )
    if r.status_code != 200: return {}
    out = {}
    for row in r.json():
        name = (row.get('fighter_name') or '').strip()
        if not name: continue
        stats = {k: row.get(k) for k in _RELIABLE_FIGHTER_FIELDS
                 if row.get(k) is not None}
        out[name.lower()] = stats
    return out


def build_fight_struct(fight: dict, fighter_stats: dict | None = None) -> dict:
    """Pack fight data into a struct Claude can synthesize on.
    fighter_stats: {lowered_name: {reliable_field: value}} — enrichment
    from _fetch_fighter_stats_bulk. Optional; if None struct has no
    fighter-stat blocks (Claude will only have model probabilities)."""
    pick_side = (fight.get('recommended_side') or '').lower()   # 'a' or 'b'
    picked_fighter = fight.get('fighter_a') if pick_side == 'a' else fight.get('fighter_b')
    other_fighter = fight.get('fighter_b') if pick_side == 'a' else fight.get('fighter_a')
    p_a = fight.get('p_winner_a') or 0
    win_prob = p_a if pick_side == 'a' else (1 - p_a)
    picked_odds = fight.get('odds_a_median') if pick_side == 'a' else fight.get('odds_b_median')

    def pct(v): return round((v or 0) * 100, 1)

    struct = {
        'event': fight.get('event_name'),
        'date': fight.get('event_date'),
        'fight_order': fight.get('fight_order'),
        'fighter_a': fight.get('fighter_a'),
        'fighter_b': fight.get('fighter_b'),
        'model_pick_side': pick_side.upper(),
        'model_pick_fighter': picked_fighter,
        'opponent': other_fighter,
        'win_probability_pct': round(win_prob * 100, 1),
        'conviction_winner': fight.get('conviction_winner'),
        'tier_winner': fight.get('tier_winner'),
        'method_prob_ko': pct(fight.get('p_method_ko')),
        'method_prob_sub': pct(fight.get('p_method_sub')),
        'method_prob_dec': pct(fight.get('p_method_dec')),
        'round_distribution': {
            'r1': pct(fight.get('p_round_1')),
            'r2': pct(fight.get('p_round_2')),
            'r3': pct(fight.get('p_round_3')),
            'r4': pct(fight.get('p_round_4')),
            'r5': pct(fight.get('p_round_5')),
        },
        'distance_prob_pct': pct(fight.get('p_distance')),
        'odds_picked_side_median': picked_odds,
        'edge_method': fight.get('edge_method'),
        'edge_distance': fight.get('edge_distance'),
        'ev_recommended_side': fight.get('ev_recommended_side'),
        'ev_tier': fight.get('ev_tier'),
    }

    # 2026-08-08 enrichment: append reliable fighter stats.
    # Only fields verified populated + accurate (record, physicals, strike/grapple
    # rates). NEVER include finish-method breakdown — scraper is broken and
    # data is 0 for 94% of fighters (would create false narratives).
    if fighter_stats is not None:
        struct['fighter_a_stats'] = fighter_stats.get(
            (fight.get('fighter_a') or '').lower(), {})
        struct['fighter_b_stats'] = fighter_stats.get(
            (fight.get('fighter_b') or '').lower(), {})

    return struct


def _render_fighter_stats(name: str, stats: dict) -> str:
    """Render a fighter_stats block. Empty string if no stats."""
    if not stats: return f'  {name}: NO PROFILE DATA AVAILABLE — do not cite record/physicals/stats for this fighter.'
    parts = []
    if stats.get('record'): parts.append(f'record {stats["record"]}')
    if stats.get('total_wins') is not None: parts.append(f'{stats["total_wins"]}W-{stats.get("total_losses",0)}L')
    ko = stats.get('wins_by_ko'); sub = stats.get('wins_by_sub'); dec = stats.get('wins_by_dec')
    if ko is not None and sub is not None and dec is not None:
        parts.append(f'wins by KO/TKO {ko}, SUB {sub}, DEC {dec}')
    fr = stats.get('finishing_rate')
    if fr is not None: parts.append(f'finish rate {fr}%')
    if stats.get('height'): parts.append(f'height {stats["height"]}')
    if stats.get('reach'): parts.append(f'reach {stats["reach"]}')
    if stats.get('stance'): parts.append(f'stance {stats["stance"]}')
    if stats.get('slpm') is not None: parts.append(f'SLpM {stats["slpm"]}')
    if stats.get('str_acc') is not None: parts.append(f'strike acc {stats["str_acc"]}%')
    if stats.get('str_def') is not None: parts.append(f'strike def {stats["str_def"]}%')
    if stats.get('td_avg') is not None: parts.append(f'TD/15 {stats["td_avg"]}')
    if stats.get('td_def') is not None: parts.append(f'TD def {stats["td_def"]}%')
    if stats.get('sub_avg') is not None: parts.append(f'sub att/15 {stats["sub_avg"]}')
    return f'  {name}: {" · ".join(parts)}'


def render_prompt(struct: dict) -> str:
    """Fight-specific prompt. Guardrails against hallucination — Jerry must
    cite only fields in struct, never invent stats about the fighters.

    2026-08-08 hardening: Gamrot hallucination ("no finish in 25 fights"
    when real record is 26-4 with 14 finishes) traced to Claude filling
    in fight history from training data. Explicit instructions to ONLY
    cite fields present in the struct + fighter_stats blocks below.
    """
    fa_stats_block = _render_fighter_stats(struct['fighter_a'], struct.get('fighter_a_stats') or {})
    fb_stats_block = _render_fighter_stats(struct['fighter_b'], struct.get('fighter_b_stats') or {})
    return f"""You are Jerry — combat sports analyst for The Sweat Locker. Read the model output for this UFC fight and deliver ONE actionable synthesis.

═══════════════════════════════════════════════════════════════════════
HARD RULES (2026-08-08):
1. ONLY cite numbers that appear in THIS PROMPT. If it's not below, don't say it.
2. NEVER invent fighter records, finish counts, streaks, camp changes,
   weight-cut history, head-to-head results, or "he's never been finished".
3. If a fighter's PROFILE DATA is "NOT AVAILABLE" — do not describe them
   with any record-based facts. Stay on the MODEL numbers only.
4. Do NOT reference specific past fights, opponents, or dates from your
   general knowledge. Only what's in the struct.
═══════════════════════════════════════════════════════════════════════

Voice: direct analyst. No "lock", "smash", "must play".

Fight:
  Event: {struct['event']}
  {struct['fighter_a']} vs {struct['fighter_b']} (fight #{struct.get('fight_order','?')})

Fighter profiles (cite only what's here):
{fa_stats_block}
{fb_stats_block}

Model output:
  Recommended pick: {struct['model_pick_fighter']} at {struct['win_probability_pct']}% win probability
  Tier: {struct.get('tier_winner','?')} · Conviction {struct.get('conviction_winner','?')}
  Method distribution: KO {struct['method_prob_ko']}% · SUB {struct['method_prob_sub']}% · DEC {struct['method_prob_dec']}%
  Distance probability: {struct['distance_prob_pct']}%
  Round distribution: R1 {struct['round_distribution']['r1']}% · R2 {struct['round_distribution']['r2']}% · R3 {struct['round_distribution']['r3']}%
  Odds (picked side): {struct.get('odds_picked_side_median','n/a')}
  Edge angles: method={struct.get('edge_method','none')} · distance={struct.get('edge_distance','none')}
  EV tier: {struct.get('ev_tier','n/a')}

Return EXACTLY this format:

---SHORT---
<40-60 words. Lead with BACK / FADE / PASS + one-line reason. Cite the win probability, method dominance, and the strongest edge. Reference odds/EV if relevant. End with what would kill the pick (opponent adjustment, weight cut concern, etc — general MMA principles ok, no fabricated stats).>

---LONG---
<180-260 words. Deeper synthesis: pathway to victory (which method has the model behind it), how the round distribution supports the read, what the odds imply vs model probability, EV framing. Analytical voice — no hype. If EV tier or edge angles are populated, work them in. Do NOT invent fighter records, camp changes, weight cut history, or head-to-head results.>

---CALL---
VERDICT: <BACK | FADE | PASS>
CONVICTION: <integer 0-100. Anchor on conviction_winner but adjust for: (a) EV tier, (b) method dominance clarity (>60% in one method = high conviction), (c) tier_winner label. If model win_prob <55% → CALL PASS.>
CALL_TEXT: <human-readable e.g. "Rakic ML" or "Stirling by KO/TKO" or "Pass">
"""


def call_claude(prompt: str) -> str | None:
    if not ANTHROPIC_API_KEY:
        print('  ⚠ ANTHROPIC_API_KEY missing'); return None
    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={'x-api-key': ANTHROPIC_API_KEY,
                     'anthropic-version': '2023-06-01',
                     'content-type': 'application/json'},
            json={'model': MODEL, 'max_tokens': 1500,
                  'messages': [{'role': 'user', 'content': prompt}]},
            timeout=45)
        if r.status_code != 200:
            print(f'  ⚠ claude {r.status_code}: {r.text[:200]}'); return None
        return r.json()['content'][0]['text']
    except Exception as e:
        print(f'  ⚠ claude call failed: {e}'); return None


def parse_synthesis(raw: str) -> dict:
    def _sec(name):
        m = re.search(rf'---{name}---\s*(.*?)(?=---[A-Z]+---|$)', raw, re.S)
        return m.group(1).strip() if m else None
    short = _sec('SHORT') or ''
    long_ = _sec('LONG') or ''
    call = _sec('CALL') or ''
    # Parser hardening: fall back to raw if CALL block missing
    verdict_src = call if 'VERDICT' in call else raw
    conv_src = call if 'CONVICTION' in call else raw
    v_m = re.search(r'VERDICT\s*:\s*(\w+)', verdict_src)
    c_m = re.search(r'CONVICTION\s*:\s*(\d+)', conv_src)
    t_m = re.search(r'CALL_TEXT\s*:\s*(.+?)(?:\n|$)', call, re.S)
    verdict = v_m.group(1).upper() if v_m else None
    conviction = max(0, min(100, int(c_m.group(1)))) if c_m else None
    call_text = t_m.group(1).strip() if t_m else None
    return {'short_read': short, 'long_read': long_,
            'call_verdict': verdict, 'conviction': conviction,
            'call_text': call_text}


def upsert_read(fight: dict, parsed: dict, struct: dict) -> bool:
    # Composite game_id for UFC: event_date + fight_order (unique per event)
    gid = f"ufc_{fight['event_date']}_{fight['fight_order']}"
    pick_side = (fight.get('recommended_side') or '').upper()
    verdict = (parsed.get('call_verdict') or '').upper()
    # jerry_reads doesn't have call_verdict — encode via call_market:
    #   BACK → market='fight', side=picked
    #   FADE → market='fight', side=opposite of picked
    #   PASS → market='pass'
    if verdict == 'BACK':
        call_market = 'fight'; call_side = pick_side
    elif verdict == 'FADE':
        call_market = 'fight'
        call_side = 'B' if pick_side == 'A' else 'A'
    else:
        call_market = 'pass'; call_side = None

    payload = {
        'sport': 'UFC',
        'game_id': gid,
        'game_date': fight['event_date'],
        'call_market': call_market,
        'call_side': call_side,
        'call_line': None,
        'call_text': parsed.get('call_text'),
        'short_read': parsed.get('short_read'),
        'long_read': parsed.get('long_read'),
        'conviction': parsed.get('conviction'),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'prompt_version': PROMPT_VERSION,
        'input_snapshot': struct,
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
        headers=H_WRITE, json=payload, timeout=20)
    if r.status_code in (200, 201, 204): return True
    print(f'  ⚠ upsert {r.status_code}: {r.text[:200]}'); return False


def main(game_date: str | None = None, force: bool = False, limit: int | None = None):
    fights = fetch_upcoming_fights(game_date=game_date)
    if not fights:
        print(f'no upcoming UFC fights on {game_date or today_et()}'); return
    print(f'  {len(fights)} UFC fights in window')

    # 2026-08-08 enrichment: bulk-fetch fighter_stats for every fighter
    # on the card. Prompt has access to real record + physicals + strike
    # stats. Prevents "Gamrot has no finish in 25 fights" hallucination
    # class where Claude filled in from training data (or 0-corrupted DB
    # values). Only reliable fields — no wins_by_ko/sub/dec until scraper
    # is repaired (see ufc_espn_enrich.py for backfill flow).
    unique_names = set()
    for f in fights:
        if f.get('fighter_a'): unique_names.add(f['fighter_a'])
        if f.get('fighter_b'): unique_names.add(f['fighter_b'])
    fighter_stats_map = _fetch_fighter_stats_bulk(list(unique_names))
    print(f'  fighter_stats loaded for {len(fighter_stats_map)}/{len(unique_names)} fighters')

    done = 0
    for f in fights:
        if limit and done >= limit: break
        gid = f"ufc_{f['event_date']}_{f['fight_order']}"
        if not force:
            check = requests.get(
                f'{SUPABASE_URL}/rest/v1/jerry_reads',
                headers=H_READ,
                params={'sport': 'eq.UFC', 'game_id': f'eq.{gid}',
                        'game_date': f'eq.{f["event_date"]}', 'select': 'id'},
                timeout=10)
            if check.status_code == 200 and check.json():
                continue
        struct = build_fight_struct(f, fighter_stats=fighter_stats_map)
        prompt = render_prompt(struct)
        raw = call_claude(prompt)
        if not raw:
            print(f'  ⚠ {f["fighter_a"]} vs {f["fighter_b"]}: no claude response')
            continue
        parsed = parse_synthesis(raw)
        if not parsed.get('short_read'):
            print(f'  ⚠ {f["fighter_a"]} vs {f["fighter_b"]}: parse missing short')
            continue
        if upsert_read(f, parsed, struct):
            v = parsed.get('call_verdict') or '?'; c = parsed.get('conviction') or '?'
            print(f'  ✓ {f["fighter_a"][:16]:<16} vs {f["fighter_b"][:16]:<16} → {v} {c} ({parsed.get("call_text","?")})')
            done += 1
    print(f'\n=== wrote {done} UFC jerry_reads ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--force', action='store_true')
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    main(game_date=args.date, force=args.force, limit=args.limit)
