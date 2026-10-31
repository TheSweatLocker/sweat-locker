"""Invariants every published receipt must satisfy, at the one chokepoint.

══ 2026-10-30 · WHY THIS IS ONE MODULE AND NOT FOUR PATCHES ═══════════════
ANDY: "we need to fix the 4 things wrong, espesillay so these same issue
keep happening."

Tonight's board had four defects and every one of them is the same shape — a
surface wrote a receipt that contradicted itself, and nothing between the
composer and the table objected:

  1. Houston -6.5 and Detroit -8.5 captured with pick_odds = NULL. A graded
     play with no price gets priced at a guess, and flat -110 inflates every
     figure it touches (feedback_flat_110_inflates_every_ml_figure).
  2. daily_degen receipt said "3 legs" while its cache payload held 2.
  3. An NBA `total` fade receipt was published for a market measured
     negative and already in FADE_BANNED (45.5%, ROI -13.2%, n=33) — the
     ban lives in the composer, so a stale payload walked straight past it.
  4. Houston -6.5 published at STRONG while our own projection had it short
     of the line.

`public_receipt.capture()` already refuses a line-bearing market with no
line (`unactionable`). That guard is the right shape and the right place —
it is simply the only one of its kind. Four more defects means four more
rules, and they belong beside it rather than in each composer, because a
guard on one of eight sources is not a guard
(feedback_guard_belongs_at_the_chokepoint): the 10-22 future-game rule had
to be moved here for exactly this reason after 237 bad rows got in through
the one backfill path that never received it.

WHAT THIS DELIBERATELY DOES NOT DO
It does not judge whether a pick is GOOD. Tier, edge and coherence are the
engine's job, and this module has no model. It only refuses a row that is
internally inconsistent — a record that cannot be graded honestly whatever
the pick turns out to be.

Severity is split on purpose:
  * REFUSE  — the row cannot be graded honestly. Never written.
  * WARN    — the row is gradeable but suspect. Written, and printed loudly,
              because a surface that silently drops picks is the bug that
              hid MLB's empty prop pool for a season.
"""
from __future__ import annotations

from typing import Any, Optional

#: markets whose receipt is meaningless without a price. A moneyline or a
#: spread settles at a number someone laid; "no odds" is not a bet.
PRICE_REQUIRED_MARKETS = {
    'ml', 'rl', 'spread', 'puckline', 'pl', 'total', 'prop',
    'parlay', 'chalk_parlay', 'potd', 'dotd', 'fight',
}

#: A price outside this band is almost certainly a parse error rather than a
#: real quote. +10000 longshots exist, but not on these surfaces.
PRICE_SANITY = (-100000.0, 100000.0)

#: Surfaces that publish a PLAY: the record is a claim we staked, so a row
#: that cannot be priced cannot be graded honestly and is refused.
#:
#: `game_read` is deliberately NOT here. It is COVERAGE -- an opinion on
#: every game, most of it never published as a play -- and refusing those
#: rows would delete the coverage record instead of fixing its price, which
#: is a worse trade than recording it priceless and flagging it. 406 of
#: 9,593 receipts since the 09-19 pricing floor carry NULL pick_odds and
#: ZERO are graded Win/Loss yet, so nothing published is wrong today; the
#: real repair is recovering the price at the composer (R58), and this guard
#: exists to stop NEW priceless PLAYS while that is done.
PLAY_SURFACES = {
    'sweat_card', 'sharp_card', 'potd', 'dotd', 'fade',
    'ledger', 'daily_degen', 'prop_jerry', 'ladder',
}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _banned(sport, market) -> Optional[str]:
    """Is this (sport, market) one the FADE backtest measured NEGATIVE?

    Imported from generate_the_fade rather than restated so the ban cannot
    drift between the composer that applies it and the guard that enforces
    it. An import failure returns None — this module must never be the
    reason a surface loses its card.
    """
    try:
        from generate_the_fade import FADE_BANNED
    except Exception:
        return None
    key = (str(sport or '').upper(), str(market or '').lower())
    val = FADE_BANNED.get(key) if isinstance(FADE_BANNED, dict) else None
    return str(val) if val else None


