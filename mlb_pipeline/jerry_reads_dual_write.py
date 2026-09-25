"""Universal parser + jerry_reads writer for sport game reads (2026-08-25).

Ports the parse_nfl_synthesis + upsert_jerry_read_nfl pair out of
generate_nfl_game_reads.py so NCAAF, NCAAB, and any future sport can
dual-write to the Phase 2 jerry_reads table with one import.

Contract with the sport's generate_*_game_reads.py:

    from jerry_reads_dual_write import parse_synthesis, upsert_jerry_read

    parsed = parse_synthesis(narrative)          # dict, may be all-None
    if parsed.get('short_read'):
        upsert_jerry_read(
            sport='NCAAF', game_id=gid, game_date=today,
            struct=struct, parsed=parsed, narrative=narrative,
            prompt_version='ncaaf_game_read_v2_2026-08-25',
        )

Both callers stay non-fatal — upsert failure prints a warning but does
not raise. Prose still lives in jerry_cache (legacy) via each caller's
existing write_cache; this table adds structured pick data on top.
"""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone
from typing import Optional

import requests


SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
_SB_WRITE = ({'apikey': SUPABASE_KEY,
              'Authorization': f'Bearer {SUPABASE_KEY}',
              'Content-Type': 'application/json'}
             if SUPABASE_KEY else None)


def _write_env() -> tuple[str | None, dict | None]:
    """Resolve the write credentials AT CALL TIME, not at import.

    2026-09-23. Reading these at module scope is a landmine, and it went
    off. generate_ncaaf_game_reads.py imports this module on line 27 and
    calls load_dotenv on line 91 — sixty-four lines later. So the module
    captured SUPABASE_URL=None, _SB_WRITE=None, and upsert_jerry_read
    returned False from its guard on every single call while the caller
    printed "✓ wrote cache" and reported 85/85 success.

    In CI the vars are already exported into the environment, so the
    dual-write works there and the defect is invisible. It only bites a
    local run — which is exactly when someone is repairing something and
    most needs the write to land. Two full 85-game regenerations were
    spent before this surfaced, both reporting success, neither writing a
    row.

    Reading os.environ on each call costs nothing and removes any
    dependence on import order.
    """
    url = os.environ.get('SUPABASE_URL') or SUPABASE_URL
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY') or SUPABASE_KEY)
    if not url or not key:
        return url, None
    return url, {'apikey': key, 'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json'}


_VALID_MARKETS = {'ml', 'spread', 'rl', 'total', 'prop', 'lean', 'pass', None}
_VALID_SIDES = {'HOME', 'AWAY', 'OVER', 'UNDER', None}


def smart_truncate_short(text: str, max_chars: int = 350) -> str:
    """Cap short_read at max_chars but back up to last sentence boundary.

    2026-09-16: added after HOU@TTU screenshot showed short_read of 480ch
    that included the first paragraph of a multi-paragraph LLM SHORT
    section, cut off mid-sentence at 'projected point margi'. LLM
    sometimes ignores the 40-60 word prompt constraint and writes
    long-form into the SHORT slot. This truncates gracefully so the
    user sees complete sentences instead of chopped mid-word text.

    Rules:
      - Input <= max_chars: return unchanged.
      - Input > max_chars: cut at max_chars, then back up to the last
        sentence-ending punctuation (. ! ?) if within the second half
        of the cut so we're not throwing away most of the content.
      - Final fallback: append ellipsis at hard cut.
    """
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in ('. ', '! ', '? ', '.\n', '!\n', '?\n', '."', '.', '!', '?'):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.5:
            return cut[:idx + len(sep.rstrip())].rstrip() + (sep.rstrip()[-1] if sep.rstrip() and sep.rstrip()[-1] not in '.!?' else '')
    return cut.rstrip() + '…'


