#!/usr/bin/env python3
"""SIDE-RULE SHADOW — record what a sport-conditional side rule would pick,
publish nothing, and let it earn its way in.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
Andy: "I guarantee it is better than what we currently do for engine
selection process." Measured on the football spreads we actually published
in 2026, graded off each receipt's own pick_side + result:

    NFL curated   n=59    engine 45.8% ROI -12.64%   take-AWAY 72.9% +39.13%
    NCAAF curated n=346   engine 49.4% ROI  -5.65%   take-HOME 58.4% +11.45%

He is right, and the engine is below what random side-picking would give on
NFL. But three things stop this shipping as a live publish rule today:

 1. **The winning rule is OPPOSITE by sport** (NFL away, NCAAF home). A
    global rule would be actively wrong on the sport with 6x the volume.
 2. **The flip is NOT the mechanism.** Flipping our pick yields the
    complement of our record — 49.0% -> 51.0% walk-forward on NCAAF, which
    is still under breakeven. The side rule is a different and much stronger
    thing, and shipping the flip because the side rule measured well would
    be shipping the wrong lever. (The flip IS already live in
    ncaaf_pipeline.yml and walk-forwards at ~51.5%.)
 3. **The evidence is thin where it is strongest.** Walk-forward by week:

        NCAAF take-HOME  58.4%  n=202  CI[51.5,65.0]  5 of 5 weeks
        NFL   take-AWAY  77.4%  n=31   CI[60.2,88.6]  2 of 2 weeks

    NCAAF has 5 of 5 weeks but its CI lower bound (51.5) sits just BELOW
    breakeven. NFL clears its CI on n=31 across two periods — and the NFL
    away-cover rate over 7,181 games since 1999 is 51.1%, so 77% is a hot
    run, not a law.

Betting subscribers' money on n=31 is the exact mistake this codebase has
paid for repeatedly: the adaptive fade (55.7% fitted -> 47.7%), the prop
ratio rule (STABLE EDGE on a leaked feature), and a weekend-tuned "take the
away side" that went 10-2 then 9-4 and is 51.1% long-run.

So this writes a SHADOW, per feedback_suppression_gate_needs_shadow: the
rule's call is recorded next to the engine's, nothing published moves, and
after enough weeks the record decides. Promotion criterion stated up front
so it cannot be rationalised later:

    PROMOTE a sport's rule when, on SHADOW rows only, it has
      * n >= 150 graded, AND
      * a Wilson lower bound above 52.38%, AND
      * more than 60% of weeks above breakeven, AND
      * it BEATS THE CONCURRENT LEAGUE-WIDE SEASON COVER RATE for that
        side -- added 2026-10-26 after measuring that most of the apparent
        edge is a season regime, not our selection:

            NCAAF home cover   50.6% long-run | 55.0% league 2026 | 58.4% ours
            NFL   home cover   48.9% long-run | 42.0% league 2026 | 27.1% ours

        Of NCAAF's +7.8pts over long-run, ~+4.4 is THE SEASON. Without this
        clause the bar promotes a regime and calls it an edge.

    AND THE REGIME MEAN-REVERTS, measured 2026-10-26 over 28 NFL seasons:

        corr(H1 home-cover, H2 home-cover) = -0.474   <-- NEGATIVE
        H1 and H2 favour the SAME side in 10 of 28 seasons (36%)
        betting the season-to-date leading side: 49.3% ROI -5.90% n=6,161

    So a season tilt does not merely fade, it tends to REVERSE. 2026 NFL
    went H1 50.0% -> H2 34.0%, meaning the away tilt this NFL rule rides is
    a second-half phenomenon in the sport where second halves invert. Treat
    a promotion on seasonal evidence as disqualified, not merely weak.

WHAT IT TOUCHES
Adds exactly one key, `_side_rule_shadow`, inside `primary_play` — the same
convention as the existing `_goat_shadow`. Read-modify-write preserving every
other key, so manual-override markers survive
(feedback_lock_must_see_a_manual_decision) and the publish invariant still
reads primary_play.side / .label unchanged.

Dry-run by default.

    python side_rule_shadow.py --sport NFL
    python side_rule_shadow.py --sport NFL --apply
    python side_rule_shadow.py --sport NCAAF --date 2026-10-24 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# The measured rule, per sport. NOT fitted here -- these come from the
# 2026 receipt measurement above, and a sport absent from this map gets no
# shadow rather than a guessed one.
SIDE_RULE = {'NFL': 'AWAY', 'NCAAF': 'HOME'}

# Long-run cover rate of that same side, from the results tables
# (NFL 7,181 games since 1999; NCAAF 6,527 since 2022). Carried in the
# shadow so nobody reads the 2026 number without the base rate beside it.
BASE_RATE = {'NFL': 51.1, 'NCAAF': 50.6}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}

PROMOTE_MIN_N = 150
PROMOTE_MIN_LOWER = 52.38
PROMOTE_MIN_WEEK_FRAC = 0.60


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
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        # a 400 names a COLUMN, not just a table -- print the body
        print('    HTTP %s on %s %s :: %s'
              % (e.code, method, path,
                 e.read().decode('utf-8', 'replace')[:400]))
        return None, e.code


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def et_today():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


def as_dict(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return dict(v) if isinstance(v, dict) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(CTX))
    ap.add_argument('--date', default=None,
                    help='ET game_date; default today')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    sport = a.sport
    date = a.date or et_today()
    want = SIDE_RULE.get(sport)
    print('=' * 78)
    print('SIDE-RULE SHADOW  %s  %s  (%s)'
          % (sport, date, 'APPLY' if a.apply else 'DRY RUN'))
    print('=' * 78)
    if not want:
        print('  no measured rule for %s -- writing nothing rather than'
              ' guessing a side.' % sport)
        return 0
    print('  measured rule: always take %s   (2026 receipts)' % want)
    print('  long-run cover rate of that side: %.1f%%  <-- read these'
          ' together' % BASE_RATE[sport])

    rows, st = _req('GET', CTX[sport], {'select': '*',
                                        'game_date': 'eq.%s' % date})
    if not isinstance(rows, list):
        print('  read failed (status %s)' % st)
        return 1
    print('  games on the slate: %d' % len(rows))
    if not rows:
        return 0

    agree = differ = skipped = written = 0
    for g in rows:
        pp = as_dict(g.get('primary_play'))
        away = str(g.get('away_team') or '')
        home = str(g.get('home_team') or '')
        label = '%s @ %s' % (away, home)
        cs = f(g.get('close_spread'))
        eng_side = str(pp.get('side') or '').upper()
        eng_label = str(pp.get('label') or '')

        if not pp:
            print('  %-14s no primary_play -- skipped' % label)
            skipped += 1
            continue

        # The rule's side, and the number that side is actually getting.
        # NFL close_spread is the home handicap NEGATED (validated
        # 7181/7181); NCAAF close_spread IS the home handicap (6503/6504).
        if cs is None:
            rule_line = None
        elif sport == 'NFL':
            rule_line = (-cs) if want == 'HOME' else cs
        else:
            rule_line = cs if want == 'HOME' else (-cs)

        team = home if want == 'HOME' else away
        shadow = {
            'rule': 'always take %s' % want,
            'side': want,
            'team': team,
            'line': rule_line,
            'basis': '2026 receipts: %s engine vs take-%s'
                     % (sport, want),
            'long_run_base_rate': BASE_RATE[sport],
            'agrees_with_engine': (eng_side == want) if eng_side else None,
            'engine_side': eng_side or None,
            'engine_label': eng_label or None,
            'status': 'SHADOW -- not published, not a play',
            'promote_when': 'n>=%d and Wilson lower >%.2f%% and >%.0f%% of'
                            ' weeks over breakeven'
                            % (PROMOTE_MIN_N, PROMOTE_MIN_LOWER,
                               100 * PROMOTE_MIN_WEEK_FRAC),
            'computed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        }

        same = (eng_side == want)
        agree += same
        differ += (not same)
        mark = 'AGREES' if same else 'DIFFERS'
        print('  %-14s engine %-5s %-14s | rule %-5s %-5s %-6s  %s'
              % (label, eng_side or '-', eng_label or '-', want, team,
                 ('%+g' % rule_line) if rule_line is not None else '-',
                 mark))

        if not a.apply:
            continue

        # read-modify-write: add ONE key, preserve everything else, so
        # manual-decision markers and the publish invariant are untouched
        fresh, _s = _req('GET', CTX[sport],
                         {'select': 'primary_play', 'game_id': 'eq.%s'
                          % g.get('game_id')})
        live = as_dict((fresh or [{}])[0].get('primary_play')) if fresh \
            else dict(pp)
        if not live:
            live = dict(pp)
        live['_side_rule_shadow'] = shadow
        _r, stt = _req('PATCH', CTX[sport], {'game_id': 'eq.%s' % g.get('game_id')},
                       {'primary_play': live},
                       {'Prefer': 'return=minimal'})
        if stt in (200, 204):
            # assert the OUTPUT moved -- a green step may write nothing
            back, _s2 = _req('GET', CTX[sport],
                             {'select': 'primary_play', 'game_id': 'eq.%s'
                              % g.get('game_id')})
            got = as_dict((back or [{}])[0].get('primary_play')) if back \
                else {}
            if isinstance(got.get('_side_rule_shadow'), dict):
                written += 1
                # and the engine's own side must be unchanged
                if str(got.get('side') or '').upper() != eng_side:
                    print('     ABORT: engine side changed on %s' % label)
                    return 1
            else:
                print('     write reported %s but the key is absent on %s'
                      % (stt, label))
        else:
            print('     PATCH failed on %s (status %s)' % (label, stt))

    print('')
    print('  rule AGREES with the engine on %d, DIFFERS on %d, skipped %d'
          % (agree, differ, skipped))
    if a.apply:
        print('  shadow written on %d games' % written)
    else:
        print('  DRY RUN -- nothing written. Re-run with --apply.')
    print('')
    print('  NOTHING PUBLISHED CHANGED. This is a shadow; the engine still')
    print('  owns the published side until the record promotes the rule.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
