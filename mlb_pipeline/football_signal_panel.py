"""Do money flow, SOR and stat differentials separate covers? — NFL + NCAAF.

ANDY 2026-10-09: "i do think the money flow data should ahve distiguishable
patterns along with SOR and stat differentials." And the reason it matters:

    2026-10-08  TB 24 @ DAL 16   TB +8.5   WON by 16.5   tier=COVERAGE conv=45
    2026-09-20  CLE 23 @ TB 19   TB ML     LOST          tier=PRIME    conv=72

The side selection found a 16.5-point winner and buried it at conviction 45,
while its highest-conviction pick lost. Conviction correlates with winning at
r~+0.03 in football. So the problem is NOT that the engine cannot pick a side
— it is that the ORDERING is broken. This script looks for something that can
replace that ordering.

THE QUESTION, stated so it can fail
For each lens: when it points at a side, does that side cover more than the
52.38% a -110 bet needs? And — the part that would actually rebuild conviction
— when two INDEPENDENT lenses agree, does the cover rate rise above either
alone? That is what a conviction scale should be: agreement between things
that do not already know each other.

WHY INDEPENDENCE IS THE WHOLE GAME HERE
The NCAAF lens panel failed because five of its six lenses correlated at
r=+0.76..+0.97 — all of them asking "how good is this offence" in different
costumes, so stacking them added confidence without adding information. Money
flow is the first candidate that is structurally independent: it measures what
OTHER PEOPLE are betting, which no team-quality rating can express. The script
reports the correlation between lenses, and a pair that correlates above ~0.85
is reported as one lens wearing two hats, not as agreement.

THREE LENSES, AND EACH HAS A DIFFERENT HONEST SAMPLE
This is the part that cannot be papered over. The lenses are limited by
different data, so forcing all three into their common window would throw away
most of the evidence and still leave the three-way cell thin:

  SOR gap          RECONSTRUCTED point-in-time, so the sample is every graded
                   game. Refit per week on games STRICTLY BEFORE that week,
                   reusing fit_srs from compute_margin_strength — the current
                   `sor` column is an upserted snapshot and regressing it on
                   past outcomes reads the outcome
                   (project_rolling_stats_leak_trap_929).
  money flow       cleatz_signals, 2026-09-10 onward: ~1,458 NCAAF and ~488
                   NFL game-days. Genuinely point-in-time — it is scraped
                   before kickoff and stamped with snapshot_date.
  stat differential team_stats_rolling_history, 2026-09-26 onward, 10-11
                   snapshot dates. THIS IS THE BINDING CONSTRAINT. Only the
                   dated history may be used; the live table is a current
                   snapshot and would leak.

So each lens is reported at its own maximum n, pairs at their intersection,
and the three-way cell is printed with its n and an explicit warning rather
than quietly treated as a result.

NOTHING IS FITTED. These are bucketed measurements with two-standard-error
bands, which is why no train/test split is needed for the reporting itself —
there is no parameter to overfit. The only fitted object is the SOR rating,
and that is refit walk-forward on prior games only.

MULTIPLE COMPARISONS ARE COUNTED. The script tallies its own tests and says
how many 2SE "hits" pure chance would produce, because a panel of this shape
will always produce a few.

A KNOWN DATA DEFECT, handled rather than ignored
cleatz money flow carries `sharp_handle_pct == 100.0` on about 20% of NCAAF
rows and `divergence == 0` on ~16% — a 100% handle share is not a real market
(project_cz_splits_source_is_broken_1007). Those rows are EXCLUDED and counted
separately, never averaged in.

WRITES NOTHING.

CLI
    python football_signal_panel.py --sport NCAAF
    python football_signal_panel.py --sport NFL
    python football_signal_panel.py --sport NCAAF --min-n 40
"""
from __future__ import annotations

