#!/usr/bin/env python3
"""The Fade — track what fading the public ATS side actually does. All sports.

WHY (2026-10-07)
----------------
Andy: "I want to get this going for all sports, that's the point, we have the
money flow information. It doesn't even have to be proven, we can just label
what it is — The Fade — and what it does. We are just tracking the record
honestly."

That is the right frame and it changes what this is. This is NOT a claim of
edge. It is a graded record of a rule someone else's money defines, published
whatever it says. Same discipline as publishing our own losses, except the
picks are not ours, so there is no incentive to flatter them.

THE RULE, stated plainly so a user can check it:
  * ATS markets only (spread / run line). NOT moneyline — measured, the public
    ML side WINS: NCAAF 84.6-92.6% and MLB 58.4-61.8% at >=70% of tickets,
    because the public backs heavy favourites and favourites win games. That
    is the public being right at a bad price, which a hit-rate fade cannot
    see. Fading it would be a disaster.
  * Take the side holding >= THRESHOLD of TICKETS (not handle), as measured by
    oddscrowd — the ONLY one of the four archive sources keyed on the actual
    pick_side. fr/ftp/cz key on sharp_side_norm and are excluded; see the
    SOURCES comment. This cost the first published record 0.3pp in MLB
    (+5.63% -> +5.31%) and more elsewhere, which is the point of fixing it.
  * Bet the other side. Score at -110. Pushes are 0 units, not losses.
  * Latest pre-game capture per (game, side) — the archive scrapes a game
    repeatedly and counting every capture would weight busy games.

THE NFL GAME_ID TRAP — this is why NFL looked like missing data.
public_splits_archive keys NFL by a 32-char hash while nfl_game_results uses
season_week_away_home:

    splits            fc362aff0d889ec52d358307a70c32ed
    nfl_game_results  2024_18_SEA_LA
    overlap           ZERO

19,512 NFL split rows existed and joined to nothing. I reported "we have no
NFL split data" rather than asking why the join was empty. nfl_game_context
carries BOTH the hash and the teams/date, so it bridges — all 77 NFL split
game_ids resolve through it. Every sport here goes through the same
bridge-then-fallback path so this cannot silently drop a sport again, and the
join loss is REPORTED at every hop.

    python compute_fade_records.py                 # report only
    python compute_fade_records.py --apply         # write surface_records
    python compute_fade_records.py --threshold 60
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

JUICE = -110
ATS_MARKETS = ('rl', 'spread', 'ats')

# sport -> (results table, context table used to bridge a hash id)
SPORTS = {
    'MLB':   ('mlb_game_results',   'mlb_game_context'),
    'NFL':   ('nfl_game_results',   'nfl_game_context'),
    'NCAAF': ('ncaaf_game_results', 'ncaaf_game_context'),
    'NHL':   ('nhl_game_results',   'nhl_game_context'),
    'NBA':   ('nba_game_results',   'nba_game_context'),
}
# ONE source, and the reason is not preference — it is what the columns mean.
#
# public_splits_archive has four bets% columns and only ONE of them indexes the
# public's side. archive_public_splits.py builds the archive key per source:
#
#   latest_oc   key = (game_id, market, pick_side)        <- the actual side
#   latest_fr   key = (game_id, market, sharp_side_norm)  <- the SHARP side
#   latest_ftp  key = (game_id, market, sharp_side_norm)  <- the SHARP side
#   latest_cz   key = (game_id, market, sharp_side_norm)  <- the SHARP side
#
# So for fr/ftp/cz the row's pick_side is whichever side that feed called
# sharp, and the pct is that side's ticket share. A >=65% reading there is not
# "the public is piled on this side", it is "the sharp side also happens to
# hold most tickets" — consensus, the opposite of what a fade means. Their
# means say the same thing: fr 41.2, ftp 43.2 (a sharp side normally holds a
# MINORITY of tickets) against oc 63.6.
#
# cz is worse than mislabelled. It reads cleatz_signals.sharp_bets_pct, which
# lands on exactly 100.0 in 4,297 of 28,211 rows (15.2%) and on 0 never, while
# fr/ftp/oc hit 100% zero times across 139,188 values. Not a two-sided share.
#
# Averaging them was producing readings like Liberty 71.3% from cz 100.0 +
# fr 57.0 + ftp 57.0 — a sharp-side number dragging a public-side number over
# the threshold. Same shape as returning_rush_yards holding totalRushingPPA:
# the column name promised one quantity and held another.
#
# oc is live (last capture 2026-10-06 23:26) and archives BOTH sides, so it
# alone supports the rule as stated.
SOURCES = [('oc', 'oc_bets_pct')]

# Kept for display only — shown beside a play as context, never in the
# threshold. Labelled as sharp-side readings because that is what they are.
SHARP_SIDE_SOURCES = [('ftp', 'ftp_bets_pct'), ('cz', 'cz_bets_pct'),
                      ('fr', 'fr_bettors_pct')]


def page(table, params):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                         timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code}: {r.text[:160]}')
            return out
        chunk = r.json()
        if not isinstance(chunk, list):
            print(f'  ! {table} returned a non-list payload')
            return out
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def latest_readings(rows):
    """{(game_id, side): {'pcts': [(src, val)], 'line':…, 'odds':…, 'at':…}}

    Per SOURCE, the latest capture that actually HAS a value — not the latest
    row, then read the column.

    That distinction was silently shrinking everything. The four scrapers run
    independently and each archive row carries whatever that pull managed to
    get, so the most recent row for a game very often has oc=NULL because that
    pull only reached fr/cz. Deduping to the newest row and then reading
    oc_bets_pct therefore discarded a real oc reading that existed one capture
    earlier. Dodgers @ Braves on 2026-10-06 is the worked example: oc had the
    Dodgers at 75.0% of tickets, the newest row for that (game, side) had
    oc=NULL, and the game vanished from both the live list and the record.

    Same failure family as the NFL game_id mismatch — a join/dedup step
    dropping population quietly, which reads downstream as "small sample"
    rather than "we threw rows away."
    """
    out = {}
    for r in rows:
        k = (str(r.get('game_id')), str(r.get('pick_side')))
        at = str(r.get('captured_at'))
        slot = out.setdefault(k, {'pcts': {}, 'line': None, 'odds': None,
                                  'at': None})
        for name, col in SOURCES:
            try:
                v = float(r.get(col))
            except (TypeError, ValueError):
                continue
            prev = slot['pcts'].get(name)
            if prev is None or at > prev[1]:
                slot['pcts'][name] = (v, at)
        if r.get('current_line') is not None and (
                slot['at'] is None or at > slot['at']):
            slot['line'] = r.get('current_line')
            slot['odds'] = r.get('current_odds')
            slot['at'] = at
    # flatten to the shape callers want, dropping keys with no reading at all
    return {k: {'pcts': [(n, v) for n, (v, _a) in sorted(s['pcts'].items())],
                'line': s['line'], 'odds': s['odds'],
                'at': s['at'] or ''}
            for k, s in out.items() if s['pcts']}


def load_sport(sport):
    """(by_gameid, by_teams, bridge) for one sport. Each maps to an ATS result.

    by_gameid   result table's own id -> (spread_result, game_date)
    by_teams    (date, home, away)    -> (spread_result, game_date)
    bridge      context hash id       -> (date, home, away)
    """
    res_tbl, ctx_tbl = SPORTS[sport]
    by_gid, by_teams = {}, {}
    for g in page(res_tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                      'spread_result'}):
        sr = str(g.get('spread_result') or '')
        if sr not in ('home_covered', 'away_covered', 'push'):
            continue
        d = str(g.get('game_date'))[:10]
        rec = (sr, d)
        if g.get('game_id'):
            by_gid[str(g['game_id'])] = rec
        by_teams[(d, str(g.get('home_team')), str(g.get('away_team')))] = rec
    bridge = {}
    for z in page(ctx_tbl, {'select': 'game_id,game_date,home_team,away_team'}):
        if z.get('game_id'):
            bridge[str(z['game_id'])] = (str(z.get('game_date'))[:10],
                                         str(z.get('home_team')),
                                         str(z.get('away_team')))
    return by_gid, by_teams, bridge


def resolve(gid, by_gid, by_teams, bridge):
    """Direct id first, then the context bridge. Returns (result, date, how)."""
    hit = by_gid.get(gid)
    if hit:
        return hit[0], hit[1], 'direct'
    key = bridge.get(gid)
    if key:
        hit = by_teams.get(key)
        if hit:
            return hit[0], hit[1], 'bridged'
        return None, None, 'bridged-but-ungraded'
    return None, None, 'no-context-row'


def unit(fade_won):
    if fade_won is None:
        return 0.0
    return (100.0 / abs(JUICE)) if fade_won else -1.0


def publish_today(thr: float, apply: bool) -> int:
    """Today's qualifying fades, written where the app can read them.

    The record answers "what has this done"; this answers "what does it say
    tonight". Both apply the SAME rule from the same file — a second
    implementation of the threshold would drift from the one being graded,
    and then the published record would not describe the published plays.

    Writes to jerry_cache (the established app-read payload path) rather than
    a new table, so this needs no migration.
    """
    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    horizon = (today + dt.timedelta(days=2)).isoformat()
    print(f'=== The Fade · today\'s qualifying plays · '
          f'{today.isoformat()}..{horizon} ===\n')

    # Captures for tonight's games can land yesterday, so reach back a day.
    since = (today - dt.timedelta(days=1)).isoformat()
    splits = [r for r in page('public_splits_archive',
                              {'select': 'game_id,sport,market,pick_side,'
                                         'captured_at,current_line,current_odds,'
                                         'cz_bets_pct,fr_bettors_pct,'
                                         'ftp_bets_pct,oc_bets_pct',
                               'captured_at': f'gte.{since}'})
              if str(r.get('market')) in ATS_MARKETS]
    print(f'ATS split rows captured since {since}: {len(splits)}')

    by_sport = collections.defaultdict(list)
    for r in splits:
        by_sport[str(r.get('sport'))].append(r)

    plays = []
    for sport in sorted(by_sport):
        if sport not in SPORTS:
            continue
        _res_tbl, ctx_tbl = SPORTS[sport]
        meta = {}
        for z in page(ctx_tbl, {'select': 'game_id,game_date,home_team,'
                                          'away_team',
                                'game_date': f'gte.{today.isoformat()}'}):
            meta[str(z.get('game_id'))] = z

        latest = latest_readings(by_sport[sport])

        for (gid, side), slot in latest.items():
            g = meta.get(gid)
            if not g:
                continue
            gdate = str(g.get('game_date'))[:10]
            if not (today.isoformat() <= gdate <= horizon):
                continue
            pcts = slot['pcts']
            if not pcts:
                continue
            mean = sum(v for _n, v in pcts) / len(pcts)
            if mean < thr:
                continue
            fade_side = 'AWAY' if side == 'HOME' else 'HOME'
            fade_team = (g.get('away_team') if fade_side == 'AWAY'
                         else g.get('home_team'))
            public_team = (g.get('home_team') if side == 'HOME'
                           else g.get('away_team'))
            plays.append({
                'sport': sport,
                'game_date': gdate,
                'game_id': gid,
                'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
                'public_on': public_team,
                'public_ticket_pct': round(mean, 1),
                'sources': {n: v for n, v in pcts},
                'source_count': len(pcts),
                'fade_side': fade_side,
                'fade_team': fade_team,
                'public_line': slot['line'],
                'public_odds': slot['odds'],
                'price': JUICE,
                'captured_at': slot['at'],
            })

    plays.sort(key=lambda p: (-p['public_ticket_pct'], p['sport']))
    print(f'\nqualifying plays (public >= {thr:.0f}% of tickets): {len(plays)}\n')
    for p in plays:
        print(f"  {p['sport']:<6} {p['game_date']}  {p['matchup'][:34]:34s} "
              f"public {p['public_ticket_pct']:5.1f}% on {str(p['public_on'])[:18]:18s} "
              f"-> FADE {str(p['fade_team'])[:18]:18s} "
              f"({p['source_count']} src)")
    if not plays:
        print('  (none — either no games in window or nobody is that lopsided)')

    if not apply:
        print('\n  report only — re-run with --apply to publish')
        return 0

    key = f'fade_public_{today.isoformat()}'
    payload = {
        'cache_key': key,
        'sport': 'MULTI',
        'game_id': key,
        'narrative': (f'{len(plays)} game(s) where the public holds at least '
                      f'{thr:.0f}% of ATS tickets.'),
        'data': {
            'rule': (f'ATS only. Side holding >= {thr:.0f}% of public TICKETS '
                     f'is faded. Graded at {JUICE}. Pushes are 0 units.'),
            'threshold_pct': thr,
            'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'plays': plays,
        },
        'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    r = requests.post(f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
                      headers=H_W, timeout=60, data=json.dumps(payload))
    if r.status_code in (200, 201, 204):
        print(f'\n  published {len(plays)} play(s) to jerry_cache:{key}')
        return 0
    print(f'\n  publish failed {r.status_code}: {r.text[:300]}')
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, default=65.0,
                    help='minimum %% of TICKETS on the public side')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--today', action='store_true',
                    help="publish today's qualifying plays instead of the record")
    args = ap.parse_args()
    thr = args.threshold

    if args.today:
        return publish_today(thr, args.apply)

    print(f'=== The Fade · ATS only · public >= {thr:.0f}% of tickets · '
          f'{"APPLY" if args.apply else "REPORT"} ===\n')

    splits = [r for r in page('public_splits_archive',
                              {'select': 'game_id,sport,market,pick_side,'
                                         'captured_at,cz_bets_pct,'
                                         'fr_bettors_pct,ftp_bets_pct,'
                                         'oc_bets_pct'})
              if str(r.get('market')) in ATS_MARKETS]
    print(f'ATS split rows: {len(splits)}')
    by_sport = collections.defaultdict(list)
    for r in splits:
        by_sport[str(r.get('sport'))].append(r)
    print(f'  by sport: {dict((k, len(v)) for k, v in sorted(by_sport.items()))}\n')

    rows_out = []
    for sport in sorted(by_sport):
        if sport not in SPORTS:
            print(f'{sport}: no results table registered — skipped\n')
            continue
        by_gid, by_teams, bridge = load_sport(sport)

        # latest NON-NULL reading per source per (game, side) — see
        # latest_readings(); deduping to the newest row first threw away real
        # oc values whenever that pull had not reached oddscrowd.
        latest = latest_readings(by_sport[sport])

        how = collections.Counter()
        graded = []
        for (gid, side), slot in latest.items():
            sr, gdate, mode = resolve(gid, by_gid, by_teams, bridge)
            how[mode] += 1
            if sr is None:
                continue
            pcts = slot['pcts']
            if not pcts:
                continue
            if sr == 'push':
                fade = None
            else:
                public_won = ((sr == 'home_covered') if side == 'HOME'
                              else (sr == 'away_covered'))
                fade = not public_won
            mean = sum(v for _n, v in pcts) / len(pcts)
            graded.append({'date': gdate, 'mean': mean, 'fade': fade,
                           'per_src': pcts})

        qual = [g for g in graded if g['mean'] >= thr]
        w = sum(1 for g in qual if g['fade'] is True)
        l = sum(1 for g in qual if g['fade'] is False)
        p = sum(1 for g in qual if g['fade'] is None)
        n = w + l
        u = sum(unit(g['fade']) for g in qual)
        print(f'{sport}')
        print(f'  latest-capture game/side rows : {len(latest)}')
        print(f'  id resolution                 : {dict(how)}')
        print(f'  graded                        : {len(graded)}')
        print(f'  qualifying (>= {thr:.0f}% tickets)   : {len(qual)}')
        if n:
            print(f'  THE FADE  {w}-{l}' + (f'-{p}' if p else '')
                  + f'   {w/n*100:.1f}%   {u:+.2f}u   '
                    f'ROI {u/len(qual)*100:+.1f}%   n={n}')
            dates = sorted(g['date'] for g in qual if g['date'])
            rows_out.append({
                'sport': sport,
                'surface': 'fade_public_ats',
                'window_key': f'tickets_{int(thr)}pct',
                'wins': w, 'losses': l, 'pushes': p,
                'picks_count': len(qual),
                'hit_rate': round(w / n * 100, 2),
                'units_net': round(u, 2),
                'roi_pct': round(u / len(qual) * 100, 2),
                'epoch_start': dates[0] if dates else None,
                'last_pick_date': dates[-1] if dates else None,
                'last_computed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        else:
            print(f'  THE FADE  no graded qualifying games')
        print()

    print(f'{"="*70}')
    print('Breakeven at -110 is 52.38%. Pushes count 0 units, not losses.')
    print('This is a TRACKED RECORD of a public-money rule, not a claim of')
    print('edge. Small n is labelled, never hidden.')
    print(f'{"="*70}\n')

    # A sport that no longer has qualifying games must lose its row, not keep
    # an orphan. The first run published NHL 5-9 / -31.82% off sharp-side
    # columns; on the corrected source NHL has n=0, and an upsert alone would
    # have left that number sitting in the app with a stale last_computed_at.
    # The writer owns the whole set it publishes, so it reconciles both ways.
    live = {str(r0['sport']) for r0 in rows_out}
    existing = {str(r0['sport']) for r0 in page(
        'surface_records', {'select': 'sport',
                            'surface': 'eq.fade_public_ats',
                            'window_key': f'eq.tickets_{int(thr)}pct'})}
    orphans = sorted(existing - live)

    if not args.apply:
        print(f'{len(rows_out)} surface_records row(s) would be written. '
              f'Re-run with --apply.')
        if orphans:
            print(f'  and {len(orphans)} stale row(s) would be removed '
                  f'(no qualifying games): {", ".join(orphans)}')
        return 0
    if not rows_out:
        print('nothing to write')
        return 0
    r = requests.post(f'{SB}/rest/v1/surface_records'
                      f'?on_conflict=sport,surface,window_key',
                      headers=H_W, timeout=60, data=json.dumps(rows_out))
    if r.status_code not in (200, 201, 204):
        print(f'write failed {r.status_code}: {r.text[:300]}')
        return 1
    print(f'wrote {len(rows_out)} surface_records row(s)')

    for sp in orphans:
        d = requests.delete(f'{SB}/rest/v1/surface_records', headers=H_W,
                            timeout=60,
                            params={'sport': f'eq.{sp}',
                                    'surface': 'eq.fade_public_ats',
                                    'window_key': f'eq.tickets_{int(thr)}pct'})
        ok = d.status_code in (200, 204)
        print(f'  removed stale {sp} row: '
              f'{"ok" if ok else f"FAILED {d.status_code} {d.text[:120]}"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
