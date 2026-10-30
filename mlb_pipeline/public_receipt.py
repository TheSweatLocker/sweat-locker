"""Live receipt capture — write the evidence AT publish time.

Andy 2026-09-20: "someone could call me out on record we post and we
would have nothing to show."

Every receipt in public_receipts today (12,774 of them) is
`capture_mode='reconstructed'` — rebuilt after the fact by
backfill_public_receipts.py from mutable source tables. That was the
right emergency move and it is NOT the finished state:

  * reconstructed = an inference from rows that survived. If a source
    row was later mutated, deleted, or filtered out of a view, the
    receipt inherits that damage silently.
  * live = the pick as it went in front of a user, written in the same
    breath as the publish.

They look identical in a table and are worth very different amounts the
moment anyone disputes a record. Only a publisher is entitled to claim
'live', and it has to be an affirmative claim — `capture_mode` is
NEVER defaulted to 'live' anywhere.

FIRST-WRITE-WINS
Writes use `resolution=ignore-duplicates` on
(sport, surface, game_date, source_id), matching prop_publish_lock. The
live write happens at publish, before any backfill runs, so live always
wins the race and a later reconstruction can never overwrite it.

FAIL-SOFT BUT LOUD
A receipt failure must never block a publish — the user-facing card
matters more than the audit row. But it must never be silent either:
that is the exact `|| echo` failure class we spent 2026-09-20 removing.
Every failure prints and is counted in the returned tally.

USAGE
    from public_receipt import capture, sharp_card_rows
    capture(sharp_card_rows(items, today), surface='sharp_card')
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
H_WRITE = {
    'apikey': KEY,
    'Authorization': f'Bearer {KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=ignore-duplicates,return=minimal',
}

UNIQUE_KEY = 'sport,surface,game_date,source_id'

# == 2026-10-28 . A SPREAD PRICE THAT NO BOOK POSTS MUST NOT FREEZE ==========
# pick_odds allows NULL -> value and then FREEZES (migration 20260918b), so a
# wrong price written once is wrong forever. Measured tonight: 8 rl/spread
# receipts carry a price impossible for that market, 7 already graded --
#
#     2026-10-28 NBA   Cleveland Cavaliers -9.5   -400   (ungraded)
#     2026-09-27 NFL   DET -9.5                   -300
#     2026-09-26 NCAAF Mississippi State -1.5     -230   WIN
#
# -- every one a moneyline captured against a spread pick after the pick
# moved. A WIN at -230 pays 0.43u where the real -110 pays 0.91u, so the
# published record understates its own returns, and none of it can be
# repaired.
#
# Bands are the SAME measured p1..p99 spread quotes repair_stale_read_prices
# uses, and they are per sport on purpose: MLB's run line and NHL's puckline
# are fixed at +-1.5 and legitimately price far wider than a football or
# basketball spread, where the line moves and the price stays near -110. A
# flat band would have destroyed 32 correct prices there.
#
# NULL, never a guessed -110: backfill_receipt_prices can still fill a NULL
# from line_history at the pick's own line, and a NULL is visibly absent
# where a stale number reads as real (feedback_gate_the_stored_row).
_SPREAD_BANDS = {
    'NFL': (-150, 130), 'NCAAF': (-150, 130), 'NBA': (-150, 130),
    'NCAAB': (-150, 130), 'MLB': (-400, 350), 'NHL': (-400, 350),
}
_SPREAD_MARKETS = ('rl', 'spread')
_INT_FIELDS = ('conviction', 'pick_odds')
_MARKET_MAX = 24

# ══ 2026-10-23 · B142 · A PICK ON THESE MARKETS IS NOTHING WITHOUT A NUMBER ══
# Measured since 09-01: 126 receipts carried one of these markets with
# pick_line NULL, and 114 of them were GRADED into the published record
# (60 WIN / 51 LOSS / 3 PUSH). "Over" with no number cannot be graded against
# anything we showed, so those outcomes were scored against a line the user
# never saw. Both write paths did it — 68 reconstructed, 58 live.
#
# Same family as feedback_consensus_line_must_be_posted: a missing line makes
# a fake price, and here it also made a fake result.
#
# ml is deliberately absent: a moneyline has no line and a NULL is correct.
_LINE_REQUIRED_MARKETS = ('total', 'rl', 'spread', 'puckline')

# Anchored at the END of the label on purpose. A loose \d+ search picks the
# 76 out of "Philadelphia 76ers -5.5" and stores a 76-point spread; the
# trailing token is the line in every label shape we emit ("Under 6.5",
# "Baylor -6", "Over 230.5"). A label with no trailing number ("Ottawa
# Senators RL", "Over") correctly yields nothing and the row is refused.
_TRAILING_LINE_RE = re.compile(r'(?:^|\s)([+-]?\d+(?:\.\d+)?)\s*$')
_LINE_SANITY_MAX = 400.0


def recover_line(row: dict):
    """Fill pick_line from pick_label where the label carries the number.

    Returns the row. Some live sweat_card rows store "Under 6.5" in
    pick_label with pick_line NULL — the number was never missing, just
    never parsed into the column the grader and the UI both read.
    """
    if row.get('pick_line') is not None:
        return row
    if str(row.get('market') or '').lower() not in _LINE_REQUIRED_MARKETS:
        return row
    m = _TRAILING_LINE_RE.search(str(row.get('pick_label') or ''))
    if not m:
        return row
    try:
        v = float(m.group(1))
    except (TypeError, ValueError):
        return row
    if abs(v) <= _LINE_SANITY_MAX:
        row['pick_line'] = v
    return row


def unactionable(row: dict) -> bool:
    """True when this row claims a line-bearing market but carries no line."""
    return (str(row.get('market') or '').lower() in _LINE_REQUIRED_MARKETS
            and row.get('pick_line') is None)


def sanitize(row: dict) -> dict:
    """Normalise one receipt row in place and return it.

    Shared by live capture AND backfill_public_receipts so the two can
    never drift. Both defects below were found in live data on
    2026-09-20:

      * INTEGER COERCION. conviction and pick_odds are integer columns
        but sources hand over floats (daily_degen passes avg_conviction
        as 79.0). PostgREST rejects with 22P02 and the batch is
        all-or-nothing, so one float killed all 148 rows.
      * MARKET CLAMP. Two rows carried an entire markdown write-up in
        `market` because a source had prose where a market code belonged.
        A field holding the wrong KIND of value quietly poisons every
        GROUP BY built on it. Clamp, and park the original in audit
        rather than dropping it.
    """
    for f in _INT_FIELDS:
        v = row.get(f)
        if v is None or isinstance(v, int):
            continue
        try:
            row[f] = int(round(float(v)))
        except (TypeError, ValueError):
            row[f] = None
    # refuse a spread price outside what that sport's spreads actually trade
    # at -- it is a moneyline that followed the pick, and pick_odds freezes
    _mk = str(row.get('market') or '').strip().lower()
    _od = row.get('pick_odds')
    if _mk in _SPREAD_MARKETS and isinstance(_od, int):
        _lo, _hi = _SPREAD_BANDS.get(
            str(row.get('sport') or '').strip().upper(), (-150, 130))
        if not (_lo <= _od <= _hi):
            row['pick_odds'] = None
            _aud = row.get('audit')
            if not isinstance(_aud, dict):
                _aud = {} if _aud is None else {'prior_audit': _aud}
            _aud['price_refused'] = _od
            _aud['price_refused_why'] = (
                f'{_od} is outside the measured {_mk} band [{_lo}, {_hi}] '
                f'for this sport — a moneyline on a spread pick')
            row['audit'] = _aud
    m = row.get('market')
    if isinstance(m, str) and (len(m) > _MARKET_MAX or '\n' in m):
        aud = row.get('audit')
        if isinstance(aud, dict):
            aud['market_raw'] = m[:400]
        else:
            row['audit'] = {'market_raw': m[:400]}
        # Take the first token that still has content after stripping
        # markdown. The earlier version took split()[0] unconditionally,
        # so "** ml  \n**side:** home" stripped to '' and fell through to
        # 'unknown' — discarding a perfectly recoverable 'ml'. Walk the
        # tokens instead; only give up if none survives.
        code = ''
        for tok in m.split():
            cleaned = tok.strip('*:|-# \t\n')
            if cleaned:
                code = cleaned[:_MARKET_MAX]
                break
        row['market'] = code.lower() or 'unknown'
    return row


def _sid(*parts: Any) -> str:
    """Stable source_id from its parts.

    Must be unique within (sport, surface, game_date): one game can carry
    both an ml and a total pick on the same card, so the market has to be
    part of the key or the second pick silently loses the conflict and is
    never recorded.
    """
    raw = ':'.join('' if p is None else str(p) for p in parts)
    if len(raw) <= 120:
        return raw
    return raw[:80] + ':' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _live_keys(payload: list):
    """Keys already holding a NON-VOID receipt, or None if unknowable.

    ══ 2026-10-27 · WHY THIS EXISTS INSTEAD OF ?on_conflict= ══
    20261025a replaced the full UNIQUE CONSTRAINT on
    (sport, surface, game_date, source_id) with a PARTIAL unique index
    (WHERE result IS NULL OR lower(result) <> 'void') so a voided receipt
    could be replaced by the correct side. That is the right data rule and
    the 19 void+replacement pairs in the table depend on it.

    But Postgres can only infer a PARTIAL index as an ON CONFLICT arbiter
    when the statement also supplies the index predicate, and PostgREST's
    `on_conflict=` parameter carries column names only. So every live write
    returned 42P10 "no unique or exclusion constraint matching the ON
    CONFLICT specification" from 10-25 onward: public_receipts took no write
    for two days on EVERY surface while the cards published normally,
    because capture() is fail-soft and nobody reads the step log.

    Dropping the parameter clears 42P10, but a plain INSERT then raises
    23505 on the second publish of the day and the batch is all-or-nothing.
    So first-write-wins is enforced here: read the keys that are already
    live, skip them, insert the rest.

    Returns None when the read fails. The caller must then NOT pre-filter,
    because an empty set would read as "nothing exists yet" and would
    duplicate the entire card.
    """
    if not payload:
        return set()
    seen = set()
    groups = {(str(r.get('sport')), str(r.get('surface')),
               str(r.get('game_date'))) for r in payload}
    for sport, surf, gd in groups:
        off = 0
        while True:
            try:
                resp = requests.get(
                    f'{SB}/rest/v1/public_receipts', headers=H_READ,
                    params={'select': 'source_id,result',
                            'sport': f'eq.{sport}', 'surface': f'eq.{surf}',
                            'game_date': f'eq.{gd}',
                            'limit': '1000', 'offset': str(off)},
                    timeout=45)
            except Exception as e:                          # noqa: BLE001
                print(f'  ⚠ receipts ({surf}): dedupe read raised: {e}')
                return None
            if resp.status_code != 200:
                print(f'  ⚠ receipts ({surf}): dedupe read HTTP '
                      f'{resp.status_code}: {resp.text[:160]}')
                return None
            try:
                batch = resp.json()
            except ValueError:
                return None
            if not isinstance(batch, list):
                return None
            for row in batch:
                if str(row.get('result') or '').strip().lower() != 'void':
                    seen.add((sport, surf, gd, str(row.get('source_id'))))
            if len(batch) < 1000:
                break
            off += 1000
    return seen


def capture(rows: Iterable[dict], surface: str, dry_run: bool = False,
            quiet: bool = False) -> dict:
    """Write live receipts. Returns {'written': n, 'failed': n, 'skipped': n}.

    Never raises: a publisher must not lose its card because an audit row
    failed. Always prints on failure.
    """
    rows = [r for r in (rows or []) if r]
    out = {'written': 0, 'failed': 0, 'skipped': 0}
    if not rows:
        return out
    if not (SB and KEY):
        print(f'  ⚠ receipts ({surface}): SUPABASE env missing — {len(rows)} NOT captured')
        out['failed'] = len(rows)
        return out
    now = datetime.now(timezone.utc).isoformat()
    payload = []
    refused = []
    for r in rows:
        if not r.get('sport') or not r.get('game_date') or not r.get('source_id'):
            out['skipped'] += 1
            continue
        r.setdefault('surface', surface)
        r.setdefault('published_at', now)
        # The affirmative claim. Nothing else in the codebase sets this.
        r['capture_mode'] = 'live'
        r = sanitize(recover_line(r))
        # B142: refuse a line-bearing market with no line. A receipt is the
        # published record, and an ungradeable row in it becomes a fabricated
        # W/L the moment a grader guesses the line. Loud, never silent.
        if unactionable(r):
            refused.append(r)
            out['skipped'] += 1
            continue
        payload.append(r)
    # ══ 2026-10-30 · THE REST OF THE INVARIANTS, AT THIS SAME CHOKEPOINT ══
    # `unactionable` above refuses a line-bearing market with no line. It was
    # the only guard of its kind, and tonight's board produced four more
    # defects of exactly the same shape — a priceless play, a parlay whose
    # label disagreed with its own legs, and a fade on a market already
    # measured negative. They belong here with it rather than in each
    # composer: a guard on one of eight sources is not a guard
    # (feedback_guard_belongs_at_the_chokepoint).
    try:
        from receipt_invariants import screen as _screen
        payload, _inv_refused = _screen(payload, surface, quiet=quiet)
        if _inv_refused:
            out['skipped'] += len(_inv_refused)
            out['invariant_refused'] = len(_inv_refused)
    except ImportError:
        # Loud: a missing guard must not read as a clean run.
        print(f'  !! receipt_invariants unavailable — {surface} rows written '
              f'UNCHECKED')

    if out['skipped'] and not quiet:
        print(f'  ⚠ receipts ({surface}): {out["skipped"]} row(s) not captured')
    if refused:
        out['unactionable'] = len(refused)
        print(f'  *** receipts ({surface}): REFUSED {len(refused)} row(s) — '
              f'line-bearing market with no pick_line ***')
        for r in refused[:8]:
            print(f'      {r.get("sport")} {r.get("market")} '
                  f'{r.get("game_date")} {r.get("matchup")} '
                  f'label={r.get("pick_label")!r} src={r.get("source_id")}')
    if not payload:
        return out
    if dry_run:
        out['written'] = len(payload)
        if not quiet:
            print(f'  [DRY] would capture {len(payload)} live receipt(s) → {surface}')
        return out
    # First-write-wins enforced in code, not by ON CONFLICT — see _live_keys
    # for why the partial index of 20261025a cannot be an arbiter here.
    existing = _live_keys(payload)
    if existing is None:
        print(f'  ⚠ receipts ({surface}): cannot verify what is already '
              f'recorded — refusing to write rather than duplicate the card')
        out['failed'] = len(payload)
        return out
    fresh = [r for r in payload
             if (str(r.get('sport')), str(r.get('surface')),
                 str(r.get('game_date')),
                 str(r.get('source_id'))) not in existing]
    already = len(payload) - len(fresh)
    if already:
        out['already'] = already
        if not quiet:
            print(f'  receipts ({surface}): {already} already recorded, '
                  f'left untouched')
    if not fresh:
        return out

    def _send(batch):
        return requests.post(f'{SB}/rest/v1/public_receipts',
                             headers=H_WRITE, json=batch, timeout=60)

    try:
        resp = _send(fresh)
        if resp.status_code in (200, 201, 204):
            out['written'] = len(fresh)
            if not quiet:
                print(f'  🧾 captured {len(fresh)} LIVE receipt(s) → {surface}')
        elif resp.status_code == 409:
            # Raced another writer. The batch is all-or-nothing, so retry one
            # row at a time: a single collision must not cost the rest of the
            # card its receipts.
            ok = raced = failed = 0
            for r in fresh:
                rr = _send([r])
                if rr.status_code in (200, 201, 204):
                    ok += 1
                elif rr.status_code == 409:
                    raced += 1
                else:
                    failed += 1
                    print(f'  ⚠ receipts ({surface}) row HTTP '
                          f'{rr.status_code}: {rr.text[:160]}')
            out['written'] = ok
            out['already'] = out.get('already', 0) + raced
            out['failed'] = failed
            if not quiet:
                print(f'  🧾 captured {ok} LIVE receipt(s) → {surface} '
                      f'({raced} raced, {failed} failed)')
        else:
            out['failed'] = len(fresh)
            print(f'  ⚠ receipts ({surface}) HTTP {resp.status_code}: '
                  f'{resp.text[:240]}')
    except Exception as e:                                  # noqa: BLE001
        out['failed'] = len(fresh)
        print(f'  ⚠ receipts ({surface}) raised: {e}')
    return out


# ── surface adapters ────────────────────────────────────────────────

def _side_for_receipt(it: dict, market: str, prop_side):
    """The side taken, for NON-prop markets, so CLV can be computed.

    ══ 2026-10-19 · pick_side WAS NULL ON EVERY CARD SPREAD RECEIPT ══
    All 65 sharp_card and all 41 sweat_card spread receipts carried
    pick_side=NULL, while game_read carried it on 409 of 409. Closing line
    value needs the side: a line of -6.0 is a different bet depending on
    whether we took the home team or the away team, so without it a receipt
    cannot be compared to the close at all, and CLV was computable on only
    93 of 515 spread picks.

    The card item already HAS it (`side`: 'HOME'). Both writers simply never
    read it. Falls back to deriving it from the label against the matchup,
    then to None - never a guess.
    """
    if prop_side:
        return prop_side
    sd = str(it.get('side') or it.get('pick_side') or '').strip().upper()
    if sd in ('HOME', 'AWAY', 'OVER', 'UNDER'):
        return sd
    if str(market).lower() in ('total', 'totals', 'ou'):
        lab = str(it.get('label') or it.get('pick') or '').upper()
        if lab.startswith('OVER') or ' OVER ' in lab:
            return 'OVER'
        if lab.startswith('UNDER') or ' UNDER ' in lab:
            return 'UNDER'
        return None
    # team markets: match the label's team against the matchup's two sides.
    # PREFIX then WORD BOUNDARY, never a bare substring: NFL matchups use
    # 2-3 letter codes and 'NO' is inside 'Minnesota', 'LA' inside 'Atlanta',
    # so a substring test resolves the wrong side on real rows. A tie or an
    # ambiguous match returns None - a NULL side is recoverable, a wrong one
    # silently inverts the pick.
    import re as _re
    lab = str(it.get('label') or it.get('pick') or '')
    mu = str(it.get('game') or it.get('matchup') or '')
    if ' @ ' in mu and lab:
        away, home = (q.strip() for q in mu.split(' @ ', 1))
        head = lab.split('  ·')[0].strip().lower()

        def _score(team):
            t = str(team or '').strip().lower()
            if not t:
                return 0
            if head.startswith(t):
                return 2                      # "Baylor -6" vs home "Baylor"
            if _re.search(rf'{_re.escape(t)}', head):
                return 1                      # whole word elsewhere
            return 0
        sh, sa = _score(home), _score(away)
        if sh > sa:
            return 'HOME'
        if sa > sh:
            return 'AWAY'
    return None


def sharp_card_rows(items: list, game_date: str) -> list[dict]:
    """Shape published Sharp Card items into receipt rows.

    Item shape (verified against live jerry_cache.sharp_card_2026-09-20):
      sport, type, tier, conviction, pick, matchup, line, odds, units,
      reason, game_id  — prop items additionally carry `id`.
    """
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sport = str(it.get('sport') or '').upper()
        market = str(it.get('type') or '').lower()
        if not sport or not market:
            continue
        # Anchor precedence matters. `game_id` was only added to Sharp
        # Card items on 2026-09-17, so every earlier totals pick carries
        # game_id=None with a generic label ("Under 8.0") — three
        # different games on one slate produced the SAME source_id and
        # two of the three lost the conflict silently. 11 of 410 picks
        # vanished that way. Fall back to the matchup, which identifies
        # the game even when game_id is absent, and only then to the
        # label.
        anchor = it.get('id') if market == 'prop' else it.get('game_id')
        anchor = (anchor or it.get('game_id') or it.get('id')
                  or it.get('matchup') or it.get('pick'))
        if not it.get('game_id') and not it.get('id'):
            # Generic labels repeat across games on the same slate; pin
            # the line too so two different totals never coincide.
            anchor = f'{anchor}|{it.get("pick")}|{it.get("line")}'
        out.append({
            'sport': sport,
            'surface': 'sharp_card',
            'game_date': game_date,
            'source_id': _sid(market, anchor),
            'source_table': 'jerry_cache.sharp_card',
            'game_id': it.get('game_id'),
            'matchup': it.get('matchup'),
            'market': market,
            'pick_label': it.get('pick'),
            'pick_line': it.get('line'),
            'pick_side': _side_for_receipt(it, market, None),
            'pick_odds': it.get('odds'),
            'tier': (it.get('tier') or None),
            'conviction': it.get('conviction'),
            'audit': {
                'units': it.get('units'),
                'reason': (it.get('reason') or '')[:600],
                'captured_by': 'generate_sharp_card',
                # 2026-10-06: `conviction` above is now the BASE score, so it
                # agrees with `tier` (it used to be refit, which is why The
                # Sharp showed conv 24 beside a STRONG tier while the Sweat
                # Card showed 65). refit is kept here rather than dropped:
                # it is a nearly independent score (r=+0.131 against base on
                # 386 graded rows) and the only way to settle live-or-die is
                # to keep scoring it on published picks.
                'refit_conviction': it.get('refit_conviction'),
            },
        })
    return out


_RESULT_MAP = {
    'win': 'WIN', 'w': 'WIN', 'hit': 'WIN',
    'loss': 'LOSS', 'l': 'LOSS', 'lose': 'LOSS', 'miss': 'LOSS',
    'push': 'PUSH', 'tie': 'PUSH',
    # ══ 2026-09-26 · PENDING IS NOT A RESULT ══
    # This used to store the string 'PENDING'. grade_public_receipts selects
    # `result=is.null` to find work, so a non-null sentinel meaning "not
    # graded yet" made the row invisible to the grader FOREVER — it looked
    # graded to the query and ungraded to a human. 103 receipts were stuck
    # that way, 90 of them Sweat Card, including a POTD (Chicago White Sox
    # ML, conviction 88) whose game had finished and which graded WIN as a
    # game_read on the same slate.
    #
    # Pending is the ABSENCE of a result, and NULL already means exactly
    # that. Two spellings for one state is what created the hole.
    'pending': None, 'no-pick': None, 'none': None, '': None,
}


def norm_result(v) -> str | None:
    """Source tables spell results however they like — daily_dawg says
    'Win'/'Loss'/'Push', daily_best_bet_history adds 'Pending'/'no-pick'.
    public_receipts is uppercase (WIN/LOSS/PUSH) with NULL for not-yet-
    graded. A record split across two spellings of the same outcome is a
    record nobody can total, so normalise at the boundary — and "pending"
    normalises to NULL, not to a fourth value (see _RESULT_MAP).
    """
    if v is None:
        return None
    return _RESULT_MAP.get(str(v).strip().lower(), str(v).strip().upper() or None)


def _bet_market(label: str) -> str | None:
    """Best-effort real market behind a display label ("Braves ML (Jerry
    88/100)" -> ml). Recorded in audit, never in `market` — POTD/DotD
    keep their own market codes so the 177 rows already using them stay
    countable."""
    t = str(label or '').lower()
    if ' ml' in t or t.endswith('ml'):
        return 'ml'
    if 'over' in t or 'under' in t:
        return 'total'
    if any(s in t for s in ('-1.5', '+1.5', 'rl', 'run line')):
        return 'rl'
    return None


def potd_rows(records: list, game_date: str | None = None) -> list[dict]:
    """Pick of the Day, from daily_best_bet_history rows.

    Shape (verified, 132 rows back to 2026-04-10): bet_date, sport, game,
    lean, sweat_score, odds_american, result, narrative. One POTD per
    day, so bet_date alone is a sufficient source_id.
    """
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        gd = str(rec.get('bet_date') or game_date or '')[:10]
        sport = str(rec.get('sport') or 'MLB').upper()
        lean = rec.get('lean')
        if not gd or not lean:
            continue
        out.append({
            'sport': sport,
            'surface': 'potd',
            'game_date': gd,
            'source_id': _sid('potd', gd),
            'source_table': 'daily_best_bet_history',
            'matchup': rec.get('game'),
            'market': 'potd',
            'pick_label': lean,
            'pick_odds': rec.get('odds_american'),
            'conviction': rec.get('sweat_score'),
            'result': norm_result(rec.get('result')),
            'audit': {
                'bet_market': _bet_market(lean),
                'narrative': (rec.get('narrative') or '')[:600],
                'captured_by': 'jerry_anchor_potd',
            },
        })
    return out


def _num_or_none(v):
    """float(v) or None — so a sign flip never raises on a NULL or a string."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _side_from_matchup(team, matchup) -> str | None:
    """HOME / AWAY for a team pick, from an "Away @ Home" matchup string.

    Needed because a price is stored per SIDE, not per team name: without
    this, every Dawg receipt is unpriceable. 130 of 130 Dawg receipts carry
    no price today for exactly this reason — `daily_dawg` has no price column
    of its own, and the fallback to line_history needs a side.

    Returns None rather than guessing when the team is not clearly one of the
    two: a wrong side yields the OPPOSING price, which looks valid and
    silently inverts the ROI of every row built on it.
    """
    t = str(team or '').strip().lower()
    m = str(matchup or '')
    if not t or '@' not in m:
        return None
    away, _, home = m.partition('@')
    away, home = away.strip().lower(), home.strip().lower()
    in_home = t in home or home in t
    in_away = t in away or away in t
    if in_home and not in_away:
        return 'HOME'
    if in_away and not in_home:
        return 'AWAY'
    return None


