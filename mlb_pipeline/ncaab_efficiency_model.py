"""NCAAB efficiency panel materializer (2026-08-19).

The 3-source efficiency panel (kenpom + torvik + haslam) is already
computed live per game inside ncaab_panel_predictor.py, which writes a
JSONB blob to ncaab_game_context.panel_prediction. That's great for
picks — but it means:

  * Ratings only exist for teams appearing in tonight's slate.
  * Offseason scouting / cohort backfills / dashboard rollups have no
    persistent efficiency table to join against.
  * Signal_sources expressions like `ctx.adj_em_gap` re-read live
    snapshots each build; if the KenPom scrape lags a day the game
    contexts diverge from the underlying source.

This script materializes the same panel into ncaab_team_efficiency —
one row per (team, season) — reading directly from the SAME source of
truth (ncaab_rating_snapshots) that ncaab_panel_predictor uses. No new
math, no new external calls. It's a materialized view maintained on a
schedule so downstream consumers can query "current efficiency ratings"
without recomputing the panel.

INTERNAL naming: this module + logs may reference the underlying rating
system by name (kenpom, torvik, haslam). USER-FACING copy (Jerry
writeups, card labels, app text) must NEVER attribute any component
system by name — call it "efficiency model" or "efficiency panel" per
project rule feedback_no_kenpom_attribution.md.

FORMULA (mirrors ncaab_panel_predictor.py exactly)
    Per team, per season, latest snapshot from each system:
        panel_adj_em      = mean(system.adj_em for system in {kp,tv,hm})
        panel_adj_off     = mean(system.adj_off for ...)
        panel_adj_def     = mean(system.adj_def for ...)
        panel_tempo       = mean(system.tempo   for ...)
        panel_em_stddev   = stddev of adj_em across those systems (agreement)
        systems_available = 1..3 (how many of the 3 had a snapshot)

    Also (defense-in-depth, self-derived from box scores):
        ppg_for, ppg_against, avg_margin, W-L splits — computed from
        ncaab_game_results so we retain priors even if all three panel
        systems fail scrape.

CONFIDENCE LADDER (matches Panel MIN_SYSTEMS_REQUIRED=2)
    systems_available >= 2  → efficiency row is "trusted"
    systems_available == 1  → single-source, flagged in notes
    systems_available == 0  → skipped (no panel possible)

Writes to ncaab_team_efficiency
(migration 20260819_ncaab_team_efficiency.sql).

CLI
    python ncaab_efficiency_model.py                     # both seasons
    python ncaab_efficiency_model.py --season 2024-25
    python ncaab_efficiency_model.py --season 2025-26
    python ncaab_efficiency_model.py --dry-run
    python ncaab_efficiency_model.py --top 25
"""
from __future__ import annotations
import argparse, os, statistics, sys
from collections import defaultdict
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
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SYSTEMS = ('kenpom', 'torvik', 'haslam')     # internal names only — never surface
MIN_SYSTEMS_TRUSTED = 2
LEAGUE_AVG_TEMPO = 70.0


def _f(v) -> Optional[float]:
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


# ═══════════════════════════════════════════════════════════════════════
# Loaders
# ═══════════════════════════════════════════════════════════════════════

