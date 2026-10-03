"""Turn the team-stats table into the story it already tells, in English.

Andy 2026-09-26:
  "all that data tells a story and that's what Jerry should be talking
   about, all data found in game detail, sharp money flow assessment,
   stats (top rushing team facing bottom run defense), SOS/SOR) It all
   tells a story, sharp money is on team X they are dominant in every
   stat field and fight to cover spreads throughout season, take X team
   laying the points."

WHY THIS IS COMPUTED HERE AND NOT LEFT TO THE MODEL. Jerry is already
handed every one of these numbers. On 2026-09-26 the Vanderbilt @ Auburn
read was given home_sp_overall=11.6, away_sp_overall=9.7, sp_gap=1.9 and
wrote "Auburn's SP+ sits around 24-26 ... Vanderbilt closer to 8-12 —
that 12-15 point efficiency gap mirrors the 10-point spread." Every
downstream sentence ("model and market alignment here is clean") was a
conclusion drawn from a gap that does not exist. The data was not
missing; the model re-derived it from memory because it arrived as raw
JSON rather than as a stated fact.

That is the exact failure the CONFIRMED FACTS mechanism in the read
generators already solves for spreads, totals and moneylines: state the
fact as an English string, hoist it to the top of the prompt, redact the
raw field so there is nothing to re-derive from. This module extends the
same treatment to the stats — so the comparison is arithmetic done in
Python, and Jerry's job is to narrate it rather than to compute it.

Percentiles, not raw ranks, because league_size differs BY STAT (139 for
SP+, 216 for volumetric, 266 for rates) — a 12-place gap is a big edge in
one universe and noise in another. Same correction already made in
GameDetailV2.advantage().

Deliberately NOT a pipeline step. Andy: "these need to be injected in
current processes not adding workflow to confuse system." The read
generators import it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import requests

# Resolved at CALL time, not import time. generate_ncaaf_game_reads.py
# calls load_dotenv() after its import block, so reading the environment
# at module scope bound SUPABASE_URL to None and every request failed with
# "Invalid URL 'None/rest/v1/...'". The story layer degraded quietly to
# "no story" — the whole point of this module lost, with the card still
# rendering fine. Lazy lookup makes the module independent of when the
# importer happens to load its environment.
def _env():
    sb = os.environ.get('SUPABASE_URL')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY'))
    if not sb or not key:
        raise RuntimeError('SUPABASE_URL / key not in environment yet')
    return sb, {'apikey': key, 'Authorization': f'Bearer {key}'}

def market_edge_fact(score, band) -> Optional[str]:
    """State what sweat_score MEASURES, so prose cannot call it conviction.

    ══ 2026-10-02 · ONE VOCABULARY, TWO DIFFERENT QUANTITIES ══
    Andy, on Temple @ South Florida: "why do we have that, why prime but
    lean?" The row carried primary_play.tier = LEAN and sweat_tier = PRIME
    at the same time, and nothing explained which was which.

    They measure different things. app/index.tsx states it exactly:

        sweat_score = 45 + f(|model spread - market|)
                         + f(|confluence|) + f(|total edge|)

    — i.e. HOW FAR OUR NUMBER SITS FROM THE BOOK'S. 45 means the model
    agrees with the market. It is not a verdict on the play; conviction and
    tier are. South Florida was both a LEAN pick AND 6.7 points off the
    market, which is not a contradiction once the two are named apart.

    The bug is that both use PRIME/STRONG/LEAN/PASS, so a reader maps them
    onto one scale. A 2026-09-24 fix already renamed the bottom band from
    "❌ PASS" to "⚖️ MARKET-ALIGNED" with the note "The label was the whole
    bug" — but only that one band was fixed, and the top band still reads
    "🔥 PRIME PLAY".

    It also reaches subscribers in prose. The generators hand the LLM
    `sweat: {score, tier}`, and the 10-02 Penn State read wrote "Sweat
    Score of 60 reflects LIGHT_LEAN conviction" on a pick whose conviction
    was 85. The model was told something called `tier` and used the word it
    implies.

    So the fact is stated in English and hoisted with the other CONFIRMED
    FACTS, the same cure this codebase already applies to SP+ and the
    moneyline.
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 80:
        shape = 'a LARGE gap between our number and the market'
    elif s >= 65:
        shape = 'a CLEAR gap between our number and the market'
    elif s >= 50:
        shape = 'a SLIGHT gap between our number and the market'
    else:
        shape = 'essentially NO gap — our number agrees with the market'
    return (
        f'Sweat Score {s:.0f}'
        + (f' (band {band})' if band else '')
        + f' measures {shape}. It is a measure of PRICING DISTANCE, NOT of '
        'confidence in the pick — 45 would mean the model and the book agree. '
        'Do NOT describe it as conviction, and do NOT present its band as the '
        "pick's tier: the pick's own tier and conviction are given separately "
        'in the ENGINE PICK block and those are the only confidence figures. '
        'A low Sweat Score next to a strong pick is normal and means the '
        'market is priced where we think it should be.')


