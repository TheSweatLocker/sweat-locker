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
_INT_FIELDS = ('conviction', 'pick_odds')
_MARKET_MAX = 24


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
    for r in rows:
        if not r.get('sport') or not r.get('game_date') or not r.get('source_id'):
            out['skipped'] += 1
            continue
        r.setdefault('surface', surface)
        r.setdefault('published_at', now)
        # The affirmative claim. Nothing else in the codebase sets this.
        r['capture_mode'] = 'live'
        payload.append(sanitize(r))
    if out['skipped'] and not quiet:
        print(f'  ⚠ receipts ({surface}): {out["skipped"]} row(s) missing '
              f'sport/game_date/source_id — not captured')
    if not payload:
        return out
    if dry_run:
        out['written'] = len(payload)
        if not quiet:
            print(f'  [DRY] would capture {len(payload)} live receipt(s) → {surface}')
        return out
    try:
        resp = requests.post(
            f'{SB}/rest/v1/public_receipts?on_conflict={UNIQUE_KEY}',
            headers=H_WRITE, json=payload, timeout=60)
        if resp.status_code in (200, 201, 204):
            out['written'] = len(payload)
            if not quiet:
                print(f'  🧾 captured {len(payload)} LIVE receipt(s) → {surface}')
        else:
            out['failed'] = len(payload)
            print(f'  ⚠ receipts ({surface}) HTTP {resp.status_code}: '
                  f'{resp.text[:240]}')
    except Exception as e:                                  # noqa: BLE001
        out['failed'] = len(payload)
        print(f'  ⚠ receipts ({surface}) raised: {e}')
    return out


# ── surface adapters ────────────────────────────────────────────────

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
            'pick_odds': it.get('odds'),
            'tier': (it.get('tier') or None),
            'conviction': it.get('conviction'),
            'audit': {
                'units': it.get('units'),
                'reason': (it.get('reason') or '')[:600],
                'captured_by': 'generate_sharp_card',
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


def dawg_rows(records: list, game_date: str | None = None) -> list[dict]:
    """Dawg of the Day, from daily_dawg rows.

    Shape (verified, 123 rows back to 2026-04-22): game_date, team,
    matchup, game_id, tier, conviction, close_spread, spread_delta,
    result. One per day.
    """
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        gd = str(rec.get('game_date') or game_date or '')[:10]
        team = rec.get('team')
        if not gd or not team:
            continue
        out.append({
            'sport': str(rec.get('sport') or 'MLB').upper(),
            'surface': 'dawg',
            'game_date': gd,
            'source_id': _sid('dotd', rec.get('game_id') or gd),
            'source_table': 'daily_dawg',
            'game_id': rec.get('game_id'),
            'matchup': rec.get('matchup'),
            'market': 'dotd',
            'pick_label': team,
            'pick_line': rec.get('close_spread'),
            'tier': rec.get('tier'),
            'conviction': rec.get('conviction'),
            'result': norm_result(rec.get('result')),
            'audit': {
                'spread_delta': rec.get('spread_delta'),
                'captured_by': 'generate_dawg_of_day',
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
            out.append({
                'sport': sp,
                'surface': 'sweat_card',
                'game_date': game_date,
                'source_id': _sid(market, anchor),
                'source_table': it.get('source_table') or 'jerry_cache.sweat_card',
                'game_id': it.get('game_id'),
                'matchup': it.get('game') or it.get('matchup'),
                'market': market,
                'pick_label': label,
                'pick_line': it.get('line'),
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
