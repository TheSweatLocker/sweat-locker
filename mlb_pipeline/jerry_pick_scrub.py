#!/usr/bin/env python3
"""jerry_pick_scrub — force jerry_reads.call_* to match ctx.primary_play.

The Jerry LLM writes prose + emits its own call_side / call_text at
Jerry-generation time. If ctx.primary_play is later recomputed (MC
refresh, close-line update, defensive gate flip), Jerry's stored
call_side can diverge from the mechanical ensemble pick. Result: badge
shows one team, analysis shows another.

This scrub sweeps today's jerry_reads for any game whose primary_play
disagrees with the stored jerry_reads.call_side/market/line and
overwrites the CALL fields deterministically.

2026-09-07 UPDATE — prose scrub on drift. Previously the LLM prose
(short_read / long_read) was left as-is, on the theory that the badge
+ grading being consistent was enough. In practice users READ the
prose and were seeing cards where the badge said one market and the
prose argued for a totally different one (e.g., badge "Over 8.5" but
long_read "Take the Mets ML"). User feedback: "MLB Jerry reads look
like shit." Now when we detect drift, we also overwrite short_read
and long_read with a coherent recompute-notice narrative that
matches the new call, so the whole card reads consistently. The
original take is preserved in audit_notes for internal auditability.

Sport-universal via SPORT_CONFIG.

CLI:
    python jerry_pick_scrub.py                    # today, all sports
    python jerry_pick_scrub.py --sport MLB
    python jerry_pick_scrub.py --date 2026-08-27
    python jerry_pick_scrub.py --games GID1,GID2  # regen these specifically
    python jerry_pick_scrub.py --dry-run

Runs in cron AFTER recompute_primary_play so any pick flip cascades to
Jerry's display fields on the same run. Also runs standalone in the
rescue block as a belt-and-suspenders check.
"""

from __future__ import annotations
import argparse, os, sys, datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

SPORT_CONFIG = {
    'MLB':   {'ctx': 'mlb_game_context'},
    'NFL':   {'ctx': 'nfl_game_context'},
    'NCAAF': {'ctx': 'ncaaf_game_context'},
    'NBA':   {'ctx': 'nba_game_context'},
    'NHL':   {'ctx': 'nhl_game_context'},
    'NCAAB': {'ctx': 'ncaab_game_context'},
}


def _derive_call_text(pp: dict, home_team: str, away_team: str) -> str | None:
    """Deterministic call_text from primary_play + team names."""
    ptype = (pp.get('type') or '').lower()
    side  = (pp.get('side') or '').upper()
    line  = pp.get('line')
    label = pp.get('label')
    if label and label.strip():
        return label.strip()
    if ptype == 'ml':
        if side == 'HOME': return f'{home_team} ML'
        if side == 'AWAY': return f'{away_team} ML'
    if ptype == 'total' and side in ('OVER', 'UNDER'):
        return f'{side.capitalize()}' + (f' {line}' if line is not None else '')
    if ptype == 'rl' and side in ('HOME', 'AWAY'):
        team = home_team if side == 'HOME' else away_team
        return f'{team} RL' + (f' {line}' if line is not None else '')
    return None