import argparse
import collections
import math
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML
from compute_margin_strength import SPORT_CFG, fit_srs

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
#: NCAAF/MLB store a NEGATIVE close_spread for a HOME favourite; NFL stores a
#: POSITIVE one. Both verified empirically against who actually won — getting
#: this backwards silently inverts every single cover in the panel.
HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': +1.0}
#: Lenses whose correlation with each other exceeds this are the SAME lens.
INDEPENDENCE_MAX_R = 0.85


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(s):
    """Fold the spellings that differ between our own tables.

    ncaaf_game_results and ncaaf_game_context disagree on five programmes
    (App State/Appalachian State, UConn/Connecticut, Hawai'i/Hawaii,
    San José State/San Jose State, Massachusetts/UMass) and cleatz uses its
    own forms again. Exact-match aliasing only — 'UMass Dartmouth' and
    'Central Connecticut' are DIFFERENT schools in the same tables.
    """
    s = str(s or '').strip()
    for a, b in (('é', 'e'), ('è', 'e'), ('í', 'i'), ('ó', 'o'), ("'", ''),
                 ('.', ''), ('-', ' ')):
        s = s.replace(a, b)
    return ' '.join(s.lower().split())


ALIAS = {
    'app state': 'appalachian state', 'uconn': 'connecticut',
    'hawaii': 'hawaii', 'san jose state': 'san jose state',
    'massachusetts': 'umass',
}

#: NFL lives in a different name space again. nfl_game_results uses bare codes
#: ('TB', 'GB') while cleatz emits "ABBREV Mascot" ('TEN Titans', 'NY Jets').
#: Leading-token extraction FAILS on the shared-city teams — 'NY Jets' would
#: give 'NY' where results want 'NYJ' — so this maps through the mascot, which
#: is unique per franchise, using the nfl_team_aliases table rather than a
#: hand-written list that would drift.
_NFL_KEY: dict[str, str] = {}


def load_nfl_aliases():
    if _NFL_KEY:
        return
    for r in _page('nfl_team_aliases',
                   {'select': 'canonical_name,full_name,mascot,'
                              'odds_api_name,espn_name,alt_names'}):
        canon = str(r.get('canonical_name') or '').strip()
        if not canon:
            continue
        forms = [canon, r.get('full_name'), r.get('mascot'),
                 r.get('odds_api_name'), r.get('espn_name')]
        forms += list(r.get('alt_names') or [])
        for f in forms:
            if f:
                _NFL_KEY[_norm(f)] = canon.upper()


def key(s, sport=None):
    n = _norm(s)
    if sport == 'NFL':
        if n in _NFL_KEY:
            return _NFL_KEY[n]
        # Fall back to the MASCOT (last word), which alt_names covers for
        # every franchise — 'NY Jets' -> 'jets' -> 'NYJ'.
        last = n.split()[-1] if n.split() else n
        if last in _NFL_KEY:
            return _NFL_KEY[last]
        return n.upper()
    return ALIAS.get(n, n)


#: Net-efficiency differential inputs per sport. NFL splits EPA into pass and
#: rush rather than carrying a single off_epa_per_play, so asking for the
#: NCAAF key there silently matched ZERO history rows and the lens reported
#: n=0 — the explicit-SELECT-is-a-silent-blank trap in another costume.
STAT_KEYS = {
    'NCAAF': {'off': ('off_epa_per_play',), 'def': ('def_epa_per_play',)},
    'NFL':   {'off': ('off_pass_epa', 'off_rush_epa'),
              'def': ('def_pass_epa', 'def_rush_epa')},
}


# ──────────────────────────────────────────────────────────────────────────
def load_outcomes(sport):
    """Graded games with a market line, plus the cover outcome."""
    rows = _page(RESULTS[sport],
                 {'select': 'game_id,game_date,home_team,away_team,home_score,'
                            'away_score,close_spread,spread_result,season'})
    out = []
    for x in rows:
        res = str(x.get('spread_result') or '').lower()
        cs = _f(x.get('close_spread'))
        hs, as_ = _f(x.get('home_score')), _f(x.get('away_score'))
        gd = str(x.get('game_date') or '')[:10]
        if res not in ('home_covered', 'away_covered') or cs is None or not gd:
            continue
        if hs is None or as_ is None:
            continue
        out.append({
            'date': gd, 'home': key(x['home_team'], sport),
            'away': key(x['away_team'], sport),
            'home_raw': x['home_team'], 'away_raw': x['away_team'],
            'margin': hs - as_,
            # Market's expected home margin, sign-normalised per sport.
            'mkt_home_margin': cs * HOME_FAV_SIGN[sport],
            'home_covered': 1 if res == 'home_covered' else 0,
            'season': x.get('season'),
            'game_id': str(x.get('game_id') or ''),
        })
    out.sort(key=lambda g: g['date'])
    return out


