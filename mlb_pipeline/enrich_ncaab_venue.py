"""NCAAB venue + neutral-site flag -> ncaab_game_context, from ESPN.

== 2026-11-12 . EVERY FLIPPED DUPLICATE RESULT ROW WAS A NEUTRAL-SITE GAME =
ANDY: "ncaab game details, name matching, and ncaab engine and models need
more work alot more work."

`ncaab_game_context.is_neutral_site` was False on 540 of 540 rows and
`venue` was NULL on 540 of 540, while nba_game_context.venue is 264/264
and nhl_game_context.venue is 397/397. One sport dark on a column two
others fill is a wiring break, not an absence
(feedback_any_null_finding_must_be_investigated).

THE FLAG IS LOAD-BEARING IN THE MODEL. ncaab_mc_simulator:131 is

    home_expected = home_eff * pace / 100.0 + (0.0 if neutral else HFA_POINTS)

with HFA_POINTS = 3.5, and ncaab_panel_predictor:361 reads the same
column. With the flag stuck False, every neutral-site game was handed
3.5 points of home court that does not exist -- 38 of 539 games
(7.1%) over 11-01..11-11 by ESPN's own count.

AND IT EXPLAINS THE DUPLICATE RESULT ROWS. ncaab_game_results held 13
matchups twice over 11-01..11-11, 12 of the 13 stored with home and
away SWAPPED and one row of each pair scoreless -- which made graders
report "no result" and left 6 ledger tickets ungraded. Checked against
ESPN:

    duplicate flipped pairs that are neutral-site:  14 of 14
    base rate, all games on the same dates:         38 of 539  (7.1%)

At a neutral site there IS no home team, so the designation is
arbitrary and two feeds pick differently -- the Odds API and ESPN
disagree on Bradley/Coppin St. tonight. That is the whole mechanism:
not a corrupt writer, a fact that genuinely has no answer.

    2026-11-01  Villanova / Notre Dame    Palazzo dello Sport (Rome)
    2026-11-10  Kentucky / Kansas         United Center (Chicago)
    2026-11-11  Saint Joseph's / S. Fla.  Alumni Hall (Navy) (Annapolis)

MATCHING IS ON THE UNORDERED PAIR. Keying on (game_date, home, away)
is exactly the assumption that breaks here, so this matches
{game_date, frozenset(home, away)} and then resolves both names
through ncaab_team_resolver so 'Bethune-Cookman Wildcats' and
'Bethune Cookman' are one team. A pair that matches more than one row
on a date is SKIPPED rather than guessed at.

WHAT IT DOES NOT DO. It does not rewrite home_team/away_team. Our
orientation is what every published pick, receipt and line is already
keyed on, and flipping it would re-point graded history at the other
side (feedback_grade_the_side_we_published). Neutral-site games simply
stop claiming a home edge. It also never touches scores or lines.

USAGE
    python enrich_ncaab_venue.py                      # today ET
    python enrich_ncaab_venue.py --date 2026-11-12
    python enrich_ncaab_venue.py --days 3             # today + 2 ahead
    python enrich_ncaab_venue.py --days-back 12       # backfill history
    python enrich_ncaab_venue.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

import ncaab_team_resolver as _resolver   # noqa: E402  the ONE name resolver

ESPN = ('https://site.api.espn.com/apis/site/v2/sports/basketball/'
        'mens-college-basketball/scoreboard')

#: A generic client name. Never send the user's details to an outside service.
UA = 'sweat-locker-pipeline'


def _et_today() -> str:
    return datetime.now(ZoneInfo('America/New_York')).date().isoformat()


def fetch_espn(day: str) -> list:
    """ESPN scoreboard events for one YYYY-MM-DD. groups=50 is all of D-I."""
    url = f"{ESPN}?dates={day.replace('-', '')}&groups=50&limit=400"
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read()).get('events') or []


def espn_index(day: str) -> dict:
    """{frozenset(canonical home, canonical away): {...}} for one date.

    A pair that appears twice on ESPN is dropped -- two rows for one key
    would make the match a coin flip, and a wrong venue is a wrong game.
    """
    seen = defaultdict(list)
    for e in fetch_espn(day):
        comp = (e.get('competitions') or [{}])[0]
        cs = comp.get('competitors') or []
        names = []
        for c in cs:
            t = c.get('team') or {}
            raw = (t.get('displayName') or t.get('location')
                   or t.get('shortDisplayName'))
            canon = _resolver.resolve(raw)
            names.append(canon or raw)
        if len(names) != 2 or not all(names):
            continue
        venue = comp.get('venue') or {}
        seen[frozenset(names)].append({
            'venue': venue.get('fullName'),
            'city': (venue.get('address') or {}).get('city'),
            'neutral': bool(comp.get('neutralSite')),
            'espn_away': next((( c.get('team') or {}).get('displayName')
                               for c in cs if c.get('homeAway') == 'away'), None),
            'espn_home': next((( c.get('team') or {}).get('displayName')
                               for c in cs if c.get('homeAway') == 'home'), None),
        })
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def load_contexts(dates: list) -> list:
    r = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H_READ,
                     timeout=60,
                     params={'game_date': f"in.({','.join(dates)})",
                             'select': 'game_id,game_date,home_team,away_team,'
                                       'venue,is_neutral_site',
                             'limit': '2000'})
    if r.status_code != 200:
        raise RuntimeError(f'ncaab_game_context {r.status_code}: {r.text[:300]}')
    return r.json() or []


def run(date: str | None = None, days: int = 1, days_back: int = 0,
        dry_run: bool = False) -> int:
    base = datetime.fromisoformat(date or _et_today()).date()
    dates = [(base + timedelta(days=i)).isoformat() for i in range(max(1, days))]
    dates += [(base - timedelta(days=i)).isoformat()
              for i in range(1, days_back + 1)]
    dates = sorted(set(dates))
    print(f'== enrich_ncaab_venue · {dates[0]} .. {dates[-1]} '
          f'({len(dates)} date(s)) ==')

    ctxs = load_contexts(dates)
    if not ctxs:
        print('  no ncaab_game_context rows for those dates')
        return 0
    print(f'  {len(ctxs)} context rows')

    by_date = defaultdict(list)
    for c in ctxs:
        by_date[c['game_date']].append(c)

    wrote = unchanged = unmatched = flipped = neutral_found = 0
    for day in dates:
        rows = by_date.get(day) or []
        if not rows:
            continue
        try:
            idx = espn_index(day)
        except Exception as e:
            print(f'  ! ESPN {day} failed: {e}')
            continue
        time.sleep(0.4)
        print(f'\n  {day}: {len(rows)} context rows, {len(idx)} ESPN pairs')
        for c in rows:
            ha = _resolver.resolve(c['home_team']) or c['home_team']
            aa = _resolver.resolve(c['away_team']) or c['away_team']
            hit = idx.get(frozenset((ha, aa)))
            if not hit:
                unmatched += 1
                print(f"     ? no ESPN pair for {c['away_team']} @ {c['home_team']}")
                continue
            if hit['neutral']:
                neutral_found += 1
            # Orientation disagreement is EXPECTED at a neutral site and is
            # reported, never acted on -- our home/away is what every
            # published pick is keyed on.
            espn_home_canon = _resolver.resolve(hit['espn_home']) or hit['espn_home']
            if espn_home_canon != ha:
                flipped += 1
                tag = ' (neutral — orientation is arbitrary)' if hit['neutral'] else ''
                print(f"     ~ ESPN calls {hit['espn_home']} the home side, "
                      f"we call {c['home_team']}{tag}")

            payload = {}
            if hit['venue'] and hit['venue'] != c.get('venue'):
                payload['venue'] = hit['venue']
            if bool(c.get('is_neutral_site')) != hit['neutral']:
                payload['is_neutral_site'] = hit['neutral']
            if not payload:
                unchanged += 1
                continue
            if dry_run:
                print(f"     [dry] {c['away_team'][:22]:22s} @ "
                      f"{c['home_team'][:22]:22s} {payload}")
                wrote += 1
                continue
            pr = requests.patch(f'{SB}/rest/v1/ncaab_game_context',
                                headers=H_WRITE,
                                params={'game_id': f"eq.{c['game_id']}"},
                                json=payload, timeout=60)
            if pr.status_code not in (200, 204):
                print(f"     ! patch {c['game_id']} {pr.status_code}: "
                      f'{pr.text[:200]}')
                continue
            # A 200 is not a write (feedback_204_is_not_a_write) and a column
            # can refuse one field while accepting another
            # (feedback_receipt_audit_column_is_frozen_too): read back EVERY
            # field we set.
            body = pr.json() if pr.content else []
            got = body[0] if body else {}
            bad = [k for k, v in payload.items() if got.get(k) != v]
            if bad:
                print(f"     ! readback mismatch on {c['game_id']}: {bad} "
                      f'(sent {payload}, stored '
                      f'{ {k: got.get(k) for k in payload} })')
                continue
            wrote += 1
            if 'is_neutral_site' in payload:
                print(f"     NEUTRAL  {c['away_team'][:20]:20s} @ "
                      f"{c['home_team'][:20]:20s} -> {hit['venue']} "
                      f"({hit['city']})")

    print(f'\n  wrote {wrote} · already correct {unchanged} · '
          f'no ESPN match {unmatched}')
    print(f'  neutral-site games seen: {neutral_found} · '
          f'orientation disagreements: {flipped} '
          f'(reported only, never rewritten)')
    return wrote


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD, defaults to today ET')
    p.add_argument('--days', type=int, default=1,
                   help='days forward to cover, including --date')
    p.add_argument('--days-back', type=int, default=0,
                   help='also cover this many days BEFORE --date')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    run(date=a.date, days=a.days, days_back=a.days_back, dry_run=a.dry_run)
