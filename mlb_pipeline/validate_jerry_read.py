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


def _ascii_lower(s: str) -> str:
    """Normalize accents + case for whitelist comparison ('Sánchez' → 'sanchez')."""
    if not isinstance(s, str): return ''
    import unicodedata
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii').lower()


def validate_pitcher_names(prose: str, struct: dict) -> dict:
    """Detect hallucinated pitcher names in Jerry's prose (2026-08-06).

    User caught this on TOR@CHC: Jerry wrote "David an analyst" as a pitcher
    name (his actual write was probably some hallucinated name that the brand
    sanitizer scrubbed to 'an analyst'). More broadly, Jerry sometimes uses
    names from externals[] as if they were players on the game.

    Rule: any capitalized 2-word sequence in prose that LOOKS like a person
    name (Firstname Lastname pattern) must appear in the whitelist:
      - struct.home_pitcher / struct.away_pitcher
      - struct.home_lineup / struct.away_lineup (batter names)
      - Common baseball figures (managers, umpires) — future work

    Returns:
      {
        'valid': bool,
        'suspects': [list of unrecognized names],
        'whitelist_size': int,
      }
    """
    if not prose or not isinstance(struct, dict):
        return {'valid': True, 'suspects': [], 'whitelist_size': 0}

    whitelist = set()
    # Pitcher names — normalize accents so 'Sánchez' matches 'Sanchez' in prose
    for key in ('home_pitcher', 'away_pitcher'):
        v = struct.get(key)
        if isinstance(v, str) and v.strip():
            n = _ascii_lower(v.strip())
            whitelist.add(n)
            parts = n.split()
            if len(parts) >= 2:
                whitelist.add(parts[-1])

    # Batter names from lineup (comma-sep string or list)
    for key in ('home_lineup', 'away_lineup'):
        v = struct.get(key)
        names_iter = []
        if isinstance(v, str) and v.strip():
            names_iter = v.split(',')
        elif isinstance(v, list):
            names_iter = v
        for name in names_iter:
            if not isinstance(name, str) or not name.strip(): continue
            n = _ascii_lower(name)
            whitelist.add(n)
            parts = n.split()
            if len(parts) >= 2:
                whitelist.add(parts[-1])

    # Team names (Jerry can reference teams)
    for key in ('home_team', 'away_team'):
        v = struct.get(key)
        if isinstance(v, str) and v.strip():
            for word in v.split():
                whitelist.add(_ascii_lower(word))

    # Firstname Lastname pattern. Removed `.` from word character class —
    # was catching "ERA. He'll" because "ERA." was treated as a word token.
    # Now dots are only allowed via explicit middle-name particles
    # (St., Jr.). Negative lookbehind on `. ` prevents sentence-boundary
    # matches ("...ERA. He'll..." no longer matches).
    NAME_CHAR = r"[a-zA-Z'ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜÝàáâãäåæçèéêëìíîïñòóôõöøùúûüý-]"
    name_re = re.compile(
        r"(?<!\. )(?<!\.\n)(?<![A-Z])"
        rf"([A-Z]{NAME_CHAR}{{2,}}"
        r"(?: (?:de|van|von|le|la|St\.|Jr\.|III|II))?"
        rf" [A-Z]{NAME_CHAR}{{2,}})"
        r"(?![a-zA-Z])"
    )
    # First-word stopwords: if candidate starts with these, it's not a name.
    # Covers sentence-start verbs, prepositions, article-team combos.
    _FIRST_WORD_STOP = {
        'take', 'back', 'fade', 'lean', 'consider', 'against', 'facing', 'versus', 'vs',
        'the', 'a', 'an', 'his', 'her', 'their', 'our', 'my', 'this', 'that',
        'if', 'when', 'while', 'unless', 'though', 'although', 'because', 'since',
        'over', 'under', 'above', 'below', 'through', 'during', 'after', 'before',
        'and', 'but', 'or', 'so', 'yet', 'nor',
        'sharp', 'public', 'monte', 'model', 'models', 'simulator', 'panel',
        'in', 'of', 'at', 'on', 'for', 'to', 'from', 'with', 'without',
        'ml', 'rl', 'total', 'over/under',
        # City/team-prefix false positives:
        'los', 'san', 'new', 'chicago', 'washington', 'baltimore', 'boston',
        'kansas', 'philadelphia', 'seattle', 'toronto', 'cincinnati', 'detroit',
        'minnesota', 'houston', 'oakland', 'pittsburgh', 'milwaukee', 'colorado',
        'arizona', 'atlanta', 'miami', 'tampa', 'texas',
        # Common English words Jerry uses as pseudo-headers ("Money Flow",
        # "External Context", "Historical Pattern"). Common enough that
        # if either part is one of these, we're not looking at a person name.
        'money', 'external', 'internal', 'historical', 'what', 'why', 'how',
        'data', 'signal', 'signals', 'context', 'pattern', 'patterns',
        'flow', 'reading', 'analysis', 'summary', 'result', 'results',
        'total', 'totals', 'runs', 'bullpen', 'starter', 'lineup', 'weather',
        'trend', 'trends', 'note', 'notes', 'point', 'points', 'stat', 'stats',
        'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh',
        'last', 'next', 'previous', 'recent', 'career', 'season', 'year',
    }
    _LAST_WORD_STOP = {
        # team-name endings
        'sox', 'jays', 'cubs', 'mets', 'reds', 'nationals', 'phillies', 'yankees',
        'orioles', 'rays', 'guardians', 'rangers', 'astros', 'angels', 'padres',
        'giants', 'brewers', 'pirates', 'braves', 'marlins', 'twins', 'royals',
        'mariners', 'tigers', 'diamondbacks', 'rockies', 'cardinals', 'dodgers',
        'athletics',
        # market/prop terms
        'ml', 'rl', 'over', 'under', 'era', 'whip', 'xera', 'strikeout', 'strikeouts',
        'walk', 'walks', 'hit', 'hits', 'inning', 'innings',
        # venue-word suffixes ("Wrigley Field", "Chase Field", "American Ball", etc.)
        'field', 'park', 'stadium', 'yards', 'coliseum', 'center', 'arena',
        'ballpark', 'ball', 'way', 'grounds',
        # Common English words that appear as pseudo-header second words
        'flow', 'context', 'signal', 'signals', 'pattern', 'patterns',
        'reading', 'analysis', 'summary', 'result', 'results', 'note', 'notes',
        'data', 'runs', 'total', 'totals', 'bullpen', 'starter', 'lineup',
        'weather', 'trend', 'trends',
    }

    suspects = []
    seen = set()
    for m in name_re.finditer(prose):
        candidate = m.group(1).strip()
        # Ascii-normalize for accent-insensitive whitelist compare
        lc = _ascii_lower(candidate)
        if lc in seen: continue
        seen.add(lc)
        parts = lc.split()
        # Possessive prefix filter — "Miami's Janson", "Mets' Nolan",
        # "Boston's Suarez" are attribution phrases, not hallucinated pairs.
        if parts[0].endswith("'s") or parts[0].endswith("'"): continue
        # First-word filter (verbs, prepositions, city prefixes)
        if parts[0] in _FIRST_WORD_STOP: continue
        # Last-word filter (team names, market terms)
        if parts[-1] in _LAST_WORD_STOP: continue
        # Exact whitelist match
        if lc in whitelist: continue
        # Last-name-only match (Jerry might say "Suarez" not "Ranger Suarez")
        if len(parts) >= 2 and parts[-1] in whitelist: continue
        # Sanitizer replaces brand names with "an analyst" — clear hallucination signal
        if 'an analyst' in lc:
            suspects.append(f'{candidate} (brand-scrubbed hallucination)')
            continue
        suspects.append(candidate)

    return {
        'valid': len(suspects) == 0,
        'suspects': suspects[:10],  # cap for readability
        'whitelist_size': len(whitelist),
    }


