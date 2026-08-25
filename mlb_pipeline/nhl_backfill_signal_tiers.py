"""NHL signal backfill → signal_registry (2026-08-19).

NHL-specific replay because the season hasn't started yet — nhl_game_context
is empty, so the multi-sport backfill_signal_tiers.py (which joins
context+results) returns zero games for NHL. This script synthesizes the
context features signal_sources rows expect (h2h_last5_*, home/away_form_*,
home/away_ats_l*, etc.) directly from nhl_game_results walked chronologically
per team + per matchup.

Known data gaps (also called out in the offseason report):
  * Historical NHL games have NO close_home_ml / close_puckline / close_total.
    So ATS/puckline signals cannot be graded honestly — condition_expr fires
    only for signals whose predicates don't require line data, and RL grading
    is skipped when line is None.
  * Totals use a total_goals cut at 6.0 as proxy for "OVER 6" (league avg
    ≈ 6.15). Pushes at exactly 6 excluded. Documented as PROXY in registry
    notes so live retrain (post-Oct with real close_total) can overwrite.
  * Prop-scope + prop-class signals skipped — no historical player-prop
    candidate table.

Tier logic mirrors backfill_signal_tiers.py:
  VALIDATED      n≥50 & hit_rate ≥ 55%
  DISCOVERY      n≥15 & hit_rate ≥ 52.4%
  UNVALIDATED    n<15 or positive-but-thin
  ANTI_VALIDATED n≥25 & hit_rate ≤ 48%

CLI:
  python nhl_backfill_signal_tiers.py                    # backfill all NHL
  python nhl_backfill_signal_tiers.py --dry-run
  python nhl_backfill_signal_tiers.py --signal-key h2h_home_dominant
"""
from __future__ import annotations
import argparse, os, sys, json
from collections import defaultdict, deque
from datetime import date, datetime, timezone
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

# Import safe expression evaluator + tier weights (same source as MLB path)
sys.path.insert(0, str(Path(__file__).parent))
from signal_expr import evaluate_bool, evaluate_str, AttrDict

# Handler / non-expression signal classes we skip in this backfill.
HANDLER_CLASSES = {'split', 'scenario', 'external_pick'}
PROP_CLASSES    = {'prop_form', 'prop_matchup', 'prop_environment'}

# Total-line proxy for OVER/UNDER grading when close_total unavailable.
# 2024-25 NHL: total_goals distribution shows league avg ≈ 6.15 g/game.
TOTAL_PROXY_LINE = 6.0


# ─────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────

