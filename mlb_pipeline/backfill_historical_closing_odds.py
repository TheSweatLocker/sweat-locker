"""backfill_historical_closing_odds — recover closing lines from The Odds API.

THE PROBLEM. Two sports have never stored a closing line, so nothing in
them has ever been graded, so not one of their signals has ever been
validated:

                      close line      graded      signals validated
    NCAAB   0 / 5,911 rows        0 / 5,911        0 / 65
    NHL     contaminated*         0 / 1,382        0 / 62

    * nhl_game_results.close_puckline holds MONEYLINE prices on every
      historical row (range -290..+240) — an NHL puck line is always
      +/-1.5. See backfill_nhl_grades, which refuses to grade off it.

Compare MLB 512/578 and NFL 171/298 signals validated. On Nov 3 (NCAAB)
and Oct 8 (NHL) every one of those 127 signals would publish at the
unvalidated weight floor, and we would spend the season discovering which
of them work on live picks.

WHY THIS IS NOW WORTH DOING. A source hunt on 2026-08-20 concluded The
Odds API historical archive is the clean answer (floor 2020-11-16) but
worried it would consume ~75% of a 60,589-credit quota. Quota today is
4,885,766. Re-probed live: a /odds snapshot costs 30 credits and returned
101 of 101 NCAAB games with spreads on 2025-01-15. The cost objection is
gone — a full NCAAB season is roughly 27k credits, 0.55% of quota.

CORRECTNESS — WHY NOT ONE SNAPSHOT PER DAY. The obvious approach, one
/odds call late in the day, returns IN-GAME prices for anything that has
already tipped. Writing those as "closing" would corrupt the exact column
grading compares against — the same hazard that made the live poller need
an is_pregame guard, and the same class of error as the moneyline values
already sitting in close_puckline. So: one /events call per date (1
credit) to learn commence times, then one /odds snapshot 10 minutes before
each distinct commence hour, and each game is taken only from the snapshot
immediately preceding its own tip-off. Every line written is genuinely
pre-game.

SIGN CONVENTION. Imports the live pullers' own consensus functions rather
than reimplementing them (project_close_spread_sign_bug_914: the sign
differs per sport and getting it backwards silently inverts every spread
grade). Historical and forward rows therefore cannot disagree.

TEAM MATCHING. Exact, through curated maps: ncaab_team_aliases has an
`odds_api_name` column built for precisely this, and NHL goes through
NHL_TEAMS. No fuzzy matching — an unmatched game is skipped and counted.

IDEMPOTENT: only fills columns that are NULL (or implausible, for the
contaminated NHL puckline) unless --force.

CLI
  python backfill_historical_closing_odds.py --sport NCAAB --start 2024-11-01 --end 2025-04-10 --dry-run
  python backfill_historical_closing_odds.py --sport NHL --start 2024-10-01 --end 2025-06-30
"""
from __future__ import annotations
import argparse, os, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    Retry = None

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
HIST = 'https://api.the-odds-api.com/v4/historical/sports'

_S = requests.Session()
if Retry is not None:
    _S.mount('https://', HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=0.6,
                          status_forcelist=(500, 502, 503, 504, 429),
                          allowed_methods=frozenset(['GET', 'PATCH'])),
        pool_connections=8, pool_maxsize=8))

SPORTS = {
    'NCAAB': {
        'odds_sport': 'basketball_ncaab',
        'results': 'ncaab_game_results',
        'spread_col': 'close_spread',
        'total_col': 'close_total',
        'home_ml_col': 'home_ml_close',
        'away_ml_col': 'away_ml_close',
        # A basketball spread lives well inside +/-60; anything outside is
        # not a spread. Guards against writing a stray moneyline.
        'spread_sane': 60.0,
        'total_range': (100.0, 200.0),
    },
    'NHL': {
        'odds_sport': 'icehockey_nhl',
        'results': 'nhl_game_results',
        'spread_col': 'close_puckline',
        'total_col': 'close_total',
        'home_ml_col': 'close_home_ml',
        'away_ml_col': 'close_away_ml',
        # NHL puck line is always +/-1.5. This is the check that would have
        # caught the existing contamination.
        'spread_sane': 3.0,
        'total_range': (3.0, 10.0),
    },
}


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── team matching ────────────────────────────────────────────────────────
def _norm_team(s) -> str:
    """Normalise a team string so both naming worlds meet in the middle.

    The Odds API writes "Cleveland St Vikings" where our canonical form is
    "Cleveland St.", and "Boston Univ. Terriers" against "Boston
    University". Dropping punctuation and folding Univ/University to a
    common token resolved 9 of 10 sampled misses.
    """
    s = str(s or '').lower().replace('.', '').replace('&', ' and ')
    s = re.sub(r'\b(univ|university)\b', 'u', s)
    return ' '.join(s.split())


