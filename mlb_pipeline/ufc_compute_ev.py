"""UFC EV compute — reads model prob + market odds, computes EV per side,
sets ev_tier + ev_recommended_side on ufc_picks.

EV formula (per $100 wagered):
    EV(side) = p_win × (decimal_odds - 1) × 100 − (1 - p_win) × 100

Tier gate:
    PRIME:  EV ≥ +8  AND conviction_winner ≥ 30
    STRONG: EV +4 to +7.99
    LEAN:   EV +2 to +3.99
    SKIP:   EV < +2 OR no odds OR conviction < 25

Recommended side = whichever side has higher +EV (or NONE if both negative).

USAGE:
  python ufc_compute_ev.py --event-date 2026-08-01
  python ufc_compute_ev.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def compute_ev(p_win: float, decimal_odds: float) -> float:
    """EV per $100 bet on this side."""
    if p_win is None or decimal_odds is None:
        return None
    return round(p_win * (decimal_odds - 1) * 100 - (1 - p_win) * 100, 2)


def ev_tier(ev: float | None, conviction: float | None,
             odds_dec: float | None = None) -> str:
    if ev is None:
        return 'SKIP'
    if conviction is not None and conviction < 25:
        return 'SKIP'
    # 2026-08-16 morning-audit rule: 90%+ model win-prob favorites went 2-6
    # over 30d — model overweights win probability in a single-round-KO
    # sport. Require odds >= 1.5 (roughly +/- even money or better) to
    # earn PRIME, so we skip -200+ juice traps regardless of EV.
    if ev >= 8 and (conviction is None or conviction >= 30):
        if odds_dec is not None and odds_dec < 1.5:
            return 'STRONG'  # good edge but juice trap → cap at STRONG
        return 'PRIME'
    if ev >= 4:
        return 'STRONG'
    if ev >= 2:
        return 'LEAN'
    return 'SKIP'


# 2026-08-16 morning-audit rule: 90%+ model win-prob picks went 2-6 over
# 30d. Single KO ends any skill gap in MMA — the win-prob calibration
# just doesn't hold at extremes. Cap the win-prob we display + hand to
# downstream tiering at 80%. EV math still uses the raw probability so
# we don't over-recommend, but users don't see 91% and think it's locked.
UFC_WIN_PROB_DISPLAY_CAP = 0.80


def clamp_display_win_prob(p: float | None) -> float | None:
    if p is None:
        return None
    return min(p, UFC_WIN_PROB_DISPLAY_CAP)


def load_picks(event_date: str) -> list:
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks',
        params={
            'event_date': f'eq.{event_date}',
            'select': 'id,fight_order,fighter_a,fighter_b,p_winner_a,'
                      'conviction_winner,odds_a_median,odds_b_median,'
                      'odds_a_best,odds_b_best,odds_book_count',
        },
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def update_pick(pick_id: int, payload: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/ufc_picks?id=eq.{pick_id}',
        headers=H_WRITE, json=payload, timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f'    ⚠ patch {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(event_date: str | None = None, dry_run: bool = False):
    if event_date is None:
        today = datetime.now(timezone.utc).date()
        days_until_sat = (5 - today.weekday()) % 7
        event_date = (today + timedelta(days=days_until_sat)).isoformat()

    print(f'== UFC EV compute for {event_date} ==')
    picks = load_picks(event_date)
    if not picks:
        print('  no picks')
        return

    scored = 0
    updated = 0
    tier_counts = {'PRIME': 0, 'STRONG': 0, 'LEAN': 0, 'SKIP': 0}
    for pick in picks:
        p_a = pick.get('p_winner_a')
        p_b = 1.0 - p_a if p_a is not None else None
        odds_a = pick.get('odds_a_median')
        odds_b = pick.get('odds_b_median')
        conv = pick.get('conviction_winner')

        ev_a = compute_ev(p_a, odds_a) if p_a is not None else None
        ev_b = compute_ev(p_b, odds_b) if p_b is not None else None

        # Recommended = whichever side has higher +EV; both negative → NONE
        rec_side = None
        best_ev = None
        if ev_a is not None and ev_b is not None:
            if ev_a >= ev_b:
                if ev_a > 0: rec_side, best_ev = 'a', ev_a
                elif ev_b > 0: rec_side, best_ev = 'b', ev_b
            else:
                if ev_b > 0: rec_side, best_ev = 'b', ev_b
                elif ev_a > 0: rec_side, best_ev = 'a', ev_a

        # Pass odds_dec so ev_tier can apply the juice-trap PRIME cap
        rec_odds = odds_a if rec_side == 'a' else (odds_b if rec_side == 'b' else None)
        tier = ev_tier(best_ev, conv, rec_odds) if best_ev is not None else 'SKIP'
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        payload = {
            'ev_side_a': ev_a,
            'ev_side_b': ev_b,
            'ev_tier': tier,
            'ev_recommended_side': rec_side,
        }
        fname = pick['fighter_a'] if rec_side == 'a' else (pick['fighter_b'] if rec_side == 'b' else '-')
        odds_pick = odds_a if rec_side == 'a' else (odds_b if rec_side == 'b' else '-')
        print(f'  {tier:6s} {ev_a!s:>7} / {ev_b!s:>7}  '
              f'conv={conv}  rec={rec_side or "-"} {fname[:20]:20s} @ {odds_pick}  '
              f'[{pick["fighter_a"][:14]} vs {pick["fighter_b"][:14]}]')
        scored += 1
        if not dry_run:
            if update_pick(pick['id'], payload):
                updated += 1

    print(f'\nSummary: {scored} scored · {updated} updated')
    print(f'Tier distribution: {tier_counts}')

    # 2026-08-08 auto-refresh display labels after EV compute.
    # display_labels is user-facing translated text (odds w/ American,
    # method breakdown in English, action badge). Kept in sync with EV
    # here so app always renders latest translated view.
    if not dry_run and event_date:
        try:
            from ufc_display_labels import refresh_for_event
            refresh_for_event(event_date)
        except Exception as e:
            print(f'  ⚠ display_labels refresh failed (non-fatal): {e}')
        try:
            from ufc_detail_view import refresh_for_event as refresh_detail
            refresh_detail(event_date)
        except Exception as e:
            print(f'  ⚠ detail_view refresh failed (non-fatal): {e}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--event-date', help='YYYY-MM-DD (defaults to next Saturday)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(event_date=args.event_date, dry_run=args.dry_run)