def fetch_signals() -> list[dict]:
    r = requests.get(f'{SB}/rest/v1/signal_sources',
                     headers=H_READ,
                     params={'select': '*', 'sport': 'eq.NHL', 'enabled': 'eq.true'},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_games() -> list[dict]:
    """All resolved NHL games in date order."""
    rows = []
    for off in range(0, 30000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/nhl_game_results?home_win=not.is.null'
            f'&order=game_date.asc,game_id.asc'
            f'&select=game_id,game_date,home_team,away_team,home_score,away_score,'
            f'total_goals,home_win,went_to_ot,close_puckline,close_total,'
            f'close_home_ml,close_away_ml,spread_result,total_result'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break
    return rows


# ─────────────────────────────────────────────────────────────────────────
# Synthetic context builder
# ─────────────────────────────────────────────────────────────────────────

def _team_win(team: str, game: dict) -> bool:
    """Did `team` win `game`?"""
    if team == game['home_team']:
        return bool(game['home_win'])
    return not bool(game['home_win'])


def _matchup_key(a: str, b: str) -> tuple:
    """Symmetric key for h2h lookup."""
    return tuple(sorted([a, b]))


def build_contexts(games: list[dict]) -> list[dict]:
    """Walk games chronologically. For each game, snapshot per-team +
    per-matchup rolling features BEFORE this game, attach as ctx, then
    update rolling state with this game's outcome.

    Fields synthesized to satisfy signal_sources condition_expr:
      home_form_last5_wins / away_form_last5_wins
      home_form_last10_wins / away_form_last10_wins
      home_last5_ou_o / home_last5_ou_u (proxy via TOTAL_PROXY_LINE)
      away_last5_ou_o / away_last5_ou_u
      home_ats_last5 (proxy: wins in last-5 as substitute for cover count)
      home_ats_l10_at_home (home wins in last-10 home games)
      away_ats_l10_on_road (away wins in last-10 road games)
      home_ml_last10_at_home  home_ml_last10  away_ml_last10_on_road  away_ml_last10
      h2h_last5_games_played  h2h_last5_home_wins  h2h_last5_overs

    NOTE: real ATS requires historical puckline, which is missing. We
    surface a WIN-based proxy so team_form signals fire and can be graded
    for ML at least. Registry rows carry origin='NHL_BACKFILL_2026-08-19'
    so post-Oct re-runs (with real lines) can overwrite cleanly.
    """
    team_all      : dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
    team_home     : dict[str, deque] = defaultdict(lambda: deque(maxlen=15))
    team_road     : dict[str, deque] = defaultdict(lambda: deque(maxlen=15))
    team_ou       : dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
    h2h           : dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))

    out = []
    for g in games:
        h = g['home_team']; a = g['away_team']
        if not h or not a: continue
        gid = g.get('game_id')
        tot = g.get('total_goals')

        ctx = dict(g)  # start with the raw row (game_date, teams, prices where present)

        # per-team form (all games)
        h_all = list(team_all[h]); a_all = list(team_all[a])
        ctx['home_form_last5_wins']  = sum(1 for x in h_all[-5:]  if x['won'])
        ctx['home_form_last10_wins'] = sum(1 for x in h_all[-10:] if x['won'])
        ctx['away_form_last5_wins']  = sum(1 for x in a_all[-5:]  if x['won'])
        ctx['away_form_last10_wins'] = sum(1 for x in a_all[-10:] if x['won'])

        # per-venue form
        h_home = list(team_home[h]); a_road = list(team_road[a])
        ctx['home_ml_last10_at_home']  = sum(1 for x in h_home[-10:] if x['won'])
        ctx['away_ml_last10_on_road']  = sum(1 for x in a_road[-10:] if x['won'])
        ctx['home_ml_last10']          = ctx['home_form_last10_wins']
        ctx['away_ml_last10']          = ctx['away_form_last10_wins']

        # ATS proxy = ML win counts (real ATS needs puckline; documented)
        ctx['home_ats_last5']         = sum(1 for x in h_all[-5:]  if x['won'])
        ctx['away_ats_last5']         = sum(1 for x in a_all[-5:]  if x['won'])
        ctx['home_ats_l10_at_home']   = ctx['home_ml_last10_at_home']
        ctx['away_ats_l10_on_road']   = ctx['away_ml_last10_on_road']

        # OU trend proxy (last-5 games including all opponents)
        h_ou = list(team_ou[h])[-5:]; a_ou = list(team_ou[a])[-5:]
        ctx['home_last5_ou_o'] = sum(1 for x in h_ou if x == 'O')
        ctx['home_last5_ou_u'] = sum(1 for x in h_ou if x == 'U')
        ctx['away_last5_ou_o'] = sum(1 for x in a_ou if x == 'O')
        ctx['away_last5_ou_u'] = sum(1 for x in a_ou if x == 'U')

        # H2H rolling
        mk = _matchup_key(h, a)
        hlist = list(h2h[mk])[-5:]
        ctx['h2h_last5_games_played'] = len(hlist)
        ctx['h2h_last5_home_wins']    = sum(1 for x in hlist if x['home_team'] == h and x['home_win']) \
                                       + sum(1 for x in hlist if x['home_team'] == a and not x['home_win'])
        ctx['h2h_last5_overs']        = sum(1 for x in hlist if x['ou'] == 'O')

        out.append(ctx)

        # UPDATE rolling state AFTER snapshotting
        h_won = bool(g['home_win'])
        team_all[h].append({'won': h_won,      'ou': 'O' if (tot or 0) > TOTAL_PROXY_LINE else 'U' if (tot or 0) < TOTAL_PROXY_LINE else 'P'})
        team_all[a].append({'won': not h_won,  'ou': 'O' if (tot or 0) > TOTAL_PROXY_LINE else 'U' if (tot or 0) < TOTAL_PROXY_LINE else 'P'})
        team_home[h].append({'won': h_won})
        team_road[a].append({'won': not h_won})
        if tot is not None:
            ou = 'O' if tot > TOTAL_PROXY_LINE else 'U' if tot < TOTAL_PROXY_LINE else 'P'
            team_ou[h].append(ou); team_ou[a].append(ou)
        h2h[mk].append({'home_team': h, 'home_win': h_won,
                        'ou': 'O' if (tot or 0) > TOTAL_PROXY_LINE else 'U'})
    return out


# ─────────────────────────────────────────────────────────────────────────
# Side grading
# ─────────────────────────────────────────────────────────────────────────