def build_name_map(sport: str) -> tuple[dict, set]:
    """({normalised name -> our team name}, {ambiguous keys to refuse}).

    Exact curated lookups first — ncaab_team_aliases carries an
    `odds_api_name` column built for precisely this join. A mascot-stripped
    fallback is layered on top, because the feed appends nicknames the
    alias rows do not always carry.

    COLLISIONS ARE TRACKED AND REFUSED. Stripping the mascot off "Miami
    Hurricanes" gives "Miami", which is also "Miami OH" — the same trap as
    Iowa/Northern Iowa. Any normalised key that more than one canonical
    team claims is dropped rather than resolved to whichever row was read
    first.
    """
    out: dict = {}
    claims: dict = defaultdict(set)

    def _claim(key: str, canon: str):
        if key:
            claims[key].add(canon)

    if sport == 'NCAAB':
        rows = []
        for off in range(0, 4000, 1000):
            r = _S.get(f'{SB}/rest/v1/ncaab_team_aliases', headers=H_READ, timeout=30,
                       params={'select': 'canonical_name,odds_api_name,alt_names',
                               'limit': 1000, 'offset': off})
            if r.status_code != 200:
                break
            chunk = r.json()
            if not isinstance(chunk, list):
                break
            rows += chunk
            if len(chunk) < 1000:
                break
        for row in rows:
            canon = row.get('canonical_name')
            if not canon:
                continue
            for v in [row.get('odds_api_name'), canon] + list(row.get('alt_names') or []):
                if not v:
                    continue
                _claim(_norm_team(v), canon)
            # Mascot-stripped forms of the feed's own name, e.g.
            # "Lafayette Leopards" -> "lafayette".
            oan = _norm_team(row.get('odds_api_name'))
            words = oan.split()
            for cut in range(len(words) - 1, 0, -1):
                _claim(' '.join(words[:cut]), canon)
    elif sport == 'NHL':
        from odds_pull_core import NHL_TEAMS
        for full, (_ab, _place) in NHL_TEAMS.items():
            _claim(_norm_team(full), full)

    ambiguous = {k for k, v in claims.items() if len(v) > 1}
    for k, v in claims.items():
        if k not in ambiguous:
            out[k] = next(iter(v))
    return out, ambiguous


def resolve_team(name, names: dict, ambiguous: set) -> Optional[str]:
    """Exact normalised match, then mascot-strip. Refuses ambiguity."""
    n = _norm_team(name)
    if n in names:
        return names[n]
    if n in ambiguous:
        return None
    words = n.split()
    for cut in range(len(words) - 1, 0, -1):
        cand = ' '.join(words[:cut])
        if cand in ambiguous:
            return None
        if cand in names:
            return names[cand]
    return None


# ── odds fetching ────────────────────────────────────────────────────────
def hist_events(odds_sport: str, iso_ts: str) -> tuple[list, int]:
    r = _S.get(f'{HIST}/{odds_sport}/events',
               params={'apiKey': ODDS_KEY, 'date': iso_ts}, timeout=40)
    if r.status_code != 200:
        return [], 0
    return (r.json().get('data') or []), int(r.headers.get('x-requests-last') or 0)


def hist_odds(odds_sport: str, iso_ts: str) -> tuple[list, int]:
    r = _S.get(f'{HIST}/{odds_sport}/odds',
               params={'apiKey': ODDS_KEY, 'regions': 'us',
                       'markets': 'spreads,totals,h2h',
                       'oddsFormat': 'american', 'date': iso_ts}, timeout=60)
    if r.status_code != 200:
        return [], 0
    return (r.json().get('data') or []), int(r.headers.get('x-requests-last') or 0)


def consensus_for(sport: str, event: dict) -> dict:
    """Delegate to the live puller's own maths so signs cannot diverge."""
    home_raw = event.get('home_team'); away_raw = event.get('away_team')
    if sport == 'NCAAB':
        from ncaab_odds_pull import _consensus_spread, _consensus_total, _consensus_ml
        return {'spread': _consensus_spread(event, home_raw),
                'total': _consensus_total(event),
                'home_ml': _consensus_ml(event, home_raw),
                'away_ml': _consensus_ml(event, away_raw)}
    from odds_pull_core import _consensus
    c = _consensus(event, home_raw, away_raw)
    return {'spread': c.get('spread'), 'total': c.get('total'),
            'home_ml': c.get('home_ml'), 'away_ml': c.get('away_ml')}


