#!/usr/bin/env python3
"""Independently regrade public_receipts from PRIMARY data. Read-only by default.

== 2026-10-20 · WHY ==
ANDY: "I have been asking you if the sharp has been graded corretcly and you
have said yes yet i know for a fact it wasnt graded corretcly yesterday? ...
Since we know there is na issure i have no trust you have verified past dates
in the ahsrp corretcly, so lets do a full audti of the last 7 days ... also
want to audit all recipets as whole to ensure accuracy"

He was right and I was wrong. One leg of the 2026-10-19 Sharp (Freddy Peralta
Over 13.5 OUTS, PRIME) is stored LOSS while the prop threw 16 outs - a WIN.

THE POINT OF THIS SCRIPT is that it does NOT ask the grader whether the grader
was right. Every receipt is regraded from the primary record:

    ml / rl / spread / total  ->  the FINAL SCORE in <sport>_game_results
    prop                      ->  final_value in <sport>_pipeline_props

and the stored result is compared to that. A disagreement is reported with
both numbers so it can be checked by hand.

FIVE RULES THIS FOLLOWS, each one from a bug that has already cost us:

  * GRADE FROM THE PUBLISHED LABEL, not from close_spread. The label is what
    the user saw ("Tampa Bay Rays -1.5"), and MLB/NHL/NBA spread orientation
    in the results tables is UNVERIFIED - assuming a sign is how the fade
    analysis got NFL backwards. The label carries team + number, so no
    convention is needed.
  * JOIN GAMES BY TEAMS + DATE, never by game_id. The ids differ between the
    odds feed and the results tables, and in UFC an ordinal index was
    renumbered between runs.
  * A PROP NEEDS A UNIQUE MATCH on (player, prop_type, line, date). A player
    can hold two lines for one stat; grading the wrong one fabricates a
    result. Non-unique -> reported as unresolvable, never guessed.
  * 0 ROWS IS A WIRING ERROR, NOT A FINDING. Unresolvable receipts are
    counted and shown separately from disagreements, and the script says so
    loudly if the primary tables come back empty.
  * EVERY PAGE IS ORDERED. An unordered 1000-row page silently drops recent
    data - that is how I once reported "no mismatches" from a broken join.

USAGE
    python audit_receipt_grades.py --days 7
    python audit_receipt_grades.py --days 7 --surface sharp_card
    python audit_receipt_grades.py --all
    python audit_receipt_grades.py --all --show-unresolvable
"""
from __future__ import annotations
import argparse
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

RESULTS = {'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
           'NCAAF': 'ncaaf_game_results', 'NHL': 'nhl_game_results',
           'NBA': 'nba_game_results', 'NCAAB': 'ncaab_game_results'}
PROPS = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props',
         'NBA': 'nba_pipeline_props', 'NHL': 'nhl_pipeline_props'}
#: receipt label suffix -> prop_type stem
STAT_STEM = {'OUTS': 'outs', 'HA': 'ha', 'KS': 'ks', 'BB': 'bb', 'ER': 'er',
             'HITS': 'hits', 'TOTAL_BASES': 'total_bases', 'RBIS': 'rbis',
             'RUNS': 'runs', 'HR': 'hr'}
PROP_LABEL = re.compile(r'^(.+?)\s+(Over|Under)\s+([\d.]+)\s+(.+)$', re.I)
SIDE_LABEL = re.compile(r'^(.*?)\s*([+-]\d+(?:\.\d+)?)\s*$')
TERMINAL = ('WIN', 'LOSS', 'PUSH')


def page(tbl: str, params: dict, order: str) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=240,
                         params=dict(params, limit='1000', offset=str(off),
                                     order=order))
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def nz(s) -> str:
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ''.join(c for c in s.lower() if c.isalnum())


def split_matchup(m):
    """'A @ B' -> ('A','B'). Returns (None,None) when unparseable."""
    s = str(m or '')
    for sep in (' @ ', ' at ', '@'):
        if sep in s:
            a, b = s.split(sep, 1)
            return a.strip(), b.strip()
    return None, None