def strip_section_markers(txt, fallback_key: str):
    """Remove parser section markers from text that is about to be published.

    A marker like ---SHORT--- is an instruction to the parser and must never
    reach a subscriber. Three NFL reads shipped with short_read literally
    beginning with the marker, then a newline, then "LA's passing offense
    ranks 11th...".

    SHARED ON PURPOSE. 2026-09-24: this started life as a nested helper inside
    upsert_jerry_read, and I claimed in the commit that being "the last gate
    before the write" meant it caught bad text "no matter which path produced
    it". That was wrong. generate_nfl_game_reads has its OWN writer,
    upsert_jerry_read_nfl, which posts to jerry_reads directly and imports only
    smart_truncate_short from this module. So the guard never ran on the NFL
    path, and the markers came back on the very next generator run — the same
    three reads I had repaired by hand that morning.

    The real leak is that writer's fallback:

        'short_read': parsed.get('short_read') or narrative[:500]

    When parse_synthesis yields no section, the RAW narrative is published,
    markers included.

    So this lives at module level and both writers call it. A third writer
    should call it too rather than grow a fourth copy — the duplicated
    "align read to pick" logic in this codebase is the same mistake one level
    up.
    """
    if not txt:
        return txt
    if '---SHORT---' not in txt and '---LONG---' not in txt:
        return txt
    reparsed = parse_synthesis(txt) or {}
    candidate = reparsed.get(fallback_key)
    # VERIFY THE OUTPUT, do not trust it. parse_synthesis has an
    # unmarked-prose fallback: when no section yields content it assigns the
    # whole raw string to short_read and long_read. Feed it a string whose
    # section is empty — "---SHORT---" alone, or a marker with nothing after
    # it — and it hands the marker straight back, so this function returned
    # the exact text it exists to remove. A guard that can emit the thing it
    # is guarding against is not a guard.
    if candidate and '---' not in candidate:
        return candidate
    stripped = re.sub(r'---[A-Z]+---\s*', '', txt).strip()
    return stripped or None


def parse_synthesis(raw: str) -> dict:
    """Parse a Jerry LLM synthesis into structured pick fields.

    Expects sections `---SHORT---`, `---LONG---`, `---CALL---` (with
    MARKET / SIDE / LINE / CALL_TEXT / CONVICTION fields inside CALL).

    Falls back gracefully — missing CALL block returns all-None call
    fields so caller can still store prose without a structured pick.
    """
    if not raw:
        return {'short_read': None, 'long_read': None, 'call_market': None,
                'call_side': None, 'call_line': None, 'call_text': None,
                'conviction': None}

    def _section(name):
        m = re.search(rf"---{name}---\s*(.*?)(?=---[A-Z]+---|$)", raw, re.S)
        return m.group(1).strip() if m else None

    short = _section('SHORT') or ''
    long_ = _section('LONG') or ''
    call_block = _section('CALL') or ''

    # 2026-08-31: fallback when the LLM returned free-form prose (no
    # ---SHORT--- markers). NCAAF + NFL prompt templates don't enforce
    # the section format so their LLM narratives were dropping to
    # short_read=None → upsert_jerry_read never fired → jerry_reads
    # stayed on bridge output only. Treat unmarked responses as a
    # single short_read (first 480 chars) so downstream still persists.
    if not short and not long_ and not call_block:
        _raw = (raw or '').strip()
        if _raw:
            short = _raw[:480]
            long_ = _raw

    call_block = re.sub(r'\*+', '', call_block)
    call_block = re.sub(r'_+', '', call_block)

    def _field(field):
        m = re.search(rf"\**{field}\**\s*:\s*(.+?)(?=\n\**[A-Z_]+\**\s*:|$)",
                      call_block, re.S)
        if not m:
            return None
        val = m.group(1).strip()
        val = re.sub(r'^[*_\s]+|[*_\s]+$', '', val)
        return val or None

    market = (_field('MARKET') or '').lower() or None
    side = (_field('SIDE') or '').upper() or None
    if side == 'NULL':
        side = None
    line_raw = _field('LINE')
    try:
        line = float(line_raw) if line_raw and line_raw.lower() != 'null' else None
    except ValueError:
        line = None
    call_text = _field('CALL_TEXT')
    conv_raw = _field('CONVICTION')
    try:
        conviction = (max(0, min(100, int(re.sub(r'\D', '', conv_raw or ''))))
                      if conv_raw else None)
    except ValueError:
        conviction = None

    if market not in _VALID_MARKETS:
        market = None
        side = None
    if side not in _VALID_SIDES:
        side = None

    # 2026-09-16: smart-truncate short_read at last complete sentence
    # (see smart_truncate_short docstring). Applies to both the section-
    # parsed path AND the free-form fallback since LLM ignores the
    # 40-60 word ask both ways.
    short_out = smart_truncate_short(short or '') if short else None
    return {
        'short_read': short_out,
        'long_read': long_ or None,
        'call_market': market,
        'call_side': side,
        'call_line': line,
        'call_text': call_text,
        'conviction': conviction,
    }


