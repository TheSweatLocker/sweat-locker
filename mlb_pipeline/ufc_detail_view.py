"""UFC fight detail-view builder (2026-08-08).

Builds a comprehensive JSONB blob for the app's fight detail page.
User feedback: current detail page renders empty because app only
reads a few raw fields. Pipeline has rich data — this packages ALL
of it into one struct the app renders verbatim.

Fields produced (see migration 20260808b_ufc_detail_view.sql for full
schema). Structured for direct app consumption:
  - header: matchup + event metadata
  - verdict: action badge + tier + edge
  - odds: both sides with dec + American + implied prob + book count
  - method: array for bar chart (KO/SUB/Decision)
  - distance: label + expected pct
  - rounds: array for round-distribution chart
  - fighters.a / fighters.b: full profile each side
  - compare: side-by-side stat rows with edge highlighting
  - jerry: short + long read text
  - meta: freshness timestamps

Called by:
  - ufc_compute_ev.py    (after EV compute)
  - ufc_odds_pull.py     (after odds pull)
  - generate_ufc_fight_synthesis.py (after Jerry write)
So every write path keeps the detail_view fresh.

CLI backfill:
  python ufc_detail_view.py --event-date 2026-08-08
  python ufc_detail_view.py --all
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


# Reuse label formatters
try:
    from ufc_display_labels import (
        decimal_to_american, format_odds, format_pct,
    )
except ImportError:
    def decimal_to_american(dec):
        if dec is None: return None
        try: d = float(dec)
        except (TypeError, ValueError): return None
        if d <= 1.0: return None
        if d >= 2.0: return f'+{int(round((d - 1) * 100))}'
        return f'-{int(round(100 / (d - 1)))}'
    def format_odds(dec):
        if dec is None: return 'no line'
        try: d = float(dec)
        except (TypeError, ValueError): return 'no line'
        am = decimal_to_american(d)
        return f'{d:.2f} ({am})' if am else f'{d:.2f}'
    def format_pct(v):
        if v is None: return '—'
        try: return f'{round(float(v) * 100)}%'
        except (TypeError, ValueError): return '—'


def _implied_pct(dec) -> Optional[float]:
    if dec is None: return None
    try: return 1.0 / float(dec)
    except (TypeError, ValueError, ZeroDivisionError): return None


def _age_from_dob(dob_str) -> Optional[int]:
    if not dob_str: return None
    try:
        dob = datetime.strptime(dob_str[:10], '%Y-%m-%d')
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        yrs = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return yrs
    except (ValueError, TypeError): return None


def _f(v, default=None):
    if v is None: return default
    try: return float(v)
    except (TypeError, ValueError): return default


def _fighter_profile(stats: dict) -> dict:
    """Extract fighter profile in a stable schema for the detail card."""
    if not stats: return {}
    return {
        'name': stats.get('fighter_name'),
        'nickname': stats.get('nickname'),
        'record': stats.get('record'),
        'wins': stats.get('total_wins'), 'losses': stats.get('total_losses'),
        'draws': stats.get('total_draws'),
        'ko': stats.get('wins_by_ko'), 'sub': stats.get('wins_by_sub'),
        'dec': stats.get('wins_by_dec'),
        'finish_rate_pct': stats.get('finishing_rate'),
        'height': stats.get('height'),
        'weight': stats.get('weight'),
        'reach': stats.get('reach'),
        'stance': stats.get('stance'),
        'age': _age_from_dob(stats.get('dob')),
        'slpm': stats.get('slpm'),
        'str_acc_pct': stats.get('str_acc'),
        'sapm': stats.get('sapm'),
        'str_def_pct': stats.get('str_def'),
        'td_avg': stats.get('td_avg'),
        'td_acc_pct': stats.get('td_acc'),
        'td_def_pct': stats.get('td_def'),
        'sub_avg': stats.get('sub_avg'),
    }


def _compare_rows(a: dict, b: dict) -> list:
    """Side-by-side stat rows with edge highlighting.
    Returns [{label, a_val, b_val, edge_side}] where edge_side is 'a' or 'b'."""
    def _row(label, a_val, b_val, higher_is_better=True, format_fn=str):
        if a_val is None and b_val is None: return None
        edge_side = None
        try:
            av = float(a_val) if a_val is not None else None
            bv = float(b_val) if b_val is not None else None
            if av is not None and bv is not None and av != bv:
                if higher_is_better: edge_side = 'a' if av > bv else 'b'
                else:                edge_side = 'a' if av < bv else 'b'
        except (TypeError, ValueError): pass
        return {'label': label,
                'a_val': format_fn(a_val) if a_val is not None else '—',
                'b_val': format_fn(b_val) if b_val is not None else '—',
                'edge_side': edge_side}

    rows = []
    def _pct1(v): return f'{round(float(v),1)}%' if v is not None else '—'
    def _num1(v): return f'{round(float(v),2)}' if v is not None else '—'
    def _int0(v): return f'{int(round(float(v)))}' if v is not None else '—'

    add = lambda *args, **kw: rows.append(_row(*args, **kw)) if _row(*args, **kw) else None

    add('Record',      a.get('record'),         b.get('record'),         format_fn=str)
    add('Wins',        a.get('wins'),           b.get('wins'),           format_fn=_int0)
    add('Finish rate', a.get('finish_rate_pct'), b.get('finish_rate_pct'), format_fn=_pct1)
    add('Age',         a.get('age'),            b.get('age'),            higher_is_better=False, format_fn=_int0)
    add('Height (in)', a.get('height'),         b.get('height'),         format_fn=_num1)
    add('Reach (in)',  a.get('reach'),          b.get('reach'),          format_fn=_num1)
    add('Weight (lb)', a.get('weight'),         b.get('weight'),         format_fn=_num1)
    add('Stance',      a.get('stance'),         b.get('stance'),         format_fn=str)
    add('SLpM (strikes landed / min)',  a.get('slpm'),          b.get('slpm'),          format_fn=_num1)
    add('SApM (strikes absorbed / min)', a.get('sapm'),          b.get('sapm'),          higher_is_better=False, format_fn=_num1)
    add('Strike accuracy',   a.get('str_acc_pct'),   b.get('str_acc_pct'),   format_fn=_pct1)
    add('Strike defense',    a.get('str_def_pct'),   b.get('str_def_pct'),   format_fn=_pct1)
    add('TD avg / 15',       a.get('td_avg'),        b.get('td_avg'),        format_fn=_num1)
    add('TD accuracy',       a.get('td_acc_pct'),    b.get('td_acc_pct'),    format_fn=_pct1)
    add('TD defense',        a.get('td_def_pct'),    b.get('td_def_pct'),    format_fn=_pct1)
    add('Sub attempts / 15', a.get('sub_avg'),       b.get('sub_avg'),       format_fn=_num1)
    return [r for r in rows if r]


def _method_chart(pick: dict) -> list:
    """Bar-chart-friendly method distribution."""
    def _pct(v): return round(float(v)*100, 1) if v is not None else 0
    ko = _pct(pick.get('p_method_ko'))
    sub = _pct(pick.get('p_method_sub'))
    dec = _pct(pick.get('p_method_dec'))
    return [
        {'name': 'KO/TKO', 'pct': ko, 'bar_pct': ko},
        {'name': 'Submission', 'pct': sub, 'bar_pct': sub},
        {'name': 'Decision', 'pct': dec, 'bar_pct': dec},
    ]


def _round_chart(pick: dict) -> list:
    """Round-distribution array with cumulative for cumulative-curve display."""
    out = []
    cum = 0
    for i in range(1, 6):
        v = pick.get(f'p_round_{i}')
        if v is None: continue
        try: pct = round(float(v) * 100, 1)
        except (TypeError, ValueError): continue
        cum = round(cum + pct, 1)
        out.append({'name': f'R{i}', 'pct': pct, 'cumulative_pct': cum})
    return out


def _distance_block(pick: dict) -> dict:
    d = _f(pick.get('p_distance'))
    if d is None: return {'label': '—', 'pct': None, 'expected_finish_pct': None}
    dist_pct = round(d * 100)
    finish_pct = round((1 - d) * 100)
    if d >= 0.55: label = f'Goes to Decision ({dist_pct}%)'
    elif d <= 0.35: label = f'Finish Expected ({finish_pct}%)'
    else: label = f'Distance uncertain ({dist_pct}%)'
    return {'label': label, 'pct': dist_pct, 'expected_finish_pct': finish_pct}


def _verdict_block(pick: dict) -> dict:
    """Action recommendation summary."""
    dl = pick.get('display_labels') or {}
    tier = pick.get('tier_winner') or pick.get('ev_tier')
    rec = (pick.get('ev_recommended_side') or pick.get('recommended_side') or '').lower()
    p_a = _f(pick.get('p_winner_a'), 0) or 0
    win_prob = p_a if rec == 'a' else (1 - p_a) if rec == 'b' else 0
    odds = pick.get('odds_a_median') if rec == 'a' else (pick.get('odds_b_median') if rec == 'b' else None)
    implied = _implied_pct(odds)
    edge_pp = round((win_prob - implied) * 100) if implied is not None else None
    return {
        'action': dl.get('action_badge'),
        'tier': tier,
        'model_win_pct': round(win_prob * 100, 1),
        'implied_win_pct': round(implied * 100, 1) if implied is not None else None,
        'edge_pp': edge_pp,
        'recommended_fighter': dl.get('recommended_fighter'),
        'recommended_odds': dl.get('recommended_odds'),
    }


def _fetch_jerry_read(game_id: str) -> dict:
    r = requests.get(
        f'{SB}/rest/v1/jerry_reads',
        headers=H_READ,
        params={'sport': 'eq.UFC', 'game_id': f'eq.{game_id}',
                'select': 'short_read,long_read,call_text,conviction'},
        timeout=10,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows: return {}
    r0 = rows[0]
    return {
        'short': r0.get('short_read'),
        'long': r0.get('long_read'),
        'call_text': r0.get('call_text'),
        'conviction': r0.get('conviction'),
    }


def build_detail_view(pick: dict, fighter_stats_map: dict) -> dict:
    """One-fight detail-view struct. See module docstring for schema."""
    fa_stats = fighter_stats_map.get((pick.get('fighter_a') or '').lower(), {})
    fb_stats = fighter_stats_map.get((pick.get('fighter_b') or '').lower(), {})
    fa = _fighter_profile(fa_stats)
    fb = _fighter_profile(fb_stats)
    gid = f'ufc_{pick["event_date"]}_{pick["fight_order"]}'
    jerry = _fetch_jerry_read(gid)

    a_odds = pick.get('odds_a_median')
    b_odds = pick.get('odds_b_median')
    ia = _implied_pct(a_odds); ib = _implied_pct(b_odds)

    return {
        'header': {
            'matchup': f'{pick.get("fighter_a")} vs {pick.get("fighter_b")}',
            'fighter_a': pick.get('fighter_a'),
            'fighter_b': pick.get('fighter_b'),
            'event': pick.get('event_name'),
            'event_date': pick.get('event_date'),
            'fight_order': pick.get('fight_order'),
        },
        'verdict': _verdict_block(pick),
        'odds': {
            'a': {
                'dec': float(a_odds) if a_odds is not None else None,
                'american': decimal_to_american(a_odds),
                'display': format_odds(a_odds),
                'implied_pct': round(ia * 100, 1) if ia is not None else None,
                'books': pick.get('odds_book_count'),
            },
            'b': {
                'dec': float(b_odds) if b_odds is not None else None,
                'american': decimal_to_american(b_odds),
                'display': format_odds(b_odds),
                'implied_pct': round(ib * 100, 1) if ib is not None else None,
                'books': pick.get('odds_book_count'),
            },
            'pulled_at': pick.get('odds_pulled_at'),
        },
        'method': _method_chart(pick),
        'distance': _distance_block(pick),
        'rounds': _round_chart(pick),
        'fighters': {'a': fa, 'b': fb},
        'compare': _compare_rows(fa, fb),
        'jerry': jerry,
        'meta': {
            'computed_at': datetime.now(timezone.utc).isoformat(),
            'odds_pulled_at': pick.get('odds_pulled_at'),
            'model_generated_at': pick.get('generated_at'),
        },
    }


def refresh_for_event(event_date: str, dry_run: bool = False) -> int:
    """Rebuild detail_view for every pick on one event_date."""
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks', headers=H_READ,
        params={'event_date': f'eq.{event_date}', 'select': '*',
                'order': 'fight_order.asc'},
        timeout=15,
    )
    picks = r.json() if r.status_code == 200 else []
    if not picks: return 0
    # Bulk-fetch fighter_stats for the card
    names = set()
    for p in picks:
        if p.get('fighter_a'): names.add(p['fighter_a'])
        if p.get('fighter_b'): names.add(p['fighter_b'])
    in_list = ','.join(f'"{n}"' for n in names)
    s = requests.get(
        f'{SB}/rest/v1/ufc_fighter_stats', headers=H_READ,
        params={'fighter_name': f'in.({in_list})', 'select': '*'},
        timeout=15,
    )
    stats_rows = s.json() if s.status_code == 200 else []
    stats_map = {row['fighter_name'].lower(): row for row in stats_rows}

    updated = 0
    for pk in picks:
        view = build_detail_view(pk, stats_map)
        if dry_run:
            print(f'  DRY {pk["fighter_a"][:15]}@{pk["fighter_b"][:15]}: view built')
            continue
        r2 = requests.patch(
            f'{SB}/rest/v1/ufc_picks?id=eq.{pk["id"]}',
            headers=H_WRITE, data=json.dumps({'detail_view': view}), timeout=10,
        )
        if r2.status_code in (200, 204): updated += 1
        else: print(f'  ⚠ PATCH {r2.status_code} for id {pk["id"]}: {r2.text[:150]}')
    print(f'  refreshed detail_view for {updated}/{len(picks)} picks on {event_date}')
    return updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--event-date', help='YYYY-MM-DD — refresh one card')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.event_date:
        refresh_for_event(args.event_date, dry_run=args.dry_run)
    elif args.all:
        r = requests.get(f'{SB}/rest/v1/ufc_picks', headers=H_READ,
                         params={'select': 'event_date', 'odds_a_median': 'not.is.null', 'limit': '5000'},
                         timeout=30).json()
        dates = sorted({p['event_date'] for p in r if p.get('event_date')})
        print(f'  refreshing {len(dates)} event dates...')
        for d in dates: refresh_for_event(d, dry_run=args.dry_run)
    else:
        print('Specify --event-date or --all')


if __name__ == '__main__':
    main()
