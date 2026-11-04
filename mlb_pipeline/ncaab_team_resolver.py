"""Resolve any NCAAB team spelling to its canonical name.

══ WHY THIS EXISTS ══════════════════════════════════════════════════════
line_history stores NCAAB games under a COMPOSED id and the raw Odds API
matchup string:

    game_id  'ncaab_20261103_Milwaukee_USC'
    matchup  'Milwaukee Panthers @ USC Trojans'

ncaab_game_context stores an md5-style hash id and canonical team names:

    game_id  'aba48d8d67ce2a195effdea02236b8f0'
    home_team 'Presbyterian'   away_team 'St. Thomas (MN) Tommies'

The ids can never join, so the bridge has to be (play date + teams). On
2026-11-02 I wrote that bridging them "needs a team-name alias layer" and
that building one by guess was the risk, citing the NCAAF composed game_id
that produced 21 duplicate rows with one home/away INVERTED
(project_ncaaf_five_team_name_mismatch_1021).

That was wrong, and the correction matters: the alias layer already exists.
ncaab_team_aliases has 365 rows, 361 carrying BOTH odds_api_name and
espn_id, plus kenpom_name, bart_name and an alt_names array. It was built
deliberately as "a PERMANENT lookup table -- not runtime fuzzy matching"
(ncaab_enrich_aliases.py). Nothing needed guessing; it needed reading.

MEASURED, 2026-11-02:
    ncaab_game_context    244 distinct team names   241 resolved (98.8%)
    line_history NCAAB    150 distinct team names   150 resolved (100%)
    the three gaps: 'Loyola (Chi) Ramblers', 'UMKC Kangaroos',
                    'Boston Univ. Terriers'  -- all 1 appearance, all fixed
                    by adding alt_names to the TABLE, not by a constant here

And the join it enables, same day, on (ET date, resolved home, resolved
away) over the 78 distinct NCAAB games line_history holds:

    before the game_date repair      1 of 78 matched a ctx row
    after                           75 of 78

The 3 remaining misses are two games ctx does not carry at all and one
where the two feeds disagree about the tip DATE -- not name failures.

══ 57 KEYS IN THE TABLE ARE AMBIGUOUS, AND THAT IS FINE ═════════════════
I first wrote here that the normalizer produced "0 ambiguous keys over all
365 rows". Running --audit falsified that immediately: 57 keys are claimed
by more than one canonical team, every one of them a bare MASCOT, because
ncaab_enrich_aliases deliberately seeds "mascot-only" variants and 365
schools do not have 365 distinct mascots.

    'eagles'   -> 10 teams   'bulldogs' -> 14 teams
    'cougars'  ->  6 teams   'aggies'   ->  5 teams

Those keys resolve to None, not to whichever row the loader saw first.
Without that guard 'Eagles' would silently become Boston College and put a
line on the wrong fixture -- the exact shape of
project_ncaaf_five_team_name_mismatch_1021, where a name-derived key
inverted home and away.

The guard costs nothing on real data: no feed sends a bare mascot. Measured
the same day, 0 of 150 line_history names and 0 of 244 ctx names resolve
through an ambiguous key.

══ THE RULE ══
No fuzzy matching, ever. Every answer comes from a key present in
ncaab_team_aliases. The only transformation is a deterministic normalizer
(case, punctuation, '&'->'and', Saint/State -> st) plus stripping a trailing
MASCOT, which is safe only because the table already stores location-only
forms in alt_names -- so a mascot strip is still a table lookup, not a
guess. A name that does not resolve returns None and the caller skips the
row. It never returns a best guess: a wrong team is a wrong game, and a
wrong game is a wrong line on a published pick.

    python ncaab_team_resolver.py --self-test
    python ncaab_team_resolver.py --audit      # report unresolved live names
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

#: Set once by load_aliases(). normalized key -> canonical_name.
_LOOKUP: dict[str, str] = {}
#: normalized key -> set of canonical names, for keys the table makes
#: ambiguous. Those keys resolve to None rather than to whichever row the
#: loader happened to see first.
_AMBIGUOUS: dict[str, set] = {}
_LOADED = False

#: The alias fields a canonical row can be reached through. alt_names is an
#: array and handled separately.
_NAME_FIELDS = ('canonical_name', 'odds_api_name', 'kenpom_name', 'bart_name')


def normalize(s) -> str:
    """Deterministic key for a team name. No fuzziness, no edit distance.

    'St.' and 'State' collapse to the same token because the four feeds
    disagree only on that spelling ('Georgia St.' / 'Georgia St Panthers' /
    'Georgia State Panthers'). 'Saint' collapses too -- the table's own
    normalizer chain does the same (ncaab_enrich_aliases: "direct ->
    St.<->Saint -> State<->St.").
    """
    s = str(s or '').strip().lower()
    if not s:
        return ''
    s = s.replace('&', ' and ')
    s = re.sub(r'\bsaint\b', 'st', s)
    s = re.sub(r'\bstate\b', 'st', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _add(key: str, canonical: str) -> None:
    k = normalize(key)
    if not k or not canonical:
        return
    if k in _LOOKUP and _LOOKUP[k] != canonical:
        _AMBIGUOUS.setdefault(k, {_LOOKUP[k]}).add(canonical)
        return
    _LOOKUP[k] = canonical


def load_aliases(rows=None) -> int:
    """Build the lookup. Pass `rows` to load from memory (tests); otherwise
    read ncaab_team_aliases.

    A read that returns 0 rows raises. An empty lookup would make every
    resolve() return None, which downstream reads as "no NCAAB games have
    lines" -- a wiring error wearing the costume of a finding
    (feedback_zero_rows_is_a_wiring_error).
    """
    global _LOADED
    _LOOKUP.clear()
    _AMBIGUOUS.clear()
    if rows is None:
        rows = _fetch_alias_rows()
    if not rows:
        raise RuntimeError(
            'ncaab_team_aliases returned 0 rows. Refusing to run with an '
            'empty lookup: every team would fail to resolve and the caller '
            'would read that as "no games have lines".')
    for r in rows:
        canon = r.get('canonical_name')
        if not canon:
            continue
        for f in _NAME_FIELDS:
            _add(r.get(f), canon)
        for alt in (r.get('alt_names') or []):
            _add(alt, canon)
    _LOADED = True
    return len(_LOOKUP)


def _fetch_alias_rows():
    import requests
    env = Path(__file__).with_name('.env')
    if env.exists():
        for line in env.read_text().split('\n'):
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ['SUPABASE_KEY'])
    out, off = [], 0
    while off < 5000:
        r = requests.get(
            f'{base}/rest/v1/ncaab_team_aliases',
            headers={'apikey': key, 'Authorization': 'Bearer ' + key},
            params={'select': 'canonical_name,odds_api_name,kenpom_name,'
                              'bart_name,alt_names',
                    'limit': 1000, 'offset': off},
            timeout=60)
        if r.status_code != 200:
            raise RuntimeError(
                f'ncaab_team_aliases HTTP {r.status_code}: {r.text[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000
    return out


def _suffix_claims_another(dropped: list, canon: str):
    """(key, other_canonical) if the tokens we are about to DROP name a
    different school, else None.

    A trailing mascot claims nothing -- a bare mascot key is either absent
    or _AMBIGUOUS (57 of them are). A trailing LOCATION does claim: dropping
    'little rock' from 'arkansas little rock trojans' leaves 'arkansas',
    which is a real school that is not the one named. Checking every leading
    run of the dropped tokens catches it whatever the mascot is.
    """
    for take in range(1, len(dropped) + 1):
        cand = ' '.join(dropped[:take])
        if cand in _AMBIGUOUS:
            continue          # claimed by several schools -> claims none
        other = _LOOKUP.get(cand)
        if other and other != canon:
            return cand, other
    return None


def resolve(name, *, with_method: bool = False):
    """Canonical name for `name`, or None.

    None means "not in the table". The caller must skip that row, never
    substitute a close match.
    """
    if not _LOADED:
        load_aliases()
    k = normalize(name)
    if not k:
        return (None, 'empty') if with_method else None
    if k in _AMBIGUOUS:
        return (None, 'ambiguous') if with_method else None
    if k in _LOOKUP:
        return (_LOOKUP[k], 'exact') if with_method else _LOOKUP[k]
    # Strip a trailing mascot, one token at a time. Safe ONLY because the
    # table stores location-only forms, so each attempt is still a lookup --
    # AND only while no school's name begins with another school's complete
    # alias. 2026-11-03: three live spellings did exactly that, and this
    # loop answered with the wrong school.
    #
    #   'CSU Fullerton Titans'         -> 'Colorado St.'  ('CSU' = Colo St.)
    #   'Arkansas-Little Rock Trojans' -> 'Arkansas'
    #   'Tenn-Martin Skyhawks'         -> 'Tennessee'
    #
    # Each is a real alias claiming the FIRST tokens of a different school.
    # That is the failure this module exists to prevent ("a wrong team is a
    # wrong game"), reached through the one path that is not a direct lookup.
    parts = k.split()
    for cut in range(1, min(4, len(parts))):
        cand = ' '.join(parts[:-cut])
        if cand in _AMBIGUOUS:
            break
        if cand in _LOOKUP:
            canon = _LOOKUP[cand]
            claim = _suffix_claims_another(parts[-cut:], canon)
            if claim:
                # Refuse rather than guess. None means the caller skips the
                # row, which is the documented contract; a wrong school
                # would be a line on the wrong fixture.
                return ((None, f'refused: suffix {claim[0]!r} names '
                         f'{claim[1]!r}, not {canon!r}') if with_method
                        else None)
            return ((canon, f'mascot-strip({cut})') if with_method else canon)
    return (None, 'unresolved') if with_method else None


def resolve_matchup(matchup):
    """('Away Name @ Home Name') -> (away_canonical, home_canonical).

    (None, None) unless BOTH sides resolve. Half a matchup is not a game,
    and pairing one resolved team with a guess is how a line lands on the
    wrong fixture.
    """
    s = str(matchup or '')
    if ' @ ' not in s:
        return None, None
    away, home = (x.strip() for x in s.split(' @ ', 1))
    ra, rh = resolve(away), resolve(home)
    if not ra or not rh:
        return None, None
    return ra, rh


# ── self-test ─────────────────────────────────────────────────────────────

_FIXTURE = [
    {'canonical_name': 'Georgia St.', 'odds_api_name': 'Georgia State Panthers',
     'kenpom_name': 'Georgia St.', 'bart_name': 'Georgia St.',
     'alt_names': ['Georgia State', 'Panthers', 'GAST']},
    {'canonical_name': 'USC', 'odds_api_name': 'USC Trojans',
     'kenpom_name': 'USC', 'bart_name': 'USC',
     'alt_names': ['Southern California', 'Trojans']},
    {'canonical_name': 'Milwaukee', 'odds_api_name': 'Milwaukee Panthers',
     'kenpom_name': 'Milwaukee', 'bart_name': 'Milwaukee',
     'alt_names': ['Wisconsin-Milwaukee', 'UW-Milwaukee']},
    {'canonical_name': "Saint Mary's", 'odds_api_name': "Saint Mary's Gaels",
     'kenpom_name': "Saint Mary's", 'bart_name': "Saint Mary's",
     'alt_names': ["St. Mary's", 'Gaels']},
    {'canonical_name': 'Texas A&M-CC',
     'odds_api_name': 'Texas A&M-CC Islanders',
     'kenpom_name': 'Texas A&M Corpus Chris', 'bart_name': 'Texas A&M-CC',
     'alt_names': ['Islanders']},
    # 2026-11-03 prefix-collision pair: 'CSU' is Colorado St.'s alias and
    # also the first token of Cal St. Fullerton's feed spelling.
    {'canonical_name': 'Colorado St.', 'odds_api_name': 'Colorado State Rams',
     'kenpom_name': 'Colorado St.', 'bart_name': 'Colorado St.',
     'alt_names': ['CSU', 'Colorado State', 'Rams']},
    {'canonical_name': 'Cal St. Fullerton',
     'odds_api_name': 'Cal State Fullerton Titans',
     'kenpom_name': 'Cal St. Fullerton', 'bart_name': 'Cal St. Fullerton',
     'alt_names': ['CSUF', 'Fullerton', 'Titans']},
]


def _self_test() -> int:
    load_aliases(_FIXTURE)
    cases = [
        # (input, expected canonical)
        ('Georgia St.', 'Georgia St.'),
        ('Georgia State Panthers', 'Georgia St.'),
        ('Georgia St Panthers', 'Georgia St.'),       # the ctx third spelling
        ('USC Trojans', 'USC'),
        ('USC', 'USC'),
        ('Milwaukee Panthers', 'Milwaukee'),
        ("Saint Mary's Gaels", "Saint Mary's"),
        ("St. Mary's", "Saint Mary's"),               # Saint/St. unified
        ('Texas A&M-CC Islanders', 'Texas A&M-CC'),   # & and hyphen
        ('Texas A and M-CC', 'Texas A&M-CC'),
        # prefix collision: must NOT become Colorado St. Before the
        # _suffix_claims_another guard this returned 'Colorado St.'.
        ('CSU Fullerton Titans', None),
        ('CSU Fullerton', None),
        ('CSU', 'Colorado St.'),                      # control, unmoved
        ('Colorado State Rams', 'Colorado St.'),      # control, unmoved
        ('Cal State Fullerton Titans', 'Cal St. Fullerton'),
        # must NOT resolve
        ('Nowhere State Fighting Nobodies', None),
        ('', None),
        (None, None),
        ('   ', None),
    ]
    ok = True
    print('resolve():')
    for raw, want in cases:
        got, how = resolve(raw, with_method=True)
        good = got == want
        ok = ok and good
        print(f'  {"ok  " if good else "FAIL"} {str(raw)!r:36s} -> '
              f'{str(got)!r:18s} ({how}) wanted {str(want)!r}')

    print('\nresolve_matchup():')
    mcases = [
        ('Milwaukee Panthers @ USC Trojans', ('Milwaukee', 'USC')),
        ('Georgia State Panthers @ USC', ('Georgia St.', 'USC')),
        # one side unknown -> BOTH None, never a half match
        ('Nobody State @ USC Trojans', (None, None)),
        ('USC Trojans', (None, None)),          # no separator
        ('', (None, None)),
    ]
    for raw, want in mcases:
        got = resolve_matchup(raw)
        good = got == want
        ok = ok and good
        print(f'  {"ok  " if good else "FAIL"} {str(raw)!r:38s} -> {got} '
              f'wanted {want}')

    # An empty table must RAISE, not quietly resolve nothing.
    print('\nempty table:')
    try:
        load_aliases([])
        print('  FAIL load_aliases([]) returned instead of raising')
        ok = False
    except RuntimeError as e:
        print(f'  ok   raised RuntimeError: {str(e)[:58]}...')

    # Ambiguity must resolve to None, not to a coin flip.
    print('\nambiguous key:')
    load_aliases([
        {'canonical_name': 'Team A', 'alt_names': ['Shared Name']},
        {'canonical_name': 'Team B', 'alt_names': ['Shared Name']},
    ])
    got, how = resolve('Shared Name', with_method=True)
    good = got is None and how == 'ambiguous'
    ok = ok and good
    print(f'  {"ok  " if good else "FAIL"} two canonicals claim one alias '
          f'-> {got!r} ({how})')

    print('\nself-test', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


def _audit() -> int:
    """Report live names that do not resolve. Reads the real tables."""
    import collections
    import requests
    n = load_aliases()
    print(f'lookup keys from ncaab_team_aliases: {n}')
    if _AMBIGUOUS:
        print(f'  !! {len(_AMBIGUOUS)} AMBIGUOUS keys (resolve to None):')
        for k, v in list(_AMBIGUOUS.items())[:10]:
            print(f'     {k!r} claimed by {sorted(v)}')
    else:
        print('  0 ambiguous keys')

    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ['SUPABASE_KEY'])
    hdr = {'apikey': key, 'Authorization': 'Bearer ' + key}

    def page(table, params):
        out, off = [], 0
        while off < 200000:
            r = requests.get(f'{base}/rest/v1/{table}', headers=hdr,
                             params=dict(params, limit=1000, offset=off),
                             timeout=180)
            if r.status_code != 200:
                print(f'  !! {table} HTTP {r.status_code} {r.text[:200]}')
                return out, False
            b = r.json()
            out += b
            if len(b) < 1000:
                return out, True
            off += 1000
        return out, False

    ctx, full = page('ncaab_game_context',
                     {'select': 'home_team,away_team'})
    names = collections.Counter()
    for r in ctx:
        names[r['home_team']] += 1
        names[r['away_team']] += 1
    bad = {k: v for k, v in names.items() if resolve(k) is None}
    print(f'\nncaab_game_context: {len(names)} distinct names, complete={full}'
          f'  UNRESOLVED {len(bad)}')
    for k, v in sorted(bad.items(), key=lambda x: -x[1]):
        print(f'   {v:4d}x  {k!r}')

    lh, lfull = page('line_history',
                     {'select': 'matchup', 'sport': 'eq.NCAAB'})
    mk = collections.Counter()
    for r in lh:
        m = str(r.get('matchup') or '')
        if ' @ ' not in m:
            continue
        a, h = m.split(' @ ', 1)
        mk[a.strip()] += 1
        mk[h.strip()] += 1
    bad2 = {k: v for k, v in mk.items() if resolve(k) is None}
    print(f'\nline_history NCAAB: {len(mk)} distinct names, complete={lfull}'
          f'  UNRESOLVED {len(bad2)}')
    for k, v in sorted(bad2.items(), key=lambda x: -x[1]):
        print(f'   {v:4d}x  {k!r}')
    print('\nFix an unresolved name by adding it to that team\'s alt_names in '
          'ncaab_team_aliases. Do NOT add a constant to this module.')
    return 0 if not (bad or bad2 or _AMBIGUOUS) else 1


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        raise SystemExit(_self_test())
    if '--audit' in sys.argv:
        raise SystemExit(_audit())
    print(__doc__)
    print('  --self-test   offline, no DB')
    print('  --audit       report unresolved live names')
