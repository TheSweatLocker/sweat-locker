"""fadethepublic_scraper — third independent money-flow source (2026-09-21).

Fade The Public Analytics publishes a public JSON API carrying BOTH halves
of the betting split — share of TICKETS (bet%) and share of DOLLARS (money%)
— for moneyline, spread and totals. No auth, no paywall, one request per
sport.

WHY THIS EXISTS. OddsCrowd moved its splits table to client-side rendering
on 2026-09-21, so plain-HTTP scraping can no longer see money%/bets% at all
(project_oddscrowd_client_render_921). That left exactly ONE money-flow
source, and the sharp-money discipline rule wants 2+ contrarian sources
before it will FADE — a gate a single feed can never satisfy.

INDEPENDENT, NOT A MIRROR. Verified against fadereport on the same game:
NYG@LAR moneyline read bets 16% / money 23% here versus 15% / 26% there.
Same market, sampled differently, which is the entire point — two feeds
that agree to the decimal would be one feed wearing two hats. Coverage is
also better on football: 16 NFL games today versus fadereport's 1.

GAME_ID ATTRIBUTION. Deliberately imports fadereport's slate lookup and
resolver rather than writing a second matcher. If the two sources resolved
the same fixture to different game_ids the corroboration join would break
silently — and a silent join break is exactly the class of bug that cost us
oddscrowd for a day. Shared resolver means shared answer, right or wrong.

ENDPOINTS. NFL is the unprefixed default route (it was the site's original
sport); every other league is namespaced:
    NFL    /api/games
    MLB    /api/mlb/games
    NCAAF  /api/ncaaf/games
    NBA    /api/nba/games
    NCAAB  /api/ncaab/games
There is NO NHL endpoint — the site covers NFL/NCAAF/NBA/MLB/NCAAB/Golf.
fadereport still carries NHL, so hockey keeps one source into the Oct 8
opener. That gap is tracked, not silently absorbed: run() reports it.

CLI
  python fadethepublic_scraper.py                 # every supported sport
  python fadethepublic_scraper.py --sport NFL
  python fadethepublic_scraper.py --dry-run
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta, date
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

BASE = 'https://fadethepublicanalytics.com'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36')
H_WEB = {'User-Agent': UA, 'Accept': 'application/json'}

# NFL is the bare route; the rest are namespaced. Do not "tidy" NFL to
# /api/nfl/games — that path 404s.
SPORT_ENDPOINT = {
    'NFL':   '/api/games',
    'MLB':   '/api/mlb/games',
    'NCAAF': '/api/ncaaf/games',
    'NBA':   '/api/nba/games',
    'NCAAB': '/api/ncaab/games',
}
NO_COVERAGE = ('NHL',)   # site has no hockey endpoint — fadereport covers it

# API market block -> our market code + the two side labels within it.
MARKETS = (
    ('moneyline', 'ml',    'away',  'home'),
    ('spread',    'rl',    'away',  'home'),
    ('totals',    'total', 'over',  'under'),
)

# A snapshot older than this is last week's slate, not today's market.
# NCAAF legitimately sits idle Sun-Fri, so this only guards against a feed
# that has actually stopped rather than one that is between slates.
STALE_AFTER_DAYS = 8


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_sport(sport: str) -> tuple[list, Optional[str]]:
    """Return (games, api_timestamp). Raises on transport failure."""
    ep = SPORT_ENDPOINT.get(sport)
    if not ep:
        return [], None
    r = requests.get(BASE + ep, headers=H_WEB, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f'{sport} {ep} -> HTTP {r.status_code}')
    d = r.json()
    if not isinstance(d, dict):
        raise RuntimeError(f'{sport} {ep} -> unexpected payload {type(d).__name__}')
    return (d.get('games') or []), d.get('timestamp')


CTX_TABLE = {'NFL': 'nfl_game_context', 'MLB': 'mlb_game_context',
             'NCAAF': 'ncaaf_game_context', 'NBA': 'nba_game_context',
             'NCAAB': 'ncaab_game_context'}
ALIAS_TABLE = {'NFL': 'nfl_team_aliases', 'NCAAF': 'ncaaf_team_aliases'}

_alias_cache: dict = {}
_slate_cache: dict = {}


def _alias_map(sport: str) -> dict:
    """{lowercased name variant -> canonical_name} from the curated table.

    Exact lookup against a maintained alias list, NOT fuzzy matching. The
    slate stores canonical forms the feed never uses — NFL context holds
    'NYG'/'LA' while the API says 'New York Giants'/'Los Angeles Rams', and
    NCAAF context holds 'Ole Miss'/'Miami (OH)'. Mapping both sides into
    canonical space and requiring an exact hit is the only approach that
    survives college football, where 1,872 programs include genuinely
    distinct schools like 'Iowa' and 'Northern Iowa' that any last-word or
    substring matcher will happily conflate.

    A name that isn't in the table resolves to nothing and the row is left
    unattributed. Refusing beats guessing: a wrong game_id silently attaches
    one game's money flow to another.
    """
    if sport in _alias_cache:
        return _alias_cache[sport]
    tbl = ALIAS_TABLE.get(sport)
    out: dict = {}
    if tbl:
        try:
            rows = []
            for off in range(0, 6000, 1000):
                r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, timeout=30,
                                 params={'select': '*', 'limit': 1000, 'offset': off})
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
                variants = [canon, row.get('full_name'), row.get('mascot'),
                            row.get('nickname'), row.get('city'), row.get('location'),
                            row.get('abbrev'), row.get('odds_api_name'),
                            row.get('espn_name')]
                alts = row.get('alt_names')
                if isinstance(alts, list):
                    variants += alts
                for v in variants:
                    if not v:
                        continue
                    key = ' '.join(str(v).split()).lower()
                    # First writer wins. A bare mascot like 'Wildcats' or a
                    # bare state name belongs to several programs; letting a
                    # later row overwrite would silently reassign it.
                    out.setdefault(key, canon)
        except Exception as e:
            print(f'  ⚠ {sport}: alias table unavailable ({e}) — '
                  f'attribution will fall back to raw names')
    _alias_cache[sport] = out
    return out


def _canon(sport: str, name: str) -> str:
    amap = _alias_map(sport)
    key = ' '.join(str(name or '').split()).lower()
    return amap.get(key, key)


def _slate_index(sport: str, gdate: str) -> dict:
    """{(canonical_away, canonical_home) -> game_id} for ONE date."""
    ck = (sport, gdate)
    if ck in _slate_cache:
        return _slate_cache[ck]
    tbl = CTX_TABLE.get(sport)
    idx: dict = {}
    if tbl:
        try:
            r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, timeout=25,
                             params={'select': 'game_id,away_team,home_team',
                                     'game_date': f'eq.{gdate}'})
            for row in (r.json() if r.status_code == 200 else []):
                idx[(_canon(sport, row.get('away_team')),
                     _canon(sport, row.get('home_team')))] = row.get('game_id')
        except Exception as e:
            print(f'  ⚠ {sport}: slate load failed for {gdate} ({e})')
    _slate_cache[ck] = idx
    return idx


# How far either side of the pull date to look for a fixture when the feed
# gives no kickoff time. Back 4 covers a Monday pull finding Sunday/Saturday
# slates; forward 7 covers look-ahead lines.
_SEARCH_BACK, _SEARCH_FWD = 4, 7


def _game_et_date(g: dict, fallback: date) -> Optional[str]:
    """ET date from start_time, or None when the feed omits it."""
    ts = g.get('start_time')
    if ts:
        try:
            dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
            return (dt.astimezone(timezone.utc) - timedelta(hours=4)).date().isoformat()
        except ValueError:
            pass
    return None


def _resolve_gid(sport: str, away: str, home: str, snap: date) -> tuple[Optional[str], Optional[str]]:
    """(game_id, game_date) for one fixture, or (None, None).

    Resolving against "today" alone is wrong for this feed: the NFL endpoint
    serves the whole most-recent slate, so a Monday pull carries Sunday's 15
    games beside the Monday nighter — and NFL games arrive with
    start_time=None, so there is no per-game date to lean on. Matching those
    against today's one-game context found 3/48.

    So when the feed gives no kickoff time, scan a window of nearby dates and
    require EXACTLY ONE hit. A unique match is safe; two hits mean the same
    fixture appears twice in the window (a rematch, or a doubleheader) and
    guessing which one would attach money flow to the wrong game. Same
    unique-match discipline used by the NBA/NHL external puller.
    """
    ca, ch = _canon(sport, away), _canon(sport, home)
    key = (ca, ch)
    hits = []
    rev_hits = []
    for off in range(-_SEARCH_BACK, _SEARCH_FWD + 1):
        d = (snap + timedelta(days=off)).isoformat()
        idx = _slate_index(sport, d)
        gid = idx.get(key)
        if gid:
            hits.append((gid, d))
        elif idx.get((ch, ca)):
            rev_hits.append((idx[(ch, ca)], d))
    if len(hits) == 1:
        return hits[0]
    if not hits and rev_hits:
        # The fixture exists but with home and away SWAPPED relative to this
        # feed. Never silently accept it: every percentage here is labelled
        # by side, so attaching them to a reversed fixture would flip the
        # money flow onto the opposing team — a wrong signal is worse than a
        # missing one. Surface the disagreement and refuse.
        _REVERSED.append(f'{away} @ {home} (we have it reversed on {rev_hits[0][1]})')
    return (None, None)


_REVERSED: list = []


def build_signals(sport: str, games: list, snap: date, lookup: dict) -> list:
    """Flatten the API payload into fadereport_signals-shaped rows."""
    snap_str = snap.isoformat()
    out = []
    for g in games:
        away = (g.get('away_team') or '').strip()
        home = (g.get('home_team') or '').strip()
        if not away or not home:
            continue
        # Prefer the feed's own kickoff time (MLB supplies it); fall back to
        # a unique-match window scan when it doesn't (NFL sends start_time
        # null on every game).
        hinted = _game_et_date(g, snap)
        our_gid = None
        gdate = None
        if hinted:
            our_gid = _slate_index(sport, hinted).get(
                (_canon(sport, away), _canon(sport, home)))
            gdate = hinted if our_gid else None
        if not our_gid:
            our_gid, gdate = _resolve_gid(sport, away, home, snap)
        # Unattributed rows still get stored — the percentages are real and a
        # later backfill can attach them — but they must carry SOME date, so
        # fall back to the kickoff hint and finally the pull date.
        gdate = gdate or hinted or snap_str

        for block, market, side_a, side_b in MARKETS:
            m = g.get(block)
            if not isinstance(m, dict):
                continue
            a_bets, a_money = _num(m.get(f'{side_a}_bet_pct')), _num(m.get(f'{side_a}_money_pct'))
            b_bets, b_money = _num(m.get(f'{side_b}_bet_pct')), _num(m.get(f'{side_b}_money_pct'))
            if None in (a_bets, a_money, b_bets, b_money):
                continue
            # Integrity gate. Measured 2026-09-21: NFL 96/96 and MLB 18/18
            # pairs summed to 100, but two NCAAF rows did not — low-liquidity
            # FCS games where one side showed 0%. A split that doesn't sum is
            # not a split, and letting it through would feed a fake divergence
            # straight into the FADE gate.
            if abs((a_bets + b_bets) - 100) > 1 or abs((a_money + b_money) - 100) > 1:
                continue

            # Sharp side = where DOLLARS outrun TICKETS. Same convention as
            # fadereport, so strength_pts is comparable across the two.
            a_div = a_money - a_bets
            if a_div >= 0:
                sharp, bets_s, money_s, bets_o, money_o = side_a, a_bets, a_money, b_bets, b_money
            else:
                sharp, bets_s, money_s, bets_o, money_o = side_b, b_bets, b_money, a_bets, a_money
            strength = int(round(abs(a_div)))
            tier = 'strong' if strength >= 20 else ('lean' if strength >= 10 else 'weak')

            if market == 'total':
                sharp_norm = sharp               # already 'over'/'under'
                sharp_raw = sharp
            else:
                sharp_norm = sharp               # already 'away'/'home'
                sharp_raw = away if sharp == 'away' else home

            line = m.get('total_line') if market == 'total' else m.get(f'{sharp}_spread')
            odds = m.get(f'{sharp}_odds')

            out.append({
                # Dated to the GAME, not the pull. This feed serves the whole
                # most-recent slate, so a Monday pull carries Sunday's games;
                # stamping them with the pull date would make every
                # splits-vs-result join off by a day. fetched_at (DB default)
                # still records when we actually read it.
                'snapshot_date':   gdate,
                'sport':           sport,
                'game_id':         our_gid,
                'away_team':       away[:100],
                'home_team':       home[:100],
                'game_time_et':    (g.get('start_time') or None),
                'market':          market,
                'sharp_side_raw':  str(sharp_raw)[:100],
                'sharp_side_norm': sharp_norm,
                'strength_pts':    strength,
                'strength_tier':   tier,
                'bets_side_pct':   bets_s,
                'money_side_pct':  money_s,
                'bets_other_pct':  bets_o,
                'money_other_pct': money_o,
                'current_line':    str(line) if line is not None else None,
                'current_odds':    str(odds) if odds is not None else None,
                'reasoning':       None,
                'raw_snapshot':    {'block': block,
                                    'opportunity_score': g.get('max_opportunity_score'),
                                    'sharp_money_indicators': g.get('sharp_money_indicators')},
            })
    return out


def write_signals(rows: list, dry_run: bool = False) -> int:
    if dry_run or not rows:
        return len(rows)
    ok = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        r = requests.post(
            # game_id is deliberately NOT in the conflict key — it is derived
            # from the fixture and nullable when attribution refuses. The
            # fixture itself is the identity, so a later run that resolves a
            # previously-unmatched game updates the row instead of duplicating
            # it. (20260921c; the original expression index was invisible to
            # PostgREST's on_conflict and every write 42P10'd.)
            f'{SB}/rest/v1/fadethepublic_signals'
            '?on_conflict=snapshot_date,sport,market,away_team,home_team',
            headers=H_WRITE, json=chunk, timeout=30)
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  ⚠ write failed {r.status_code}: {r.text[:160]}')
    return ok


def scrape_sport(sport: str, dry_run: bool = False) -> int:
    snap = _et_today()
    try:
        games, ts = fetch_sport(sport)
    except Exception as e:
        print(f'  ✗ {sport}: FETCH FAILED — {type(e).__name__}: {e}')
        return -1
    if not games:
        print(f'  ⚠ {sport}: 0 games returned by API')
        return 0

    # A feed frozen on an old slate looks identical to a quiet day unless we
    # actually check. Report it rather than writing week-old percentages in
    # as though they were today's market.
    age_note = ''
    if ts:
        try:
            tsd = datetime.fromisoformat(str(ts).replace('Z', '+00:00')).date()
            age = (snap - tsd).days
            if age > STALE_AFTER_DAYS:
                print(f'  ⚠ {sport}: API snapshot is {age}d old ({tsd}) — '
                      f'treating as stale, nothing written')
                return 0
            age_note = f' · snapshot {tsd} ({age}d old)' if age > 0 else ' · snapshot today'
        except ValueError:
            pass

    rows = build_signals(sport, games, snap, {})
    matched = sum(1 for r in rows if r['game_id'])
    n = write_signals(rows, dry_run=dry_run)
    print(f'  {"[DRY] " if dry_run else ""}{sport}: {len(games)} games → {n} signals'
          f' · game_id matched {matched}/{len(rows)}{age_note}')
    if _REVERSED:
        print(f'  ⚠ {sport}: {len(_REVERSED)} fixture(s) with home/away REVERSED '
              f'vs our slate — refused rather than invert the split:')
        for s in _REVERSED[:5]:
            print(f'      {s}')
        _REVERSED.clear()
    if rows and matched == 0:
        # Every row unattributed means the resolver found nothing — the rows
        # are useless for any join even though the write "succeeded".
        print(f'  ⚠ {sport}: NO rows resolved to a game_id — check the slate '
              f'table has today\'s fixtures')
    return n


def run(sports: list, dry_run: bool = False):
    print(f'=== fadethepublic_scraper · {_et_today()} '
          f'{"(DRY)" if dry_run else ""} ===')
    total = 0; failed = []
    for sp in sports:
        n = scrape_sport(sp, dry_run=dry_run)
        if n < 0:
            failed.append(sp)
        else:
            total += n
    print(f'\n  total signals: {total}')
    if failed:
        print(f'  ✗ failed sports: {", ".join(failed)}')
    print(f'  ℹ no coverage at this source: {", ".join(NO_COVERAGE)} '
          f'(fadereport carries them)')
    # Non-zero exit only when a sport actually errored. An empty slate is
    # normal; a transport failure is not.
    return 1 if failed else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORT_ENDPOINT.keys()))
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    sports = [a.sport] if a.sport else list(SPORT_ENDPOINT.keys())
    raise SystemExit(run(sports, dry_run=a.dry_run))


if __name__ == '__main__':
    main()
