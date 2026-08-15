"""compute_clv — closing line value per pick, sport-universal.

After a game is graded, walks every pick we made on it (from primary_play,
supplementary, prop cards) and computes:
  clv = (closing_number - our_number) × pick_direction

Positive clv = we beat the close (market moved toward us — sharp direction)
Negative clv = market moved against us (either late news or sharps disagreed)

CLV is the industry-standard proxy for pick quality that's independent
of win/loss variance. A pick can lose but still have +CLV (line still
moved sharp with us). Over enough n, per-model CLV correlates with actual
long-term ROI.

Writes to clv_snapshots. Sport-universal — pick sources vary by sport
but the CLV math is identical.

CLV conventions:
  spread pick   clv = (our_spread - close_spread) × sign
                  where sign = +1 backing home (spread negative for fav)
                  or -1 backing away. Basically: did we get a better line?
  total pick    clv = (close_total - our_total) if OVER
                       (our_total - close_total) if UNDER
  ml pick       clv = decimal_at_pick - decimal_at_close
                       (positive = we got a better price)

CLI
  python compute_clv.py                    # today's graded games, all sports
  python compute_clv.py --sport MLB
  python compute_clv.py --date 2026-08-14
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Per-sport context table + closing-line column names
SPORT_TABLE = {
    'MLB':   ('mlb_game_context',   'close_total', 'close_spread', 'home_ml_close', 'away_ml_close'),
    'NFL':   ('nfl_game_context',   'close_total', 'close_spread', 'close_home_ml', 'close_away_ml'),
    'NCAAF': ('ncaaf_game_context', 'close_total', 'close_spread', 'close_home_ml', 'close_away_ml'),
    'NCAAB': ('ncaab_game_context', 'close_total', 'close_spread', 'close_home_ml', 'close_away_ml'),
    'NHL':   ('nhl_game_context',   'close_total', 'close_spread', 'close_home_ml', 'close_away_ml'),
}

RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NHL':   'nhl_game_results',
}


def american_to_decimal(am: int | None) -> float | None:
    if am is None: return None
    try: am = int(am)
    except (TypeError, ValueError): return None
    if am >= 100:  return 1 + am / 100.0
    if am <= -100: return 1 + 100.0 / abs(am)
    return None


def compute_spread_clv(our_spread: float, close_spread: float, pick_side: str) -> float:
    """CLV for a spread pick.

    We picked HOME @ our_spread. If our_spread was -3 and close is -2.5,
    we got a better line (line got worse for HOME) → +0.5 clv.
    If our_spread was -3 and close is -3.5, we got a worse line → -0.5.
    """
    if pick_side == 'HOME':
        return round(close_spread - our_spread, 2)
    else:  # AWAY — spreads flip sign
        return round(our_spread - close_spread, 2)


def compute_total_clv(our_total: float, close_total: float, pick_side: str) -> float:
    """CLV for a total pick. OVER wants close_total higher than our_total."""
    if pick_side == 'OVER':
        return round(close_total - our_total, 2)
    else:  # UNDER
        return round(our_total - close_total, 2)


def compute_ml_clv(our_odds: int, close_odds: int) -> float | None:
    """CLV for a ML pick — decimal odds delta. Positive = we got a better price."""
    d_our = american_to_decimal(our_odds)
    d_close = american_to_decimal(close_odds)
    if d_our is None or d_close is None: return None
    return round(d_our - d_close, 3)


def fetch_picks(sport: str, game_date: str) -> list:
    """Return all picks for graded games on this date, all models.

    Sources:
      * mlb_game_context.primary_play + supplementary_play (spread/total/ml)
      * mlb_props (prop cards — market='total'/'ml'/etc based on prop type)

    Different sports have different pick storage; MLB is the reference,
    others follow the same pattern with sport-specific table names.
    """
    ctx_tbl = SPORT_TABLE[sport][0]
    # Pull today's rows with pick info + closing lines
    fields = ['game_id', 'home_team', 'away_team',
              'primary_play', 'supplementary_play',
              'close_total', 'close_spread',
              'close_locked_at']
    if sport == 'MLB':
        fields += ['home_ml_close', 'away_ml_close']
    else:
        fields += ['close_home_ml', 'close_away_ml']
    r = requests.get(
        f'{SB}/rest/v1/{ctx_tbl}'
        f'?select={",".join(fields)}&game_date=eq.{game_date}',
        headers=H_READ, timeout=20)
    if r.status_code != 200:
        print(f'  ✗ ctx fetch {r.status_code}: {r.text[:150]}')
        return []
    return r.json() or []


def _extract_close_ml(ctx: dict, sport: str, side: str) -> int | None:
    """Read close_home_ml / close_away_ml — handles MLB naming variant."""
    if sport == 'MLB':
        col = 'home_ml_close' if side == 'HOME' else 'away_ml_close'
    else:
        col = 'close_home_ml' if side == 'HOME' else 'close_away_ml'
    val = ctx.get(col)
    try: return int(val) if val is not None else None
    except (TypeError, ValueError): return None


def build_clv_rows(sport: str, ctx: dict) -> list:
    """Extract every pick from a ctx row and compute CLV per pick."""
    game_date = str(ctx.get('game_date') or datetime.now().date().isoformat())
    gid = ctx.get('game_id')
    close_total = ctx.get('close_total')
    close_spread = ctx.get('close_spread')
    home_close_ml = _extract_close_ml(ctx, sport, 'HOME')
    away_close_ml = _extract_close_ml(ctx, sport, 'AWAY')

    rows = []
    for play_field, tier_hint in (('primary_play', 'PRIME'),
                                    ('supplementary_play', 'STRONG')):
        play = ctx.get(play_field)
        if not play or not isinstance(play, dict):
            continue
        # play is a dict like {"type": "total", "side": "OVER", "line": 8.5, "odds": -110, ...}
        ptype = (play.get('type') or play.get('market') or '').lower()
        side  = (play.get('side') or play.get('pick_side') or '').upper()
        our_number = play.get('line') or play.get('number') or play.get('total') or play.get('spread')
        our_odds   = play.get('odds')
        our_tier   = play.get('tier') or tier_hint
        our_model  = play.get('model') or 'engine'

        try: our_number = float(our_number) if our_number is not None else None
        except (TypeError, ValueError): our_number = None

        clv = None; close_number = None; close_odds = None
        if ptype == 'total' and our_number is not None and close_total is not None:
            close_number = float(close_total)
            clv = compute_total_clv(our_number, close_number, side)
        elif ptype in ('spread', 'runline', 'puckline') and our_number is not None and close_spread is not None:
            close_number = float(close_spread)
            clv = compute_spread_clv(our_number, close_number, side)
        elif ptype == 'ml':
            close_odds = home_close_ml if side == 'HOME' else away_close_ml
            if our_odds is not None and close_odds is not None:
                clv = compute_ml_clv(int(our_odds), close_odds)

        if clv is None:
            continue

        rows.append({
            'sport':         sport,
            'game_id':       gid,
            'game_date':     game_date,
            'market':        ptype,
            'pick_side':     side,
            'our_number':    our_number,
            'our_odds':      int(our_odds) if our_odds is not None else None,
            'our_tier':      our_tier,
            'our_model':     our_model,
            'close_number':  close_number,
            'close_odds':    close_odds,
            'clv':           clv,
            'clv_direction': 'FOR' if clv > 0 else ('AGAINST' if clv < 0 else 'EVEN'),
        })
    return rows


def run_sport(sport: str, game_date: str, dry_run: bool = False) -> int:
    if sport not in SPORT_TABLE:
        print(f'  {sport}: skipped (no context table configured)')
        return 0
    ctxs = fetch_picks(sport, game_date)
    print(f'  {sport}: {len(ctxs)} games')
    all_rows = []
    for ctx in ctxs:
        all_rows.extend(build_clv_rows(sport, ctx))
    if not all_rows:
        print(f'  {sport}: 0 picks with computable CLV')
        return 0
    if dry_run:
        for r in all_rows[:5]:
            print(f'    [DRY] {r["market"]:<6} {r["pick_side"]:<6} '
                  f'our={r["our_number"]} close={r["close_number"]} '
                  f'clv={r["clv"]:+.2f} ({r["clv_direction"]})')
        print(f'    [DRY] {len(all_rows)} rows total')
        return len(all_rows)

    r = requests.post(
        f'{SB}/rest/v1/clv_snapshots'
        f'?on_conflict=sport,game_id,market,pick_side,our_model',
        headers=H_WRITE, json=all_rows, timeout=30)
    if r.status_code in (200, 201, 204):
        # Distribution snapshot
        pos = sum(1 for x in all_rows if x['clv'] > 0)
        neg = sum(1 for x in all_rows if x['clv'] < 0)
        print(f'  ✓ {sport}: {len(all_rows)} rows · +CLV={pos} · -CLV={neg}')
        return len(all_rows)
    print(f'  ✗ {sport}: {r.status_code} {r.text[:200]}')
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORT_TABLE.keys()) + ['ALL'], default='ALL')
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    game_date = args.date or datetime.now().date().isoformat()
    sports = list(SPORT_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    print(f'=== compute_clv · {"/".join(sports)} · {game_date} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    total = 0
    for s in sports:
        total += run_sport(s, game_date, dry_run=args.dry_run)
    print(f'\n  ✓ {total} CLV rows written')


if __name__ == '__main__':
    main()