def substitute_hallucinated_names(prose: str, struct: dict, suspects: list) -> str:
    """Layer C fallback: when retry still leaves hallucinated names in prose,
    substitute them with generic 'home/away starter' phrasing rather than
    shipping the bad text.

    Not perfect — reader gets slightly awkward prose ("the home starter faces
    the away starter") but it's honest instead of shipping "David an analyst
    gets tagged early." Structural credibility > prose polish.
    """
    if not prose or not suspects:
        return prose
    out = prose
    home_p = struct.get('home_pitcher', '') if isinstance(struct, dict) else ''
    away_p = struct.get('away_pitcher', '') if isinstance(struct, dict) else ''
    for suspect in suspects:
        # Strip parenthetical note (from validate_pitcher_names)
        base = suspect.split(' (')[0]
        # Try to guess if this was standing in for home or away — pick whichever
        # is shorter/absent in the prose already
        if home_p and home_p.lower() not in out.lower():
            replacement = f'the home starter ({home_p})'
        elif away_p and away_p.lower() not in out.lower():
            replacement = f'the away starter ({away_p})'
        else:
            replacement = 'the opposing starter'
        out = re.sub(re.escape(base), replacement, out, count=1)
    return out


def build_corrective_prompt(original_prompt: str, hallucination_report: dict,
                             name_report: dict) -> str:
    """Build the corrective retry prompt when hallucinations are detected.

    Layer A of the hallucination-guard shipped 2026-08-06.
    """
    issues = []
    if hallucination_report.get('hallucinated_numbers'):
        nums = hallucination_report['hallucinated_numbers'][:5]
        issues.append(
            f"You cited these numbers that DO NOT appear in the input struct: {', '.join(nums)}.\n"
            "Regenerate using ONLY numbers verbatim from the struct. Do not invent, estimate, or derive values."
        )
    if name_report.get('suspects'):
        suspects = name_report['suspects'][:3]
        issues.append(
            f"You referenced these names that are NOT starters/players in this game: {', '.join(suspects)}.\n"
            "Pitchers are struct.home_pitcher and struct.away_pitcher — use those exact names or say 'the home/away starter'."
        )
    if not issues:
        return original_prompt

    corrective = original_prompt + "\n\n=== REGENERATE: previous output had errors ===\n"
    corrective += "\n\n".join(issues)
    corrective += "\n\nProduce the output again in the same format, this time strictly using only struct data."
    return corrective