_VALID_MARKETS_BY_SPORT = {
    'MLB':   {'ml', 'rl', 'total', 'nrfi', 'yrfi'},
    'NFL':   {'ml', 'rl', 'spread', 'total'},
    'NCAAF': {'ml', 'rl', 'spread', 'total'},
    'NBA':   {'ml', 'rl', 'spread', 'total'},
    'NCAAB': {'ml', 'rl', 'spread', 'total'},
    'NHL':   {'ml', 'rl', 'puckline', 'total'},
    'UFC':   {'ml', 'fight'},
}


def retarget_line(prose: str | None, call_label: str, call_line=None) -> str | None:
    """Rewrite the line numbers inside prose to match the call.

    2026-09-23. When the market is right and only the NUMBER drifted —
    MIL@PHI called Under 7.0 while the prose argued "make Under 7.5 the
    lean" — discarding the read costs 1,400 characters of real analysis
    over one stale digit. Correcting the digit keeps the analysis and
    removes the lie.

    Only ever applied when the prose already talks about the same market
    as the call; a moneyline read is never retargeted into a total.
    Returns None when there is no line to align to.
    """
    import re as _re
    if not prose:
        return None
    m = _re.search(r'(?:Under|Over)\s*([\d]+(?:\.[\d]+)?)', str(call_label or ''))
    target = m.group(1) if m else (str(call_line) if call_line is not None else None)
    if target is None:
        return None
    try:
        float(target)
    except (TypeError, ValueError):
        return None
    # Show the line exactly as the CALL spells it. `:g` would render 7.0
    # as "7", and a betting line written without its decimal reads as a
    # different number to anyone scanning the card.
    disp = target if m else f'{float(target):.1f}'

    def _sub(mm):
        return f'{mm.group(1)} {disp}'
    out = _re.sub(r'\b(Under|Over)\s*[\d]+(?:\.[\d]+)?', _sub, prose)
    return out


def prose_is_stale(short: str | None, market: str, label: str,
                   line=None) -> bool:
    """True when short_read no longer describes the call it sits beside.

    2026-09-23. Three symptoms on the 09-23 MLB card, one cause: the
    pick moved and the prose did not.

      HOU@SEA   call "Seattle Mariners ML"   prose "Under 8.0 — ..."
      CLE@BOS   call "Cleveland Guardians ML" prose "Under 6.5 — OC-dissent flip"
      MIL@PHI   call "Under 7.0"              prose "...make Under 7.5 the lean"

    The first two changed market — a total became a moneyline and the
    words stayed on the total. The third kept its market and had the
    line move underneath it. A user reading either sees us recommend a
    number we are not offering.

    Previously only two cases forced a rebuild: prose that said "engine
    passed", and prose under 60 characters. Neither catches a confident
    sentence about the wrong bet, which is the dangerous one, because it
    reads as analysis rather than as an obvious stub.

    Checks, in order of how badly they mislead:
      1. pass-prose sitting on a live call
      2. prose naming a total when the call is a moneyline (or vice versa)
      3. prose naming a different line than the call
      4. prose so short it is a field dump, not a read
    """
    import re as _re
    sr = (short or '').strip()
    if not sr:
        return True
    low = sr.lower()
    if 'engine passed' in low or low.startswith('pass'):
        return True

    mkt = (market or '').lower()
    prose_total = bool(_re.search(r'\b(Under|Over)\s*\d', sr))
    prose_ml = bool(_re.search(r'\bML\b|moneyline', sr, _re.I))
    if mkt == 'ml' and prose_total and not prose_ml:
        return True
    if mkt in ('total', 'nrfi', 'yrfi') and prose_ml and not prose_total:
        return True

    # A line named in the prose must be the line we are offering. Only
    # judged when the CALL itself carries a number — an ML has none.
    call_num = None
    m = _re.search(r'(?:Under|Over)\s*([\d]+(?:\.[\d]+)?)', str(label or ''))
    if m:
        call_num = m.group(1)
    elif line is not None:
        call_num = str(line)
    if call_num is not None:
        try:
            cv = float(call_num)
            named = [float(v) for v in
                     _re.findall(r'(?:Under|Over)\s*([\d]+(?:\.[\d]+)?)', sr)]
            if named and all(abs(v - cv) > 1e-9 for v in named):
                return True
        except ValueError:
            pass

    return len(sr) < 100


