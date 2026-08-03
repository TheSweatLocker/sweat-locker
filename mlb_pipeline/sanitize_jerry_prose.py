"""Post-LLM brand-name sanitizer for Jerry synthesis output (2026-08-03).

Belt-and-suspenders defense: the prompt has a BRAND ATTRIBUTION GUARDRAIL,
but LLMs occasionally leak brand names from the input struct despite the
rule. This module regex-scrubs Jerry's output before storage, replacing
banned brand names with the approved generic phrasings.

Called AFTER parse_synthesis and BEFORE upsert_jerry_read in the synth
pipeline. Also used by generate_prop_jerry_synthesis.

Design principles:
  1. Case-insensitive matching
  2. Replace with the closest generic term
  3. Preserve capitalization when possible
  4. Idempotent — re-running on cleaned text is a no-op

Per feedback_brand_attribution_803: External Picks panel handles named
attribution separately; this module only sanitizes Jerry's PROSE output.

Usage:
    from sanitize_jerry_prose import scrub
    clean_short = scrub(parsed['short_read'])
    clean_long  = scrub(parsed['long_read'])
"""
import re
from typing import Optional

# Order matters — longer patterns first to prevent partial matches
# (e.g. "Action Network" before "Action")
BRAND_REPLACEMENTS = [
    # Data providers (money flow, aggregators, models)
    (r'\b(?:the\s+)?OddsCrowd(?:\.com)?\b', 'sharp money data'),
    (r'\bAction\s+Network\b',               'external aggregator'),
    (r'\bVSiN(?:\s+[A-Z][a-z]+)?\b',        'external handicapper'),
    (r'\bCircaSports\b',                     'sharp market'),
    (r'\bCirca\b',                           'sharp market'),
    (r'\bPinnacle\b',                        'sharp market'),
    (r'\bCovers(?:\.com)?\b',               'external aggregator'),
    (r'\bRotogrinders\b',                   'external aggregator'),
    (r'\bKenPom\b',                         'efficiency model'),
    (r'\bFanGraphs\b',                      'advanced stats'),
    # Handicappers / individual pickers (safe on any casing)
    (r"\bDoc\s+Sports\b",                    'external handicapper'),
    (r"\bBetfirm(?:\'s)?\b",                'external handicapper'),
    (r"\bRocky\s+Atkinson\b",                'external handicapper'),
    (r"\bTony(?:'s)?\s+Picks?\b",            'external handicapper'),
    (r"\bPickswise\b",                       'external handicapper'),
    (r"\bPickdawgz\b",                       'external handicapper'),
    (r"\bPeterson\b(?=\s|,|\.|$)",           'external handicapper'),  # VSiN Peterson
    # Sportsbooks (Jerry shouldn't cite specific books)
    (r'\bDraftKings\b',                      'the market'),
    (r'\bFanDuel\b',                         'the market'),
    (r'\bBetMGM\b',                          'the market'),
    (r'\bESPN\s+BET\b',                      'the market'),
    (r'\bHard\s+Rock(?:\s+Bet)?\b',         'the market'),
    (r'\bWilliam\s+Hill\b',                  'the market'),
    (r'\bBovada\b',                          'the market'),
    (r'\bBetRivers\b',                       'the market'),
    # Data source products
    (r'\bStatcast\b',                        'batted-ball data'),
    (r'\bBaseball[-\s]?Reference\b',         'historical stats'),
    (r'\bPro[-\s]?Football[-\s]?Reference\b','historical stats'),
]

# Compile once for perf
_COMPILED = [(re.compile(pattern, re.IGNORECASE), replacement)
              for pattern, replacement in BRAND_REPLACEMENTS]


def scrub(text: Optional[str]) -> Optional[str]:
    """Return text with all banned brand names replaced by generic phrasings.
    Idempotent: scrub(scrub(x)) == scrub(x). Preserves None input."""
    if text is None or not text: return text
    cleaned = text
    for pat, replacement in _COMPILED:
        cleaned = pat.sub(replacement, cleaned)
    return cleaned


def scrub_dict(d: dict, keys: tuple = ('short_read', 'long_read')) -> dict:
    """Scrub specific string keys in a dict. Returns new dict (non-mutating)."""
    if not isinstance(d, dict): return d
    out = dict(d)
    for k in keys:
        if k in out and isinstance(out[k], str):
            out[k] = scrub(out[k])
    return out


def audit(text: Optional[str]) -> list:
    """Return list of brand-name matches found in text (for logging/testing).
    Empty list = clean."""
    if not text: return []
    hits = []
    for pat, _ in _COMPILED:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    return hits


if __name__ == '__main__':
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass
    # Smoke test
    samples = [
        "OddsCrowd shows 79% money UNDER; sharp fade of public action.",
        "Doc Sports and Rocky Atkinson both lean HOME; Tony's Picks fades.",
        "KenPom rank #4 vs #47 — massive efficiency gap.",
        "DraftKings moved from -110 to -125; Action Network money% jumped.",
        "Pickdawgz, Doc Sports, Pickswise all lean home.",
        "Pass — no clean signal (already clean).",
    ]
    print('=== sanitizer smoke test ===')
    for s in samples:
        cleaned = scrub(s)
        marker = '✓' if s == cleaned else '🔧'
        print(f'\n{marker} in:  {s}')
        print(f'   out: {cleaned}')
        found = audit(cleaned)
        if found:
            print(f'   ⚠ still contains: {found}')