def reconcile_edge_side(ctx: dict, home: str, away: str,
                        edge_team: Optional[str],
                        edge_fact: Optional[str]) -> Optional[str]:
    """Restate edge_side when it names a different team than the call.

    2026-10-02. edge_side is derived from model-vs-market SPREAD. The
    published call can come from somewhere else entirely — on the 10-02
    NCAAF slate an LR override (_engine=lr_v1) flipped a HOME/LEAN into an
    AWAY ML on two of three games, moving the call and leaving edge_side
    pointing the old way. Because edge_side sits inside the "quote these
    VERBATIM" contract AHEAD of the ENGINE PICK block, the read obeyed the
    first instruction it was given and argued against its own pick:

      Pitt @ VT      published 'Virginia Tech -3', read closed with
                     "Pittsburgh +2.5 has the edge in a low-conviction spot"
      Liberty @ DEL  published 'Liberty ML', read said "the gap favors
                     Delaware's plus-6.5 as the better value"

    Returns None when there is nothing to change (no call, a totals call,
    no directional edge, or the two already agree) so the caller keeps the
    original string. Otherwise returns a replacement that states BOTH the
    model's spread value and the published call, and explicitly forbids
    recommending the side we are not on. Disclosing the tension is the
    product ("we show the receipts"); arguing the other side is the bug.
    """
    if not edge_team or not edge_fact:
        return None
    pp = ctx.get('primary_play')
    if not isinstance(pp, dict):
        return None
    market = str(pp.get('type') or '').lower()
    side = str(pp.get('side') or '').upper()
    # Totals carry no side team; ml/spread/rl do.
    if market not in ('ml', 'spread', 'rl') or side not in ('HOME', 'AWAY'):
        return None
    call_team = home if side == 'HOME' else away
    if call_team == edge_team:
        return None
    label = pp.get('label') or f'{call_team} {market}'
    # Keep the measured gap from the original sentence; drop its
    # "→ take X with points" instruction, which is the part that flipped
    # the prose.
    head = edge_fact.split(' → ')[0].rstrip(')')
    return (
        f'{head}). HOWEVER the published call is {label} '
        f'({market}/{side}), which backs {call_team}, NOT {edge_team}. '
        f'The spread model and the call point different ways on this game. '
        f'Your prose must argue for {label} and may cite this disagreement '
        f'as a reason conviction is limited — state it plainly. Do NOT tell '
        f'the reader to take {edge_team}, and do NOT close by calling '
        f'{edge_team} the better value.')


