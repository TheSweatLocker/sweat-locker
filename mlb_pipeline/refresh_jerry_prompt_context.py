"""Weekly refresher for Jerry synthesis prompt's MODEL PERFORMANCE CONTEXT block.

Interim adaptive-weighting move (2026-08-02, ahead of Phase 3). Instead of
Jerry reading stale July audit numbers hardcoded into the prompt for months,
this script pulls the CURRENT numbers from model_track_records + computes
money-flow hit rate from oddscrowd_snapshot vs results, then rebuilds the
prompt's context block and PATCHes prompt_templates in place.

What we CAN cleanly refresh today (from lifetime track records):
  MODEL_TOTAL   (v3/v4 total) — real hit rate from jerry_cache history
  MODEL_SPREAD  (v3/v4 spread) — real hit rate from jerry_cache history
  RESOLVER      (ensemble total call)
  RESOLVER_SIDE (ensemble ML call)
  MONEY_ML      (computed live from oddscrowd_snapshot vs game results)
  MONEY_TOTAL   (computed live from oddscrowd_snapshot vs game results)

What we CAN'T yet (waiting for Option B snapshot data ~30d):
  PANEL, MC individually — historical jerry_cache pooled them together.
  snapshot_mlb_game_context started collecting cleanly from 8/2 forward.
  Once we have 30d of snapshots, add them here and the block gets richer.

Sport-parametric via SPORT_PROMPT_MAP registry — same script refreshes MLB
today, will refresh NFL / NCAAF / NCAAB / NBA when their jerry_synthesis
prompts + track records ship.

Run weekly via GHA cron. Prints a diff summary so pipeline runs are auditable.

Usage:
    python refresh_jerry_prompt_context.py [--sport MLB] [--dry-run]
"""
import argparse, os, re, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Sport → prompt template registry. Add rows here as new sports ship jerry synth.
SPORT_PROMPT_MAP = {
    'MLB': {'template_name': 'jerry_synthesis', 'context_table': 'mlb_game_context', 'results_table': 'mlb_game_results'},
    # 'NFL': {'template_name': 'jerry_synthesis', 'context_table': 'nfl_game_context', 'results_table': 'nfl_game_results'},
    # 'NCAAF': {...},
}


def _pull_track_records(sport: str) -> dict:
    """Return {model_name: {'hit_rate': %, 'n': int, 'market': 'TOTAL'/'ML'}}."""
    r = requests.get(f'{SB}/rest/v1/model_track_records',
                     headers=H_READ,
                     params={'sport': f'eq.{sport}', 'bucket_window': 'eq.lifetime',
                             'select': 'model_name,market,sample_n,hit_rate'},
                     timeout=15).json()
    out = {}
    if isinstance(r, list):
        for row in r:
            out[row['model_name']] = {'hit_rate': row.get('hit_rate'),
                                       'n': row.get('sample_n'),
                                       'market': row.get('market')}
    return out


def _compute_money_hit_rate(sport: str, days: int = 90) -> dict:
    """Live-compute MONEY_ML + MONEY_TOTAL from oddscrowd_snapshot vs results.
    Not currently in model_track_records — this is the cheap live computation.
    """
    cfg = SPORT_PROMPT_MAP.get(sport, {})
    ctx_table = cfg.get('context_table')
    res_table = cfg.get('results_table')
    if not (ctx_table and res_table): return {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')

    # Pull all contexts with money data since cutoff (paginated)
    all_ctx = []
    offset = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{ctx_table}',
                         headers=H_READ,
                         params={'game_date': f'gte.{cutoff}',
                                 'select': 'game_id,game_date,home_team,away_team,close_total,oddscrowd_snapshot',
                                 'limit': '500', 'offset': str(offset)},
                         timeout=20).json()
        if not isinstance(r, list) or not r: break
        all_ctx += r
        if len(r) < 500: break
        offset += 500

    # Pull matching results
    game_ids = list({c['game_id'] for c in all_ctx if c.get('game_id')})
    results = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i:i+100]
        in_str = ','.join(f'"{g}"' for g in chunk)
        r = requests.get(f'{SB}/rest/v1/{res_table}',
                         headers=H_READ,
                         params={'game_id': f'in.({in_str})',
                                 'select': 'game_id,home_score,away_score', 'limit': '500'},
                         timeout=15).json()
        for x in (r if isinstance(r, list) else []):
            if x.get('home_score') is not None:
                results[x['game_id']] = x

    ml_w = ml_n = tot_w = tot_n = 0
    for c in all_ctx:
        res = results.get(c.get('game_id'))
        if not res: continue
        hs, as_ = res['home_score'], res['away_score']
        line = c.get('close_total')
        money = c.get('oddscrowd_snapshot') or {}
        if isinstance(money, str):
            try: money = json.loads(money)
            except: money = {}
        # ML pick
        ml_pick = (money.get('ml') or {}).get('pick')
        if ml_pick in ('HOME', 'AWAY') and hs != as_:
            winner = 'HOME' if hs > as_ else 'AWAY'
            ml_n += 1
            if ml_pick == winner: ml_w += 1
        # Total pick
        tot_pick = (money.get('total') or {}).get('pick')
        if tot_pick in ('OVER', 'UNDER') and line is not None:
            actual_tot = hs + as_
            if actual_tot != line:
                actual = 'OVER' if actual_tot > line else 'UNDER'
                tot_n += 1
                if tot_pick == actual: tot_w += 1

    return {
        'MONEY_ML': {'hit_rate': round(ml_w/ml_n*100, 1) if ml_n else None, 'n': ml_n},
        'MONEY_TOTAL': {'hit_rate': round(tot_w/tot_n*100, 1) if tot_n else None, 'n': tot_n},
    }


