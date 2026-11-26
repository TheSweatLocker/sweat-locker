# -*- coding: utf-8 -*-
"""Regression for public_receipts wager identity and the write screen.

== WHY THIS EXISTS (2026-11-25) ==========================================
46 wagers were in `public_receipts` twice, 48 extra GRADED rows, and the
guard written to stop exactly that was dead on the rows that did it:

  * `_bet_key` returned None whenever `game_id` was NULL, on the stated
    grounds that such a row is a parlay slip. True of `ledger`, false of
    the Sweat Card -- 284 of the 400 newest `sweat_card` receipts carry a
    NULL game_id, because the potd, dotd, prop and MLB-era total rows all
    do. So the wager-level check never ran on them.
  * the copies disagree on `market` ('potd' beside 'total', 'ml' beside
    'dotd'), so even a working key compared two different markets.
  * VOID rows were skipped entirely, which licensed an endless re-mint:
    one wager was present EIGHTY-EIGHT times. It also made a dedupe
    impossible to land -- voiding a duplicate is how the record gets
    corrected, and the next capture wrote it straight back.
  * `backfill_public_receipts.py`, which runs nightly over a 14-day
    window and MADE these rows, re-implemented the screen instead of
    sharing it, and kept a two-value unpack of a helper returning four.

The cases below are the real rows. The one that must NOT merge is as
important as the ones that must: 2026-10-03 published two `Under 7.0`
bets on different games, both with NULL game_id, and merging them would
erase a real wager.

OFFLINE: imports the module and calls pure functions only.

    python test_receipt_bet_identity.py
"""
import os
import sys

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import public_receipt as P    # noqa: E402

fails = []


def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r'
          % (got, want))
    if not ok:
        fails.append(name)


def row(**kw):
    r = {'sport': 'NBA', 'surface': 'sweat_card', 'game_date': '2026-11-24',
         'market': 'total', 'pick_label': 'Under 213.5', 'pick_line': 213.5,
         'game_id': None, 'matchup': None, 'source_id': 'x'}
    r.update(kw)
    return r


print('== canonical market: a SLOT NAME IS NOT A MARKET ==')
chk('potd + Under reads as total',
    P.canon_market(row(market='potd', pick_label='Under 213.5')), 'total')
chk('potd + ML reads as ml',
    P.canon_market(row(market='potd', pick_label='Denver Nuggets ML')), 'ml')
chk('dotd + -1.5 reads as rl',
    P.canon_market(row(market='dotd', pick_label='Edmonton Oilers -1.5')), 'rl')
chk('a real market is untouched', P.canon_market(row(market='total')), 'total')
chk('spread is an alias of rl', P.canon_market(row(market='spread')), 'rl')

print('== label normalisation (writers decorate differently) ==')
chk('(Jerry 56/100) stripped',
    P.norm_bet_label(row(pick_label='Under 213.5 (Jerry 56/100)')),
    P.norm_bet_label(row(pick_label='Under 213.5')))
chk('(NHL LEAN 56/100) stripped',
    P.norm_bet_label(row(pick_label='Buffalo Sabres RL (NHL LEAN 56/100)')),
    P.norm_bet_label(row(pick_label='Buffalo Sabres RL')))
chk('proj tail stripped',
    P.norm_bet_label(row(pick_label='Tarik Skubal Over 7.5 Ks  ·  proj 8.8')),
    P.norm_bet_label(row(pick_label='Tarik Skubal Over 7.5 Ks')))

print('== identity ==')
# 2026-11-24: the duplicate that made the card count its only winner twice
a = row(market='potd', pick_label='Under 213.5 (Jerry 56/100)',
        matchup='Detroit Pistons @ Orlando Magic', source_id='potd:2026-11-24')
b = row(market='total', pick_label='Under 213.5', game_id='401909314',
        matchup='Detroit Pistons @ Orlando Magic', source_id='total:401909314')
gmap = {'detroit pistons @ orlando magic': '401909314'}
chk('11-24 potd and total are ONE wager',
    P.bet_identity(a, gid_of_matchup=gmap) == P.bet_identity(b, gid_of_matchup=gmap),
    True)
chk('and without the matchup map they would NOT match (two key spaces)',
    P.bet_identity(a) == P.bet_identity(b), False)

# 2026-10-03: two Under 7.0 bets, NULL game_id, different games
u1 = row(sport='MLB', game_date='2026-10-03', pick_label='Under 7.0',
         pick_line=7.0, matchup='Chicago White Sox @ Cleveland Guardians')
u2 = row(sport='MLB', game_date='2026-10-03', pick_label='Under 7.0',
         pick_line=7.0, matchup='New York Yankees @ Tampa Bay Rays')
chk('10-03 two Under 7.0 games stay APART',
    P.bet_identity(u1) == P.bet_identity(u2), False)

chk('a row with no game and no matchup cannot be judged',
    P.bet_identity(row()), None)