# A unit of offense pointed at the unit of defense that has to stop it.
# This is the "top rushing team facing bottom run defense" pairing, stated
# once per sport. (attack_key, defend_key, phrase)
# 2026-10-02 · THESE KEYS ARE NOT INTERCHANGEABLE BETWEEN SPORTS.
# The two sports were given identical pair tables on 09-26. NCAAF's keys
# exist; NFL's did not — 7 of the 12 keys named here were absent from
# team_stats_rolling for NFL, so stats.get() returned None, _pct() returned
# None, the loop `continue`d, and the NFL unit-vs-unit story produced
# **0 lines across 223 games** while reporting success. Only the solo keys
# that happened to exist (sos/sor/points_allowed_pg) ever rendered.
#
# Verify with _probe_matchup_story.py after ANY edit here: it reports the
# named-vs-present key diff and counts lines actually produced per sport.
# A hand-written key table against a per-sport schema needs that check.
#
# NCAAF NAMING TRAP: `def_epa_per_play` carries display_label "Def Pass EPA"
# and its values match ncaaf_game_context.def_pass_epa_allowed — the column
# holds PASS EPA allowed, not overall. The label is right, the key name is
# a lie. So it is correct for 'passing game' and WRONG for 'offense
# overall', which is why Northwestern's "Def Pass EPA 0.385" appeared twice
# in the 10-02 Penn State read as two different "clear edges" off one fact.
MATCHUP_PAIRS = {
    'NCAAF': [
        ('rush_yds_pg',      'def_rush_epa_allowed',     'run game'),
        # def_epa_per_play IS pass EPA allowed here — see trap note above.
        ('pass_yds_pg',      'def_epa_per_play',         'passing game'),
        ('off_success_rate', 'def_success_rate_allowed', 'staying on schedule'),
        # Overall offence against a genuinely overall defensive measure.
        # Was def_epa_per_play (pass-only), double-counting one fact.
        ('off_epa_per_play', 'points_allowed_pg',        'offense overall'),
    ],
    'NFL': [
        ('rush_yds_pg',   'rush_yds_allowed_pg', 'run game'),
        ('pass_yds_pg',   'pass_yds_allowed_pg', 'passing game'),
        ('off_pass_epa',  'def_pass_epa',        'passing efficiency'),
        ('off_rush_epa',  'def_rush_epa',        'rushing efficiency'),
        ('total_yds_pg',  'yds_allowed_pg',      'offense overall'),
    ],
}

# Stats worth calling out on their own, as a straight head-to-head.
# NFL carries no turnovers_pg / third_down_pct in team_stats_rolling (both
# were named here and both were dead); ints_pg and sacks_suffered_pg are the
# real equivalents it does carry.
SOLO_KEYS = {
    'NCAAF': ['sp_overall', 'sor', 'sos', 'points_allowed_pg',
              'turnovers_pg', 'third_down_pct'],
    'NFL':   ['sor', 'sos', 'points_allowed_pg', 'ints_pg',
              'sacks_suffered_pg'],
}

# A mismatch has to be genuinely lopsided before it is worth a sentence.
# Top-30% attacking bottom-30% is the bar; anything tighter is two average
# units and saying so adds nothing.
STRONG_PCT = 0.70
WEAK_PCT = 0.30
# Head-to-head gap that counts as "clearly better" on a solo stat.
SOLO_GAP = 0.25


def _page(path: str, params: dict) -> list:
    sb, headers = _env()
    out, off = [], 0
    while True:
        r = requests.get(f'{sb}/rest/v1/{path}', headers=headers,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:160]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


# A derived stat older than this many days is DROPPED rather than narrated.
# Football plays weekly, so a row this stale has missed a whole slate and its
# per-game rate is arithmetic over the wrong divisor. 4 days leaves room for
# one missed nightly run before anything disappears.
MAX_STAT_AGE_DAYS = float(os.environ.get('MATCHUP_STORY_MAX_STAT_AGE_DAYS', '4'))


