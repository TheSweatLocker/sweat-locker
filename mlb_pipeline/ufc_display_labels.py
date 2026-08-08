"""UFC display-labels formatter (2026-08-08).

Translates internal analyst shorthand into user-facing English + writes
to ufc_picks.display_labels. Per feedback_backside_dictates_app_renders
— app renders the label verbatim, zero translation logic on frontend.

Called by:
  - ufc_compute_ev.py (after EV compute)
  - ufc_odds_pull.py  (after odds refresh)
Both trigger since odds change → ev change → labels need refresh.

Fields produced:
  odds_a           "1.65 (-154)"                — decimal + American
  odds_b           "2.35 (+135)"
  method_breakdown "KO 41% · SUB 25% · Decision 34%"
  distance         "Goes to Decision (58%)"     — plain English
  rounds           "R1 48% · R2 29% · R3 22%"
  conviction_badge "PRIME · 85% model win"
  action_badge     "BACK Nurgozhay (+26pp edge)" or "PASS · odds too tight"
  recommended_odds "1.65 (-154)"
  recommended_fighter "Diyar Nurgozhay"

Standalone CLI for backfill:
  python ufc_display_labels.py --event-date 2026-08-08
  python ufc_display_labels.py --all
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
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
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def decimal_to_american(dec: Optional[float]) -> Optional[str]:
    """Convert decimal odds to American string.
    1.65 → "-154", 2.35 → "+135", 2.0 → "+100"."""
    if dec is None: return None
    try: d = float(dec)
    except (TypeError, ValueError): return None
    if d <= 1.0: return None
    if d >= 2.0:
        return f'+{int(round((d - 1) * 100))}'
    else:
        return f'-{int(round(100 / (d - 1)))}'


def format_odds(dec: Optional[float]) -> str:
    """'1.65 (-154)' — decimal + American together for max clarity."""
    if dec is None: return 'no line'
    try: d = float(dec)
    except (TypeError, ValueError): return 'no line'
    am = decimal_to_american(d)
    return f'{d:.2f} ({am})' if am else f'{d:.2f}'


def format_pct(v: Optional[float]) -> str:
    """0.42 → '42%'."""
    if v is None: return '—'
    try: return f'{round(float(v) * 100)}%'
    except (TypeError, ValueError): return '—'


def _round_dist(pick: dict) -> str:
    """R1 48% · R2 29% · R3 22% (skip R4/R5 unless 5-rounder with signal)."""
    parts = []
    for i in range(1, 6):
        v = pick.get(f'p_round_{i}')
        if v is None: continue
        try: v = float(v)
        except (TypeError, ValueError): continue
        if i <= 3 or v >= 0.03:
            parts.append(f'R{i} {round(v*100)}%')
    return ' · '.join(parts) if parts else '—'


def _method_breakdown(pick: dict) -> str:
    """Plain-English method distribution."""
    ko = pick.get('p_method_ko')
    sub = pick.get('p_method_sub')
    dec = pick.get('p_method_dec')
    parts = []
    if ko is not None: parts.append(f'KO/TKO {format_pct(ko)}')
    if sub is not None: parts.append(f'SUB {format_pct(sub)}')
    if dec is not None: parts.append(f'Decision {format_pct(dec)}')
    return ' · '.join(parts) if parts else '—'


def _distance_label(pick: dict) -> str:
    """Turn distance probability into a sentence users can read."""
    dist = pick.get('p_distance')
    if dist is None: return '—'
    try: d = float(dist)
    except (TypeError, ValueError): return '—'
    pct_str = format_pct(d)
    if d >= 0.55: return f'Goes to Decision ({pct_str})'
    if d <= 0.35: return f'Finish Expected ({format_pct(1-d)})'
    return f'Distance uncertain ({pct_str})'


def _conviction_badge(pick: dict) -> str:
    """PRIME · 85% model win."""
    tier = (pick.get('tier_winner') or '').upper()
    p_a = pick.get('p_winner_a') or 0
    rec = (pick.get('recommended_side') or '').lower()
    win_prob = p_a if rec == 'a' else (1 - p_a)
    prob_pct = round(win_prob * 100)
    if tier: return f'{tier} · {prob_pct}% model win'
    return f'{prob_pct}% model win'


def _action_badge(pick: dict) -> str:
    """BACK/FADE/PASS with edge/reason in one line."""
    ev_tier = (pick.get('ev_tier') or '').upper()
    rec = (pick.get('ev_recommended_side') or pick.get('recommended_side') or '').lower()
    fighter = pick.get('fighter_a') if rec == 'a' else (pick.get('fighter_b') if rec == 'b' else None)
    ev = pick.get('ev_side_a') if rec == 'a' else (pick.get('ev_side_b') if rec == 'b' else None)
    p_a = pick.get('p_winner_a') or 0
    win_prob = p_a if rec == 'a' else (1 - p_a) if rec == 'b' else 0
    odds = pick.get('odds_a_median') if rec == 'a' else (pick.get('odds_b_median') if rec == 'b' else None)

    if not fighter:
        return 'PASS · no clear side'
    if ev_tier == 'SKIP' or ev is None or ev < 0:
        # Skip reason: which failure mode
        if odds is None: return f'PASS · no line posted'
        try: implied = 1 / float(odds)
        except (TypeError, ValueError): implied = 0
        if implied >= win_prob + 0.05: return f'PASS · odds too tight for model edge'
        return f'PASS · insufficient EV'

    # Active recommendation — BACK or FADE (fade if ev_recommended_side flips model)
    verb = 'BACK'
    if pick.get('ev_recommended_side') and pick.get('recommended_side') and \
       pick.get('ev_recommended_side') != pick.get('recommended_side'):
        verb = 'FADE (EV flip)'

    # Edge magnitude in pp
    try:
        implied = 1 / float(odds) if odds else 0
        edge_pp = round((win_prob - implied) * 100)
        edge_str = f'+{edge_pp}pp edge' if edge_pp > 0 else f'{edge_pp}pp edge'
    except (TypeError, ValueError, ZeroDivisionError):
        edge_str = f'{ev_tier} EV'

    return f'{verb} {fighter} ({edge_str})'


def build_display_labels(pick: dict) -> dict:
    """Compute full display_labels dict for one ufc_picks row."""
    rec = (pick.get('ev_recommended_side') or pick.get('recommended_side') or '').lower()
    rec_fighter = pick.get('fighter_a') if rec == 'a' else (pick.get('fighter_b') if rec == 'b' else None)
    rec_odds = pick.get('odds_a_median') if rec == 'a' else (pick.get('odds_b_median') if rec == 'b' else None)
    return {
        'odds_a': format_odds(pick.get('odds_a_median')),
        'odds_b': format_odds(pick.get('odds_b_median')),
        'method_breakdown': _method_breakdown(pick),
        'distance': _distance_label(pick),
        'rounds': _round_dist(pick),
        'conviction_badge': _conviction_badge(pick),
        'action_badge': _action_badge(pick),
        'recommended_fighter': rec_fighter,
        'recommended_odds': format_odds(rec_odds),
        'computed_at': datetime.now(timezone.utc).isoformat(),
    }


def update_display_labels_for_pick(pick_id: int, pick: dict, dry_run: bool = False) -> bool:
    """Compute + PATCH one row. Returns True on success."""
    labels = build_display_labels(pick)
    if dry_run:
        print(f'  DRY [id {pick_id}] {labels.get("action_badge")}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/ufc_picks?id=eq.{pick_id}',
        headers=H_WRITE, data=json.dumps({'display_labels': labels}), timeout=10,
    )
    return r.status_code in (200, 204)


def refresh_for_event(event_date: str, dry_run: bool = False) -> int:
    """Refresh display_labels for every pick on one event_date."""
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks',
        headers=H_READ,
        params={'event_date': f'eq.{event_date}', 'select': '*',
                'order': 'fight_order.asc'},
        timeout=15,
    )
    picks = r.json() if r.status_code == 200 else []
    updated = 0
    for pk in picks:
        if update_display_labels_for_pick(pk['id'], pk, dry_run=dry_run):
            updated += 1
    print(f'  refreshed display_labels for {updated}/{len(picks)} picks on {event_date}')
    return updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--event-date', help='YYYY-MM-DD — refresh one card')
    ap.add_argument('--all', action='store_true', help='refresh all events with odds')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.event_date:
        refresh_for_event(args.event_date, dry_run=args.dry_run)
    elif args.all:
        r = requests.get(f'{SB}/rest/v1/ufc_picks',
                         headers=H_READ,
                         params={'select': 'event_date',
                                 'odds_a_median': 'not.is.null',
                                 'limit': '5000'},
                         timeout=30).json()
        dates = sorted({p['event_date'] for p in r if p.get('event_date')})
        print(f'  refreshing {len(dates)} event dates...')
        for d in dates:
            refresh_for_event(d, dry_run=args.dry_run)
    else:
        print('Specify --event-date YYYY-MM-DD or --all')


if __name__ == '__main__':
    main()
