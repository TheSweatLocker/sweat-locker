"""Strength of Schedule and Strength of Record, every sport.

Andy 2026-09-26: "SOS and SOR added somewhere in game detail across all
sports, will def help in college sports modeling with the red green
highlighting which is better."

THEY ARE NOT THE SAME THING, which is why both are here:

  SOS — how hard the opponents you have played are. Says nothing about
        you. A 1-4 team can have the best SOS in the country.
  SOR — how impressive YOUR results are given that schedule. Roughly:
        would an average team have gone 4-1 against those five teams?
        This is the one that separates teams, and it is the one college
        football arguments are actually about.

    SOR = your win% − (win% an average team would expect vs this slate)

  So +0.30 means you are winning 30 points more often than a neutral
  team would against the same opponents. Negative means the record
  flatters you.

HEAD-TO-HEAD IS EXCLUDED, and it matters more than it sounds. A naive
SOS averages your opponents' win%, but those records INCLUDE their games
against you — so every team you lose to looks stronger precisely because
it beat you. First pass showed Charlotte at 0-2 with an SOS of 1.000 for
exactly that reason. Opponent win% here drops all games against the team
being rated, which is the standard correction.

WHERE IT LANDS: team_stats_rolling, the table the Team Stats card already
renders, as two ordinary stat_keys. No new component, no new fetch, no
new workflow step — the existing card picks them up with its percentile
chip, its head-to-head colouring and its ⓘ, because that card is generic
over stat_key.

    python compute_schedule_strength.py --dry-run
    python compute_schedule_strength.py
    python compute_schedule_strength.py --sport NCAAF
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

TARGET = 'team_computed_stats'
SPORTS = ['NCAAF', 'NFL', 'MLB', 'NBA', 'NHL', 'NCAAB']

# A team needs this many decided games before its record means anything,
# and before it is allowed to contribute to anyone else's SOS.
#
# ══ 2026-09-30 · RAISED 2 -> 3, AND IT DOUBLES AS THE FBS FILTER ══
# At MIN_GAMES=2 the NCAAF pool was 152 teams against 138 actual FBS teams, so
# the rankings carried VMI, Duquesne, Towson, Portland State, Norfolk State and
# Mercyhurst — FCS schools that appear in our data only because they played one
# or two FBS opponents. Duquesne came out at SOR -1.000, bottom of college
# football, on two games.
#
# A name-match against CFBD /teams/fbs was the obvious filter and is a trap:
# CFBD spells them 'App State', 'UConn', "Hawai'i" while team_recent_games says
# 'Appalachian State', 'Connecticut', 'Hawaii', so the match silently DROPS
# real FBS teams. Verified: all three return False against the CFBD list.
#
# Games played is the honest discriminator and needs no alias table. An FBS
# team plays a full 12-game FBS schedule; an FCS team never gets more than one
# or two FBS games all year, and its FCS-vs-FCS games are not in our data at
# all. Measured 2026-09-30 the distribution was {1:85, 2:22, 3:31, 4:94, 5:12},
# so a floor of 3 yields 137 teams against 138 real FBS -- and it stays correct
# as the season runs, because the gap widens rather than closes.
MIN_GAMES = 3

# ══ 2026-09-30 · SHRINKAGE — THE FIX FOR THE TIES AND THE 1.000s ══
# Andy: "seeing a few ones that are the same is that because of the sample size
# right now being small? Is it calculated correctly?"
#
# Half right, and the half that is not is the actual bug. The sample IS tiny,
# but raw win% turns a tiny sample into a CERTAIN-looking extreme instead of an
# uncertain middling one. Measured before this change:
#
#     NCAAF SOS  152 teams, only 21 distinct values, 15 teams at exactly 1.000
#                ties: +0.500 x28, +0.750 x25, +0.667 x23
#     NFL   SOS   32 teams, only  7 distinct values,  8 at 1.000, 7 at 0.000
#     NCAAF SOR  range -1.000 .. +1.000
#
# The NFL case proves the mechanism outright: every team had played exactly 3
# games, so after head-to-head removal each opponent had 2 games and a win% of
# 0, 0.5 or 1. Averaging three of those can only land on 0, 1/3, 1/2, 2/3 or 1
# -- which is precisely the 7 values observed. Nothing was miscomputed; the
# estimator just had no humility.
#
# So each opponent's win% is shrunk toward 0.500 by its own game count:
#     p_adj = (w + K/2) / (n + K)
# With K=4 a 2-0 opponent reads 0.667 rather than 1.000 and an 0-2 reads 0.333
# rather than 0.000. Ties break, the impossible extremes disappear, and the
# correction fades on its own as n grows -- by week 12 a 9-3 team sits at 0.719
# against a raw 0.750.
#
# K=4 is a deliberate round number, not a fit. Fitting a shrinkage constant on
# 4 games of data would be the same mistake this file is correcting.
SHRINK_K = 4.0


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:180]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def counts_competitively(sport: str, row: dict) -> bool:
    """False for exhibition games that must not feed SOS or SOR.

    2026-09-28, Andy on the first NHL game detail: "SOS and SOR look
    strangely numbered dont think corretc in nhl." They were not correct.
    Measured: all 130 NHL season-2026 rows in team_recent_games carry
    game-type digits '01' — every single one a PRESEASON game. So each
    team's SOS and SOR were computed from 4-5 exhibitions, which is how
    Dallas ended up rated 0.75 "Strength of Record" and a third of the
    league landed on 0.1667 (literally one win in six).

    Preseason results are the least meaningful games in hockey: coaches
    rotate four lines, split goalies by period and play prospects who will
    not be on the roster. Rating a team on them is worse than having no
    rating, because a number on the card reads as knowledge.

    NHL game IDs encode the type at digits 4-6: 01 preseason, 02 regular
    season, 03 playoffs. Only NHL is filtered here — the other sports do
    not use this id shape, and their own exhibition games are excluded
    upstream of team_recent_games. With this in place NHL simply has no
    SOS/SOR until the regular season produces decided games, which is the
    correct state rather than a fabricated one, and it fills in on its own.
    """
    if sport != 'NHL':
        return True
    gid = str(row.get('game_id') or '')
    if len(gid) < 6:
        return False          # unparseable — do not guess it counts
    return gid[4:6] in ('02', '03')


def compute(sport: str, season: int) -> list[dict]:
    rows = page('team_recent_games', {
        'sport': f'eq.{sport}', 'season': f'eq.{season}',
        'select': 'team,opp,won,game_id'})
    decided = [r for r in rows
               if r.get('won') is not None and r.get('opp')
               and counts_competitively(sport, r)]
    if not decided:
        return []

    # Per-team record, and per-(team, opponent) record so head-to-head can
    # be removed from the opponent's win% below.
    rec = defaultdict(lambda: [0, 0])
    vs = defaultdict(lambda: [0, 0])
    opps = defaultdict(list)
    for r in decided:
        t, o, w = r['team'], r['opp'], bool(r['won'])
        rec[t][0 if w else 1] += 1
        vs[(t, o)][0 if w else 1] += 1
        opps[t].append(o)

    def winpct_excluding(opp: str, against: str):
        """Opponent's win% with its games vs `against` removed, SHRUNK toward
        0.500 by how many games that leaves.

        Head-to-head removal matters because otherwise every team that beats
        you inflates your SOS using that very win — which is how an 0-2 team
        ended up with a perfect 1.000 strength of schedule.

        The shrinkage matters just as much and was missing. After H2H removal
        an opponent can be left with one or two games, and a raw 2-0 reads as
        a flat 1.000 — a certainty the sample cannot support. See SHRINK_K.
        """
        w, l = rec[opp]
        ow, ol = vs[(opp, against)]
        w -= ow
        l -= ol
        n = w + l
        if n <= 0:
            return None
        return (w + SHRINK_K / 2.0) / (n + SHRINK_K)

    out = []
    for team, (w, l) in rec.items():
        n = w + l
        if n < MIN_GAMES:
            continue
        ratios = [winpct_excluding(o, team) for o in opps[team]]
        ratios = [x for x in ratios if x is not None]
        if not ratios:
            continue
        sos = sum(ratios) / len(ratios)
        # Win rate a neutral team would expect against this exact slate.
        #
        # NOTE THE IDENTITY, because it is easy to over-read SOR:
        #   expected = mean(1 - p) = 1 - mean(p) = 1 - sos
        #   sor      = win% - expected = win% + sos - 1
        # So SOR carries NO information beyond win% and SOS together. It is
        # still the right number to show — it answers "how good is this record
        # for this schedule" in one figure — but it must never be treated as an
        # independent third signal, and a model given win%, SOS and SOR has
        # been handed a perfectly collinear feature.
        #
        # The form is not arbitrary: 1 - p is exactly log5 for a .500 team
        # against an opponent of strength p, so this is the standard
        # expectation, not a heuristic. It is also why the pre-shrinkage values
        # could reach +/-1.000 -- that requires win% and sos both pinned at an
        # extreme, which raw small-sample win% happily produced.
        #
        # The team's own win% is shrunk on the same scale as its opponents',
        # otherwise a 3-0 team is measured as 1.000 against opponents who have
        # all been pulled toward 0.500, and SOR inherits the asymmetry.
        own = (w + SHRINK_K / 2.0) / (n + SHRINK_K)
        expected = sum(1.0 - x for x in ratios) / len(ratios)
        sor = own - expected
        out.append({'team': team, 'sos': round(sos, 4), 'sor': round(sor, 4),
                    'games': n, 'raw_win_pct': round(w / n, 4)})
    return out


def _ranked(vals: list[dict], key: str) -> dict:
    """Rank 1 = best, TIES SHARE A RANK. Both metrics are higher-is-better:
    a higher SOS means a tougher slate (so the record deserves more
    credit), a higher SOR means outperforming it.

    Competition ranking (1,2,2,4) is not cosmetic here — it is required for
    correctness downstream. Early in a season these values are coarsely
    quantised: a win rate over four opponents can only land on a handful of
    values, so on 2026-09-26 twenty-eight NCAAF teams shared SOS 0.5000
    exactly. Ranking by array position gave them 91 through 118, and the
    client turns rank into a percentile — so GameDetailV2 rendered "40th
    %ile" against "22nd %ile", and advantage() painted one side green and
    the other red, for two schedules that are identical to four decimals.
    That is precisely the manufactured-edge complaint Andy raised about the
    situational section. Every stat already in team_stats_rolling_full
    tie-ranks (29 teams at 1.0 turnovers/game all carry rank 46); this
    brings the computed half in line with the matview half.
    """
    order = sorted(vals, key=lambda x: -x[key])
    out: dict = {}
    prev_val, prev_rank = None, 0
    for i, v in enumerate(order):
        cur = v[key]
        # float equality is the right test: identical arithmetic on identical
        # inputs yields bit-identical results, and near-but-not-equal values
        # are genuinely different schedules that should not be merged.
        if prev_val is not None and cur == prev_val:
            out[v['team']] = prev_rank          # tie -> same rank
        else:
            prev_rank = i + 1                   # skip the consumed slots
            prev_val = cur
            out[v['team']] = prev_rank
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    sports = [args.sport.upper()] if args.sport else SPORTS

    # ══ 2026-09-30 · STAMP refreshed_at EXPLICITLY ══
    # The column is `timestamptz DEFAULT now()`, and a DEFAULT only fires on
    # INSERT. These writes are upserts, so every re-run took the UPDATE path
    # and refreshed_at stayed pinned to the row's first insert. Verified: after
    # a run that moved Florida's SOR from +1.000 to +0.464, every NCAAF row
    # still read refreshed_at = 2026-09-26. Values fresh, timestamp four days
    # stale -- so nothing could tell whether this job had ever run again, which
    # is precisely how it sat unscheduled and unnoticed since 09-26.
    #
    # Third instance of this exact bug tonight, after both pick writers
    # (recompute_{nfl,ncaaf}_primary_play). Worth treating as a pattern: a
    # DEFAULT now() column plus an upsert equals a timestamp that lies.
    _NOW = datetime.now(timezone.utc).isoformat()
    produced: dict[str, set] = {}
    payload = []
    for sport in sports:
        vals = compute(sport, args.season)
        if not vals:
            print(f'  {sport}: no decided games for {args.season} — skipped')
            continue
        produced[sport] = {v['team'] for v in vals}
        rk_sos = _ranked(vals, 'sos')
        rk_sor = _ranked(vals, 'sor')
        size = len(vals)
        for v in vals:
            payload.append({
                'sport': sport, 'team': v['team'], 'season': args.season,
                'stat_key': 'sos', 'raw_value': v['sos'],
                'rank': rk_sos[v['team']], 'league_size': size,
                'direction': 'higher', 'display_label': 'Strength of Sched',
                'unit': '', 'refreshed_at': _NOW,
            })
            payload.append({
                'sport': sport, 'team': v['team'], 'season': args.season,
                'stat_key': 'sor', 'raw_value': v['sor'],
                'rank': rk_sor[v['team']], 'league_size': size,
                'direction': 'higher', 'display_label': 'Strength of Record',
                'unit': '', 'refreshed_at': _NOW,
            })
        top = sorted(vals, key=lambda x: -x['sor'])[:3]
        print(f'  {sport}: {size} teams · best SOR ' +
              ', '.join(f"{t['team']} {t['sor']:+.3f}" for t in top))

    print(f'\n  rows to write: {len(payload)}')
    if args.dry_run:
        print('  DRY RUN — no writes.')
        return
    if not payload:
        return

    written = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/{TARGET}'
            '?on_conflict=sport,team,season,stat_key',
            headers=H_W, json=chunk, timeout=120)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ write -> {r.status_code}: {(r.text or "")[:240]}')

    # Read back — a 2xx is not proof the rows landed.
    chk = requests.get(f'{SB}/rest/v1/{TARGET}',
                       headers={**H, 'Prefer': 'count=exact', 'Range': '0-0'},
                       params={'select': 'team', 'season': f'eq.{args.season}',
                               'stat_key': 'in.(sos,sor)'}, timeout=60)
    landed = (chk.headers.get('content-range') or '').split('/')[-1]
    print(f'  wrote {written}/{len(payload)} · rows in table: {landed}')

    # ══ 2026-09-30 · PRUNE TEAMS WE NO LONGER RATE ══
    # An upsert writes; it never removes what it stopped producing. When
    # MIN_GAMES rose 2 -> 3 the NCAAF pool went 152 -> 137, and the 16 dropped
    # FCS rows STAYED in the table carrying their old values — Duquesne still
    # sat at SOR -1.000, bottom of college football, from a two-game sample we
    # had just decided not to rate at all. A reader cannot tell a live row from
    # an abandoned one, and the view surfaces both identically.
    #
    # Only prunes sports that computed successfully THIS run (`produced`), so a
    # sport skipped because its season has not started keeps whatever it had
    # instead of being wiped. Same discipline as
    # compute_surface_records.prune_stale.
    pruned = 0
    for sport, keep in produced.items():
        cur = page(TARGET, {'sport': f'eq.{sport}',
                            'season': f'eq.{args.season}',
                            'stat_key': 'in.(sos,sor)', 'select': 'team'})
        orphans = sorted({r['team'] for r in cur} - keep)
        if not orphans:
            continue
        shown = ', '.join(orphans[:6]) + (' …' if len(orphans) > 6 else '')
        print(f'  {sport}: pruning {len(orphans)} team(s) no longer rated ({shown})')
        for t in orphans:
            # Deleted one at a time on purpose: in.() cannot express names
            # containing , ( ) — 'Miami (OH)' has already broken an in.()
            # filter once in this codebase (shadow_v2_backtest).
            d = requests.delete(f'{SB}/rest/v1/{TARGET}', headers=H_W,
                                params={'sport': f'eq.{sport}',
                                        'season': f'eq.{args.season}',
                                        'stat_key': 'in.(sos,sor)',
                                        'team': f'eq.{t}'}, timeout=60)
            if d.status_code in (200, 204):
                pruned += 1
            else:
                print(f'    ! delete {t} -> {d.status_code}')
    if pruned:
        print(f'  pruned {pruned} orphan row(s)')


if __name__ == '__main__':
    main()
