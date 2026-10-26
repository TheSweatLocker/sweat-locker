"""Interrogate the ENGINE'S OWN PICK on every game against the matchup stats.

Andy's actual ask: for every game, look at the stats / rolling numbers /
matchup data, find where the engine is demonstrably WRONG, and flip those.
Three columns: original pick | flipped pick | the stats-backed reason.

Verified before use (both on 2026-10-24's 44 NCAAF rows):
  off_epa_pp  corr +0.747 with l4_ppg      -> higher = better offence
  def_epa_pp  corr +0.838 with def_ppg     -> higher = WORSE defence
  both on the same per-play scale (-0.18..+0.39), so
  quality = off_epa_pp - def_epa_pp is a legitimate single number
  sos_rank / sor_rank: 1 = BEST (Miami rank 3 @ SP+ 25.3, NIU 134 @ -25.0)
  sp_plus_overall is already in POINTS of margin vs an average opponent

Every flag below is a number from the row, printed with the number, so each
reason is checkable rather than asserted.
"""
import argparse
import datetime as dt
import os

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        or os.environ.get('SUPABASE_KEY'))
H = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'}


class _Q:
    @staticmethod
    def get(table, params, limit=1000):
        p = dict(params)
        p['limit'] = str(limit)
        r = requests.get(f'{SB}/rest/v1/{table}', params=p, headers=H,
                         timeout=30)
        if r.status_code != 200:
            return {'__error': r.status_code, '__body': r.text[:300]}, None
        return r.json(), None


q = _Q()

from sport_line_sign import HOME_HANDICAP_SIGN as HOME_SIGN  # 2026-10-25: was a local dict. Canonical now lives in
# sport_line_sign.py with its validation counts (NFL 56/56,
# NCAAF 310/310) -- the convention exists in two parameterisations
# that look contradictory, and retyping one is how a sign error
# gets underneath everything fitted on top of it.
HFA = {'NCAAF': 2.5, 'NFL': 1.8}
PLAYS = 65.0          # typical offensive plays, for EPA -> points


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def page(t, p, cap=10):
    out, off = [], 0
    while off < cap * 1000:
        r, _ = q.get(t, dict(p, offset=str(off)), limit=1000)
        if isinstance(r, dict):
            print('  ERR', t, str(r)[:200])
            return out
        out += r
        if len(r) < 1000:
            return out
        off += 1000
    return out


def amer_prob(o):
    o = float(o)
    return (-o / (-o + 100.0)) if o < 0 else (100.0 / (o + 100.0))


now = dt.datetime.now(dt.timezone.utc)


