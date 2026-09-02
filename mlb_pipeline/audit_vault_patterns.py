"""Vault Match spot-check auditor.

Pulls recent graded games matching each pattern in PATTERN_CATALOG,
prints:
  - Which games fired the pattern
  - What outcome we graded them as
  - The raw game_results row (home_win, spread_result, total_result)
  - Wilson CI on the current sample

So the human can manually verify our matches_fn + outcome_fn agree
with reality before flipping the shadow-mode flag off.

USAGE:
    python audit_vault_patterns.py --sport MLB
    python audit_vault_patterns.py --sport MLB --pattern mlb_sharp_confirmed_prime
    python audit_vault_patterns.py --sport NFL --sample 10   # show 10 games per pattern
"""
import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

from compute_sport_patterns import PATTERN_CATALOG, fetch_games

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def audit(sport: str, pattern_filter: Optional[str], sample: int) -> None:
    patterns = [p for p in PATTERN_CATALOG
                if p['sport'] == sport.upper()
                and (not pattern_filter or p['key'] == pattern_filter)]
    if not patterns:
        print(f'no patterns matched: sport={sport} filter={pattern_filter}')
        return

    for p in patterns:
        print(f"\n{'='*70}")
        print(f"PATTERN  {p['sport']} · {p['key']}")
        print(f"LABEL    {p['label']}")
        print(f"DESC     {p['description']}")
        print(f"LOOKBACK {p['lookback_days']}d")
        print('='*70)

        games = fetch_games(p['sport'], p['lookback_days'])
        matched = []
        for g in games:
            try:
                if p['matches'](g):
                    matched.append(g)
            except Exception as e:
                print(f'  ⚠ matches_fn error on game {g.get("game_id")}: {e}')

        print(f"\n  {len(matched)} games matched (out of {len(games)} graded)")
        if not matched:
            continue

        w = l = pu = 0
        for g in matched:
            try:
                oc = p['outcome'](g)
            except Exception:
                oc = None
            if oc == 'W': w += 1
            elif oc == 'L': l += 1
            elif oc == 'P': pu += 1

        n = w + l
        pct = (100.0 * w / n) if n else 0.0
        lo, hi = _wilson_ci(w, n)
        print(f"  ROLLING  {w}W-{l}L-{pu}P  =  {pct:.1f}%  (n={n})")
        print(f"  WILSON   [{lo*100:.1f}%, {hi*100:.1f}%]  (95% CI)")
        below_juice = pct < 52.4
        below_wilson = lo < 0.55
        below_thresh = pct < 65.0 or n < 15
        badges = []
        if below_thresh: badges.append('BELOW_THRESHOLD')
        if below_juice:  badges.append('BELOW_JUICE_BE')
        if below_wilson: badges.append('WILSON_LO<55%')
        if badges:
            print(f"  STATUS   ⚠ {' · '.join(badges)}  → would NOT render badge")
        else:
            print(f"  STATUS   ✓ clears all guardrails  → WOULD render badge")

        print(f"\n  SAMPLE   (last {min(sample, len(matched))} matches — verify manually):")
        recent = sorted(matched, key=lambda g: g.get('game_date', ''), reverse=True)[:sample]
        for g in recent:
            gd = g.get('game_date', '?')
            hm = (g.get('home_team') or '?')[:20]
            aw = (g.get('away_team') or '?')[:20]
            hw = g.get('home_win')
            sr = str(g.get('spread_result') or '—').lower()[:14]
            tr = str(g.get('total_result') or '—').lower()[:10]
            try: oc = p['outcome'](g)
            except: oc = '?'
            print(f"    {gd}  {aw:20} @ {hm:20}  hw={str(hw):5}  spread={sr:14}  total={tr:10}  → {oc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, help='MLB / NFL / NCAAF')
    ap.add_argument('--pattern', help='Optional: audit only this pattern_key')
    ap.add_argument('--sample', type=int, default=5, help='How many recent matches to print (default 5)')
    args = ap.parse_args()
    audit(args.sport, args.pattern, args.sample)


if __name__ == '__main__':
    main()
