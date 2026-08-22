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
    'NFL': 'nfl_pipeline_props',      # enabled 2026-08-03 for Week 1 launch
    # 'NBA': 'nba_pipeline_props',
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


def _fetch_playbook_decision(sport: str, prop: dict) -> dict | None:
    """2026-08-17: fetch the prop playbook's shadow decision for this prop.
    Returned dict has playbook_tier / playbook_side / playbook_score /
    playbook_sources (signal contribution list).

    Playbook is shadow-mode — reading its decision here just gives Jerry
    additional structured reasoning to ground prose on, without changing
    the pick outcome (legacy tier still authoritative in Jerry's verdict)."""
    try:
        r = requests.get(f'{SUPABASE_URL}/rest/v1/prop_playbook_decisions',
                         headers=H_READ,
                         params={'sport': f'eq.{sport}',
                                 'player_name': f'eq.{prop.get("player_name", "")}',
                                 'prop_type': f'eq.{prop.get("prop_type", "")}',
                                 'direction': f'eq.{prop.get("direction", "")}',
                                 'game_date': f'eq.{prop.get("game_date", "")}',
                                 'select': 'playbook_tier,playbook_side,playbook_score,playbook_conviction,playbook_sources',
                                 'order': 'scored_at.desc',
                                 'limit': '1'},
                         timeout=5)
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if rows else None
    except Exception:
        return None


def _format_playbook_prose(pb: dict | None) -> str:
    """Convert playbook decision + signal contributions into a compact
    Jerry-readable brief. Only surface signals with real contribution."""
    if not pb or not pb.get('playbook_sources'):
        return ''
    sources = pb.get('playbook_sources') or []
    real = [s for s in sources if s.get('contribution', 0) > 0]
    if not real:
        return ''
    lines = [
        f'\n\n=== PLAYBOOK CROSS-CHECK (structured signals — shadow mode) ===',
        f'Playbook tier: {pb.get("playbook_tier", "?")} · side: {pb.get("playbook_side", "?")} · '
        f'aggregate score: {pb.get("playbook_score", 0):.2f}',
        f'Fired signals contributing to this side:',
    ]
    for s in real[:6]:
        lines.append(f'  · {s.get("signal_key", "?"):<32}  contrib={s.get("contribution", 0):+.2f}  → {s.get("prose", "")[:120]}')
    lines.append(
        'Use these plug-in signals as concrete evidence to ANCHOR your prose. '
        'They ARE data-backed reasons (not guesses). Cite the top 2-3 in your writeup. '
        'DO NOT invent stats not present in these signals or in the legacy signals below.'
    )
    return '\n'.join(lines)