def load_team_stats(sport: str, season: int,
                    as_of: Optional[str] = None) -> dict:
    """-> {(team, stat_key): row}. One read for the whole slate.

    2026-10-02 · STALE ROWS ARE DROPPED, NOT NARRATED.
    team_stats_rolling serves team_computed_stats as authoritative (migration
    20260926d). Two of its writers — recompute_ncaaf_per_game_stats.py and
    recompute_nfl_epa_units.py — were written 09-26, run once by hand and
    never scheduled, so their rows froze while still being quoted as
    CONFIRMED FACTS with a live-looking percentile beside them. On the 10-02
    NCAAF slate that published Northwestern as the nation's #1 passing
    offence at 403.5 yd/g (truth: 269.0, a 2-game divisor on 3 games) and was
    wrong on 5 of 6 teams.

    `refreshed_at` was NOT in the old explicit select, so the staleness was
    invisible to this module by construction — the missing-column-is-a-silent-
    blank trap. It is selected now and enforced here: a row older than
    MAX_STAT_AGE_DAYS is withheld, so build_story omits the line instead of
    asserting a wrong number. Matches the rule this module already follows
    for untrustworthy teams: no row beats a fabricated one.

    AGE IS MEASURED AGAINST NOW, NOT THE GAME DATE. The question this guard
    answers is "has a writer frozen?", which is a fact about the pipeline at
    generation time. Measuring against a future kickoff instead drops data
    that is perfectly current: reads are generated up to a month ahead, so a
    10-04 slate scored today made 09-29 rows look 5 days stale and withheld
    NFL plays_pg that was the latest figure in existence. `as_of` therefore
    exists only to make this deterministic in tests; leave it None in
    production.

    NOTE the companion defect this depends on: `refreshed_at` is
    `timestamptz DEFAULT now()`, so it only populates on INSERT. Any writer
    that upserts without stamping it explicitly leaves the timestamp pinned
    to the row's first insert and WILL be dropped here despite holding
    correct values. recompute_ncaaf_per_game_stats.py,
    recompute_nfl_epa_units.py and compute_schedule_strength.py all stamp it;
    a new writer must too.
    """
    rows = _page('team_stats_rolling', {
        'sport': f'eq.{sport}', 'season': f'eq.{season}',
        'select': 'team,stat_key,raw_value,rank,league_size,direction,'
                  'display_label,unit,refreshed_at'})
    ref = _parse_ts(as_of) or datetime.now(timezone.utc)
    out, dropped = {}, {}
    for r in rows:
        ts = _parse_ts(r.get('refreshed_at'))
        if ts is not None:
            age = (ref - ts).total_seconds() / 86400.0
            if age > MAX_STAT_AGE_DAYS:
                dropped[r['stat_key']] = dropped.get(r['stat_key'], 0) + 1
                continue
        out[(r['team'], r['stat_key'])] = r
    if dropped:
        print(f'  ⚠ matchup_story: dropped {sum(dropped.values())} stat rows '
              f'older than {MAX_STAT_AGE_DAYS}d — {dropped}')
    return out


def _parse_ts(v):
    """Lenient ISO-8601 -> aware datetime, or None."""
    if not v:
        return None
    s = str(v).strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Bare date, e.g. a game_date passed as as_of.
        try:
            dt = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(row: Optional[dict]) -> Optional[float]:
    """Percentile where 1.0 = best in the league, polarity already handled.

    rank is always 1 = best (the matview and the computed table both
    guarantee it), so direction does not need re-applying here.
    """
    if not row or row.get('rank') is None or not row.get('league_size'):
        return None
    try:
        rank, size = int(row['rank']), int(row['league_size'])
    except (TypeError, ValueError):
        return None
    if size <= 1:
        return None
    return 1.0 - (rank - 1) / (size - 1)


def _fmt(row: dict) -> str:
    v = row.get('raw_value')
    unit = (row.get('unit') or '').strip()
    try:
        fv = float(v)
        s = f'{fv:.0f}' if abs(fv) >= 100 else (f'{fv:.1f}' if abs(fv) >= 10 else f'{fv:.3f}')
    except (TypeError, ValueError):
        s = str(v)
    return f'{s}{(" " + unit) if unit else ""}'


def _ord(p: float) -> str:
    n = max(1, min(99, int(round(p * 100))))
    suf = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suf}'


