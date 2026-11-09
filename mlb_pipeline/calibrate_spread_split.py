#!/usr/bin/env python3
"""Calibrate the published favourite/dog spread split to the market's own rate.

== 2026-11-08 . ANDY'S DIRECTIVE =====================================
"we know the market history i.e how often dawg spreads cover or favorites
cover across the season in any given sport. we should direct the engine to
essentially try to match that via our predictions so if our engine is
picking 75 percent spread favourite but the market history says its like 54
percent then the engine should match that by flipping the necessary games
to reach that split. Now how the engine flips those games should be
assessed via games data and signals."

Measured the day this was written, published spread picks since 2026-08-01:

    sport   FAV   DOG   dog share   blanket dog cover rate
    NBA      65    10      13.3%    43.4%  n=145
    NCAAB    60     8      11.8%    47.9%  n=192
    NCAAF   261   142      35.2%    47.5%  n=706

So the engine published a dog on 1 of 8 NBA spread picks while dogs covered
43 of every 100 games. That is not a read on the games, it is a broken
estimator -- and an estimator whose output distribution is nowhere near the
base rate is the definition of miscalibrated.

THREE STAGES, each independently inspectable:

  1. BASE RATE   what the market actually did, per sport, with an n gate and
                 a per-sport CONSISTENCY CHECK. A sport whose close_spread
                 disagrees with its own spread_result is REFUSED, not
                 calibrated to garbage -- NFL fails this today (73.8%
                 agreement, home favoured in 38.2% of games).
  2. RANK        which games are most likely dog covers, scored from the
                 signals that are actually FILLED for that game. A missing
                 signal drops out and the weights renormalise; it never
                 reads as zero (feedback_dict_get_on_a_guessed_column).
  3. FLIP        convert the N highest-ranked favourite picks to the dog on
                 the SAME game until the split reaches the target.

`--backtest` is the only mode that should decide whether stage 3 ever runs
live. It asks the one question that matters: do the dogs this ranking picks
cover MORE often than a random dog? If they do not, the ranking is noise and
flipping by it just trades one miscalibration for another.

Usage:
    python calibrate_spread_split.py --sport NBA --backtest
    python calibrate_spread_split.py --sport NBA --date 2026-11-09
    python calibrate_spread_split.py --sport NBA --date 2026-11-09 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
SB = os.getenv('SUPABASE_URL')
KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': 'Bearer %s' % KEY}
H_WRITE = dict(H)
H_WRITE['Content-Type'] = 'application/json'
H_WRITE['Prefer'] = 'return=representation'

#: A sport needs this many graded games before its base rate is usable.
MIN_BASE_N = 30
#: close_spread and spread_result must agree at least this often or the
#: sport's spread data is not trustworthy enough to calibrate against.
MIN_CONSISTENCY = 0.95

SPORTS = {
    'NBA': ('nba_game_context', 'nba_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results'),
    'NFL': ('nfl_game_context', 'nfl_game_results'),
    'NHL': ('nhl_game_context', 'nhl_game_results'),
}


def page(table, **params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get('%s/rest/v1/%s' % (SB, table), headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            raise RuntimeError('%s %s %s' % (table, r.status_code,
                                             r.text[:200]))
        b = r.json()
        out += b
        if len(b) < 1000:
            break
        off += 1000
    return out


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


# ─────────────────────────── stage 1: base rate ───────────────────────────

def base_rate(sport, since):
    """Return (dog_cover_pct, n, note) or (None, n, reason) if unusable.

    dog_cover_pct is the share of non-push games the UNDERDOG covered,
    computed from final scores and close_spread rather than trusting a
    precomputed column. close_spread is the HOME line (negative = home
    favoured); that orientation is CHECKED here, not assumed.
    """
    _, results = SPORTS[sport]
    rows = [x for x in page(results,
                            select='home_score,away_score,close_spread,'
                                   'spread_result',
                            game_date='gte.%s' % since)
            if x.get('home_score') is not None
            and x.get('away_score') is not None
            and _f(x.get('close_spread')) not in (None, 0.0)]
    if len(rows) < MIN_BASE_N:
        return None, len(rows), 'only %d graded games, need %d' % (
            len(rows), MIN_BASE_N)

    # Consistency: does the stored spread_result agree with the home-line
    # reading of close_spread? NFL fails this at 73.8%.
    agree = tot = 0
    for x in rows:
        sr = str(x.get('spread_result') or '')
        if sr not in ('home_covered', 'away_covered'):
            continue
        edge = (x['home_score'] - x['away_score']) + _f(x['close_spread'])
        if abs(edge) < 1e-9:
            continue
        tot += 1
        if ('home_covered' if edge > 0 else 'away_covered') == sr:
            agree += 1
    if tot and (agree / tot) < MIN_CONSISTENCY:
        return None, len(rows), (
            'close_spread disagrees with spread_result on %d of %d games '
            '(%.1f%% agreement) -- the spread data is not trustworthy, '
            'REFUSING to calibrate against it'
            % (tot - agree, tot, 100.0 * agree / tot))

    dog = fav = push = 0
    for x in rows:
        cs = _f(x['close_spread'])
        edge = (x['home_score'] - x['away_score']) + cs
        if abs(edge) < 1e-9:
            push += 1
            continue
        home_is_dog = cs > 0
        if (home_is_dog and edge > 0) or ((not home_is_dog) and edge < 0):
            dog += 1
        else:
            fav += 1
    n = dog + fav
    return (100.0 * dog / n), n, 'push %d, consistency %.1f%%' % (
        push, 100.0 * agree / tot if tot else 100.0)


# ──────────────────────── stage 2: dog-lean ranking ───────────────────────
#
# Every component returns a value in roughly [-1, +1] where POSITIVE means
# "this dog is more likely to cover", or None when its input is missing for
# this game. A None drops out and the remaining weights renormalise, so an
# absent signal never reads as a neutral zero that dilutes the others.

def _cushion(ctx, dog_side, dog_line, proj_home_positive):
    """Our own projected margin for the dog, plus the points it gets.

    This is the same quantity coherence_ok tests. Positive means our model
    says the dog covers. Scaled by 10 points, clipped to +-1.
    """
    ps = _f(ctx.get('projected_spread'))
    if ps is None or dog_line is None:
        return None
    norm = ps * (1.0 if proj_home_positive else -1.0)   # +ve = home favoured
    got = norm if dog_side == 'HOME' else -norm
    return max(-1.0, min(1.0, (got + dog_line) / 10.0))


def _dog_ats(ctx, dog_side):
    """The dog team's own record covering AS A DOG, centred on 50%."""
    key = 'home_covers_as_dog_pct' if dog_side == 'HOME' \
        else 'away_covers_as_dog_pct'
    v = _f(ctx.get(key))
    if v is None:
        return None
    if v <= 1.0:                      # stored as a fraction somewhere
        v *= 100.0
    return max(-1.0, min(1.0, (v - 50.0) / 50.0))


