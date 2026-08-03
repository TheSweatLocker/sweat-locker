"""Post-LLM hallucination detector for Jerry synthesis output (2026-08-03).

Sprint 2 deliverable — the "zero-hallucination" architecture promised in
the NFL prop pipeline spec. Runs after parse_synthesis + sanitize, before
upsert. Scans Jerry's prose for specific numeric claims and verifies each
one traces back to the input struct.

Why: Today's Cole/Yankees pitcher-attribution hallucination proved that
soft prompt rules aren't sufficient. Jerry sometimes invents numbers.
This module is the hard defense.

Approach:
  1. Extract every specific numeric token from short_read + long_read
     (matches patterns like 287, 42.5, 6.7, 79%, etc.)
  2. Flatten the input struct into a set of allowed numeric strings
     (with common formatting variations: 6.5 also matches 6.50, etc.)
  3. For each cited number in output, check if it appears in the allowed
     set OR within 1% tolerance (for rounding — 287 ≈ 287.4)
  4. Flag anything unmatched → hallucination candidate
  5. Return {is_valid, hallucinated_numbers, allowed_count, cited_count}

Not every unmatched number is a hallucination — Jerry may compute derived
values (e.g. "6.7% edge" from projection 257 vs line 275). Whitelist:
  - percentages (may be derived)
  - single-digit small numbers (may be sample counts like "3 of 5")
  - round numbers matching common lines (0.5, 1.5, 2.5, 6.5, 7.5, 8.5)

For MVP: validator LOGS issues + returns report. Auto-regenerate on
detected hallucinations is Sprint 2b (needs prompt corrective loop).

Usage:
    from validate_jerry_read import validate
    report = validate(short_read, long_read, input_struct)
    if not report['is_valid']:
        print(f'⚠ hallucinated: {report["hallucinated_numbers"]}')
"""
from __future__ import annotations
import re, json
from typing import Any


# Match specific numeric tokens (whole/decimal, optional %) but exclude
# obvious non-data-numbers like years (2024, 2025) or standalone integers
# that look like counts ("3 of 5").
_NUM_RE = re.compile(r'(?<![a-zA-Z])(\d+(?:\.\d+)?)(%?)(?![a-zA-Z])')

# Numbers to whitelist regardless — common line values, small counts, etc.
_LINE_WHITELIST = {
    '0.5', '1.5', '2.5', '3.5', '4.5', '5.5', '6.5', '7.5', '8.5', '9.5',
    '10.5', '11.5', '12.5', '13.5', '14.5',   # NFL/MLB common lines
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',   # counts
    '100', '110',   # standard American odds framings
}

_YEAR_RE = re.compile(r'^20[12]\d$')  # 2010-2029 as year


def _extract_numbers(text: str) -> list[str]:
    """Return list of numeric tokens found in text, with % marker preserved."""
    if not text: return []
    out = []
    for m in _NUM_RE.finditer(text):
        num, pct = m.group(1), m.group(2)
        # Skip obvious years
        if _YEAR_RE.match(num): continue
        out.append(f'{num}{pct}')
    return out


def _flatten_struct(obj: Any, out: set):
    """Recursively collect all numeric-looking values from a nested struct."""
    if obj is None:
        return
    if isinstance(obj, (int, float)):
        # Add both integer and one-decimal forms
        s = str(obj)
        out.add(s)
        # Also add rounded / truncated variants
        try:
            f = float(obj)
            out.add(f'{f:.0f}')
            out.add(f'{f:.1f}')
            out.add(f'{f:.2f}')
        except (ValueError, TypeError): pass
    elif isinstance(obj, str):
        # Extract embedded numbers from string values
        for m in _NUM_RE.finditer(obj):
            num, pct = m.group(1), m.group(2)
            if _YEAR_RE.match(num): continue
            out.add(f'{num}{pct}')
            out.add(num)  # also without %
            try:
                f = float(num)
                out.add(f'{f:.0f}')
                out.add(f'{f:.1f}')
            except ValueError: pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_struct(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten_struct(v, out)


def _within_tolerance(cited: str, allowed_set: set, pct_tol: float = 1.0) -> bool:
    """Check if cited number is within pct_tol% of any allowed number."""
    try:
        c = float(cited.rstrip('%'))
    except ValueError:
        return False
    for a in allowed_set:
        try:
            av = float(a.rstrip('%'))
        except ValueError: continue
        if av == 0:
            if c == 0: return True
            continue
        if abs(c - av) / abs(av) <= pct_tol / 100:
            return True
    return False


def validate(short_read: str, long_read: str, input_struct: dict,
             tolerance_pct: float = 1.0) -> dict:
    """Validate Jerry's output against input struct.

    Returns:
        {
          'is_valid': bool (True if no hallucinations detected),
          'cited_numbers': [...],
          'allowed_count': int,
          'hallucinated_numbers': [...],
          'notes': str,
        }
    """
    text = (short_read or '') + '\n' + (long_read or '')
    cited = _extract_numbers(text)
    cited_unique = list(set(cited))

    allowed = set()
    _flatten_struct(input_struct, allowed)
    allowed_count = len(allowed)

    hallucinated = []
    for num in cited_unique:
        # Skip whitelisted lines / counts
        num_bare = num.rstrip('%')
        if num_bare in _LINE_WHITELIST:
            continue
        # Skip percentages — Jerry commonly derives them
        if num.endswith('%'):
            continue
        # Skip very small integers (likely counts / step numbers)
        try:
            if float(num_bare) < 10 and float(num_bare).is_integer():
                continue
        except ValueError:
            pass
        # Check exact + tolerance match
        if num in allowed or num_bare in allowed:
            continue
        if _within_tolerance(num, allowed, tolerance_pct):
            continue
        hallucinated.append(num)

    return {
        'is_valid': len(hallucinated) == 0,
        'cited_numbers': cited_unique,
        'allowed_count': allowed_count,
        'hallucinated_numbers': hallucinated,
        'notes': f'{len(cited_unique)} unique numbers cited, {len(hallucinated)} unmatched',
    }


if __name__ == '__main__':
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass
    # Smoke test — realistic Jerry read + struct
    struct = {
        'player': 'Patrick Mahomes',
        'projection': {'value': 257, 'inputs': {'L5_avg': 289, 'opp_D_note': 'top-5 D rank 4'}},
        'book_line': 275.5,
        'signals': {
            'l5_form': 'L5 pass_yds avg 289 over 5 games',
            'weather': '15mph wind, 42°F',
        },
        'vs_opp_last_3': [231, 268, 249],
    }
    good_read = 'Consider backing UNDER 275.5. Mahomes projects 257 vs the 275.5 line — 6.7% edge. L5 avg 289. Prior 3 vs opp: 231, 268, 249 — never over.'
    bad_read = 'Consider UNDER 275.5. Mahomes projects 257 vs 275.5 — 6.7% edge. Last 3 vs opp: 312, 288, 301 — never over.'
    # ^ bad_read has FABRICATED prior-vs-opp numbers (312, 288, 301 aren't in struct)
    print('=== validator smoke test ===\n')
    print('--- GOOD READ ---')
    r1 = validate(good_read, '', struct)
    print(json.dumps(r1, indent=2, default=str))
    print('\n--- BAD READ (fabricated 312, 288, 301) ---')
    r2 = validate(bad_read, '', struct)
    print(json.dumps(r2, indent=2, default=str))
