"""Does the AP-rank differential predict covers? — the away-path candidate.

WHY THIS TEST EXISTS
audit_home_away_signal_bias showed the engine fires HOME on 91.4% of signal
firings and that AP rank, SOS and SOR have ZERO signals among 110 NCAAF
entries. So when Iowa (AP #20, better on every rating) visited unranked
Washington, the engine had no way to express Iowa's case and produced
Washington on all three markets.

But the same audit showed the home lean is EARNED — home picks 55.2% ATS
against away picks 46.2%, versus a 50.48% market baseline. So the fix is NOT
"pick away more". It is to find out whether an away-capable QUALITY signal
would make the away picks better. AP rank is the first honest candidate: it is
a real pre-game fact, it is sport-agnostic rather than home/away biased, and
it has never been tested in either direction.

SOR is deliberately NOT a candidate — it measured NULL as a predictor on
11,856 walk-forward games, so adding it would mean adding a known
non-predictor.

WHY THIS IS LEAK-FREE, which the stored column is not
ncaaf_game_context.{home,away}_ap_rank CANNOT be used. Until 2026-10-09 the
rankings puller had a duplicate dict key that dropped its lower date bound, so
it repeatedly patched the OLDEST 400 games with whatever the CURRENT week's
poll was — contaminating early-season rows with later rankings. Regressing
those on outcomes would read the future.

Instead this pulls the AP poll from CFBD PER (season, week) and joins it to
games of that same week. The week-N poll is published after week N-1 plays and
before week N plays, so it is exactly the information available pre-game.
Nothing is fitted; these are bucketed measurements with 2SE bands.

THE QUESTION, in the form that matters
Not "do ranked teams win" — they obviously do, and the market knows. The
question is whether the better-ranked team COVERS, i.e. whether the poll
carries information the closing line has not already priced. And specifically:
when the AWAY team is the better-ranked one, does backing it beat 52.38%?

WRITES NOTHING.

CLI
    python test_ap_rank_signal.py
    python test_ap_rank_signal.py --since 2022 --min-n 60
"""
from __future__ import annotations

import argparse
import collections
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
CFBD_BASE = 'https://api.collegefootballdata.com'
CFBD_KEY = os.environ.get('CFBD_API_KEY') or os.environ.get('CFBD_KEY')
#: NCAAF stores a NEGATIVE close_spread for a HOME favourite — verified on
#: 6,444 graded games (home wins 78.9% when spread<0, correlation -0.723,
#: spread_result agrees with margin+spread>0 on 99.8%).
HOME_FAV_NEG = True


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _norm(s):
    """Fold the spellings that differ between CFBD and our results table.

    CFBD says 'App State', 'UConn', "Hawai'i", 'San José State'; our results
    table uses those AND the long forms. Accent-folding plus punctuation
    stripping covers the real cases without fuzzy matching, which would merge
    'UMass' with 'UMass Dartmouth'.
    """
    s = ''.join(c for c in unicodedata.normalize('NFD', str(s or ''))
                if unicodedata.category(c) != 'Mn')
    for a, b in (("'", ''), ('.', ''), ('-', ' '), ('&', 'and')):
        s = s.replace(a, b)
    return ' '.join(s.lower().split())


ALIAS = {
    'app state': 'appalachian state', 'uconn': 'connecticut',
    'massachusetts': 'umass', 'southern california': 'usc',
    'miami fl': 'miami', 'miami oh': 'miami oh',
}


def key(s):
    n = _norm(s)
    return ALIAS.get(n, n)


_POLL_CACHE: dict = {}


def ap_poll(season, week):
    """{team_key: rank} for the AP poll published before that week's games."""
    ck = (int(season), int(week))
    if ck in _POLL_CACHE:
        return _POLL_CACHE[ck]
    out = {}
    if CFBD_KEY:
        try:
            r = requests.get(f'{CFBD_BASE}/rankings',
                             headers={'Authorization': f'Bearer {CFBD_KEY}'},
                             params={'year': season, 'week': week,
                                     'seasonType': 'regular'}, timeout=25)
            if r.status_code == 200:
                data = r.json() or []
                polls = (data[0] or {}).get('polls', []) if data else []
                ap = next((p for p in polls
                           if (p.get('poll') or '').lower() == 'ap top 25'),
                          None)
                for e in ((ap or {}).get('ranks') or []):
                    if e.get('school') and isinstance(e.get('rank'), int):
                        out[key(e['school'])] = int(e['rank'])
            else:
                print(f'    ! CFBD {season} wk{week}: {r.status_code}')
        except Exception as e:                              # noqa: BLE001
            print(f'    ! CFBD {season} wk{week} {type(e).__name__}')
        time.sleep(0.25)
    _POLL_CACHE[ck] = out
    return out


