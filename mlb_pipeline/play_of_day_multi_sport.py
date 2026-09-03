"""Cross-sport POTD selector — runs AFTER play_of_day.py.

play_of_day.py is MLB-only (4863 lines, 11 mlb_game_context refs, 0 other-
sport refs). Rather than refactor that file pre-launch, this companion
module scans NFL/NCAAF/NBA/NCAAB/NHL game_contexts for the strongest
PRIME/STRONG pick, applies the LR predictor as a gate, and — if the
cross-sport winner has meaningfully higher conviction than the MLB POTD
already published to jerry_cache.best_bet_{today} — OVERRIDES with the
football/hoops/hockey pick. Otherwise leaves MLB POTD alone.

LR PROBABILITY FOLD (matches Dawg-of-Day pattern shipped 9/3):
  Each candidate gets p_pick_wins evaluated via defensive_gates._lr_predict_ml.
  Candidates where LR sees COIN or FADE (p < 0.55) get their conviction
  discounted by up to 20pts — so a legacy-strong pick the LR distrusts
  can't beat a MLB POTD that both scorers agree on.

Runs in workflow AFTER play_of_day.py so it can OVERWRITE best_bet_{today}.
The MLB POTD write is preserved in best_bet_{today}_mlb for audit/rollback.

Usage:
  python play_of_day_multi_sport.py             # publish
  python play_of_day_multi_sport.py --dry-run   # inspect only
  python play_of_day_multi_sport.py --min-lead 5  # override lead threshold
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {'apikey': K, 'Authorization': f'Bearer {K}',
           'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


SPORT_CONTEXTS = [
    ('NFL',   'nfl_game_context'),
    ('NCAAF', 'ncaaf_game_context'),
    ('NBA',   'nba_game_context'),
    ('NCAAB', 'ncaab_game_context'),
    ('NHL',   'nhl_game_context'),
]


def _today_et() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _fetch_sport_candidates(today: str) -> list[dict]:
    """Pull PRIME/STRONG primary_plays from each sport's context table for today."""
    candidates = []
    for sport, tbl in SPORT_CONTEXTS:
        try:
            # SELECT * — sport tables use different column names for the
            # same concept (close_home_ml vs home_ml_close, etc). Rather
            # than probe schemas, take everything; LR predictor and any
            # downstream code that reads ctx will pick what it needs.
            r = requests.get(
                f'{SB}/rest/v1/{tbl}',
                params={
                    'select': '*',
                    'game_date': f'eq.{today}',
                    'primary_play': 'not.is.null',
                },
                headers=H_READ, timeout=15,
            )
            if r.status_code != 200: continue
            rows = r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            print(f'  {sport} fetch failed: {e}')
            continue
        for row in rows:
            pp = row.get('primary_play') or {}
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except Exception: pp = {}
            if not isinstance(pp, dict): continue
            tier = str(pp.get('tier') or '').upper()
            if tier not in ('PRIME', 'STRONG'): continue
            candidates.append({
                'sport': sport,
                'game_id': row.get('game_id'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'ctx_row': row,
                'pp': pp,
                'tier': tier,
                'conviction': int(pp.get('conviction') or 0),
                'side': pp.get('side'),
                'type': pp.get('type'),
                'label': pp.get('label') or '',
                'sub': pp.get('sub') or '',
            })
    return candidates


def _score_with_lr(candidate: dict) -> tuple[float | None, str]:
    """Apply sport-specific LR predictor to this candidate and return
    (p_pick_wins, notes). Adjusts conviction in-place: LR-COIN or LR-FADE
    picks get discounted so they can't overwrite a MLB POTD both scorers agree on.
    Returns (None, '') if LR unavailable for this sport."""
    try:
        from defensive_gates import (_lr_predict_ml, _LR_MODEL_MLB_ML,
                                     _LR_MODEL_NFL_ML, _LR_MODEL_NCAAF_ML)
    except Exception:
        return None, ''
    model_map = {
        'MLB':   _LR_MODEL_MLB_ML,
        'NFL':   _LR_MODEL_NFL_ML,
        'NCAAF': _LR_MODEL_NCAAF_ML,
    }
    model = model_map.get(candidate['sport'])
    if model is None:
        return None, ''  # NBA/NHL/NCAAB LR not yet trained (data-blocked)
    ctx = candidate['ctx_row']
    pred = _lr_predict_ml(ctx, model=model)
    if not pred:
        return None, ''
    p_home = pred['p_home_win']
    side = str(candidate['side'] or '').upper()
    p_pick = p_home if side == 'HOME' else (1 - p_home) if side == 'AWAY' else 0.5
    note = f'LR p_win={p_pick:.2f}'
    if p_pick < 0.45:
        # LR disagrees strongly — discount conviction so this can't override MLB
        candidate['conviction'] = max(0, candidate['conviction'] - 20)
        candidate['_lr_disagrees'] = True
        note += ' (discounted -20: LR fades)'
    elif p_pick < 0.55:
        candidate['conviction'] = max(0, candidate['conviction'] - 8)
        candidate['_lr_soft_disagree'] = True
        note += ' (discounted -8: LR coin)'
    else:
        note += ' (LR confirms)'
    candidate['_lr_p_pick'] = round(p_pick, 4)
    return p_pick, note


def _get_current_potd(today: str) -> dict | None:
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        params={'game_id': f'eq.best_bet_{today}', 'select': '*'},
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json() if isinstance(r.json(), list) else []
    return rows[0] if rows else None


def _publish_potd_override(today: str, winner: dict, mlb_row: dict | None, dry_run: bool):
    """Write the cross-sport winner to jerry_cache.best_bet_{today}.
    Preserves the outgoing MLB POTD to best_bet_{today}_mlb for audit."""
    ctx = winner['ctx_row']
    if winner['side'] == 'HOME':
        ml = ctx.get('close_home_ml') or ctx.get('home_ml_close')
    else:
        ml = ctx.get('close_away_ml') or ctx.get('away_ml_close')
    pick_team = winner['home_team'] if winner['side'] == 'HOME' else winner['away_team']

    payload = {
        'sport': winner['sport'],
        'game': f"{winner['away_team']} @ {winner['home_team']}",
        'matchup': f"{winner['away_team']} @ {winner['home_team']}",
        'game_id': winner['game_id'],
        'anchor': 'cross_sport_potd_v1',
        'confidence': winner['tier'].lower(),
        'score': {
            'total': winner['conviction'],
            'source': 'cross_sport_ensemble_lr',
        },
        'leanDisplay': f"{pick_team} {(winner['type'] or 'ML').upper()} "
                       f"({winner['sport']} {winner['tier']} {winner['conviction']}/100)",
        'label': winner['label'],
        'sub': winner['sub'],
        'context': {
            'sport': winner['sport'],
            'lr_p_pick_wins': winner.get('_lr_p_pick'),
            'was_mlb_potd_conviction': (mlb_row or {}).get('data', {}).get('score', {}).get('total'),
        },
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'pipelineGenerated': True,
    }

    if dry_run:
        print(f'  [DRY] would OVERRIDE best_bet_{today} with:')
        print(f'    sport={payload["sport"]} · {payload["leanDisplay"]}')
        return

    # Preserve outgoing MLB POTD for audit / rollback
    if mlb_row and mlb_row.get('data', {}).get('sport') == 'MLB':
        try:
            requests.post(
                f'{SB}/rest/v1/jerry_cache',
                headers=H_WRITE, timeout=15,
                json={
                    'game_id': f'best_bet_{today}_mlb',
                    'sport': 'MLB',
                    'cache_key': f'best_bet_{today}_mlb',
                    'data': mlb_row.get('data'),
                    'narrative': mlb_row.get('narrative'),
                    'fetched_at': datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e:
            print(f'  ⚠ mlb-backup write failed (non-fatal): {e}')

    # Now overwrite best_bet_{today}
    r = requests.post(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_WRITE, timeout=15,
        json={
            'game_id': f'best_bet_{today}',
            'sport': winner['sport'],
            'cache_key': f'best_bet_{today}',
            'data': payload,
            'narrative': (winner['sub'] or winner['label'])[:500],
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        },
    )
    if r.status_code in (200, 201, 204):
        print(f'  ✓ published cross-sport POTD: {winner["sport"]} {payload["leanDisplay"]}')
    else:
        print(f'  ✗ publish failed {r.status_code}: {r.text[:200]}')


def run(dry_run: bool = False, min_lead: int = 5):
    today = _today_et()
    print(f'== Cross-sport POTD scan · {today} ==')

    mlb_row = _get_current_potd(today)
    mlb_conviction = (mlb_row or {}).get('data', {}).get('score', {}).get('total', 0) or 0
    mlb_sport = (mlb_row or {}).get('data', {}).get('sport', 'NONE')
    print(f'  Current best_bet_{today}: sport={mlb_sport} conviction={mlb_conviction}')

    candidates = _fetch_sport_candidates(today)
    print(f'  Cross-sport candidates (PRIME/STRONG): {len(candidates)}')
    if not candidates:
        print('  no non-MLB PRIME/STRONG picks today — leaving MLB POTD as-is')
        return 0

    # Apply LR gate to each candidate — updates conviction in-place
    scored = []
    for c in candidates:
        p, note = _score_with_lr(c)
        c['_lr_note'] = note
        scored.append(c)

    # Sort by (post-LR) conviction desc
    scored.sort(key=lambda x: -x['conviction'])
    print('  Top 5 cross-sport candidates after LR gate:')
    for c in scored[:5]:
        print(f'    {c["sport"]:6s} {c["tier"]:6s} c={c["conviction"]:>3d}  '
              f'{c["away_team"][:20]:20s} @ {c["home_team"][:20]:20s}  {c["_lr_note"]}')

    winner = scored[0]
    lead = winner['conviction'] - mlb_conviction
    print(f'\n  Cross-sport winner conviction: {winner["conviction"]} vs MLB POTD {mlb_conviction} '
          f'(lead={lead:+d}, min_lead={min_lead})')

    # If MLB POTD already IS the winner or the cross-sport lead isn't material, keep MLB
    if mlb_sport != 'MLB':
        print('  Current POTD is not MLB — skipping override to avoid stomping non-pipeline write')
        return 0
    if lead < min_lead:
        print(f'  Lead {lead} < threshold {min_lead} — keeping MLB POTD')
        return 0

    _publish_potd_override(today, winner, mlb_row, dry_run)
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--min-lead', type=int, default=5,
                    help='Minimum conviction lead over MLB POTD to trigger override')
    args = ap.parse_args()
    sys.exit(run(dry_run=args.dry_run, min_lead=args.min_lead))
