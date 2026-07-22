"""Consensus-fade alert detector — SUBSTANTIATED version (v2).

For each game on today's slate, aggregates external picks + looks up
the matching bucket in consensus_bucket_calibration to answer "does
THIS type of consensus (sport, surface, pct band, model alignment)
historically LOSE?" — only then fires FADE. Otherwise labels as
MONITORING (transparent — no unsubstantiated alerts).

v1 (7/22 morning) fired on ANY 75%+ consensus. User caught the flaw:
"some consensus is signal, some is noise — how do we tell?" v2 answer:
look up historical hit rate for the specific bucket the game falls into.

Detector states written to <sport>_game_context:
  consensus_fade_flag = TRUE  -> confirmed fade (audit hit_pct < 48%,
                                 n >= 20). App shows RED alert chip.
  consensus_fade_flag = FALSE + consensus_fade_note starts 'MONITORING'
                             -> consensus exists but audit isn't
                                substantiated yet. App shows YELLOW
                                monitoring note instead of alert.
  no fields set        -> no notable consensus.

NFL day-1 problem (few graded games until Week 6+): confidence='low'
buckets get MONITORING treatment automatically. No false NFL alerts
until we have real sample size.

Sport-parameterized: --sport MLB (default) / NFL / NCAAB.

USAGE:
    python detect_consensus_fade.py                    # today MLB
    python detect_consensus_fade.py --date 2026-07-22
    python detect_consensus_fade.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

# Consensus detection thresholds (game qualifies for LOOKUP, not for
# firing FADE — firing requires audit substantiation).
CONSENSUS_PCT_THRESHOLD = 0.75   # 75%+ books on one side
CONSENSUS_MIN_SOURCES = 5         # at least 5 books to be meaningful

# Substantiation thresholds — bucket must clear these for FADE to fire.
# Below these the flag stays FALSE with a MONITORING note.
FADE_AUDIT_HIT_PCT_MAX = 48.0    # bucket must hit <48% historically
FADE_AUDIT_MIN_N = 20             # bucket must have n >= 20 graded picks

# Sport-specific game_context table
SPORT_CONTEXT_TABLE = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAB': 'ncaab_game_context',
}

PCT_BANDS = [
    ('75-84',  0.75, 0.849),
    ('85-94',  0.85, 0.949),
    ('95-100', 0.95, 1.001),
]


def _pct_band(pct: float) -> Optional[str]:
    for label, lo, hi in PCT_BANDS:
        if lo <= pct <= hi:
            return label
    return None


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def fetch_picks(game_date: str, sport: str) -> list:
    r = requests.get(
        f'{SB}/rest/v1/external_picks?'
        f'game_date=eq.{game_date}&sport=eq.{sport}'
        f'&select=game_id,source,surface,pick_side,fade_flag'
        f'&limit=1000',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_bucket_calibration(sport: str) -> dict:
    """Load latest 30d calibration keyed by (surface, pct_band, alignment)."""
    r = requests.get(
        f'{SB}/rest/v1/consensus_bucket_calibration'
        f'?sport=eq.{sport}&window_label=eq.30d'
        f'&select=surface,pct_band,model_alignment,hit_pct,sample_n,confidence,computed_date'
        f'&order=computed_date.desc&limit=200',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        return {}
    out = {}
    for row in r.json():
        key = (row['surface'], row['pct_band'], row['model_alignment'])
        if key not in out:   # latest computed_date wins (already ordered)
            out[key] = row
    return out


def fetch_model_direction(sport: str, game_id: str) -> dict:
    """Return {ml_side, total_side} from our composite projections."""
    tbl = SPORT_CONTEXT_TABLE[sport]
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{game_id}'
        f'&select=projected_spread,projected_total,close_total&limit=1',
        headers=H_READ, timeout=10,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows: return {'ml_side': None, 'total_side': None}
    row = rows[0]
    ml_side = total_side = None
    ps = row.get('projected_spread')
    if ps is not None:
        try: ml_side = 'HOME' if float(ps) > 0 else 'AWAY' if float(ps) < 0 else None
        except: pass
    pt = row.get('projected_total'); ct = row.get('close_total')
    if pt is not None and ct is not None:
        try: total_side = 'OVER' if float(pt) > float(ct) else 'UNDER' if float(pt) < float(ct) else None
        except: pass
    return {'ml_side': ml_side, 'total_side': total_side}


def compute_alerts(picks: list, sport: str) -> dict:
    """Group picks by game+surface, detect consensus, look up substantiation.

    Returns {game_id: {flag, side, pct, n, surface, note}}. flag=True only
    when audit substantiates (hit_pct < FADE_AUDIT_HIT_PCT_MAX at n>=FADE_AUDIT_MIN_N).
    Otherwise flag=False with a MONITORING note.
    """
    bucket_cal = fetch_bucket_calibration(sport)

    picks_by = defaultdict(list)
    for p in picks:
        picks_by[(p['game_id'], p['surface'])].append(p)

    alerts = {}
    for (gid, surface), plist in picks_by.items():
        if surface not in ('ml', 'spread', 'rl', 'total'):
            continue
        by_side = defaultdict(list)
        for p in plist:
            side = (p.get('pick_side') or '').upper()
            if side: by_side[side].append(p)
        total = sum(len(v) for v in by_side.values())
        if total < CONSENSUS_MIN_SOURCES:
            continue
        dominant_side, side_picks = max(by_side.items(), key=lambda kv: len(kv[1]))
        pct = len(side_picks) / total
        if pct < CONSENSUS_PCT_THRESHOLD:
            continue

        band = _pct_band(pct)
        model_dir = fetch_model_direction(sport, gid)
        our_side = model_dir['total_side'] if surface == 'total' else model_dir['ml_side']
        if our_side is None:
            alignment = 'unknown'
        elif our_side == dominant_side:
            alignment = 'aligned'
        else:
            alignment = 'contra'

        # Substantiation lookup
        bucket = bucket_cal.get((surface, band, alignment))
        substantiated_fade = False
        audit_str = 'no audit yet — MONITORING'
        if bucket:
            hp = bucket.get('hit_pct')
            n = bucket.get('sample_n') or 0
            conf = bucket.get('confidence') or 'low'
            if hp is not None and n >= FADE_AUDIT_MIN_N and hp < FADE_AUDIT_HIT_PCT_MAX:
                substantiated_fade = True
                audit_str = f'AUDIT: {band}% consensus historically hits {hp}% (n={n}, {conf})'
            elif hp is not None:
                audit_str = f'audit: {band}% consensus hits {hp}% (n={n}, {conf}) — MONITORING'

        # Fade-tagged sources on dominant side (secondary signal)
        fade_sources = [p['source'] for p in side_picks
                        if p.get('fade_flag') == 'fade']

        note_bits = [f'{len(side_picks)}/{total} books on {dominant_side}',
                     f'model {alignment}']
        if fade_sources:
            note_bits.append(f'audit-fade sources: {", ".join(fade_sources)}')
        note_bits.append(audit_str)

        surface_rank = {'ml': 0, 'total': 1, 'spread': 2, 'rl': 3}
        candidate = {
            'flag': substantiated_fade,
            'side': dominant_side, 'pct': round(pct, 3),
            'n': total, 'surface': surface,
            'note': ' · '.join(note_bits),
            'rank': surface_rank.get(surface, 9),
        }
        existing = alerts.get(gid)
        if existing is None or candidate['rank'] < existing['rank']:
            alerts[gid] = candidate
    return alerts


def patch_context(gid: str, alert: dict, sport: str, dry_run: bool = False) -> bool:
    if dry_run:
        return True
    tbl = SPORT_CONTEXT_TABLE[sport]
    payload = {
        'consensus_fade_flag': alert['flag'],
        'consensus_fade_side': alert['side'],
        'consensus_fade_pct': alert['pct'],
        'consensus_fade_n': alert['n'],
        'consensus_fade_note': alert['note'],
    }
    r = requests.patch(
        f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
        headers=H_WRITE, json=payload, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def clear_stale_flags(game_date: str, active_gids: set, sport: str,
                      dry_run: bool = False) -> int:
    """Games that USED to have consensus fade but no longer meet criteria
    should get flag reset. Prevents yesterday's alerts from persisting."""
    tbl = SPORT_CONTEXT_TABLE[sport]
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?game_date=eq.{game_date}'
        f'&consensus_fade_flag=eq.true&select=game_id',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        return 0
    prev = {g['game_id'] for g in r.json()}
    stale = prev - active_gids
    if not stale:
        return 0
    if dry_run:
        print(f'  [DRY] would clear stale fade flag on {len(stale)} games')
        return len(stale)
    for gid in stale:
        payload = {
            'consensus_fade_flag': None, 'consensus_fade_side': None,
            'consensus_fade_pct': None, 'consensus_fade_n': None,
            'consensus_fade_note': None,
        }
        requests.patch(
            f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
            headers=H_WRITE, json=payload, timeout=15,
        )
    return len(stale)