def validate_direction(prop: dict, call_verdict: str, call_direction: str | None) -> dict:
    """Reject BACK calls whose direction contradicts the projection.

    Catches the "Painter BB Under @ -135 with projection 2.10 BB" class of
    Jerry logic errors: LLM cites market-implied vs refit prob gap to
    justify BACK, but the model's own projected value is on the OPPOSITE
    side of the line.

    Returns:
        {
          'contradicts': bool,
          'edge_pct': float | None,   # (projection - line) / line
          'reason': str,
        }

    A positive edge_pct means projection is ABOVE line (favors OVER).
    A negative edge_pct means projection is BELOW line (favors UNDER).
    BACK on OVER with edge_pct <= -0.05  → contradicts (projection says UNDER)
    BACK on UNDER with edge_pct >= +0.05 → contradicts (projection says OVER)
    """
    if not call_verdict or call_verdict.upper() != 'BACK':
        return {'contradicts': False, 'edge_pct': None, 'reason': 'not_a_back'}

    signals = prop.get('signals') or {}
    if not isinstance(signals, dict):
        return {'contradicts': False, 'edge_pct': None, 'reason': 'no_signals'}

    # Prefer explicit _edge_pct if present (from sweep_prop_coverage)
    edge = signals.get('_edge_pct')
    if edge is None:
        # Fall back: parse "Projected X vs line Y" from signals['projection']
        proj_str = signals.get('projection') or ''
        m = re.search(r'([-+]?\d+(?:\.\d+)?)\s*vs\s*line\s*([-+]?\d+(?:\.\d+)?)', proj_str)
        if m:
            try:
                proj_val = float(m.group(1))
                line_val = float(m.group(2))
                if line_val != 0:
                    edge = (proj_val - line_val) / line_val
            except (ValueError, ZeroDivisionError):
                pass
    if edge is None:
        return {'contradicts': False, 'edge_pct': None, 'reason': 'no_projection_to_check'}

    direction = (call_direction or prop.get('direction') or '').lower()
    if direction not in ('over', 'under'):
        return {'contradicts': False, 'edge_pct': edge, 'reason': 'unknown_direction'}

    # 5% tolerance — small edge in the wrong direction is noise, not contradiction
    if direction == 'over' and edge <= -0.05:
        return {'contradicts': True, 'edge_pct': edge,
                'reason': f'BACK_over_but_projection_edge_{edge*100:+.1f}pct_favors_under'}
    if direction == 'under' and edge >= 0.05:
        return {'contradicts': True, 'edge_pct': edge,
                'reason': f'BACK_under_but_projection_edge_{edge*100:+.1f}pct_favors_over'}
    return {'contradicts': False, 'edge_pct': edge, 'reason': 'projection_aligned'}