def fetch_latest_snapshots(season: str) -> dict:
    """Return {system: {team: {adj_em, adj_off, adj_def, tempo}}} using the
    most recent snapshot_date per (system, team) for the given season."""
    out: dict = {sys_: {} for sys_ in SYSTEMS}
    for sys_ in SYSTEMS:
        # First: find latest snapshot_date for this system+season
        r = requests.get(
            f'{SB}/rest/v1/ncaab_rating_snapshots'
            f'?rating_system=eq.{sys_}&season=eq.{season}'
            f'&select=snapshot_date&order=snapshot_date.desc&limit=1',
            headers=H_READ, timeout=30,
        )
        d = r.json() if r.status_code == 200 else []
        if not d: continue
        latest = d[0]['snapshot_date']

        rows: list[dict] = []; off = 0
        while True:
            rr = requests.get(
                f'{SB}/rest/v1/ncaab_rating_snapshots'
                f'?rating_system=eq.{sys_}&season=eq.{season}'
                f'&snapshot_date=eq.{latest}'
                f'&select=team,adj_em,adj_off,adj_def,tempo'
                f'&limit=1000&offset={off}',
                headers=H_READ, timeout=30,
            )
            chunk = rr.json() if rr.status_code == 200 else []
            rows.extend(chunk)
            if len(chunk) < 1000: break
            off += 1000

        for row in rows:
            team = row.get('team')
            if not team: continue
            out[sys_][team] = row
    return out


def fetch_games(season: str) -> list[dict]:
    """Season game log for defense-in-depth PPG rollups."""
    rows: list[dict] = []; off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results'
            f'?select=home_team,away_team,home_score,away_score'
            f'&season=eq.{season}'
            f'&home_score=not.is.null&away_score=not.is.null'
            f'&order=game_date.asc&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


# ═══════════════════════════════════════════════════════════════════════
# Compute
# ═══════════════════════════════════════════════════════════════════════

def compute_box_score_rollups(games: list[dict]) -> dict:
    """Return {team: {games_played, wins, losses, home/away splits,
    points_for, points_against, ppg_for, ppg_against, avg_margin}}."""
    agg: dict[str, dict] = {}
    def _new(t):
        return {'team': t, 'games_played': 0, 'wins': 0, 'losses': 0,
                'home_wins': 0, 'home_losses': 0,
                'away_wins': 0, 'away_losses': 0,
                'points_for': 0, 'points_against': 0}

    for g in games:
        h = g.get('home_team'); a = g.get('away_team')
        try:
            hs = int(g['home_score']); as_ = int(g['away_score'])
        except (TypeError, ValueError, KeyError):
            continue
        if not h or not a: continue
        agg.setdefault(h, _new(h)); agg.setdefault(a, _new(a))
        agg[h]['games_played'] += 1; agg[a]['games_played'] += 1
        agg[h]['points_for']   += hs; agg[h]['points_against'] += as_
        agg[a]['points_for']   += as_; agg[a]['points_against'] += hs
        if hs > as_:
            agg[h]['wins']       += 1; agg[h]['home_wins']    += 1
            agg[a]['losses']     += 1; agg[a]['away_losses']  += 1
        elif as_ > hs:
            agg[a]['wins']       += 1; agg[a]['away_wins']    += 1
            agg[h]['losses']     += 1; agg[h]['home_losses']  += 1

    for team, rec in agg.items():
        gp = rec['games_played'] or 1
        rec['ppg_for']     = round(rec['points_for']     / gp, 2)
        rec['ppg_against'] = round(rec['points_against'] / gp, 2)
        rec['avg_margin']  = round(rec['ppg_for'] - rec['ppg_against'], 2)
    return agg


