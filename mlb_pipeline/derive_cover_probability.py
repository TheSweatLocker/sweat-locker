"""Derive mc_p_home_covers for the sports whose simulator never computed it.

══ 2026-10-30 · WHY THE LADDER HAS BEEN PARKED ══
steam_room_ladder needs a model probability for the side it is sizing. For a
spread pick it reads a COVER probability, and only the NBA simulator writes
one (`mc_p_home_covers`). NCAAF, NFL and NHL write mc_p_home / mc_p_away —
outright win, which is a different question — so every spread candidate came
back win_prob=None, the edge gate saw -999, and the ladder could not qualify a
single one. Tonight that was 7 NCAAF rl/LEAN + 1 rl/STRONG + 3 NFL rl/LEAN
silently discarded.

Nothing needs re-simulating. Those simulators already publish the full margin
distribution — mc_expected_margin and mc_stddev_margin — so

    P(home covers) = P(margin > threshold) = 1 - Phi((threshold - mu) / sd)

Orientation was verified against actual results rather than assumed
(corr of mc_expected_margin with the real home margin):

    NCAAF +0.5796 n=427 sd 20.38      NBA +0.2624 n=69  sd 16.22
    NHL   +0.1545 n=213               NFL +0.1460 n=108 sd 14.54

All four are HOME-oriented margins. NCAAF's +0.58 is the strongest model
signal measured anywhere in this repo.

THE THRESHOLD, AND WHY IT IS SELF-CHECKED
NFL stores close_spread as a home MARGIN (positive = home favoured) while
NCAAF/NBA/NHL store a betting spread (negative home = home favoured), so the
threshold differs by sport and a sign slip would invert every probability.
feedback_edge_formula_sign_audit and project_flipped_lines_marquee_games_1009
are both this mistake. So --check replays the chosen threshold against
spread_result on completed games and reports the agreement rate; anything
under 97% refuses to write.

USAGE
  python derive_cover_probability.py --check
  python derive_cover_probability.py --sport NCAAF --days 8
  python derive_cover_probability.py --days 8        # every sport
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: sport -> (ctx table, results table, spread column, bridge column or None,
#:           spread_is_home_margin, fallback sd, check_spread_from)
#:
#: ══ 2026-10-30 · THE CHECK WAS READING THE WRONG LINE (R54) ══════════════
#: NFL failed the sign check at 93% and so never got a cover probability,
#: which is what kept it off the ladder. The sign was never the problem.
#:
#: `nfl_game_context.close_spread` is a LIVE Odds API snapshot, written each
#: pull from `_pick_book(event, 'spreads')`. That is the right number for an
#: upcoming game — but the moment a game leaves the odds feed the row stops
#: being refreshed, so a PLAYED game keeps whatever mid-week line was last
#: pulled. `nfl_game_results.close_spread` comes from nflverse and is the
#: true close. Measured over 272 bridged rows: 209 agree, 63 disagree, and
#: on the disagreeing rows ctx matches NOTHING else in its own table (43) or
#: just the open line (20) — the signature of a stale snapshot, not a sign
#: flip (only 3 of 272 were exact negations) and not a projection leak.
#:
#: Arbitrated against `line_history`, which holds real per-book posted
#: quotes and is independent of both: on the disagreeing games books posted
#: the RESULTS number 19 times and the CTX number once. (results.close_spread
#: also agrees with results.spread_result 100/100, but spread_result is
#: computed from that same column, so that check is circular and proves
#: nothing on its own — feedback_lens_independence_needs_different_inputs.)
#:
#: So each source is correct for a different job, and `check_spread_from`
#: says which to use: the CHECK only ever looks at played games, so it takes
#: the authoritative close from results; the WRITE targets forward games,
#: where ctx holds the live line and a results row is only a pre-game
#: estimate. Nothing is repaired here — ctx is not wrong about the future.
CFG = {
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', 'close_spread',
              None, False, 20.38, 'ctx'),
    'NFL': ('nfl_game_context', 'nfl_game_results', 'close_spread',
            'results_game_id', True, 14.54, 'results'),
    'NHL': ('nhl_game_context', 'nhl_game_results', 'close_puckline',
            None, False, 2.05, 'ctx'),
    'NBA': ('nba_game_context', 'nba_game_results', 'close_spread',
            None, False, 16.22, 'ctx'),
}

MIN_AGREEMENT = 97.0


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _threshold(spread, is_home_margin):
    """The home margin the game must EXCEED for home to cover."""
    return spread if is_home_margin else -spread


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:170]}')
            return []
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def check(sports):
    """Replay the threshold against known spread_result. Returns {sport: pct}."""
    agree = {}
    for sport in sports:
        ctbl, rtbl, spcol, bridge, is_margin, _sd, spread_from = CFG[sport]
        sel = f'game_id,{spcol}' + (f',{bridge}' if bridge else '')
        ctx = page(ctbl, {'select': sel})
        _rsel = 'game_id,home_score,away_score,spread_result'
        if spread_from == 'results':
            _rsel += f',{spcol}'
        res = {str(r['game_id']): r for r in page(rtbl, {'select': _rsel})}
        ok = tot = 0
        for c in ctx:
            key = str(c.get(bridge)) if bridge else str(c['game_id'])
            g = res.get(key)
            if not g or g.get('home_score') is None:
                continue
            # the CHECK grades played games, so it reads the authoritative
            # close; see check_spread_from in CFG.
            sp = _f(g.get(spcol)) if spread_from == 'results' \
                else _f(c.get(spcol))
            if sp is None:
                continue
            sr = str(g.get('spread_result') or '').lower()
            if sr not in ('home_covered', 'away_covered'):
                continue
            margin = float(g['home_score']) - float(g['away_score'])
            thr = _threshold(sp, is_margin)
            if abs(margin - thr) < 1e-9:
                continue          # a push proves nothing either way
            tot += 1
            if (margin > thr) == (sr == 'home_covered'):
                ok += 1
        pct = (100.0 * ok / tot) if tot else 0.0
        agree[sport] = (pct, tot)
        flag = 'OK' if pct >= MIN_AGREEMENT else 'FAILS — WILL NOT WRITE'
        print(f'  {sport:6s} threshold agrees with spread_result on '
              f'{ok}/{tot} = {pct:.2f}%  {flag}')
    return agree


def run(sports, days, dry_run):
    agree = check(sports)
    base = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    lo = base.isoformat()
    hi = (base + timedelta(days=max(1, days) - 1)).isoformat()
    print(f'\nwriting mc_p_home_covers for {lo}..{hi}'
          + ('  [DRY RUN]' if dry_run else ''))
    for sport in sports:
        pct, n = agree.get(sport, (0.0, 0))
        if pct < MIN_AGREEMENT:
            print(f'  {sport:6s} SKIPPED — sign check {pct:.2f}% on n={n}')
            continue
        # the WRITE targets forward games, where ctx holds the live
        # line; check_spread_from applies only to the check above.
        ctbl, _rt, spcol, _b, is_margin, fb_sd, _sf = CFG[sport]
        rows = page(ctbl, {'select': f'game_id,game_date,{spcol},mc_probabilities',
                           'game_date': [f'gte.{lo}', f'lte.{hi}']})
        wrote = skipped = failed = 0
        for row in rows:
            mc = row.get('mc_probabilities')
            if not isinstance(mc, dict):
                skipped += 1
                continue
            mu = _f(mc.get('mc_expected_margin'))
            sp = _f(row.get(spcol))
            if mu is None or sp is None:
                skipped += 1
                continue
            sd = _f(mc.get('mc_stddev_margin')) or fb_sd
            if not sd or sd <= 0:
                skipped += 1
                continue
            thr = _threshold(sp, is_margin)
            p_home = 1.0 - _phi((thr - mu) / sd)
            p_home = max(0.0, min(1.0, p_home))
            new = dict(mc)
            new['mc_p_home_covers'] = round(p_home, 4)
            new['mc_p_away_covers'] = round(1.0 - p_home, 4)
            new['mc_cover_sd_used'] = round(sd, 2)
            new['mc_cover_source'] = ('simulated_sd'
                                      if _f(mc.get('mc_stddev_margin'))
                                      else 'fallback_sd')
            if (mc.get('mc_p_home_covers') == new['mc_p_home_covers']
                    and mc.get('mc_cover_sd_used') == new['mc_cover_sd_used']):
                skipped += 1
                continue
            if dry_run:
                wrote += 1
                continue
            pr = requests.patch(f'{SB}/rest/v1/{ctbl}', headers=H_WRITE,
                                timeout=60,
                                params={'game_id': f'eq.{row["game_id"]}',
                                        'game_date': f'eq.{row["game_date"]}'},
                                json={'mc_probabilities': new})
            ok = False
            if pr.status_code in (200, 201):
                back = pr.json() or []
                ok = bool(back) and (
                    (back[0].get('mc_probabilities') or {}).get('mc_p_home_covers')
                    == new['mc_p_home_covers'])
            if ok:
                wrote += 1
            else:
                failed += 1
                if failed <= 2:
                    print(f'    ! {sport} {row["game_id"]} not confirmed '
                          f'(HTTP {pr.status_code})')
        print(f'  {sport:6s} wrote {wrote:4d}  unchanged/no-inputs {skipped:4d}  '
              f'failed {failed:3d}  of {len(rows)} games')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append', choices=sorted(CFG))
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--check', action='store_true',
                    help='only replay the sign check, write nothing')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    sports = a.sport or sorted(CFG)
    print('=== derive_cover_probability ===')
    if a.check:
        check(sports)
        return
    run(sports, a.days, a.dry_run)


if __name__ == '__main__':
    main()