chk('ledger is never judged on its label',
    P.bet_identity(row(surface='ledger', game_id='g1',
                       pick_label='chalk_parlay (2 legs)')), None)
chk('a blank label cannot be judged',
    P.bet_identity(row(game_id='g1', pick_label='')), None)

print('== the screen (shared by capture and the nightly backfill) ==')


def screen(live, payload):
    """screen_for_write with the live read stubbed out."""
    maps, bets, voids, seen = {}, set(), set(), set()
    got, voidt = {}, set()
    grps = {(str(r.get('sport')), str(r.get('surface')),
             str(r.get('game_date'))) for r in payload}
    for g in grps:
        gm = {}
        for r in live + payload:
            mu = ' '.join(str(r.get('matchup') or '').split()).lower()
            if mu and r.get('game_id'):
                gm.setdefault(mu, str(r['game_id']))
        maps[g] = gm
        for r in live:
            if (str(r.get('sport')), str(r.get('surface')),
                    str(r.get('game_date'))) != g:
                continue
            voided = str(r.get('result') or '').lower() == 'void'
            if not voided:
                seen.add(g + (str(r.get('source_id')),))
            bk = P._bet_key(g[0], g[1], g[2], r, gm)
            if bk:
                bets.add(bk)
                if voided:
                    voids.add(bk)
                bt = P.bet_tuple_no_game(r, g[0], g[1], g[2])
                if bt:
                    got.setdefault(bt, set()).add(bk[-1])
                    if voided:
                        voidt.add(bt)
    keys = P.LiveKeys(seen, bets, voids, maps, got, voidt)
    P._live_keys = lambda rows: keys       # stub the live read
    return P.screen_for_write(payload, label='test', quiet=True)


_real = P._live_keys
try:
    # the 11-24 re-mint: the total is live, the backfill offers the potd copy
    fresh, st = screen([b], [a])
    chk('a potd copy of a live total is REFUSED', len(fresh), 0)
    chk('and counted as a wager duplicate', st['dup_bet'], 1)

    # the same wager twice inside ONE payload
    fresh, st = screen([], [b, dict(b, source_id='other')])
    chk('one payload carrying the bet twice writes it ONCE', len(fresh), 1)
    chk('and says so', st['in_batch'], 1)

    # a VOID row must not license a re-mint (this is what makes a dedupe stick)
    fresh, st = screen([dict(b, result='VOID')], [a])
    chk('an identical VOIDed wager is NOT re-minted', len(fresh), 0)
    chk('and the refusal is reported', st['void_blocked'], 1)

    # a replacement on the OTHER side is still allowed
    other = row(market='total', pick_label='Over 213.5', game_id='401909314',
                matchup='Detroit Pistons @ Orlando Magic', source_id='o2')
    fresh, st = screen([dict(b, result='VOID')], [other])
    chk('a replacement on the other side still writes', len(fresh), 1)

    # no game_id and no matchup, one known game for the tuple -> duplicate
    bare = row(market='ml', pick_label='San Diego Padres ML', pick_line=None,
               sport='MLB', game_date='2026-10-04',
               source_id='ml:|San Diego Padres ML')
    livep = row(market='ml', pick_label='San Diego Padres ML', pick_line=None,
                sport='MLB', game_date='2026-10-04', game_id='6da40e91',
                matchup='San Diego Padres @ Arizona', source_id='ml:6da40e91')
    fresh, st = screen([livep], [bare])
    chk('a bare re-mint with ONE known game is REFUSED', len(fresh), 0)

    # ...but with TWO known games it is ambiguous and must be written
    l1 = row(sport='MLB', game_date='2026-10-03', pick_label='Under 7.0',
             pick_line=7.0, game_id='g1', matchup='A @ B', source_id='t:g1')
    l2 = row(sport='MLB', game_date='2026-10-03', pick_label='Under 7.0',
             pick_line=7.0, game_id='g2', matchup='C @ D', source_id='t:g2')
    bare2 = row(sport='MLB', game_date='2026-10-03', pick_label='Under 7.0',
                pick_line=7.0, source_id='t:bare')
    fresh, st = screen([l1, l2], [bare2])
    chk('a bare row on a label spanning TWO games is written, not guessed',
        len(fresh), 1)
    chk('and flagged as ambiguous', st.get('ambiguous'), 1)

    # a genuinely new bet is never blocked
    new = row(market='ml', pick_label='Boston Celtics ML', game_id='401909999',
              matchup='Boston Celtics @ Miami Heat', source_id='ml:401909999')
    fresh, st = screen([b], [new])
    chk('a new wager writes normally', len(fresh), 1)
finally:
    P._live_keys = _real

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means the receipt duplicate guard is wrong, '
          'not that the test is stale.')
else:
    print('all receipt bet-identity cases pass')
raise SystemExit(1 if fails else 0)
