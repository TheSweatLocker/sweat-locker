"""Verify a published Sweat Card / Sharp before anyone acts on it.

══ 2026-10-03 · WHY ══
2026-10-02's Sharp carried three defects at once, all of which this would
have caught before publish:

    {"pick": "Liberty ML",     "odds": -300, "juice_swapped": false}
    {"pick": "Penn State ML",  "reason": "State · 85% vs 59% implied ..."}
    {"pick": "Virginia Tech -3"}  # read closed "Pittsburgh +2.5 has the edge"

A -300 moneyline past the documented -200 trap; a team rendered as "State"
because team_short took the last word of "Penn State"; and a write-up
arguing the opposite side of its own pick.

The cards are the LAST surface before a subscriber sees a play, and the
Sweat Card HARD-LOCKS at noon ET — so a bad card published at 10:55 is
awkward to retract. A check that runs right after generation is the cheapest
insurance available, and all of its inputs are already in the database.

Every check compares the CARD against the CURRENT engine state, so it also
catches the card going stale relative to a later re-gate.

Exit 1 on any FAIL so a workflow step can gate on it. WARNs do not fail.

    python check_card_integrity.py                  # today, both surfaces
    python check_card_integrity.py --date 2026-10-03
    python check_card_integrity.py --surface sharp_card
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
load_dotenv('.env')
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
S = requests.Session()
S.headers.update(H)

CTX_TBL = {
    'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
    'NHL': 'nhl_game_context', 'NBA': 'nba_game_context',
    'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context',
}
# The documented heavy-favourite trap (feedback_heavy_fav_ml_trap_803) and the
# level defensive_gates.HEAVY_ML_THRESHOLD reroutes at. Kept in sync via that
# import so there is one number, not a second opinion.
try:
    from defensive_gates import HEAVY_ML_THRESHOLD
except Exception:
    HEAVY_ML_THRESHOLD = -200

START_COLS = ('kickoff_utc', 'game_time_utc', 'start_time_utc', 'commence_time')
fails: list[str] = []
warns: list[str] = []
drifts: list[str] = []


def F(msg):
    fails.append(msg)
    print(f'  ✖ FAIL  {msg}')


def W(msg):
    warns.append(msg)
    print(f'  ⚠ warn  {msg}')


def D(msg):
    """Post-lock engine drift. Reported, counted, NOT gating."""
    drifts.append(msg)
    print(f'  ~ drift {msg}')


# ══ 2026-11-18 · A DETECTOR MUST KNOW THE INTENTIONAL CASE ═════════════
# Check 1 below carried the comment "the card must agree with the engine as
# it stands NOW". That premise is wrong by construction after the lock
# hour, and it produced 4 FAILs on 11-17 that were all expected behaviour.
#
# The card is DELIBERATELY immutable. `generate_sharp_card._publish` hard
# locks at SHARP_CARD_HARD_LOCK_ET_HOUR (default 12 ET) and refuses a
# republish "even with --force", quoting Andy's 09-19 directive: "whatever
# comes out in the morning stays. No more refreshing the sweat card,
# sharp, or jerry game analysis picks." The ENGINE, by contrast, keeps
# re-deriving from the live board all day.
#
# So after the lock the two are SUPPOSED to diverge. Measured on the
# 11-17 FAIL 4: the card published `Over 130.5` at 15:41Z, which was the
# live number (fanduel 130.5 at 13:28Z, espnbet 130.5 at 14:26Z); the
# market then drifted down (draftkings 128.5 at 14:54Z, espnbet 17:26Z,
# betmgm 20:57Z) and the engine's label followed to `Over 128.5`. Nothing
# was wrong with the card. That is a LAG, not a disagreement
# ([[feedback_a_lag_is_not_a_disagreement]]).
#
# The severity rule is whether the finding is STILL ACTIONABLE:
#   * before the lock -> FAIL. The card can still be rebuilt.
#   * at/after the lock -> DRIFT. Republish is refused, so a FAIL is
#     unactionable noise - and a detector that fails every day from
#     12:00 ET is a detector nobody reads
#     ([[feedback_a_detector_can_be_the_defect]]).
# Only the engine-comparison checks move. Checks about what we published
# being wrong ON ITS OWN TERMS - juice traps, prose naming the other team,
# a spread with no line - stay FAIL at every hour, because those are
# defects in the frozen artifact itself, not drift away from a moving
# reference.
def _past_hard_lock(surface: str, card_date: str) -> tuple[bool, int, int]:
    """(past_lock, et_hour, lock_hour) for this surface's own lock hour.

    Keyed on the CARD'S date, not just the clock. A card for a date earlier
    than ET today is locked at every hour -- checking yesterday's card at
    3am must not report FAILs, which is what a bare `hour >= lock` test
    does ([[feedback_a_window_from_runner_clock_is_a_day_ahead]]).
    """
    env = ('SWEAT_CARD_HARD_LOCK_ET_HOUR' if surface == 'sweat_card'
           else 'SHARP_CARD_HARD_LOCK_ET_HOUR')
    lock = int(os.environ.get(env, '12'))
    try:
        from sweat_dates import et_now as _et_now
        _now = _et_now()
    except Exception:                                          # noqa: BLE001
        from datetime import datetime as _d
        try:
            from zoneinfo import ZoneInfo
            _now = _d.now(ZoneInfo('America/New_York'))
        except Exception:                                      # noqa: BLE001
            # Refuse to GUESS the hour with a fixed offset - that is the
            # EDT/EST bug this repo has fixed in four files. With no tz
            # database, treat the card as locked: the conservative read
            # reports drift instead of crying FAIL.
            return True, -1, lock
    today = _now.strftime('%Y-%m-%d')
    if card_date < today:
        return True, _now.hour, lock          # a past slate is always locked
    if card_date > today:
        return False, _now.hour, lock         # tomorrow's card is still open
    return _now.hour >= lock, _now.hour, lock


def _card_items(data) -> list:
    """Normalise BOTH card shapes to one list of comparable picks.

    ══ 2026-10-03 · THE SWEAT CARD IS NOT A LIST OF `items` ══
    The first version read `data['items']`, which is the SHARP's shape. The
    Sweat Card has no such key — it carries top_8 / unified_top_picks /
    football_picks / top_props plus single potd / dawg / lock objects. So the
    check reported "published with ZERO items" on two consecutive days and I
    relayed that to Andy as an empty dashboard. It was not empty: 10-03 had
    5 in top_8, 6 unified, 5 football, 3 props and a POTD.

    A checker that misreads a surface is worse than no checker, because its
    silence and its alarms are both untrustworthy. Field names differ too —
    the Sharp uses `pick`/`reason`, the Sweat Card uses `label`/`sub` — so
    they are normalised here rather than special-cased at every assertion.
    """
    if isinstance(data, list):
        return list(data)
    if not isinstance(data, dict):
        return []
    raw: list = []
    if isinstance(data.get('items'), list):          # sharp_card
        raw += data['items']
    for k in ('top_8', 'unified_top_picks', 'football_picks', 'top_props'):
        v = data.get(k)
        if isinstance(v, list):
            raw += [dict(x, _from=k) for x in v if isinstance(x, dict)]
    for k in ('potd', 'dawg', 'lock', 'secondary_lock'):
        v = data.get(k)
        if isinstance(v, dict) and v:
            raw.append(dict(v, _from=k))
    # The same pick legitimately appears in more than one list (top_8 AND
    # unified_top_picks, say), so dedupe on identity or every finding is
    # reported two or three times — which on the first run made 3 real
    # failures look like 3 duplicated ones.
    out, seen = [], set()
    for it in raw:
        ident = (it.get('sport'), it.get('game_id'),
                 it.get('pick') or it.get('label') or it.get('team'),
                 it.get('type'))
        if ident in seen:
            continue
        seen.add(ident)
        out.append({
            'sport': it.get('sport') or it.get('_sport'),
            'game_id': it.get('game_id'),
            'pick': it.get('pick') or it.get('label') or it.get('team'),
            'type': it.get('type'),
            'tier': it.get('tier'),
            'reason': it.get('reason') or it.get('sub'),
            'odds': it.get('odds') or it.get('pick_odds'),
            'line': it.get('line'),
            '_from': it.get('_from', 'items'),
        })
    return out


def paged(tbl, params):
    out, off = [], 0
    while True:
        r = S.get(f'{SB}/rest/v1/{tbl}',
                  params=dict(params, limit='1000', offset=str(off)), timeout=120)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def ml_price(ctx, side):
    names = (('close_home_ml', 'home_ml_close') if side == 'HOME'
             else ('close_away_ml', 'away_ml_close'))
    for n in names:
        if ctx.get(n) is not None:
            try:
                return int(ctx[n])
            except (TypeError, ValueError):
                return None
    return None


def check(date: str, surface: str) -> None:
    key = f'{surface}_{date}'
    rows = paged('jerry_cache', {'select': 'cache_key,data,created_at',
                                 'cache_key': f'eq.{key}'})
    if not rows:
        print(f'\n=== {key} === NOT PUBLISHED yet — nothing to check')
        return
    data = rows[0].get('data') or {}
    items = _card_items(data)
    print(f"\n=== {key} === {len(items)} items · generated "
          f"{str(data.get('generated_at') or rows[0].get('created_at'))[:16]}")
    if not items:
        W(f'{key}: published with ZERO items')
        return

    # Severity for the ENGINE-comparison checks only. See the long note by
    # D() above: past this surface's hard lock the card cannot be rebuilt,
    # so divergence from a still-moving engine is expected and unactionable.
    _past, _eth, _lockh = _past_hard_lock(surface, date)
    E = D if _past else F
    print(f'  engine-comparison severity: '
          f'{"DRIFT (non-gating)" if _past else "FAIL (gating)"} — '
          f'ET {_eth if _eth >= 0 else "unknown"}'
          f'{"" if _eth >= 0 else " (no tz db — assuming locked)"} '
          f'vs {surface} hard lock {_lockh:02d}:00 ET')

    # One context fetch per sport present on the card.
    ctx_by_sport = {}
    for sp in {str(i.get('sport')) for i in items}:
        tbl = CTX_TBL.get(sp)
        if tbl:
            ctx_by_sport[sp] = {
                c['game_id']: c for c in
                paged(tbl, {'select': '*', 'game_date': f'eq.{date}'})}
    reads = {}
    for sp in ctx_by_sport:
        for r in paged('jerry_reads', {
                'select': 'game_id,call_market,call_side,call_line,'
                          'price_american,long_read,short_read',
                'sport': f'eq.{sp}', 'game_date': f'eq.{date}'}):
            reads[r['game_id']] = r

    now = datetime.now(timezone.utc)
    by_sport = Counter()
    no_ctx = 0
    for it in items:
        sp, gid = str(it.get('sport')), it.get('game_id')
        pick, typ = str(it.get('pick')), str(it.get('type') or '').lower()
        tier = str(it.get('tier') or '').upper()
        tag = f'{key} · {pick} ({sp})'
        by_sport[sp] += 1
        # Props and MLB card entries carry no game_id (they key on
        # source_key / player+prop), so there is no context row to compare
        # against. That is the surface's design, not a defect — counting it
        # quietly beats 21 identical warnings burying 3 real failures.
        if not gid or sp not in CTX_TBL:
            no_ctx += 1
            continue
        ctx = (ctx_by_sport.get(sp) or {}).get(gid)
        if not ctx:
            W(f'{tag}: {sp} game_id {gid} not in context for {date}')
            continue
        pp = ctx.get('primary_play') or {}
        side = str(pp.get('side') or '').upper()

        # 1 · the card vs the engine. BEFORE the lock these are failures the
        # card can still be rebuilt to fix; AFTER it, the card is immutable
        # by design and the engine has moved on, so they are drift.
        if str(pp.get('type') or '').lower() != typ:
            E(f'{tag}: card market {typ!r} != engine {pp.get("type")!r}')
        if pp.get('label') and str(pp['label']).strip() != pick.strip():
            E(f'{tag}: card pick != engine label {pp["label"]!r}')
        if pp.get('tier') and str(pp['tier']).upper() != tier:
            W(f'{tag}: card tier {tier} != engine {pp["tier"]}')

        # 2 · heavy-juice moneyline — the Liberty -300 case
        if typ == 'ml':
            px = it.get('odds')
            if px is None:
                px = ml_price(ctx, side)
            try:
                px = int(px)
            except (TypeError, ValueError):
                px = None
            if px is not None and px <= HEAVY_ML_THRESHOLD:
                F(f'{tag}: ML at {px:+d}, past the {HEAVY_ML_THRESHOLD:+d} '
                  f'heavy-favourite trap — should have rerouted to the spread')

        # 3 · a published tier needs a market to have an edge over
        if tier in ('PRIME', 'STRONG', 'LEAN'):
            if typ == 'ml' and ml_price(ctx, side) is None:
                F(f'{tag}: tier {tier} with NO moneyline price stored')
            if typ in ('rl', 'spread') and ctx.get('close_spread') is None \
                    and ctx.get('close_puckline') is None:
                F(f'{tag}: tier {tier} with NO spread/puckline stored')

        # 4 · the reason must not name a different market than the pick
        rsn = it.get('reason')
        if isinstance(rsn, str) and ':' in rsn:
            pre = rsn.split(':', 1)[0].strip()
            if pre.endswith(' ML') and not pick.endswith(' ML'):
                F(f'{tag}: reason says {pre!r} but the pick is a spread')

        # 5 · truncated team name — the "State · 85%" case.
        #
        # COLLEGE ONLY, AND ONLY WHEN THE REMNANT IS GENERIC. A first version
        # flagged "Wild · 56% vs 55% implied" for Minnesota Wild, which is a
        # false positive: in pro sports the field is "City Nickname" and the
        # nickname IS the identity ("Bruins", "Rangers", "Wild"). College
        # fields are school names whose last token is often the generic half,
        # so "Penn State" -> "State" loses the team while "Michigan State" ->
        # "State" collides with it. A check that cries wolf gets ignored.
        if isinstance(rsn, str) and sp in ('NCAAF', 'NCAAB'):
            head = rsn.split('·')[0].strip().split(':')[0].strip()
            team = pick.rsplit(' ', 1)[0].strip()
            _GENERIC = {'state', 'tech', 'a&m', 'university', 'college',
                        'st.', 'st', 'southern', 'northern', 'eastern',
                        'western', 'central', 'international'}
            if head and team and head != team and team.endswith(head) \
                    and head.lower() in _GENERIC:
                F(f'{tag}: reason opens with {head!r}, a generic truncation '
                  f'of {team!r} — team_short dropped the identity')

        # 6 · a line/price appropriate to the market
        if typ in ('rl', 'spread') and it.get('line') is None:
            W(f'{tag}: spread pick with no line on the card')
        rd = reads.get(gid)
        if rd:
            # jerry_reads is re-synced from the engine all day too, so it
            # drifts away from the frozen card for the same reason the
            # engine does — including to `pass` when the engine withdraws
            # the call after the lock. Same severity rule.
            if rd.get('call_market') and str(rd['call_market']).lower() != typ:
                E(f'{tag}: jerry_read call_market {rd["call_market"]!r} != '
                  f'card {typ!r} — the write-up is for another market')
            if rd.get('call_side') and side and \
                    str(rd['call_side']).upper() != side:
                E(f'{tag}: jerry_read call_side {rd["call_side"]} != pick side {side}')
            if rd.get('price_american') is None:
                W(f'{tag}: no price captured — ungradeable for ROI')
            # the 10-02 defect: prose arguing the other team
            # ══ 2026-10-03 · RECOMMENDING vs DESCRIBING ══
            # Bare "favors <other>" was in this list and had to come out. The
            # edge_side reconciliation shipped 10-02 DELIBERATELY names the
            # other side, because disclosing the tension is the product:
            #
            #   "the model actually favors TCU at plus money; however, the
            #    engine still backs BYU -6 because ..."
            #
            # That is the fix working, and the checker called it a FAIL — so
            # it would have fired on every correctly-reconciled read from now
            # on. What was actually wrong on 10-02 was RECOMMENDING language
            # at the close: "Delaware has value on the number", "Pittsburgh
            # +2.5 has the edge", "the gap favors Delaware's plus-6.5 as the
            # better value". Those tell the reader to take the other team.
            # Only those phrasings are flagged.
            other = (ctx.get('away_team') if side == 'HOME'
                     else ctx.get('home_team'))
            txt = f"{rd.get('long_read') or ''} {rd.get('short_read') or ''}"
            if other and txt:
                for ph in (f'{other} has value', f'{other} has the edge',
                           f'take {other}', f'{other} the better value',
                           f'{other} as the better value',
                           f'value on {other}', f'back {other}'):
                    if ph.lower() in txt.lower():
                        F(f'{tag}: write-up says "{ph}" — recommending '
                          f'against its own pick')
                        break

        # 7 · never publish a game already under way
        for c in START_COLS:
            v = ctx.get(c)
            if not v:
                continue
            try:
                dt = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
            except ValueError:
                break
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= now:
                W(f'{tag}: game already started ({c}={str(v)[:16]})')
            break
    print(f'  composition: {dict(by_sport)}'
          + (f' · {no_ctx} item(s) have no game_id to verify (props/MLB)'
             if no_ctx else ''))


def _default_card_date() -> str:
    """The ET slate date, not the UTC one.

    ══ 2026-11-17 · THE EVENING CHECK WAS BLIND TO THE LIVE CARD ═══════
    This defaulted to the UTC date, which rolls at 19:00 ET (20:00 in
    summer). So from 7pm ET onward it checked TOMORROW's card, reported
    "NOT PUBLISHED yet — nothing to check" and exited FAIL 0 while
    tonight's published card was live.

    Measured tonight: at 19:30 ET it printed `card integrity 2026-11-18
    ... FAIL 0 warn 0`, and the same run forced to `--date 2026-11-17`
    returned **FAIL 4** on the live Sharp — a total published at a stale
    line (card 'Over 130.5' vs engine 'Over 128.5'), and a
    'Vanderbilt -42.5' whose engine label is 'Under 147.5' and whose
    jerry_read says `pass`, i.e. the write-up is for another market.

    A FAIL 0 that was computed on an unbuilt card is worse than no check,
    because it reads as an all-clear ([[feedback_a_green_step_that_writes_nothing]],
    [[feedback_a_window_from_runner_clock_is_a_day_ahead]],
    [[feedback_hardcoded_et_offset_breaks_in_est]]).
    """
    try:
        from sweat_dates import et_today
        return et_today()
    except Exception:                                         # noqa: BLE001
        from zoneinfo import ZoneInfo
        return str(datetime.now(ZoneInfo('America/New_York')).date())


def _self_test() -> int:
    """Pin the clock and assert the severity rule both ways.

    The rule's whole value is that it still FAILS before the lock. A change
    that only ever returns DRIFT would silently disable this detector, and
    the live data cannot exercise the pre-lock path at 3am, so it is
    asserted here instead of assumed.
    """
    from datetime import datetime as _d
    from zoneinfo import ZoneInfo
    import sweat_dates as _sd
    _et = ZoneInfo('America/New_York')
    cases = [
        ('2026-11-18 09:00', '2026-11-18', 'today pre-lock',   False),
        ('2026-11-18 12:00', '2026-11-18', 'today at lock',    True),
        ('2026-11-18 16:00', '2026-11-18', 'today post-lock',  True),
        ('2026-11-18 02:00', '2026-11-17', 'past slate, 2am',  True),
        ('2026-11-18 02:00', '2026-11-19', 'future slate',     False),
    ]
    _orig = getattr(_sd, 'et_now', None)
    bad = 0
    try:
        for clock, card, label, want in cases:
            _sd.et_now = (lambda c: (lambda: _d.strptime(
                c, '%Y-%m-%d %H:%M').replace(tzinfo=_et)))(clock)
            got, hour, lock = _past_hard_lock('sharp_card', card)
            ok = got == want
            bad += 0 if ok else 1
            print(f'  {"ok " if ok else "BAD"} {label:<18} '
                  f'et={hour:02d} lock={lock:02d} -> '
                  f'{"DRIFT" if got else "FAIL"} (want '
                  f'{"DRIFT" if want else "FAIL"})')
    finally:
        if _orig is not None:
            _sd.et_now = _orig
    print(f'\nself-test: {len(cases) - bad}/{len(cases)} passed')
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=_default_card_date())
    ap.add_argument('--surface', choices=['sweat_card', 'sharp_card'])
    ap.add_argument('--self-test', action='store_true',
                    help='assert the lock severity rule and exit')
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    surfaces = [a.surface] if a.surface else ['sweat_card', 'sharp_card']
    print(f'== card integrity · {a.date} == '
          f'(heavy-ML threshold {HEAVY_ML_THRESHOLD:+d})')
    for s in surfaces:
        check(a.date, s)
    print(f'\n{"=" * 60}\nFAIL {len(fails)} · warn {len(warns)} · '
          f'drift {len(drifts)}')
    if fails:
        print('\nFAILURES:')
        for f in fails:
            print(f'  - {f}')
    if drifts:
        print('\nPOST-LOCK DRIFT (expected — the card is frozen, the engine '
              'is not; not gating):')
        for d in drifts:
            print(f'  - {d}')
    raise SystemExit(1 if fails else 0)