def dawg_rows(records: list, game_date: str | None = None) -> list[dict]:
    """Dawg of the Day, from daily_dawg rows.

    Shape (verified, 123 rows back to 2026-04-22): game_date, team,
    matchup, game_id, tier, conviction, close_spread, spread_delta,
    result. One per day.

    2026-10-28: THE PRICE EXISTS NOW. The multi-sport Dawg rebuild
    shipped today added price_american / price_implied / priced_at /
    market / pick_side to `daily_dawg`, so the sentence below stopped
    being true the same day it was still being relied on. Verified on
    tonight's two rows: MLB +135, NHL +117, both receipts pick_odds NULL.
    `pick_odds` now comes straight off the source row, and the source's
    own `pick_side` wins over the derived one when it is present --
    the generator knows which side it picked; this builder is guessing
    from a matchup string.

    `market` stays 'dotd'. The source row says 'ml', but the receipt is
    what reached the user and its market sets the denominator every
    published Dawg record is computed against (the R3 decision, 10-28).
    The source's market is recorded in `audit` instead, so a later ROI
    pass can use the right breakeven without the record moving.

    2026-10-06: this builder set no `pick_odds` and no `pick_side`, and
    `daily_dawg` had no price column at all — so all 130 Dawg receipts are
    unpriced. The price is recoverable from line_history (which holds spread
    prices for every sport), but only given a side, so `pick_side` is now
    derived from the matchup. `pick_line` already carries close_spread, which
    is the other half line_history needs to price the right line.
    """
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        gd = str(rec.get('game_date') or game_date or '')[:10]
        team = rec.get('team')
        if not gd or not team:
            continue
        _side = (str(rec.get('pick_side')).strip().upper()
                 if rec.get('pick_side')
                 else _side_from_matchup(team, rec.get('matchup')))
        _cs = _num_or_none(rec.get('close_spread'))
        out.append({
            'pick_side': _side,
            # The line FROM THE PICKED SIDE, not the home handicap.
            # Verified 2026-10-06 on 4 of 4 games with spread quotes:
            # close_spread equals the HOME line every time, while
            # line_history stores each side's own line (home +1.5 / away
            # -1.5 are the two halves of one market). The Dawg is usually
            # the AWAY dog, so storing close_spread unflipped pointed at the
            # FAVOURITE's run line — a real price for the opposite bet.
            # 2026-10-20 · GUARD THE NEGATION. This was
            #     -_num_or_none(rec.get('close_spread')) if _side == 'AWAY'
            # and `-None` is a TypeError, so ONE row with a NULL close_spread
            # (2026-07-09 Cubs @ Orioles, the only one of 132) raised inside
            # this list comprehension and took the ENTIRE dawg backfill down
            # on every run since the flip was added on 10-06:
            #     x dawg failed: bad operand type for unary -: 'NoneType'
            # Nothing was lost only because MLB's last dawg was 10-13 and the
            # upsert ignores duplicates; the next dawg pick in any sport would
            # have written no receipt at all. A row with no line now yields a
            # null pick_line, which is honest, instead of killing 131 others.
            'pick_line': (-_cs if (_side == 'AWAY' and _cs is not None) else _cs),
            'sport': str(rec.get('sport') or 'MLB').upper(),
            'surface': 'dawg',
            'game_date': gd,
            'source_id': _sid('dotd', rec.get('game_id') or gd),
            'source_table': 'daily_dawg',
            'game_id': rec.get('game_id'),
            'matchup': rec.get('matchup'),
            'market': 'dotd',
            'pick_label': team,
            'pick_odds': _num_or_none(rec.get('price_american')),
            'tier': rec.get('tier'),
            'conviction': rec.get('conviction'),
            'result': norm_result(rec.get('result')),
            'audit': {
                'spread_delta': rec.get('spread_delta'),
                'captured_by': 'generate_dawg_of_day',
                'price_basis': ('publish' if rec.get('price_american')
                                is not None else None),
                'price_source': ('daily_dawg.price_american'
                                 if rec.get('price_american') is not None
                                 else None),
                'source_market': rec.get('market'),
            },
        })
    return out


