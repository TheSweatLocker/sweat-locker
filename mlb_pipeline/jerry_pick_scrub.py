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
like shit." Added template-prose overwrite.

2026-09-08 UPDATE — SCRUB PROSE DISABLED. The template overwrite
became the DEFAULT text 12 of 15 MLB games showed "Model recomputed
to X" as their primary read every day. That's worse than the
original cross-market problem — users NEVER saw real Jerry analysis.
Root cause: pipeline flips primary_play frequently late in the day
(LR override, refit, etc), each flip triggers a scrub, scrub keeps
stomping fresh prose with the template.

New behavior: scrub ONLY updates call_market/call_side/call_line/
call_text. Prose is LEFT UNTOUCHED. If prose is stale relative to
new call, that's a data-freshness issue to fix by re-running
generate_jerry_synthesis (which writes real prose for the current
pick). Template writes always lose against real prose in the "worse
than nothing" comparison.

The original cross-market prose leak is addressed by:
  - jerry_pre_publish_audit H_scenario_matrix path (writes real
    skip narratives via user_short/user_long)
  - generate_jerry_synthesis --force re-run after major recomputes
  - eventually: a real Claude-regenerate path in this scrub

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

def _team_aliases(name: str, sport: str) -> set:
    """Every string the prose might use for this team, lowercased.

    NFL game_context stores abbreviations ('CHI'); the LLM writes city
    names ('Chicago') and nicknames ('Bears'). Matching the raw ctx value
    against prose therefore never hits, which is why the ml/rl flip check
    below was dead for NFL. Expand through the ESPN abbreviation map and
    keep every word of the full name that is distinctive enough to stand
    alone.
    """
    raw = (name or '').strip()
    if not raw:
        return set()
    out = {raw.lower()}
    full = raw
    if sport == 'NFL':
        try:
            from resolve_nfl_props_espn import _ESPN_TEAM_ABBR
            full = _ESPN_TEAM_ABBR.get(raw.upper(), raw)
        except Exception:
            full = raw
    out.add(full.lower())
    toks = [t for t in full.split() if len(t) > 3]
    # 'New York Jets' -> keep 'jets'; drop 'new'/'york' which collide with
    # the other New York team. Last token is the nickname in every NFL and
    # most NCAAF names.
    if toks:
        out.add(toks[-1].lower())
    if len(toks) >= 2:
        out.add(' '.join(toks[:-1]).lower())   # city portion, e.g. 'green bay'
    return {a for a in out if len(a) >= 3}