def pit_sor(sport, games):
    """Point-in-time SOR per game, refit walk-forward on prior games only.

    Refits once per distinct game DATE using only games strictly earlier, so
    no rating can see the game it is used to predict. Refitting per date
    rather than per game keeps this to a few hundred solves instead of
    thousands while preserving the no-leak property exactly.
    """
    cfg = SPORT_CFG[sport]
    dates = sorted({g['date'] for g in games})
    # Group prior games per season so a rating is not built across years.
    by_season = collections.defaultdict(list)
    for g in games:
        by_season[str(g.get('season'))].append(g)
    ratings_at = {}
    for season, gs in by_season.items():
        gs.sort(key=lambda g: g['date'])
        sdates = sorted({g['date'] for g in gs})
        for d in sdates:
            prior = [{'home': g['home'], 'away': g['away'],
                      'margin': g['margin'], 'neutral': False}
                     for g in gs if g['date'] < d]
            # A rating off fewer than ~20 games is noise in both leagues.
            ratings_at[(season, d)] = fit_srs(prior, cfg) if len(prior) >= 20 \
                else {}
    hfa = cfg['hfa']
    n_ok = 0
    for g in games:
        r = ratings_at.get((str(g.get('season')), g['date'])) or {}
        hr, ar = r.get(g['home']), r.get(g['away'])
        if hr is None or ar is None:
            g['sor_edge'] = None
            continue
        # SOR-implied home margin minus what the market already prices.
        g['sor_edge'] = (hr - ar + hfa) - g['mkt_home_margin']
        n_ok += 1
    print(f'    point-in-time SOR computed for {n_ok}/{len(games)} games '
          f'({len(ratings_at)} walk-forward refits)')
    return games


#: "HOME money 18%/bets 12%" — the shape BOTH oddscrowd and scoresandodds use
#: inside external_picks.raw_text. Parsed rather than re-scraped because the
#: history is already sitting in the table: these two go back to 2026-07-28
#: and 2026-08-26 against cleatz's 2026-09-10, and they are INDEPENDENT
#: sources, so agreement between them is itself evidence rather than one feed
#: counted twice. Verified 0 parse failures across 12,418 rows.
_MF_TEXT = __import__('re').compile(
    r'(HOME|AWAY|OVER|UNDER)\s+money\s+(\d{1,3})%\s*/\s*bets\s+(\d{1,3})%',
    __import__('re').I)


def money_flow_from_externals(sport):
    """-> {(date, side_of_home): divergence} per source, from raw_text.

    Divergence is money% - bets% on the HOME side. Positive means money is
    heavier than ticket count on home, which is the classic sharp-money
    reading. The two sides are complementary, so one number per game suffices.
    """
    rows = _page('external_picks',
                 {'select': 'sport,source,surface,game_date,game_id,raw_text',
                  'sport': f'eq.{sport}', 'surface': 'eq.rl',
                  'source': 'in.(oddscrowd,scoresandodds)'})
    per_source = collections.defaultdict(dict)
    failed = collections.Counter()
    for x in rows:
        src = str(x['source'])
        ms = _MF_TEXT.findall(str(x.get('raw_text') or ''))
        sides = {s.upper(): (int(m), int(b)) for s, m, b in ms}
        if 'HOME' not in sides or 'AWAY' not in sides:
            failed[src] += 1
            continue
        hm, hb = sides['HOME']
        div = hm - hb                 # home money minus home tickets
        gd = str(x.get('game_date') or '')[:10]
        if not gd:
            continue
        gid = str(x.get('game_id') or '')
        if not gid:
            continue
        # Keyed on the game_id SLUG, not game_date: external_picks
        # carries rows whose game_date disagrees with the date inside
        # their own game_id (seen: game_id ...20260904... with
        # game_date 2026-08-28). The slug is canonical in both tables.
        per_source[src][gid] = div
    for src, d in per_source.items():
        print(f'      {src}: {len(d)} parsed rl readings'
              f'{f" ({failed[src]} unparsed)" if failed[src] else ""}')
    return per_source


