"""Universal team-form enricher (2026-08-18).

For each sport, for today's slate, compute per-game:
  - HOME team's last 10 records AT HOME (ATS, ML, OU)
  - AWAY team's last 10 records ON THE ROAD (ATS, ML, OU)
  - Head-to-head last 5 meetings (regardless of venue): home_wins,
    home_covers, overs, avg_total, avg_margin

Patches into {sport}_game_context. Uses each sport's results table as
the historical source.

User's ask (2026-08-18): "look at last X matchups against opponent...
Last 10 at home ATS, Last 10 at home ML (same for away). A quant would
look at these." Universal because the pattern applies to every sport
where team A plays team B at a venue and has a rolling record.

CLI:
    python enrich_team_form_universal.py                # today, all sports
    python enrich_team_form_universal.py --sport NCAAB
    python enrich_team_form_universal.py --date 2026-08-18
    python enrich_team_form_universal.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


# Sport → (context_table, results_table, home_score_col, away_score_col,
#         close_spread_col, close_total_col)
SPORT_CFG = {
    'MLB':   ('mlb_game_context',   'mlb_game_results',
              'home_score', 'away_score', 'close_spread', 'close_total'),
    'NFL':   ('nfl_game_context',   'nfl_game_results',
              'home_score', 'away_score', 'close_spread', 'close_total'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results',
              'home_score', 'away_score', 'close_spread', 'close_total'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results',
              'home_score', 'away_score', 'close_spread', 'close_total'),
    'NHL':   ('nhl_game_context',   'nhl_game_results',
              'home_score', 'away_score', 'close_spread', 'close_total'),
    'NBA':   ('nba_game_context',   'nba_game_results',
              'home_score', 'away_score', 'close_spread', 'close_total'),
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _grade_ats(home_score: float, away_score: float, close_spread: float | None,
                team_is_home: bool) -> str | None:
    """W = team covered, L = didn't, P = pushed. close_spread is HOME's
    line (negative if home favored)."""
    if home_score is None or away_score is None or close_spread is None: return None
    try: sp = float(close_spread)
    except (TypeError, ValueError): return None
    home_margin = home_score - away_score
    home_ats = home_margin + sp  # positive = home covers
    if abs(home_ats) < 0.01: return 'P'
    if team_is_home:
        return 'W' if home_ats > 0 else 'L'
    else:
        return 'W' if home_ats < 0 else 'L'


def _grade_ml(home_score: float, away_score: float, team_is_home: bool) -> str | None:
    if home_score is None or away_score is None: return None
    if home_score == away_score: return 'P'
    home_won = home_score > away_score
    if team_is_home:
        return 'W' if home_won else 'L'
    else:
        return 'W' if not home_won else 'L'


def _grade_ou(home_score: float, away_score: float, close_total: float | None) -> str | None:
    if home_score is None or away_score is None or close_total is None: return None
    try: tot = float(close_total)
    except (TypeError, ValueError): return None
    actual = home_score + away_score
    if abs(actual - tot) < 0.01: return 'P'
    return 'O' if actual > tot else 'U'


def compute_team_venue_form(sport: str, team_name: str, game_date: str,
                             is_home: bool, n: int = 10) -> dict:
    """Team's last N games AT HOME (if is_home=True) or ON THE ROAD.
    Returns {ats_wins, ats_losses, ml_wins, ml_losses, ou_overs, ou_unders,
             games_played}."""
    ctx_t, res_t, hs, as_, csp, cto = SPORT_CFG[sport]
    team_col = 'home_team' if is_home else 'away_team'

    r = requests.get(
        f'{SB}/rest/v1/{res_t}',
        headers=H_READ,
        params={
            f'{team_col}': f'eq.{team_name}',
            'game_date': f'lt.{game_date}',
            hs: 'not.is.null',
            'select': f'game_date,{hs},{as_},{csp},{cto}',
            'order': 'game_date.desc',
            'limit': str(n),
        },
        timeout=15,
    )
    games = r.json() if r.status_code == 200 else []

    ats_w = ats_l = ml_w = ml_l = overs = unders = 0
    for g in games:
        home = g.get(hs); away = g.get(as_)
        spread = g.get(csp); total = g.get(cto)
        # ATS
        ats = _grade_ats(home, away, spread, team_is_home=is_home)
        if ats == 'W': ats_w += 1
        elif ats == 'L': ats_l += 1
        # ML
        ml = _grade_ml(home, away, team_is_home=is_home)
        if ml == 'W': ml_w += 1
        elif ml == 'L': ml_l += 1
        # OU
        ou = _grade_ou(home, away, total)
        if ou == 'O': overs += 1
        elif ou == 'U': unders += 1

    return {
        'games_played': len(games),
        'ats_wins': ats_w, 'ats_losses': ats_l,
        'ml_wins': ml_w, 'ml_losses': ml_l,
        'overs': overs, 'unders': unders,
    }


def compute_h2h(sport: str, home_team: str, away_team: str, game_date: str,
                 n: int = 5) -> dict:
    """Last N meetings between these two teams regardless of venue.
    Returns {games_played, home_wins, home_covers, overs, avg_total,
             avg_margin} — home = today's home team specifically."""
    _, res_t, hs, as_, csp, cto = SPORT_CFG[sport]
    # Pull games where either team was home vs the other
    r = requests.get(
        f'{SB}/rest/v1/{res_t}',
        headers=H_READ,
        params={
            'or': f'(and(home_team.eq.{home_team},away_team.eq.{away_team}),'
                  f'and(home_team.eq.{away_team},away_team.eq.{home_team}))',
            'game_date': f'lt.{game_date}',
            hs: 'not.is.null',
            'select': f'game_date,home_team,away_team,{hs},{as_},{csp},{cto}',
            'order': 'game_date.desc',
            'limit': str(n),
        },
        timeout=15,
    )
    games = r.json() if r.status_code == 200 else []

    home_wins = home_covers = overs = 0
    total_sum = margin_sum = 0.0
    counted_total = counted_margin = 0
    for g in games:
        hs_v = g.get(hs); as_v = g.get(as_)
        if hs_v is None or as_v is None: continue
        # Was today's home_team the home OR away in this historical game?
        was_home = g.get('home_team') == home_team
        my_score = hs_v if was_home else as_v
        opp_score = as_v if was_home else hs_v
        # HOME (today's home team) wins
        if my_score > opp_score: home_wins += 1
        # HOME (today's home team) covers — need historical spread
        sp = g.get(csp)
        if sp is not None:
            try:
                sp_f = float(sp)
                # If today's home team was AWAY in that game, flip sign
                my_line = sp_f if was_home else -sp_f
                my_margin = my_score - opp_score
                cover_val = my_margin + my_line
                if cover_val > 0: home_covers += 1
            except (TypeError, ValueError):
                pass
        # OU
        tot = g.get(cto)
        if tot is not None:
            try:
                total_sum += hs_v + as_v
                counted_total += 1
                if hs_v + as_v > float(tot): overs += 1
            except (TypeError, ValueError):
                pass
        margin_sum += abs(my_score - opp_score)
        counted_margin += 1

    avg_total = round(total_sum / counted_total, 2) if counted_total else None
    avg_margin = round(margin_sum / counted_margin, 2) if counted_margin else None
    return {
        'games_played': len(games),
        'home_wins': home_wins, 'home_covers': home_covers,
        'overs': overs,
        'avg_total': avg_total, 'avg_margin': avg_margin,
    }