def render_prompt(template: str, prop: dict, sport: str,
                  bucket_roi: dict | None = None) -> str:
    odds = prop.get('book_over_odds') if prop.get('direction') == 'over' else prop.get('book_under_odds')
    implied = _implied_prob(odds)
    sigs = prop.get('signals') or {}
    sig_lines = '\n'.join(f'  - {k}: {v}' for k, v in sigs.items() if not k.startswith('_'))[:2000]

    # 2026-08-17: Fetch playbook decision + signals to ground Jerry prose
    # in structured plug-in reasoning. Shadow-mode — doesn't change verdict.
    playbook_decision = _fetch_playbook_decision(sport, prop)
    playbook_prose = _format_playbook_prose(playbook_decision)

    # CALIBRATION SIGNAL SURFACE (2026-08-05). apply_calibration stores its
    # fade/flip reason under signals['_calibration_fade']. The underscore
    # normally hides it from Jerry (implementation detail), but when a fade
    # fired we MUST tell Jerry — otherwise he may write PASS prose while
    # the mechanical layer forces STRONG conviction, producing a card
    # with inconsistent tier vs narrative. Surface it as an explicit
    # instruction so Jerry's prose aligns with the calibrated call.
    cal_fade = sigs.get('_calibration_fade')
    calibration_directive = ''
    if cal_fade:
        calibration_directive = (
            f'\n\n=== CALIBRATION OVERRIDE (must honor) ===\n'
            f'A historical trap pattern fired for this prop: {cal_fade}\n'
            f'This means the ORIGINAL side is a known losing bucket at this juice band. '
            f'The prop has already been FLIPPED to the winning side ({prop.get("direction")}) '
            f'at the tier/conviction shown. Your job: write BACK prose for THIS side, '
            f'explaining WHY the market has priced the fade in (juice-band trap, form drift '
            f'already digested, etc.). Do NOT return PASS or FADE for this prop — '
            f'the mechanical layer has already made the call and your narrative must '
            f'match. Set VERDICT: BACK and CONVICTION consistent with the tier shown.'
        )

    # Bucket ROI injection (2026-08-01 R-4): give Jerry the historical hit
    # rate + juice-adjusted ROI for this exact (tier, prop_type, direction)
    # bucket. Jerry uses this to decide BACK/FADE/PASS with real edge instead
    # of just parroting tier labels. e.g. SKIP outs_under = +38.8% ROI → BACK
    # despite the SKIP tier.
    bucket_hint = ''
    if bucket_roi:
        from bucket_roi_lookup import lookup_prop, format_prop_hint
        b = lookup_prop(bucket_roi, prop.get('tier',''),
                        prop.get('prop_type',''), prop.get('direction',''))
        bucket_hint = format_prop_hint(b)

    rendered = (template
        .replace('{PLAYER}', prop.get('player_name') or '?')
        .replace('{PROP_TYPE}', prop.get('prop_type') or '?')
        .replace('{DIRECTION}', prop.get('direction') or '?')
        .replace('{PROP_LINE}', str(prop.get('prop_line') or '?'))
        .replace('{BOOK_ODDS}', str(odds or 'n/a'))
        .replace('{IMPLIED_PROB}', f'{implied:.1%}' if implied else 'n/a')
        .replace('{REFIT_CONVICTION}', str(prop.get('refit_conviction') or 'n/a'))
        .replace('{CONVICTION}', str(prop.get('conviction') or '?'))
        .replace('{SIGNALS}', sig_lines or '(no signals recorded)'))

    # Append bucket hint if template doesn't have a placeholder for it yet.
    # Older templates won't have {BUCKET_HINT} — safe append pattern.
    if '{BUCKET_HINT}' in rendered:
        rendered = rendered.replace('{BUCKET_HINT}', bucket_hint)
    elif bucket_hint:
        rendered = (
            f'{rendered}\n\n---HISTORICAL BUCKET (season-long backtest) ---\n{bucket_hint}\n'
            f'IMPORTANT: Weight this bucket ROI heavily in your BACK/FADE/PASS decision. '
            f'Positive ROI = evidence to BACK. Negative ROI = evidence to FADE regardless of tier.'
        )
    # Append calibration override AFTER bucket hint so it's the final instruction
    # Jerry sees. Highest-priority context = highest place in prompt tail.
    if calibration_directive:
        rendered = f'{rendered}{calibration_directive}'

    # 2026-08-17: append playbook signal grounding LAST. Placed after
    # calibration directive so it doesn't override picks but IS the most
    # recent structured evidence Jerry sees when drafting prose. Since
    # playbook is shadow-mode, this doesn't change the verdict — just
    # grounds the LANGUAGE in real signals (reducing hallucination risk).
    if playbook_prose:
        rendered = f'{rendered}{playbook_prose}'

    return rendered


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
    # 2026-08-22: 'source' field distinguishes 'llm' (Jerry Claude synth) from
    # 'template' (render_prop_template.py deterministic renderer). If parsed
    # already carries source, honor it; else default to 'llm' for LLM-path calls.
    source = parsed.get('source') or 'llm'
    payload = {
        'sport': sport,
        'game_id': prop.get('game_id'),
        'game_date': game_date,
        'player_name': prop.get('player_name'),
        'prop_type': prop.get('prop_type'),
        'direction': prop.get('direction'),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'prompt_version': PROMPT_VERSION if source == 'llm' else 'template_v1',
        'prop_line': prop.get('prop_line'),
        'book_odds': prop.get('book_over_odds') if prop.get('direction') == 'over' else prop.get('book_under_odds'),
        'refit_conviction': prop.get('refit_conviction'),
        'input_snapshot': {'signals': prop.get('signals'), 'conviction': prop.get('conviction'),
                           'source': source},
        **{k: v for k, v in parsed.items() if k != 'source'},
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/prop_jerry_reads?on_conflict=sport,game_id,player_name,prop_type,direction,game_date',
        headers=H_WRITE, json=payload, timeout=20,
    )
    if r.status_code in (200, 201, 204):
        return True
    # If the 'source' column doesn't exist yet (pre-migration), retry without it
    if r.status_code == 400 and 'source' in (r.text or ''):
        payload2 = {k: v for k, v in payload.items() if k != 'source'}
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/prop_jerry_reads?on_conflict=sport,game_id,player_name,prop_type,direction,game_date',
            headers=H_WRITE, json=payload2, timeout=20,
        )
        if r.status_code in (200, 201, 204):
            return True
    print(f'  ⚠ upsert {r.status_code}: {r.text[:200]}')
    return False