def run(sport: str = 'MLB', game_date: Optional[str] = None,
        dry_run: bool = False) -> None:
    game_date = game_date or _et_now().date().isoformat()
    print(f'=== consensus-fade detector v2 · {sport} · {game_date} ===')

    picks = fetch_picks(game_date, sport)
    print(f'  external picks pulled: {len(picks)}')
    if not picks:
        return

    alerts = compute_alerts(picks, sport)
    n_fade = sum(1 for a in alerts.values() if a['flag'])
    n_monitor = sum(1 for a in alerts.values() if not a['flag'])
    print(f'  substantiated FADE alerts: {n_fade}')
    print(f'  MONITORING (consensus, no audit yet): {n_monitor}')

    total_written = 0
    for gid, a in alerts.items():
        state = '🚨 FADE' if a['flag'] else 'MONITORING'
        if dry_run:
            print(f"    [DRY] {state:12} {gid[:12]}...  "
                  f"{a['surface']:6}:{a['side']:5}  "
                  f"{a['pct']*100:5.1f}% n={a['n']}  {a['note']}")
        else:
            if patch_context(gid, a, sport):
                total_written += 1

    # Clear stale entries on today's games not in this run
    cleared = clear_stale_flags(game_date, set(alerts.keys()), sport, dry_run=dry_run)

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {total_written} rows ({n_fade} fade + {n_monitor} monitoring) · cleared {cleared} stale')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB',
                    choices=list(SPORT_CONTEXT_TABLE.keys()))
    ap.add_argument('--date', default=None,
                    help='YYYY-MM-DD (default today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport=args.sport, game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
