"""NCAAB Panel model (2026-08-14).

Session 3 of NCAAB 5-lens build · lens #2 of 5 (MC + Panel + V4 + KenPom + Jerry).

The Panel is the wisdom-of-crowds lens. Reads latest snapshot from EACH
independent rating system (KenPom + Torvik + Haslam) and averages into
a single projection. The value comes from two places:

  1. Point projection (mean of systems) beats any individual source on
     out-of-sample calibration (well-established in academic literature —
     Bayesian model averaging beats single-model).
  2. Dispersion (stddev across systems) is a leading indicator of edge:
     systems agreeing tightly = high conviction; systems disagreeing =
     uncertainty gate (Panel skips low-agreement games).

METHODOLOGY
  Per team, average across available systems (min 2 required):
    panel_em    = mean(kenpom.adj_em, torvik.adj_em, haslam.adj_em)
    panel_off   = mean(kenpom.adj_off, torvik.adj_off, haslam.adj_off)
    panel_def   = mean(kenpom.adj_def, torvik.adj_def, haslam.adj_def)
    panel_em_stddev = stddev of adj_em across those systems

  Per game (same efficiency framework as MC — apples-to-apples):
    pace         = (home_tempo + away_tempo) / 2  [from ncaab_game_context]
    home_eff     = panel_home_off × (panel_away_def / league_avg_eff)
    away_eff     = panel_away_off × (panel_home_def / league_avg_eff)
    home_expect  = home_eff × pace / 100 + HFA (unless neutral)
    away_expect  = away_eff × pace / 100
    panel_margin = home_expect - away_expect
    panel_total  = home_expect + away_expect

  Confidence:
    max_stddev = max(panel_em_stddev_home, panel_em_stddev_away)
    high   if max_stddev <  2.0
    medium if max_stddev <  4.0
    low    if max_stddev >= 4.0

INPUT SOURCES
  ncaab_rating_snapshots — latest snapshot per (team, system, season)
  ncaab_game_context     — today's games + home_tempo/away_tempo (KenPom join)

OUTPUT
  ncaab_game_context.panel_prediction JSONB — see migration for shape

CLI
  python ncaab_panel_predictor.py                    # today's slate
  python ncaab_panel_predictor.py --game-date 2026-11-04
  python ncaab_panel_predictor.py --dry-run          # print, don't write
"""
from __future__ import annotations
import argparse, os, sys, statistics
from datetime import date, datetime, timezone
from collections import defaultdict
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

LEAGUE_AVG_EFF = 106.0
HFA_POINTS = 3.5
MIN_SYSTEMS_REQUIRED = 2

STDDEV_HIGH_CONF = 2.0
STDDEV_MED_CONF = 4.0