def compute_panel(snapshots_by_sys: dict, box_agg: dict) -> dict:
    """Merge the 3 rating systems into a single per-team panel record.
    Also fold in box-score derived splits. Returns {team: full_record}."""
    all_teams: set = set()
    for sys_ in SYSTEMS:
        all_teams.update(snapshots_by_sys[sys_].keys())
    all_teams.update(box_agg.keys())

    out: dict[str, dict] = {}
    for team in all_teams:
        systems_hit = []
        adj_em_vals, adj_off_vals, adj_def_vals, tempo_vals = [], [], [], []
        for sys_ in SYSTEMS:
            row = snapshots_by_sys[sys_].get(team)
            if not row: continue
            em  = _f(row.get('adj_em'))
            offv = _f(row.get('adj_off'))
            defv = _f(row.get('adj_def'))
            tmp = _f(row.get('tempo'))
            if em is None and offv is None and defv is None: continue
            systems_hit.append(sys_)
            if em  is not None: adj_em_vals.append(em)
            if offv is not None: adj_off_vals.append(offv)
            if defv is not None: adj_def_vals.append(defv)
            if tmp is not None: tempo_vals.append(tmp)

        rec = {'team': team, 'systems_available': len(systems_hit),
               'systems_hit': ','.join(systems_hit) if systems_hit else None}

        if adj_em_vals:
            rec['panel_adj_em'] = round(statistics.mean(adj_em_vals), 2)
            rec['panel_em_stddev'] = round(statistics.pstdev(adj_em_vals), 2) if len(adj_em_vals) > 1 else 0.0
        if adj_off_vals:
            rec['panel_adj_off'] = round(statistics.mean(adj_off_vals), 2)
        if adj_def_vals:
            rec['panel_adj_def'] = round(statistics.mean(adj_def_vals), 2)
        if tempo_vals:
            rec['panel_tempo'] = round(statistics.mean(tempo_vals), 2)
            rec['tempo_source'] = 'panel_mean'
        else:
            rec['panel_tempo'] = LEAGUE_AVG_TEMPO
            rec['tempo_source'] = 'league_avg_fallback'

        # Fold in box-score rollups (independent priors)
        bx = box_agg.get(team, {})
        rec['games_played']   = bx.get('games_played', 0)
        rec['wins']           = bx.get('wins', 0)
        rec['losses']         = bx.get('losses', 0)
        rec['home_wins']      = bx.get('home_wins', 0)
        rec['home_losses']    = bx.get('home_losses', 0)
        rec['away_wins']      = bx.get('away_wins', 0)
        rec['away_losses']    = bx.get('away_losses', 0)
        rec['points_for']     = bx.get('points_for', 0)
        rec['points_against'] = bx.get('points_against', 0)
        rec['ppg_for']        = bx.get('ppg_for')
        rec['ppg_against']    = bx.get('ppg_against')
        rec['avg_margin']     = bx.get('avg_margin')

        # Self-derived off/def ratings using panel tempo as possessions estimate
        if rec['ppg_for'] is not None and rec['panel_tempo']:
            rec['self_off_rating'] = round(rec['ppg_for']     / rec['panel_tempo'] * 100, 2)
            rec['self_def_rating'] = round(rec['ppg_against'] / rec['panel_tempo'] * 100, 2)
            rec['self_net_rating'] = round(rec['self_off_rating'] - rec['self_def_rating'], 2)
        out[team] = rec
    return out


# ═══════════════════════════════════════════════════════════════════════
# Write
# ═══════════════════════════════════════════════════════════════════════

def build_rows(agg: dict, season: str) -> list[dict]:
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for team, rec in agg.items():
        # Only persist teams that have EITHER panel data OR game log data
        if rec.get('systems_available', 0) == 0 and rec.get('games_played', 0) == 0:
            continue
        rows.append({
            'team': team, 'season': season,
            'games_played':   rec.get('games_played', 0),
            'wins':           rec.get('wins', 0),
            'losses':         rec.get('losses', 0),
            'home_wins':      rec.get('home_wins', 0),
            'home_losses':    rec.get('home_losses', 0),
            'away_wins':      rec.get('away_wins', 0),
            'away_losses':    rec.get('away_losses', 0),
            'points_for':     rec.get('points_for', 0),
            'points_against': rec.get('points_against', 0),
            'ppg_for':        rec.get('ppg_for'),
            'ppg_against':    rec.get('ppg_against'),
            'avg_margin':     rec.get('avg_margin'),
            'est_tempo':      rec.get('panel_tempo'),
            # est_* columns → the PANEL projections (canonical)
            'est_off_rating': rec.get('panel_adj_off') or rec.get('self_off_rating'),
            'est_def_rating': rec.get('panel_adj_def') or rec.get('self_def_rating'),
            'est_net_rating': rec.get('panel_adj_em')  or rec.get('self_net_rating'),
            'data_source':    'panel_kp_tv_hm+ncaab_game_results',
            'tempo_source':   rec.get('tempo_source'),
            'computed_at':    now_iso,
            'updated_at':     now_iso,
        })
    return rows


