#!/usr/bin/env python3
"""Snap a pick's line to the number the market actually quoted.

== 2026-10-19 - WHY ==
Jerry's reads publish spread numbers that were never available anywhere.
Verified against line_history, 1000 captures across 11 books over an 11-day
window per game:

    PHI -6.5   books quoted  -2.5 -2.0 -1.5  3.0  3.5   -> never
    KC  -2.5   books quoted  -9.0 -8.5 -8.0 -7.5 -7.0   -> never
    NO  +2.5   books quoted  -1.5 -1.0 -0.5  0.0  1.0   -> never
    BAL -6.5   books quoted  -4.0 -3.5 -3.0 -2.5 -2.0   -> never

Across both football sports, 30 of 62 checked game_read picks (48%) carried a
line no book ever posted, mean 1.1 points off and up to 5.0. The curated
surfaces are far cleaner - sweat_card 1 of 15, sharp_card 2 of 17 - so this is
specifically the per-game read, where call_line comes straight out of the LLM
parse and nothing ever compares it to the market.

WHY IT MATTERS MORE THAN IT LOOKS. A record graded at a number nobody could
bet is not a record. A pick published 5 points better than the market wins for
free and inflates the surface; one published 5 points worse loses for free.
It is upstream of every tier, gate and model, and it contaminated my own
analysis: the closing-line-value effect I found (CLV>0 60.6% vs CLV<0 44.8%)
is substantially this artifact, because "we beat the close" largely MEANS "our
published number was wrongly generous".

ORIENTATION IS VERIFIED, NOT ASSUMED. close_spread means opposite things by
sport, so the mapping to a HOME HANDICAP was checked against line_history on
games whose last capture was within 2h of kickoff:

    NFL    home handicap = -close_spread    93% exact (<=0.5), mean |err| 0.29
    NCAAF  home handicap = +close_spread    89% exact (<=0.5), mean |err| 0.34

FAILS OPEN. No context, no close_spread, or an unknown sport returns the line
untouched. A read that publishes is better than a crash, and the audit script
catches whatever slips through.
"""
from __future__ import annotations

#: how far a published line may sit from the market before it is snapped.
#: Half a point is the smallest increment books actually move in.
SNAP_TOLERANCE = 0.5

#: close_spread -> HOME HANDICAP multiplier, measured per sport (see module
#: docstring). Absent sport = unknown orientation = do not touch.
_HOME_HANDICAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def market_handicap(ctx: dict, sport: str, side: str):
    """The market's handicap FOR THE GIVEN SIDE, or None when unknowable."""
    if not isinstance(ctx, dict):
        return None
    sgn = _HOME_HANDICAP_SIGN.get(str(sport).upper())
    if sgn is None:
        return None
    cs = _f(ctx.get('close_spread'))
    if cs is None:
        return None
    home_handicap = sgn * cs
    s = str(side or '').strip().upper()
    if s == 'HOME':
        return home_handicap
    if s == 'AWAY':
        return -home_handicap
    return None


def snap_line(line, ctx: dict, sport: str, side: str, market: str,
              tolerance: float = SNAP_TOLERANCE):
    """Return (snapped_line, note). note is None when nothing changed.

    Only spread markets are touched. A moneyline has no line and a total is
    a different number entirely.
    """
    if str(market or '').lower() not in ('rl', 'spread', 'ats'):
        return line, None
    ours = _f(line)
    mk = market_handicap(ctx, sport, side)
    if mk is None:
        return line, None
    if ours is None:
        # no line emitted at all: the market's number is strictly better
        # than publishing nothing, and it is the number a user could take
        return mk, (f'line absent; set to the market handicap {mk:+.1f}')
    if abs(ours - mk) <= tolerance:
        return ours, None
    return mk, (f'published {ours:+.1f} but the market was {mk:+.1f} '
                f'({ours - mk:+.1f} off); snapped to the market')


def apply_to_parsed(parsed: dict, ctx: dict, sport: str,
                    verbose: bool = True) -> dict:
    """Snap parsed['call_line'] in place against the market. Fails open."""
    try:
        if not isinstance(parsed, dict):
            return parsed
        mkt = str(parsed.get('call_market') or '')
        if mkt.lower() not in ('rl', 'spread', 'ats'):
            return parsed
        new, note = snap_line(parsed.get('call_line'), ctx, sport,
                              parsed.get('call_side'), mkt)
        if note:
            parsed['call_line'] = new
            parsed['_line_snapped'] = note
            # rebuild call_text so the label a user reads carries the real
            # number: a snapped line with a stale label is worse than either
            ct = parsed.get('call_text')
            if ct:
                import re
                parsed['call_text'] = re.sub(
                    r'[+-]\d+(?:\.\d+)?', f'{new:+.1f}', str(ct), count=1)
            if verbose:
                print(f'  [line] {note}')
        return parsed
    except Exception as e:                                     # noqa: BLE001
        if verbose:
            print(f'  [line] snap SKIPPED (fail-open): '
                  f'{type(e).__name__}: {e}')
        return parsed