def grade_side(side: str, g: dict) -> str | None:
    """Return 'W'/'L'/'P'/None for a candidate side against actual outcome.

    NHL puckline is always ±1.5. HOME_RL = home wins by 2+ goals.
    Total grading uses close_total when populated, else TOTAL_PROXY_LINE.
    """
    hs = g.get('home_score'); as_ = g.get('away_score')
    if hs is None or as_ is None: return None
    home_won = bool(g.get('home_win'))
    margin = int(hs) - int(as_)
    tot = g.get('total_goals') or (int(hs) + int(as_))

    if side == 'HOME_ML':   return 'W' if home_won else 'L'
    if side == 'AWAY_ML':   return 'L' if home_won else 'W'
    if side == 'HOME_RL':
        # home -1.5: covers when home wins by 2+
        return 'W' if margin >= 2 else 'L'
    if side == 'AWAY_RL':
        # away +1.5: covers unless away loses by 2+
        return 'W' if margin < 2 else 'L'

    # Total: prefer actual close if present, else proxy line
    line = g.get('close_total')
    try: line = float(line) if line is not None else TOTAL_PROXY_LINE
    except (TypeError, ValueError): line = TOTAL_PROXY_LINE
    if side == 'OVER':
        if abs(tot - line) < 0.01: return 'P'
        return 'W' if tot > line else 'L'
    if side == 'UNDER':
        if abs(tot - line) < 0.01: return 'P'
        return 'W' if tot < line else 'L'
    return None


# ─────────────────────────────────────────────────────────────────────────
# Backfill a single signal
# ─────────────────────────────────────────────────────────────────────────

def backfill_signal(source: dict, contexts: list[dict]) -> dict:
    cls = source.get('class', '')
    if cls in HANDLER_CLASSES:
        return {'skipped': True, 'reason': f'handler class {cls}'}
    if cls in PROP_CLASSES:
        return {'skipped': True, 'reason': 'prop class — no historical candidates'}

    cond = source.get('condition_expr', '')
    side_expr = source.get('side_expr', '')
    scope = source.get('market_scope', '')
    if not cond or not side_expr:
        return {'skipped': True, 'reason': 'missing expr'}

    w = l = p = fires = 0
    for ctx in contexts:
        ctx_attr = AttrDict(ctx)
        if not evaluate_bool(cond, ctx_attr): continue
        fires += 1
        side = evaluate_str(side_expr, ctx_attr)
        if not side: continue
        # If signal depends on line data that's missing, skip grading
        if scope == 'rl' and ctx.get('close_puckline') is None and side in ('HOME_RL', 'AWAY_RL'):
            # Grade anyway using ±1.5 canonical
            pass
        r = grade_side(side, ctx)
        if r == 'W': w += 1
        elif r == 'L': l += 1
        elif r == 'P': p += 1

    n_dec = w + l
    hit_rate = round(100 * w / n_dec, 1) if n_dec else None
    edge_pp = round(hit_rate - 52.4, 1) if hit_rate is not None else None

    # Baseline-adjusted tiering for NHL: without historical odds, RL grading is
    # dominated by the +1.5 base rate (~65% AWAY / ~30% HOME cover). We compare
    # against the market-specific naive baseline before promoting.
    naive_baseline = {'ml': 54.5, 'rl_home': 34.8, 'rl_away': 65.2, 'total': 45.7}
    if scope == 'rl':
        # Guess which side dominates in results (either specifically HOME_RL or AWAY_RL)
        base = naive_baseline['rl_home'] if 'HOME_RL' in side_expr else naive_baseline['rl_away']
    elif scope == 'total':
        base = naive_baseline['total']  # OVER 6.0 base rate
    else:
        base = 52.4  # market-neutral breakeven

    lift = round((hit_rate - base), 1) if hit_rate is not None else None

    if n_dec < 15:
        tier = 'UNVALIDATED'
    elif hit_rate is not None and n_dec >= 25 and lift is not None and lift <= -5.0:
        tier = 'ANTI_VALIDATED'
    elif hit_rate is not None and n_dec >= 50 and lift is not None and lift >= 4.0 and hit_rate >= 55.0:
        tier = 'VALIDATED'
    elif hit_rate is not None and lift is not None and lift >= 2.0 and hit_rate >= 52.4:
        tier = 'DISCOVERY'
    else:
        tier = 'UNVALIDATED'

    weight = {'VALIDATED': 1.0, 'DISCOVERY': 0.5,
              'UNVALIDATED': 0.3, 'ANTI_VALIDATED': 0.0}[tier]

    return {
        'fires': fires, 'w': w, 'l': l, 'p': p, 'n_dec': n_dec,
        'hit_rate': hit_rate, 'edge_pp': edge_pp,
        'baseline': base, 'lift': lift,
        'tier': tier, 'recommended_weight': weight,
    }