def build_game_index(sports, dates):
    """(sport, away_nz, home_nz, date) -> result row, plus a date-only bucket."""
    idx, by_date, counts = {}, defaultdict(list), {}
    for sp in sports:
        tbl = RESULTS.get(sp)
        if not tbl:
            continue
        sel = 'game_date,away_team,home_team,away_score,home_score'
        try:
            rows = page(tbl, {'select': sel}, 'game_date.desc')
        except RuntimeError as e:
            print(f'  !! {tbl}: {e}')
            continue
        counts[sp] = len(rows)
        for x in rows:
            if x.get('home_score') is None or x.get('away_score') is None:
                continue
            d = str(x.get('game_date'))[:10]
            idx[(sp, nz(x.get('away_team')), nz(x.get('home_team')), d)] = x
            by_date[(sp, d)].append(x)
    return idx, by_date, counts


def find_game(sport, matchup, date, idx, by_date):
    a, b = split_matchup(matchup)
    if a and b:
        g = idx.get((sport, nz(a), nz(b), date))
        if g:
            return g, 'exact'
        g = idx.get((sport, nz(b), nz(a), date))     # matchup stored reversed
        if g:
            return g, 'reversed'
        # substring tolerance: 'Tampa' vs 'Tampa Bay Rays'
        hits = [x for x in by_date.get((sport, date), [])
                if (nz(a) in nz(x['away_team']) or nz(x['away_team']) in nz(a))
                and (nz(b) in nz(x['home_team']) or nz(x['home_team']) in nz(b))]
        if len(hits) == 1:
            return hits[0], 'fuzzy'
    return None, 'no-game'


def team_in_game(team, g):
    """'home' / 'away' / None for a team name against a result row."""
    t = nz(team)
    if not t:
        return None
    for role in ('home', 'away'):
        name = nz(g[f'{role}_team'])
        if t == name or t in name or name in t:
            return role
    return None


def expected_for_game_market(rec, g):
    """(expected, why) from the FINAL SCORE and the published label."""
    hs, as_ = f(g['home_score']), f(g['away_score'])
    mkt = infer_market(rec)
    label = clean_label(rec.get('pick_label'))
    lab = label.lower()

    if mkt in ('total', 'ou'):
        line = f(rec.get('pick_line'))
        if line is None:
            m = re.search(r'([\d.]+)', label)
            line = f(m.group(1)) if m else None
        if line is None:
            return None, 'no total line on the receipt'
        want = 'OVER' if 'over' in lab else ('UNDER' if 'under' in lab else None)
        if want is None:
            return None, 'over/under not stated in the label'
        tot = hs + as_
        if abs(tot - line) < 1e-9:
            return 'PUSH', f'total {tot:g} = line {line:g}'
        got = 'OVER' if tot > line else 'UNDER'
        return ('WIN' if got == want else 'LOSS'), f'total {tot:g} vs {want} {line:g}'

    if mkt in ('ml', 'moneyline'):
        team = re.sub(r'\b(ml|moneyline)\b', '', label, flags=re.I).strip()
        role = team_in_game(team, g) or (
            'home' if nz(rec.get('pick_side')) == 'home' else
            'away' if nz(rec.get('pick_side')) == 'away' else None)
        if role is None:
            return None, f'cannot place "{team}" in {g["away_team"]} @ {g["home_team"]}'
        if abs(hs - as_) < 1e-9:
            return 'PUSH', 'tie'
        won = (hs > as_) if role == 'home' else (as_ > hs)
        return ('WIN' if won else 'LOSS'), f'{role} {"won" if won else "lost"} {as_:g}-{hs:g}'

    if mkt in ('rl', 'spread', 'ats', 'puckline'):
        m = SIDE_LABEL.match(label)
        team, line = (m.group(1).strip(), f(m.group(2))) if m else (label, f(rec.get('pick_line')))
        if line is None:
            return None, 'no handicap in the label or on the receipt'
        role = team_in_game(team, g)
        if role is None:
            return None, f'cannot place "{team}" in {g["away_team"]} @ {g["home_team"]}'
        margin = (hs - as_) if role == 'home' else (as_ - hs)
        eff = margin + line
        if abs(eff) < 1e-9:
            return 'PUSH', f'{role} margin {margin:+g} with {line:+g} = push'
        return ('WIN' if eff > 0 else 'LOSS'), \
               f'{role} margin {margin:+g} with {line:+g} -> {eff:+g}'

    return None, f'market {mkt!r} not a game market'


