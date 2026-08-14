"""Fadereport sharp-signal scraper (2026-08-14).

Nightly Playwright scrape of fadereport.com per-sport pages. Fadereport
is a CURATED sharp-signal service — each entry is one game × one market
where their algo detected sharp action (money% vs bets% divergence).

Runs across MLB / NCAAB / NHL / NFL / NCAAF (URL pattern /<sport-slug>).
Writes to fadereport_signals table (sport-universal).

WHY WE STORE THIS
  Second opinion alongside our primary OddsCrowd sharp-signal source.
  When both agree tightly on the same side, high-confidence sharp
  signal. When they disagree (see 2026-08-14 slate audit: 4 games with
  opposite money attribution), individual source is book-mix noise.

  Real sharp arbiter is Pinnacle line movement (planned separate
  capture). Once 4-6 weeks of accumulated OC + FR + Pinnacle data,
  audit which aggregator aligns with actual sharp market movement.

METHODOLOGY
  1. Playwright loads /<sport-slug> page (JS-toggled tabs)
  2. Click through Spread / Total tabs (extracts market-specific views)
  3. Parse visible signal cards: matchup + market + sharp side +
     strength + bets/money split
  4. Match teams to today's *_game_context row by fuzzy name (game_id
     resolution) — NULL game_id acceptable when no match
  5. Upsert to fadereport_signals

CLI
  python fadereport_scraper.py                     # all sports today
  python fadereport_scraper.py --sport MLB         # single sport
  python fadereport_scraper.py --dry-run           # print, don't write
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
}

# Map sport -> game_context table for game_id resolution
SPORT_TABLE = {
    'MLB': 'mlb_game_context',
    'NCAAB': 'ncaab_game_context',
    'NHL': 'nhl_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
}


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _load_todays_games(sport: str, snapshot_date: date) -> dict:
    """Return {(away_last_word, home_last_word): game_id} for fuzzy join.
    Fadereport uses team short names (Cardinals not St. Louis Cardinals)."""
    tbl = SPORT_TABLE.get(sport)
    if not tbl: return {}
    r = requests.get(
        f'{SB}/rest/v1/{tbl}?select=game_id,away_team,home_team'
        f'&game_date=eq.{snapshot_date.isoformat()}',
        headers=H_READ, timeout=15)
    if r.status_code != 200: return {}
    lookup = {}
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        gid = row.get('game_id')
        away = (row.get('away_team') or '').lower()
        home = (row.get('home_team') or '').lower()
        # Fuzzy key = last word of each team name
        away_key = away.split()[-1] if away else ''
        home_key = home.split()[-1] if home else ''
        lookup[(away_key, home_key)] = gid
        # Also store full-string version for exact matches
        lookup[(away, home)] = gid
    return lookup


def _resolve_game_id(away_fr: str, home_fr: str, game_lookup: dict) -> Optional[str]:
    """Match fadereport team names to our game_id via last-word fuzzy join."""
    a = away_fr.lower().split()[-1]
    h = home_fr.lower().split()[-1]
    # Special aliases (fadereport uses these; our DB uses full)
    aliases = {'blue': 'blue jays', 'red': 'red sox', 'white': 'white sox'}
    if a in aliases: a = aliases[a]
    if h in aliases: h = aliases[h]
    # Prefer exact-key match
    if (a, h) in game_lookup: return game_lookup[(a, h)]
    # Try known variants — fadereport often shows "Blue Jays" but DB has "Toronto Blue Jays"
    for (ak, hk), gid in game_lookup.items():
        if a in ak.split() and h in hk.split(): return gid
        if ak.endswith(a) and hk.endswith(h): return gid
    return None


def scrape_sport(sport: str, dry_run: bool = False) -> int:
    """Scrape one sport page, extract signals, upsert."""
    from playwright.sync_api import sync_playwright
    import time

    url = SPORT_URL.get(sport)
    if not url:
        print(f'  ✗ unknown sport {sport}'); return 0

    snapshot = _et_today()
    game_lookup = _load_todays_games(sport, snapshot)

    signals = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(20000)
        try:
            page.goto(url, wait_until='networkidle')
        except Exception as e:
            print(f'  ✗ page load fail: {e}')
            browser.close()
            return 0
        time.sleep(3)

        # The DEFAULT "All Sharp Signals" view shows every game × market
        # with a sharp signal — we don't need to click tabs (each tab
        # filters the SAME data by market; default view has all markets
        # already visible).
        html = page.content()
        browser.close()

    signals = _parse_signals(html, sport, snapshot)
    print(f'  parsed {len(signals)} signals from {sport}')

    # Resolve game_ids
    for sig in signals:
        sig['game_id'] = _resolve_game_id(
            sig['away_team'], sig['home_team'], game_lookup)

    if dry_run:
        print(f'  [DRY] would upsert {len(signals)} rows')
        for s in signals:
            print(f'    {s.get("away_team")} @ {s.get("home_team")} · {s["market"]:6} · {s["sharp_side_raw"]:20} · '
                  f'{s.get("strength_pts",0)}pt · bets/money {s.get("bets_side_pct")}/{s.get("money_side_pct")} · game_id={s["game_id"]}')
        return len(signals)

    # Upsert
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


def _parse_signals(html: str, sport: str, snapshot_date: date) -> list:
    """Parse rendered HTML for signal cards."""
    body = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<(br|/div|/p|/tr|/td|/li)[^>]*>', '\n', body)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n+', '\n', text)

    signals = []

    # Split into signal blocks — each signal starts with "sharp signal +Npt"
    # Pattern: "🔥/⚠️ Strong/Lean sharp signal +Npt ... % of Bets ... % of Money"
    blocks = re.split(r'(?:🔥\s*Strong sharp signal|⚠️\s*Lean sharp)', text)
    for blk in blocks[1:]:  # skip header
        blk = blk.strip()
        # Extract strength
        m = re.match(r'\s*\+?(\d+)pt', blk)
        if not m: continue
        strength = int(m.group(1))
        tier = 'strong' if strength >= 20 else 'lean'

        # Extract market ("mlb · Moneyline" / "mlb · Total" / "mlb · Spread")
        mkt_m = re.search(r'(?:mlb|ncaab|nhl|nfl|ncaaf)\s*[·•]\s*(Moneyline|Total|Spread)', blk, re.I)
        if not mkt_m: continue
        market = {'moneyline':'ml','total':'total','spread':'spread'}.get(mkt_m.group(1).lower())

        # Extract game time  ("8/14, 2:20 PM")
        time_m = re.search(r'(\d{1,2}/\d{1,2}),?\s*(\d{1,2}:\d{2}\s*[AP]M)', blk)
        game_time = time_m.group(2).strip() if time_m else None

        # Extract team names — pattern differs by market
        # For Total: "Team1 vs Team2\nOVER\no<line>\nUNDER\nu<line>"
        # For ML/Spread stacked: "Team1\n<odds> [◀ sharp]\nmlb · Market\nTIME\nTeam2\n[sharp ▶] <odds>"
        pre_bets = blk.split('% of Bets')[0]

        vs_m = re.search(r'([A-Z][\w\s\.]*?)\s+vs\s+([A-Z][\w\s\.]*?)\s+(?:OVER|UNDER)', pre_bets)
        if vs_m:
            away_team = vs_m.group(1).strip()
            home_team = vs_m.group(2).strip()
        else:
            # ML/Spread stacked layout — split into lines, find team names
            # Team lines are ones that look like team names (alpha with possible spaces)
            # NOT: odds (start with +/-/o/u), markers ("mlb · X"), time (contains :), signals (sharp/point)
            lines = [ln.strip() for ln in pre_bets.split('\n') if ln.strip()]
            teams = []
            for ln in lines:
                if any(sk in ln.lower() for sk in [
                    'sharp signal', 'point diff', 'sharp ', ' sharp', 'why the',
                    'mlb', 'nba', 'nhl', 'nfl', 'ncaab', 'ncaaf',
                    'moneyline', 'total', 'spread', 'am ', 'pm', ' pm', ' am',
                    'pt ', 'over', 'under',
                ]):
                    continue
                # Skip pure-numeric lines (odds, spreads)
                if re.match(r'^[+\-]?[\dou\.\s]+$', ln): continue
                if re.match(r'^[ou][\d\.]+', ln): continue  # o7.5, u8
                if re.match(r'^[+\-][\d\.]+', ln): continue  # +1.5, -170
                # Skip lines that are just digits or contain slash (date)
                if re.match(r'^\d', ln): continue
                # Must have at least one letter and be short-ish
                if not re.search(r'[A-Za-z]', ln): continue
                if len(ln) > 30: continue
                teams.append(ln)
            teams = teams[:2]
            if len(teams) >= 2:
                away_team, home_team = teams[0], teams[1]
            elif len(teams) == 1:
                # Sometimes second team on same line as its odds — try alt regex
                alt = re.findall(r'\n\s*([A-Z][A-Za-z\s\.]{2,25})\s*\n', pre_bets)
                alt = [a.strip() for a in alt if not any(sk in a.lower() for sk in ['mlb','sharp','moneyline','total','spread'])]
                if len(alt) >= 2:
                    away_team, home_team = alt[0], alt[1]
                else:
                    continue
            else:
                continue

        # Extract sharp side — indicated by "◀ sharp" or "sharp ▶"
        sharp_side_raw = ''
        # Left-arrow sharp = away side (first team in stacked layout)
        # Right-arrow sharp = home side (second team)
        pre_time_or_bets = pre_bets
        if '◀ sharp' in pre_time_or_bets:
            # sharp is AWAY-side or first-listed
            if vs_m:
                # Total: sharp side is OVER or UNDER
                over_m = re.search(r'(OVER|UNDER)\s+([ou]?[\d\.]+)\s*◀\s*sharp', pre_time_or_bets)
                if over_m:
                    sharp_side_raw = f'{over_m.group(1)} {over_m.group(2)}'
                    sharp_side_norm = over_m.group(1).lower()
                else:
                    sharp_side_raw = 'OVER'; sharp_side_norm = 'over'
            else:
                sharp_side_raw = away_team
                sharp_side_norm = 'away'
        elif 'sharp ▶' in pre_time_or_bets:
            if vs_m:
                over_m = re.search(r'sharp\s*▶\s*(OVER|UNDER)?\s*([ou]?[\d\.]+)', pre_time_or_bets)
                if over_m and over_m.group(1):
                    sharp_side_raw = f'{over_m.group(1)} {over_m.group(2)}'
                    sharp_side_norm = over_m.group(1).lower()
                else:
                    sharp_side_raw = 'UNDER'; sharp_side_norm = 'under'
            else:
                sharp_side_raw = home_team
                sharp_side_norm = 'home'
        else:
            # Fallback: parse from context if arrows missing
            sharp_side_raw = away_team
            sharp_side_norm = 'away'

        # Extract splits: "16% % of Bets 84% ... 62% % of Money 38%"
        # Splits appear duplicated in DOM — take first occurrence
        bets_m = re.search(r'(\d+)%\s*% of Bets\s*(\d+)%', blk)
        money_m = re.search(r'(\d+)%\s*% of Money\s*(\d+)%', blk)
        if not (bets_m and money_m): continue

        left_bets = int(bets_m.group(1)); right_bets = int(bets_m.group(2))
        left_money = int(money_m.group(1)); right_money = int(money_m.group(2))

        # "left" is the first-listed side (usually AWAY for ML/spread, OVER for total)
        # Determine which is the SHARP side
        if sharp_side_norm in ('away', 'over'):
            bets_side, money_side = left_bets, left_money
            bets_other, money_other = right_bets, right_money
        else:
            bets_side, money_side = right_bets, right_money
            bets_other, money_other = left_bets, left_money

        # Extract reasoning
        reason_m = re.search(r'Why the sharps like it:\s*(.{0,500})', blk)
        reasoning = reason_m.group(1).strip() if reason_m else None
        # Truncate at next signal marker
        if reasoning:
            reasoning = reasoning.split('\n')[0][:500]

        signals.append({
            'snapshot_date': snapshot_date.isoformat(),
            'sport': sport,
            'away_team': away_team[:100],
            'home_team': home_team[:100],
            'game_time_et': game_time,
            'market': market,
            'sharp_side_raw': sharp_side_raw[:100],
            'sharp_side_norm': sharp_side_norm,
            'strength_pts': strength,
            'strength_tier': tier,
            'bets_side_pct': bets_side,
            'money_side_pct': money_side,
            'bets_other_pct': bets_other,
            'money_other_pct': money_other,
            'reasoning': reasoning,
            'raw_snapshot': {'block_sample': blk[:800]},
            'generated_at': datetime.now(timezone.utc).isoformat(),
        })

    return signals


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
    p.add_argument('--sport', choices=list(SPORT_URL.keys()),
                   help='Single sport; default all')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = [args.sport] if args.sport else list(SPORT_URL.keys())
    run(sports, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