def _refit_self_heal_if_stale(sport: str, game_date: str, table: str) -> None:
    """2026-08-17: fail-safe against pipeline sequencing gaps.

    If today's props have >5% missing refit_conviction, invoke
    apply_prop_refit.py directly before Jerry synthesizes. Prevents
    the class of failure where user's manual pipeline run skips
    apply_prop_refit and Jerry proceeds with stale conviction values,
    producing prop takes that ignore the calibration layer entirely.

    MLB-only for now (refit v2 is MLB-only per project_prop_edge_calibration_july).
    Non-fatal — a self-heal failure just means Jerry runs with whatever
    refit coverage exists today; not worse than the old behavior."""
    if sport != 'MLB': return
    try:
        r = requests.get(f'{SUPABASE_URL}/rest/v1/{table}',
                         headers=H_READ,
                         params={'game_date': f'eq.{game_date}',
                                 'select': 'refit_conviction',
                                 'limit': 500},
                         timeout=15)
        rows = r.json() if r.status_code == 200 else []
        if not rows: return
        missing = sum(1 for row in rows if row.get('refit_conviction') is None)
        coverage_pct = 100.0 * (len(rows) - missing) / len(rows)
        if missing / len(rows) > 0.05:  # >5% missing = sequencing gap
            print(f'  [{sport}] refit_conviction coverage {coverage_pct:.1f}% ({missing}/{len(rows)} missing) — '
                  f'firing apply_prop_refit.py as self-heal')
            import subprocess
            here = os.path.dirname(os.path.abspath(__file__))
            subprocess.run([sys.executable, os.path.join(here, 'apply_prop_refit.py')],
                           capture_output=True, text=True, timeout=180, env=os.environ.copy())
            print(f'  [{sport}] refit self-heal complete')
    except Exception as e:
        print(f'  [{sport}] refit self-heal failed (non-fatal): {e}')


