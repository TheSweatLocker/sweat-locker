#!/usr/bin/env python3
"""NCAAF advanced team metrics computed from ESPN play-by-play. No CFBD.

WHY (2026-10-06)
----------------
Andy: "living with a frozen EPA until November 1st is not an answer ... I need
solutions."

He is right. CFBD's monthly quota is exhausted and does not reset until Nov 1,
and ncaaf_team_game_stats carries the advanced columns that matter most —
def_ppa is one of only two signals that repeated above breakeven in all three
seasons (stat_pit_vs_cover). Shipping stale inputs to paying subscribers for
four weeks is not acceptable, and "wait" is not a plan.

The insight that makes this tractable: MOST of what CFBD calls "advanced" is
not a model at all. Success rate, stuff rate, power success and the
line/second-level/open-field yardage splits are pure arithmetic on down,
distance and yards gained. CFBD computes them from play-by-play. So can we,
from ESPN, which needs no key and has no quota.

Proven before this file was written, on Vanderbilt @ Georgia 2026-10-03:

    Vanderbilt success_rate   mine 0.3400        CFBD 0.3400        exact
    Vanderbilt stuff_rate     mine 0.17391304    CFBD 0.17391304    exact
    Georgia    stuff_rate     mine 0.12121212    CFBD 0.12121212    exact
    Vanderbilt off_plays      mine 50            CFBD 50            exact

ESPN play coverage is complete: down, distance, yardsToEndzone, statYardage,
type, isPenalty, isTurnover and the drive's offensive team, 156/156 on the
probe game.

ONLY PPA/EPA NEEDS A MODEL, and that is deliberately NOT in this file. The
inputs for it are all present, and CFBD's own off_ppa/def_ppa sit in our DB
for three seasons, so an EP model can be fitted and PROVEN against ~5,400
team-games of ground truth before anything depends on it. That is the next
step, not this one. Explosiveness is also held back: it is average EPA on
successful plays, so it needs the same model.

DEFINITIONS ARE VALIDATED, NOT ASSUMED
Every metric here is checked against the CFBD values we already hold, and the
exact convention (per-rush average vs total, which downs count, what counts as
a play) is settled by whether it reproduces them. --validate prints per-field
agreement; --debug lists play types the classifier did not recognise, which is
how the remaining Georgia 66-vs-69 play gap gets closed.

    python ncaaf_advanced_from_pbp.py --validate --date 2026-10-03
    python ncaaf_advanced_from_pbp.py --validate --date 2026-10-03 --debug
    python ncaaf_advanced_from_pbp.py --date 2026-10-11 --apply
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

from team_resolver import resolve_ncaaf_team          # noqa: E402

ESPN = ('https://site.api.espn.com/apis/site/v2/sports/football/'
        'college-football')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120 Safari/537.36'}
CACHE = _HERE / '_pbp_cache'      # gitignored (mlb_pipeline/_*)


# ── play classification ──────────────────────────────────────────────
# A play's type text drives everything. Anything unrecognised is counted and
# reported by --debug rather than silently dropped, because a dropped play
# changes a denominator and so changes every rate computed from it.
def classify(type_text: str, text: str):
    t = type_text or ''
    if 'Rush' in t:
        return 'rush'
    if 'Sack' in t:
        return 'pass'          # a sack is a failed pass play
    # "Interception" and "Interception Return Touchdown" carry no "Pass" in
    # the type text, so the first version dropped all 33 of them on 10-03 and
    # under-counted offensive plays — Georgia came out 66 against CFBD's 69.
    # An intercepted pass is still a pass play that consumed a down.
    if 'Interception' in t:
        return 'pass'
    if 'Pass' in t:
        return 'pass'
    if 'Fumble' in t:
        # A fumble on a run or pass is still an offensive play; the text says
        # which. Recovered-by-defence fumbles still consumed a down.
        low = (text or '').lower()
        if ' pass' in low:
            return 'pass'
        if ' rush' in low or ' run' in low:
            return 'rush'
        return 'other'
    return None


def successful(down: int, dist: float, gain: float):
    """Connelly: 50% of needed on 1st, 70% on 2nd, 100% on 3rd/4th."""
    if down == 1:
        return gain >= 0.5 * dist
    if down == 2:
        return gain >= 0.7 * dist
    if down in (3, 4):
        return gain >= dist
    return None


def line_credit(gain: float) -> float:
    """Connelly line-yards credit for one rush.

    negative: 120% of it · 0-4 yd: 100% · 5-10 yd: 50% · 11+: nothing.
    """
    if gain < 0:
        return gain * 1.2
    if gain <= 4:
        return gain
    if gain <= 10:
        return 4 + (gain - 4) * 0.5
    return 7.0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── fetch ────────────────────────────────────────────────────────────
def _cached_summary(event_id: str) -> dict | None:
    """ESPN summary, cached on disk. A full-season backfill re-reads the same
    games repeatedly while definitions are being tuned; without a cache that
    is thousands of needless requests against a free endpoint."""
    CACHE.mkdir(exist_ok=True)
    fp = CACHE / f'{event_id}.json'
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    for attempt in range(3):
        try:
            r = requests.get(f'{ESPN}/summary', headers=UA, timeout=30,
                             params={'event': event_id})
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            fp.write_text(r.text, encoding='utf-8')
            return r.json()
        time.sleep(1.0)
    return None


def _resolve(team: dict):
    for cand in (team.get('location'), team.get('displayName'),
                 team.get('shortDisplayName'), team.get('name')):
        if cand:
            got = resolve_ncaaf_team(cand)
            if got:
                return got
    return None


def event_ids(date: str) -> list:
    r = requests.get(f'{ESPN}/scoreboard', headers=UA, timeout=30,
                     params={'dates': date.replace('-', ''), 'groups': '80',
                             'limit': '400'})
    if r.status_code != 200:
        print(f'  ⚠ scoreboard {r.status_code} for {date}')
        return []
    return [e['id'] for e in r.json().get('events', [])
            if (e.get('competitions') or [{}])[0]
            .get('status', {}).get('type', {}).get('state') == 'post']


# ── the computation ──────────────────────────────────────────────────
def game_metrics(summary: dict, unknown: collections.Counter):
    """{canonical_team: offensive metric dict} for one game."""
    # Map ESPN team id -> canonical name, from the boxscore team blocks.
    id2name = {}
    for t in ((summary.get('boxscore') or {}).get('teams') or []):
        tm = t.get('team') or {}
        if tm.get('id'):
            id2name[str(tm['id'])] = _resolve(tm)
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    drives = collections.Counter()
    for d in ((summary.get('drives') or {}).get('previous') or []):
        off_id = str(((d.get('team') or {}).get('id')) or '')
        off = id2name.get(off_id) or _resolve(d.get('team') or {})
        if not off:
            continue
        drives[off] += 1
        for p in (d.get('plays') or []):
            ttext = (p.get('type') or {}).get('text')
            ptext = p.get('text') or ''
            # A snap that drew a flag, and a fumble whose text does not say
            # pass or rush, are still SCRIMMAGE PLAYS — they belong in the
            # play count and the success denominator. They must NOT count as
            # rushes: the first attempt put accepted penalties through the
            # rush path and every rush-based metric got worse (stuff 65.7 ->
            # 59.3, line 68.5 -> 62.0) while off_plays improved, which is
            # what pointed at this split.
            # "NO PLAY" text means the snap was waved off — not a play.
            if p.get('isPenalty'):
                if 'NO PLAY' in ptext.upper():
                    continue
                kind = 'play_only'
            else:
                kind = classify(ttext, ptext)
                if kind == 'other':
                    kind = 'play_only'
            if kind is None:
                unknown[ttext] += 1
                continue
            st = p.get('start') or {}
            down, dist = st.get('down'), _num(st.get('distance'))
            gain = _num(p.get('statYardage'))
            if not down or dist is None or gain is None:
                unknown[f'{ttext} (no down/dist/gain)'] += 1
                continue
            a = agg[off]
            a['plays'] += 1
            s = successful(int(down), dist, gain)
            if s is not None:
                a['success'] += int(s)
            if kind == 'rush':
                a['rushes'] += 1
                a['stuffed'] += int(gain <= 0)
                a['line'] += line_credit(gain)
                a['second'] += max(0.0, min(gain, 10.0) - 5.0)
                a['open'] += max(0.0, gain - 10.0)
                if int(down) in (3, 4) and dist <= 2:
                    a['power_n'] += 1
                    a['power_ok'] += int(gain >= dist or p.get('scoringPlay'))
    out = {}
    for team, a in agg.items():
        n, r = a['plays'], a['rushes']
        out[team] = {
            'off_plays': int(n),
            'off_drives': int(drives[team]),
            'off_success_rate': (a['success'] / n) if n else None,
            'off_stuff_rate': (a['stuffed'] / r) if r else None,
            'off_line_yards': (a['line'] / r) if r else None,
            'off_second_level_yards': (a['second'] / r) if r else None,
            'off_open_field_yards': (a['open'] / r) if r else None,
            'off_power_success': ((a['power_ok'] / a['power_n'])
                                  if a['power_n'] else None),
            'off_rushing_plays': int(r),
        }
    return out


# Field -> (our key, CFBD column, tolerance). Separate tolerances because a
# rate and a yards-per-rush figure are not comparable quantities.
COMPARE = (
    ('off_plays',              'off_plays',              0.001),
    ('off_success_rate',       'off_success_rate',       0.0005),
    ('off_stuff_rate',         'off_stuff_rate',         0.0005),
    ('off_line_yards',         'off_line_yards',         0.02),
    ('off_second_level_yards', 'off_second_level_yards', 0.02),
    ('off_open_field_yards',   'off_open_field_yards',   0.02),
    ('off_power_success',      'off_power_success',      0.0005),
    ('off_drives',             'off_drives',             0.001),
    ('off_rushing_plays',      'off_rushing_plays',      0.001),
)
WRITABLE = tuple(c for _k, c, _t in COMPARE)


def db_rows(date: str):
    r = requests.get(f'{SB}/rest/v1/ncaaf_team_game_stats', headers=H,
                     timeout=60,
                     params={'select': '*', 'game_date': f'eq.{date}',
                             'limit': '400'})
    if r.status_code not in (200, 206):
        print(f'  ⚠ db {r.status_code}: {r.text[:200]}')
        return {}, {}
    rows = r.json()
    by_team = {z['team']: z for z in rows if z.get('team')}
    by_opp = {}
    for z in rows:
        if z.get('opponent'):
            by_opp.setdefault(z['opponent'], z)
    return by_team, by_opp


def collect(dates: list, debug: bool):
    """[(date, team, computed, db_row)] plus the unknown-play tally."""
    unknown = collections.Counter()
    rows = []
    for d in dates:
        ids = event_ids(d)
        if not ids:
            continue
        by_team, by_opp = db_rows(d)
        got = 0
        for eid in ids:
            j = _cached_summary(eid)
            if not j:
                continue
            m = game_metrics(j, unknown)
            # Within a game the two teams are each other's opponents, so a
            # team whose canonical name differs from CFBD's spelling is found
            # through the other side — UConn via Syracuse.
            names = list(m)
            for team, comp in m.items():
                other = next((x for x in names if x != team), None)
                db = by_team.get(team) or (by_opp.get(other) if other else None)
                rows.append((d, team, comp, db))
                got += 1
        print(f'  {d}: {len(ids)} games · {got} team-sides computed')
    return rows, unknown


def validate(dates: list, debug: bool) -> int:
    rows, unknown = collect(dates, debug)
    agree = collections.Counter()
    differ = collections.Counter()
    ex = collections.defaultdict(list)
    nodb = 0
    for d, team, comp, db in rows:
        if db is None:
            nodb += 1
            continue
        for key, col, tol in COMPARE:
            cv, dv = comp.get(key), db.get(col)
            if cv is None or dv is None:
                continue
            if abs(float(cv) - float(dv)) <= tol:
                agree[key] += 1
            else:
                differ[key] += 1
                if len(ex[key]) < 5:
                    ex[key].append(f'{team[:20]} {d} mine={float(cv):.6g} '
                                   f'cfbd={float(dv):.6g}')
    print(f'\n  team-sides computed: {len(rows)}   no CFBD row: {nodb}')
    print(f'\n  {"metric":<26} {"agree":>7} {"differ":>7} {"match":>8}')
    ta = td = 0
    for key, _col, _tol in COMPARE:
        a, x = agree[key], differ[key]
        if a + x == 0:
            print(f'  {key:<26} {"-":>7} {"-":>7} {"no overlap":>8}')
            continue
        ta += a
        td += x
        print(f'  {key:<26} {a:7d} {x:7d} {a/(a+x)*100:7.1f}%'
              + ('' if x == 0 else '   <<<'))
    if ta + td:
        print(f'\n  OVERALL {ta}/{ta+td} ({ta/(ta+td)*100:.2f}%)')
    for key, lst in ex.items():
        print(f'\n  mismatches · {key}:')
        for e in lst:
            print(f'     {e}')
    if unknown:
        print(f'\n  --- play types the classifier did NOT recognise '
              f'({sum(unknown.values())} plays) ---')
        for t, n in unknown.most_common(18 if debug else 8):
            print(f'     {n:5d}  {t}')
        print('  Each of these changes a denominator, so they are reported '
              'rather than dropped quietly.')
    return 0


def apply_dates(dates: list, do_write: bool) -> int:
    rows, unknown = collect(dates, False)
    written = nodb = 0
    for d, team, comp, db in rows:
        if db is None:
            nodb += 1
            continue
        patch = {c: comp[k] for k, c, _t in COMPARE
                 if comp.get(k) is not None and db.get(c) is None}
        if not patch:
            continue
        if not do_write:
            print(f'     {team[:24]:24s} ' + ' '.join(
                f'{k}={round(v, 4) if isinstance(v, float) else v}'
                for k, v in list(patch.items())[:4]))
            continue
        patch['source'] = 'espn_pbp'
        r = requests.patch(f'{SB}/rest/v1/ncaaf_team_game_stats', headers=H_W,
                           timeout=60,
                           params={'team': f'eq.{db["team"]}',
                                   'game_date': f'eq.{d}'},
                           data=json.dumps(patch))
        body = r.json() if r.content else []
        first = next(iter(patch))
        ok = (r.status_code in (200, 201) and isinstance(body, list) and body
              and body[0].get(first) is not None)
        if ok:
            written += 1
        else:
            print(f'   x {team} {r.status_code} '
                  f'rows={len(body) if isinstance(body, list) else "?"} '
                  f'{r.text[:110]}')
    print(f'\n  {"wrote" if do_write else "would write"} {written} row(s)'
          f'{f"; {nodb} with no CFBD row" if nodb else ""}')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--debug', action='store_true',
                    help='list every unrecognised play type')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    dates = ([args.date] if args.date else
             [(today - dt.timedelta(days=i)).isoformat()
              for i in range(args.days)])
    mode = 'VALIDATE' if args.validate else ('APPLY' if args.apply else 'DRY')
    print(f'=== ncaaf_advanced_from_pbp · {len(dates)} date(s) · {mode} ===')
    if args.validate:
        return validate(dates, args.debug)
    return apply_dates(dates, args.apply)


if __name__ == '__main__':
    sys.exit(main())