def check_row(row: dict, surface: str) -> tuple[Optional[str], Optional[str]]:
    """-> (severity, reason). severity is 'REFUSE', 'WARN' or None."""
    market = str(row.get('market') or '').lower()
    sport = str(row.get('sport') or '').upper()

    # (1) A PLAY WITH NO PRICE. Mirrors `unactionable`, for the price.
    if market in PRICE_REQUIRED_MARKETS:
        odds = _f(row.get('pick_odds'))
        _is_play = surface in PLAY_SURFACES
        if odds is None:
            return ('REFUSE' if _is_play else 'WARN',
                    f'{market} receipt with no pick_odds — a graded play '
                    f'with no price is priced at a guess')
        if not (PRICE_SANITY[0] <= odds <= PRICE_SANITY[1]) or odds == 0:
            return ('REFUSE' if _is_play else 'WARN',
                    f'pick_odds {odds:g} is not a plausible price')

    # (2) A MARKET MEASURED NEGATIVE, enforced where every source passes.
    # Only on the surface the ban was measured for; a game read is allowed
    # to hold an opinion about a market we refuse to PUBLISH as a play.
    if surface == 'fade':
        why = _banned(sport, market)
        if why:
            return 'REFUSE', f'{sport} {market} is in FADE_BANNED ({why})'

    # (3) A MULTI-LEG TICKET MUST AGREE WITH ITS OWN LEG COUNT.
    # daily_degen published "3 legs" over a 2-leg payload, so the record
    # claimed a parlay nobody could reconstruct or grade.
    if market in ('parlay', 'chalk_parlay'):
        audit = row.get('audit') if isinstance(row.get('audit'), dict) else {}
        claimed = _f(audit.get('n_legs'))
        actual = audit.get('legs')
        actual_n = float(len(actual)) if isinstance(actual, list) else None
        if claimed is not None and actual_n is not None and claimed != actual_n:
            return 'REFUSE', (f'ticket claims {claimed:g} legs but carries '
                              f'{actual_n:g} — ungradeable as published')
        label = str(row.get('pick_label') or '')
        if actual_n is not None and 'leg' in label:
            import re as _re
            m = _re.search(r'(\d+)\s*legs?', label)
            if m and float(m.group(1)) != actual_n:
                return 'REFUSE', (f'label says {m.group(1)} legs but payload '
                                  f'carries {actual_n:g}')

    # (4) A SIDE-BEARING MARKET WITH NO SIDE is gradeable only by guessing.
    if market in ('ml', 'rl', 'spread', 'puckline', 'pl') and not row.get('pick_side'):
        return 'WARN', f'{market} receipt carries no pick_side'

    return None, None


def check_line_coherence(rows: list, surface: str,
                         quiet: bool = False) -> list:
    """Flag a receipt whose NUMBER disagrees with one already published.

    == 2026-10-30 . THE SAME SIDE WENT OUT AT TWO DIFFERENT NUMBERS =====
    Lakers @ Golden State shipped as Warriors +1.5 (-108) on the Fade at
    13:51 and Warriors +3.5 (-115) on the Sweat Card at 21:53. Orlando @
    Toronto shipped Magic +5.5 at -105 and again at -110. Nobody
    mis-derived it: every composer reads close_spread at its own run time
    and that column moves with the money.

    WARN, never REFUSE. Refusing the second receipt would leave a pick
    visible on a surface with NO receipt behind it, which is worse than a
    disagreeing one -- an unrecorded play cannot be graded at all
    (feedback_green_step_that_writes_nothing). The cure is composers
    calling public_receipt.frozen_quote before they display a number; this
    is how we SEE the ones that have not been wired yet.

    One batched read per capture, keyed on the game_ids in the payload.
    """
    import os

    import requests

    sb = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not sb or not key:
        return []
    hdr = {'apikey': key, 'Authorization': f'Bearer {key}'}
    LINE_MKT = {'rl', 'spread', 'puckline', 'pl', 'total'}
    want = [r for r in rows
            if str(r.get('market') or '').lower() in LINE_MKT
            and r.get('game_id') is not None
            and r.get('pick_line') is not None]
    if not want:
        return []
    gids = sorted({str(r['game_id']) for r in want})
    found = []
    try:
        enc = ','.join(f'"{g}"' for g in gids)
        r = requests.get(
            f'{sb}/rest/v1/public_receipts', headers=hdr, timeout=20,
            params={'game_id': f'in.({enc})',
                    'select': 'game_id,surface,market,pick_side,pick_line,'
                              'pick_odds,result,published_at',
                    'order': 'published_at.asc'})
        prior = r.json() if r.status_code == 200 else []
    except Exception:                                      # noqa: BLE001
        return []
    if not isinstance(prior, list):
        return []
    for row in want:
        gid = str(row['game_id'])
        mkt = str(row.get('market') or '').lower()
        side = str(row.get('pick_side') or '').upper()
        for p in prior:
            if str(p.get('game_id')) != gid:
                continue
            if str(p.get('market') or '').lower() != mkt:
                continue
            if str(p.get('result') or '').upper() in ('VOID', 'VOIDED'):
                continue
            if p.get('pick_line') is None:
                continue
            if side and str(p.get('pick_side') or '').upper() != side:
                continue
            try:
                same = abs(float(p['pick_line'])
                           - float(row['pick_line'])) < 0.01
            except (TypeError, ValueError):
                same = False
            if same:
                break
            msg = (f'{row.get("pick_label")!r} publishes '
                   f'{row["pick_line"]} but {p.get("surface")} already '
                   f'published {p["pick_line"]} on the same side of this '
                   f'game at {str(p.get("published_at"))[:19]} -- call '
                   f'public_receipt.frozen_quote() before displaying a '
                   f'number')
            found.append((row, msg))
            if not quiet:
                print(f'  ! line incoherence [{surface}]: {msg}')
            break
    return found