def _load_latest_snapshots() -> dict:
    """Return {team_name: {system: {adj_em, adj_off, adj_def}}}
    from most-recent snapshot per (team, system, season)."""
    r = requests.get(
        f'{SB}/rest/v1/ncaab_rating_snapshots?select=team,rating_system,'
        f'adj_off,adj_def,adj_em,snapshot_date,season'
        f'&order=snapshot_date.desc&limit=5000',
        headers=H_READ, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ ncaab_rating_snapshots fetch {r.status_code}: {r.text[:200]}')
        return {}
    rows = r.json() or []

    latest = defaultdict(dict)   # {team: {system: dict}}
    seen = set()                 # (team, system) already recorded
    for row in rows:
        team = row.get('team'); sys_name = row.get('rating_system')
        if not team or not sys_name: continue
        key = (team, sys_name)
        if key in seen: continue
        seen.add(key)
        em = row.get('adj_em'); off = row.get('adj_off'); de = row.get('adj_def')
        if em is None or off is None or de is None: continue
        latest[team][sys_name] = {
            'adj_em': float(em), 'adj_off': float(off), 'adj_def': float(de)}
    return dict(latest)


def _panel_team(systems: dict) -> Optional[dict]:
    """Aggregate one team's snapshots across systems. None if <2 systems."""
    if not systems or len(systems) < MIN_SYSTEMS_REQUIRED:
        return None
    ems = [s['adj_em'] for s in systems.values()]
    offs = [s['adj_off'] for s in systems.values()]
    defs = [s['adj_def'] for s in systems.values()]
    return {
        'panel_em': statistics.fmean(ems),
        'panel_off': statistics.fmean(offs),
        'panel_def': statistics.fmean(defs),
        'panel_em_stddev': statistics.pstdev(ems) if len(ems) > 1 else 0.0,
        'systems_count': len(systems),
        'systems_list': sorted(systems.keys()),
    }


def _load_games(game_date: date) -> list:
    fields = ('game_id,game_date,home_team,away_team,close_total,close_spread,'
              'is_neutral_site,home_tempo,away_tempo,'
              'home_adj_em,away_adj_em')  # KenPom values for delta sanity
    r = requests.get(
        f'{SB}/rest/v1/ncaab_game_context?select={fields}'
        f'&game_date=eq.{game_date.isoformat()}',
        headers=H_READ, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ ncaab_game_context fetch {r.status_code}: {r.text[:200]}')
        return []
    return r.json() or []


def predict_game(home_panel: dict, away_panel: dict,
                 pace: float, neutral: bool = False,
                 home_kenpom_em: Optional[float] = None,
                 away_kenpom_em: Optional[float] = None) -> dict:
    """Return panel_prediction blob for one game."""
    home_eff = home_panel['panel_off'] * (away_panel['panel_def'] / LEAGUE_AVG_EFF)
    away_eff = away_panel['panel_off'] * (home_panel['panel_def'] / LEAGUE_AVG_EFF)

    home_expect = home_eff * pace / 100.0 + (0.0 if neutral else HFA_POINTS)
    away_expect = away_eff * pace / 100.0

    panel_margin = home_expect - away_expect
    panel_total = home_expect + away_expect

    max_std = max(home_panel['panel_em_stddev'], away_panel['panel_em_stddev'])
    if max_std < STDDEV_HIGH_CONF: conf = 'high'
    elif max_std < STDDEV_MED_CONF: conf = 'medium'
    else: conf = 'low'

    blob = {
        'panel_projected_margin': round(panel_margin, 2),
        'panel_projected_total': round(panel_total, 2),
        'panel_home_em': round(home_panel['panel_em'], 2),
        'panel_away_em': round(away_panel['panel_em'], 2),
        'panel_em_gap': round(home_panel['panel_em'] - away_panel['panel_em'], 2),
        'panel_home_off': round(home_panel['panel_off'], 2),
        'panel_away_off': round(away_panel['panel_off'], 2),
        'panel_home_def': round(home_panel['panel_def'], 2),
        'panel_away_def': round(away_panel['panel_def'], 2),
        'panel_systems_home': home_panel['systems_count'],
        'panel_systems_away': away_panel['systems_count'],
        'panel_systems_home_list': home_panel['systems_list'],
        'panel_systems_away_list': away_panel['systems_list'],
        'panel_em_stddev_home': round(home_panel['panel_em_stddev'], 2),
        'panel_em_stddev_away': round(away_panel['panel_em_stddev'], 2),
        'panel_confidence': conf,
        'panel_neutral_site': bool(neutral),
        'panel_pace_used': round(pace, 2),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    # Sanity delta: how much does Panel differ from single-source KenPom?
    if home_kenpom_em is not None and away_kenpom_em is not None:
        kenpom_margin = (home_kenpom_em - away_kenpom_em) + (0.0 if neutral else HFA_POINTS)
        blob['panel_vs_kenpom_margin_delta'] = round(panel_margin - kenpom_margin, 2)

    return blob


def _write_panel(game_id, blob: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/ncaab_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json={'panel_prediction': blob}, timeout=15)
    if r.status_code not in (200, 204):
        print(f'  ✗ write game {game_id}: {r.status_code} {r.text[:150]}')
        return False
    return True


def run(game_date: Optional[date] = None, dry_run: bool = False) -> int:
    if game_date is None:
        game_date = datetime.now(timezone.utc).date()
    print(f'=== ncaab_panel_predictor · {game_date.isoformat()} ===')

    try:
        from data_quality import DQ
        dq = DQ(source='ncaab_panel_predictor.py', sport='NCAAB')
    except Exception:
        dq = None

    snapshots = _load_latest_snapshots()
    team_panels = {}
    for team, systems in snapshots.items():
        p = _panel_team(systems)
        if p is not None:
            team_panels[team] = p
    print(f'  panel-eligible teams: {len(team_panels)} (min {MIN_SYSTEMS_REQUIRED} systems)')
    if dq: dq.assert_range(len(team_panels), 100, 400, 'ncaab_panel_team_count',
                            context={'n': len(team_panels)})

    if not team_panels:
        print('  ✗ no panel-eligible teams — skipped'); return 0

    games = _load_games(game_date)
    print(f'  games on slate: {len(games)}')
    if not games:
        print('  ° no games scheduled — panel no-op (expected off-season)')
        return 0

    n_ok = 0; n_missing = 0; n_default_pace = 0
    for g in games:
        home_name = g.get('home_team'); away_name = g.get('away_team')
        home_p = team_panels.get(home_name)
        away_p = team_panels.get(away_name)
        if not (home_p and away_p):
            n_missing += 1
            miss = [n for n, p in [('home', home_p), ('away', away_p)] if not p]
            print(f'  ° skip {away_name} @ {home_name} — missing panel: {miss}')
            continue

        # Pace: prefer game_context join; default to national avg if missing
        h_tempo, a_tempo = g.get('home_tempo'), g.get('away_tempo')
        if h_tempo is not None and a_tempo is not None:
            pace = (float(h_tempo) + float(a_tempo)) / 2.0
        else:
            pace = 66.0   # D-I median tempo
            n_default_pace += 1

        blob = predict_game(
            home_p, away_p, pace,
            neutral=bool(g.get('is_neutral_site')),
            home_kenpom_em=g.get('home_adj_em'),
            away_kenpom_em=g.get('away_adj_em'))

        if dry_run:
            delta = blob.get('panel_vs_kenpom_margin_delta', '—')
            print(f'  [DRY] {away_name} @ {home_name}: '
                  f'margin={blob["panel_projected_margin"]:+.1f} '
                  f'total={blob["panel_projected_total"]:.1f} '
                  f'conf={blob["panel_confidence"]} '
                  f'Δkenpom={delta}')
            n_ok += 1
        else:
            if _write_panel(g['game_id'], blob):
                n_ok += 1

    print(f'  ✓ {n_ok} panel predictions written; '
          f'{n_missing} skipped (coverage gap); '
          f'{n_default_pace} used default pace')
    return n_ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--game-date', help='YYYY-MM-DD; defaults to today')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = date.fromisoformat(args.game_date) if args.game_date else None
    run(gd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