def build_story(sport: str, home: str, away: str, stats: dict) -> dict:
    """-> {'lines': [str], 'dominance': str|None}

    Every string is safe to quote verbatim: the numbers come straight from
    the same rows the app renders, so the read and the card cannot
    disagree.
    """
    lines: list[str] = []

    # ── unit vs unit ────────────────────────────────────────────────────
    for attacker, defender in ((away, home), (home, away)):
        for atk_key, def_key, phrase in MATCHUP_PAIRS.get(sport, []):
            a_row = stats.get((attacker, atk_key))
            d_row = stats.get((defender, def_key))
            a_p, d_p = _pct(a_row), _pct(d_row)
            if a_p is None or d_p is None:
                continue
            if a_p >= STRONG_PCT and d_p <= WEAK_PCT:
                lines.append(
                    f"{attacker}'s {phrase} ({a_row['display_label']} "
                    f"{_fmt(a_row)}, {_ord(a_p)} pct) meets {defender}'s "
                    f"{d_row['display_label']} {_fmt(d_row)} ({_ord(d_p)} pct) "
                    f"— a clear edge for {attacker}")
            elif a_p <= WEAK_PCT and d_p >= STRONG_PCT:
                lines.append(
                    f"{attacker}'s {phrase} ({a_row['display_label']} "
                    f"{_fmt(a_row)}, {_ord(a_p)} pct) runs into {defender}'s "
                    f"{d_row['display_label']} {_fmt(d_row)} ({_ord(d_p)} pct) "
                    f"— a clear problem for {attacker}")

    # ── straight head-to-heads ──────────────────────────────────────────
    for key in SOLO_KEYS.get(sport, []):
        h_row, a_row = stats.get((home, key)), stats.get((away, key))
        h_p, a_p = _pct(h_row), _pct(a_row)
        if h_p is None or a_p is None:
            continue
        if abs(h_p - a_p) < SOLO_GAP:
            continue
        better, worse = ((home, h_row, h_p), (away, a_row, a_p)) if h_p > a_p \
            else ((away, a_row, a_p), (home, h_row, h_p))
        lines.append(
            f"{h_row['display_label']}: {better[0]} {_fmt(better[1])} "
            f"({_ord(better[2])} pct) vs {worse[0]} {_fmt(worse[1])} "
            f"({_ord(worse[2])} pct)")

    # ── who wins the sheet overall ──────────────────────────────────────
    # Andy's "dominant in every stat field" — counted, not asserted.
    dominance = None
    keys = {k for (_t, k) in stats.keys()}
    h_win = a_win = graded = 0
    for key in keys:
        h_p, a_p = _pct(stats.get((home, key))), _pct(stats.get((away, key)))
        if h_p is None or a_p is None or abs(h_p - a_p) < 0.05:
            continue
        graded += 1
        if h_p > a_p:
            h_win += 1
        else:
            a_win += 1
    if graded >= 6:
        lead, n = (home, h_win) if h_win > a_win else (away, a_win)
        if n / graded >= 0.70:
            dominance = (f'{lead} is the better side in {n} of {graded} '
                         f'measured stat categories')
        else:
            dominance = (f'the stat sheet is split — {home} better in '
                         f'{h_win} of {graded} categories, {away} in {a_win}')

    return {'lines': lines, 'dominance': dominance}


# ── MONEY FLOW, EXPLAINED ────────────────────────────────────────────────
# Andy 2026-09-26: "Jerry should explain the why — if sharp money in
# certain buckets in comparison to market and bets, along with previously
# seen edges. Jerry should explain it coherently and without
# hallucinations."
#
# The "without hallucinations" half is why this is arithmetic in Python
# and not an instruction to the model. splits_summary already carries
# bets_pct_avg, money_pct_avg and sources_agree per market and side; the
# read generator was handing that raw dict to the LLM and hoping. Same
# failure mode that produced "Auburn's SP+ sits around 24-26" against a
# stored 11.6 — the data was present and got paraphrased into fiction.
#
# THE SIGNAL. bets% is ticket count, money% is dollars. When dollars run
# ahead of tickets on a side, the average bet on that side is bigger than
# the average bet against it, which is the usual fingerprint of sharp
# money. When they track each other, the split says nothing and should be
# reported as saying nothing rather than dressed up.
#
# "previously seen edges" is read from v_signal_records, never invented,
# and omitted entirely when the sample is too thin to publish
# (feedback_sample_size_with_pct: every % carries its n, gate at n>=30).

