# -*- coding: utf-8 -*-
"""Regression for the Sweat Card dedupe keys and the CARD_FLOOR ladder.

== WHY THIS EXISTS (2026-11-25) ==========================================
Two bugs on one card, both of which this file pins:

  1. ONE BET TOOK TWO SLOTS for 25 days. `Under 213.5` was on the
     2026-11-24 card as the POTD and again as a STRONG total, so the
     card's only winner counted twice: published 2W-5L, really 1W-4L.
     The branch order in `_card_bet_key` is load-bearing and I got it
     wrong twice while fixing it -- checking game_id first breaks the
     2026-10-31 Marshall pair, checking the side first breaks the totals
     pair -- so all three cases are pinned here, including the one that
     must NOT merge.

  2. A CAP THAT ONLY SUBTRACTS. Dropping the DotD and capping props at 1
     is right on the measured ROI, but applied alone it put ten of the
     last thirty-one cards at 1-3 picks. Andy: "I dont wnat it to be
     short forever we will asses how short but we cnat just cit it off
     either." `apply_card_floor` refills from the bench in measured-ROI
     order, and the two-stage ladder is the part that must not regress:
     a losing surface may RESCUE a tiny card but must never PAD a merely
     short one, or we are back to the 48.1% card we started from.

Writing these caught a third live bug: `dedupe_one_bet_per_slot` in
generate_sweat_card.py sorted duplicate groups on TIER alone, so it kept
the STRONG total and dropped the POTD -- leaving the card holding the bet
with no item typed `potd`, which is the state that made the recap
reconciler append the same play twice on 2026-11-10. The standalone
dedupe script had already been fixed; the generator had not.

OFFLINE AND FAST BY DESIGN, so it can gate the card build rather than
run after it. Verified to import with both .env files moved aside
([[feedback_exit_1_after_0s_is_an_import_crash]]).

    python test_card_floor_and_dedupe.py
"""
import os
import sys

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_sweat_card as G    # noqa: E402

fails = []
def chk(name, got, want):
    ok = got == want
    print(('  PASS ' if ok else '  FAIL ') + name + '  got=%r want=%r' % (got, want))
    if not ok: fails.append(name)

print('== dedupe cases must still pass ==')
# 11-24: totals POTD + STRONG total, same game -> ONE bet
a = {'type':'potd','label':'Under 213.5','sport':'NBA','game':'Detroit Pistons @ Orlando Magic','game_id':'401909314','tier':'LEAN','source_key':'2026-11-24'}
b = {'type':'total','label':'Under 213.5','sport':'NBA','game':'Detroit Pistons @ Orlando Magic','game_id':'401909314','tier':'STRONG'}
chk('11-24 totals pair merges', len(G.dedupe_one_bet_per_slot([a,b],'t: ')), 1)
chk('11-24 keeps the POTD', G.dedupe_one_bet_per_slot([a,b],'t: ')[0]['type'], 'potd')
# 10-31 Marshall: POTD copy has NO game_id, composite copy does
c = {'type':'potd','label':'Marshall ML','sport':'NCAAF','game':'Marshall @ Georgia Southern','tier':'LEAN','source_key':'2026-10-31'}
d = {'type':'ml','label':'Marshall ML','sport':'NCAAF','game':'Marshall @ Georgia Southern','game_id':'g999','tier':'STRONG'}
chk('10-31 Marshall pair merges', len(G.dedupe_one_bet_per_slot([c,d],'t: ')), 1)
# 10-03: two DIFFERENT games, same under line -> must NOT merge
e = {'type':'total','label':'Under 7.0','sport':'MLB','game':'A @ B','game_id':'g1'}
f = {'type':'total','label':'Under 7.0','sport':'MLB','game':'C @ D','game_id':'g2'}
chk('10-03 two games stay apart', len(G.dedupe_one_bet_per_slot([e,f],'t: ')), 2)

print('== the floor ==')
def bench(**kw):
    b = {'fade':[], 'overflow':[], 'prop':[], 'dotd':[]}; b.update(kw); return b

card2 = [{'type':'ml','label':'Duke ML','sport':'NCAAB','game':'Duke @ Pitt','game_id':'g10','odds':-150},
         {'type':'prop','label':'Jenkins PRA UNDER 24.5','sport':'NBA','game':'X @ Y','game_id':'g11','odds':-120,'source_key':'Daniss Jenkins|pra|24.5'}]

# 1. floor fills from the FADE rung first even when other rungs are fuller
B = bench(fade=[{'type':'rl','label':'Nets +7.5','sport':'NBA','game':'Nets @ Heat','game_id':'g20','odds':-110}],
          prop=[{'type':'prop','label':'Queta PRA UNDER 22.5','sport':'NBA','game':'P @ Q','game_id':'g21','odds':-115,'source_key':'Neemias Queta|pra|22.5'}],
          dotd=[{'type':'DotD','label':'Detroit Mercy ML','sport':'NCAAB','game':'DM @ R','game_id':'g22','odds':160}])
out = G.apply_card_floor(list(card2), 4, B, where='t: ', weak_floor=4)
chk('fills to the floor', len(out), 4)
chk('fade rung went FIRST', out[2]['label'], 'Nets +7.5')
chk('DotD is the LAST rung', out[3]['label'], 'Queta PRA UNDER 22.5')