def _render_context_block(sport: str, tr: dict, money: dict, today: str) -> str:
    """Build the MODEL PERFORMANCE CONTEXT block Jerry sees in the prompt."""
    def fmt(name, market, label, note=''):
        rec = tr.get(name)
        if not rec or rec.get('hit_rate') is None:
            return f'  {label:<30} → data pending (Option B accumulation)'
        return f'  {label:<30} → {rec["hit_rate"]:.1f}% W  (n={rec["n"]})  {note}'.rstrip()

    def fmt_money(key, label):
        rec = money.get(key)
        if not rec or rec.get('hit_rate') is None:
            return f'  {label:<30} → n/a'
        return f'  {label:<30} → {rec["hit_rate"]:.1f}% W  (n={rec["n"]}, 90d)'

    lines = [
        f'MODEL PERFORMANCE CONTEXT (auto-refreshed {today}, from live track records):',
        '',
        'The numbers below are ACTUAL rolling hit rates from the model_track_records',
        'table. Use them to weight relative reliability when signals disagree.',
        '',
        fmt('RESOLVER_SIDE',  'ML',    'RESOLVER_SIDE (ensemble ML)'),
        fmt('RESOLVER',       'TOTAL', 'RESOLVER (ensemble total)'),
        fmt('MODEL_TOTAL',    'TOTAL', 'MODEL_TOTAL (v3/v4 total)'),
        fmt('MODEL_SPREAD',   'ML',    'MODEL_SPREAD (v3/v4 spread)', '← historically weak, discount'),
        fmt_money('MONEY_ML', 'MONEY_ML (public $%)'),
        fmt_money('MONEY_TOTAL', 'MONEY_TOTAL (public $%)'),
        '',
        'PANEL (jerry_pred) and MC individually are pending Option B snapshot data',
        '(~30d of clean per-model predictions). External audits estimated PANEL ~65%',
        'W recent — treat as best available signal but recognize we cannot yet verify.',
        '',
        'RULES OF THUMB:',
        '  - Sub-50% models (MODEL_SPREAD, etc.) are FADE-worthy on their own',
        '  - When 2+ models agree AND their hit rates support them → high conviction',
        '  - Never let a single signal drive conviction over 55',
        '  - Money-flow divergence <20pp = supplementary, not primary signal',
    ]
    return '\n'.join(lines)


def refresh(sport: str = 'MLB', dry_run: bool = False) -> None:
    cfg = SPORT_PROMPT_MAP.get(sport)
    if not cfg:
        print(f'  [{sport}] not registered in SPORT_PROMPT_MAP — skip'); return

    print(f'=== refresh_jerry_prompt_context · {sport} ===')

    tr = _pull_track_records(sport)
    print(f'  track records loaded: {len(tr)} models')
    money = _compute_money_hit_rate(sport)
    print(f'  MONEY hit rates (90d): ML={money.get("MONEY_ML",{})}  TOTAL={money.get("MONEY_TOTAL",{})}')

    # Fetch current template
    r = requests.get(f'{SB}/rest/v1/prompt_templates',
                     headers=H_READ,
                     params={'name': f'eq.{cfg["template_name"]}',
                             'sport': f'eq.{sport}',
                             'select': 'id,template'},
                     timeout=15).json()
    if not (isinstance(r, list) and r):
        print(f'  ⚠ no template found for name={cfg["template_name"]} sport={sport}'); return
    tid = r[0]['id']
    tmpl = r[0]['template']

    today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
    new_block = _render_context_block(sport, tr, money, today)

    # Replace the block: match from "MODEL PERFORMANCE CONTEXT" through the blank line
    # before "RULES OF THUMB" OR "PITCHER/TEAM ATTRIBUTION GUARDRAIL" OR "Game:".
    # The new block ITSELF ends with RULES OF THUMB so we need to also swallow the
    # OLD rules of thumb section (which was static, we're regenerating dynamically).
    # Anchor endpoint = the first line starting with "PITCHER/TEAM ATTRIBUTION" or "Game:".
    pattern = re.compile(
        r'MODEL PERFORMANCE CONTEXT.*?(?=(PITCHER/TEAM ATTRIBUTION|Game:\s*\{AWAY_TEAM\}))',
        re.DOTALL,
    )
    m = pattern.search(tmpl)
    if not m:
        print('  ⚠ could not locate MODEL PERFORMANCE CONTEXT block anchor')
        return

    new_tmpl = pattern.sub(new_block + '\n\n', tmpl)

    if dry_run:
        print('\n--- WOULD WRITE ---')
        print(new_block)
        print(f'\n(template len: {len(tmpl)} → {len(new_tmpl)})')
        return

    pr = requests.patch(f'{SB}/rest/v1/prompt_templates?id=eq.{tid}',
                        headers=H_WRITE, json={'template': new_tmpl}, timeout=15)
    if pr.status_code in (200, 204):
        print(f'  ✅ patched {sport} template (id={tid})')
        print('\n--- NEW CONTEXT BLOCK ---')
        print(new_block)
    else:
        print(f'  ⚠ patch {pr.status_code}: {pr.text[:200]}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    refresh(sport=args.sport, dry_run=args.dry_run)
