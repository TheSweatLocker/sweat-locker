#!/usr/bin/env python3
"""The record-weighted panel, making a pick on every game, in shadow.

== 2026-10-14 - WHY THIS EXISTS ==
ANDY's engine, in his words: a board of panelists, each with their own data and
their own take, each making a pick; the engine weighs those picks BY THEIR
RECORDS alongside money flow, situation, tendencies, rolling stats, matchup,
injuries, situational records, regression factors and the market, and makes a
selection from weighted inputs.

ANDY: "we wont know until we build and monitor surely its better than what we
are doing i cant stress this enough we need to close all gaps, build/fix whats
needed and get there."

He is right, and the argument I gave against building it was weaker than his.
The engine ALREADY weights its panelists -- uniformly -- and that uniform weight
is shared by NFL v4 at -9.6% ROI and by duplicates that inflate agreement.
Weight 0 for a duplicate and 0.93 for a measured loser is strictly better than
1.0 for both. And a scheme that is never run cannot earn the forward record
that would justify promoting it.

So this runs it, writes nothing published, and grades itself.

== HOW A PICK IS MADE ==
Direction-only votes, weighted by record. For each panelist with a number:

    edge  = panelist_number - market_number
    vote  = +1 if edge >= EDGE_MIN, -1 if edge <= -EDGE_MIN, else ABSTAIN
    net   = sum(weight * vote) / sum(weight of those who voted)

net lands in [-1, +1]: +1 is every voter on HOME/OVER, 0 is a split. A pick
needs |net| >= NET_THRESH and at least MIN_VOTERS voters; otherwise PASS.

VOTES ARE DIRECTION-ONLY ON PURPOSE. Scaling a vote by how far a model
disagrees sounds better and is how a model that is merely far from the line
starts dominating the panel -- NFL's projected_total sits near 44.5 and would
have shouted on every game while being ANTI-informative (slope -0.908,
CI[-1.553,-0.242]). A slope is not a betting rule and neither is a big
disagreement; the side is what gets bet.

PASS IS STORED. The discipline is the product as much as the hit rate, and an
engine judged only on the games it chose to play cannot be compared to one that
plays everything.

== TWO VERSIONS, GRADED SEPARATELY ==
  panel_only_v1            the weighted panel alone
  panel_plus_confluence_v1 the same panel, then required to agree with the
                           existing signal_confluence_net (money flow,
                           situation, tendencies, injuries). Disagreement
                           becomes PASS.

Andy's design has the engine combining both, and nobody knows which is better,
so both are written and both accumulate a record. That is the monitoring half
of "build and monitor".

Each row also stores the existing engine's confluence_net on the same game, so
the comparison is head to head rather than the new thing judged alone.

NOTHING IS PUBLISHED. engine_shadow_picks only.

USAGE
    python shadow_panelist_engine.py --backfill --dry-run
    python shadow_panelist_engine.py --backfill
    python shadow_panelist_engine.py --today
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict

import requests

import build_panelist_records as B
import market_line as ML
import panelist_registry as REG

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

V_PANEL = 'panel_only_v1'
V_BLEND = 'panel_plus_confluence_v1'
#: Weighted consensus needed to make a pick rather than PASS.
NET_THRESH = 0.34
#: Fewer than this many voters is not a panel.
MIN_VOTERS = 2
SPORTS = ['NFL', 'NCAAF', 'NHL', 'NBA', 'MLB', 'NCAAB']


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=180)
        if r.status_code not in (200, 206):
            return out, r.status_code
        b = r.json()
        if not isinstance(b, list):
            return out, r.status_code
        out += b
        if len(b) < 1000:
            return out, 200
        off += 1000


def load_weights():
    rows, sc = page('panelist_weights', {'select': '*'})
    if sc not in (200, 206):
        print(f'  panelist_weights unreadable (HTTP {sc}) — migration '
              f'20261014c not applied yet. Falling back to REGISTRY-BLOCKED '
              f'uniform: every seatable panelist at 1.0, every duplicate at 0. '
              f'Record-based tilt is unavailable, but a duplicate must never '
              f'vote just because the weights table is missing.')
        return {}, False
    w = {(r['sport'], r['panelist'], r['market']): float(r['weight'])
         for r in rows}
    print(f'  loaded {len(w)} weights '
          f'({sum(1 for v in w.values() if v == 0)} blocked at 0)')
    return w, True


def extra_predictions(sport, market):
    """Panelists that live in panelist_predictions rather than a ctx column."""
    rows, sc = page('panelist_predictions',
                    {'select': 'game_id,panelist,prediction',
                     'sport': f'eq.{sport}', 'market': f'eq.{market}'})
    out = defaultdict(dict)
    for r in rows:
        v = B._f(r.get('prediction'))
        if v is not None:
            out[str(r.get('game_id'))][r.get('panelist')] = v
    return out


def load_games(sport, mode):
    """Context rows paired with their result row, INCLUDING unplayed games.

    ══ 2026-10-14 · WHY NOT build_panelist_records.joined() ══
    joined() requires home_score and away_score, by design -- it grades. Using
    it here meant `--today` saw only COMPLETED games and wrote 316 rows marked
    pregame=true for games already played. That is hindsight stamped as a live
    record, which is precisely what the pregame flag exists to prevent, and it
    would have made the shadow engine's "live" record the one number nobody
    could trust.

    So this loader keeps unplayed games and reports which bucket each row is
    in. --today takes the UNPLAYED ones; --backfill takes the graded ones.
    """
    ctx, _ = page(B.CONTEXT[sport], {'select': '*'})
    res, _ = page(B.RESULTS[sport], {'select': '*'})
    by_id, by_key = {}, {}
    for r in res:
        if r.get('game_id'):
            by_id[str(r['game_id'])] = r
        by_key[(str(r.get('game_date'))[:10], str(r.get('home_team')),
                str(r.get('away_team')))] = r
    played, unplayed = [], []
    for c in ctx:
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        hs = B._f((g or {}).get('home_score'))
        as_ = B._f((g or {}).get('away_score'))
        # The market lives on the results row where there is one, else on ctx.
        src = g if g else c
        if hs is not None and as_ is not None:
            played.append((c, src, hs, as_))
        else:
            unplayed.append((c, src, None, None))
    print(f'  {sport}: {len(played)} graded, {len(unplayed)} unplayed')
    return unplayed if mode == 'today' else played


def decide(votes, weights_used):
    """votes: {panelist: +1/-1}. -> (net, voters, total_weight)."""
    tw = sum(weights_used[p] for p in votes)
    if tw <= 0:
        return 0.0, 0, 0.0
    net = sum(weights_used[p] * v for p, v in votes.items()) / tw
    return net, len(votes), tw


def run(sport, mode, dry, weights, have_weights):
    emin = B.EDGE_MIN[sport]
    rows = load_games(sport, mode)
    if not rows:
        kind = 'unplayed' if mode == 'today' else 'graded'
        print(f'{sport}: no {kind} games — nothing to decide')
        return
    extra_by_market = {m: extra_predictions(sport, m)
                       for m in ('margin', 'total')}
    payload = []
    stats = defaultdict(int)

    for market in ('margin', 'total'):
        if market == 'margin' and sport not in B.REAL_SPREAD:
            continue
        tbl = (B.MARGIN_PANELISTS if market == 'margin'
               else B.TOTAL_PANELISTS).get(sport) or {}
        extra = extra_by_market[market]
        for c, g, hs, as_ in rows:
            gid = str(c.get('game_id'))
            if market == 'margin':
                cs = B._f(g.get('close_spread'))
                if cs is None:
                    continue
                home_line = -cs if sport == 'NFL' else cs
                mkt = -home_line
                pos, neg = 'HOME', 'AWAY'
            else:
                mkt = B._f(g.get('close_total'))
                pos, neg = 'OVER', 'UNDER'
            if mkt is None:
                continue

            cand = {name: acc(c) for name, acc in tbl.items()}
            cand.update(extra.get(gid, {}))

            votes, used, detail = {}, {}, {}
            for name, pv in cand.items():
                if pv is None:
                    continue
                if have_weights:
                    # Weights loaded: an unknown panelist is BLOCKED rather
                    # than silently seated at 1.0.
                    wgt = weights.get((sport, name, market), 0.0)
                else:
                    # No weights table. Fall back to registry-blocked uniform
                    # rather than plain uniform: the record-based tilt is
                    # small (0.93-1.03), but seating a DUPLICATE is the error
                    # this whole exercise exists to remove, and
                    # panelist_registry knows which those are without any
                    # migration. A panelist the registry has never heard of
                    # keeps 1.0 -- absence of evidence is not evidence of
                    # badness.
                    known = REG.kind(sport, name) is not None
                    wgt = 1.0 if (not known or REG.may_vote(sport, name))                         else 0.0
                if wgt <= 0:
                    detail[name] = {'pred': pv, 'weight': 0.0,
                                    'vote': 'BLOCKED'}
                    continue
                edge = pv - mkt
                if abs(edge) < emin:
                    detail[name] = {'pred': pv, 'weight': wgt,
                                    'vote': 'ABSTAIN', 'edge': round(edge, 3)}
                    continue
                v = 1 if edge > 0 else -1
                votes[name] = v
                used[name] = wgt
                detail[name] = {'pred': pv, 'weight': wgt,
                                'vote': pos if v > 0 else neg,
                                'edge': round(edge, 3)}

            net, voters, tw = decide(votes, used) if votes else (0.0, 0, 0.0)
            conf = B._f(c.get('signal_confluence_net'))

            if voters < MIN_VOTERS or abs(net) < NET_THRESH:
                side_panel = 'PASS'
            else:
                side_panel = pos if net > 0 else neg
            # blend: the panel must not contradict the existing confluence net
            side_blend = side_panel
            if side_panel != 'PASS' and conf is not None and conf != 0:
                conf_side = pos if conf > 0 else neg
                if market == 'margin' and conf_side != side_panel:
                    side_blend = 'PASS'
            stats[f'{market}:{side_panel}'] += 1

            base = {'sport': sport, 'game_id': gid,
                    'game_date': str(c.get('game_date'))[:10],
                    'market': market, 'net': round(net, 4),
                    'total_weight': round(tw, 4), 'voters': voters,
                    'detail': detail, 'market_line': mkt,
                    'confluence_net': conf,
                    'pregame': False if mode == 'backfill' else True}
            payload.append(dict(base, side=side_panel,
                                engine_version=V_PANEL))
            payload.append(dict(base, side=side_blend,
                                engine_version=V_BLEND))

    print(f'\n{sport}: {len(payload)//2} game-markets decided')
    for k in sorted(stats):
        print(f'    {k:<16}{stats[k]}')
    if dry or not payload:
        print(f'  {"DRY RUN — would write" if dry else "nothing to write"} '
              f'{len(payload)} rows')
        return
    wrote = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(f'{SB}/rest/v1/engine_shadow_picks', headers=H_WRITE,
                          params={'on_conflict':
                                  'sport,game_id,market,engine_version'},
                          data=json.dumps(chunk), timeout=180)
        if r.status_code not in (200, 201, 204):
            print(f'    WRITE FAILED {r.status_code}: {r.text[:220]}')
            continue
        wrote += len(chunk)
    print(f'  wrote {wrote} rows')


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--backfill', action='store_true')
    g.add_argument('--today', action='store_true')
    ap.add_argument('--sport', choices=sorted(B.RESULTS))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    mode = 'backfill' if a.backfill else 'today'
    print(f'SHADOW PANELIST ENGINE — direction-only votes, weighted by record')
    print(f'pick needs |net| >= {NET_THRESH} and >= {MIN_VOTERS} voters, '
          f'else PASS. Publishes nothing.')
    weights, have = load_weights()
    for s in ([a.sport] if a.sport else SPORTS):
        try:
            run(s, mode, a.dry_run, weights, have)
        except Exception as e:                                 # noqa: BLE001
            print(f'\n{s}: FAILED {type(e).__name__}: {str(e)[:140]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