def run_for_sport(sport: str, game_date: str, template: str, force: bool = False,
                  limit: int | None = None, tier_gate: set[str] | None = None):
    table = PROPS_TABLE.get(sport)
    if not table:
        print(f'  [{sport}] no props table registered — skip'); return 0
    # 2026-08-17: refit self-heal before Jerry runs — catches sequencing
    # gaps where apply_prop_refit was skipped in the pipeline.
    _refit_self_heal_if_stale(sport, game_date, table)
    # Fetch today's props for this sport
    r = requests.get(f'{SUPABASE_URL}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': 'game_id,player_name,prop_type,direction,prop_line,'
                                       'signals,conviction,refit_conviction,book_over_odds,book_under_odds,tier',
                             'limit': 300},
                     timeout=30)
    props = r.json() if r.status_code == 200 else []
    # Kill switch (2026-08-01 Path B): JERRY_BUCKET_ROI_ENABLED=false disables
    # the entire bucket ROI injection path. Fallback = pre-8/1 behavior.
    BUCKET_ROI_ON = os.environ.get('JERRY_BUCKET_ROI_ENABLED', 'true').lower() != 'false'
    _bucket_roi = {}
    if BUCKET_ROI_ON:
        # Skip SKIP-tier props ONLY when the bucket doesn't say BACK. 8/1 audit
        # found SKIP outs_under at +38.8% ROI (n=79) — literally hidden gold.
        from bucket_roi_lookup import load_prop_buckets, lookup_prop
        _bucket_roi = load_prop_buckets(sport=sport)
        def _keep_skip(p):
            if (p.get('tier') or '').upper() != 'SKIP':
                return True
            b = lookup_prop(_bucket_roi, 'SKIP', p.get('prop_type',''), p.get('direction',''))
            return b and (b.get('jerry_hint') or '').upper() == 'BACK'
        before = len(props)
        props = [p for p in props if _keep_skip(p)]
        if before != len(props):
            print(f'  [{sport}] dropped {before - len(props)} SKIP-tier props with no BACK hint')
    else:
        # Kill switch active — fall back to prior SKIP filter
        props = [p for p in props if p.get('tier') != 'SKIP']
        print(f'  [{sport}] JERRY_BUCKET_ROI_ENABLED=false — using legacy SKIP filter')

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

    # Tier calibration (2026-08-03 v2): FADE historical losers (flip direction),
    # cap juice traps, promote goldmine SKIP tiers. Per user directive: don't
    # suppress — Jerry should say "fade the other side, market has priced it
    # in". A 29% W bucket is a 71% FADE signal.
    try:
        from prop_tier_calibration import apply_calibration
        faded = capped = promoted = 0
        for p in props:
            r = apply_calibration(p, jerry_verdict='BACK')
            if r.get('flip_direction'):
                # Flip direction, adjust prop_type suffix, use fade tier/conv
                old_dir = p['direction']
                new_dir = r['new_direction']
                p['direction'] = new_dir
                p['tier'] = r['new_tier']
                p['conviction'] = r['new_conviction']
                # prop_type flip: outs_over → outs_under, etc.
                if p.get('prop_type', '').endswith(f'_{old_dir}'):
                    family = p['prop_type'].rsplit('_', 1)[0]
                    p['prop_type'] = f'{family}_{new_dir}'
                p.setdefault('signals', {})['_calibration_fade'] = r['reason']
                faded += 1
            elif 'promoted' in r.get('reason', ''):
                p['tier'] = r['new_tier']
                p['conviction'] = r['new_conviction']
                promoted += 1
            elif r['new_conviction'] < (p.get('conviction') or 0):
                p['conviction'] = r['new_conviction']
                capped += 1
        if faded or capped or promoted:
            print(f'  [{sport}] tier calibration: faded {faded} '
                  f'· juice-capped {capped} · goldmine-promoted {promoted}')
    except ImportError:
        pass  # calibration module optional; earlier pipeline unaffected

    # 2026-08-22 tier-gate + template renderer split:
    # When --tier-gate PRIME,STRONG is passed, LLM narration only fires for
    # card-worthy tiers. Below-gate props get deterministic template-rendered
    # short_read from render_prop_template.py — same output shape as LLM,
    # zero Claude cost, no hallucination risk. Every prop still lands in
    # prop_jerry_reads so downstream (grader, refit override, sweat card)
    # sees a uniform table.
    props_for_llm = props
    props_for_template = []
    if tier_gate:
        props_for_llm = [p for p in props if (p.get('tier') or '').upper() in tier_gate]
        props_for_template = [p for p in props if (p.get('tier') or '').upper() not in tier_gate]
        print(f'  [{sport}] tier-gate {sorted(tier_gate)}: '
              f'{len(props_for_llm)} LLM · {len(props_for_template)} template')

    # Batch-fetch playbook decisions so template renderer gets rich chip data
    # without per-prop DB round-trips.
    playbook_by_key: dict = {}
    if props_for_template:
        try:
            pb_r = requests.get(f'{SUPABASE_URL}/rest/v1/prop_playbook_decisions',
                headers=H_READ,
                params={'sport': f'eq.{sport}', 'game_date': f'eq.{game_date}',
                        'select': 'player_name,prop_type,direction,prop_line,'
                                  'playbook_side,playbook_sources'},
                timeout=15)
            if pb_r.status_code == 200:
                for row in pb_r.json() or []:
                    k = (row.get('player_name'), row.get('prop_type'),
                         row.get('direction'), row.get('prop_line'))
                    playbook_by_key[k] = row
        except Exception as _e:
            print(f'  [{sport}] playbook fetch failed (template will use signals dict): {_e}')

    # Render templates for below-gate props FIRST — fast, no LLM
    tmpl_done = 0
    if props_for_template:
        try:
            from render_prop_template import render_prop_template
            for prop in props_for_template:
                if not force:
                    check = requests.get(f'{SUPABASE_URL}/rest/v1/prop_jerry_reads',
                        headers=H_READ, params={
                            'sport': f'eq.{sport}', 'game_id': f'eq.{prop["game_id"]}',
                            'player_name': f'eq.{prop["player_name"]}',
                            'prop_type': f'eq.{prop["prop_type"]}',
                            'direction': f'eq.{prop["direction"]}',
                            'game_date': f'eq.{game_date}', 'select': 'id,source'}, timeout=10)
                    existing = check.json() if check.status_code == 200 else []
                    # Only re-render templates over templates; don't clobber an LLM read.
                    if existing and existing[0].get('source') not in (None, 'template'):
                        continue
                pb_key = (prop.get('player_name'), prop.get('prop_type'),
                          prop.get('direction'), prop.get('prop_line'))
                pb_row = playbook_by_key.get(pb_key)
                rendered = render_prop_template(prop, pb_row)
                # Map template output → same parsed shape as LLM path
                parsed = {
                    'short_read': rendered.get('short_read'),
                    'long_read': '',  # template path is short-only; app renders chips for detail
                    'call_verdict': rendered.get('verdict'),
                    'conviction': rendered.get('conviction'),
                    'source': 'template',
                }
                if upsert_read(sport, prop, parsed, '', game_date):
                    tmpl_done += 1
            print(f'  [{sport}] template rendered {tmpl_done}/{len(props_for_template)} below-gate props')
        except ImportError as _e:
            print(f'  [{sport}] render_prop_template unavailable — below-gate props skipped ({_e})')

    # Then LLM path for card-worthy props (existing loop)
    props = props_for_llm
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
        prompt = render_prompt(template, prop, sport, bucket_roi=_bucket_roi)
        raw = call_claude(prompt)
        if not raw: continue
        parsed = parse_synthesis(raw, prop.get('prop_type'))
        if not parsed.get('short_read'):
            print(f'  ⚠ parse missing take for {prop["player_name"]}'); continue
        # Post-LLM brand-name sanitizer (2026-08-03) — belt-and-suspenders
        try:
            from sanitize_jerry_prose import scrub
            parsed['short_read'] = scrub(parsed.get('short_read'))
            if 'long_read' in parsed:
                parsed['long_read'] = scrub(parsed.get('long_read'))
        except ImportError:
            pass
        # Hallucination guard v2 (2026-08-06): retry + name whitelist for props too.
        # Props are more targeted (single player, single prop_type) so name
        # whitelist is stricter — only the player and pitchers on the game
        # should appear in prose.
        try:
            from validate_jerry_read import (validate as _validate,
                                              validate_direction as _validate_dir,
                                              validate_pitcher_names,
                                              build_corrective_prompt,
                                              substitute_hallucinated_names)
            # Build prop-specific whitelist struct (player + pitchers if we have game context)
            whitelist_struct = {
                'home_pitcher': prop.get('player_name') if prop.get('prop_type','').startswith(('bb','ks','er','ha','outs')) else None,
                'away_pitcher': None,
                'home_team': prop.get('player_team',''),
                'away_team': '',
            }
            combined = (parsed.get('short_read') or '') + '\n' + (parsed.get('long_read') or '')
            num_rpt = _validate(parsed.get('short_read'), parsed.get('long_read',''), prop)
            name_rpt = validate_pitcher_names(combined, whitelist_struct)

            if not num_rpt['is_valid'] or not name_rpt['valid']:
                print(f'  ⚠ {prop["player_name"][:20]} hallucination (nums={num_rpt.get("hallucinated_numbers")[:3]}, '
                      f'names={name_rpt.get("suspects")[:2]}) — regen')
                corr = build_corrective_prompt(prompt, num_rpt, name_rpt)
                raw2 = call_claude(corr)
                worked = False
                if raw2:
                    p2 = parse_synthesis(raw2, prop.get('prop_type'))
                    if p2.get('short_read'):
                        try:
                            from sanitize_jerry_prose import scrub
                            p2['short_read'] = scrub(p2.get('short_read'))
                            if 'long_read' in p2:
                                p2['long_read'] = scrub(p2.get('long_read'))
                        except ImportError: pass
                        parsed = p2
                        c2 = (p2.get('short_read') or '') + '\n' + (p2.get('long_read') or '')
                        n2 = _validate(p2.get('short_read'), p2.get('long_read',''), prop)
                        nm2 = validate_pitcher_names(c2, whitelist_struct)
                        if n2['is_valid'] and nm2['valid']:
                            worked = True; print(f'  ✓ retry clean')
                        else:
                            name_rpt = nm2
                if not worked and name_rpt.get('suspects'):
                    parsed['short_read'] = substitute_hallucinated_names(
                        parsed.get('short_read',''), whitelist_struct, name_rpt['suspects'])
                    if parsed.get('long_read'):
                        parsed['long_read'] = substitute_hallucinated_names(
                            parsed.get('long_read',''), whitelist_struct, name_rpt['suspects'])
                    print(f'  🔧 substituted {len(name_rpt["suspects"])} suspect name(s)')

                # NUMBER HALLUCINATION HARD-ENFORCE (2026-08-06 v2): cap
                # prop conviction at 55 if retry still has hallucinated
                # numbers. Prop props with fake proj values are worse than
                # game reads (users bet on the specific projected value).
                if not worked and num_rpt.get('hallucinated_numbers'):
                    orig_conv = parsed.get('conviction') or 0
                    if orig_conv > 55:
                        parsed['conviction'] = 55
                        print(f'  🔒 {prop["player_name"][:20]} conviction capped '
                              f'{orig_conv}→55 due to unverified nums')
            # Legacy log line — keep for backward compat + audit trail
            report = num_rpt
            # Direction-contradiction check (2026-08-05) — catches BACK calls
            # whose direction disagrees with the model's own projection.
            # Painter BB Under @ -135 with projection 2.10 BB was the canary.
            # SKIP when calibration has already forced a fade-flip — the
            # projection edge belongs to the ORIGINAL direction and the
            # historical trap pattern outranks the projection here.
            calibration_flip_active = bool((prop.get('signals') or {}).get('_calibration_fade'))
            dir_report = _validate_dir(prop, parsed.get('call_verdict'),
                                       parsed.get('call_direction') or parsed.get('call_side'))
            if dir_report['contradicts'] and not calibration_flip_active:
                print(f'  ⚠ DIRECTION CONTRADICTION on {prop["player_name"]} '
                      f'{prop.get("prop_type")}/{prop.get("direction")}: {dir_report["reason"]} '
                      f'— downgrading BACK → PASS')
                parsed['call_verdict'] = 'PASS'
                # Cap conviction so downstream sweat card selection can't
                # promote a contradicted read back into the top set
                prev_conv = parsed.get('conviction') or 0
                try:
                    prev_conv = int(prev_conv)
                except (TypeError, ValueError):
                    prev_conv = 0
                parsed['conviction'] = min(prev_conv, 35)
        except ImportError:
            pass
        if upsert_read(sport, prop, parsed, prompt, game_date):
            verdict = parsed.get('call_verdict') or '?'; conv = parsed.get('conviction') or '?'
            print(f'  ✓ {prop["player_name"][:20]:<20} {prop["prop_type"]:<12} {prop["direction"]:<5} → {verdict} {conv}')
            done += 1
    return done