def derive_short_read(short: str | None, long: str | None,
                      narrative: str | None = None) -> str | None:
    """A short_read worth showing, derived from long_read when needed.

    2026-09-22 EXTRACTED so the alignment backfill can use it too.

    This logic already lived inline in upsert_jerry_read, which meant it
    only ran on the LLM write path. backfill_jerry_pick_alignment PATCHes
    jerry_reads directly, so anything it wrote skipped the guard entirely
    — and after today's pass->play prose resync that produced reads like

        "Under 8.0 - Supervised total model backs Under - 76% confidence"

    52-64 characters of engine output sitting next to games whose read is
    280 characters of actual analysis. Andy: "pre analysis mlb reads not
    uniform". Both surfaces now derive the same way, and the FULL prose
    was there the whole time — 5 of those 8 games had a real long_read.

    Rule unchanged from the original: a short_read of real length is kept
    as written; anything thinner is rebuilt from the first two sentences
    of long_read. The sentence split guards "vs." / "St." / "e.g." by
    requiring the period to be followed by space + capital.
    """
    import re as _re
    short = (short or '').strip()
    long = (long or '').strip()
    if short and len(short) >= 100:
        return short
    if long:
        sents = _re.split(r'(?<=[.!?])\s+(?=[A-Z])', long)
        return ' '.join(sents[:2])[:400] or long[:400]
    if short:
        return short
    return ((narrative or '')[:500] or None)