MONEY_MARKET_LABEL = {
    'MLB':   {'rl': 'run line', 'ml': 'moneyline', 'total': 'total', 'spread': 'run line'},
    'NHL':   {'rl': 'puck line', 'ml': 'moneyline', 'total': 'total', 'spread': 'puck line'},
    'NFL':   {'rl': 'spread', 'ml': 'moneyline', 'total': 'total', 'spread': 'spread'},
    'NCAAF': {'rl': 'spread', 'ml': 'moneyline', 'total': 'total', 'spread': 'spread'},
    'NBA':   {'rl': 'spread', 'ml': 'moneyline', 'total': 'total', 'spread': 'spread'},
    'NCAAB': {'rl': 'spread', 'ml': 'moneyline', 'total': 'total', 'spread': 'spread'},
}

# Dollars must lead tickets by this much before it is worth a sentence.
# Below it, the two are tracking and the honest report is "no signal".
SHARP_GAP_PP = 8.0


def _side_name(key, home, away):
    k = str(key).upper()
    if k == 'HOME':
        return home
    if k == 'AWAY':
        return away
    return k.title()          # OVER / UNDER


def money_flow_story(sport, home, away, splits, signal_records=None):
    """-> list[str]. Plain-English money-flow facts, safe to quote verbatim.

    `signal_records` is an optional {signal_key: row} from v_signal_records
    so a live hit rate can be attached. Nothing is invented when it is
    absent — the sentence simply omits the record.
    """
    if not isinstance(splits, dict):
        return []
    labels = MONEY_MARKET_LABEL.get(str(sport).upper(), {})
    lines, quiet = [], []

    for market, sides in splits.items():
        if not isinstance(sides, dict) or market in (
                'captured_at', 'dissent_flags', 'sources_present',
                'triple_confirmed'):
            continue
        label = labels.get(str(market).lower(), str(market))
        best = None
        for side_key, vals in sides.items():
            if not isinstance(vals, dict):
                continue
            bets, money = vals.get('bets_pct_avg'), vals.get('money_pct_avg')
            try:
                bets, money = float(bets), float(money)
            except (TypeError, ValueError):
                continue
            gap = money - bets
            if best is None or gap > best[0]:
                best = (gap, side_key, bets, money, vals.get('sources_agree'))
        if not best:
            continue
        gap, side_key, bets, money, agree = best
        who = _side_name(side_key, home, away)
        src = f', {int(agree)} sources agree' if agree else ''
        if gap >= SHARP_GAP_PP:
            lines.append(
                f'{label}: {money:.0f}% of the money is on {who} but only '
                f'{bets:.0f}% of the bets — dollars are {gap:.0f} points '
                f'ahead of tickets, the usual sign of sharp money{src}')
        else:
            quiet.append(
                f'{label}: money {money:.0f}% vs bets {bets:.0f}% on {who} '
                f'— tracking each other, no sharp signal')

    # Report the quiet markets too. A read that only ever mentions money
    # flow when it fires teaches users that silence means nothing was
    # checked, when in fact it means it was checked and was flat.
    lines.extend(quiet[:2])

    tc = splits.get('triple_confirmed')
    if isinstance(tc, list) and tc:
        lines.append(f'{len(tc)} market/side combinations are confirmed by '
                     f'all three split sources')
    df = splits.get('dissent_flags')
    if isinstance(df, list) and df:
        lines.append(f'sources disagree on: {", ".join(str(d) for d in df[:3])}')

    if isinstance(signal_records, dict):
        for key in ('SHARP_MOVE', 'CONSENSUS_ML', 'CONSENSUS'):
            rec = signal_records.get(key)
            if not rec:
                continue
            w = rec.get('wins_lifetime') or 0
            l = rec.get('losses_lifetime') or 0
            if w + l >= 30:
                lines.append(f'for reference, {key.replace("_", " ").lower()} '
                             f'signals have gone {w}-{l} '
                             f'({100.0*w/(w+l):.0f}%, n={w+l})')
            break
    return lines


def load_signal_records(sport):
    """{signal_key: row} from v_signal_records, or {} on any failure."""
    try:
        rows = _page('v_signal_records', {
            'sport': f'eq.{sport}',
            'select': 'signal_key,kind,wins_lifetime,losses_lifetime,'
                      'hit_pct_lifetime'})
    except Exception:
        return {}
    return {r['signal_key']: r for r in rows if r.get('kind') in (None, 'ok')}
