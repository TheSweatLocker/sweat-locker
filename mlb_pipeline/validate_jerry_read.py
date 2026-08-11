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

    # 2026-08-08: extend whitelist to catch every person-name Jerry might
    # cite from struct. Prior bug — umpire "Tom Hanahan" got flagged as
    # a hallucinated pitcher, then Layer C substituted it with "the
    # opposing starter", producing awkward "(the opposing starter)" prose
    # inside a sentence that was about the umpire.
    #
    # Umpire block (`struct.umpire.name` in real data snapshot).
    ump = struct.get('umpire')
    if isinstance(ump, dict):
        v = ump.get('name')
        if isinstance(v, str) and v.strip():
            n = _ascii_lower(v.strip())
            whitelist.add(n)
            parts = n.split()
            if len(parts) >= 2: whitelist.add(parts[-1])

    # Pitchers nested under `struct.pitchers.{home,away}.name` — some
    # code paths pass the derived struct which has this shape, not the
    # flat home_pitcher/away_pitcher fields.
    pitchers = struct.get('pitchers')
    if isinstance(pitchers, dict):
        for side_key in ('home', 'away'):
            sp = pitchers.get(side_key)
            if isinstance(sp, dict):
                v = sp.get('name')
                if isinstance(v, str) and v.strip():
                    n = _ascii_lower(v.strip())
                    whitelist.add(n)
                    parts = n.split()
                    if len(parts) >= 2: whitelist.add(parts[-1])

    # Batters block — struct.batters.{home,away}[] or struct.lineup.{home,away}[]
    for container_key in ('batters', 'lineup', 'lineups'):
        cont = struct.get(container_key)
        if not isinstance(cont, dict): continue
        for side_key in ('home', 'away'):
            side_list = cont.get(side_key)
            if isinstance(side_list, list):
                for entry in side_list:
                    # Each entry can be a name string or a dict with 'name' field
                    nm = entry if isinstance(entry, str) else (
                        entry.get('name') if isinstance(entry, dict) else None)
                    if not (isinstance(nm, str) and nm.strip()): continue
                    n = _ascii_lower(nm.strip())
                    whitelist.add(n)
                    parts = n.split()
                    if len(parts) >= 2: whitelist.add(parts[-1])

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


def substitute_generic_starter_refs(prose: str, struct: dict, sport: str = 'MLB') -> str:
    """Sport-universal Layer D scrub. Generalizes the MLB "the opposing
    starter" scrub to other sports where Jerry may leak generic role
    references instead of using the real player name.

    Per-sport terminology:
      MLB   → "the opposing starter/pitcher"     → home_pitcher/away_pitcher
      NFL   → "the opposing QB/quarterback"      → home_qb/away_qb (if in struct)
      NCAAF → same as NFL
      UFC   → "the opposing fighter"              → fighter_a/fighter_b

    Falls back to legacy MLB scrub for backward compat.
    """
    if sport == 'MLB' or not sport:
        return substitute_generic_pitcher_refs(prose, struct)
    if sport in ('NFL', 'NCAAF'):
        return _substitute_generic_qb_refs(prose, struct)
    if sport == 'UFC':
        return _substitute_generic_fighter_refs(prose, struct)
    if sport == 'NHL':
        return _substitute_generic_goalie_refs(prose, struct)
    # NCAAB / NBA — basketball has no single "starter" role like a
    # pitcher/QB/goalie, so generic-ref scrubbing doesn't apply cleanly.
    # Team-level references handled by team-name validators elsewhere.
    return prose