# Sweat Card `type` is a DISPLAY label, not a market code. Verified over
# 141 cached days / 854 top_8 items: Over/Under, ML, DotD, POTD and a
# long tail of prop_* families.
_SWEAT_MARKET = {
    'ml': 'ml',
    'rl': 'rl',
    'total': 'total',
    'over/under': 'total',
    'potd': 'potd',
    'dotd': 'dotd',
}


def _sweat_market(raw: str) -> str:
    t = str(raw or '').strip().lower()
    if t.startswith('prop_') or t == 'prop':
        return 'prop'
    return _SWEAT_MARKET.get(t, t or 'unknown')


def prop_jerry_rows(reads: list, sport: str) -> list[dict]:
    """Receipt rows for Prop Jerry picks, for capture AT PUBLISH TIME.

    == 2026-10-29 · PROP JERRY HAD NO LIVE CAPTURE AT ALL ==
    Measured on 10-29 over receipts for games from 10-22:

        surface        live  reconstructed  live%
        sweat_card       28         3       90.3%
        sharp_card       57         3       95.0%
        prop_jerry        0       925        0.0%

    Zero. Every prop receipt in the table exists only because
    backfill_public_receipts rebuilt it afterwards, and that path stamps
    capture_mode='reconstructed' by design. capture() is called from the
    sweat card, sharp card, dawg, POTD and fade publishers — props were
    simply never wired in, and nothing noticed because the backfill kept
    the table looking full.

    It is the reason the prop record cannot be published. Same day, on the
    curated prop surfaces where both modes exist:

        curated props  reconstructed  59.40%  ROI +5.06%
        curated props  live           56.00%  ROI -3.65%

    — the edge is in the reconstructed half only. With prop_jerry at 0%
    live there is no honest prop number to quote at all, for any sport.

    SHAPE IS COPIED FROM backfill_prop_jerry DELIBERATELY: same pick_label,
    same FADE flip, same `audit.side_convention='backed'` stamp. A live row
    and a backfilled row for the same read must be indistinguishable, or
    the two paths grade differently (the 10-04 two-conventions mess is
    exactly that failure, and it left 123 fade receipts permanently
    unsettleable).

    PASS reads are NOT captured. A receipt is the record of a play we
    published; a PASS is the record of one we declined, and writing it as a
    pick is how a denominator gets inflated with plays nobody made.
    """
    out: list[dict] = []
    for row in (reads or []):
        if not isinstance(row, dict):
            continue
        verdict = str(row.get('call_verdict') or '').upper() or None
        if verdict not in ('BACK', 'FADE'):
            continue                      # PASS / unset is not a published pick
        direction = str(row.get('direction') or '').lower()
        # A FADE backs the OPPOSITE of the prop's own side.
        backed = direction
        if verdict == 'FADE' and direction in ('over', 'under'):
            backed = 'under' if direction == 'over' else 'over'
        try:
            odds_int = (int(row['book_odds'])
                        if row.get('book_odds') is not None else None)
        except (TypeError, ValueError):
            odds_int = None
        line = row.get('prop_line')
        out.append({
            'sport': row.get('sport') or sport,
            'game_id': row.get('game_id'),
            'surface': 'prop_jerry',
            'market': 'prop',
            'game_date': row.get('game_date'),
            'player_name': row.get('player_name'),
            'prop_type': row.get('prop_type'),
            'pick_side': backed.upper() if backed in ('over', 'under') else None,
            'pick_line': line,
            'pick_odds': odds_int,
            'matchup': None,
            'pick_label': (
                f"{row.get('player_name', '')} "
                f"{(backed or direction).upper()} {line} {row.get('prop_type', '')}"
                f" @ {odds_int if odds_int else '?'}"
            ),
            'tier': None,
            'conviction': row.get('conviction'),
            'source_table': 'prop_jerry_reads',
            'source_id': str(row.get('id')),
            'audit': {'call_verdict': verdict, 'side_convention': 'backed'},
        })
    return out