def validate_line_movement(call_direction: str | None, call_line: float | None,
                           current_line: float | None,
                           min_delta: float = 0.5) -> dict:
    """Flag totals/spread calls where the line has moved AGAINST the thesis.

    Catches: Jerry leans UNDER 8.0 (LAA@BAL) but total drifts 8.0 → 9.5.
    Market has already re-priced in the direction we're fading, meaning
    our edge (if it existed) is now smaller or gone. Same for OVER at
    line X with current < X - min_delta.

    Returns:
        {
          'contradicts': bool,
          'movement': float | None,   # (current - call_line)
          'reason': str,
        }

    Only fires for over/under. Spread contradictions require a
    signed-direction convention (home fav vs dog) that varies by game
    — handle in the caller.
    """
    if call_direction is None or call_line is None or current_line is None:
        return {'contradicts': False, 'movement': None, 'reason': 'insufficient_data'}
    try:
        cl = float(call_line)
        cur = float(current_line)
    except (TypeError, ValueError):
        return {'contradicts': False, 'movement': None, 'reason': 'non_numeric_line'}

    movement = cur - cl
    d = (call_direction or '').lower()
    if d not in ('over', 'under'):
        return {'contradicts': False, 'movement': movement, 'reason': 'non_directional_call'}

    if d == 'under' and movement >= min_delta:
        return {'contradicts': True, 'movement': movement,
                'reason': f'call_under_{cl}_but_line_moved_UP_to_{cur}_(+{movement:.1f})'}
    if d == 'over' and movement <= -min_delta:
        return {'contradicts': True, 'movement': movement,
                'reason': f'call_over_{cl}_but_line_moved_DOWN_to_{cur}_({movement:+.1f})'}
    return {'contradicts': False, 'movement': movement, 'reason': 'movement_aligned_or_neutral'}


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

    # --- validate_direction smoke tests (2026-08-05) ---
    print('\n=== validate_direction smoke tests ===')
    tests = [
        # (name, prop, verdict, direction, expected_contradicts)
        ('Painter BB Under with proj OVER (should flag)',
         {'signals': {'_edge_pct': 0.40, 'projection': 'Projected bb 2.10 vs line 1.5 · edge +40.0%'},
          'direction': 'under'}, 'BACK', 'under', True),
        ('Kremer ER Over with proj OVER (aligned)',
         {'signals': {'_edge_pct': 0.48, 'projection': 'Projected er 3.7 vs line 2.5 · edge +48.0%'},
          'direction': 'over'}, 'BACK', 'over', False),
        ('Whisenhunt outs_under with proj UNDER (aligned)',
         {'signals': {'_edge_pct': -0.20, 'projection': 'Projected outs 12.4 vs line 15.5 · edge -20.0%'},
          'direction': 'under'}, 'BACK', 'under', False),
        ('BACK with tiny wrong-direction edge (noise, no flag)',
         {'signals': {'_edge_pct': -0.02, 'projection': 'Projected ks 5.4 vs line 5.5'},
          'direction': 'over'}, 'BACK', 'over', False),
        ('PASS never flags',
         {'signals': {'_edge_pct': 0.40}, 'direction': 'under'}, 'PASS', 'under', False),
        ('Parse-only fallback when no _edge_pct',
         {'signals': {'projection': 'Projected bb 2.10 vs line 1.5 · edge +40.0%'},
          'direction': 'under'}, 'BACK', 'under', True),
    ]
    for name, prop, verdict, direction, expected in tests:
        r = validate_direction(prop, verdict, direction)
        ok = '✓' if r['contradicts'] == expected else '✗ FAIL'
        print(f'  {ok} {name}: contradicts={r["contradicts"]} reason={r["reason"]}')

    # --- validate_line_movement smoke tests (2026-08-05) ---
    print('\n=== validate_line_movement smoke tests ===')
    lm_tests = [
        # (name, direction, call_line, current, expected_contradicts)
        ('LAA@BAL UNDER 8.0 with current 9.5 (should flag)', 'under', 8.0, 9.5, True),
        ('CWS@BOS UNDER 8.0 with current 9.0 (should flag)', 'under', 8.0, 9.0, True),
        ('NYM@CLE UNDER 7.5 with current 7.5 (aligned)', 'under', 7.5, 7.5, False),
        ('NYM@CLE UNDER 8.5 with current 7.5 (line moved WITH us — aligned)', 'under', 8.5, 7.5, False),
        ('OVER 8.5 with current 8.0 (line drifted DOWN, flag)', 'over', 8.5, 8.0, True),
        ('OVER 8.5 with current 9.5 (line moved WITH us — aligned)', 'over', 8.5, 9.5, False),
        ('Missing current line — no flag', 'under', 8.0, None, False),
    ]
    for name, d, cl, cur, expected in lm_tests:
        r = validate_line_movement(d, cl, cur)
        ok = '✓' if r['contradicts'] == expected else '✗ FAIL'
        print(f'  {ok} {name}: contradicts={r["contradicts"]} reason={r["reason"]}')
