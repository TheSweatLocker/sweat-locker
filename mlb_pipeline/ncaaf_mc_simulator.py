"""NCAAF Monte Carlo simulator (2026-09-01).

5th lens for NCAAF's model stack — mirrors NFL/MLB MC pattern. Runs
10,000-game Monte Carlo per matchup using the ctx's already-computed
`projected_spread` + `projected_total` (which fold in SP+, EPA,
returning production, HFA, neutral-site handling) as expected values.
Writes `mc_probabilities` JSONB blob to `ncaaf_game_context`.

Design choice — REUSE ctx projections, don't recompute:
  ncaaf_game_context.py already produces `projected_spread` (home-
  perspective margin) and `projected_total`. Those go through all the
  sport-specific adjustments — SP+ gap × K_PTS_SP + HFA, EPA fallback,
  returning-production tilt, neutral-site flag. Re-implementing that
  math here would drift over time. Instead we take the ctx projections
  as our expected values and add MC variance around them. The MC blob
  becomes a "how tight are these projections" chip alongside the model
  point estimate.

MC constants (calibrated to CFB, distinct from NFL):
  LEAGUE_AVG_PPG      ≈ 29    (CFB scores higher than NFL — bigger
                                dispersion between elite + FCS-adjacent)
  GAME_STDDEV         ≈ 13.5  (CFB has more variance than NFL's 10.5;
                                garbage-time swings, wider talent gap)
  HFA_POINTS          ≈ 3.0   (already baked into projected_spread by
                                ctx — do NOT double-count)
  N_SIMS              = 10000
  MIN_PROJ_CONFIDENCE = require both projected_spread + projected_total
                        present. Skip when either is null (Week 1 games
                        without SP+ baseline, FCS opponents, etc.)

Outputs (same shape as NFL/MLB MC blobs so LensGrid renders uniformly):
  mc_p_home, mc_p_away, mc_expected_margin, mc_expected_total,
  mc_stddev_margin, mc_p_over_line, mc_confidence_high, generated_at

CLI:
    python ncaaf_mc_simulator.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, math, random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# NCAAF-tuned MC constants
LEAGUE_AVG_PPG = 29.0
GAME_STDDEV = 13.5
N_SIMS = 10000


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _f(v) -> Optional[float]:
    try:
        if v is None: return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_rest_map(game_date: str, ctx_teams: set) -> dict:
    """Return {team: rest_days} for teams playing on `game_date`.

    Rest = days since last graded game in ncaaf_game_results. Fetches a
    30-day-prior window in one bulk request. Teams with no prior game
    in the window (Week 1, byes) get None.

    2026-09-01 (Tier C of MC improvements per user directive).
    """
    from datetime import datetime as _dt, timedelta as _td
    try:
        target = _dt.fromisoformat(game_date).date()
    except Exception:
        return {}
    since = (target - _td(days=30)).isoformat()
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_results?'
        f'game_date=gte.{since}&game_date=lt.{game_date}'
        f'&home_score=not.is.null'
        f'&select=home_team,away_team,game_date&order=game_date.desc&limit=2000',
        headers=H_READ, timeout=20)
    if r.status_code != 200: return {}
    rows = r.json() if isinstance(r.json(), list) else []
    # Latest game per team
    latest: dict = {}
    for row in rows:
        d = row.get('game_date')
        for tk in ('home_team', 'away_team'):
            t = row.get(tk)
            if t and t in ctx_teams and t not in latest:
                latest[t] = d
    out = {}
    for t, d in latest.items():
        try:
            gd = _dt.fromisoformat(d).date()
            out[t] = (target - gd).days
        except Exception:
            continue
    return out


def _apply_signal_adjustments(base_home_exp: float, base_away_exp: float,
                              ctx: dict, home_rest: Optional[int],
                              away_rest: Optional[int]) -> tuple:
    """Apply A+B+C signal adjustments to expected scores + stddev.

    Returns (home_expected, away_expected, home_stddev, away_stddev, notes).

    A. WEATHER (outdoor games only):
       wind >= 15 mph → -2.5 pts both sides + 15% wider stddev each
       temp <= 32°F  → -1.5 pts both sides + 10% wider stddev each

    B. RETURNING PRODUCTION:
       team's returning < 55% → 20% wider stddev for that team
       (high roster churn = more Week 1-4 uncertainty)

    C. REST DAYS:
       team on short week (<=4 days) → -1.5 pts + 8% wider stddev
       team with extra rest (>=10 days) → +1.0 pts (byes, coming off bye)
    """
    home_exp = base_home_exp
    away_exp = base_away_exp
    home_std = GAME_STDDEV
    away_std = GAME_STDDEV
    notes = []

    # ── A. WEATHER ──
    is_dome = bool(ctx.get('dome'))
    if not is_dome:
        wind = _f(ctx.get('wind'))
        temp = _f(ctx.get('temp'))
        if wind is not None and wind >= 15:
            home_exp -= 2.5; away_exp -= 2.5
            home_std *= 1.15; away_std *= 1.15
            notes.append(f'wind {wind:.0f}mph → -2.5 each, wider var')
        if temp is not None and temp <= 32:
            home_exp -= 1.5; away_exp -= 1.5
            home_std *= 1.10; away_std *= 1.10
            notes.append(f'temp {temp:.0f}°F → -1.5 each')

    # ── B. RETURNING PRODUCTION ──
    home_rp = _f(ctx.get('home_returning_production'))
    away_rp = _f(ctx.get('away_returning_production'))
    if home_rp is not None and home_rp < 0.55:
        home_std *= 1.20
        notes.append(f'home ret prod {home_rp*100:.0f}% → wider home var')
    if away_rp is not None and away_rp < 0.55:
        away_std *= 1.20
        notes.append(f'away ret prod {away_rp*100:.0f}% → wider away var')

    # ── C. REST DAYS ──
    if home_rest is not None and home_rest <= 4:
        home_exp -= 1.5; home_std *= 1.08
        notes.append(f'home short week ({home_rest}d) → -1.5 pts')
    elif home_rest is not None and home_rest >= 10:
        home_exp += 1.0
        notes.append(f'home extra rest ({home_rest}d) → +1.0 pts')
    if away_rest is not None and away_rest <= 4:
        away_exp -= 1.5; away_std *= 1.08
        notes.append(f'away short week ({away_rest}d) → -1.5 pts')
    elif away_rest is not None and away_rest >= 10:
        away_exp += 1.0
        notes.append(f'away extra rest ({away_rest}d) → +1.0 pts')

    # ── D. COHORT TILT (2026-09-01 · MC improvements Tier D) ──
    # signal_confluence_net is the SIGNED count of cohorts firing on
    # each side (positive = home advantage, negative = away). When it's
    # strongly confluent (|net| >= 3), shift expected margin toward the
    # majority side by ~1.5 pts — this bakes cohort agreement into the
    # sim's central tendency rather than treating each cohort as pure
    # noise sitting outside the projection. Data source: ctx.signal_
    # confluence_net (already populated by ncaaf_game_context.py).
    conf_net = ctx.get('signal_confluence_net')
    try:
        conf_int = int(conf_net) if conf_net is not None else 0
    except (TypeError, ValueError):
        conf_int = 0
    if conf_int >= 3:
        # Home has strong signal confluence; shift margin toward home
        # by 0.5 pts per net-signal above 2, capped at +2.5
        tilt = min((conf_int - 2) * 0.5, 2.5)
        home_exp += tilt / 2.0
        away_exp -= tilt / 2.0
        notes.append(f'cohort tilt home (net +{conf_int}) → +{tilt:.1f} pts margin')
    elif conf_int <= -3:
        tilt = min((abs(conf_int) - 2) * 0.5, 2.5)
        away_exp += tilt / 2.0
        home_exp -= tilt / 2.0
        notes.append(f'cohort tilt away (net {conf_int}) → -{tilt:.1f} pts margin')

    # ── E. SHARP MONEY NUDGE (2026-09-01 · MC improvements Tier E) ──
    # splits_summary aggregates cross-source money% + bets% per (market,
    # side). When money-pct on one side significantly exceeds bets-pct
    # (divergence >= 15), that's sharp money on the money side while
    # public tickets sit on the other. Nudge expected margin ~1.5 pts
    # toward the sharp side. Uses ML market by default since ML sharp
    # signal is cleanest; falls back to spread if ML absent.
    ss = ctx.get('splits_summary')
    if isinstance(ss, dict):
        # Try ML first, then spread/rl
        for mkt_key in ('ml', 'moneyline', 'spread', 'rl'):
            mkt = ss.get(mkt_key)
            if not isinstance(mkt, dict): continue
            home_side = mkt.get('HOME') if isinstance(mkt.get('HOME'), dict) else {}
            away_side = mkt.get('AWAY') if isinstance(mkt.get('AWAY'), dict) else {}
            h_money = home_side.get('money_pct_avg')
            h_bets  = home_side.get('bets_pct_avg')
            a_money = away_side.get('money_pct_avg')
            a_bets  = away_side.get('bets_pct_avg')
            # HOME sharp: money on home >> bets on home (public elsewhere)
            if (h_money is not None and h_bets is not None
                and h_money >= 65 and (h_money - h_bets) >= 15):
                home_exp += 0.75; away_exp -= 0.75
                notes.append(f'sharp $ home ({mkt_key}: {h_money:.0f}% money vs {h_bets:.0f}% bets)')
                break
            if (a_money is not None and a_bets is not None
                and a_money >= 65 and (a_money - a_bets) >= 15):
                away_exp += 0.75; home_exp -= 0.75
                notes.append(f'sharp $ away ({mkt_key}: {a_money:.0f}% money vs {a_bets:.0f}% bets)')
                break

    return home_exp, away_exp, home_std, away_std, notes


def simulate_game(projected_spread: float, projected_total: float,
                  n_sims: int = N_SIMS,
                  posted_total: Optional[float] = None,
                  ctx: Optional[dict] = None,
                  home_rest: Optional[int] = None,
                  away_rest: Optional[int] = None) -> dict:
    """Run n_sims of the game with A+B+C signal adjustments applied.

    Base expected scores from ctx projections (SP+/EPA/HFA/returning):
        home = (projected_total + projected_spread) / 2
        away = (projected_total - projected_spread) / 2

    Adjustments applied per _apply_signal_adjustments — weather, returning
    production variance, rest days. Each side gets its own stddev so
    high-churn team has wider dispersion than stable opponent.
    """
    base_home = max((projected_total + projected_spread) / 2.0, 3.0)
    base_away = max((projected_total - projected_spread) / 2.0, 3.0)

    home_exp, away_exp, home_std, away_std, notes = _apply_signal_adjustments(
        base_home, base_away, ctx or {}, home_rest, away_rest)
    # Enforce floor after adjustments
    home_exp = max(home_exp, 3.0)
    away_exp = max(away_exp, 3.0)

    home_wins = 0
    over_hits = 0
    total_margins = 0.0
    total_totals = 0.0
    margin_sq_sum = 0.0

    for _ in range(n_sims):
        home_score = max(random.gauss(home_exp, home_std), 0)
        away_score = max(random.gauss(away_exp, away_std), 0)
        margin = home_score - away_score
        total = home_score + away_score
        total_margins += margin
        total_totals += total
        margin_sq_sum += margin * margin
        if home_score > away_score: home_wins += 1
        if posted_total is not None and total > posted_total: over_hits += 1

    mean_margin = total_margins / n_sims
    mean_total = total_totals / n_sims
    var_margin = (margin_sq_sum / n_sims) - (mean_margin ** 2)
    std_margin = math.sqrt(max(var_margin, 0))

    p_home = home_wins / n_sims
    result = {
        'mc_p_home': round(p_home, 3),
        'mc_p_away': round(1 - p_home, 3),
        'mc_expected_margin': round(mean_margin, 2),
        'mc_expected_total': round(mean_total, 2),
        'mc_stddev_margin': round(std_margin, 2),
        'mc_confidence_high': (abs(mean_margin) > 7.0 and std_margin < 19.5),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    if posted_total is not None:
        result['mc_p_over_line'] = round(over_hits / n_sims, 3)
    if notes:
        # Attach adjustments for audit visibility. Truncate to keep JSONB small.
        result['mc_adjustments'] = notes[:6]
    return result


def run(game_date: str, dry_run: bool = False) -> int:
    """Load NCAAF ctx rows for the date; simulate each with projections
    + A+B+C signal adjustments (weather / returning production / rest)."""
    print(f'=== NCAAF MC simulator · {game_date} ===')

    r = requests.get(f'{SB}/rest/v1/ncaaf_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,home_team,away_team,close_total,'
                          'projected_spread,projected_total,neutral_site,'
                          # Signal fields for A+B+C adjustments:
                          'temp,wind,dome,'
                          'home_returning_production,away_returning_production,'
                          # Signal fields for D+E adjustments (Tier D=cohort,
                          # Tier E=sharp money):
                          'signal_confluence_net,splits_summary'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    games = r.json() if isinstance(r.json(), list) else []
    if not games:
        print(f'  no NCAAF games for {game_date}'); return 0
    print(f'  {len(games)} NCAAF games in context')

    # 2026-09-01 (Tier C): bulk-fetch rest days for all teams playing today
    ctx_teams = set()
    for g in games:
        if g.get('home_team'): ctx_teams.add(g['home_team'])
        if g.get('away_team'): ctx_teams.add(g['away_team'])
    rest_map = _compute_rest_map(game_date, ctx_teams)
    if rest_map:
        print(f'  computed rest days for {len(rest_map)} teams')

    written = 0
    skipped_no_projections = 0

    for g in games:
        home = g.get('home_team'); away = g.get('away_team')
        if not (home and away): continue
        proj_spread = _f(g.get('projected_spread'))
        proj_total = _f(g.get('projected_total'))
        if proj_spread is None or proj_total is None:
            skipped_no_projections += 1
            continue

        result = simulate_game(
            projected_spread=proj_spread,
            projected_total=proj_total,
            posted_total=_f(g.get('close_total')),
            ctx=g,
            home_rest=rest_map.get(home),
            away_rest=rest_map.get(away),
        )

        matchup = f'{away} @ {home}'
        neutral_marker = ' (N)' if g.get('neutral_site') else ''
        adj_marker = f'  adj={len(result.get("mc_adjustments") or [])}' if result.get('mc_adjustments') else ''
        print(f'  {matchup:36}{neutral_marker}  home {result["mc_p_home"]*100:5.1f}%  '
              f'margin {result["mc_expected_margin"]:+5.1f}  '
              f'tot {result["mc_expected_total"]:5.1f}  '
              f'{"HIGH-CONF" if result["mc_confidence_high"] else ""}{adj_marker}')

        if dry_run: continue

        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json={'mc_probabilities': result}, timeout=10)
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'    write failed: {pr.status_code} {pr.text[:150]}')

    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {written} MC blobs · '
          f'skipped {skipped_no_projections} without projections '
          f'(usually FCS opps or missing SP+ baseline).')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--days', type=int, default=1,
                   help='Days to simulate starting from --date (default 1)')
    args = p.parse_args()
    start = args.date or _et_today()
    total = 0
    if args.days == 1:
        total = run(game_date=start, dry_run=args.dry_run)
    else:
        base = datetime.fromisoformat(start).date()
        for i in range(args.days):
            d = (base + timedelta(days=i)).isoformat()
            total += run(game_date=d, dry_run=args.dry_run)
    print(f'\nTotal MC blobs written: {total}')


if __name__ == '__main__':
    main()