def sweat_card_rows(card: dict, game_date: str) -> list[dict]:
    """Shape a published Sweat Card into receipt rows.

    Takes the whole card, not one list, because the picks users see live
    in TWO sections and only counting one silently halves the record —
    the same mistake publish_lock made until 2026-09-19, when football
    picks turned out to have never been locked at all.

      top_8          dashboard top-8. Carries NO `sport` field; every
                     source_table observed across 141 days is MLB
                     (mlb_pipeline_props, mlb_game_results, daily_dawg,
                     daily_best_bet_history), and football lives in its
                     own section, so MLB is the correct default rather
                     than a guess.
      football_picks NCAAF/NFL, carries an explicit `sport` + `game_id`.
    """
    if not isinstance(card, dict):
        return []
    out = []
    sections = (('top_8', 'MLB'), ('football_picks', None))
    for section, default_sport in sections:
        for it in (card.get(section) or []):
            if not isinstance(it, dict):
                continue
            raw_type = it.get('type')
            market = _sweat_market(raw_type)
            sp = str(it.get('sport') or default_sport or '').upper()
            label = it.get('label') or it.get('pick')
            if not sp or not label:
                continue
            # source_key is the anchor the card itself uses: a game_id for
            # ML/total, a prop id (sometimes "table:id") for props, and the
            # DATE for POTD/DotD — which is why market has to be part of
            # the key, or the day's POTD and DotD collapse into one row.
            anchor = it.get('source_key') or it.get('game_id') or it.get('id')
            if isinstance(anchor, str) and ':' in anchor:
                anchor = anchor.split(':', 1)[1]
            if not anchor:
                anchor = f'{it.get("game") or ""}|{label}'
            # ══ 2026-10-04 · A PROP RECEIPT MUST CARRY ITS OWN IDENTITY ══
            # These rows were written with player_name, prop_type, pick_line
            # and pick_side ALL NULL, with everything packed into source_id as
            # 'prop:Shohei Ohtani|hits_under|1.5'. grade_public_receipts
            # matches on the structured columns, so every sweat_card prop was
            # invisible to it — on 10-03 that stranded Ohtani and Kim, both of
            # which had WON, while the prop_jerry receipts for the same players
            # graded normally because that writer populates the columns.
            # Same table, same day, different writer.
            #
            # The card already knows all of it: source_key is
            # "{player}|{prop_type}|{prop_line}". Parse it rather than ask the
            # grader to.
            _player = _ptype = _pside = None
            _pline = it.get('line')
            if market == 'prop':
                _sk = str(it.get('source_key') or '')
                _parts = _sk.split('|')
                if len(_parts) == 3:
                    _player, _ptype, _raw_line = (p.strip() for p in _parts)
                    try:
                        _pline = float(_raw_line)
                    except (TypeError, ValueError):
                        pass
                    # prop_type encodes the side: hits_under / ks_over.
                    _tail = _ptype.rsplit('_', 1)[-1].lower()
                    if _tail in ('over', 'under'):
                        _pside = _tail.upper()
            out.append({
                'sport': sp,
                'surface': 'sweat_card',
                'game_date': game_date,
                'source_id': _sid(market, anchor),
                'player_name': _player,
                'prop_type': _ptype,
                'pick_side': _side_for_receipt(it, market, _pside),
                'source_table': it.get('source_table') or 'jerry_cache.sweat_card',
                'game_id': it.get('game_id'),
                'matchup': it.get('game') or it.get('matchup'),
                'market': market,
                'pick_label': label,
                'pick_line': _pline,
                'pick_odds': it.get('odds'),
                'tier': (it.get('tier') or None),
                'conviction': it.get('conviction'),
                # top_8 carries its own graded result (826 of 854 items
                # across 141 days: 479 W / 331 L / 16 push). Dropping it
                # would leave the deepest surface we have showing zero
                # graded picks while the grade sat right there in the
                # payload. football_picks carries none — those grade via
                # the resolver like any other football pick.
                'result': norm_result(it.get('result')),
                'audit': {
                    'section': section,
                    'card_type': raw_type,
                    'rank': it.get('rank'),
                    'tier_source': it.get('tier_source'),
                    'side': it.get('side'),
                    'captured_by': 'generate_sweat_card',
                },
            })
    return out
