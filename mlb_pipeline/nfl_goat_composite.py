"""nfl_goat_composite — GOAT model shadow writer for NFL.

Fuses seven signal sources into a single composite z-score per game:
  - 0.30 · ensemble    (current NFL primary_play, tier→prob mapped)
  - 0.25 · lr_shadow   (nfl_ml_logreg output, already stamped)
  - 0.15 · talent      (Madden team OVR + QB Madden + Top100 star density)
  - 0.10 · panel       (Sleeper-derived panel_pred point differential)
  - 0.10 · injury      (position-weighted status counts, home vs away delta)
  - 0.05 · rest        (rest days delta, capped ±3)
  - 0.05 · weather     (wind >15 mph penalty; total only)

Writes back to primary_play._goat_shadow with:
  { score, tier, side, p_home, edge_home, contributions, inputs, total, chip }

`chip` is the front-end payload consumed by align.chips_extra so the app's
alignment strip surfaces GOAT alongside Panel / MC / LR without hardcoding
the chip in TSX. TOS-safe tooltip copy — no source names surfaced.

SHADOW-ONLY. Jerry does not weigh GOAT into pick selection. Purpose is
data collection so a real LR fit can be trained on the fused feature
vector after Weeks 1-3 land (~48 games with GOAT prediction + actual
outcome).

USAGE:
    python nfl_goat_composite.py                    # today + next 7 days
    python nfl_goat_composite.py --days 14
    python nfl_goat_composite.py --dry-run
    python nfl_goat_composite.py --date 2026-09-13  # single date
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

WEIGHTS = {
    'ensemble': 0.30, 'lr': 0.25, 'talent': 0.15, 'panel': 0.10,
    'injury': 0.10, 'rest': 0.05, 'weather': 0.05,
}
TIER_TO_P = {'PRIME': 0.85, 'STRONG': 0.68, 'LEAN': 0.55, 'COVERAGE': 0.52, '': 0.5}
POS_WEIGHT = {
    'QB': 4.0, 'RB': 1.2, 'WR': 1.0, 'TE': 0.6, 'OT': 0.8, 'OG': 0.5,
    'C': 0.7, 'DE': 1.2, 'DT': 0.9, 'CB': 1.1, 'S': 0.7, 'LB': 0.8,
    'EDGE': 1.3, 'DL': 0.9, 'OL': 0.6, 'DB': 0.7,
}
STATUS_MULT = {'Out': 1.0, 'Doubtful': 0.7, 'Questionable': 0.3, 'Probable': 0.1}

TOOLTIP = (
    'Proprietary predictive model that fuses team quality signals, '
    'player-level projections, live matchup data, and historical '
    'outcomes into an independent win-probability estimate. Runs in '
    'parallel to primary picks — when it agrees, it strengthens '
    'confidence; when it dissents, it flags the game for closer look.'
)


def _clip(x, lo, hi): return max(lo, min(hi, x))


def _impl_prob(american):
    if american is None: return 0.5
    try: o = float(american)
    except (TypeError, ValueError): return 0.5
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def _tier_from_z(z):
    if z >  0.35: return ('PRIME',   'HOME')
    if z >  0.15: return ('STRONG',  'HOME')
    if z >  0.05: return ('LEAN',    'HOME')
    if z > -0.05: return ('COVERAGE', None)
    if z > -0.15: return ('LEAN',    'AWAY')
    if z > -0.35: return ('STRONG',  'AWAY')
    return ('PRIME', 'AWAY')


def _chip_kind(rel: str, tier: str) -> str:
    """Front-end kind hint for InfoChip color: ok=green, warn=yellow, neutral=grey."""
    if rel == 'agree' and tier in ('PRIME', 'STRONG'): return 'ok'
    if rel == 'dissent':                                return 'warn'
    return 'neutral'


def compute_composite(ctx: dict, injury_impact: dict) -> dict:
    home = ctx.get('home_team', '')
    away = ctx.get('away_team', '')
    pp = ctx.get('primary_play') or {}
    lr = pp.get('_lr_ml_shadow') or {}

    # 1. Ensemble prob signal from primary_play
    ens_type = (pp.get('type') or '').lower()
    ens_side = (pp.get('side') or '').upper()
    ens_tier = (pp.get('tier') or '').upper()
    tier_wt = TIER_TO_P.get(ens_tier, 0.5)
    if ens_type == 'ml':
        p_ens = tier_wt if ens_side == 'HOME' else (1 - tier_wt)
    elif ens_type in ('rl', 'spread'):
        p_ens = 0.5 + (tier_wt - 0.5) * 0.7 * (1 if ens_side == 'HOME' else -1)
    else:
        p_ens = 0.5
    p_ens = _clip(p_ens, 0.02, 0.98)
    s_ens = (p_ens - 0.5) * 2.0

    # 2. LR shadow (already p_home_win)
    p_lr = lr.get('p_home_win')
    p_lr = float(p_lr) if p_lr is not None else 0.5
    s_lr = (p_lr - 0.5) * 2.0

    # 3. Talent — blended Madden team + QB + Top100
    m_home = ctx.get('home_madden_ovr'); m_away = ctx.get('away_madden_ovr')
    m_diff = _clip((m_home - m_away) / 10.0, -1.0, 1.0) if (m_home and m_away) else 0.0
    qb_home = ctx.get('home_qb_madden_ovr'); qb_away = ctx.get('away_qb_madden_ovr')
    qb_diff = _clip((qb_home - qb_away) / 15.0, -1.0, 1.0) if (qb_home and qb_away) else 0.0
    t100_diff = _clip(((ctx.get('home_top100_count') or 0) - (ctx.get('away_top100_count') or 0)) / 5.0, -1.0, 1.0)
    talent = _clip(0.50 * m_diff + 0.35 * qb_diff + 0.15 * t100_diff, -1.0, 1.0)

    # 4. Panel projection point differential (Sleeper-derived)
    ph = ctx.get('panel_pred_home_pts'); pa = ctx.get('panel_pred_away_pts')
    panel = _clip((float(ph) - float(pa)) / 14.0, -1.0, 1.0) if (ph is not None and pa is not None) else 0.0

    # 5. Injury delta (away injuries help home)
    inj_h = injury_impact.get(home, 0.0)
    inj_a = injury_impact.get(away, 0.0)
    injury = _clip((inj_a - inj_h) / 6.0, -1.0, 1.0)

    # 6. Rest advantage
    rh = ctx.get('home_rest'); ra = ctx.get('away_rest')
    rest = _clip((float(rh) - float(ra)) / 4.0, -1.0, 1.0) if (rh is not None and ra is not None) else 0.0

    # 7. Weather — total-only signal; sides get 0
    wind = ctx.get('wind') or 0
    weather_total_penalty = -0.5 if (wind and float(wind) > 15) else 0.0

    # Composite
    contrib = {
        'ensemble': WEIGHTS['ensemble'] * s_ens,
        'lr':       WEIGHTS['lr']       * s_lr,
        'talent':   WEIGHTS['talent']   * talent,
        'panel':    WEIGHTS['panel']    * panel,
        'injury':   WEIGHTS['injury']   * injury,
        'rest':     WEIGHTS['rest']     * rest,
        'weather':  0.0,
    }
    z = sum(contrib.values())
    tier, side = _tier_from_z(z)
    p_home = 0.5 + z / 2.0
    imp_home = _impl_prob(ctx.get('close_home_ml') or ctx.get('home_ml_odds'))
    edge_home = p_home - imp_home

    # Total pick
    goat_total = None
    tot = ctx.get('close_total')
    if tot is not None and ph is not None and pa is not None:
        proj = float(ph) + float(pa) + weather_total_penalty
        diff = proj - float(tot)
        if   diff >  4.5:  goat_total = {'side': 'OVER',  'tier': 'STRONG', 'proj': round(proj, 1)}
        elif diff >  1.5:  goat_total = {'side': 'OVER',  'tier': 'LEAN',   'proj': round(proj, 1)}
        elif diff < -4.5:  goat_total = {'side': 'UNDER', 'tier': 'STRONG', 'proj': round(proj, 1)}
        elif diff < -1.5:  goat_total = {'side': 'UNDER', 'tier': 'LEAN',   'proj': round(proj, 1)}
        else:              goat_total = {'side': 'PASS',  'tier': 'PASS',   'proj': round(proj, 1)}

    # Relationship to current pick (for chip color)
    ens_side_pick = ens_side if ens_type in ('ml', 'rl', 'spread') and ens_side in ('HOME', 'AWAY') else None
    goat_side_pick = side if side in ('HOME', 'AWAY') else None
    if goat_side_pick and ens_side_pick and goat_side_pick == ens_side_pick:
        rel = 'agree'
    elif goat_side_pick and ens_side_pick and goat_side_pick != ens_side_pick:
        rel = 'dissent'
    else:
        rel = 'neutral'

    # Chip payload (front-end reads this from align.chips_extra)
    if goat_side_pick:
        team = ctx.get('home_team') if goat_side_pick == 'HOME' else ctx.get('away_team')
        chip_value = f'{team} · {tier}'
    else:
        chip_value = 'PASS'
    chip = {
        'key': 'goat',
        'label': 'GOAT',
        'value': chip_value,
        'tooltip': TOOLTIP,
        'kind': _chip_kind(rel, tier),
        'priority': 25,  # slots between ext (30) and money (20)
    }

    return {
        'score': round(z, 4),
        'p_home': round(p_home, 4),
        'edge_home': round(edge_home, 4),
        'tier': tier,
        'side': side,
        'rel_to_pick': rel,  # agree | dissent | neutral
        'total': goat_total,
        'contributions': {k: round(v, 4) for k, v in contrib.items()},
        'weights': WEIGHTS,
        'inputs': {
            'p_ens': round(p_ens, 4), 'p_lr': round(p_lr, 4),
            'madden_diff_raw': (m_home - m_away) if (m_home and m_away) else None,
            'qb_madden_diff': (qb_home - qb_away) if (qb_home and qb_away) else None,
            'top100_diff': (ctx.get('home_top100_count') or 0) - (ctx.get('away_top100_count') or 0),
            'panel_pred_diff': round(float(ph) - float(pa), 1) if (ph is not None and pa is not None) else None,
            'injury_impact_home': round(inj_h, 1),
            'injury_impact_away': round(inj_a, 1),
            'rest_diff': (float(rh) - float(ra)) if (rh is not None and ra is not None) else None,
            'wind_mph': wind,
        },
        'chip': chip,
        'model_version': 'goat_v0',
        'computed_at': datetime.now(timezone.utc).isoformat(),
    }


def _fetch_ctxs(date_from: str, date_to: str) -> list:
    url = (f'{SB}/rest/v1/nfl_game_context'
           f'?game_date=gte.{date_from}&game_date=lte.{date_to}'
           f'&select=game_id,game_date,home_team,away_team,'
           f'close_home_ml,close_away_ml,home_ml_odds,away_ml_odds,'
           f'close_spread,close_total,primary_play,wind,'
           f'home_madden_ovr,away_madden_ovr,home_qb_madden_ovr,away_qb_madden_ovr,'
           f'home_top100_count,away_top100_count,'
           f'panel_pred_home_pts,panel_pred_away_pts,home_rest,away_rest'
           f'&order=game_date,kickoff_utc')
    r = requests.get(url, headers=H_R, timeout=30)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def _fetch_injury_impact(cutoff_date: str) -> dict:
    r = requests.get(
        f'{SB}/rest/v1/nfl_injuries', headers=H_R,
        params=[('report_date', f'gte.{cutoff_date}'),
                ('select', 'team,position,injury_status')],
        timeout=30,
    )
    if r.status_code != 200 or not isinstance(r.json(), list): return {}
    impact = {}
    for i in r.json():
        t = i.get('team'); pos = (i.get('position') or '').upper(); st = i.get('injury_status') or ''
        if not t: continue
        w = POS_WEIGHT.get(pos, 0.5) * STATUS_MULT.get(st, 0.0)
        if w > 0:
            impact[t] = impact.get(t, 0.0) + w
    return impact


def _patch(game_id: str, goat_shadow: dict, primary_play: dict) -> bool:
    """Merge goat_shadow into primary_play, patch back to context row.
    Preserves all other primary_play fields (ensemble output, LR shadow, etc)."""
    new_pp = dict(primary_play or {})
    new_pp['_goat_shadow'] = goat_shadow
    r = requests.patch(
        f'{SB}/rest/v1/nfl_game_context', headers=H_W,
        params={'game_id': f'eq.{game_id}'},
        json={'primary_play': new_pp}, timeout=15,
    )
    return r.status_code < 300


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8,
                    help='look-ahead window from today (default 8)')
    ap.add_argument('--date', type=str,
                    help='single date YYYY-MM-DD (overrides --days)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    et = datetime.now(timezone.utc) - timedelta(hours=4)
    today = et.date().isoformat()
    if args.date:
        date_from = date_to = args.date
    else:
        date_from = today
        date_to = (et.date() + timedelta(days=args.days)).isoformat()

    print(f'== nfl_goat_composite == window={date_from} to {date_to} dry_run={args.dry_run}')
    ctxs = _fetch_ctxs(date_from, date_to)
    print(f'  fetched {len(ctxs)} NFL games')
    if not ctxs:
        print('  nothing to score.')
        return 0

    injury_impact = _fetch_injury_impact((et.date() - timedelta(days=14)).isoformat())
    print(f'  fetched injury impact for {len(injury_impact)} teams')

    written = skipped = 0
    for c in ctxs:
        goat = compute_composite(c, injury_impact)
        gid = c['game_id']
        pp = c.get('primary_play') or {}
        rel = goat['rel_to_pick']; tier = goat['tier']; side = goat['side'] or '—'
        print(f"  {c['away_team'][:12]:12s} @ {c['home_team'][:12]:12s}  "
              f"z={goat['score']:+.3f}  tier={tier:8s} side={side:4s}  rel={rel:8s}  "
              f"edge={goat['edge_home']*100:+.1f}%")
        if args.dry_run:
            skipped += 1
            continue
        if _patch(gid, goat, pp):
            written += 1
        else:
            skipped += 1
            print(f'    ⚠ patch failed for {gid}')

    print(f'\n== TOTAL == written={written} skipped={skipped}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