def build_prop_index(sports):
    idx, counts = defaultdict(list), {}
    for sp in sports:
        tbl = PROPS.get(sp)
        if not tbl:
            continue
        try:
            rows = page(tbl, {'select': 'player_name,prop_type,prop_line,'
                                        'game_date,result,final_value'},
                        'game_date.desc')
        except RuntimeError as e:
            print(f'  !! {tbl}: {e}')
            continue
        counts[sp] = len(rows)
        for x in rows:
            idx[(sp, str(x.get('game_date'))[:10], nz(x.get('player_name')),
                 str(x.get('prop_type') or '').lower(),
                 f(x.get('prop_line')))].append(x)
    return idx, counts


#: labels frequently carry the price: "... Over 45.5 reception_yds over @ -114"
_ODDS_SUFFIX = re.compile(r'\s*@\s*[+-]?\d+\s*$')
#: ... and sometimes an unpriced placeholder
_ODDS_PLACEHOLDER = re.compile(r'\s*@\s*\?+\s*$')


def clean_label(v) -> str:
    s = str(v or '').strip()
    for rx in (_ODDS_PLACEHOLDER, _ODDS_SUFFIX):
        s = rx.sub('', s).strip()
    return s


def infer_market(rec) -> str:
    """The real market, when the `market` column holds a SURFACE name.

    214 dotd and 197 potd receipts store market='dotd'/'potd' - the surface,
    not the bet - so a grader keyed on market cannot place them. The label
    still says what the bet was, so recover it rather than skipping.
    """
    mkt = str(rec.get('market') or '').lower()
    if mkt in ('ml', 'moneyline', 'rl', 'spread', 'ats', 'puckline',
               'total', 'ou', 'prop'):
        return mkt
    label = clean_label(rec.get('pick_label'))
    if rec.get('player_name') or rec.get('prop_type') or PROP_LABEL.match(label):
        return 'prop'
    low = label.lower()
    if 'over' in low or 'under' in low:
        return 'total'
    if SIDE_LABEL.match(label):
        return 'spread'
    if re.search(r'ml|moneyline', low):
        return 'ml'
    return mkt