def _recent_ats(ctx, dog_side):
    """Recent ATS form of the dog, from whichever window this sport has."""
    for wins_k, loss_k in (
            ('home_ats_last10', 'home_ats_last10_losses'),
            ('home_ats_last5', 'home_ats_last5_losses')) \
            if dog_side == 'HOME' else (
            ('away_ats_last10', 'away_ats_last10_losses'),
            ('away_ats_last5', 'away_ats_last5_losses')):
        w, l = _f(ctx.get(wins_k)), _f(ctx.get(loss_k))
        if w is not None and l is not None and (w + l) > 0:
            return max(-1.0, min(1.0, (w - l) / (w + l)))
    return None


def _venue_ats(ctx, dog_side):
    """Dog's ATS record in this venue role (home-at-home / away-on-road)."""
    if dog_side == 'HOME':
        w, l = _f(ctx.get('home_ats_l10_at_home')), \
            _f(ctx.get('home_ats_l10_at_home_losses'))
    else:
        w, l = _f(ctx.get('away_ats_l10_on_road')), \
            _f(ctx.get('away_ats_l10_on_road_losses'))
    if w is None or l is None or (w + l) <= 0:
        return None
    return max(-1.0, min(1.0, (w - l) / (w + l)))


def _public_fade(ctx, dog_side):
    """Positive when the PUBLIC is on the favourite, i.e. the dog is the fade.

    splits_summary shapes vary by scraper, so this reads defensively and
    returns None rather than guessing.
    """
    ss = ctx.get('splits_summary')
    if isinstance(ss, str):
        try:
            ss = json.loads(ss)
        except Exception:                                   # noqa: BLE001
            return None
    if not isinstance(ss, dict):
        return None
    for k in ('home_ticket_pct', 'home_bets_pct', 'home_tickets',
              'home_pct'):
        v = _f(ss.get(k))
        if v is None:
            continue
        if v <= 1.0:
            v *= 100.0
        home_public = v
        dog_public = home_public if dog_side == 'HOME' else 100.0 - home_public
        # public on the FAVOURITE (dog_public < 50) -> positive fade score
        return max(-1.0, min(1.0, (50.0 - dog_public) / 50.0))
    return None


