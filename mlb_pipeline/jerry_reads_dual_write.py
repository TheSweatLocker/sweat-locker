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


_VALID_MARKETS = {'ml', 'spread', 'rl', 'total', 'prop', 'lean', 'pass', None}
_VALID_SIDES = {'HOME', 'AWAY', 'OVER', 'UNDER', None}


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

    return {
        'short_read': short or None,
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
    # Soft-tier / no-pick path → force PASS on the badge, preserve prose
    if tier in ('COVERAGE', 'PASS', 'SKIP') or market not in valid_markets or not side or not label:
        parsed['call_market'] = 'pass'
        parsed['call_side'] = None
        parsed['call_line'] = None
        parsed['call_text'] = 'Pass'
        parsed['conviction'] = 0
        # Prose stays — the LLM's analytical read is still valuable to the
        # user even when we're not publishing a pick. This is different
        # from the audit path that used to overwrite prose (fixed
        # 2026-09-10 in jerry_pre_publish_audit.py). Belt-and-suspenders:
        # only rewrite short_read if it's suspiciously short.
        orig_short = (parsed.get('short_read') or '').strip()
        if len(orig_short) < 60:
            engine_sub = str(pp.get('sub') or '').strip()
            new_short = (f'Engine passed — no publishable edge on this game.'
                         + (f' {engine_sub}' if engine_sub else ''))
            parsed['short_read'] = new_short[:2000]
        return parsed
    # Real pick — align badge fields to ensemble
    parsed['call_market'] = market
    parsed['call_side'] = str(side).upper()
    parsed['call_line'] = line
    parsed['call_text'] = label   # human-readable e.g. "PHI +5.5"
    if isinstance(conviction, (int, float)):
        parsed['conviction'] = max(0, min(100, int(conviction)))
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
    if not _SB_WRITE or not SUPABASE_URL:
        return False
    # ─── ENSEMBLE ALIGNMENT ENFORCER ───────────────────────────────
    # Runs BEFORE the truncation guard so the guard sees final prose.
    parsed = enforce_primary_play_alignment(sport, parsed, struct)
    # 2026-09-05 short_read truncation guard. LLM sometimes emits a
    # fragment like "UCLA's SP+ sits at 5.4 vs." (26 chars, cut on
    # "vs." period). If short is under 100 chars, derive from the
    # first 1-2 sentences of long_read instead so users don't see
    # mid-sentence garbage. Preserves LLM when it wrote a real short.
    _short_raw = parsed.get('short_read') or ''
    _long_raw = parsed.get('long_read') or narrative or ''
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
        'input_snapshot': {
            'source': f'generate_{sport.lower()}_game_reads',
            'matchup': (struct or {}).get('matchup'),
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
            f'{SUPABASE_URL}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
            headers={**_SB_WRITE,
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
