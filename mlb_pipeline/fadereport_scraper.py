"""Fadereport full-slate splits scraper (rewritten 2026-08-15 pm).

PERMANENT FIX for daily puller. Previous version parsed rendered HTML
text and only captured the visible "sharp signal cards" (~5-10 per sport).
User confirmed FR has splits for EVERY game on their site — the data was
being missed because it lives in the Next.js RSC payload, not in the
rendered card DOM.

Fix: extract the RSC JSON directly from `self.__next_f.push()` chunks.
The payload contains a full games array with public_bets_pct /
sharp_bets_pct / public_money_pct / sharp_money_pct / sharp_side per
game×market. No Playwright / no browser rendering required — plain
HTTP GET.

Bet types in FR data:
  'moneyline' → 'ml'
  'spread'    → 'rl'
  'ou'        → 'total'

game_id format: `mlb_<team1>-vs-<team2>_YYYY-MM-DD_<bet_type>`
Date in game_id is ET-based game_date (source of truth for filtering).

CLI
  python fadereport_scraper.py                     # all sports today
  python fadereport_scraper.py --sport MLB
  python fadereport_scraper.py --dry-run
"""
from __future__ import annotations
import argparse, os, re, sys, json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SPORT_URL = {
    'MLB':   'https://www.fadereport.com/mlb',
    'NCAAB': 'https://www.fadereport.com/ncaab',
    'NHL':   'https://www.fadereport.com/nhl',
    'NFL':   'https://www.fadereport.com/nfl',
    'NCAAF': 'https://www.fadereport.com/ncaaf',
    'NBA':   'https://www.fadereport.com/nba',
}

SPORT_TABLE = {
    'MLB':   'mlb_game_context',
    'NCAAB': 'ncaab_game_context',
    'NHL':   'nhl_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA':   'nba_game_context',
}

BET_TYPE_MAP = {'moneyline': 'ml', 'spread': 'rl', 'ou': 'total'}


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _load_todays_games(sport: str, snap: date) -> dict:
    """Return {(away_last, home_last): game_id} for fuzzy join."""
    tbl = SPORT_TABLE.get(sport)
    if not tbl: return {}
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?select=game_id,away_team,home_team'
        f'&game_date=eq.{snap.isoformat()}',
        headers=H_READ, timeout=15)
    if r.status_code != 200: return {}
    lookup = {}
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        gid = row.get('game_id')
        away = (row.get('away_team') or '').lower()
        home = (row.get('home_team') or '').lower()
        a_last = away.split()[-1] if away else ''
        h_last = home.split()[-1] if home else ''
        lookup[(a_last, h_last)] = gid
        lookup[(away, home)] = gid
    return lookup


ALIASES = {'blue': 'jays', 'red': 'sox', 'white': 'sox'}
def _fuzzy_resolve(a: str, h: str, lookup: dict) -> Optional[str]:
    a = (a or '').lower(); h = (h or '').lower()
    a_last = a.split()[-1] if a else ''
    h_last = h.split()[-1] if h else ''
    if (a_last, h_last) in lookup: return lookup[(a_last, h_last)]
    for (ak, hk), gid in lookup.items():
        if a_last and (a_last in ak.split() or ak.endswith(a_last)) and \
           h_last and (h_last in hk.split() or hk.endswith(h_last)):
            return gid
    return None


def _extract_games_json(html: str) -> list:
    """Parse Next.js self.__next_f.push chunks, extract full game objects.

    The chunk that has the games array contains a JS-escaped RSC string.
    We unescape it, then extract JSON objects matching the game schema.
    """
    pushes = re.findall(r'self\.__next_f\.push\((\[.*?\])\)', html, re.DOTALL)
    for push in pushes:
        m = re.match(r'\[\d+,"(.*)"\]$', push, re.DOTALL)
        if not m: continue
        try:
            decoded = m.group(1).encode('utf-8').decode('unicode_escape', errors='replace')
        except UnicodeDecodeError:
            continue
        if '"sharp_money_pct"' not in decoded: continue
        # Match full game objects: {"id":NNN,"game_id":"...","sport":"...",...,"final_score":<null|...>...}
        pat = re.compile(
            r'\{"id":\d+,"game_id":"[^"]+","sport":"[^"]+"[^}]*?"final_score":[^,}]+[^}]*?\}'
        )
        games = []
        for match in pat.finditer(decoded):
            try:
                obj = json.loads(match.group(0))
                games.append(obj)
            except json.JSONDecodeError:
                continue
        if games:
            return games
    return []