def enforce_primary_play_alignment(sport: str, parsed: dict, struct: dict) -> dict:
    """Force parsed jerry_read call_* fields to match ensemble primary_play.

    2026-09-10 PERMANENT CROSS-SPORT FIX.

    Chronic bug across MLB / NFL / NCAAF (user has flagged repeatedly):
    ensemble picks one thing, LLM narrative writes about another, both
    ship on the same card. Users see BUF ML badge + "UNDER is the edge"
    prose. Kills trust.

    Rule: ensemble.primary_play is the single source of truth for the PICK
    (call_market / call_side / call_line / call_text / conviction). The
    LLM's prose (short_read, long_read) is preserved as-is — it's the
    ANALYSIS layer, not the pick layer. This function overwrites the
    call_* fields at write time.

    MLB has had this since 2026-08-22 (generate_jerry_synthesis.py:609
    defer_call_to_ensemble). This function ports the pattern to every
    sport using the shared dual_write path.

    Behavior:
      - ensemble tier PRIME/STRONG/LEAN + valid market/side/label → align
      - ensemble tier COVERAGE/PASS/SKIP → force call_market='pass',
        preserve original LLM prose (long_read + short_read) if analytical
      - primary_play absent or malformed → leave parsed as-is (fall back
        to LLM's opinion rather than nulling the pick entirely)
    """
    if not isinstance(struct, dict): return parsed
    pp = struct.get('primary_play')
    if not isinstance(pp, dict): return parsed
    market = str(pp.get('type') or '').lower()
    side = pp.get('side')
    label = pp.get('label')
    conviction = pp.get('conviction')
    line = pp.get('line')
    tier = str(pp.get('tier') or '').upper()
    valid_markets = _VALID_MARKETS_BY_SPORT.get(sport.upper(), set())
    # ── 2026-09-23: a PASS now requires that NO PICK EXISTS ─────────
    #
    # This used to read:
    #
    #     if tier in ('COVERAGE','PASS','SKIP') or market not in valid
    #        or not side or not label:
    #             -> emit Pass
    #
    # so a demoted-but-real pick became "engine passed — no publishable
    # edge". That is not what a demotion means. COVERAGE is assigned by
    # the LR override, the MC-dissent gate and the juice cap to a pick
    # that still HAS a side, a line and a conviction — it means "we hold
    # this lightly", not "we have nothing to say".
    #
    # On 09-23 that distinction cost the whole card: 8 of 16 games shipped
    # as "engine passed" while carrying live picks underneath, five of
    # them PRIME, one at conviction 97. Every one was COVERAGE only
    # because a blind LR model emitted its all-features-missing constant
    # (fixed separately in defensive_gates._is_blind).
    #
    # Andy, 09-23: "there shouldn't be any passes on any games, there
    # should be some kind of take and a lean/strong/prime."
    #
    # SAFE TO SHOW, because the record does not read this field.
    # compute_surface_records and aggregate_daily_records both filter on
    # primary_play.tier in ('PRIME','STRONG') — a COVERAGE game is
    # excluded from every published record no matter what the read says.
    # So surfacing the take is a display change, not a record change: the
    # user sees our actual lean and its confidence, and the number we
    # publish is untouched.
    #
    # A pass is now emitted ONLY when there is genuinely nothing to show:
    # no side, no label, or a market we cannot render. That makes a
    # mislabelled pass impossible by construction rather than by
    # vigilance — there is no branch left that turns a real pick into
    # "engine passed".
    have_pick = bool(side) and bool(label) and market in valid_markets
    if have_pick and tier in ('COVERAGE', 'PASS', 'SKIP'):
        # Demoted, not absent. Show the take at its real confidence.
        parsed['call_market'] = market
        parsed['call_side'] = str(side).upper()
        parsed['call_line'] = line
        parsed['call_text'] = label
        if isinstance(conviction, (int, float)):
            parsed['conviction'] = max(0, min(100, int(conviction)))
        # Prose that argued for a PASS no longer describes this row.
        _sr = (parsed.get('short_read') or '').strip()
        if (not _sr) or 'engine passed' in _sr.lower() or _sr.lower().startswith('pass'):
            _sub = str(pp.get('sub') or '').strip()
            parsed['short_read'] = (f'{label} — {_sub}'[:2000] if _sub else str(label))
        return parsed

    if not have_pick:
        parsed['call_market'] = 'pass'
        parsed['call_side'] = None
        parsed['call_line'] = None
        parsed['call_text'] = 'Pass'
        parsed['conviction'] = 0
        # ── 2026-09-22: short_read is now ALWAYS rewritten on a pass ──
        #
        # The prior rule preserved the LLM's prose unless it was under 60
        # characters, reasoning that the analysis is still useful even
        # when we decline the bet. That reasoning is wrong for THIS field.
        # The LLM wrote its read to argue for the pick the engine then
        # killed, so preserving it renders as:
        #
        #     [ NO PLAY ]  Michael King has been sharp lately ... The data
        #                  leans under. Back the UNDER 8.
        #
        # which is SD@LAD on the 09-22 card — a refusal and a
        # recommendation stacked on one game. The analysis is not lost: it
        # stays in long_read, which is what the detail view renders. This
        # field is the one-line card summary, and on a pass the only
        # honest summary is why we passed.
        #
        # The MC-dissent branch below came from defer_call_to_ensemble,
        # which was a near-copy of this function carrying better prose.
        # That copy is now a delegate — see generate_jerry_synthesis.
        dissent = pp.get('_mc_dissent') or {}
        pct, orig = dissent.get('mc_pick_win_pct'), dissent.get('orig_tier')
        if pct is not None and orig:
            new_short = (f'Engine passed — the {orig} setup collapses under '
                         f'MC sim ({pct}% win prob for our side). No play.')
        else:
            engine_sub = str(pp.get('sub') or '').strip()
            new_short = ('Engine passed — no publishable edge on this game.'
                         + (f' {engine_sub}' if engine_sub else ''))
        parsed['short_read'] = new_short[:2000]
        return parsed
    # Real pick — align badge fields to ensemble
    parsed['call_market'] = market
    parsed['call_side'] = str(side).upper()
    parsed['call_line'] = line
    parsed['call_text'] = label   # human-readable e.g. "PHI +5.5"

    # 2026-09-23: the prose must describe THIS call. When the pick moves
    # and the words do not, the card recommends a bet we are not
    # offering — see prose_is_stale for the three 09-23 cases. Rebuild
    # from long_read when there is real analysis there; fall back to the
    # engine's own stated reason only when there is nothing else.
    if prose_is_stale(parsed.get('short_read'), market, label, line):
        _short = (parsed.get('short_read') or '').strip()
        _long = (parsed.get('long_read') or '').strip()
        _rebuilt = None

        # Line-only drift: same market, wrong number. Correct the number
        # rather than throw the analysis away — discarding 1,400
        # characters of real reasoning over one stale digit is a worse
        # read, not a safer one.
        for _src in (_short, _long):
            if not _src:
                continue
            _fixed = retarget_line(_src, label, line)
            if _fixed and not prose_is_stale(_fixed, market, label, line):
                _rebuilt = derive_short_read(None, _fixed) if _src is _long else _fixed
                break

        if not _rebuilt and _long and not prose_is_stale(_long, market, label, line):
            _rebuilt = derive_short_read(None, _long)
        if not _rebuilt:
            _sub = str(pp.get('sub') or '').strip()
            _rebuilt = f'{label} — {_sub}' if _sub else str(label)
        parsed['short_read'] = _rebuilt[:2000]
    if isinstance(conviction, (int, float)):
        parsed['conviction'] = max(0, min(100, int(conviction)))

    # ── 2026-09-22 PASS→PLAY PROSE STALENESS ────────────────────────
    # Preserving prose is right for play→play and play→pass: the
    # analysis still describes the game. It is WRONG for pass→play,
    # because pass-prose does not analyse the game, it narrates the
    # DECISION NOT TO BET — and that decision has just been reversed.
    #
    # This is a sequencing bug, not a logic one. Both this function and
    # generate_jerry_synthesis handle PASS correctly at the moment they
    # run. What breaks is the gap between runs:
    #
    #   11:54-11:57  primary_play was soft → Jerry writes
    #                "Engine passed — no publishable edge on this game."
    #   15:30        recompute upgrades the pick to PRIME
    #                this function re-points the badge at the new pick
    #                prose is preserved → stale
    #
    # 5 MLB games shipped that way on 09-22, four of them PRIME. The
    # card read "Boston Red Sox ML · 0" directly above "the PRIME setup
    # collapses under MC sim. No play." — the engine contradicting
    # itself on one screen.
    #
    # Rewriting from the ensemble's own `sub` invents nothing: that
    # string is the engine's stated reason for the pick it just made.
    # Long_read is dropped rather than patched — it is a multi-paragraph
    # argument for passing and cannot be salvaged by find/replace. A
    # short true read beats a long false one.
    _short = (parsed.get('short_read') or '')
    if 'engine passed' in _short.lower() or _short.strip().lower().startswith('pass'):
        engine_sub = str(pp.get('sub') or '').strip()
        parsed['short_read'] = (
            f'{label} — {engine_sub}'[:2000] if engine_sub else str(label)
        )
        _long = (parsed.get('long_read') or '')
        if 'engine passed' in _long.lower() or 'no play' in _long.lower():
            parsed['long_read'] = None
        parsed['_prose_resynced'] = True
    return parsed