def report(label, rows, min_n, tests):
    if len(rows) < min_n:
        print(f'    {label:<44}n={len(rows)}  under the n>={min_n} floor')
        return
    hit = statistics.fmean(rows) * 100
    se = (0.5 / len(rows) ** 0.5) * 100
    edge = hit - BREAKEVEN
    tag = ('SIGNAL (>2SE over breakeven)' if edge > 2 * se else
           'suggestive' if edge > se else '')
    print(f'    {label:<44}{hit:6.2f}%  n={len(rows):<5} +/-{2 * se:4.2f}pp  '
          f'vs 52.38 {edge:+6.2f}  {tag}')
    tests.append((label, hit, len(rows), 2 * se, edge, tag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', type=int, default=2022)
    ap.add_argument('--min-n', type=int, default=60, dest='min_n')
    a = ap.parse_args()

    if not CFBD_KEY:
        print('! CFBD_API_KEY not set — cannot fetch point-in-time polls.')
        print('  Refusing to fall back on ncaaf_game_context.*_ap_rank: those')
        print('  rows were contaminated by the pre-2026-10-09 puller bug that')
        print('  repatched old games with the current week\'s poll.')
        return 1

    rows = _page('ncaaf_game_results',
                 {'select': 'game_date,season,week,season_type,home_team,'
                            'away_team,home_score,away_score,close_spread,'
                            'spread_result'})
    games = []
    for x in rows:
        if str(x.get('season_type') or 'regular') != 'regular':
            continue
        sr = str(x.get('spread_result') or '').lower()
        if sr not in ('home_covered', 'away_covered'):
            continue
        try:
            season, week = int(x['season']), int(x['week'])
        except (TypeError, ValueError, KeyError):
            continue
        if season < a.since:
            continue
        games.append({'season': season, 'week': week,
                      'home': key(x['home_team']), 'away': key(x['away_team']),
                      'home_cover': 1 if sr == 'home_covered' else 0,
                      'spread': x.get('close_spread')})
    weeks = sorted({(g['season'], g['week']) for g in games})
    print(f'=== AP RANK SIGNAL · {len(games)} graded games, '
          f'{len(weeks)} (season, week) polls to fetch')
    print('    Point-in-time: the week-N poll publishes after week N-1 plays')
    print('    and before week N plays, so it is pre-game information.')

    for season, week in weeks:
        ap_poll(season, week)
    got = sum(1 for k, v in _POLL_CACHE.items() if v)
    print(f'    polls retrieved: {got}/{len(weeks)}')

    # Attach ranks and bucket.
    both, one, neither = [], [], []
    away_better, home_better = [], []
    rk_unrk_away, rk_unrk_home = [], []
    gap_buckets = collections.defaultdict(list)
    matched = 0
    for g in games:
        poll = _POLL_CACHE.get((g['season'], g['week'])) or {}
        if not poll:
            continue
        hr, ar = poll.get(g['home']), poll.get(g['away'])
        matched += 1
        # "better ranked" = lower number; unranked = worse than any rank.
        if hr is not None and ar is not None:
            both.append(g['home_cover'])
            better_home = hr < ar
            (home_better if better_home else away_better).append(
                g['home_cover'] if better_home else 1 - g['home_cover'])
            gap_buckets[min(abs(hr - ar) // 6, 3)].append(
                g['home_cover'] if better_home else 1 - g['home_cover'])
        elif hr is not None or ar is not None:
            one.append(g['home_cover'])
            if hr is not None:               # home ranked, away unranked
                rk_unrk_home.append(g['home_cover'])
                home_better.append(g['home_cover'])
            else:                            # away ranked, home unranked
                rk_unrk_away.append(1 - g['home_cover'])
                away_better.append(1 - g['home_cover'])
        else:
            neither.append(g['home_cover'])
    print(f'    games with a poll: {matched}')

    tests = []
    print('\n' + '-' * 74)
    print('  DOES THE BETTER-RANKED TEAM COVER?')
    print('  Not "do ranked teams win" — the line already prices that. The')
    print('  question is whether the poll knows something the close does not.')
    print()
    report('better-ranked team covers (all)',
           home_better + away_better, a.min_n, tests)
    report('  ...when the BETTER team is HOME', home_better, a.min_n, tests)
    report('  ...when the BETTER team is AWAY  <-- the Iowa case',
           away_better, a.min_n, tests)
    print()
    report('ranked AWAY vs unranked HOME covers', rk_unrk_away, a.min_n, tests)
    report('ranked HOME vs unranked AWAY covers', rk_unrk_home, a.min_n, tests)
    print()
    print('  BY RANK GAP (both teams ranked):')
    for b in sorted(gap_buckets):
        lo, hi = b * 6, (b * 6 + 5) if b < 3 else 99
        report(f'  gap {lo}-{hi} places', gap_buckets[b], a.min_n, tests)

    print('\n' + '=' * 74)
    hits = [t for t in tests if t[5].startswith('SIGNAL')]
    print(f'  {len(tests)} buckets measured · {len(hits)} cleared breakeven '
          f'by >2SE · chance would give ~{len(tests) * 0.025:.1f}')
    if hits:
        print('\n  CLEARED:')
        for lbl, hit, n, band, edge, _t in sorted(hits, key=lambda z: -z[4]):
            print(f'    {lbl.strip():<44}{hit:6.2f}%  n={n:<5} '
                  f'edge {edge:+.2f}pp')
        print('\n  An AP-based signal is worth building for the buckets above.')
    else:
        print('\n  NOTHING cleared. The AP poll carries no information the')
        print('  closing line has not already priced — so an AP signal would')
        print('  not have helped the Iowa case, and the away path needs a')
        print('  different candidate. That is a real answer, not a null result')
        print('  from thin data: check the n column.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