def scrape_sport(sport: str, dry_run: bool = False) -> int:
    url = SPORT_URL.get(sport)
    if not url:
        print(f'  ✗ unknown sport {sport}'); return 0

    snap = _et_today()
    lookup = _load_todays_games(sport, snap)
    print(f'  · loaded {len(lookup)//2} game_context rows for {snap}')

    # RSC endpoint returns the same HTML page but explicitly typed as RSC —
    # both work. Direct GET, no browser needed.
    try:
        r = requests.get(f'{url}?_rsc=1odh1',
                         headers={'User-Agent': 'Mozilla/5.0 (SweatLocker)'},
                         timeout=25)
    except Exception as e:
        print(f'  ✗ fetch fail: {e}'); return 0
    if r.status_code != 200:
        print(f'  ✗ HTTP {r.status_code}'); return 0

    all_games = _extract_games_json(r.text)
    print(f'  · {len(all_games)} game rows in RSC payload (all history)')

    snap_str = snap.isoformat()
    today_games = [g for g in all_games if snap_str in (g.get('game_id') or '')]
    # Dedupe on (game_id) — same row can appear multiple times
    seen = set(); dedup = []
    for g in today_games:
        gid = g.get('game_id')
        if gid in seen: continue
        seen.add(gid); dedup.append(g)
    today_games = dedup
    print(f'  · {len(today_games)} unique game×market rows for {snap_str} (post-dedupe)')

    signals = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for g in today_games:
        bt = BET_TYPE_MAP.get(g.get('bet_type'))
        if not bt: continue

        # Convention observed in FR data: team1 = away, team2 = home
        away_team = g.get('team1'); home_team = g.get('team2')

        sharp_side_raw = g.get('sharp_side') or ''
        if bt == 'total':
            sharp_norm = sharp_side_raw.lower() if sharp_side_raw.lower() in ('over','under') else ''
        else:
            if sharp_side_raw == away_team: sharp_norm = 'away'
            elif sharp_side_raw == home_team: sharp_norm = 'home'
            else: sharp_norm = ''

        money_side  = g.get('sharp_money_pct')
        money_other = g.get('public_money_pct')
        bets_side   = g.get('sharp_bets_pct')
        bets_other  = g.get('public_bets_pct')

        div = g.get('divergence') or 0
        strength = abs(int(div))
        tier_bin = 'strong' if strength >= 20 else ('lean' if strength >= 10 else 'weak')

        our_gid = _fuzzy_resolve(away_team, home_team, lookup)

        signals.append({
            'snapshot_date': snap_str,
            'sport': sport,
            'game_id': our_gid,
            'away_team': (away_team or '')[:100],
            'home_team': (home_team or '')[:100],
            'game_time_et': None,
            'market': bt,
            'sharp_side_raw': sharp_side_raw[:100],
            'sharp_side_norm': sharp_norm,
            'strength_pts': strength,
            'strength_tier': tier_bin,
            'bets_side_pct': bets_side,
            'money_side_pct': money_side,
            'bets_other_pct': bets_other,
            'money_other_pct': money_other,
            'reasoning': (g.get('ai_blurb') or '')[:500] or None,
            'raw_snapshot': {'fr_id': g.get('id'),
                             'game_time_raw': g.get('game_time_raw'),
                             'tier': g.get('tier')},
            'generated_at': now_iso,
        })

    print(f'  parsed {len(signals)} signals from full-slate RSC payload')
    matched = sum(1 for s in signals if s.get('game_id'))
    print(f'  matched to our game_context: {matched}/{len(signals)}')

    if dry_run:
        for s in signals:
            print(f"    {s['away_team']:<20} @ {s['home_team']:<20} · {s['market']:<5} · "
                  f"sharp={s['sharp_side_norm']:<5} · money% {s['money_side_pct']}/{s['money_other_pct']} · "
                  f"bets% {s['bets_side_pct']}/{s['bets_other_pct']} · gid={s['game_id']}")
        return len(signals)

    written = 0
    for i in range(0, len(signals), 100):
        chunk = signals[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/fadereport_signals?on_conflict=snapshot_date,sport,away_team,home_team,market',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} signals')
    return written


def run(sports: list, dry_run: bool = False):
    total = 0
    for sport in sports:
        print(f'\n=== fadereport_scraper · {sport} ===')
        try:
            n = scrape_sport(sport, dry_run=dry_run)
            total += n
        except Exception as e:
            print(f'  ✗ {sport} failed: {e}')
    print(f'\n✓ done · {total} signals total across {len(sports)} sports')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORT_URL.keys()))
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = [args.sport] if args.sport else list(SPORT_URL.keys())
    run(sports, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