def _substitute_generic_qb_refs(prose: str, struct: dict) -> str:
    """NFL/NCAAF: replace 'the opposing QB/quarterback' with actual
    home_qb/away_qb from struct. Same proximity resolution as MLB Layer D."""
    if not prose: return prose
    home_qb = (struct.get('home_qb') or struct.get('home_starter') or '').strip()
    away_qb = (struct.get('away_qb') or struct.get('away_starter') or '').strip()
    if not (home_qb or away_qb):
        return prose

    def last_name(full: str) -> str:
        return full.split()[-1] if full else ''
    home_last = last_name(home_qb); away_last = last_name(away_qb)

    def resolve(match_start, phrase):
        window = prose[max(0, match_start - 180):match_start].lower()
        h_pos = window.rfind(home_last.lower()) if home_last else -1
        a_pos = window.rfind(away_last.lower()) if away_last else -1
        if 'opposing' in phrase.lower():
            if h_pos > a_pos and h_pos >= 0 and away_qb: return away_last
            if a_pos > h_pos and a_pos >= 0 and home_qb: return home_last
            return home_last or away_last or phrase
        return phrase

    def _sub(m): return resolve(m.start(), m.group(0))
    out = re.sub(
        r'\bthe (opposing|home|away) (?:QB|quarterback)\b(?!\s*\()',
        _sub, prose, flags=re.IGNORECASE,
    )
    # Corrupted-prefix variant (matches Layer D pattern 3)
    out = re.sub(
        r'(?<=[A-Za-z])the (opposing|home|away) (?:QB|quarterback)\b(?!\s*\()',
        _sub, out, flags=re.IGNORECASE,
    )
    return out


def _substitute_generic_goalie_refs(prose: str, struct: dict) -> str:
    """NHL: replace 'the opposing goalie/goaltender' with actual starter."""
    if not prose: return prose
    home_g = (struct.get('home_goalie') or struct.get('home_starter') or '').strip()
    away_g = (struct.get('away_goalie') or struct.get('away_starter') or '').strip()
    if not (home_g or away_g): return prose

    def last_name(full: str) -> str:
        return full.split()[-1] if full else ''
    h_last = last_name(home_g); a_last = last_name(away_g)

    def resolve(match_start, phrase):
        window = prose[max(0, match_start - 180):match_start].lower()
        h_pos = window.rfind(h_last.lower()) if h_last else -1
        a_pos = window.rfind(a_last.lower()) if a_last else -1
        if 'opposing' in phrase.lower():
            if h_pos > a_pos and h_pos >= 0 and away_g: return a_last
            if a_pos > h_pos and a_pos >= 0 and home_g: return h_last
            return h_last or a_last or phrase
        return phrase

    def _sub(m): return resolve(m.start(), m.group(0))
    out = re.sub(
        r'\bthe (opposing|home|away) (?:goalie|goaltender)\b(?!\s*\()',
        _sub, prose, flags=re.IGNORECASE,
    )
    return out


def _substitute_generic_fighter_refs(prose: str, struct: dict) -> str:
    """UFC: replace 'the opposing fighter' with fighter_a or fighter_b."""
    if not prose: return prose
    a = (struct.get('fighter_a') or '').strip()
    b = (struct.get('fighter_b') or '').strip()
    if not (a or b): return prose

    def last_name(full: str) -> str:
        return full.split()[-1] if full else ''
    a_last = last_name(a); b_last = last_name(b)

    def resolve(match_start, phrase):
        window = prose[max(0, match_start - 180):match_start].lower()
        a_pos = window.rfind(a_last.lower()) if a_last else -1
        b_pos = window.rfind(b_last.lower()) if b_last else -1
        if a_pos > b_pos and a_pos >= 0 and b: return b_last
        if b_pos > a_pos and b_pos >= 0 and a: return a_last
        return a_last or b_last or phrase

    def _sub(m): return resolve(m.start(), m.group(0))
    out = re.sub(
        r'\bthe opposing fighter\b(?!\s*\()',
        _sub, prose, flags=re.IGNORECASE,
    )
    return out


