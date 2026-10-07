"""A read that overrides the models without a stated reason cannot be STRONG.

WHY (2026-10-07)
----------------
Measured point-in-time from frozen read snapshots, graded ATS against the
close, across every sport that has the data:

                      agrees with models        overrides them
    NFL               78.6%  (n=14)             48.1%  (n=27)     66% override
    NCAAF             55.0%  (n=60)             44.7%  (n=85)     59% override
    MLB               57.4%  (n=101)            44.7%  (n=141)    58% override
    pooled            58.3%  (n=175)            45.1%  (n=253)

The models on those same overridden games ran 55%. Overriding cost 47.7 units
since August. And in MLB the override lands on the PUBLIC's side 113 times out
of 141 — nearly 5:1 onto the worst voter on the panel (43.9%).

The prompts now tell Jerry to defer. A prompt is advice; this is the gate.

WHAT IT DOES, AND DELIBERATELY DOES NOT DO
It does NOT flip the side. The pick stays exactly what the read said — users
see the call that was written, and nothing silently rewrites a take.

It caps CONVICTION at the LEAN ceiling when the call contradicts the model
majority and the long read never names a reason the models could not see. A
disagreement grounded in a late scratch, a weather change or a lineup note is
legitimate and keeps its tier; "feel", money flow and consensus picks are not,
because all three are measured below breakeven.

FAILS OPEN. If the struct carries no usable model margins or no market line,
nothing is capped — a missing input must never silently demote a real pick.
"""
from __future__ import annotations

import re

#: ceiling applied to an unjustified override. 64 is the top of LEAN in the
#: published tier map (65-79 STRONG, 80+ PRIME).
LEAN_CEILING = 64

#: THERE IS DELIBERATELY NO PROSE ESCAPE HATCH. This started life with a
#: JUSTIFIERS keyword list — scratch / weather / lineup / rest / injury — on
#: the theory that an override grounded in a fact the models cannot see is
#: legitimate and should keep its tier. Measured the same day, that theory is
#: dead:
#:
#:      override, keywords present   45.0%  -19.73u  n=140
#:      override, keywords absent    45.1%  -15.64u  n=113
#:
#: A 0.1pp difference, and the exception covered 55% of all overrides — 87%
#: of NFL reads and 62% of MLB reads trip it, because the reads enumerate the
#: injury report and the forecast either way. "Weather is a non-factor" and
#: "no injury concerns" both match. Prose keywords cannot tell a load-bearing
#: fact from a passing mention, so the exception was a hole, not a rule.
#:
#: If the exception is ever wanted back it has to be a field the read
#: DECLARES — not something inferred from stray words — and it has to be
#: measured before it is trusted.


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def model_majority(sport: str, struct: dict, need: float):
    """(side, n_for, n_total) across the model margins in a live struct.

    `need` is how much the HOME team must win by to cover. Mirrors
    model_scorecard.extract_models so the gate and the scorecard can never
    disagree about what the models said.
    """
    if not isinstance(struct, dict) or need is None:
        return None, 0, 0
    margins = []
    if sport == 'MLB':
        fm = struct.get('full_models') or {}
        for key, sub in (('jerry', 'pred_spread'), ('model_v4', 'pred_spread')):
            v = _f((fm.get(key) or {}).get(sub))
            if v is not None:
                margins.append(v)
        v = _f((fm.get('panel') or {}).get('implied_margin'))
        if v is not None:
            margins.append(v)
        v = _f(((fm.get('monte_carlo') or {}).get('probs') or {})
               .get('mc_expected_margin'))
        if v is not None:
            margins.append(v)
    elif sport == 'NCAAF':
        m = struct.get('model') or {}
        v = _f(m.get('projected_spread'))
        if v is None:
            hp, ap = _f(m.get('model_pred_home_points')), _f(m.get('model_pred_away_points'))
            v = (hp - ap) if None not in (hp, ap) else None
        if v is not None:
            margins.append(v)
    else:
        ms = struct.get('models') or {}
        mm = ms.get('matchup') or {}
        v = _f(mm.get('projected_spread'))
        if v is None:
            hp, ap = _f(mm.get('home_pts')), _f(mm.get('away_pts'))
            v = (hp - ap) if None not in (hp, ap) else None
        if v is not None:
            margins.append(v)
        pn = ms.get('panel') or {}
        ph, pa = _f(pn.get('home_pts')), _f(pn.get('away_pts'))
        if None not in (ph, pa):
            margins.append(ph - pa)

    sides = ['HOME' if m > need else 'AWAY'
             for m in margins if abs(m - need) >= 0.5]
    if not sides:
        return None, 0, 0
    h, a = sides.count('HOME'), sides.count('AWAY')
    if h == a:
        return None, h, len(sides)
    return ('HOME' if h > a else 'AWAY'), max(h, a), len(sides)


def market_need(sport: str, struct: dict):
    """How much HOME must win by to cover, from the struct's own market block.

    NFL stores a POSITIVE spread as a HOME favourite; the others store the
    home handicap. Getting this backwards would invert the whole gate, so it
    is written out rather than inferred.

    THE LINE LIVES IN A DIFFERENT PLACE IN EVERY SPORT, verified against live
    snapshots 2026-10-07. Reading only `market.spread` found it in MLB and
    NCAAF and MISSED BOTH OTHERS — NFL carries no `market` key whatsoever
    (its line is `signals.close_spread`) and NHL calls it `puckline`. That
    silently failed the gate open on the sport with the single worst override
    penalty, which is the opposite of what it is for.
    """
    if not isinstance(struct, dict):
        return None
    mk = struct.get('market') or {}
    sig = struct.get('signals') or {}
    sp = None
    for blk, keys in ((mk, ('spread', 'close_spread', 'puckline')),
                      (sig, ('close_spread', 'spread'))):
        if not isinstance(blk, dict):
            continue
        for k in keys:
            sp = _f(blk.get(k))
            if sp is not None:
                break
        if sp is not None:
            break
    if sp is None:
        return None
    home_line = -sp if sport == 'NFL' else sp
    return -home_line


def apply(sport: str, struct: dict, parsed: dict) -> tuple[dict, str | None]:
    """(parsed, note) with conviction capped when the call fights the models.

    The SIDE is never touched — the published pick stays exactly what the read
    said. Only the tier moves, so a capped pick still ships; it just stops
    carrying a STRONG or PRIME badge it has not earned.

    Fails open on every missing input: no line or no model margins means no
    cap, because a missing input must never silently demote a real pick.
    """
    if not isinstance(parsed, dict) or not isinstance(struct, dict):
        return parsed, None
    side = str(parsed.get('call_side') or '').upper()
    if side not in ('HOME', 'AWAY'):
        return parsed, None                 # totals/props are not gated here
    need = market_need(sport, struct)
    mside, n_for, n_tot = model_majority(sport, struct, need)
    if mside is None or mside == side:
        return parsed, None                 # no majority, or we agree

    conv = _f(parsed.get('conviction'))
    if conv is None or conv <= LEAN_CEILING:
        return parsed, None
    parsed['conviction'] = LEAN_CEILING
    return parsed, (f'conviction {conv:.0f} -> {LEAN_CEILING}: call is {side} '
                    f'but {n_for}/{n_tot} models are {mside}')
