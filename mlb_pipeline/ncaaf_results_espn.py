#!/usr/bin/env python3
"""NCAAF final scores from ESPN — the CFBD-free fallback.

WHY (2026-10-06)
----------------
Andy: "mlb grade overnight failed ... ncaaf team stats pull py failed exit 1."
CFBD is returning `429 {"message":"Monthly call quota exceeded."}` on every
request, on the 6th of the month.

The stale stats are annoying. This is the part that actually breaks the
product: `resolve_ncaaf_results` gets its scores from CFBD, so with the quota
gone it reports "CFBD games w/ scores: 0" and **NCAAF cannot be graded at
all**. Next Saturday has 48 games already scheduled and 0 scored. Without a
second source, three weekends of NCAAF results would sit ungraded until the
quota resets.

ESPN's college-football scoreboard needs no key and has no quota. Validated
against the one weekend that DID land before the quota died (2026-10-03):

    ESPN events                         54   (our table: 54 games)
    ESPN team names resolved to ours  108/108  (100%)
    scores agreeing with CFBD's        53 match, 0 mismatch, 1 not in table

Zero disagreements on 53 games is why this is a safe substitute rather than a
guess.

FALLBACK, NOT REPLACEMENT. Only fills rows where `home_score IS NULL`, so a
CFBD-sourced score is never overwritten and running this is always safe. Run
it AFTER resolve_ncaaf_results so CFBD stays the primary when it has quota.

Name resolution goes through team_resolver.resolve_ncaaf_team, which returns
None rather than guessing — a game whose teams cannot both be resolved is
skipped and reported, never written against a guessed team. A substring match
once graded a -38.5 favourite as a loss on a 55-0 win ("Iowa" inside
"Northern Iowa"), which is why nothing here matches loosely.

    python ncaaf_results_espn.py                    # dry, last 10 days
    python ncaaf_results_espn.py --apply
    python ncaaf_results_espn.py --date 2026-10-10 --apply
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

from team_resolver import resolve_ncaaf_team

ESPN = ('https://site.api.espn.com/apis/site/v2/sports/football/'
        'college-football/scoreboard')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'}


def _resolve(team: dict):
    """ESPN team object -> our canonical name, or None. Never guesses."""
    for cand in (team.get('location'), team.get('displayName'),
                 team.get('shortDisplayName'), team.get('name')):
        if cand:
            r = resolve_ncaaf_team(cand)
            if r:
                return r
    return None


def espn_finals(date: str) -> dict:
    """{(away, home): (away_score, home_score, overtime)} for one date."""
    out = {}
    # groups=80 is FBS. Division I-A only, matching what we publish.
    r = requests.get(ESPN, headers=UA, timeout=30,
                     params={'dates': date.replace('-', ''), 'groups': '80',
                             'limit': '400'})
    if r.status_code != 200:
        print(f'  ⚠ ESPN {r.status_code} for {date}')
        return out
    for e in r.json().get('events', []):
        c = e['competitions'][0]
        if c['status']['type']['state'] != 'post':
            continue
        try:
            a = [x for x in c['competitors'] if x['homeAway'] == 'away'][0]
            h = [x for x in c['competitors'] if x['homeAway'] == 'home'][0]
        except IndexError:
            continue
        ka, kh = _resolve(a['team']), _resolve(h['team'])
        if not (ka and kh):
            print(f'     skip (unresolved): {a["team"].get("displayName")} @ '
                  f'{h["team"].get("displayName")}')
            continue
        try:
            asc, hsc = int(a.get('score')), int(h.get('score'))
        except (TypeError, ValueError):
            continue
        ot = (c['status'].get('period') or 4) > 4
        out[(ka, kh)] = (asc, hsc, ot)
    return out


def grade(hs: int, as_: int, spread, total):
    """close_spread is the HOME handicap in NCAAF (verified n=7,325)."""
    res = {}
    margin = hs - as_
    if spread is not None:
        try:
            adj = margin + float(spread)
            res['spread_result'] = ('home_covered' if adj > 0.001 else
                                    'away_covered' if adj < -0.001 else 'push')
        except (TypeError, ValueError):
            pass
    if total is not None:
        try:
            t = hs + as_
            res['total_result'] = ('over' if t > float(total) else
                                   'under' if t < float(total) else 'push')
        except (TypeError, ValueError):
            pass
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--days', type=int, default=10)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    dates = ([args.date] if args.date else
             [(today - dt.timedelta(days=i)).isoformat() for i in range(args.days)])

    print(f'=== ncaaf_results_espn · {len(dates)} date(s) · '
          f'{"APPLY" if args.apply else "DRY"} ===')

    fixes, stats = [], collections.Counter()
    for d in dates:
        rows = requests.get(f'{SB}/rest/v1/ncaaf_game_results', headers=H,
                            timeout=60,
                            params={'select': 'game_id,game_date,away_team,'
                                              'home_team,home_score,close_spread,'
                                              'close_total',
                                    'game_date': f'eq.{d}',
                                    'home_score': 'is.null', 'limit': '400'})
        need = rows.json() if rows.status_code in (200, 206) else []
        if not need:
            continue
        finals = espn_finals(d)
        if not finals:
            stats['dates with no ESPN finals'] += 1
            continue
        for g in need:
            key = (g['away_team'], g['home_team'])
            hit = finals.get(key)
            if not hit:
                stats['no ESPN match'] += 1
                continue
            asc, hsc, ot = hit
            patch = {'away_score': asc, 'home_score': hsc,
                     'total_points': asc + hsc, 'home_win': hsc > asc,
                     'overtime': ot}
            patch.update(grade(hsc, asc, g.get('close_spread'), g.get('close_total')))
            fixes.append((g, patch))

    print(f'\n  fillable from ESPN: {len(fixes)}')
    if stats:
        print(f'  {dict(stats)}')
    for g, p in fixes[:12]:
        print(f'     {g["game_date"]} {g["away_team"][:22]:22s} '
              f'{p["away_score"]:>3}-{p["home_score"]:<3} {g["home_team"][:22]:22s} '
              f'sp={p.get("spread_result","-"):<13} tot={p.get("total_result","-")}')
    if not args.apply:
        print('\n  re-run with --apply')
        return 0

    ok = bad = 0
    for g, p in fixes:
        r = requests.patch(f'{SB}/rest/v1/ncaaf_game_results', headers=H_W,
                           timeout=60,
                           params={'game_id': f'eq.{g["game_id"]}',
                                   'home_score': 'is.null'},
                           data=json.dumps(p))
        body = r.json() if r.content else []
        # Verify the VALUE, not just that a row came back.
        if (r.status_code in (200, 204) and body
                and str(body[0].get('home_score')) == str(p['home_score'])):
            ok += 1
        else:
            bad += 1
            if bad <= 3:
                print(f'   x {g["game_id"]} {r.status_code} {r.text[:110]}')
    print(f'\n  filled {ok}/{len(fixes)} (verified by read-back), {bad} failed')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