def substitute_generic_pitcher_refs(prose: str, struct: dict) -> str:
    """Layer D (2026-08-09): mechanical scrub of 'the opposing starter' →
    real pitcher name based on nearby team context.

    Runs unconditionally after retry — belt-and-suspenders for the case
    where the LLM keeps saying 'the opposing starter' despite corrective
    prompt. Was previously only conviction-capped, still leaked the phrase
    into shipped prose (8/15 games on 2026-08-09).

    Heuristic: for each occurrence of `the (opposing|home|away) starter`
    (without a following parenthetical name), look ~180 chars back for a
    team name or possessive ("Cleveland lineup", "White Sox offense",
    "Miami's durability"). If the nearer team is HOME, sub in away
    pitcher (and vice versa). Falls back to `home_p vs away_p` construct
    when context is ambiguous.

    Also patches the specific hallucination pattern where an umpire or
    park factor line contains "(the opposing starter, ...)" — parenthetical
    entity mismatches — by stripping the misplaced clause.
    """
    if not prose:
        return prose
    home_p = (struct.get('home_pitcher') or '').strip() if isinstance(struct, dict) else ''
    away_p = (struct.get('away_pitcher') or '').strip() if isinstance(struct, dict) else ''
    home_t = (struct.get('home_team') or '').strip() if isinstance(struct, dict) else ''
    away_t = (struct.get('away_team') or '').strip() if isinstance(struct, dict) else ''
    if not (home_p or away_p):
        return prose

    def last_name(full: str) -> str:
        return full.split()[-1] if full else ''

    def team_keys(full: str) -> list:
        """Match on last word (Yankees, Dodgers) + city variants."""
        if not full: return []
        parts = full.split()
        keys = [full.lower()]
        if len(parts) > 1:
            keys.append(parts[-1].lower())  # 'Yankees'
            keys.append(' '.join(parts[:-1]).lower())  # 'New York'
        return [k for k in keys if len(k) >= 3]

    home_keys = team_keys(home_t)
    away_keys = team_keys(away_t)

    home_last = last_name(home_p)
    away_last = last_name(away_p)

    def resolve(match_start: int, phrase: str) -> str:
        window = prose[max(0, match_start - 180):match_start].lower()
        # Prefer pitcher-name proximity (most reliable), fall back to team keys.
        # 2026-08-09: if the "lead" pitcher name in the window ISN'T either
        # home_p or away_p, Jerry hallucinated a name — resolve against the
        # OTHER position (whichever wasn't just mentioned by nearest name).
        home_p_pos = window.rfind(home_last.lower()) if home_last else -1
        away_p_pos = window.rfind(away_last.lower()) if away_last else -1
        home_pos = max([home_p_pos] + [window.rfind(k) for k in home_keys], default=-1)
        away_pos = max([away_p_pos] + [window.rfind(k) for k in away_keys], default=-1)
        # Detect hallucinated-pitcher pattern: window has a capitalized surname
        # right before the phrase but it's neither home_p nor away_p.
        # e.g. "Sheehan has been solid... the opposing starter is getting shelled"
        # when actual pitchers are Wrobleski / Rodriguez.
        raw_window = prose[max(0, match_start - 180):match_start]
        m_lead = re.search(r'\b([A-Z][a-z]{3,})\s+(?:has|is|was|allowed|carries|pitches|throws)',
                            raw_window)
        if m_lead:
            lead = m_lead.group(1).lower()
            if lead and lead != home_last.lower() and lead != away_last.lower():
                # Lead name isn't in our real pitchers → Jerry hallucinated it.
                # Since we can't tell which side it stood in for, prefer the
                # pitcher whose stats-context in the phrase (following text)
                # matches: default to home_p (arbitrary but consistent).
                if 'opposing' in phrase.lower() and home_p and away_p:
                    # Prefer the pitcher that appears LATER in the full prose
                    # (Jerry often names the "opposing" pitcher explicitly
                    # further down).
                    tail = prose[match_start:].lower()
                    if home_last.lower() in tail and away_last.lower() not in tail:
                        return home_last
                    if away_last.lower() in tail and home_last.lower() not in tail:
                        return away_last
                    # Both or neither in tail → default to home starter
                    return home_last
        if 'opposing' in phrase.lower():
            # 'the opposing starter' — opposite of the last-mentioned side
            if home_pos > away_pos and home_pos >= 0 and away_p:
                return away_last
            if away_pos > home_pos and away_pos >= 0 and home_p:
                return home_last
            # Neither team/pitcher mentioned in window — pick the pitcher whose
            # name is NOT anywhere in the prose yet (avoids repeating the same
            # name back-to-back).
            if home_p and home_last.lower() not in prose.lower():
                return home_last
            if away_p and away_last.lower() not in prose.lower():
                return away_last
            # Truly ambiguous — name whichever we have.
            return home_last or away_last or phrase
        elif 'home' in phrase.lower():
            return home_last if home_last else phrase
        elif 'away' in phrase.lower():
            return away_last if away_last else phrase
        return phrase

    out = prose

    # Pattern 1: umpire/park lines that got hallucinated with pitcher label
    # ("The umpire, the opposing starter, runs...", "the park (the opposing
    # starter, factor 103)"). Strip the misplaced clause.
    # 1a) Parenthetical: "the park (the opposing starter, factor 103)"
    #     → "the park (factor 103)"  (preserve balanced parens)
    out = re.sub(
        r'\(\s*the opposing starter\s*,\s*',
        '(',
        out,
        flags=re.IGNORECASE,
    )
    # 1b) Comma-clause: "The umpire, the opposing starter, runs a neutral zone"
    #     → "The umpire runs a neutral zone"
    out = re.sub(
        r'(umpire|park|weather|stadium)\s*,\s*the opposing starter\s*,\s*',
        r'\1 ',
        out,
        flags=re.IGNORECASE,
    )

    # Pattern 2: 'the (opposing|home|away) starter' NOT followed by '(Name)'
    def _sub(m):
        return resolve(m.start(), m.group(0))

    out = re.sub(
        r'\bthe (opposing|home|away) starter\b(?!\s*\()',
        _sub,
        out,
        flags=re.IGNORECASE,
    )

    # Pattern 3 (2026-08-10): corrupted-prefix cases where the LLM concatenated
    # a truncated pitcher name with "the opposing starter" (e.g. "Macthe
    # opposing starter" seen on TEX@LAA read). No leading word boundary —
    # match "the (opposing|home|away) starter" anywhere it appears, but ONLY
    # when NOT preceded by whitespace (which pattern 2 already handled).
    out = re.sub(
        r'(?<=[A-Za-z])the (opposing|home|away) starter\b(?!\s*\()',
        _sub,
        out,
        flags=re.IGNORECASE,
    )

    # Pattern 3b (2026-08-11): "opposing starter/pitcher" WITHOUT the leading
    # "the" (e.g. "weak opposing starter in recent breakdown"). Catches
    # adjective-preceded variants that pattern 2 misses because it required
    # "the" as anchor. Same proximity-based resolution.
    def _sub_no_the(m):
        # Replace "opposing starter/pitcher" (no "the") with the correct
        # last name, keeping any preceding adjective (e.g. "weak Whisenhunt")
        return resolve(m.start(), 'the opposing starter')  # reuse resolver
    out = re.sub(
        r'\bopposing (?:starter|pitcher)\b(?!\s*\()',
        _sub_no_the,
        out,
        flags=re.IGNORECASE,
    )

    # Pattern 4 (2026-08-11): "the [Team] starter/pitcher" references. The LLM
    # sometimes writes "the Giants starter, Carson Whisenhunt" — literally
    # factual but violates feedback_never_generic_pitcher_ref_809 (never ship
    # generic starter refs). Match team names from home/away context and
    # substitute the corresponding pitcher's last name.
    if home_t and away_t:
        # Build map: team-key → pitcher last name
        team_to_pitcher = {}
        for t, p_last in ((home_t, home_last), (away_t, away_last)):
            for key in team_keys(t):
                if p_last and len(key) >= 3:
                    team_to_pitcher[key.lower()] = p_last
        def _sub_team(m):
            team_word = m.group(1).lower()
            return team_to_pitcher.get(team_word, m.group(0))
        # Match "the [Team-word] starter/pitcher" where Team-word is one of
        # the two teams' last-word or full-name keys. Skip if followed by
        # parenthetical (already qualified).
        team_pattern = '|'.join(sorted(set(k for k in team_to_pitcher
                                            if len(k) >= 3), key=len, reverse=True))
        if team_pattern:
            out = re.sub(
                rf'\bthe ({team_pattern}) (?:starter|starting pitcher|pitcher)\b(?!\s*\()',
                _sub_team, out, flags=re.IGNORECASE,
            )

    # Pattern 4 (2026-08-11): TBD-starter sentence scrub. When a starter is
    # genuinely unconfirmed (home_pitcher IS null), Jerry falls back to
    # phrases like "the Nationals' starter remains TBD" / "the Washington
    # starter is not yet confirmed" / "the starting pitcher remains TBD".
    # These VIOLATE feedback_never_generic_pitcher_ref_809 which requires
    # NEVER shipping generic starter references. We can't substitute a name
    # (there isn't one) — instead strip the entire sentence and let the rest
    # of the read carry the pick. If Jerry's thesis leans on the confirmed
    # starter's edge (typical case), the pick still stands.
    #
    # Detected patterns:
    #   "The [Team]'s starter remains TBD."
    #   "The [Team] starter is not yet confirmed."
    #   "[Team]'s starter has yet to be announced."
    #   "The starting pitcher remains TBD."
    #   "The opposing starter is TBD."
    tbd_sentence_patterns = [
        # Team-scoped: "The Nationals' starter remains TBD."
        r'(?:^|\s)[Tt]he\s+[A-Z][A-Za-z]+(?:\'s|s\')?\s+(?:starter|starting pitcher)\s+'
        r'(?:remains|is)\s+(?:TBD|not yet (?:confirmed|announced|set)|unconfirmed|unknown)\.?',
        # Team-scoped with 'has yet to be': "Nationals' starter has yet to be named."
        r'(?:^|\s)(?:[Tt]he\s+)?[A-Z][A-Za-z]+(?:\'s|s\')?\s+(?:starter|starting pitcher)\s+'
        r'has yet to be\s+(?:named|confirmed|announced)\.?',
        # Generic: "The starting pitcher remains TBD."
        r'(?:^|\s)[Tt]he\s+(?:starting pitcher|opposing starter|home starter|away starter)\s+'
        r'(?:remains|is)\s+(?:TBD|not yet (?:confirmed|announced|set)|unconfirmed|unknown)\.?',
    ]
    for pat in tbd_sentence_patterns:
        out = re.sub(pat, '', out)
    # Clean up any resulting double-spaces
    out = re.sub(r'  +', ' ', out).strip()
    return out


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