WEIGHTS = {
    'cushion': 0.45,       # our own projection vs the number
    'dog_ats': 0.20,       # this team's record covering as a dog
    'recent_ats': 0.15,
    'venue_ats': 0.10,
    'public_fade': 0.10,
}


def dog_lean(ctx, dog_side, dog_line, proj_home_positive):
    """Weighted dog-cover score plus which components were available."""
    parts = {
        'cushion': _cushion(ctx, dog_side, dog_line, proj_home_positive),
        'dog_ats': _dog_ats(ctx, dog_side),
        'recent_ats': _recent_ats(ctx, dog_side),
        'venue_ats': _venue_ats(ctx, dog_side),
        'public_fade': _public_fade(ctx, dog_side),
    }
    live = {k: v for k, v in parts.items() if v is not None}
    if not live:
        return None, parts
    wsum = sum(WEIGHTS[k] for k in live)
    score = sum(WEIGHTS[k] * v for k, v in live.items()) / wsum
    return score, parts


# ──────────────────────────── stage 3: report ─────────────────────────────

def proj_convention(sport):
    try:
        from retier_from_sport_bars import _PROJ_HOME_POS
        return _PROJ_HOME_POS.get(sport)
    except Exception:                                       # noqa: BLE001
        return None


def published_split(sport, since):
    """(fav, dog) counts among published spread picks with a line."""
    ctx_t, _ = SPORTS[sport]
    rows = page(ctx_t, select='primary_play', game_date='gte.%s' % since)
    fav = dog = 0
    for x in rows:
        pp = x.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:                               # noqa: BLE001
                pp = None
        if not isinstance(pp, dict):
            continue
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            continue
        ln = _f(pp.get('line'))
        if ln is None or ln == 0:
            continue
        if ln > 0:
            dog += 1
        else:
            fav += 1
    return fav, dog


def backtest(sport, since):
    """Do the dogs this ranking prefers cover MORE than a random dog?

    For every graded game, synthesise the dog pick, score it, and compare
    the cover rate of the top-ranked tranches against the blanket rate.
    """
    ctx_t, res_t = SPORTS[sport]
    hp = proj_convention(sport)
    if hp is None:
        print('  REFUSED: %s has no proven projected_spread orientation, so '
              'the cushion component cannot be computed.' % sport)
        return 2
    ctx = {x['game_id']: x for x in page(ctx_t, select='*',
                                        game_date='gte.%s' % since)
           if x.get('game_id')}
    res = [x for x in page(res_t,
                           select='game_id,home_score,away_score,close_spread',
                           game_date='gte.%s' % since)
           if x.get('home_score') is not None
           and _f(x.get('close_spread')) not in (None, 0.0)]
    scored = []
    for r in res:
        c = ctx.get(r['game_id'])
        if not c:
            continue
        cs = _f(r['close_spread'])
        dog_side = 'HOME' if cs > 0 else 'AWAY'
        dog_line = abs(cs)
        s, _parts = dog_lean(c, dog_side, dog_line, hp)
        if s is None:
            continue
        edge = (r['home_score'] - r['away_score']) + cs
        if abs(edge) < 1e-9:
            continue
        dog_covered = (edge > 0) if dog_side == 'HOME' else (edge < 0)
        scored.append((s, dog_covered))
    if len(scored) < MIN_BASE_N:
        print('  only %d scorable games, need %d' % (len(scored),
                                                     MIN_BASE_N))
        return 2
    scored.sort(key=lambda t: -t[0])
    n = len(scored)
    blanket = 100.0 * sum(1 for _, c in scored if c) / n
    print('  scorable games: %d   blanket dog cover: %.1f%%' % (n, blanket))
    print('  (break-even at -110 is 52.38%%)')
    print()
    print('  %-22s %6s %8s %10s' % ('tranche', 'n', 'cover%', 'vs blanket'))
    for label, frac in (('top 10%', .10), ('top 25%', .25),
                        ('top 50%', .50), ('bottom 50%', -.50),
                        ('bottom 25%', -.25)):
        k = max(1, int(abs(frac) * n))
        part = scored[:k] if frac > 0 else scored[-k:]
        cv = 100.0 * sum(1 for _, c in part if c) / len(part)
        print('  %-22s %6d %7.1f%% %+9.1fpp' % (label, len(part), cv,
                                                cv - blanket))
    print()
    xs = [s for s, _ in scored]
    ys = [1.0 if c else 0.0 for _, c in scored]
    if len(set(xs)) > 1:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        vx = sum((a - mx) ** 2 for a in xs)
        vy = sum((b - my) ** 2 for b in ys)
        if vx > 0 and vy > 0:
            print('  rank correlation with covering: r = %+.3f'
                  % (cov / (vx ** .5 * vy ** .5)))
    print()
    print('  READ THIS BEFORE WIRING THE FLIP: if the top tranches are not')
    print('  clearly above blanket AND above 52.38%, the ranking is noise')
    print('  and flipping by it trades one miscalibration for another.')
    return 0