# 2. a benched prop for a player ALREADY on the card is refused
B = bench(prop=[{'type':'prop','label':'Jenkins PRA UNDER 27.5','sport':'NBA','game':'X @ Y','game_id':'g11','odds':-130,'source_key':'Daniss Jenkins|pra|27.5'}])
out = G.apply_card_floor(list(card2), 5, B, where='t: ', weak_floor=5)
chk('second Jenkins line REFUSED', len(out), 2)

# 3. a fade on a game the card already plays is refused
B = bench(fade=[{'type':'rl','label':'Pitt +4.5','sport':'NCAAB','game':'Duke @ Pitt','game_id':'g10','odds':-110}])
chk('same-game fade REFUSED', len(G.apply_card_floor(list(card2), 5, B, where='t: ', weak_floor=5)), 2)

# 4. the exact same BET benched twice is not added twice
B = bench(fade=[{'type':'ml','label':'Duke ML','sport':'NCAAB','game':'Duke @ Pitt','game_id':'g10','odds':-150}])
chk('duplicate bet REFUSED', len(G.apply_card_floor(list(card2), 5, B, where='t: ', weak_floor=5)), 2)

# 5. a -200 ML on the bench never fills a slot (CLAUDE.md heavy-fav trap)
B = bench(overflow=[{'type':'ml','label':'Kansas ML','sport':'NCAAB','game':'Kansas @ Iowa St','game_id':'g30','odds':-260}])
chk('-260 ML REFUSED by the juice gate', len(G.apply_card_floor(list(card2), 5, B, where='t: ', weak_floor=5)), 2)

# 6. a candidate the withdrawal gate would remove is not a fill
B = bench(overflow=[{'type':'total','label':'Over 43.5','sport':'NFL','game':'A @ B','game_id':'g40','odds':-110}])
out = G.apply_card_floor(list(card2), 5, B, validate=lambda c: [], where='t: ', weak_floor=5)
chk('gate-removable candidate REFUSED', len(out), 2)

# 7. empty bench -> short card, no crash, no padding
chk('empty bench leaves it short', len(G.apply_card_floor(list(card2), 5, bench(), where='t: ', weak_floor=5)), 2)

# 8. floor already met -> untouched, bench not drained
B = bench(fade=[{'type':'ml','label':'Z ML','sport':'NBA','game':'Z @ W','game_id':'g50','odds':-110}])
chk('floor met -> no fill', len(G.apply_card_floor(list(card2), 2, B, where='t: ', weak_floor=2)), 2)

# 9. CARD_FLOOR=0 switches it off
chk('floor 0 is OFF', len(G.apply_card_floor(list(card2), 0, B, where='t: ', weak_floor=0)), 2)

# 10. never overfills past the floor
B = bench(fade=[{'type':'ml','label':'F%d ML'%i,'sport':'NBA','game':'F%d @ G'%i,'game_id':'gf%d'%i,'odds':-110} for i in range(6)])
chk('never exceeds the floor', len(G.apply_card_floor(list(card2), 5, B, where='t: ', weak_floor=5)), 5)


print('== the two-stage ladder (the caps must not be undone) ==')
# 11. a 3-pick card is NOT padded to 5 by prop/DotD when weak_floor=3
B = bench(prop=[{'type':'prop','label':'Queta PRA UNDER 22.5','sport':'NBA','game':'P @ Q','game_id':'g21','odds':-115,'source_key':'Neemias Queta|pra|22.5'}],
          dotd=[{'type':'DotD','label':'Detroit Mercy ML','sport':'NCAAB','game':'DM @ R','game_id':'g22','odds':160}])
card3 = card2 + [{'type':'ml','label':'Iowa ML','sport':'NCAAB','game':'Iowa @ Neb','game_id':'g60','odds':-140}]
chk('3-pick card NOT padded by weak rungs', len(G.apply_card_floor(list(card3), 5, B, where='t: ', weak_floor=3)), 3)

# 12. but a 2-pick card IS rescued up to the weak floor
chk('2-pick card rescued to weak floor', len(G.apply_card_floor(list(card2), 5, B, where='t: ', weak_floor=3)), 3)

# 13. fade/overflow still fill ALL the way to CARD_FLOOR on that same card
B2 = bench(fade=[{'type':'rl','label':'Nets +7.5','sport':'NBA','game':'Nets @ Heat','game_id':'g20','odds':-110},
                 {'type':'rl','label':'Suns +3.5','sport':'NBA','game':'Suns @ Jazz','game_id':'g23','odds':-105}],
           prop=[{'type':'prop','label':'Queta PRA UNDER 22.5','sport':'NBA','game':'P @ Q','game_id':'g21','odds':-115,'source_key':'Neemias Queta|pra|22.5'}])
out = G.apply_card_floor(list(card3), 5, B2, where='t: ', weak_floor=3)
chk('fade fills to the FULL floor', len(out), 5)
chk('and the prop stayed benched', all('Queta' not in str(p.get('label')) for p in out), True)

print()
if fails:
    print('*** %d CASE(S) FAILED: %s ***' % (len(fails), ', '.join(fails)))
    print('    A failure here means a card guard is now wrong, not that '
          'the test is stale.')
else:
    print('all %d card dedupe/floor cases pass' % 18)
raise SystemExit(1 if fails else 0)