def validate_style_rules(short_read: str, long_read: str, struct: dict) -> dict:
    """Catch the class of "sounds dumb" errors user flagged on 2026-08-08:
      - generic pitcher refs ("the opposing starter", "Red Sox starter")
        that leak past the pitcher-name guard
      - hitter L7 hallucinations ("hitting in 6-of-7 last 7 at-bats")
      - simulator vs market gap > 15pp (96% at -250 = 71% implied)
      - "13 relievers in the last three days" (bullpen unit muddle)
      - post-bet conditionals ("if X, revisit")

    Returns:
      {
        'valid': bool,
        'issues': [{rule, snippet, suggestion}, ...],
      }
    """
    combined = ((short_read or '') + '\n' + (long_read or '')).strip()
    if not combined:
        return {'valid': True, 'issues': []}

    issues = []

    # Rule 1: generic pitcher reference in prose (post-substitution) —
    # OK if the actual pitcher name literally is "(TBD)"
    home_p = (struct.get('home_pitcher') or '').strip() if isinstance(struct, dict) else ''
    away_p = (struct.get('away_pitcher') or '').strip() if isinstance(struct, dict) else ''
    tbd = (not home_p or home_p == '(TBD)') and (not away_p or away_p == '(TBD)')
    if not tbd:
        for pat in (r'\bthe opposing starter\b',
                    r'\bthe home starter\b(?! \()',
                    r'\bthe away starter\b(?! \()',
                    r"\b(?:Red Sox|Yankees|Cubs|Mets|Dodgers|Giants|Astros|Nationals|Braves|Phillies|Angels|Athletics|Rockies|Marlins|Padres|Cardinals|Twins|Guardians|Rangers|Rays|Blue Jays|Orioles|Reds|Brewers|Diamondbacks|Pirates|Mariners|Tigers|Royals) starter\b"):
            m = re.search(pat, combined, re.IGNORECASE)
            if m:
                issues.append({
                    'rule': 'generic_pitcher_ref',
                    'snippet': combined[max(0,m.start()-20):m.end()+20],
                    'suggestion': f'Use pitcher name by real name — {home_p or "(home starter TBD)"} / {away_p or "(away starter TBD)"}',
                })

    # Rule 2: hitter L7 at-bats phrasing (physically impossible sustained)
    m = re.search(r'\b\d+[-–—]of[-–—]\d+ (?:last|of the last) \d+ (?:at[- ]bats|ABs|plate appearances)\b',
                  combined, re.IGNORECASE)
    if m:
        issues.append({
            'rule': 'hitter_l7_ab_fabrication',
            'snippet': combined[max(0,m.start()-20):m.end()+20],
            'suggestion': 'Use team-level offense stats or cite prop struct rows — never per-hitter AB rates',
        })
    m2 = re.search(r'\bhitting in \d+[-–—]\d+ (?:of )?(?:their|his) last \d+ (?:at[- ]bats|ABs)\b',
                   combined, re.IGNORECASE)
    if m2:
        issues.append({
            'rule': 'hitter_l7_ab_fabrication',
            'snippet': combined[max(0,m2.start()-20):m2.end()+20],
            'suggestion': 'Fabricated per-hitter AB stat — a sustained .857+ BA is impossible',
        })

    # Rule 3: simulator vs market gap > 15pp.
    # Apply the FAVORITE'S implied cap (more negative ML). Any sim claim
    # naturally refers to the winning side, so we check against the
    # highest reasonable implied win rate.
    market = struct.get('market') if isinstance(struct, dict) else None
    if isinstance(market, dict):
        implieds = []
        for ml_key in ('home_ml', 'away_ml'):
            ml = market.get(ml_key)
            if ml is None: continue
            try:
                ml_f = float(ml)
                imp = 100.0 / (ml_f + 100.0) if ml_f > 0 else -ml_f / (-ml_f + 100.0)
                implieds.append(imp)
            except (TypeError, ValueError): continue
        if implieds:
            fav_implied = max(implieds)   # the favorite's implied win rate
            cap_pct = round((fav_implied + 0.15) * 100)
            for m in re.finditer(r'simulator (?:sees|gives|runs|has) [^.]*?(\d{2,3})\s*%', combined, re.IGNORECASE):
                pct = int(m.group(1))
                if pct > cap_pct + 2:  # 2pp forgiveness for rounding
                    issues.append({
                        'rule': 'sim_market_gap_over_cap',
                        'snippet': combined[max(0,m.start()-15):m.end()+15],
                        'suggestion': f'Simulator claim {pct}% exceeds favorite implied+15pp cap ({cap_pct}%). Cap the reported number.',
                    })
                    break  # one flag per read

    # Rule 4: "N relievers" without unit
    m = re.search(r'\b(\d{2,3}) reliever(?:s)?(?! (?:appearance|IP|innings|pitches|outings))\b',
                  combined, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if n >= 10:  # 10+ implies unit muddle (teams carry ~8 relievers)
            issues.append({
                'rule': 'bullpen_unit_missing',
                'snippet': combined[max(0,m.start()-15):m.end()+15],
                'suggestion': f'"{n} relievers" needs a unit — use "{n} reliever appearances", "{n} bullpen IP", or "{n} outings".',
            })

    # Rule NEW (2026-08-08 evening): SIMULATOR-vs-PICK DIRECTION MISMATCH.
    # Marlins/Angels caught by user: Jerry cited "simulator expects 10.9 runs"
    # then picked UNDER 7.5. Reader sees pick contradicting its own cited
    # number → catastrophic credibility loss. If prose cites a run-total
    # number that clearly implies OVER (X > line + 1.5) and pick is UNDER,
    # flag. Same for the reverse.
    call_market = (struct.get('call_market') or '').lower() if isinstance(struct, dict) else ''
    call_side = (struct.get('call_side') or '').upper() if isinstance(struct, dict) else ''
    call_line = struct.get('call_line') if isinstance(struct, dict) else None
    if call_market == 'total' and call_side in ('OVER', 'UNDER') and call_line is not None:
        try:
            line_f = float(call_line)
        except (TypeError, ValueError):
            line_f = None
        if line_f is not None:
            # Match "simulator (expects|sees|projects|has|runs|is|gets) ... N runs"
            for m in re.finditer(
                r'(?:simulator|sim|our model|the model)\s+(?:expects|sees|projects|has|projects|runs|is at|builds to|gives|puts).{0,30}?(\d{1,2}(?:\.\d)?)\s*(?:total\s+)?runs?',
                combined, re.IGNORECASE):
                cited = float(m.group(1))
                # OVER pick but cited number under line-1.5 → mismatch
                if call_side == 'OVER' and cited < line_f - 1.5:
                    issues.append({
                        'rule': 'sim_pick_direction_mismatch',
                        'snippet': combined[max(0,m.start()-25):m.end()+15],
                        'suggestion': f'Cited {cited} runs but pick is OVER {line_f} — cited number supports UNDER. Use jerry.pred_total.',
                    })
                    break
                if call_side == 'UNDER' and cited > line_f + 1.5:
                    issues.append({
                        'rule': 'sim_pick_direction_mismatch',
                        'snippet': combined[max(0,m.start()-25):m.end()+15],
                        'suggestion': f'Cited {cited} runs but pick is UNDER {line_f} — cited number supports OVER. Use jerry.pred_total or MC mean, NOT v4 raw.',
                    })
                    break

    # Rule 5: post-bet conditional ("if X, revisit/reconsider")
    m = re.search(r'\bif [^.,]{5,100}(?:revisit|reconsider|reevaluate|walk (?:it |this )?back|change (?:the |your |our )?take)\b',
                  combined, re.IGNORECASE)
    if m:
        issues.append({
            'rule': 'post_bet_conditional',
            'snippet': combined[max(0,m.start()-15):m.end()+15],
            'suggestion': 'Move risks BEFORE the take — you cannot "revisit" a placed bet',
        })

    return {'valid': len(issues) == 0, 'issues': issues}


def build_corrective_prompt(original_prompt: str, hallucination_report: dict,
                             name_report: dict, style_report: dict | None = None) -> str:
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
    if style_report and style_report.get('issues'):
        style_msgs = []
        for it in style_report['issues'][:5]:
            style_msgs.append(f'- rule={it["rule"]}: "{it["snippet"]}" → {it["suggestion"]}')
        issues.append(
            "Your prose violated these style rules that ship-block credibility:\n"
            + '\n'.join(style_msgs)
            + '\nRegenerate strictly following the rules stated in the prompt (pitcher-name, hitter-AB, sim-market-cap, bullpen-unit, no post-bet conditionals).'
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