def screen(rows: list, surface: str, quiet: bool = False) -> tuple[list, list]:
    """Split rows into (keep, refused) and print every decision loudly."""
    keep, refused, warned = [], [], []
    for r in rows or []:
        sev, why = check_row(r, surface)
        if sev == 'REFUSE':
            refused.append((r, why))
        else:
            if sev == 'WARN':
                warned.append((r, why))
            keep.append(r)
    if refused and not quiet:
        print(f'  *** receipt_invariants ({surface}): REFUSED {len(refused)} '
              f'row(s) ***')
        for r, why in refused[:8]:
            print(f'      {r.get("sport")} {r.get("market")} '
                  f'{r.get("pick_label")!r}: {why}')
    if warned and not quiet:
        print(f'  ! receipt_invariants ({surface}): {len(warned)} warning(s)')
        for r, why in warned[:6]:
            print(f'      {r.get("sport")} {r.get("market")} '
                  f'{r.get("pick_label")!r}: {why}')
    return keep, [r for r, _w in refused]


if __name__ == '__main__':
    cases = [
        ({'sport': 'NBA', 'market': 'rl', 'pick_label': 'Houston Rockets -6.5',
          'pick_odds': None, 'pick_side': 'AWAY'}, 'sweat_card', 'REFUSE'),
        ({'sport': 'NBA', 'market': 'rl', 'pick_label': 'Houston Rockets -6.5',
          'pick_odds': -110, 'pick_side': 'AWAY'}, 'sweat_card', None),
        ({'sport': 'NBA', 'market': 'total', 'pick_label': 'Under 224.5',
          'pick_odds': -110}, 'fade', 'REFUSE'),
        ({'sport': 'NBA', 'market': 'total', 'pick_label': 'Under 224.5',
          'pick_odds': -110}, 'game_read', None),
        ({'sport': 'MULTI', 'market': 'parlay', 'pick_odds': 622,
          'pick_label': 'daily_degen (3 legs)',
          'audit': {'n_legs': 3, 'legs': [1, 2]}}, 'daily_degen', 'REFUSE'),
        ({'sport': 'NHL', 'market': 'ml', 'pick_label': 'Seattle Kraken ML',
          'pick_odds': -115}, 'game_read', 'WARN'),
        # a priceless COVERAGE row warns, it is not deleted
        ({'sport': 'NBA', 'market': 'rl', 'pick_label': 'Lakers ATS',
          'pick_odds': None, 'pick_side': 'HOME'}, 'game_read', 'WARN'),
    ]
    ok = True
    for row, surf, want in cases:
        got, why = check_row(row, surf)
        flag = 'ok ' if got == want else 'FAIL'
        if got != want:
            ok = False
        print(f'  {flag} [{surf}] {row.get("pick_label")!r} -> {got} '
              f'(wanted {want}){"  " + str(why) if why else ""}')
    print('\nself-test', 'PASSED' if ok else 'FAILED')
    raise SystemExit(0 if ok else 1)