def main(force: bool = False, sport: str | None = None,
         game_date: str | None = None, limit: int | None = None,
         tier_gate: set[str] | None = None):
    gd = game_date or today_et()
    print(f'=== generate_prop_jerry_synthesis · {gd} ===')
    if tier_gate:
        print(f'  tier-gate active: only synthesizing {sorted(tier_gate)}')
    template = load_prompt()
    if not template:
        print('  ⛔ no prop_jerry_synthesis prompt — run seed_prop_jerry_prompt.py first'); return
    sports = [sport] if sport else list(PROPS_TABLE.keys())
    total = 0
    for s in sports:
        total += run_for_sport(s, gd, template, force=force, limit=limit, tier_gate=tier_gate)
    print(f'\n=== wrote {total} prop_jerry_reads ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--force', action='store_true')
    p.add_argument('--sport', help='MLB only for now; others when their pipelines ship')
    p.add_argument('--date')
    p.add_argument('--limit', type=int)
    # 2026-08-22: --tier-gate caps LLM narration to specified tiers only.
    # Default (no flag) preserves legacy behavior — every non-SKIP prop gets
    # a Claude call (~332 calls/night = ~25 min). With --tier-gate PRIME,STRONG
    # only card-worthy props get LLM prose (~61 calls = ~4 min). Below-gate
    # props still get graded and scored — they just skip the LLM narration
    # step (their tier + conviction + signals dict is already the pick).
    # Post-launch: pair with render_prop_template.py to fill deterministic
    # short_read for the skipped props so app renders cleanly.
    p.add_argument('--tier-gate', help='Comma-separated tiers to synthesize (e.g. PRIME,STRONG). Others skipped.')
    args = p.parse_args()
    gate = None
    if args.tier_gate:
        gate = {t.strip().upper() for t in args.tier_gate.split(',') if t.strip()}
    main(force=args.force, sport=args.sport, game_date=args.date,
         limit=args.limit, tier_gate=gate)