def enrich_game(sport: str, game: dict, dry_run: bool = False) -> bool:
    ctx_t = SPORT_CFG[sport][0]
    home = game.get('home_team'); away = game.get('away_team')
    gd = game.get('game_date')
    if not (home and away and gd): return False

    # Home team's L10 at home
    hh = compute_team_venue_form(sport, home, gd, is_home=True, n=10)
    # Away team's L10 on road
    aa = compute_team_venue_form(sport, away, gd, is_home=False, n=10)
    # H2H last 5
    h2h = compute_h2h(sport, home, away, gd, n=5)

    payload = {
        'home_ats_l10_at_home': hh['ats_wins'],
        'home_ats_l10_at_home_losses': hh['ats_losses'],
        'home_ml_l10_at_home': hh['ml_wins'],
        'home_ml_l10_at_home_losses': hh['ml_losses'],
        'home_ou_l10_at_home_overs': hh['overs'],
        'home_ou_l10_at_home_unders': hh['unders'],
        'away_ats_l10_on_road': aa['ats_wins'],
        'away_ats_l10_on_road_losses': aa['ats_losses'],
        'away_ml_l10_on_road': aa['ml_wins'],
        'away_ml_l10_on_road_losses': aa['ml_losses'],
        'away_ou_l10_on_road_overs': aa['overs'],
        'away_ou_l10_on_road_unders': aa['unders'],
        'h2h_last5_games_played': h2h['games_played'],
        'h2h_last5_home_wins': h2h['home_wins'],
        'h2h_last5_home_covers': h2h['home_covers'],
        'h2h_last5_overs': h2h['overs'],
        'h2h_last5_avg_total': h2h['avg_total'],
        'h2h_last5_avg_margin': h2h['avg_margin'],
        'team_form_enriched_at': datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        print(f'  [DRY] {away} @ {home}: H_hATS={hh["ats_wins"]}-{hh["ats_losses"]}  '
              f'A_rATS={aa["ats_wins"]}-{aa["ats_losses"]}  '
              f'H2H(n={h2h["games_played"]}): hW={h2h["home_wins"]} '
              f'hC={h2h["home_covers"]} O={h2h["overs"]} avgT={h2h["avg_total"]}')
        return True

    gid = game.get('game_id')
    if not gid: return False
    r = requests.patch(
        f'{SB}/rest/v1/{ctx_t}?game_id=eq.{gid}&game_date=eq.{gd}',
        headers=H_WRITE, json=payload, timeout=15,
    )
    ok = r.status_code in (200, 204)
    if not ok:
        print(f'  x patch {gid}: {r.status_code} {r.text[:150]}')
    return ok


def run(sport: str = None, game_date: str = None, dry_run: bool = False):
    gd = game_date or _et_today()
    sports = [sport] if sport else list(SPORT_CFG.keys())
    print(f'=== enrich_team_form_universal · {gd} · {"/".join(sports)}'
          f'{" [DRY]" if dry_run else ""} ===')
    for s in sports:
        ctx_t = SPORT_CFG[s][0]
        r = requests.get(
            f'{SB}/rest/v1/{ctx_t}',
            headers=H_READ,
            params={'game_date': f'eq.{gd}',
                    'select': 'game_id,game_date,home_team,away_team',
                    'limit': '200'},
            timeout=20,
        )
        games = r.json() if r.status_code == 200 else []
        if not games:
            print(f'  {s}: no games on {gd}'); continue
        ok = 0
        for g in games:
            if enrich_game(s, g, dry_run=dry_run): ok += 1
        print(f'  {s}: enriched {ok}/{len(games)}')


def backfill_days(sport: str, days: int, dry_run: bool = False):
    """Populate the venue-split + H2H columns for the last N days of games.
    Needed after the migration lands so backfill_signal_tiers can grade the
    new signals against real historical fire data (otherwise every new
    signal has n=0 and stays UNVALIDATED forever)."""
    from datetime import date as _date
    sports = [sport] if sport else list(SPORT_CFG.keys())
    print(f'=== enrich_team_form_universal BACKFILL · last {days} days · '
          f'{"/".join(sports)}{" [DRY]" if dry_run else ""} ===')
    for s in sports:
        ctx_t = SPORT_CFG[s][0]
        total_enriched = 0
        total_days = 0
        for d_off in range(1, days + 1):
            gd = (_date.today() - timedelta(days=d_off)).isoformat()
            r = requests.get(
                f'{SB}/rest/v1/{ctx_t}',
                headers=H_READ,
                params={'game_date': f'eq.{gd}',
                        'select': 'game_id,game_date,home_team,away_team',
                        'limit': '200'},
                timeout=20,
            )
            games = r.json() if r.status_code == 200 else []
            if not games: continue
            for g in games:
                if enrich_game(s, g, dry_run=dry_run): total_enriched += 1
            total_days += 1
        print(f'  {s}: enriched {total_enriched} games across {total_days} dates')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORT_CFG.keys()))
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--backfill-days', type=int,
                   help='Populate the last N days retroactively (for signal_tiers backfill priming)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if args.backfill_days:
        backfill_days(sport=args.sport, days=args.backfill_days, dry_run=args.dry_run)
    else:
        run(sport=args.sport, game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
