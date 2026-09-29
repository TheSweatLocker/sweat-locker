"""Scrape LIVE NFL depth chart as of today, extract QB1/RB1/WR1/WR2/TE1
per team, and diff against the authoritative `_NFL_QB1_MAP` in
`nfl_game_context.py`.

2026-09-14: Andy reported nflverse `rosters.csv` team assignments are stale
(reflect prior-season teams, e.g. Kamara listed as NO RB1 when NO started
Etienne). This script pulls the nflverse `depth_charts_2026.csv` release
asset (refreshed daily) and picks pos_rank=1 (RB/QB/TE) + top-2 (WR)
per team from the offense pos_grp ("3WR 1TE").

Usage:
    python nfl_live_depth_scrape.py             # default: nflverse feed
    python nfl_live_depth_scrape.py --espn      # fallback: ESPN HTML depth chart

Output:
    mlb_pipeline/nfl_current_depth_chart.json   (untracked scratch)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, 'nfl_current_depth_chart.json')

NFLVERSE_URL = 'https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_2026.csv'

# Andy's manual corrections for cross-check reporting.
ANDY_FLAGGED: list[tuple[str, str, str]] = [
    ('ATL', 'QB1', 'Cooper Rush'),
    ('NYJ', 'QB1', 'Geno Smith'),
    ('NO',  'RB1', 'Travis Etienne'),
    ('TB',  'WR1', 'Mike Evans'),
    ('PHI', 'RB1', 'Saquon Barkley'),
    ('NE',  'RB1', 'Rhamondre Stevenson'),
]

# 32 NFL abbrs — used to sanity-check coverage.
TEAMS_32 = [
    'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB',
    'HOU','IND','JAX','KC','LA','LAC','LV','MIA','MIN','NE','NO','NYG',
    'NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS',
]

# ESPN uses lowercased abbrs with a couple of quirks.
ESPN_ABBR_MAP: dict[str, str] = {t: t.lower() for t in TEAMS_32}
ESPN_ABBR_MAP.update({'LA': 'lar', 'WAS': 'wsh', 'JAX': 'jax'})


def _load_current_qb1_map() -> dict[str, str]:
    """Read _NFL_QB1_MAP from nfl_game_context.py without importing it
    (import would try to hit Supabase). Regex-parse the literal instead."""
    path = os.path.join(HERE, 'nfl_game_context.py')
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()
    m = re.search(r"_NFL_QB1_MAP:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\n\}", src, re.DOTALL)
    if not m:
        raise RuntimeError('could not locate _NFL_QB1_MAP literal')
    body = m.group(1)
    out: dict[str, str] = {}
    # matches: 'ABC': 'Player Name'
    for team, name in re.findall(r"'([A-Z]{2,3})':\s*'([^']+)'", body):
        out[team] = name
    return out


def _fetch_nflverse() -> pd.DataFrame:
    print(f'[fetch] {NFLVERSE_URL}', flush=True)
    r = requests.get(NFLVERSE_URL, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content))
    # normalize dt
    df['dt'] = pd.to_datetime(df['dt'], utc=True, errors='coerce')
    return df


def _pick_starters_nflverse(df: pd.DataFrame) -> tuple[dict[str, dict[str, str]], str]:
    """Filter to newest snapshot, offense pos_grp, and pick starters."""
    latest = df['dt'].max()
    print(f'[nflverse] latest snapshot: {latest.isoformat()}')
    latest_df = df[df['dt'] == latest].copy()
    # offense only. NFLverse uses "3WR 1TE" for base offense in 2026 dump.
    off = latest_df[latest_df['pos_grp'].astype(str).str.contains('WR', na=False)]
    if off.empty:
        off = latest_df  # fallback if the naming shifted
    per_team: dict[str, dict[str, str]] = {t: {} for t in TEAMS_32}
    for team in TEAMS_32:
        sub = off[off['team'] == team]
        if sub.empty:
            continue
        # QB1
        qbs = sub[sub['pos_abb'] == 'QB'].sort_values('pos_rank')
        if not qbs.empty:
            per_team[team]['QB1'] = qbs.iloc[0]['player_name']
        # RB1
        rbs = sub[sub['pos_abb'] == 'RB'].sort_values('pos_rank')
        if not rbs.empty:
            per_team[team]['RB1'] = rbs.iloc[0]['player_name']
        # WR1, WR2
        wrs = sub[sub['pos_abb'] == 'WR'].sort_values('pos_rank')
        if len(wrs) >= 1:
            per_team[team]['WR1'] = wrs.iloc[0]['player_name']
        if len(wrs) >= 2:
            per_team[team]['WR2'] = wrs.iloc[1]['player_name']
        # TE1
        tes = sub[sub['pos_abb'] == 'TE'].sort_values('pos_rank')
        if not tes.empty:
            per_team[team]['TE1'] = tes.iloc[0]['player_name']
    return per_team, latest.isoformat()


_TRAILING_TAG_RE = re.compile(r'(Q|IR|SUSP|PUP|DNR|D|O|NFI|COV)$')


def _clean_name(name: str) -> str:
    """Strip trailing ESPN injury tags like 'Q'/'IR' pasted onto the name."""
    name = name.strip()
    m = _TRAILING_TAG_RE.search(name)
    if m and len(name) - len(m.group(0)) >= 4:
        # peel it off only if a real name remains before the tag
        name = name[: -len(m.group(0))].rstrip()
    return name


def _fetch_espn_depth(team_abbr: str) -> Optional[dict[str, str]]:
    """Fallback: scrape ESPN's team depth-chart page.

    ESPN renders the depth chart as PAIRED tables: an odd-indexed
    "labels" table (POS column: QB / RB / WR / WR / WR / TE ...) and the
    next even-indexed "data" table (Starter | 2nd | 3rd | 4th). We zip
    them row-for-row.
    """
    espn_abbr = ESPN_ABBR_MAP.get(team_abbr, team_abbr.lower())
    url = f'https://www.espn.com/nfl/team/depth/_/name/{espn_abbr}'
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f'[espn] {team_abbr} fail: {e}', flush=True)
        return None
    soup = BeautifulSoup(r.text, 'html.parser')
    tables = soup.find_all('table')
    out: dict[str, str] = {}
    # Pair (labels, data). ESPN emits pairs of tables — labels first, data next.
    wr_seen = 0
    for i in range(0, len(tables) - 1, 2):
        label_tbl = tables[i]
        data_tbl = tables[i + 1]
        label_rows = label_tbl.find_all('tr')
        data_rows = data_tbl.find_all('tr')
        for lr, dr in zip(label_rows, data_rows):
            lcell = lr.find_all(['td', 'th'])
            dcells = dr.find_all(['td', 'th'])
            if not lcell or not dcells:
                continue
            pos = lcell[0].get_text(strip=True).upper()
            names = [c.get_text(strip=True) for c in dcells]
            if not names:
                continue
            first = _clean_name(names[0])
            if first in ('', '-', 'Starter'):
                continue
            if pos == 'QB' and 'QB1' not in out:
                out['QB1'] = first
            elif pos == 'RB' and 'RB1' not in out:
                out['RB1'] = first
            elif pos == 'WR':
                wr_seen += 1
                if wr_seen == 1:
                    out['WR1'] = first
                elif wr_seen == 2:
                    out['WR2'] = first
            elif pos == 'TE' and 'TE1' not in out:
                out['TE1'] = first
    return out or None


def _pick_starters_espn() -> tuple[dict[str, dict[str, str]], str]:
    per_team: dict[str, dict[str, str]] = {}
    for i, team in enumerate(TEAMS_32):
        got = _fetch_espn_depth(team)
        per_team[team] = got or {}
        print(f'[espn] {team}: {got}', flush=True)
        time.sleep(0.4)
    return per_team, 'ESPN live HTML @ ' + pd.Timestamp.utcnow().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--espn', action='store_true', help='force ESPN HTML fallback')
    args = ap.parse_args()

    if args.espn:
        per_team, freshness = _pick_starters_espn()
        source = 'espn_html'
    else:
        df = _fetch_nflverse()
        per_team, freshness = _pick_starters_nflverse(df)
        source = 'nflverse_depth_charts_2026'
        # Guard: if latest snapshot older than 7 days, warn.
        latest_ts = pd.to_datetime(freshness)
        age_days = (pd.Timestamp.utcnow() - latest_ts).total_seconds() / 86400.0
        if age_days > 7:
            print(f'[warn] nflverse snapshot is {age_days:.1f} days old — '
                  f'consider rerunning with --espn', flush=True)

    # -------- table print --------
    print()
    print('=' * 84)
    print(f' NFL LIVE DEPTH CHART  (source={source}, freshness={freshness})')
    print('=' * 84)
    header = f' {"TEAM":4}  {"QB1":22} {"RB1":22} {"WR1":22} {"WR2":22} {"TE1":22}'
    print(header)
    print('-' * len(header))
    for team in TEAMS_32:
        r = per_team.get(team, {})
        print(f' {team:4}  '
              f'{(r.get("QB1") or "-"):22} '
              f'{(r.get("RB1") or "-"):22} '
              f'{(r.get("WR1") or "-"):22} '
              f'{(r.get("WR2") or "-"):22} '
              f'{(r.get("TE1") or "-"):22}')

    # -------- diff vs current QB1 map --------
    cur_map = _load_current_qb1_map()
    print()
    print('=' * 84)
    print(' QB1 DIFFS vs mlb_pipeline/nfl_game_context.py::_NFL_QB1_MAP')
    print('=' * 84)
    n_diff = 0
    for team in TEAMS_32:
        scraped = (per_team.get(team) or {}).get('QB1')
        current = cur_map.get(team)
        if scraped and current and scraped.strip().lower() != current.strip().lower():
            print(f' {team:4}  current={current!r:32}  scraped={scraped!r}')
            n_diff += 1
        elif scraped is None:
            print(f' {team:4}  current={current!r:32}  scraped=<MISSING>')
    if n_diff == 0:
        print(' (no QB1 differences)')
    print(f' total QB1 diffs: {n_diff}')

    # -------- Andy's flagged corrections --------
    print()
    print('=' * 84)
    print(" ANDY'S FLAGGED CORRECTIONS — match?")
    print('=' * 84)
    for team, slot, expected in ANDY_FLAGGED:
        got = (per_team.get(team) or {}).get(slot)
        ok = (got or '').strip().lower() == expected.strip().lower()
        mark = 'OK   ' if ok else 'MISS '
        print(f' [{mark}] {team:4} {slot:3}  expected={expected!r:26}  scraped={got!r}')

    # -------- save --------
    payload = {
        'source': source,
        'freshness': freshness,
        'scraped_at_utc': pd.Timestamp.utcnow().isoformat(),
        'per_team': per_team,
    }
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    print()
    print(f'[saved] {OUT_JSON}')


if __name__ == '__main__':
    main()