def _prose_recommends(prose: str, team: str, sport: str) -> bool:
    """True if `prose` appears to RECOMMEND `team`, not merely mention it.

    Looks for a team alias sitting next to a betting construction:
    a signed number ('Chicago -4.5'), a moneyline call ('Cincinnati ML'),
    or an endorsement verb ('holds value', 'covers', 'take').

    Deliberately narrow on the verb list: 'Chicago is favored' is a fact
    about the market, not a recommendation, and flagging it would fire on
    almost every write-up.
    """
    import re
    for alias in _team_aliases(team, sport):
        a = re.escape(alias)
        pats = [
            rf'{a}\s*[+-]\s*\d',             # Chicago -4.5 / Cincinnati +2.5
            rf'{a}\s+ml\b',                   # Houston ML
            rf'take\s+(the\s+)?{a}\b',        # take Chicago
            rf'\bbacks?\s+(the\s+)?{a}\b',    # back / BACKS Cleveland
            rf'{a}\b[^.]{{0,40}}\bholds value\b',
            rf'{a}\b[^.]{{0,40}}\bcovers\b',
            # 2026-09-20: three more shapes, all found live on the 09-20
            # slate after the first version still missed two games.
            #   "backs Cleveland plus the points"   (no signed number)
            #   "the model has Jacksonville ahead by 2.1 points"
            #   "a 4.6-point gap favoring the road team"
            # The common thread is that real prose states a PREFERENCE
            # without ever writing the line next to the team name, which
            # is what the number-adjacent patterns above assume.
            rf'{a}\b[^.]{{0,30}}\bplus the points\b',
            rf'{a}\b[^.]{{0,30}}\band the points\b',
            rf'\bhas\s+{a}\s+ahead\b',
            rf'{a}\b[^.]{{0,30}}\bahead by\b',
            rf'\bfavor(s|ing)\s+(the\s+)?{a}\b',
            rf'\blikes?\s+(the\s+)?{a}\b',
        ]
        for p in pats:
            if re.search(p, prose):
                return True
    return False


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
                dry_run: bool = False) -> tuple[int, int, list]:
    """Return (checked, fixed, nulled_game_ids).

    2026-09-20: the early-exit paths returned a 2-TUPLE while the normal
    path returns 3, so main() raised
    "ValueError: not enough values to unpack (expected 3, got 2)" on ANY
    day a sport had no games with a primary_play. For NCAAF that is every
    Sunday — and NCAAF was wired into its workflow on 09-19, so it would
    have crashed on its very first scheduled run.

    Both this and the call_text crash below are invisible in production
    because every invocation is wrapped in `|| echo "... non-fatal"`.
    """
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        return (0, 0, [])
    # Fetch ctx rows for today (or specified games)
    # 2026-09-13: params dict per URL-encoding fix above.
    _params = {'select': 'game_id,home_team,away_team,primary_play',
               'primary_play': 'not.is.null'}
    if game_ids:
        ids = ','.join(f'"{g}"' for g in game_ids)
        _params['game_id'] = f'in.({ids})'
    else:
        _params['game_date'] = f'eq.{gd}'
    r = requests.get(f'{SB}/rest/v1/{cfg["ctx"]}',
                     params=_params, headers=H_READ, timeout=30)
    if r.status_code != 200:
        print(f'  {sport}: ctx fetch failed {r.status_code}')
        return (0, 0, [])
    ctx_rows = r.json() or []
    if not ctx_rows:
        print(f'  {sport} {gd}: no ctx rows with primary_play')
        return (0, 0, [])
    ctx_by_gid = {c['game_id']: c for c in ctx_rows}

    # Fetch jerry_reads for those games
    # 2026-09-13: use requests.params for proper URL-encoding. Prior raw
    # string interpolation broke on NCAAF game_ids containing spaces
    # ("ncaaf_20260912_Tennessee_Georgia Tech") — PostgREST 400'd. MLB
    # hex-hash game_ids never hit this. requests handles encoding
    # correctly when the filter is passed as a params dict.
    ids = ','.join(f'"{g}"' for g in ctx_by_gid.keys())
    r = requests.get(
        f'{SB}/rest/v1/jerry_reads',
        params={
            'sport': f'eq.{sport}',
            'game_id': f'in.({ids})',
            'select': 'id,game_id,call_market,call_side,call_line,call_text,'
                      'short_read,long_read,audit_notes,conviction',
        },
        headers=H_READ, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: jerry_reads fetch failed {r.status_code}')
        return (0, 0, [])
    jerry_rows = r.json() or []

    fixed = 0
    nulled_gids: list[str] = []  # 2026-09-16 track rows we null-out for chain-regen
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
        # 2026-09-12 FIX: previously only scanned long_read but prior scrub
        # runs null long_read (leaving short_read stale). Andy audit
        # confirmed 4 of 5 today's mismatches had contradiction in
        # short_read only (long_read empty). Scan BOTH fields now.
        _prose_all = (_lower_short + ' ' + _lower_long)
        _has_ml_take    = (' ml' in _prose_all or 'moneyline' in _prose_all)
        _has_total_take = ('take under' in _prose_all or 'take over' in _prose_all
                           or 'take the under' in _prose_all or 'take the over' in _prose_all)
        _prose_cross_market = (
            (pp_type == 'total' and _has_ml_take and not _has_total_take) or
            (pp_type == 'ml'    and _has_total_take)
        )
        # 2026-09-12 SAME-MARKET SIDE FLIP detection. Andy audit surfaced 5 of
        # 15 MLB writeups where prose recommends opposite side of same market:
        #   card OVER 9.0 vs prose "Take UNDER 9.0"
        #   card Dodgers ML vs prose "Take Marlins ML" (opposite team)
        # Previous code only caught cross-market swaps (ml vs total). Same-
        # market flip is just as broken from user POV. Detects by parsing
        # short_read + long_read for direction words and comparing to
        # pp_side. When flipped, treated identically to cross-market:
        # null both prose fields so app shows "analysis pending" instead
        # of prose contradicting the card.
        _same_market_side_flip = False
        if pp_type == 'total':
            _prose_says_over  = ('take over'  in _lower_short or 'take over'  in _lower_long
                                 or 'take the over'  in _lower_short or 'take the over'  in _lower_long)
            _prose_says_under = ('take under' in _lower_short or 'take under' in _lower_long
                                 or 'take the under' in _lower_short or 'take the under' in _lower_long)
            if pp_side == 'OVER'  and _prose_says_under and not _prose_says_over:
                _same_market_side_flip = True
            if pp_side == 'UNDER' and _prose_says_over  and not _prose_says_under:
                _same_market_side_flip = True
        elif pp_type in ('ml', 'rl'):
            # Extract team names — for ml/rl side flip, prose recommends the
            # OTHER team by name. Use home/away team from ctx row.
            _home = (c.get('home_team') or '').lower().strip()
            _away = (c.get('away_team') or '').lower().strip()
            if _home and _away and _home != _away:
                _picked_team = _home if pp_side == 'HOME' else _away
                _other_team  = _away if pp_side == 'HOME' else _home
                # 2026-09-19: REWRITTEN. The old check looked for the
                # literal phrase "take <team>" against the raw ctx team
                # string, and failed on NFL twice over:
                #
                #  1. NFL ctx stores ABBREVIATIONS ('CHI', 'CIN') while
                #     the prose writes city names ('Chicago',
                #     'Cincinnati'), so it compared "take chi" against
                #     text that never contains it. The detector could
                #     not fire for NFL at all, wired in or not.
                #  2. Real prose rarely says "take X". Andy caught two
                #     2026-09-20 games where the card said MIN +4.5 and
                #     HOU ML while the write-ups argued "Chicago -4.5
                #     holds value" and "Cincinnati +2.5" — opposite
                #     sides, no "take" anywhere.
                #
                # Now: expand each side to aliases, then look for any
                # RECOMMENDATION pattern attached to one of them.
                _prose_all = (_lower_short + ' ' + _lower_long)
                _other_rec  = _prose_recommends(_prose_all, _other_team, sport)
                _picked_rec = _prose_recommends(_prose_all, _picked_team, sport)
                # Still require the picked side to be ABSENT, so prose
                # that weighs both teams before landing on ours is not
                # flagged.
                if _other_rec and not _picked_rec:
                    _same_market_side_flip = True
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

        # 2026-09-13 OC-DISSENT FLIP detection. Andy audit tonight caught
        # 2 NCAAF games (Tennessee@GT, SDSU@UCLA) where the primary_play
        # flipped from ensemble's original pick to the opposite side
        # because OC (external consensus) had 64-70% money on the other
        # side. The pick label shows the FLIPPED side but the LLM prose
        # was written arguing for the PRE-FLIP side — reads as internal
        # contradiction to users.
        #
        # Detection signature: pp.sub starts with "OC-dissent flip".
        # Format from ensemble scorer: "OC-dissent flip. Ensemble had X;
        # OC has Y% money on the other side." When present, prose is
        # ALWAYS stale relative to the current call, regardless of what
        # words the LLM used.
        _oc_dissent_flip = 'oc-dissent flip' in str(pp.get('sub', '') or '').lower()
        stale_prose = (_tmpl_fallback or _prose_cross_market or
                       _stale_recompute_template or _same_market_side_flip
                       or _oc_dissent_flip)

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

        # 2026-09-08 KILL SWITCH: prose scrub disabled. Was overwriting
        # real Jerry synthesis prose with the "Model recomputed to X"
        # template every time pipeline picks flipped, and pipeline
        # flips several times per day per game. Users saw the template
        # as their PRIMARY read (12/15 MLB games today). Terrible UX.
        #
        # New behavior: patch call fields ONLY. Prose stays whatever
        # generate_jerry_synthesis last wrote — if that's stale relative
        # to the new pick, next generate_jerry_synthesis run heals it.
        # In the meantime users see REAL Jerry analysis (possibly
        # arguing a slightly-different market variant), not a template.
        #
        # Cross-market cleanup (badge=total but prose=Take X ML) still
        # handled by jerry_pre_publish_audit's H_scenario_matrix path
        # which writes coherent skip narratives via user_short/user_long,
        # not this scrub. Belt-and-suspenders long-read cleanup only
        # fires now when prose is DEMONSTRABLY cross-market (both a
        # "take X ml" cue AND an ml-picked game, or the reverse) — no
        # template writes, just a null-out that the render treats as
        # "analysis pending".
        # 2026-09-12: expanded from _prose_cross_market only to also catch
        # _same_market_side_flip. Andy audit found 5 of 15 MLB writeups with
        # prose contradicting the card — 4 cross-market, 1 same-market
        # (Cubs card Over 9.0 vs prose "Take UNDER 9.0"). Same-market
        # flip is same UX bug from user POV, deserves same treatment.
        # Also now nulls BOTH short_read AND long_read (was long_read only)
        # since users read the short_read as the primary prose on the
        # card — leaving stale short_read defeats the null-long_read fix.
        # 2026-09-13: OC-dissent flip added to hard-null trigger. Andy caught
        # 2 NCAAF cases tonight where prose argued for pre-flip side while
        # pick label showed post-flip. OC-dissent DOESN'T need `drift`
        # since pp.type/side may already have been rewritten during
        # ensemble scoring — the signal is the "OC-dissent flip" phrase
        # itself, which guarantees prose staleness regardless.
        _hard_bad_prose = (
            (_prose_cross_market or _same_market_side_flip) and (drift or stale_prose)
        ) or _oc_dissent_flip
        if _hard_bad_prose:
            # 2026-09-16 STOP-NULLING: previously wrote short_read = None
            # and let the client render "analysis pending". That path relied
            # on a client-side fallback added 9/15 which is NOT in the v1.0
            # TestFlight build users are actually on — so users saw the
            # Jerry card VANISH on cross-market flips. Two consecutive
            # mornings shipped 3-9 blank cards to production.
            #
            # New behavior: write a canonical template that names the new
            # call so the card ALWAYS renders. chain-regen (main()) then
            # overwrites with real LLM prose in the same pipeline pass;
            # if that fails the template stays as the graceful floor.
            # 2026-09-19 STOP SHIPPING INTERNAL STATUS TEXT.
            # The template above read "Model recomputed to X. Fresh read
            # regenerating." and went straight to users. On 09-19 four
            # MLB reads sat on it for ~9 hours because chain-regen never
            # replaced them — including KC @ PIT, which displayed
            # "Pass · 55% confidence" while the model held Under 8.0 at
            # conviction 83. "Regenerating" is a promise to the user that
            # we have no way to keep if the regen fails.
            #
            # The engine already writes a publishable one-liner for every
            # pick: primary_play['sub'] (e.g. "Supervised total model
            # backs Under · 80% confidence"). That is a real reason, it
            # is true at the moment of writing, and it needs no LLM call.
            # Use it as the floor. chain-regen still upgrades it to full
            # prose; if that fails, the user sees a genuine short read
            # rather than a status message.
            _new_text = _side_readable or (pp.get('label') or '').strip()
            if not _new_text:
                _new_text = f"{pp_type.upper()} {pp_side}".strip()
            _sub = (pp.get('sub') or '').strip()
            # 2026-09-19: the engine's `sub` can itself be stale. On
            # PHI @ TEN the pick had drifted from TEN ML to PHI -7 while
            # sub still read "TEN ML: DIM is on this side…", so pasting
            # the new label in front produced "PHI -7 — TEN ML: …" —
            # a contradiction rebuilt out of the fix for contradictions.
            # Reuse the flip detector: if the rationale argues the team we
            # did NOT pick, discard it rather than dress it up.
            if _sub:
                _other = ((c.get('away_team') or '') if pp_side == 'HOME'
                          else (c.get('home_team') or ''))
                _picked = ((c.get('home_team') or '') if pp_side == 'HOME'
                           else (c.get('away_team') or ''))
                _sub_l = _sub.lower()
                if _other and _prose_recommends(_sub_l, _other, sport) \
                        and not _prose_recommends(_sub_l, _picked, sport):
                    _sub = ''
            if _sub:
                _tmpl = f"{_new_text} — {_sub}" if _new_text.lower() not in _sub.lower() else _sub
            else:
                # No engine rationale available. Still avoid promising a
                # regeneration we may not deliver — state the call plainly.
                _tmpl = f"{_new_text}. Full write-up updates with the next model run."
            payload['short_read'] = _tmpl
            payload['long_read']  = _tmpl
            nulled_gids.append(j['game_id'])
            _flip_kind = ('cross-market' if _prose_cross_market
                          else 'same-market-side-flip')
            _orig_note = (
                f'[jerry_pick_scrub 2026-09-12 null-out {_flip_kind}: '
                f'call is now {pp_type.upper()}/{pp_side} {_side_readable[:40]}. '
                f'Original short: {orig_short[:300]} · Original long start: {orig_long_pre[:200]}]'
            )
            payload['audit_notes'] = _orig_note[:1500]
        # Note: `should_rewrite_prose` no longer triggers any prose write.
        # Kept in code above for future reference / potential Claude-regen path.

        if not payload:
            continue  # nothing to change

        matchup = f'{c["away_team"][:14]:14s} @ {c["home_team"][:14]:14s}'
        tag = 'DRIFT' if drift else 'STALE'
        prose_tag = '  [prose scrubbed]' if 'long_read' in payload else ''
        # 2026-09-20: `or "?"` not `.get(..., "?")`. call_text is present
        # but NULL on every `pass` read, so the default never applied and
        # this line raised TypeError: 'NoneType' is not subscriptable —
        # killing the whole scrub the moment it reached a pass game.
        #
        # The step is wrapped in `|| echo "jerry scrub non-fatal"`, so the
        # pipeline carried on and nothing reported it. Every game AFTER
        # the first pass went unchecked, every run. That is why the same
        # read problems kept reappearing daily: the thing that fixes them
        # was dying before it got to most of them.
        _ct = (j.get('call_text') or '?')
        print(f'  {tag} {matchup}  '
              f'{j_market}/{j_side} {_ct[:20]} -> '
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
    return (len(jerry_rows), fixed, nulled_gids)


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
    # 2026-09-16: track nulled game_ids per sport so we can chain-invoke
    # generate_jerry_synthesis to regen matching prose in the same
    # pipeline pass. Prior behavior left "analysis pending" until the
    # next scheduled synth — the reason 9 MLB reads were blank in
    # production on both 9/15 and 9/16 mornings.
    nulled_by_sport: dict[str, list[str]] = {}
    for sp in sports:
        c, f, nulled = scrub_sport(sp, gd, game_ids, dry_run=args.dry_run)
        if c or f:
            print(f'  {sp}: checked {c}, fixed {f}, nulled {len(nulled)}')
        total_c += c; total_f += f
        if nulled:
            nulled_by_sport[sp] = nulled
    print(f'DONE - checked {total_c}, fixed {total_f}')

    # Chain-regen for any nulled rows so users never see "analysis
    # pending" cards in production. Skips on --dry-run.
    if not args.dry_run and nulled_by_sport:
        import subprocess, sys
        for sp, gids in nulled_by_sport.items():
            print(f'  chain-regen {sp}: {len(gids)} game(s) nulled — invoking '
                  f'generate_jerry_synthesis --game-id')
            cmd = [sys.executable, 'generate_jerry_synthesis.py',
                   '--sport', sp, '--date', gd,
                   '--game-id', ','.join(gids), '--force']
            try:
                rc = subprocess.call(cmd, timeout=900)
                print(f'    chain-regen {sp} exit={rc}')
            except Exception as e:
                print(f'    chain-regen {sp} FAILED: {e}')


if __name__ == '__main__':
    main()