def expected_for_prop(rec, pidx):
    label = clean_label(rec.get('pick_label'))
    m = PROP_LABEL.match(label)
    if not m:
        return None, 'prop label unparseable', None
    player, direction, line, stat = (m.group(1).strip(), m.group(2).lower(),
                                     f(m.group(3)), m.group(4).strip().upper())
    # Some labels bake the direction into the stat ("Ben Williamson Under 0.5
    # hits_under"), so strip a trailing _OVER/_UNDER before mapping.
    #
    # ══ 2026-10-20 · WHEN THE TWO DIRECTIONS DISAGREE, REFUSE ══
    # My first pass told this to trust the suffix over the leading word, and
    # that MANUFACTURED 317 "mis-grades" on prop_jerry - because labels like
    #
    #     Blake Snell OVER 3.5 ha_under @ -110
    #     Robert Gasser UNDER 2.5 ha_over @ 100
    #
    # state one side in the word the user reads and the OPPOSITE in the
    # prop_type. Which one was published is a RECEIPTS question, not a data
    # question, so grading either way asserts something unproven - the same
    # reason grade_stranded_receipts refuses a receipt whose displayed line
    # disagrees with its source. These are reported as a conflict instead,
    # which is a real integrity defect in its own right.
    stat_key = stat.replace(' ', '_')
    m_dir = re.search(r'_(OVER|UNDER)$', stat_key)
    if m_dir:
        suffix_dir = m_dir.group(1).lower()
        stat_key = stat_key[:m_dir.start()]
        if suffix_dir != direction:
            return (None, f'CONFLICT: label says {direction.upper()} but '
                          f'prop_type says {suffix_dir.upper()}', None)
        direction = suffix_dir
    stem = STAT_STEM.get(stat_key)
    if not stem:
        return None, f'stat {stat_key!r} not mapped', None
    sport = str(rec.get('sport') or 'MLB').upper()
    date = str(rec.get('game_date'))[:10]
    rows = pidx.get((sport, date, nz(player), f'{stem}_{direction}', line)) or []
    if len(rows) != 1:
        return None, f'{len(rows)} prop rows matched (need exactly 1)', None
    src = rows[0]
    fv = f(src.get('final_value'))
    if fv is None:
        return None, 'source prop has no final_value yet', src
    if abs(fv - line) < 1e-9:
        return 'PUSH', f'final {fv:g} = line {line:g}', src
    got_over = fv > line
    want_over = direction == 'over'
    return ('WIN' if got_over == want_over else 'LOSS'), \
           f'final {fv:g} vs {direction} {line:g}', src


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--all', action='store_true', help='every receipt ever')
    ap.add_argument('--surface')
    ap.add_argument('--show-unresolvable', action='store_true')
    ap.add_argument('--fix', action='store_true',
                    help='PATCH receipts whose stored result disagrees with '
                         'the primary record. Conflicts and unresolvables '
                         'are never touched.')
    a = ap.parse_args()

    hi = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    params = {'select': 'id,surface,sport,market,game_date,matchup,pick_label,'
                        'pick_side,pick_line,pick_odds,tier,result,graded_at,'
                        'player_name,prop_type'}
    if not a.all:
        params['game_date'] = f'gte.{hi - timedelta(days=a.days)}'
    if a.surface:
        params['surface'] = f'eq.{a.surface}'
    recs = page('public_receipts', params, 'game_date.desc')
    scope = 'ALL TIME' if a.all else f'last {a.days} days'
    print(f'receipts in scope ({scope}'
          f'{", surface=" + a.surface if a.surface else ""}): {len(recs)}')
    graded = [r for r in recs if str(r.get('result') or '').upper() in TERMINAL]
    print(f'  carrying a terminal grade (WIN/LOSS/PUSH): {len(graded)}')
    if not graded:
        print('  nothing graded in scope - stopping rather than reporting "clean"')
        return 0

    sports = sorted({str(r.get('sport') or '').upper() for r in graded})
    gidx, by_date, gcounts = build_game_index(sports, None)
    pidx, pcounts = build_prop_index(sports)
    print(f'  primary game rows: {gcounts}')
    print(f'  primary prop rows: {pcounts}')
    if not gidx and not pidx:
        print('  !! NO primary data loaded - this is a WIRING ERROR, not a clean audit')
        return 1

    agree, disagree, unresolved = 0, [], []
    for rec in graded:
        sport = str(rec.get('sport') or '').upper()
        mkt = infer_market(rec)
        stored = str(rec.get('result') or '').upper()
        if mkt == 'prop':
            exp, why, src = expected_for_prop(rec, pidx)
            ev = why
        else:
            g, how = find_game(sport, rec.get('matchup'), str(rec.get('game_date'))[:10],
                               gidx, by_date)
            if not g:
                unresolved.append((rec, f'no result row ({how})'))
                continue
            exp, why = expected_for_game_market(rec, g)
            ev = f'{why} [{how}]' if why else how
        if exp is None:
            unresolved.append((rec, ev))
            continue
        if exp == stored:
            agree += 1
        else:
            disagree.append((rec, exp, ev))

    print(f'\n{"="*78}')
    print(f'VERDICT  agree {agree} · DISAGREE {len(disagree)} · '
          f'unresolvable {len(unresolved)}  (of {len(graded)} graded)')
    print('='*78)

    if disagree:
        print('\nDISAGREEMENTS — stored result vs the primary record:')
        for rec, exp, ev in sorted(disagree, key=lambda t: str(t[0].get('game_date')),
                                   reverse=True):
            print(f'\n  {str(rec["game_date"])[:10]}  {rec.get("surface")}/'
                  f'{rec.get("sport")}/{rec.get("market")}  id={rec["id"]}')
            print(f'     {str(rec.get("pick_label"))[:62]}')
            print(f'     stored {str(rec.get("result")):<5}  SHOULD BE {exp:<5}  '
                  f'tier={rec.get("tier")} odds={rec.get("pick_odds")}')
            print(f'     evidence: {ev}')
            print(f'     graded_at {str(rec.get("graded_at"))[:19]}')
        by_surface = Counter(f'{r.get("surface")}/{r.get("sport")}'
                             for r, _e, _v in disagree)
        print(f'\n  by surface: {dict(by_surface)}')

        # Record impact, per surface, BEFORE anything is written - a grade
        # correction moves a published number, so it has to be visible
        # whether or not --fix is passed.
        delta = defaultdict(lambda: [0, 0])
        for rec, exp, _ev in disagree:
            k = str(rec.get('surface'))
            was = str(rec.get('result') or '').upper()
            if was == 'WIN':
                delta[k][0] -= 1
            elif was == 'LOSS':
                delta[k][1] -= 1
            if exp == 'WIN':
                delta[k][0] += 1
            elif exp == 'LOSS':
                delta[k][1] += 1
        print('')
        print('  RECORD IMPACT if corrected (wins, losses):')
        for k in sorted(delta):
            w, l = delta[k]
            print('    %-16s %+d W  %+d L' % (k, w, l))

        if a.fix:
            print('')
            print('  applying corrections...')
            hw = dict(H, **{'Content-Type': 'application/json',
                            'Prefer': 'return=minimal'})
            ok = 0
            for rec, exp, _ev in disagree:
                pr = requests.patch(
                    SB + '/rest/v1/public_receipts?id=eq.' + str(rec['id']),
                    headers=hw,
                    json={'result': exp,
                          'graded_at': datetime.now(timezone.utc).isoformat()},
                    timeout=30)
                if pr.status_code in (200, 204):
                    ok += 1
                else:
                    print('    x id=%s: %s %s' % (rec['id'], pr.status_code,
                                                  (pr.text or '')[:120]))
            print('  patched %d/%d' % (ok, len(disagree)))
            # A 2xx is not proof of a write. Re-read every one.
            still = 0
            for rec, exp, _ev in disagree:
                rr = requests.get(SB + '/rest/v1/public_receipts', headers=H,
                                  timeout=30,
                                  params={'select': 'id,result',
                                          'id': 'eq.' + str(rec['id'])})
                rows = rr.json() if rr.status_code in (200, 206) else []
                got = str((rows or [{}])[0].get('result')).upper()
                if got != exp:
                    still += 1
            print('  verified: %d now correct, %d still wrong'
                  % (len(disagree) - still, still))
    else:
        print('\nNo disagreement found on any resolvable receipt in scope.')

    if unresolved:
        reasons = Counter(why for _r, why in unresolved)
        print(f'\nUNRESOLVABLE ({len(unresolved)}) — cannot be checked either way, '
              'reported rather than counted as clean:')
        for why, n in reasons.most_common(12):
            print(f'    {n:4d}  {why}')
        if a.show_unresolvable:
            for rec, why in unresolved[:60]:
                print(f'      {str(rec["game_date"])[:10]} {rec.get("surface")}/'
                      f'{rec.get("market")} id={rec["id"]} '
                      f'{str(rec.get("pick_label"))[:44]} :: {why}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