def attach_money_flow(sport, games):
    """cleatz handle-vs-bets divergence, pointed at a side."""
    cz = _page('cleatz_signals',
               {'select': 'snapshot_date,sport,away_team,home_team,market,'
                          'sharp_side_norm,sharp_handle_pct,sharp_bets_pct,'
                          'divergence',
                'sport': f'eq.{sport}', 'market': 'eq.rl'})
    idx, degenerate = {}, 0
    for x in cz:
        hp = _f(x.get('sharp_handle_pct'))
        dv = _f(x.get('divergence'))
        side = str(x.get('sharp_side_norm') or '').lower()
        if dv is None or side not in ('home', 'away'):
            continue
        # A 100% handle share is not a real market, and zero divergence
        # carries no direction. Both are known defects of this source.
        if hp is not None and hp >= 100.0:
            degenerate += 1
            continue
        if dv == 0:
            degenerate += 1
            continue
        # ══ 2026-10-09 · snapshot_date IS THE SCRAPE DATE, NOT THE GAME DATE
        # cleatz scrapes UPCOMING games every day (~50 NCAAF rows/day), so a
        # Saturday game appears in the Monday..Friday snapshots too. The first
        # version of this join required snapshot_date == game_date, which
        # matched only games played ON a scrape day — it joined 244 of 5,550
        # games instead of ~1,400, and the survivors were biased toward
        # midweek games (MACtion) rather than being a random subset. Index by
        # teams and pick the latest snapshot at or before kickoff instead.
        idx.setdefault((key(x['away_team'], sport),
                        key(x['home_team'], sport)), []).append(
            (str(x['snapshot_date'])[:10], side, abs(dv)))
    for v in idx.values():
        v.sort()
    import datetime as _dt

    def _days(a, b):
        try:
            return (_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days
        except ValueError:
            return 999

    hit = 0
    for g in games:
        snaps = idx.get((g['away'], g['home'])) or []
        best = None
        for sd, side, dv in snaps:
            # At or before kickoff only — a scrape on game day still precedes
            # it, since cleatz lists games that have not started. And within
            # 7 days, so a rematch later in the season cannot borrow an old
            # snapshot.
            if sd <= g['date'] and 0 <= _days(g['date'], sd) <= 7:
                best = (sd, side, dv)      # list is sorted, so last wins
        if not best:
            g['mf_side'] = g['mf_div'] = None
            continue
        g['mf_side'] = best[1]
        g['mf_div'] = best[2]
        g['mf_lag'] = _days(g['date'], best[0])
        hit += 1
    print(f'    money flow joined on {hit}/{len(games)} games '
          f'({len(cz)} cleatz rows, {degenerate} degenerate excluded, '
          f'{len(idx)} team-pairs indexed)')
    lags = [g['mf_lag'] for g in games if g.get('mf_lag') is not None]
    if lags:
        print(f'      snapshot lag before kickoff: median '
              f'{statistics.median(lags):.0f}d, max {max(lags)}d')

    # ── TWO MORE INDEPENDENT SOURCES, parsed out of external_picks ──────
    ext = money_flow_from_externals(sport)
    for src, idx2 in ext.items():
        hit2 = 0
        for g in games:
            d = idx2.get(g.get('game_id'))
            if d is None:
                continue
            g[f'mf_{src}_div'] = abs(d)
            g[f'mf_{src}_side'] = 'home' if d > 0 else 'away'
            hit2 += 1
        print(f'      {src} joined on {hit2} games')
    return games


def attach_stat_diff(sport, games):
    """Offence-minus-defence differential from DATED history snapshots only."""
    sk = STAT_KEYS[sport]
    wanted = list(sk['off']) + list(sk['def'])
    h = _page('team_stats_rolling_history',
              {'select': 'team,stat_key,raw_value,snapshot_date',
               'sport': f'eq.{sport}',
               'stat_key': f'in.({",".join(wanted)})'})
    byk = collections.defaultdict(lambda: collections.defaultdict(dict))
    for x in h:
        v = _f(x.get('raw_value'))
        if v is None:
            continue
        byk[str(x['stat_key'])][str(x['snapshot_date'])[:10]][
            key(x['team'], sport)] = v
    kdates = {k: sorted(d) for k, d in byk.items()}

    def latest_before(stat, team, gd):
        for d in reversed(kdates.get(stat, [])):
            if d < gd:
                v = byk[stat][d].get(team)
                if v is not None:
                    return v
        return None

    hit = 0
    for g in games:
        def tot(keys, team):
            vals = [latest_before(k, team, g['date']) for k in keys]
            return None if any(v is None for v in vals) else sum(vals)
        ho = tot(sk['off'], g['home']); hd = tot(sk['def'], g['home'])
        ao = tot(sk['off'], g['away']); ad = tot(sk['def'], g['away'])
        if None in (ho, hd, ao, ad):
            g['stat_edge'] = None
            continue
        # Net efficiency: own offence minus own defence allowed, home vs away.
        # def_epa_per_play is "points allowed per play", so LOWER is better
        # and it is subtracted.
        g['stat_edge'] = (ho - hd) - (ao - ad)
        hit += 1
    print(f'    stat differential joined on {hit}/{len(games)} games '
          f'({len(kdates.get(sk["off"][0], []))} history dates)')
    return games


# ──────────────────────────────────────────────────────────────────────────
def cover_rate(rows):
    """Cover rate of the SIGNALLED side, with a 2SE band."""
    if not rows:
        return None
    hit = statistics.fmean([r['signal_covered'] for r in rows]) * 100
    se = (0.5 / len(rows) ** 0.5) * 100
    return hit, len(rows), 2 * se


def report(label, rows, min_n, tests):
    r = cover_rate(rows)
    if r is None or r[1] < min_n:
        print(f'    {label:<40}n={len(rows)}  below the n>={min_n} floor')
        return None
    hit, n, band = r
    edge = hit - BREAKEVEN
    verdict = ('SIGNAL (>2SE over breakeven)' if edge > band else
               'suggestive' if edge > band / 2 else
               'nothing')
    print(f'    {label:<40}{hit:6.2f}%  n={n:<5} +/-{band:4.2f}pp  '
          f'edge vs 52.38 {edge:+6.2f}  {verdict}')
    tests.append((label, hit, n, band, edge, verdict))
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--min-n', type=int, default=60, dest='min_n')
    a = ap.parse_args()
    sport = a.sport

    if sport == 'NFL':
        load_nfl_aliases()
        print(f'    loaded {len(_NFL_KEY)} NFL name forms from nfl_team_aliases')
    print(f'=== {sport} SIGNAL PANEL · money flow x SOR x stat differentials')
    games = load_outcomes(sport)
    print(f'    {len(games)} graded games with a market line '
          f'({games[0]["date"]}..{games[-1]["date"]})')
    base = statistics.fmean([g['home_covered'] for g in games]) * 100
    print(f'    base home-cover rate {base:.2f}%  ·  breakeven {BREAKEVEN}%')
    print()
    games = pit_sor(sport, games)
    games = attach_money_flow(sport, games)
    games = attach_stat_diff(sport, games)

    # Each lens points at a side; score whether THAT side covered.
    def side_covered(g, side):
        return g['home_covered'] if side == 'home' else 1 - g['home_covered']

    for g in games:
        g['sor_side'] = (None if g.get('sor_edge') is None
                         else 'home' if g['sor_edge'] > 0 else 'away')
        g['stat_side'] = (None if g.get('stat_edge') is None
                          else 'home' if g['stat_edge'] > 0 else 'away')

    tests = []
    print('\n' + '-' * 74)
    print('  EACH LENS ALONE, at its own maximum sample')
    print('  "signalled side covered" — so 52.38% is the bar, not 50%.')

    def subset(pred, side_field, extra=None):
        out = []
        for g in games:
            s = g.get(side_field)
            if not s or not pred(g):
                continue
            if extra and not extra(g):
                continue
            gg = dict(g)
            gg['signal_covered'] = side_covered(g, s)
            out.append(gg)
        return out

    print()
    report('SOR disagrees with market (any size)',
           subset(lambda g: True, 'sor_side'), a.min_n, tests)
    for lo in (1.0, 3.0, 6.0):
        report(f'SOR edge >= {lo:.0f} pts',
               subset(lambda g, lo=lo: abs(g['sor_edge']) >= lo, 'sor_side'),
               a.min_n, tests)
    print()
    mf = [g for g in games if g.get('mf_side')]
    for lo in (0.0, 10.0, 20.0, 30.0):
        rows = []
        for g in mf:
            if g['mf_div'] < lo:
                continue
            gg = dict(g); gg['signal_covered'] = side_covered(g, g['mf_side'])
            rows.append(gg)
        report(f'MONEY FLOW side, divergence >= {lo:.0f}pp', rows,
               a.min_n, tests)
    print()
    report('STAT differential side',
           subset(lambda g: True, 'stat_side'), a.min_n, tests)

    # ── PER-SOURCE money flow, then CROSS-SOURCE CONSENSUS ──────────────
    # Three independent feeds. Testing them separately first is the whole
    # point: if one shows a gradient and the others do not, that is a
    # SCRAPER artefact, not a market signal. Only a direction that survives
    # in more than one feed is worth believing.
    print()
    print('-' * 74)
    print('  MONEY FLOW BY SOURCE — does the direction survive across feeds?')
    print('  A gradient in one feed only is a scraper artefact, not a signal.')
    print()
    SRCS = [('cleatz', 'mf_side', 'mf_div'),
            ('oddscrowd', 'mf_oddscrowd_side', 'mf_oddscrowd_div'),
            ('scoresandodds', 'mf_scoresandodds_side',
             'mf_scoresandodds_div')]
    for name, sf, df in SRCS:
        for lo in (0.0, 10.0, 20.0):
            rows = []
            for g in games:
                if not g.get(sf) or g.get(df) is None or g[df] < lo:
                    continue
                gg = dict(g); gg['signal_covered'] = side_covered(g, g[sf])
                rows.append(gg)
            report(f'{name} div >= {lo:.0f}pp', rows, a.min_n, tests)
        print()

    print('  CROSS-SOURCE CONSENSUS — independent feeds pointing the same way')
    print('  This is the conviction candidate: not one feed shouting louder,')
    print('  but separate feeds agreeing.')
    print()
    for lo in (0.0, 10.0):
        agree, split = [], 0
        for g in games:
            sides = [g.get(sf) for _n, sf, df in SRCS
                     if g.get(sf) and g.get(df) is not None and g[df] >= lo]
            if len(sides) < 2:
                continue
            if len(set(sides)) == 1:
                gg = dict(g); gg['signal_covered'] = side_covered(g, sides[0])
                agree.append(gg)
            else:
                split += 1
        report(f'>=2 sources AGREE, div >= {lo:.0f}pp', agree, a.min_n, tests)
        print(f'    {"(sources disagreed on)":<40}n={split}')

    # ── INDEPENDENCE ─────────────────────────────────────────────────────
    print('\n' + '-' * 74)
    print('  INDEPENDENCE — do these lenses actually know different things?')
    print('  A pair correlating above '
          f'{INDEPENDENCE_MAX_R} is ONE lens in two costumes, and their')
    print('  "agreement" would be circular rather than confirmatory.')
    pairs = (('sor_edge', 'stat_edge'), ('sor_edge', 'mf_signed'),
             ('stat_edge', 'mf_signed'))
    for g in games:
        g['mf_signed'] = (None if not g.get('mf_side')
                          else g['mf_div'] * (1 if g['mf_side'] == 'home'
                                              else -1))
    for x, y in pairs:
        both = [(g[x], g[y]) for g in games
                if g.get(x) is not None and g.get(y) is not None]
        if len(both) < 30:
            print(f'    {x} vs {y}: n={len(both)} too few to correlate')
            continue
        xs = [p[0] for p in both]; ys = [p[1] for p in both]
        try:
            r = statistics.correlation(xs, ys)
        except statistics.StatisticsError:
            r = float('nan')
        tag = ('SAME LENS' if abs(r) > INDEPENDENCE_MAX_R
               else 'independent')
        print(f'    {x:<12} vs {y:<12} r = {r:+.3f}  n={len(both)}  {tag}')

    # ── AGREEMENT: the thing that would rebuild conviction ────────────────
    print('\n' + '-' * 74)
    print('  AGREEMENT — the candidate conviction scale')
    print('  If two independent lenses pointing the SAME way covers more than')
    print('  either alone, that difference is what conviction should encode.')
    print()
    combos = (('SOR + money flow', 'sor_side', 'mf_side'),
              ('SOR + stat differential', 'sor_side', 'stat_side'),
              ('money flow + stat differential', 'mf_side', 'stat_side'))
    for label, f1, f2 in combos:
        agree, disagree = [], []
        for g in games:
            s1, s2 = g.get(f1), g.get(f2)
            if not s1 or not s2:
                continue
            gg = dict(g)
            if s1 == s2:
                gg['signal_covered'] = side_covered(g, s1)
                agree.append(gg)
            else:
                disagree.append(gg)
        ra = report(f'{label} AGREE', agree, a.min_n, tests)
        if disagree:
            print(f'    {"(they disagreed on)":<40}n={len(disagree)}')
        if ra is not None:
            print()

    # three-way
    tri = []
    for g in games:
        s = [g.get('sor_side'), g.get('mf_side'), g.get('stat_side')]
        if None in s or not all(s):
            continue
        if s[0] == s[1] == s[2]:
            gg = dict(g); gg['signal_covered'] = side_covered(g, s[0])
            tri.append(gg)
    print()
    r3 = cover_rate(tri)
    if r3:
        hit, n, band = r3
        print(f'    ALL THREE AGREE                        {hit:6.2f}%  '
              f'n={n:<5} +/-{band:4.2f}pp  edge {hit - BREAKEVEN:+.2f}')
        print(f'    ^ n={n}. The three-way cell is limited by the stat')
        print('      history window (2026-09-26 onward), so treat it as a')
        print('      direction to watch, NOT as a validated result.')
    else:
        print('    ALL THREE AGREE: no games where all three lenses exist.')

    # ── MULTIPLE COMPARISONS ─────────────────────────────────────────────
    print('\n' + '=' * 74)
    hits = [t for t in tests if t[5].startswith('SIGNAL')]
    print(f'  {len(tests)} tests run · {len(hits)} cleared breakeven by >2SE')
    exp = len(tests) * 0.025
    print(f'  Pure chance would produce about {exp:.1f} at a one-sided 2SE '
          f'bar.')
    if hits:
        print('\n  CLEARED:')
        for lbl, hit, n, band, edge, _v in sorted(hits, key=lambda z: -z[4]):
            print(f'    {lbl:<40}{hit:6.2f}%  n={n:<5} edge {edge:+.2f}pp')
        if len(hits) <= exp:
            print('\n  ...but that count is at or below the chance '
                  'expectation, so')
            print('  none of it should be acted on yet.')
    else:
        print('\n  NOTHING cleared. On these samples that is a real answer for')
        print('  money flow (n~1,400 NCAAF) and for SOR, and an open question')
        print('  for anything gated on the stat history window.')
    print('\n  Reminder: conviction currently correlates with winning at')
    print('  r~+0.03 in football. Any lens above is only worth adopting if it')
    print('  beats THAT, which is a low bar — and still has to clear 52.38%.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
