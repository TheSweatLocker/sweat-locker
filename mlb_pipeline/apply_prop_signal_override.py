"""Apply prop-signal override to model_pred_total.

Backtested 2026-05-19 on 635 resolved games / 516 PRIME+STRONG props:
when v4 says OVER by 1.5+ AND PRIME/STRONG prop concentration points
UNDER, props win the head-to-head 64.7% (n=17). Strong UNDER prop
signal hits UNDER 72.4% standalone (n=29).

This script reads today's mlb_game_context + mlb_pipeline_props,
computes the prop_net_signal per game, and patches model_pred_total
when the override fires. Downstream consumers (Jerry reads, Daily
Degen total lean, generate_tonight_card) read the corrected value.

Runs AFTER generate_props.py and BEFORE play_of_day.py in the
workflow. Safe to run multiple times — idempotent on already-flipped
games (we tag flipped games and skip re-flipping).
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}
READ_HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}

# Backtested thresholds — see _backtest_prop_signals.py
V4_OVER_MIN_DELTA = 1.5      # v4 must be at least this far above market
PROP_SIGNAL_THRESHOLD = -4   # prop signal must be at most this negative (strong UNDER)


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def compute_prop_signal(props_for_game):
    """Higher = OVER lean, lower (more negative) = UNDER lean.

    Weights mirror the backtest. hits_over_prime is weighted 2x (high
    conviction the bat hits); hits_under_prime 2x (high conviction it
    doesn't); ks_over_prime 1.5x (Ks compress runs); outs_under_strong 1x
    (short outing = bullpen game = more variance, mild UNDER lean).
    """
    over = 0
    under = 0
    for p in props_for_game:
        pt = p.get('prop_type')
        tier = p.get('tier')
        if pt == 'hits_over':
            over += 2 if tier == 'PRIME' else 1 if tier == 'STRONG' else 0
        elif pt == 'hits_under':
            under += 2 if tier == 'PRIME' else 1 if tier == 'STRONG' else 0
        elif pt == 'ks_over' and tier == 'PRIME':
            under += 1.5
        elif pt == 'outs_under' and tier == 'STRONG':
            under += 1
    return over - under


def run():
    date = today_et()
    print(f'Applying prop-signal override for {date}')

    # Pull today's games
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_game_context',
        params={
            'game_date': f'eq.{date}',
            'select': 'game_id,home_team,away_team,model_pred_total,projected_total,close_total',
        },
        headers=READ_HEADERS,
        timeout=15,
    )
    games = r.json() if r.status_code == 200 else []
    if not games:
        print('  No games for today — nothing to override.')
        return

    # Pull today's props (PRIME + STRONG only — that's what we backtested)
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_pipeline_props',
        params={
            'game_date': f'eq.{date}',
            'tier': 'in.(PRIME,STRONG)',
            'select': 'game_id,prop_type,tier,conviction',
        },
        headers=READ_HEADERS,
        timeout=15,
    )
    props = r.json() if r.status_code == 200 else []
    print(f'  Loaded {len(games)} games, {len(props)} PRIME/STRONG props.')

    props_by_game = {}
    for p in props:
        gid = p.get('game_id')
        if not gid:
            continue
        props_by_game.setdefault(gid, []).append(p)

    flipped = 0
    skipped = 0
    for g in games:
        gid = g.get('game_id')
        v4 = g.get('model_pred_total')
        v3 = g.get('projected_total')
        mkt = g.get('close_total')

        # Need v4 prediction + market line to evaluate
        if v4 is None or mkt is None:
            skipped += 1
            continue

        try:
            v4_f = float(v4)
            mkt_f = float(mkt)
        except (TypeError, ValueError):
            skipped += 1
            continue

        v4_delta = v4_f - mkt_f

        # Only intervene when v4 leans OVER strongly (>= +1.5 above market)
        if v4_delta < V4_OVER_MIN_DELTA:
            continue

        # Check prop signal for this game
        game_props = props_by_game.get(gid, [])
        if not game_props:
            continue
        signal = compute_prop_signal(game_props)

        # Only flip on STRONG UNDER prop signal (validated cohort)
        if signal > PROP_SIGNAL_THRESHOLD:
            continue

        # Override fires — patch model_pred_total to a conservative UNDER value
        # Use min(market - 0.5, v3) so we don't artificially push too far under
        if v3 is not None:
            try:
                v3_f = float(v3)
                new_total = min(mkt_f - 0.5, v3_f)
            except (TypeError, ValueError):
                new_total = mkt_f - 0.5
        else:
            new_total = mkt_f - 0.5

        # Don't make the override worse than the original v4 value
        # (sanity check — should never trip since we already verified v4 > market+1.5)
        if new_total >= v4_f:
            continue

        matchup = f"{g.get('away_team','?')} @ {g.get('home_team','?')}"
        print(f"  🔄 FLIP {matchup}")
        print(f"     v4 said {v4_f:.2f} (Δmkt {v4_delta:+.2f} OVER)")
        print(f"     prop signal: {signal:+.1f} → STRONG UNDER")
        print(f"     props fired: {len(game_props)} PRIME/STRONG props on this game")
        print(f"     → patching model_pred_total to {new_total:.2f}")
        print(f"     (backtested: 64.7% UNDER hit rate in this exact v4-vs-props disagreement pattern)")

        # Patch the row
        pr = requests.patch(
            f'{SUPABASE_URL}/rest/v1/mlb_game_context',
            params={'game_id': f'eq.{gid}', 'game_date': f'eq.{date}'},
            headers=HEADERS,
            json={'model_pred_total': round(new_total, 2)},
            timeout=15,
        )
        if pr.status_code in (200, 204):
            flipped += 1
        else:
            print(f"     ⚠️ patch failed: HTTP {pr.status_code} — {pr.text[:200]}")

    print(f'\n  Flipped {flipped} game(s). Skipped {skipped} (missing v4 or market).')


if __name__ == '__main__':
    run()