def run(sport: str, start: str, end: str, dry_run: bool, force: bool) -> int:
    cfg = SPORTS[sport]
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY not set'); return 0
    names, ambiguous = build_name_map(sport)
    print(f'=== backfill_historical_closing_odds · {sport} · {start}..{end} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  name map: {len(names)} variants · {len(ambiguous)} ambiguous key(s) refused')

    # Which result rows still need a line?
    need: dict = {}
    for off in range(0, 40000, 1000):
        r = _S.get(f'{SB}/rest/v1/{cfg["results"]}', headers=H_READ, timeout=40,
                   params={'select': f'game_id,game_date,home_team,away_team,'
                                     f'{cfg["spread_col"]},{cfg["total_col"]}',
                           'game_date': f'gte.{start}',
                           'order': 'game_date.asc', 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            print(f'  ⚠ results read {r.status_code}: {r.text[:140]}'); break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        for row in chunk:
            if row['game_date'] > end:
                continue
            sp = _f(row.get(cfg['spread_col']))
            # Treat an implausible existing value as missing — that is the
            # NHL moneyline-in-the-puckline column.
            has_sane = sp is not None and abs(sp) <= cfg['spread_sane']
            if force or not has_sane:
                # Keyed by TEAMS ONLY, with the date resolved by proximity
                # below. ncaab_game_results.game_date is UTC-derived (a 7pm
                # ET tip at 00:00Z lands on the NEXT calendar day) while
                # other tables use ET. Keying on an exact date therefore
                # missed 25 of 47 games on the probe date. Matching on the
                # fixture and then picking the nearest date avoids having to
                # know each table's convention — and requiring a unique hit
                # keeps it safe.
                need.setdefault((str(row['home_team']).lower(),
                                 str(row['away_team']).lower()), []).append(row)
        if len(chunk) < 1000:
            break
    n_rows = sum(len(v) for v in need.values())
    print(f'  result rows needing a line: {n_rows} across {len(need)} fixtures')
    if not need:
        return 0

    dates = sorted({r['game_date'] for rows in need.values() for r in rows})
    print(f'  dates to walk: {len(dates)} ({dates[0]} .. {dates[-1]})')

    credits = 0
    patches: dict = {}
    stats = Counter()
    for di, ds in enumerate(dates, 1):
        # Learn commence times for the day (1 credit).
        evs, c = hist_events(cfg['odds_sport'], f'{ds}T12:00:00Z')
        credits += c
        if not evs:
            stats['no_events'] += 1
            continue
        slots = sorted({str(e.get('commence_time'))[:13] for e in evs
                        if e.get('commence_time')})
        # One snapshot 10 min before each distinct commence hour, so every
        # line is pre-game for the games in that slot.
        for slot in slots:
            try:
                slot_dt = datetime.strptime(slot, '%Y-%m-%dT%H').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            snap = (slot_dt - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
            games, c = hist_odds(cfg['odds_sport'], snap)
            credits += c
            for g in games:
                ct = str(g.get('commence_time') or '')
                if ct[:13] != slot:
                    continue        # only games tipping in THIS slot
                h = resolve_team(g.get('home_team'), names, ambiguous)
                a = resolve_team(g.get('away_team'), names, ambiguous)
                if not h or not a:
                    stats['unmatched_team'] += 1
                    continue
                cands = need.get((h.lower(), a.lower())) or []
                if not cands:
                    stats['no_result_row'] += 1
                    continue
                # Pick the result row whose date is nearest this tip-off,
                # within a day either side. Two candidates inside that
                # window means the same fixture twice in 48h — refuse
                # rather than guess which leg we are pricing.
                try:
                    ct_date = datetime.fromisoformat(ct.replace('Z', '+00:00')).date()
                except ValueError:
                    stats['bad_commence'] += 1
                    continue
                near = [rw for rw in cands
                        if abs((datetime.fromisoformat(rw['game_date']).date()
                                - ct_date).days) <= 1]
                if len(near) != 1:
                    stats['ambiguous_date' if near else 'no_result_row'] += 1
                    continue
                row = near[0]
                con = consensus_for(sport, g)
                sp, tot = _f(con.get('spread')), _f(con.get('total'))
                if sp is None or abs(sp) > cfg['spread_sane']:
                    stats['spread_missing_or_insane'] += 1
                    sp = None
                lo, hi = cfg['total_range']
                if tot is None or not (lo <= tot <= hi):
                    stats['total_missing_or_insane'] += 1
                    tot = None
                if sp is None and tot is None:
                    continue
                upd = {}
                if sp is not None: upd[cfg['spread_col']] = sp
                if tot is not None: upd[cfg['total_col']] = tot
                if con.get('home_ml') is not None: upd[cfg['home_ml_col']] = con['home_ml']
                if con.get('away_ml') is not None: upd[cfg['away_ml_col']] = con['away_ml']
                patches[row['game_id']] = upd
                stats['matched'] += 1
        if di % 20 == 0:
            print(f'    …{di}/{len(dates)} dates · {len(patches)} lines · '
                  f'{credits} credits')

    print(f'\n  lines recovered: {len(patches)}  credits used: {credits}')
    if stats:
        print(f'  detail: {dict(stats)}')
    if dry_run:
        for gid, upd in list(patches.items())[:6]:
            print(f'    [DRY] {gid} -> {upd}')
        print(f'  [DRY] would patch {len(patches)}')
        return len(patches)

    ok = fail = 0
    for gid, upd in patches.items():
        r = _S.patch(f'{SB}/rest/v1/{cfg["results"]}',
                     params={'game_id': f'eq.{gid}'}, headers=H_WRITE,
                     json=upd, timeout=30)
        if r.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  ⚠ patch {gid}: {r.status_code} {r.text[:120]}')
    print(f'  patched {ok}, failed {fail}')
    if ok < len(patches):
        print(f'  ⚠ INCOMPLETE — {len(patches) - ok} not written. Re-run to finish.')
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORTS.keys()), required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--force', action='store_true',
                   help='overwrite existing plausible lines too')
    a = p.parse_args()
    run(a.sport, a.start, a.end, a.dry_run, a.force)


if __name__ == '__main__':
    main()
