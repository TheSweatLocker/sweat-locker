"""NCAAB last-10 form -> ncaab_game_context.home_l10 / away_l10.

== 2026-11-11 . A COLUMN NO WRITER EVER TOUCHED ========================
ANDY asked for current-season stats on the NCAAB surfaces. home_l10 and
away_l10 came back NULL on every one of tonight's games, and the reason is
not a broken enricher -- there was never a writer at all:

  * home_l10 exists ONLY on ncaab_game_context. The same select 400s on
    nba_/nhl_/nfl_/ncaaf_/mlb_game_context, so no other sport has the
    column and no cross-sport script could have been populating it.
  * It was 0 of 506 rows -- not 0 for today, 0 for the table's whole life.
  * generate_ncaab_game_reads.py line ~211 READS it
    (`'home_l10': ctx.get('home_l10')`) and has therefore been handing the
    read prompt a null for form since the column shipped.

enrich_team_form_universal.py does cover NCAAB, but it writes the VENUE-
scoped family (home_ats_l10_at_home, away_ml_l10_on_road, ...). Overall
last-10 is a different fact and nothing owned it.

LEAK-FREE BY CONSTRUCTION
-------------------------
Form is built only from games with a final score and a game_date STRICTLY
EARLIER than the game being enriched. Nothing about the game itself, and
no end-of-season aggregate -- ncaab_team_stats is one row per team-season
holding FINAL numbers, so joining it to its own season leaks the outcome
(see feedback_absent_key_makes_a_gate_dead for what that measurement
showed).

SAMPLE SIZE IS THE WHOLE CAVEAT IN NOVEMBER
-------------------------------------------
On 2026-11-11 teams have played 2-4 games, so "last 10" is really "last
2-4". We write the honest record rather than a padded one: a team with
three results gets '2-1', not '2-1' dressed up as a ten-game sample. The
string carries its own n -- wins + losses IS the sample -- which is why
this writes a record and never a percentage. CLAUDE.md requires every
percentage to show its n and gates at n>=30; a 'last 10' percentage off
three games is exactly the figure that rule exists to stop
(feedback_sample_size_with_pct).

Teams with no completed games yet are left NULL rather than written as
'0-0', because 0-0 reads as a measured result and NULL reads as "no data",
which is the truth.

USAGE
    python enrich_ncaab_l10.py                 # today ET
    python enrich_ncaab_l10.py --date 2026-11-11
    python enrich_ncaab_l10.py --days 3        # today + 2 days ahead
    python enrich_ncaab_l10.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
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
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: How many recent games the label spans at most.
WINDOW = 10

#: An `in.(...)` URL truncates around 2KB and PostgREST answers PGRST100,
#: which a bare `continue` would read as "nothing stored"
#: (feedback_in_filter_url_truncates_silently). Team names are long, so
#: chunk small and RAISE on a bad status rather than skipping.
CHUNK = 20


def _et_today() -> str:
    """Real zone. A hardcoded -4 is wrong the 127 days a year ET is EST
    (feedback_hardcoded_et_offset_breaks_in_est)."""
    return datetime.now(ZoneInfo('America/New_York')).date().isoformat()


def _season_start(game_date: str) -> str:
    """First day of the season this date belongs to.

    NCAAB spans two calendar years, so a January game's season began the
    previous November. Anchored on 1 July, which no season crosses.
    """
    y, m = int(game_date[:4]), int(game_date[5:7])
    return f'{y}-07-01' if m >= 7 else f'{y - 1}-07-01'


def _get(path: str, params: dict) -> list:
    r = requests.get(f'{SB}/rest/v1/{path}', headers=H_READ,
                     params=params, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f'{path} read failed {r.status_code}: {r.text[:300]}')
    return r.json()


def load_contexts(dates: list[str]) -> list[dict]:
    rows = _get('ncaab_game_context', {
        'game_date': f'in.({",".join(dates)})',
        'select': 'game_id,game_date,home_team,away_team,home_l10,away_l10',
        'limit': '2000',
    })
    return rows


def load_results(season_start: str, before: str) -> list[dict]:
    """Every completed game from season start up to (not including) `before`."""
    out, offset = [], 0
    while True:
        page = _get('ncaab_game_results', {
            'game_date': f'gte.{season_start}',
            'home_score': 'not.is.null',
            'select': 'game_date,home_team,away_team,home_score,away_score',
            'order': 'game_date.asc',
            'limit': '1000', 'offset': str(offset),
        })
        out.extend(page)
        if len(page) < 1000:
            return [r for r in out if r['game_date'] < before]
        offset += 1000


def build_form(results: list[dict]) -> dict:
    """team -> list of (game_date, won) in chronological order."""
    hist: dict[str, list] = {}
    for r in results:
        try:
            hs, as_ = float(r['home_score']), float(r['away_score'])
        except (TypeError, ValueError):
            continue
        if hs == as_:
            continue                      # basketball has no ties; skip junk
        hist.setdefault(r['home_team'], []).append((r['game_date'], hs > as_))
        hist.setdefault(r['away_team'], []).append((r['game_date'], as_ > hs))
    for v in hist.values():
        v.sort()
    return hist


def label_for(team: str, game_date: str, hist: dict) -> str | None:
    """'W-L' over the last <=WINDOW completed games strictly before game_date."""
    games = [won for d, won in hist.get(team, ()) if d < game_date]
    if not games:
        return None                       # NULL means no data, '0-0' would lie
    recent = games[-WINDOW:]
    w = sum(1 for x in recent if x)
    return f'{w}-{len(recent) - w}'


def run(game_date: str | None = None, days: int = 1,
        dry_run: bool = False) -> int:
    game_date = game_date or _et_today()
    d0 = datetime.fromisoformat(game_date).date()
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(max(1, days))]
    print(f'== enrich_ncaab_l10 · {", ".join(dates)} ==')

    ctxs = load_contexts(dates)
    if not ctxs:
        print('  no ncaab_game_context rows for those dates — nothing to do')
        return 0
    print(f'  {len(ctxs)} context rows')

    # One results pull covers every date in the window: form is only ever
    # read for dates STRICTLY BEFORE each game, so the widest bound works.
    season_start = _season_start(dates[0])
    results = load_results(season_start, max(dates))
    hist = build_form(results)
    print(f'  {len(results)} completed games since {season_start}, '
          f'{len(hist)} teams with at least one result')

    written = skipped = unchanged = 0
    for c in ctxs:
        hl = label_for(c['home_team'], c['game_date'], hist)
        al = label_for(c['away_team'], c['game_date'], hist)
        if hl is None and al is None:
            skipped += 1
            continue
        payload = {}
        if hl is not None and hl != c.get('home_l10'):
            payload['home_l10'] = hl
        if al is not None and al != c.get('away_l10'):
            payload['away_l10'] = al
        if not payload:
            unchanged += 1
            continue
        if dry_run:
            print(f"  [dry] {c['away_team'][:22]:22s} @ {c['home_team'][:22]:22s} {payload}")
            written += 1
            continue
        r = requests.patch(
            f'{SB}/rest/v1/ncaab_game_context',
            headers=H_WRITE,
            params={'game_id': f'eq.{c["game_id"]}'},
            json=payload, timeout=60,
        )
        if r.status_code not in (200, 204):
            print(f'  ! patch {c["game_id"]} failed {r.status_code}: {r.text[:200]}')
            continue
        # A 200 is not a write (feedback_204_is_not_a_write): the response
        # body is the stored row, so check the values we set came back.
        body = r.json() if r.content else []
        got = body[0] if body else {}
        bad = [k for k, v in payload.items() if got.get(k) != v]
        if bad:
            print(f'  ! readback mismatch on {c["game_id"]}: {bad} '
                  f'(sent {payload}, stored '
                  f'{ {k: got.get(k) for k in payload} })')
            continue
        written += 1

    print(f'\n  wrote {written}, already correct {unchanged}, '
          f'no completed games yet {skipped}')
    return written


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (defaults to today ET)')
    p.add_argument('--days', type=int, default=1,
                   help='how many days forward to cover, including --date')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    run(game_date=a.date, days=a.days, dry_run=a.dry_run)
