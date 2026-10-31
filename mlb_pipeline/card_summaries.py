"""One place that recomputes a card blob's summaries from its own lists.

== 2026-10-31 . THE HEADLINE SAID 5-0 AND THE LIST UNDER IT SAID 3-0 =====
ANDY: "Also says 5-0 yesterday but on sweat card recap below only shows 3
plays and says 3-0"

A sweat_card blob carries TWO lists and TWO summaries, and each summary
describes its own list:

    top_8              -> top_8_summary     read by RecapStrip.tsx:129
                                            (the yesterday headline)
    unified_top_picks  -> unified_summary   read by index.tsx:13904
                                            (the CARD RESULT line under
                                            the list the user scrolls)

index.tsx has carried the reason since 09-21: "read the summary that
matches the list actually on screen ... top_8_summary counts MLB top_8
only, so once the card renders the unified cross-sport list it would have
reported a W-L over a different set of picks than the user can see."

reconcile_card_recap_to_receipts added the missing picks to BOTH lists and
then recomputed only top_8_summary. On 2026-10-30 that left:

    top_8              5 items, all Win   top_8_summary    5-0   <- headline
    unified_top_picks  5 items, all Win   unified_summary  3-0   <- stale

So the screen showed a 5-0 headline above a five-pick list whose own
record read 3-0. Growing a list without re-summarising it is the same
defect the 09-21 note was written about, reached from the other direction.

Every writer that touches a card list calls recompute() so the two can
never drift again -- the sanitize lesson: two copies of a normaliser
drift, and a blob summarised differently depending on who wrote it is
worse than no summary at all.

It is deliberately keyed on the LIST, not on a preferred list: a summary
whose list is absent is left alone rather than zeroed, because a missing
list is a different bug and must not be papered over with 0-0.
"""
from __future__ import annotations

#: (list key, summary key). Both pairs the app actually reads.
PAIRS = (('top_8', 'top_8_summary'),
         ('unified_top_picks', 'unified_summary'))
WON = ('WIN', 'W', 'WON')
LOST = ('LOSS', 'L', 'LOST')
PUSHED = ('PUSH', 'TIE')
#: a withdrawn pick is not a result. It is excluded from the counts rather
#: than counted as pending, which would read as "still live" on screen.
VOIDS = ('VOID', 'VOIDED', 'NO_ACTION')


def summarise(items) -> dict:
    """The shape both readers expect: wins/losses/pushes/pending/resolved."""
    w = l = p = pend = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        r = str(it.get('result') or '').upper()
        if r in VOIDS:
            continue
        if r in WON:
            w += 1
        elif r in LOST:
            l += 1
        elif r in PUSHED:
            p += 1
        else:
            pend += 1
    return {'wins': w, 'losses': l, 'pushes': p, 'pending': pend,
            'resolved': w + l + p}


def recompute(blob) -> list:
    """Rewrite every summary from the list it describes, in place.

    Returns [(summary_key, old, new), ...] for the ones that changed, so a
    caller can print what moved instead of asserting it silently.
    """
    changes = []
    if not isinstance(blob, dict):
        return changes
    for list_key, sum_key in PAIRS:
        items = blob.get(list_key)
        if not isinstance(items, list):
            continue              # absent list: not ours to zero out
        old = blob.get(sum_key)
        new = summarise(items)
        if old != new:
            blob[sum_key] = new
            changes.append((sum_key, old, new))
    return changes


if __name__ == '__main__':
    # the 10-30 blob, as found
    b = {'top_8': [{'result': 'Win'}] * 5,
         'top_8_summary': {'wins': 5, 'losses': 0, 'pushes': 0,
                           'pending': 0, 'resolved': 5},
         'unified_top_picks': [{'result': 'Win'}] * 5,
         'unified_summary': {'wins': 3, 'losses': 0, 'pushes': 0,
                             'pending': 0, 'resolved': 3}}
    ch = recompute(b)
    assert len(ch) == 1 and ch[0][0] == 'unified_summary', ch
    assert b['unified_summary']['wins'] == 5, b['unified_summary']
    # an absent list must not be zeroed
    b2 = {'top_8_summary': {'wins': 2, 'losses': 1, 'pushes': 0,
                            'pending': 0, 'resolved': 3}}
    assert recompute(b2) == [], recompute(b2)
    assert b2['top_8_summary']['wins'] == 2
    # a VOID pick is excluded, not counted pending
    b3 = {'top_8': [{'result': 'Win'}, {'result': 'VOID'},
                    {'result': None}], 'top_8_summary': None}
    recompute(b3)
    assert b3['top_8_summary'] == {'wins': 1, 'losses': 0, 'pushes': 0,
                                   'pending': 1, 'resolved': 1}, \
        b3['top_8_summary']
    print('card_summaries self-test PASSED')