def board(sport, lo):
    rows = page('line_history', {'select': 'game_id,side,line,price,book,'
                                           'commence_time,captured_at',
                                 'sport': f'eq.{sport}', 'market': 'eq.spread',
                                 'commence_time': f'gte.{lo}',
                                 'order': 'captured_at.desc'})
    seen, pool, teams = set(), {}, {}
    for r in rows:
        gid = str(r.get('game_id') or '')
        ln, pr = f(r.get('line')), f(r.get('price'))
        if ln is None or pr is None:
            continue
        k = (gid, str(r.get('book')), str(r.get('side')).lower(), ln)
        if k in seen:
            continue
        seen.add(k)
        pool.setdefault((gid, str(r.get('side')).lower(), ln), []).append(pr)
        parts = gid.split('_')
        if len(parts) >= 3:
            teams[(parts[-2], parts[-1])] = gid
    out = {}
    for k, prices in pool.items():
        if len(prices) < 3:
            continue
        ranked = sorted(prices, key=amer_prob)
        out[k] = (ranked[len(ranked) // 2], len(prices))
    return out, teams


def splits(sport):
    """(game_id) -> latest spread split. Empty is NORMAL before game day.

    Measured on 298 NCAAF games: the FIRST spread split lands a median 3.2h
    before kickoff (earliest 11.3h, and 10% land AFTER kickoff). So a Friday
    night run legitimately has nothing for Saturday, and an empty dict here
    means 'not yet', not 'broken'. Re-run on game morning to pick these up.
    """
    rows, off = [], 0
    while off < 20000:
        r, _ = q.get('line_snapshot',
                     {'select': 'game_id,market,pick_side,bets_pct,'
                                'money_pct,snapshot_ts',
                      'sport': f'eq.{sport}', 'offset': str(off),
                      'order': 'snapshot_ts.asc'}, limit=1000)
        if isinstance(r, dict) or not r:
            break
        rows += r
        if len(r) < 1000:
            break
        off += 1000
    out = {}
    for x in rows:
        if str(x.get('market') or '').lower() not in ('rl', 'spread'):
            continue
        k = str(x.get('game_id'))
        t = str(x.get('snapshot_ts'))
        if k not in out or t > str(out[k].get('snapshot_ts')):
            out[k] = x
    return out


def audit(sport, tbl, dates):
    quotes, teammap = board(sport, dates[0])
    flow = splits(sport)
    games = [g for g in page(tbl, {'select': '*', 'game_date': f'gte.{dates[0]}',
                                   'order': 'game_date.asc'})
             if str(g.get('game_date'))[:10] in dates]
    rows = []
    for g in games:
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict):
            continue
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            continue
        ours = f(pp.get('line'))
        side = str(pp.get('side') or '').upper()
        if ours is None or side not in ('HOME', 'AWAY'):
            continue
        P, O = ('home', 'away') if side == 'HOME' else ('away', 'home')
        pteam = str(g[f'{P}_team'])
        oteam = str(g[f'{O}_team'])
        gv = lambda s, k: f(g.get(f'{s}_{k}'))

        flags, score = [], 0.0

        # 1 — SP+ implied margin vs our number. SP+ is in points already.
        spP, spO = gv(P, 'sp_plus_overall'), gv(O, 'sp_plus_overall')
        sp_margin = None
        if spP is not None and spO is not None:
            hfa = HFA[sport] * (1 if P == 'home' else -1)
            sp_margin = spP - spO + hfa          # P's expected margin
            sp_handicap = -sp_margin             # P's fair handicap
            # A CULPRIT is only where OUR number is worse than fair, i.e.
            # ours < fair: we laid more points, or took fewer, than the model
            # supports. The opposite case (ours > fair) means we got a BETTER
            # number than fair and is a reason to KEEP the pick, never to flip
            # it -- scoring both alike would have had me flipping Arizona -10
            # when SP+ and EPA both said -17.
            disagree = ours - sp_handicap
            if disagree <= -3.0:
                score += abs(disagree)
                flags.append(
                    f'SP+ says {pteam} {sp_handicap:+.1f} (SP+ {spP:+.1f} vs '
                    f'{spO:+.1f}{", +HFA" if P == "home" else ", road"}), '
                    f'we published {ours:+g} — {abs(disagree):.1f} pts TOO '
                    f'CONFIDENT')
            elif disagree >= 3.0:
                flags.append(
                    f'[keep] SP+ says {pteam} {sp_handicap:+.1f}, we only '
                    f'published {ours:+g} — our number is {disagree:.1f} pts '
                    f'BETTER than fair')

        # 2 — EPA quality. quality = off_epa - def_epa, same per-play scale.
        qP = qO = None
        if None not in (gv(P, 'off_epa_pp'), gv(P, 'def_epa_pp'),
                        gv(O, 'off_epa_pp'), gv(O, 'def_epa_pp')):
            qP = gv(P, 'off_epa_pp') - gv(P, 'def_epa_pp')
            qO = gv(O, 'off_epa_pp') - gv(O, 'def_epa_pp')
            epa_margin = (qP - qO) * PLAYS + HFA[sport] * (1 if P == 'home' else -1)
            epa_handicap = -epa_margin
            dis = ours - epa_handicap
            if dis <= -4.0:
                score += abs(dis) * 0.6
                flags.append(
                    f'EPA/play says {pteam} {epa_handicap:+.1f} '
                    f'(net {qP - qO:+.3f}/play × {PLAYS:g} plays), '
                    f'we published {ours:+g} — {abs(dis):.1f} TOO CONFIDENT')
            elif dis >= 4.0:
                flags.append(
                    f'[keep] EPA says {pteam} {epa_handicap:+.1f}, we '
                    f'published {ours:+g} — {dis:.1f} better than fair')
            if ours < 0 and qP < qO:
                score += 6
                flags.append(
                    f'LAYING points with the WORSE team by EPA: {pteam} '
                    f'{qP:+.3f} vs {oteam} {qO:+.3f}/play')

        # 3 — schedule strength behind the record
        rkP, rkO = gv(P, 'sos_rank'), gv(O, 'sos_rank')
        if rkP is not None and rkO is not None and ours < 0 and rkP - rkO >= 35:
            score += 4
            flags.append(
                f'record built on a softer schedule: {pteam} SoS rank '
                f'{rkP:.0f} vs {oteam} {rkO:.0f} (1=toughest)')

        # 4 — does this team cover in THIS role?
        if ours < 0:
            cf = gv(P, 'covers_as_fav_pct')
            if cf is not None and cf <= 40:
                score += 4
                flags.append(f'{pteam} covers only {cf:.0f}% as a favourite')
        else:
            cd = gv(P, 'covers_as_dog_pct')
            if cd is not None and cd <= 40:
                score += 3
                flags.append(f'{pteam} covers only {cd:.0f}% as a dog')

        # 5 — turnover luck inflating the side we backed
        tmP, tmO = gv(P, 'to_margin'), gv(O, 'to_margin')
        if tmP is not None and tmP >= 4:
            score += 3
            flags.append(f'{pteam} turnover margin {tmP:+.0f} — record '
                         f'flattered, regresses')
        if tmO is not None and tmO <= -4:
            score += 2
            flags.append(f'{oteam} turnover margin {tmO:+.0f} — due to bounce back')

        # 6 — cover-rate regression on the side we backed
        cp = gv(P, 'season_cover_pct')
        if cp is not None and cp >= 70:
            score += 3
            flags.append(f'{pteam} covering {cp:.0f}% ATS this season — '
                         f'regression candidate')

        # 7 — run-game mismatch straight into the side we backed
        orush, pdef = gv(O, 'rush_yds_pg'), gv(P, 'def_rush_ypg')
        if orush is not None and pdef is not None and orush >= 180 and pdef >= 160:
            score += 3
            flags.append(f'{oteam} runs {orush:.0f} yd/g into {pteam} '
                         f'allowing {pdef:.0f} on the ground')

        # ══ NFL-specific. Its context carries a different stat set: no SP+,
        # no covers_as_fav_pct, no to_margin. What it DOES have is per-GAME
        # EPA on both sides of the ball, verified same-scale before combining
        # (pass_epa_pg med +1.25 vs def_pass_epa_allowed med +1.20; rush
        # -0.76 vs -0.77). EPA per game is already in points, so no
        # plays-per-game conversion is needed and none is applied -- mixing a
        # per-play number with a per-game one is the 30x error of
        # feedback_a_pair_needs_same_measure_and_scale.
        if sport == 'NFL':
            def nq(sd):
                o = (gv(sd, 'pass_epa_pg'), gv(sd, 'rush_epa_pg'))
                d = (gv(sd, 'def_pass_epa_allowed'),
                     gv(sd, 'def_rush_epa_allowed'))
                if None in o or None in d:
                    return None
                return (o[0] + o[1]) - (d[0] + d[1])
            nP, nO = nq(P), nq(O)
            if nP is not None and nO is not None:
                mar = nP - nO + HFA[sport] * (1 if P == 'home' else -1)
                fair = -mar
                dis = ours - fair
                if dis <= -3.0:
                    score += abs(dis)
                    flags.append(
                        f'EPA/game says {pteam} {fair:+.1f} (net EPA '
                        f'{nP:+.1f} vs {nO:+.1f} per game), we published '
                        f'{ours:+g} — {abs(dis):.1f} TOO CONFIDENT')
                elif dis >= 3.0:
                    flags.append(f'[keep] EPA/game says {pteam} {fair:+.1f}, '
                                 f'we published {ours:+g} — {dis:.1f} better '
                                 f'than fair')
                if ours < 0 and nP < nO:
                    score += 6
                    flags.append(f'LAYING points with the WORSE team by net '
                                 f'EPA/game: {pteam} {nP:+.1f} vs {oteam} '
                                 f'{nO:+.1f}')
            orP, orO = gv(P, 'off_rating'), gv(O, 'off_rating')
            if orP is not None and orO is not None and ours < 0 and orP < orO - 3:
                score += 3
                flags.append(f'{oteam} is the better offence by rating '
                             f'({orO:+.1f} vs {pteam} {orP:+.1f}) yet we lay '
                             f'points')
            rkP, rkO = gv(P, 'sor_rank'), gv(O, 'sor_rank')
            if rkP is not None and rkO is not None and ours < 0 and rkP - rkO >= 8:
                score += 3
                flags.append(f'weaker resume: {pteam} SoR rank {rkP:.0f} vs '
                             f'{oteam} {rkO:.0f} of 32')
            ipP, diO = gv(P, 'pass_ints_pg'), gv(O, 'def_ints_pg')
            if ipP is not None and diO is not None and ipP >= 1.0 and diO >= 0.9:
                score += 3
                flags.append(f'{pteam} throws {ipP:.2f} INT/g into a defence '
                             f'taking {diO:.2f}/g')
            ssP, dsO = gv(P, 'sacks_suffered_pg'), gv(O, 'def_sacks_pg')
            if ssP is not None and dsO is not None and ssP >= 2.5 and dsO >= 2.5:
                score += 2
                flags.append(f'{pteam} allows {ssP:.1f} sacks/g into '
                             f'{oteam} pass rush at {dsO:.1f}/g')
            rsP, rsO = gv(P, 'rest'), gv(O, 'rest')
            if rsP is not None and rsO is not None and rsO - rsP >= 3:
                score += 2
                flags.append(f'rest edge against us: {oteam} {rsO:.0f} days '
                             f'vs {pteam} {rsP:.0f}')

        # ══ SOR vs SOS — a hollow resume ══
        # Strength of RECORD ranking much better than strength of SCHEDULE
        # ranking means the record is a product of who they played. Backing
        # that team is backing the schedule, not the team. Both ranks are
        # 1=best, verified.
        sorP, sosP = gv(P, 'sor_rank'), gv(P, 'sos_rank')
        if sorP is not None and sosP is not None and sosP - sorP >= 40:
            score += 3
            flags.append(
                f'{pteam} resume is hollow: SoR rank {sorP:.0f} on a schedule '
                f'ranked only {sosP:.0f} (1=toughest) — {sosP - sorP:.0f} '
                f'places of inflation')
        sorO = gv(O, 'sor_rank')
        if sorP is not None and sorO is not None and ours < 0 and sorP > sorO:
            score += 2
            flags.append(
                f'laying points with the worse resume: {pteam} SoR {sorP:.0f} '
                f'vs {oteam} {sorO:.0f}')

        # ══ MONEY FLOW ══ tickets and money are different signals, and they
        # do not point the same way across sports. Fading heavy public
        # TICKETS on a football spread measured 59.4% / ROI +13.4% on n=96
        # today, while project_split_flags_opposite_by_sport_1012 found the
        # MONEY side is one to FOLLOW on NCAAF. So heavy tickets on our side
        # is a strike against us, and money against our side is a second,
        # independent one.
        sp_row = flow.get(str(g['game_id']))
        if sp_row is not None:
            b = f(sp_row.get('bets_pct'))
            m = f(sp_row.get('money_pct'))
            ps = str(sp_row.get('pick_side') or '').upper()
            if b is not None and ps in ('HOME', 'AWAY'):
                on_us = (ps == side)
                tickets_us = b if on_us else 100.0 - b
                if tickets_us >= 65:
                    score += 4
                    flags.append(
                        f'{tickets_us:.0f}% of TICKETS are on our side — '
                        f'fading heavy public football spreads measured '
                        f'59.4%, ROI +13.4%, n=96')
                if m is not None:
                    money_us = m if on_us else 100.0 - m
                    if money_us <= 40:
                        score += 3
                        flags.append(
                            f'MONEY is against us: only {money_us:.0f}% of '
                            f'handle on our side ({tickets_us:.0f}% of '
                            f'tickets) — money is the side to follow on '
                            f'football')
                    if tickets_us - money_us >= 15:
                        score += 2
                        flags.append(
                            f'public/sharp divergence: tickets '
                            f'{tickets_us:.0f}% vs handle {money_us:.0f}% on '
                            f'our side — small bets, big money elsewhere')
        elif sport in ('NCAAF', 'NFL'):
            flags.append('[keep] no money-flow split captured for this game '
                         'yet (median arrival is 3.2h before kickoff)')

        # 8 — our published number is already off the board
        cs = f(g.get('close_spread'))
        if cs is not None:
            close_for_us = abs(cs) if ours > 0 else -abs(cs)
            drift = ours - close_for_us
            if abs(drift) >= 1.5:
                score += abs(drift)
                flags.append(f'published line is stale: {ours:+g} vs market '
                             f'{close_for_us:+g} ({drift:+.1f})')

        # the flipped side, priced from the live board
        lh = teammap.get((str(g['away_team']), str(g['home_team'])))
        fside = 'HOME' if side == 'AWAY' else 'AWAY'
        fteam = str(g[f"{'home' if fside == 'HOME' else 'away'}_team"])
        opts = [(ln, v) for (q_gid, q_side, ln), v in quotes.items()
                if q_gid == lh and q_side == fside.lower()]
        fline, fprice, fbooks = None, None, 0
        if opts:
            best = max(opts, key=lambda z: z[0])
            fline, (fprice, fbooks) = best[0], best[1]
        rows.append({
            'game': f"{g['away_team']} @ {g['home_team']}",
            'date': str(g['game_date'])[:10],
            'orig': f"{pteam} {ours:+g}", 'tier': str(pp.get('tier') or ''),
            'flip': (f"{fteam} {fline:+g} ({fprice:+g}, {fbooks}bk)"
                     if fline is not None else 'no posted quote'),
            'score': round(score, 1), 'flags': flags,
            'sp': sp_margin, 'q': (qP, qO),
        })
    rows.sort(key=lambda r: -r['score'])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['NCAAF', 'NFL'], action='append')
    ap.add_argument('--dates', action='append',
                    help='YYYY-MM-DD, repeatable; default = next 5 days')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--flip', type=int, default=10)
    a = ap.parse_args()
    sports = a.sport or ['NCAAF', 'NFL']
    if a.dates:
        dates = a.dates
    else:
        d0 = dt.datetime.now(dt.timezone.utc).date()
        dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(5)]
    TBL = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
    for sport in sports:
        _render(sport, audit(sport, TBL[sport], dates), a.top, a.flip)


def _render(sport, rows, top, nflip):
    rows = rows[:top] if top else rows
    print(f'################ {sport} · {len(rows)} games with a spread pick '
          f'################')
    for i, r in enumerate(rows, 1):
        real = [x for x in r['flags'] if not x.startswith('[keep]')]
        mark = 'FLIP' if i <= nflip and r['score'] >= 6.0 and real else 'keep'
        print(f"{i:>2d}. [{mark}] score {r['score']:>5.1f}  {r['game']}  {r['date']}")
        print(f"      engine : {r['orig']}  ({r['tier']})")
        print(f"      flip   : {r['flip']}")
        for fl in (real or r['flags'])[:4]:
            print(f"      why    : {fl}")
        if not real:
            print(f"      why    : no stat contradiction found — leave it")
    print()


if __name__ == '__main__':
    main()