def upsert(rows: list[dict], dry_run: bool = False) -> int:
    if dry_run or not rows: return len(rows) if not dry_run else 0
    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        r = requests.post(
            f'{SB}/rest/v1/ncaab_team_efficiency?on_conflict=team,season',
            headers=H_WRITE, json=chunk, timeout=30,
        )
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  upsert failed {r.status_code}: {r.text[:180]}')
            if r.status_code == 404:
                print('  → table missing; apply migration 20260819_ncaab_team_efficiency.sql')
                return written
    return written


# ═══════════════════════════════════════════════════════════════════════
# CLI / reporting
# ═══════════════════════════════════════════════════════════════════════

def run(season: str, dry_run: bool = False, top: int = 15) -> dict:
    print(f'=== NCAAB efficiency panel materialization · season {season} ===')

    snaps = fetch_latest_snapshots(season)
    for sys_ in SYSTEMS:
        print(f'  latest {sys_:<7} snapshot rows: {len(snaps[sys_])}')

    games = fetch_games(season)
    print(f'  game log rows: {len(games)}')

    box_agg = compute_box_score_rollups(games)
    agg = compute_panel(snaps, box_agg)
    print(f'  teams with panel or box data: {len(agg)}')

    coverage = defaultdict(int)
    for rec in agg.values():
        coverage[rec.get('systems_available', 0)] += 1
    for k in sorted(coverage.keys()):
        print(f'  systems_available={k}: {coverage[k]} teams')

    rows = build_rows(agg, season)
    written = upsert(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '  '
    print(f'{prefix}{"would write" if dry_run else "wrote"} '
          f'{len(rows) if dry_run else written} rows to ncaab_team_efficiency')

    # Reporting: top/bottom by panel net (adj_em)
    ranked = sorted(
        (r for r in rows if r.get('est_net_rating') is not None),
        key=lambda r: -r['est_net_rating'],
    )
    print(f'\n=== Top {top} teams by panel net (adj_em) · {season} ===')
    for r in ranked[:top]:
        print(f"  {r['team']:<28} net={r['est_net_rating']:+6.2f}  "
              f"off={r['est_off_rating'] or 0:6.2f}  def={r['est_def_rating'] or 0:6.2f}  "
              f"ppg={r.get('ppg_for') or 0:5.1f}/{r.get('ppg_against') or 0:5.1f}  "
              f"W-L={r['wins']}-{r['losses']}  tempo={r['est_tempo']:.1f}")

    print(f'\n=== Bottom 10 by panel net · {season} ===')
    for r in ranked[-10:]:
        print(f"  {r['team']:<28} net={r['est_net_rating']:+6.2f}  "
              f"off={r['est_off_rating'] or 0:6.2f}  def={r['est_def_rating'] or 0:6.2f}  "
              f"W-L={r['wins']}-{r['losses']}")

    nets = [r['est_net_rating'] for r in ranked]
    if nets:
        n = len(nets)
        print(f'\n=== est_net_rating distribution (n={n}) ===')
        print(f"  min: {min(nets):+.2f}   p10: {nets[9*n//10]:+.2f}   "
              f"p50: {nets[n//2]:+.2f}   p90: {nets[n//10]:+.2f}   "
              f"max: {max(nets):+.2f}")

    return {'teams': len(rows), 'coverage': dict(coverage),
            'top': ranked[:5], 'bottom': ranked[-5:] if len(ranked) >= 5 else []}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', default='2024-25')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--top', type=int, default=15)
    args = p.parse_args()
    run(season=args.season, dry_run=args.dry_run, top=args.top)


if __name__ == '__main__':
    main()
