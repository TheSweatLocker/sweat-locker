"""Enrich game_context with roster_physicality fields (2026-08-23).

Joins roster_physicality → {sport}_game_context so signals can read
`ctx.home_ol_avg_wt` etc. directly without JOINs at scoring time.

Runs AFTER pull_college_rosters.py + AFTER game_context builder.
Idempotent — safe to re-run.

USAGE
─────
    python enrich_ctx_roster_physicality.py --sport NCAAF
    python enrich_ctx_roster_physicality.py --sport NCAAB
    python enrich_ctx_roster_physicality.py --sport NCAAF --days-ahead 14

FIELDS WRITTEN
──────────────
NCAAF:
  home_ol_avg_wt, away_ol_avg_wt, home_dl_avg_wt, away_dl_avg_wt,
  ol_dl_weight_gap_home (home OL avg - away DL avg),
  ol_dl_weight_gap_away (away OL avg - home DL avg),
  home_avg_class_year, away_avg_class_year,
  class_year_edge_home (home - away)

NCAAB:
  home_frontcourt_avg_ht, away_frontcourt_avg_ht,
  frontcourt_ht_gap_home (home FC - away FC),
  home_avg_ht_in, away_avg_ht_in,
  home_avg_class_year, away_avg_class_year,
  class_year_edge_home

Silent per-team NULL if roster_physicality lookup misses (missing team).
Signals evaluate to None → fire nothing for that game.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

CTX_TABLE = {'NCAAF': 'ncaaf_game_context', 'NCAAB': 'ncaab_game_context'}


def load_physicality(sport: str, season: int) -> dict:
    r = urllib.request.Request(
        f'{SB}/rest/v1/roster_physicality'
        f'?sport=eq.{sport}&season=eq.{season}'
        f'&select=team,avg_ht_in,avg_wt_lb,avg_class_year,position_groups',
        headers=H_READ,
    )
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] physicality load: {e}', flush=True)
        return {}
    return {row['team']: row for row in rows}


def load_upcoming(sport: str, days_ahead: int) -> list[dict]:
    tbl = CTX_TABLE[sport]
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    start = date.today().isoformat()
    r = urllib.request.Request(
        f'{SB}/rest/v1/{tbl}'
        f'?game_date=gte.{start}&game_date=lte.{end}'
        f'&select=game_id,home_team,away_team,game_date',
        headers=H_READ,
    )
    try:
        return json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] ctx load: {e}', flush=True)
        return []


def _pg(row: dict, group: str, field: str) -> Optional[float]:
    """Safe nested lookup into row.position_groups[group][field]."""
    if not row: return None
    pg = row.get('position_groups') or {}
    g = pg.get(group) or {}
    v = g.get(field)
    return float(v) if v is not None else None


def compute_ncaaf(home: dict, away: dict) -> dict:
    h_ol = _pg(home, 'ol', 'avg_wt_lb')
    a_ol = _pg(away, 'ol', 'avg_wt_lb')
    h_dl = _pg(home, 'dl', 'avg_wt_lb')
    a_dl = _pg(away, 'dl', 'avg_wt_lb')
    h_cls = home.get('avg_class_year') if home else None
    a_cls = away.get('avg_class_year') if away else None

    out = {
        'home_ol_avg_wt': h_ol,
        'away_ol_avg_wt': a_ol,
        'home_dl_avg_wt': h_dl,
        'away_dl_avg_wt': a_dl,
        'home_avg_class_year': h_cls,
        'away_avg_class_year': a_cls,
    }
    if h_ol is not None and a_dl is not None:
        out['ol_dl_weight_gap_home'] = round(h_ol - a_dl, 2)
    if a_ol is not None and h_dl is not None:
        out['ol_dl_weight_gap_away'] = round(a_ol - h_dl, 2)
    if h_cls is not None and a_cls is not None:
        out['class_year_edge_home'] = round(float(h_cls) - float(a_cls), 2)
    return out


def compute_ncaab(home: dict, away: dict) -> dict:
    h_fc = _pg(home, 'frontcourt', 'avg_ht_in')
    a_fc = _pg(away, 'frontcourt', 'avg_ht_in')
    h_ht = home.get('avg_ht_in') if home else None
    a_ht = away.get('avg_ht_in') if away else None
    h_cls = home.get('avg_class_year') if home else None
    a_cls = away.get('avg_class_year') if away else None

    out = {
        'home_frontcourt_avg_ht': h_fc,
        'away_frontcourt_avg_ht': a_fc,
        'home_avg_ht_in': h_ht,
        'away_avg_ht_in': a_ht,
        'home_avg_class_year': h_cls,
        'away_avg_class_year': a_cls,
    }
    if h_fc is not None and a_fc is not None:
        out['frontcourt_ht_gap_home'] = round(float(h_fc) - float(a_fc), 2)
    if h_cls is not None and a_cls is not None:
        out['class_year_edge_home'] = round(float(h_cls) - float(a_cls), 2)
    return out


def upsert_ctx(sport: str, game_id: str, fields: dict):
    tbl = CTX_TABLE[sport]
    payload = {'game_id': game_id, **fields}
    body = json.dumps([payload]).encode('utf-8')
    url = f'{SB}/rest/v1/{tbl}?on_conflict=game_id'
    hdr = {**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    req = urllib.request.Request(url, data=body, headers=hdr, method='POST')
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:
        # strip-on-400 fallback: column missing → drop it and retry once
        msg = str(e)
        if '400' in msg or 'PGRST204' in msg:
            print(f'[warn] ctx upsert 400 for {game_id}: {msg[:120]}', flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=['NCAAF', 'NCAAB'])
    ap.add_argument('--season', type=int, default=datetime.now().year)
    ap.add_argument('--days-ahead', type=int, default=14)
    args = ap.parse_args()

    sport = args.sport
    print(f'\n[start] {sport} ctx enrichment season={args.season} horizon={args.days_ahead}d',
          flush=True)

    phys = load_physicality(sport, args.season)
    if not phys:
        print(f'[abort] no roster_physicality for {sport} season {args.season}. '
              f'Run pull_college_rosters.py first.', flush=True)
        return
    print(f'[phys] loaded {len(phys)} teams', flush=True)

    upcoming = load_upcoming(sport, args.days_ahead)
    print(f'[games] {len(upcoming)} in horizon', flush=True)

    ok, missing_h, missing_a, missing_both, wrote_null = 0, 0, 0, 0, 0
    for g in upcoming:
        home, away = g['home_team'], g['away_team']
        h_row = phys.get(home)
        a_row = phys.get(away)

        if h_row is None and a_row is None:
            missing_both += 1
            continue
        if h_row is None: missing_h += 1
        if a_row is None: missing_a += 1

        if sport == 'NCAAF':
            fields = compute_ncaaf(h_row or {}, a_row or {})
        else:
            fields = compute_ncaab(h_row or {}, a_row or {})

        # Drop keys where all values are None to avoid overwriting with NULL
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            wrote_null += 1
            continue

        if upsert_ctx(sport, g['game_id'], fields):
            ok += 1

    print(f'\n[done] {sport} enriched={ok} missing_home={missing_h} '
          f'missing_away={missing_a} both_missing={missing_both} '
          f'no_data={wrote_null}', flush=True)


if __name__ == '__main__':
    main()
