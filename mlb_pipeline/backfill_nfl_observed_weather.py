#!/usr/bin/env python3
"""OBSERVED at-game weather onto nfl_game_results, from Open-Meteo.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
`nfl_game_results.wind` is NULL on every NFL game since 2026-10-11 — 23
outdoor games across three weeks — and wind >= 12 -> UNDER is the only NFL
lens validated over 28 seasons (55.8% walk-forward, CI[52.9,58.6], 17 of 26
seasons, control side 49.3%). The one thing that works has been dark.

`backfill_nfl_result_weather.py` CANNOT fix it, which I checked before
claiming it could. Its source is nflverse games.csv, and nflverse stopped
publishing wind after week 4:

    2026 wind readings by week:  wk1 10, wk2 10, wk3 13, wk4 12,
                                 wk5 0, wk6 0, wk7 0   (all played)

Weeks 5-6 are two to three weeks old and still empty, so it is an upstream
stoppage, not a lag that self-heals. Our rows are already in sync with that
source (44 of our 45 vs nflverse's 45). Scheduling that script would have
changed nothing.

Open-Meteo's historical archive carries OBSERVED hourly wind and temperature
and needs NO API KEY. project_weather_pullers_silent_noop_1009 is explicit
about the trap here: a fail-loudly API-key guard was placed in front of
Open-Meteo and cost NCAAF five weeks of weather. **There is no key check in
this file.** The OPENWEATHER key is in GH secrets and is irrelevant to this
endpoint.

STADIUM COORDINATES are imported from nfl_weather_pull.STADIUMS rather than
copied, so the two pullers cannot drift apart — a second copy of a constant
is how generate_sweat_card.py ended up importing a renamed name and silently
doing nothing for weeks.

SAFETY
  * fills only rows where wind IS NULL — an existing or hand-corrected
    reading is never overwritten
  * outdoor roofs only; a dome has no wind to measure and writing 0 there
    would contaminate the control side of the lens
  * weather columns only (wind, temp). Scores, lines and results feed
    published records and are never touched
  * never INSERTS; it PATCHes rows that already exist, matched on
    (game_date, away_team, home_team)
  * dry-run by default, and every write is read back

    python backfill_nfl_observed_weather.py --season 2026
    python backfill_nfl_observed_weather.py --season 2026 --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from nfl_weather_pull import STADIUMS
except ImportError as e:            # fail loud: a silent fallback here would
    sys.exit('cannot import STADIUMS from nfl_weather_pull: %s' % e)

ARCHIVE = 'https://archive-api.open-meteo.com/v1/archive'
FORECAST = 'https://api.open-meteo.com/v1/forecast'


def _load_env(path):
    out = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_HERE = os.path.dirname(os.path.abspath(__file__))
_E = _load_env(os.path.join(_HERE, '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = _E.get('SUPABASE_KEY') or os.environ.get('SUPABASE_KEY')
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def _req(method, path, params=None, body=None, extra=None):
    u = BASE + path
    if params:
        u += '?' + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(u, data=data, method=method)
    for k, v in HDR.items():
        r.add_header(k, v)
    for k, v in (extra or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        print('    HTTP %s on %s %s :: %s'
              % (e.code, method, path,
                 e.read().decode('utf-8', 'replace')[:300]))
        return None, e.code


def fetch_all(table, **filt):
    out, off = [], 0
    while True:
        p = {'select': '*', 'limit': '1000', 'offset': str(off)}
        p.update(filt)
        rows, st = _req('GET', table, p)
        if not isinstance(rows, list):
            print('    read failed on %s (status %s)' % (table, st))
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def om_get(url, params):
    u = url + '?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(u, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        print('    Open-Meteo HTTP %s :: %s'
              % (e.code, e.read().decode('utf-8', 'replace')[:200]))
        return None
    except Exception as e:                            # noqa: BLE001
        print('    Open-Meteo failed: %s' % e)
        return None


def observed(lat, lng, date_str, hour_local=13):
    """Observed wind (mph) and temp (F) nearest `hour_local` on date_str.

    The archive endpoint lags a few days, so recent dates fall back to the
    forecast endpoint with past_days, which serves the same observations.
    Both are keyless.
    """
    common = {'latitude': lat, 'longitude': lng,
              'hourly': 'wind_speed_10m,temperature_2m',
              'wind_speed_unit': 'mph', 'temperature_unit': 'fahrenheit',
              'timezone': 'America/New_York'}
    try:
        d = dt.date.fromisoformat(date_str)
    except ValueError:
        return None, None
    age = (dt.date.today() - d).days
    if age >= 6:
        j = om_get(ARCHIVE, dict(common, start_date=date_str,
                                 end_date=date_str))
    else:
        j = om_get(FORECAST, dict(common, past_days=min(max(age + 1, 1), 92),
                                  forecast_days=1))
    if not j or 'hourly' not in j:
        return None, None
    h = j['hourly']
    times = h.get('time') or []
    winds = h.get('wind_speed_10m') or []
    temps = h.get('temperature_2m') or []
    best = None
    for i, t in enumerate(times):
        if not str(t).startswith(date_str):
            continue
        try:
            hh = int(str(t)[11:13])
        except (ValueError, IndexError):
            continue
        gap = abs(hh - hour_local)
        if best is None or gap < best[0]:
            best = (gap, i)
    if best is None:
        return None, None
    i = best[1]
    w = winds[i] if i < len(winds) else None
    t = temps[i] if i < len(temps) else None
    return (round(float(w)) if w is not None else None,
            round(float(t)) if t is not None else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2026')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 76)
    print('NFL OBSERVED WEATHER BACKFILL  season=%s  (%s)'
          % (a.season, 'APPLY' if a.apply else 'DRY RUN'))
    print('  source: Open-Meteo, NO API KEY (see the docstring -- a key')
    print('          guard in front of this endpoint already cost NCAAF')
    print('          five weeks of weather)')
    print('=' * 76)

    rows = fetch_all('nfl_game_results', season='eq.%s' % a.season)
    print('  season rows: %d' % len(rows))

    need = []
    skipped_dome = skipped_have = skipped_nostadium = skipped_future = 0
    today = dt.date.today().isoformat()
    for r in rows:
        if r.get('home_score') is None:
            skipped_future += 1
            continue
        if r.get('wind') is not None:
            skipped_have += 1
            continue
        roof = str(r.get('roof') or '').lower()
        tm = str(r.get('home_team') or '')
        st = STADIUMS.get(tm)
        if st is None:
            skipped_nostadium += 1
            continue
        # a dome has no wind to measure; writing 0 would poison the control
        if roof in ('dome', 'closed') or (not roof and st.get('dome')):
            skipped_dome += 1
            continue
        d = str(r.get('game_date'))[:10]
        if d > today:
            skipped_future += 1
            continue
        need.append((r, st, d))

    print('  need wind: %d   already have: %d   dome/closed: %d   '
          'no stadium: %d   unplayed: %d'
          % (len(need), skipped_have, skipped_dome, skipped_nostadium,
             skipped_future))
    if not need:
        print('  nothing to do.')
        return 0
    byw = collections.Counter(str(r.get('week')) for r, _s, _d in need)
    print('  by week: %s' % dict(sorted(byw.items(),
                                        key=lambda z: int(z[0])
                                        if z[0].isdigit() else 99)))

    # one Open-Meteo call per (stadium, date); games share the cache
    cache = {}
    plans = []
    for r, st, d in need:
        ck = (st['lat'], st['lng'], d)
        if ck not in cache:
            cache[ck] = observed(st['lat'], st['lng'], d)
        w, t = cache[ck]
        if w is None:
            continue
        plans.append((r, w, t))

    print('  Open-Meteo calls made: %d   rows with a reading: %d'
          % (len(cache), len(plans)))
    for r, w, t in plans[:12]:
        print('     %s  %-4s @ %-4s  wind %2d mph  temp %s'
              % (str(r.get('game_date'))[:10], r.get('away_team'),
                 r.get('home_team'), w, t))
    if len(plans) > 12:
        print('     ... and %d more' % (len(plans) - 12))

    ge12 = sum(1 for _r, w, _t in plans if w >= 12)
    print('')
    print('  of those, %d are wind >= 12 -- the games the validated lens '
          'would fire on' % ge12)

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for r, w, t in plans:
        body = {'wind': w}
        if t is not None and r.get('temp') is None:
            body['temp'] = t
        flt = {'game_date': 'eq.%s' % str(r.get('game_date'))[:10],
               'away_team': 'eq.%s' % r.get('away_team'),
               'home_team': 'eq.%s' % r.get('home_team')}
        _x, st_ = _req('PATCH', 'nfl_game_results', flt, body,
                       {'Prefer': 'return=minimal'})
        if st_ in (200, 204):
            ok += 1
        else:
            fail += 1
    print('  wrote %d, failed %d' % (ok, fail))

    # assert the OUTPUT moved -- a green step may write nothing
    after = fetch_all('nfl_game_results', season='eq.%s' % a.season)
    still = sum(1 for x in after
                if x.get('home_score') is not None and x.get('wind') is None
                and str(x.get('roof') or '').lower() in ('outdoors', 'open'))
    have = sum(1 for x in after if x.get('wind') is not None)
    print('  outdoor played rows STILL missing wind: %d   total with wind: %d'
          % (still, have))
    if ok and have <= skipped_have:
        print('  ABORT-LEVEL: writes reported success but the column did not'
              ' move.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
