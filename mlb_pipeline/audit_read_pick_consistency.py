#!/usr/bin/env python3
"""Assert every Jerry game read AGREES with the pick it is attached to.

== 2026-10-29 · WHY THIS EXISTS ==
ANDY: "i need verification that each Jerry game read write up matches the
picks and makes sense on nfl and ncaaf, nba, and nhl."

Three different things can disagree on one card and nothing checks that they
agree:

    engine      <sport>_game_context.primary_play   (what the scorer decided)
    badge       jerry_reads.call_market/side/line   (what the card shows)
    write-up    jerry_reads.short_read/long_read    (what the reader reads)

They are written by different steps, so they drift independently. Measured
the day this was written, after a signal-weight refit rewrote primary_play:

    NCAAF   46 of 65 reads had a badge the engine no longer agreed with
    NBA      6 of 20
    NHL      0 of 44
    NFL      week-locked, unmeasurable without NFL_UNLOCK_WEEK

And separately, found by reading three NCAAF reads at random:

    badge 'Texas Tech ML'  ·  prose "This PASS on the moneyline hinges on
    pricing ... only 4.4 percentage points of edge — too thin to clear
    conviction thresholds"

A pick badge sitting on top of a write-up that declines the pick. Nothing
was red.

WHY ALIGNMENT ALONE IS NOT THE FIX. backfill_jerry_pick_alignment.py
rewrites the badge to match the engine, and
jerry_reads_dual_write.enforce_primary_play_alignment says in its own
docstring that "the LLM's prose (short_read, long_read) is preserved
as-is". So aligning a stale read moves the contradiction rather than
removing it: the badge becomes 'Washington +7.5' above prose still arguing
Nebraska -7.5. The prose has to be REGENERATED
(generate_<sport>_game_reads.py), which needs a live ANTHROPIC_API_KEY.
This script exists to prove that happened, and to fail loudly when it did
not.

PRECISION OVER RECALL. A checker that cries wolf gets muted
(feedback_a_detector_must_know_the_intentional_case), so every rule here
fires only on an unambiguous contradiction, and a read that merely looks
unusual is left alone. The deliberately-excluded cases are listed at each
rule.

USAGE
    python audit_read_pick_consistency.py                     # all sports
    python audit_read_pick_consistency.py --sport NCAAF
    python audit_read_pick_consistency.py --days 14 --verbose
    python audit_read_pick_consistency.py --warn-only         # never exit 2
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX_TABLE = {
    'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context', 'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context', 'NCAAB': 'ncaab_game_context',
}

#: A read whose prose declines the play must not carry an actionable badge.
#  Anchored to phrases the generators actually emit — verified against live
#  prose — not a generic "pass" substring, because "pass defense", "passing
#  game" and "pass rush" appear in almost every football read.
#
# == 2026-10-29 · TWO PATTERNS REMOVED AFTER THE FIRST CI RUN ==
# `not actionable` and `too thin to clear` were dropped because a read
# covers SEVERAL markets and those phrases usually decline a DIFFERENT one
# than the badge sells:
#   badge 'Portland Trail Blazers -8.5' — "projects 232.2, a negligible
#   1.3-point lean to the under, NOT ACTIONABLE. Portland -8.5 clears the
#   bar at ..."
# The read declines the TOTAL and backs the SPREAD. Correct writing, and the
# gate called it a contradiction. The Texas Tech case that motivated the
# rule is still caught, by `this pass` plus the market-qualified form below.
# `we pass` was removed for the same reason: my own regression case
# "...we pass on the total because the number is efficient..." tripped a
# SPREAD pick, because the generic pattern fired before the market check
# could rule it out. Every "pass on the <market>" form is handled by
# PASS_PROSE_MARKET below, which compares the named market to the badge's.
PASS_PROSE = (
    r'\bthis\s+pass\b',
    r'\bno\s+play\b', r'\bpassing\s+on\s+this\b',
    r'\bstaying\s+away\b', r'\bno\s+actionable\s+edge\b',
)
#: "pass on the moneyline" only contradicts an ML badge. Checked per market
#  so declining the total above a spread pick is not a finding.
PASS_PROSE_MARKET = r'\bpass(?:ing)?\s+on\s+the\s+(moneyline|spread|total|side|ml)\b'
_MKT_WORDS = {'ml': ('moneyline', 'ml'), 'rl': ('spread', 'side'),
              'spread': ('spread', 'side'), 'total': ('total',),
              'pl': ('spread', 'side')}

#: An engine-sub / bridge stub is NOT prose and must not be judged as prose.
#  The NFL 11-05 and 11-08 rows read, in full, like
#     "... back away RL · ⚠ LR shadow warns other way (p_home=0.64) — no play."
#  which is the scorer's own summary line, not a write-up arguing a side.
#  Those rows are already reported as PICK_WITH_NO_PROSE; counting them as
#  contradictions too would hold a pipeline red for games the generator has
#  not reached yet, which is the muted-step failure this file warns about.
STUB_MARKERS = ('⚠', 'Analysis pending', 'Model conviction on',
                'Supervised total model backs')
STUB_MAX_CHARS = 200
#: markets that represent a real wager (anything else is already a pass)
ACTIONABLE = ('ml', 'rl', 'spread', 'total', 'pl')


def _page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} -> {r.status_code}: {r.text[:200]}')
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _pp(row: dict):
    pp = row.get('primary_play')
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except ValueError:
            return None
    return pp if isinstance(pp, dict) else None


def _label(pp: dict) -> str:
    for k in ('pick_label', 'label', 'call_text', 'text'):
        v = pp.get(k)
        if v:
            return str(v).strip()
    return ''


def _norm(s) -> str:
    """Lowercase, strip punctuation — for tolerant name containment."""
    return re.sub(r'[^a-z0-9 ]+', ' ', str(s or '').lower()).strip()


def _teams(ctx: dict):
    return str(ctx.get('home_team') or ''), str(ctx.get('away_team') or '')


def _picked_team(call_text: str, home: str, away: str):
    """Which team the badge backs, or None for a total / unparseable badge.

    Matched by NAME CONTAINMENT against the two real teams rather than by
    vocabulary, because a label is 'Washington +7.5', 'BYU -16.5',
    'Georgia Southern ML' or 'Ottawa Senators PL' depending on sport and
    market (feedback_parse_a_pick_by_shape_not_vocabulary).
    """
    t = _norm(call_text)
    if not t:
        return None
    if re.search(r'\b(over|under)\b', t):
        return None
    hits = []
    for name in (home, away):
        n = _norm(name)
        if not n:
            continue
        # full name, or its last token (mascot), which is how short labels read
        if n in t or (n.split() and n.split()[-1] in t.split()):
            hits.append(name)
    # ambiguous when both matched (e.g. a label naming both sides)
    return hits[0] if len(hits) == 1 else None


def audit(sport: str, days: int, verbose: bool) -> dict:
    ctx_table = CTX_TABLE[sport]
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    reads = _page('jerry_reads', {
        'select': 'game_id,game_date,call_market,call_side,call_line,'
                  'call_text,short_read,long_read,conviction',
        'sport': f'eq.{sport}',
        'and': f'(game_date.gte.{today},game_date.lte.{end})'})
    ctx = _page(ctx_table, {
        'select': 'game_id,game_date,home_team,away_team,primary_play',
        'and': f'(game_date.gte.{today},game_date.lte.{end})'})
    cmap = {str(c['game_id']): c for c in ctx}

    findings = []
    checked = 0
    for r in reads:
        gid = str(r['game_id'])
        c = cmap.get(gid) or {}
        home, away = _teams(c)
        prose = f"{r.get('short_read') or ''}\n{r.get('long_read') or ''}"
        low = prose.lower()
        mkt = str(r.get('call_market') or '').strip().lower()
        label = str(r.get('call_text') or '').strip()
        checked += 1

        def add(code, detail):
            findings.append({'sport': sport, 'game_id': gid,
                             'game_date': r['game_date'], 'code': code,
                             'badge': label or '(none)', 'detail': detail})

        # 1 · the write-up declines a play the badge sells.
        #     Excluded on purpose: a badge that is already a pass/no-play,
        #     and conviction 0 rows, which ARE the pass.
        is_stub = (len(prose.strip()) < STUB_MAX_CHARS
                   or any(s in prose for s in STUB_MARKERS))
        if mkt in ACTIONABLE and label and not is_stub:
            hit = next((p for p in PASS_PROSE if re.search(p, low)), None)
            if not hit:
                # market-qualified form: only a match for THIS pick's market
                for m2 in re.finditer(PASS_PROSE_MARKET, low):
                    if m2.group(1) in _MKT_WORDS.get(mkt, ()):
                        hit = PASS_PROSE_MARKET
                        break
            if hit and (r.get('conviction') or 0) > 0:
                m = re.search(hit, low)
                frag = prose[max(0, m.start() - 60):m.start() + 90].replace('\n', ' ')
                add('PROSE_DECLINES_THE_PICK', f'...{frag.strip()}...')

        # 2 · badge the engine no longer agrees with.
        pp = _pp(c)
        if pp:
            eng = _label(pp)
            if eng and label and _norm(eng) != _norm(label):
                add('BADGE_VS_ENGINE', f'engine wants {eng!r}')

        # 3 · the backed team is never named in its own write-up.
        #     Only run when the team is unambiguous and prose is substantive,
        #     so a thin stub is not double-reported.
        #
        #     == 2026-10-29 · THIS RULE'S FIRST VERSION WAS THE BUG ==
        #     It accepted only the FULL name or the MASCOT (last token), and
        #     reported 16 reads — 13 of 44 NHL — as never naming their own
        #     pick. Every one was a false positive: the prose uses the CITY.
        #       badge 'Tampa Bay Lightning ML'    prose "Hildeby's confirmed
        #                                          in net for Tampa ..."
        #       badge 'Nashville Predators ML'    prose "The model has
        #                                          Nashville at roughly 50% ..."
        #     A checker that is wrong about the normal case is worse than no
        #     checker (feedback_a_detector_must_know_the_intentional_case),
        #     and it nearly went into a report as 16 defects.
        #
        #     Now ANY token of 4+ characters counts as naming the team, which
        #     is deliberately generous: a missed contradiction costs less than
        #     a fabricated one. Tokens under 4 chars are skipped so 'LA',
        #     'NY' and 'bay' cannot match half the alphabet.
        pt = _picked_team(label, home, away)
        if pt and len(prose.strip()) >= 200:
            np_, pr = _norm(pt), _norm(prose)
            toks = [t for t in np_.split() if len(t) >= 4]
            named = (np_ and np_ in pr) or any(t in pr for t in toks)
            if not named:
                add('PICKED_TEAM_ABSENT_FROM_PROSE',
                    f'{pt!r} never mentioned (tried {toks or [np_]})')

        # 3b · THE PROSE ARGUES THE OTHER TEAM.
        #
        #      == 2026-10-29 · THE ONE THAT ACTUALLY REACHED USERS ==
        #      Rules 2 and 3 both passed on these, which is why they were
        #      missed: the badge matched the engine, and the prose names
        #      BOTH teams, so "is the pick mentioned" is useless here.
        #
        #      A refit flipped 16 NCAAF badges, and the read generator that
        #      rewrites the prose then failed (rc=1), so the write-ups stayed
        #      from 01:45 and kept arguing the side we had just left:
        #        badge 'Washington +7.5'  prose "the model projects Nebraska
        #                                  28.2, Washington 22.7"
        #        badge 'FIU +13.5'        prose "a clear mismatch in
        #                                  Liberty's favor"
        #      Alignment moved the badge and preserved the prose, exactly as
        #      enforce_primary_play_alignment documents, and nothing compared
        #      the two.
        #
        #      Two high-precision shapes only, both anchored on the OPPONENT
        #      by name, so a read that merely discusses the other team is not
        #      flagged — only one that projects it to win or calls the edge
        #      its own.
        if pt and home and away:
            opp = away if pt == home else home
            on, pn = _norm(opp), _norm(pt)
            opp_toks = [t for t in on.split() if len(t) >= 4]
            pick_toks = [t for t in pn.split() if len(t) >= 4]
            # (a) "model projects <A> 28.2, <B> 22.7" — higher score wins
            for m in re.finditer(
                    r'project(?:s|ed)?\s+([A-Za-z][\w .&\'-]{2,28}?)\s+'
                    r'(\d{1,3}(?:\.\d)?)\s*(?:,|to|-|–|—|and)\s*'
                    r'([A-Za-z][\w .&\'-]{2,28}?)\s+(\d{1,3}(?:\.\d)?)', prose):
                a1, s1, a2, s2 = m.group(1), float(m.group(2)), m.group(3), float(m.group(4))
                hi = _norm(a1 if s1 > s2 else a2)
                if s1 == s2 or not hi:
                    continue
                hits_opp = any(t in hi for t in opp_toks)
                hits_pick = any(t in hi for t in pick_toks)
                if hits_opp and not hits_pick:
                    add('PROSE_PROJECTS_THE_OTHER_TEAM',
                        f'prose projects {a1.strip()} {s1:g}-{a2.strip()} {s2:g}, '
                        f'badge backs {pt}')
                    break
            # (b) A "who does the prose favour" rule was BUILT AND REMOVED
            #     here on 2026-10-29. Keeping the reasoning so it is not
            #     rebuilt by the next person who notices the gap.
            #
            #     Matching favour/edge vocabulary against each team produced
            #     false positives I could not defend, in both directions:
            #       'Duke -7.5'   — prose argues FOR Duke the whole way
            #                       ("Duke's offense running circles around
            #                       Boston College's defense") but never in
            #                       the words the rule knew, so the pick
            #                       side scored zero and the read was
            #                       flagged.
            #       'Colorado Avalanche ML' — "goaltending favors the
            #                       visitor by a full side", and Colorado IS
            #                       the visitor. Agreement read as conflict.
            #       'Notre Dame ML' — "anchors Notre Dame's case, THOUGH the
            #                       stat sheet splits 6-11 in Miami's
            #                       favor". A concessive clause is good
            #                       writing, not a contradiction.
            #     It reported 13 findings of which the ones I checked were
            #     all wrong. That is feedback_parse_a_pick_by_shape_not_
            #     vocabulary: a read can argue a side in unlimited phrasings
            #     and a keyword list will never cover them.
            #
            #     Judging "does this paragraph argue for this side" needs a
            #     model, not a regex. The right home for it is the read
            #     generator's own output check, where the prompt and the
            #     pick are both in hand. Until then (a) above is the honest
            #     subset: a PROJECTED SCORE is structured, so naming the
            #     opponent as the winner is unambiguous, and that is what
            #     caught the Washington/Nebraska read.

        # 4 · a total pick whose prose never takes that direction.
        if mkt == 'total':
            side = str(r.get('call_side') or '').strip().lower()
            if side in ('over', 'under') and not re.search(rf'\b{side}\b', low):
                add('TOTAL_DIRECTION_ABSENT', f'prose never says {side!r}')

        # 5 · an actionable badge with no write-up at all.
        if mkt in ACTIONABLE and label and len(prose.strip()) < 60:
            add('PICK_WITH_NO_PROSE', f'{len(prose.strip())} chars of prose')

    return {'sport': sport, 'reads': checked, 'ctx': len(ctx),
            'findings': findings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX_TABLE))
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--warn-only', action='store_true')
    a = ap.parse_args()
    sports = [a.sport] if a.sport else ['NFL', 'NCAAF', 'NBA', 'NHL']

    all_f = []
    print(f'=== audit_read_pick_consistency · {date.today()} · +{a.days}d ===')
    rows = []
    for sp in sports:
        try:
            res = audit(sp, a.days, a.verbose)
        except RuntimeError as e:
            print(f'  ! {sp}: {e}')
            continue
        all_f += res['findings']
        codes = {}
        for f in res['findings']:
            codes[f['code']] = codes.get(f['code'], 0) + 1
        rows.append((sp, res['reads'], res['ctx'], len(res['findings']), codes))

    print(f"\n{'sport':7} {'reads':>6} {'ctx':>5} {'findings':>9}  breakdown")
    for sp, n, c, f, codes in rows:
        print(f'{sp:7} {n:>6} {c:>5} {f:>9}  '
              + (', '.join(f'{k}={v}' for k, v in sorted(codes.items())) or 'clean'))

    by_code = {}
    for f in all_f:
        by_code.setdefault(f['code'], []).append(f)
    for code in sorted(by_code):
        fl = by_code[code]
        print(f'\n── {code} ({len(fl)}) ' + '─' * max(0, 50 - len(code)))
        for f in fl[: (None if a.verbose else 10)]:
            print(f"   {f['sport']:6} {f['game_date']} {f['badge'][:34]:36} {f['detail'][:110]}")
        if not a.verbose and len(fl) > 10:
            print(f'   … and {len(fl) - 10} more (--verbose for all)')

    # ══ WHAT BLOCKS A PIPELINE, AND WHAT ONLY WARNS ═════════════════════
    # Andy, 2026-10-29: "i want the write ups correct across all sports now
    # and going forward". Going forward means this runs after every read
    # generation and FAILS, so a contradiction cannot reach a card unseen.
    #
    # Only the two unambiguous, non-self-healing shapes block:
    #   PROSE_DECLINES_THE_PICK        the write-up says no play, the badge
    #                                  sells one. Never correct.
    #   PROSE_PROJECTS_THE_OTHER_TEAM  the prose's own projected score has
    #                                  the opponent winning. Structured, so
    #                                  not a parsing guess.
    # Both are fixed by regenerating that read, which is exactly what the
    # step before this one does.
    #
    # The rest WARN on purpose, because each resolves itself and a step that
    # is red for a reason nobody can action gets muted
    # (feedback_a_detector_must_know_the_intentional_case):
    #   BADGE_VS_ENGINE      transient while a refit lands - true for the
    #                        minutes between the context write and the read
    #                        rewrite.
    #   PICK_WITH_NO_PROSE   a stub for a game not yet inside the
    #                        generator's horizon; it fills as the week
    #                        advances (the NFL 11-08 rows).
    #   PICKED_TEAM_ABSENT / TOTAL_DIRECTION_ABSENT   weaker text rules,
    #                        kept visible but never blocking.
    BLOCKING = ('PROSE_DECLINES_THE_PICK', 'PROSE_PROJECTS_THE_OTHER_TEAM')
    hard = [f for f in all_f if f['code'] in BLOCKING]
    warn = [f for f in all_f if f['code'] not in BLOCKING]
    print(f'\n  total findings: {len(all_f)}   BLOCKING: {len(hard)}   '
          f'warnings: {len(warn)}')
    if hard:
        print('  >> a write-up contradicts the pick it is attached to. '
              'Regenerate that read.')
    if not all_f:
        print('  >> every read agrees with its pick.')
    if a.warn_only:
        return 0
    return 2 if hard else (1 if all_f else 0)


if __name__ == '__main__':
    raise SystemExit(main())