def upsert_jerry_read(*, sport: str, game_id: str, game_date: str,
                      struct: dict, parsed: dict, narrative: str,
                      prompt_version: str) -> bool:
    """Upsert into jerry_reads on (sport, game_id, game_date).

    Non-fatal — prints a warning on HTTP error, returns False. Skips
    silently when Supabase env is missing (unit-test friendly).

    2026-09-10: enforces ensemble alignment on every write. See
    enforce_primary_play_alignment() docstring above.
    """
    _url, _hdr = _write_env()
    if not _hdr or not _url:
        # Say so. This used to return False in silence, which is how two
        # full 85-game regenerations reported success while writing
        # nothing.
        print('  ⚠ jerry_reads dual-write SKIPPED — no Supabase write '
              'credentials in os.environ at call time')
        return False
    # ─── ENSEMBLE ALIGNMENT ENFORCER ───────────────────────────────
    # Runs BEFORE the truncation guard so the guard sees final prose.
    parsed = enforce_primary_play_alignment(sport, parsed, struct)
    # 2026-09-05 short_read truncation guard. LLM sometimes emits a
    # fragment like "UCLA's SP+ sits at 5.4 vs." (26 chars, cut on
    # "vs." period). If short is under 100 chars, derive from the
    # first 1-2 sentences of long_read instead so users don't see
    # mid-sentence garbage. Preserves LLM when it wrote a real short.
    # 2026-09-24 MARKER GUARD. A section marker is a parser instruction
    # and must never reach a subscriber. Three NFL reads shipped with
    # short_read literally beginning "---SHORT---\nLA's passing offense
    # ranks 11th..." because some write path stored the raw narrative
    # instead of the parsed sections.
    #
    # The parser itself is fine — re-running parse_synthesis on those
    # stored strings returns clean prose. So rather than hunt every
    # caller that might hand over raw text, re-parse here whenever a
    # marker is present. This is the last gate before the write, so it
    # catches the bad text no matter which path produced it.
    _demarker = strip_section_markers

    parsed = dict(parsed)
    parsed['short_read'] = _demarker(parsed.get('short_read'), 'short_read')
    parsed['long_read'] = _demarker(parsed.get('long_read'), 'long_read')

    _short_raw = parsed.get('short_read') or ''
    _long_raw = _demarker(parsed.get('long_read') or narrative or '', 'long_read')
    if _short_raw and len(_short_raw) >= 100:
        _short_final = _short_raw
    elif _long_raw:
        # Take first ~2 sentences from long_read (up to 350 chars).
        # Sentence split guarded against "vs." / "St." / "e.g." abbrevs
        # by requiring the period to be followed by space + capital OR
        # end-of-string.
        import re as _re
        _sents = _re.split(r'(?<=[.!?])\s+(?=[A-Z])', _long_raw.strip())
        _short_final = ' '.join(_sents[:2])[:400] or _long_raw[:400]
    else:
        _short_final = (narrative or '')[:500] or None

    payload = {
        'sport': sport,
        'game_id': game_id,
        'game_date': game_date,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'prompt_version': prompt_version,
        # 2026-09-15 read enrichment: was {source, matchup} only — every
        # other field build_struct populated (market, model, efficiency,
        # confluence, primary_play, sweat, cohort_tags, pre_parsed_facts,
        # casual_summary, and future key_players/injuries) got silently
        # dropped before write. Same class of bug as NFL's whitelist. Fix:
        # whitelist all enrichment keys the shared upsert might see across
        # sports — additive/safe (missing keys just don't show up).
        'input_snapshot': {
            **{k: (struct or {}).get(k) for k in (
                'matchup', 'market', 'model', 'efficiency', 'confluence',
                'primary_play', 'sweat', 'cohort_tags', 'pre_parsed_facts',
                'casual_summary', 'key_players', 'injuries', 'team_snapshot',
                'signals', 'align_status', 'season', 'week', 'season_type',
                'neutral_site', 'conference_game',
            ) if (struct or {}).get(k) is not None},
            'source': f'generate_{sport.lower()}_game_reads',
        },
        'short_read': _short_final,
        'long_read': parsed.get('long_read') or narrative or None,
        'call_text': parsed.get('call_text'),
        'call_market': parsed.get('call_market'),
        'call_side': parsed.get('call_side'),
        'call_line': parsed.get('call_line'),
        'call_odds_est': None,
        'conviction': parsed.get('conviction') or 0,
    }
    try:
        r = requests.post(
            f'{_url}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
            headers={**_hdr,
                     'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json=payload, timeout=15,
        )
    except Exception as e:
        print(f'  ⚠️ jerry_reads upsert exception ({sport}): {e}')
        return False
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠️ jerry_reads upsert failed ({sport}) '
              f'{r.status_code}: {r.text[:200]}')
        return False
    return True