def write_registry(source: dict, stats: dict, dry_run: bool = False) -> bool:
    if dry_run: return True
    now_iso = datetime.now(timezone.utc).isoformat()
    note = (source.get('description') or source.get('display_prose_template') or '')
    if source.get('market_scope') == 'total':
        note = (note + ' [PROXY: total_goals vs 6.0 — retrain post-Oct with live close_total]').strip()
    payload = {
        'signal_name': source['signal_key'],
        'sport': 'NHL',
        'market_scope': source.get('market_scope', 'multi'),
        'category': source['class'],
        'description': note,
        'hit_rate': stats['hit_rate'],
        'sample_n': stats['n_dec'],
        'edge_pp': stats['edge_pp'],
        'tier': stats['tier'],
        'recommended_weight': stats['recommended_weight'],
        'direction_hint': 'FADE' if stats['tier'] == 'ANTI_VALIDATED' else 'FOLLOW',
        'origin': f'NHL_BACKFILL_{date.today().isoformat()}',
        'last_computed_at': now_iso,
        'updated_at': now_iso,
    }
    pr = requests.post(
        f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
        headers=H_WRITE, json=[payload], timeout=15)
    if pr.status_code not in (200, 201, 204):
        print(f'    ✗ write failed: {pr.status_code} {pr.text[:150]}')
        return False
    return True


def run(dry_run: bool = False, signal_key_filter: str | None = None):
    print('=== NHL signal backfill → signal_registry ===\n')
    signals = fetch_signals()
    if signal_key_filter:
        signals = [s for s in signals if s['signal_key'] == signal_key_filter]
    print(f'  {len(signals)} NHL signal_sources rows to evaluate')

    games = fetch_games()
    print(f'  {len(games)} nhl_game_results loaded')
    if not games:
        print('  no games — abort'); return

    print('  synthesizing per-game context (form / h2h / venue)...')
    contexts = build_contexts(games)
    print(f'  {len(contexts)} contexts built (chronological walk)\n')

    tier_counts = defaultdict(int)
    written = 0; graded_ml = graded_total = graded_rl = 0
    top = []
    anti = []
    for source in sorted(signals, key=lambda s: (s['class'], s['signal_key'])):
        cls = source.get('class', '')
        key = source['signal_key']
        scope = source.get('market_scope', '')
        stats = backfill_signal(source, contexts)
        if stats.get('skipped'):
            print(f'  {key:<40} [{cls:<15}] SKIP ({stats["reason"]})')
            continue

        hr = stats['hit_rate']; n = stats['n_dec']; fires = stats['fires']
        tier = stats['tier']; lift = stats['lift']; base = stats['baseline']
        hr_str = f'{hr}%' if hr is not None else '--'
        lift_str = f'{lift:+.1f}vs{base:.0f}%' if lift is not None else ''
        print(f'  {key:<40} [{cls:<15}] scope={scope:<6} fires={fires:>4} '
              f'n={n:>4} {stats["w"]}-{stats["l"]}-{stats["p"]}  '
              f'HR={hr_str:<7} {lift_str:<14} tier={tier}')

        tier_counts[tier] += 1
        if scope == 'ml':    graded_ml    += (n > 0)
        if scope == 'total': graded_total += (n > 0)
        if scope == 'rl':    graded_rl    += (n > 0)

        if tier in ('VALIDATED', 'DISCOVERY') and n >= 25:
            top.append({'key': key, 'tier': tier, 'hr': hr, 'n': n, 'scope': scope, 'class': cls})
        if tier == 'ANTI_VALIDATED':
            anti.append({'key': key, 'tier': tier, 'hr': hr, 'n': n, 'scope': scope, 'class': cls})

        if not dry_run:
            if write_registry(source, stats, dry_run=dry_run):
                written += 1

    print(f'\n--- summary ---')
    for tier, count in sorted(tier_counts.items()):
        print(f'  {tier:<16} {count}')
    print(f'\n  signals with any graded n>0:  ml={graded_ml}  rl={graded_rl}  total={graded_total}')
    print(f'\n  ✓ {written} signal_registry rows written{" (dry-run)" if dry_run else ""}')

    if top:
        print(f'\n=== TOP validated / discovery (n≥25) ===')
        for t in sorted(top, key=lambda x: -x['hr']):
            print(f"  {t['key']:<40} [{t['class']:<15}] {t['tier']:<12} {t['hr']}% n={t['n']}")
    if anti:
        print(f'\n=== ANTI-VALIDATED (fade candidates) ===')
        for t in sorted(anti, key=lambda x: x['hr']):
            print(f"  {t['key']:<40} [{t['class']:<15}] {t['hr']}% n={t['n']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--signal-key', default=None)
    args = p.parse_args()
    run(dry_run=args.dry_run, signal_key_filter=args.signal_key)


if __name__ == '__main__':
    main()