def plan(sport, date, since, apply_it):
    """Show (and optionally apply) the flips needed to hit the base rate."""
    ctx_t, _ = SPORTS[sport]
    hp = proj_convention(sport)
    rate, n, note = base_rate(sport, since)
    print('=== %s base rate since %s ===' % (sport, since))
    if rate is None:
        print('  REFUSED: %s' % note)
        return 2
    print('  dog covered %.1f%% of %d graded games (%s)' % (rate, n, note))
    fav, dog = published_split(sport, since)
    tot = fav + dog
    if not tot:
        print('  no published spread picks in the window')
        return 1
    share = 100.0 * dog / tot
    print('  we published DOG on %d of %d spread picks = %.1f%%'
          % (dog, tot, share))
    print('  divergence: %+.1fpp' % (share - rate))
    print()
    rows = [x for x in page(ctx_t, select='*', game_date='eq.%s' % date)]
    cands = []
    for x in rows:
        pp = x.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:                               # noqa: BLE001
                pp = None
        if not isinstance(pp, dict):
            continue
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            continue
        ln = _f(pp.get('line'))
        if ln is None or ln >= 0:
            continue                      # already a dog, or no line
        cs = _f(x.get('close_spread'))
        if cs is None or cs == 0:
            continue
        dog_side = 'HOME' if cs > 0 else 'AWAY'
        s, parts = dog_lean(x, dog_side, abs(cs), hp) if hp else (None, {})
        cands.append((s if s is not None else -9.9, x, pp, dog_side,
                      abs(cs), parts))
    today_fav = len(cands)
    today_dog = sum(1 for x in rows
                    if isinstance(x.get('primary_play'), dict)
                    and (_f((x['primary_play'] or {}).get('line')) or 0) > 0)
    slate = today_fav + today_dog
    want_dog = int(round(slate * rate / 100.0))
    need = max(0, want_dog - today_dog)
    print('=== %s slate %s ===' % (sport, date))
    print('  spread picks: %d  (FAV %d / DOG %d)' % (slate, today_fav,
                                                     today_dog))
    print('  target DOG count at a %.1f%% base rate: %d  -> flip %d'
          % (rate, want_dog, need))
    if hp is None:
        print('  NOTE: no proven projection orientation for %s, so the '
              'cushion component is unavailable and ranking is weaker.'
              % sport)
    print()
    cands.sort(key=lambda t: -t[0])
    print('  %-34s %7s  %s' % ('favourite pick', 'lean', 'components'))
    for i, (s, x, pp, dside, dline, parts) in enumerate(cands):
        mark = '  <= FLIP' if i < need else ''
        avail = ','.join('%s=%+.2f' % (k, v) for k, v in parts.items()
                         if v is not None) or 'none'
        print('  %-34s %+7.3f  %s%s' % (str(pp.get('label'))[:34],
                                        s if s > -9 else float('nan'),
                                        avail, mark))
    if not apply_it:
        print()
        print('  DRY RUN. Nothing written. --apply to flip the marked rows.')
        return 0
    print()
    print('  --apply is deliberately NOT implemented yet: the flip must not')
    print('  go live until --backtest shows the ranking beats the blanket')
    print('  dog rate AND 52.38%%. Wiring it before that is exactly the')
    print('  "unmeasured repair" this repo keeps paying for.')
    return 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(SPORTS))
    ap.add_argument('--date', default=None)
    ap.add_argument('--since', default=None,
                    help='base-rate window start (default: season-ish)')
    ap.add_argument('--backtest', action='store_true')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    date = a.date or (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=5)).date().isoformat()
    since = a.since or {'NBA': '2026-10-01', 'NCAAB': '2026-10-25',
                        'NCAAF': '2026-08-20', 'NFL': '2026-09-01',
                        'NHL': '2026-10-01'}[a.sport]
    if a.backtest:
        print('=== %s dog-lean BACKTEST since %s ===' % (a.sport, since))
        return backtest(a.sport, since)
    return plan(a.sport, date, since, a.apply)


if __name__ == '__main__':
    sys.exit(main())