def scrub_sport(sport: str, gd: str, game_ids: list[str] | None = None,
                dry_run: bool = False) -> tuple[int, int]:
    """Return (checked, fixed)."""
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        return (0, 0)
    # Fetch ctx rows for today (or specified games)
    ctx_filter = f'game_date=eq.{gd}&primary_play=not.is.null'
    if game_ids:
        ids = ','.join(f'"{g}"' for g in game_ids)
        ctx_filter = f'game_id=in.({ids})'
    r = requests.get(
        f'{SB}/rest/v1/{cfg["ctx"]}?{ctx_filter}'
        '&select=game_id,home_team,away_team,primary_play',
        headers=H_READ, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: ctx fetch failed {r.status_code}')
        return (0, 0)
    ctx_rows = r.json() or []
    if not ctx_rows:
        print(f'  {sport} {gd}: no ctx rows with primary_play')
        return (0, 0)
    ctx_by_gid = {c['game_id']: c for c in ctx_rows}

    # Fetch jerry_reads for those games
    ids = ','.join(f'"{g}"' for g in ctx_by_gid.keys())
    r = requests.get(
        f'{SB}/rest/v1/jerry_reads?sport=eq.{sport}&game_id=in.({ids})'
        '&select=id,game_id,call_market,call_side,call_line,call_text,'
        'short_read,long_read,audit_notes,conviction',
        headers=H_READ, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: jerry_reads fetch failed {r.status_code}')
        return (0, 0)
    jerry_rows = r.json() or []

    fixed = 0
    for j in jerry_rows:
        c = ctx_by_gid.get(j['game_id'])
        if not c: continue
        pp = c.get('primary_play') or {}
        pp_type = (pp.get('type') or '').lower()
        pp_side = (pp.get('side') or '').upper()
        pp_line = pp.get('line')
        j_market = (j.get('call_market') or '').lower()
        j_side   = (j.get('call_side') or '').upper()
        j_line   = j.get('call_line')

        # Skip when primary_play is a non-standard market (nrfi/yrfi/fight)
        # that jerry_reads' schema also accepts — those tables agree already.
        if pp_type not in ('ml', 'rl', 'total'): continue

        # Detect drift
        drift = (j_market != pp_type or j_side != pp_side or
                 (pp_line is not None and j_line != pp_line))

        # 2026-09-07: also detect STALE PROSE independently of call-field
        # drift. When an earlier scrub run patched call fields but left
        # prose alone, the current call may already match primary_play
        # while long_read still argues for the pre-flip market. Detects
        # by looking for other-market cue phrases in long_read.
        orig_short_pre = (j.get('short_read') or '')
        orig_long_pre  = (j.get('long_read') or '')
        _lower_long    = orig_long_pre.lower()
        _lower_short   = orig_short_pre.lower()
        # Templated fallback signature: "The published call is X — Y tier"
        _tmpl_fallback = 'published call is' in _lower_long
        # Cross-market cue: ml call but prose says "Take X ML" / "Under X"
        _has_ml_take    = ' ml' in _lower_long or 'moneyline' in _lower_long
        _has_total_take = 'take under' in _lower_long or 'take over' in _lower_long \
                          or 'take the under' in _lower_long or 'take the over' in _lower_long
        _prose_cross_market = (
            (pp_type == 'total' and _has_ml_take and not _has_total_take) or
            (pp_type == 'ml'    and _has_total_take)
        )
        # 2026-09-08 STALE-SCRUB-TEMPLATE detection. When a prior scrub
        # left "Model recomputed to X" but the current call has since
        # flipped to Y, that short_read is stale (points at old pick).
        # Only detectable by parsing the template and comparing to the
        # current derived call text. Root cause of 9/8 POTD bug where
        # narrative said "Houston +1.5" but pick was actually Phillies ML.
        _stale_recompute_template = False
        if 'model recomputed to' in _lower_short:
            _new_text_for_check = _derive_call_text(pp, c['home_team'], c['away_team']) or ''
            _claimed = _lower_short.split('model recomputed to', 1)[-1].split('.', 1)[0].strip()
            _current = _new_text_for_check.lower()
            if _claimed and _current and _claimed not in _current and _current not in _claimed:
                _stale_recompute_template = True

        stale_prose = _tmpl_fallback or _prose_cross_market or _stale_recompute_template

        if not drift and not stale_prose: continue

        new_text = _derive_call_text(pp, c['home_team'], c['away_team'])
        payload = {}
        # Only touch call fields when they actually drifted
        if drift:
            payload['call_market'] = pp_type
            payload['call_side']   = pp_side
            payload['call_line']   = pp_line
            if new_text:
                payload['call_text'] = new_text

        # 2026-09-07: also scrub stale LLM prose. If the pick flipped
        # (drift True), any prior short/long the LLM wrote was arguing
        # for the OLD pick — leaving it in place produced cards where
        # the badge said one market and the prose argued for a
        # different one. Replace with a clean recompute narrative so
        # the whole card is internally consistent. Original take is
        # preserved in audit_notes for auditability.
        orig_short = (j.get('short_read') or '').strip()
        orig_long  = (j.get('long_read') or '').strip()
        _market_readable = {'ml': 'moneyline', 'rl': 'run line',
                            'total': 'total'}.get(pp_type, pp_type)
        _side_readable   = new_text or f'{pp_type.upper()} {pp_side}'
        new_short = (
            f'Model recomputed to {_side_readable}. Earlier read was '
            f'written before the pick refreshed — going with the '
            f'current {_market_readable} call.'
        )
        new_long  = (
            f'The current call is {_side_readable}. Jerry\'s original '
            f'read on this game was written before the model '
            f'recomputed, so any prior take may argue for a different '
            f'market — refer to the model signals + situational card '
            f'below for the current pick rationale. If this game earns '
            f'a curated spot, it will appear on the Sweat Card with '
            f'the current call.'
        )
        # Only rewrite prose if it needs it. H_scenario_matrix outputs
        # ("historical scenario matches", "no edge is defensible", etc.)
        # NEVER get rewritten — those are legitimate skip narratives from
        # a different upstream, we don't own their content.
        # 2026-09-08 BUG FIX: prior version also treated 'Model recomputed'
        # as an already-scrubbed marker → when the pipeline flipped picks
        # AGAIN after a scrub, the second scrub skipped and left the
        # stale "Model recomputed to X" text pointing to the OLD pick.
        # Root cause of a 9/8 POTD bug: card said Phillies ML but
        # narrative said "recomputed to Houston +1.5" (previous pick).
        # 'Model recomputed' now CHECKS whether the referenced pick still
        # matches the current call — if not, treat as stale and re-scrub.
        _H_SKIP_MARKERS = ('historical scenario matches',
                           'no edge is defensible',
                           'sitting this one out',
                           'take the discipline hit')
        h_skip_narrative = any(m.lower() in orig_short.lower() for m in _H_SKIP_MARKERS)
        # For 'Model recomputed' — only treat as already-scrubbed if the
        # referenced side matches the current call. If not, it's stale.
        is_model_recomputed = 'model recomputed' in orig_short.lower()
        model_recomputed_stale = False
        if is_model_recomputed and _side_readable:
            # Extract what pick the template claims
            # Format: "Model recomputed to {side_readable}. ..."
            claimed = orig_short.lower().split('model recomputed to', 1)[-1]
            claimed = claimed.split('.', 1)[0].strip()
            current = _side_readable.lower()
            # If the claimed pick doesn't overlap with current pick text,
            # the scrub is stale — need to re-scrub with fresh call.
            if claimed and current and claimed not in current and current not in claimed:
                model_recomputed_stale = True
        already_scrubbed = h_skip_narrative or (is_model_recomputed and not model_recomputed_stale)
        should_rewrite_prose = (drift or stale_prose or model_recomputed_stale) and not already_scrubbed

        # Even when already_scrubbed=True at short_read level, long_read
        # may still be a pre-audit narrative (H scenario matrix wrote
        # skip short but stale narrative survived on long). Detect
        # cross-market prose on long_read specifically.
        if already_scrubbed and _prose_cross_market:
            # Overwrite ONLY long_read to align with short's skip framing
            payload['long_read'] = (
                f'The pick was recomputed after this read was written. '
                f'Current call: {_side_readable}. The narrative above '
                f'may reference a different market — trust the current '
                f'call + short read for the actual pick rationale.'
            )[:2000]
        elif should_rewrite_prose:
            payload['short_read'] = new_short[:2000]
            payload['long_read']  = new_long[:2000]
            # Preserve original take in audit_notes for future reference
            _orig_note = (
                f'[jerry_pick_scrub 2026-09-07 prose-flip: '
                f'call is now {pp_type.upper()}/{pp_side} {_side_readable[:40]}. '
                f'Original short_read: {orig_short[:500]}]'
            )
            payload['audit_notes'] = _orig_note[:1500]

        if not payload:
            continue  # nothing to change

        matchup = f'{c["away_team"][:14]:14s} @ {c["home_team"][:14]:14s}'
        tag = 'DRIFT' if drift else 'STALE'
        prose_tag = '  [prose scrubbed]' if 'long_read' in payload else ''
        print(f'  {tag} {matchup}  '
              f'{j_market}/{j_side} {j.get("call_text","?")[:20]} -> '
              f'{pp_type}/{pp_side} {new_text}{prose_tag}')

        if dry_run:
            fixed += 1
            continue

        pr = requests.patch(
            f'{SB}/rest/v1/jerry_reads?id=eq.{j["id"]}',
            headers=H_WRITE, json=payload, timeout=15,
        )
        if pr.status_code in (200, 204):
            fixed += 1
        else:
            print(f'    patch failed {pr.status_code}: {pr.text[:150]}')
    return (len(jerry_rows), fixed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['ALL'] + list(SPORT_CONFIG.keys()),
                    default='ALL')
    ap.add_argument('--date', help='YYYY-MM-DD (default today ET)')
    ap.add_argument('--games', help='Comma-separated game_ids to scrub (overrides --date)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    gd = args.date or dt.date.today().isoformat()
    game_ids = args.games.split(',') if args.games else None
    sports = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]

    print(f'=== jerry_pick_scrub · {gd} · {"/".join(sports)}{" [DRY]" if args.dry_run else ""} ===')
    total_c = total_f = 0
    for sp in sports:
        c, f = scrub_sport(sp, gd, game_ids, dry_run=args.dry_run)
        if c or f:
            print(f'  {sp}: checked {c}, fixed {f}')
        total_c += c; total_f += f
    print(f'DONE - checked {total_c}, fixed {total_f}')


if __name__ == '__main__':
    main()
