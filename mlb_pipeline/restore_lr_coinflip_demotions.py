#!/usr/bin/env python3
"""Restore picks that lr_v1 demoted on a coin flip despite a positive edge.

Andy 2026-10-29: "why dont we have an opinion yet why cant we give locks
high confidence pick i am losing customers by the day because of the
complete lack of conviction in anything."

A large part of the answer was `_apply_ml_lr_override_impl`: when the
logistic model landed in [0.45,0.55] it demoted the ensemble's ML pick to
COVERAGE regardless of how good the price was. Measured on the 10-30-onward
board it had demoted 43 NFL and 7 NCAAF picks, including:

    Air Force ML  LEAN   conv 69  edge +16.6pp   LR p=0.48
    Iowa ML       STRONG conv 78  edge +15.0pp   LR p=0.51
    KC ML         LEAN   conv 64  edge  +9.9pp   LR p=0.52

`_model_edge_pp` is our win probability minus the price's implied
probability. Positive means we disagree with the MARKET, which is the only
disagreement that pays. A second model with no opinion is not evidence
against it -- and that override is measured to LOSE where judgeable, 42.0%
vs the ensemble's 57.1% on n=50 (project_lr_v1_override_erases_attribution).

defensive_gates.py now refuses that demotion on a positive edge, but the gate
only runs on generation. This repairs the rows already written. It restores
`tier` from `_pre_lr_tier` ONLY where the stored edge is positive, so a
negative or missing edge stays demoted exactly as before.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY'])
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}
TABLES = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
          'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
          'MLB': 'mlb_game_context'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(TABLES))
    ap.add_argument('--since', default=None, help='game_date >= (default today)')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    since = a.since or __import__('datetime').date.today().isoformat()
    tbl = TABLES[a.sport]
    rows = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
                        params={'select': 'game_id,game_date,home_team,away_team,primary_play',
                                'game_date': f'gte.{since}', 'order': 'game_date.asc'},
                        timeout=60).json()
    print(f'=== restore_lr_coinflip · {a.sport} · from {since}'
          f'{"" if a.apply else " [DRY RUN]"} ===')
    fixed = skipped = 0
    for r in rows:
        pp = r.get('primary_play') or {}
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except Exception: continue
        if not isinstance(pp, dict): continue
        note = str(pp.get('audit_note') or '')
        if 'coin flip' not in note.lower(): continue
        if str(pp.get('tier')) not in ('COVERAGE', 'PASS'): continue
        prior = pp.get('_pre_lr_tier')
        try:
            edge = pp.get('_model_edge_pp')
            edge = float(edge) if edge is not None else None
        except (TypeError, ValueError):
            edge = None
        lbl = f"{r['game_date']} {str(r['away_team'])[:14]:14s}@{str(r['home_team'])[:14]:14s} {str(pp.get('label'))[:18]:18s}"
        if edge is None or edge <= 0:
            skipped += 1
            continue
        if prior in (None, '', 'COVERAGE', 'PASS'):
            print(f'  {lbl} edge={edge:+.1f}pp but pre_lr tier was {prior!r} - nothing to restore')
            skipped += 1
            continue
        print(f'  {lbl} edge={edge:+6.1f}pp  COVERAGE -> {prior}')
        fixed += 1
        if not a.apply: continue
        new = dict(pp)
        new['tier'] = prior
        new['_lr_coin_flip_overruled'] = {
            'p_home_win': (pp.get('_lr_ml_shadow') or {}).get('p_home_win'),
            'model_edge_pp': round(edge, 2),
            'restored_from': 'COVERAGE',
            'reason': 'LR had no opinion; the ensemble has a positive edge '
                      'over the price, so the tier stands'}
        new['audit_note'] = (f'{note} | OVERRULED 2026-10-29: edge {edge:+.1f}pp '
                             f'over the price, tier restored to {prior}')
        resp = requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{r["game_id"]}',
                              headers=H_WRITE, json={'primary_play': new}, timeout=20)
        if resp.status_code not in (200, 204):
            print(f'    ! patch failed {resp.status_code}: {resp.text[:140]}')
            fixed -= 1
    print(f'  restored {fixed}; left demoted (edge non-positive or missing) {skipped}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
