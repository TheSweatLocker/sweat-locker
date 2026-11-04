"""Close the NCAAB alias gaps where a PREFIX of one school's name is another
school's complete alias.

== WHY ====================================================================
ncaab_team_resolver.resolve() falls back to stripping trailing tokens, each
attempt still a table lookup. That is safe only while no school's name
begins with another school's full alias. Three live feed spellings broke
that, and the resolver answered with the WRONG SCHOOL -- silently, in the
module whose own docstring says "a wrong team is a wrong game, and a wrong
game is a wrong line on a published pick":

    'CSU Fullerton Titans'         -> 'Colorado St.'   ('CSU' = Colorado St.)
    'CSU Fullerton'                -> 'Colorado St.'
    'Arkansas-Little Rock Trojans' -> 'Arkansas'       (Little Rock is NOT
                                                        Arkansas)
    'Tenn-Martin Skyhawks'         -> 'Tennessee'      (UT Martin is NOT
                                                        Tennessee)

CSUN was immune only because 'CSU Northridge' already sat in its alt_names
-- the Fullerton and Bakersfield rows just never got theirs.

'49ers' is a different shape: it is registered to Charlotte alone, so the
new guard in ncaab_team_resolver (refuse a strip whose dropped suffix names
another school) wrongly refuses 'Long Beach St 49ers'. Long Beach State's
teams are also the 49ers, so registering it on BOTH rows makes the key
AMBIGUOUS, which is the table's own mechanism for "this mascot claims
nothing" -- 57 keys already resolve to None that way. That is the correct
record, not a workaround.

Queens and Southern Indiana carry NO odds_api_name and NO alt_names at all,
so their feed spellings are added here too -- they resolve today only via
the token strip this is making stricter.

Fix path is the one ncaab_team_resolver documents: alt_names in the TABLE,
never a constant in the module. Readback-verified, then every mapping is
re-asserted through a freshly loaded resolver.
"""
import json
import os
import sys
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv

#: canonical_name -> alt_names to add
ADDS = {
    'Cal St. Fullerton':   ['CSU Fullerton', 'CSU Fullerton Titans'],
    'Cal St. Bakersfield': ['CSU Bakersfield', 'CSU Bakersfield Roadrunners'],
    'Little Rock':         ['Arkansas-Little Rock', 'Arkansas Little Rock',
                            'Arkansas-Little Rock Trojans'],
    'Tennessee Martin':    ['Tenn-Martin', 'Tenn-Martin Skyhawks',
                            'Tennessee-Martin'],
    # shared mascot -> deliberately makes '49ers' ambiguous (Charlotte too)
    'Long Beach St.':      ['49ers', 'Long Beach St 49ers',
                            'Long Beach State 49ers'],
    'Queens':              ['Queens University', 'Queens University Royals',
                            'Royals'],
    'Southern Indiana':    ['Southern Indiana Screaming Eagles',
                            'Screaming Eagles'],
    'Central Connecticut': ['Central Connecticut St',
                            'Central Connecticut St Blue Devils'],
    'Southeast Missouri':  ['SE Missouri St', 'SE Missouri St Redhawks'],
}

#: What must be true afterwards. The wrong-school cases, plus controls that
#: must NOT move.
EXPECT = [
    ('CSU Fullerton Titans',          'Cal St. Fullerton'),
    ('CSU Fullerton',                 'Cal St. Fullerton'),
    ('CSU Bakersfield',               'Cal St. Bakersfield'),
    ('Arkansas-Little Rock Trojans',  'Little Rock'),
    ('Arkansas-Little Rock',          'Little Rock'),
    ('Tenn-Martin Skyhawks',          'Tennessee Martin'),
    ('Queens University Royals',      'Queens'),
    ('Southern Indiana Screaming Eagles', 'Southern Indiana'),
    ('SE Missouri St Redhawks',       'Southeast Missouri'),
    ('Long Beach St 49ers',           'Long Beach St.'),
    # controls
    ('CSU',                           'Colorado St.'),
    ('Colorado State Rams',           'Colorado St.'),
    ('CSU Northridge Matadors',       'CSUN'),
    ('Arkansas Razorbacks',           'Arkansas'),
    ('Tennessee Volunteers',          'Tennessee'),
    ('UT Martin Skyhawks',            'Tennessee Martin'),
    ('Charlotte 49ers',               'Charlotte'),
    ('Arkansas-Pine Bluff Golden Lions', 'Arkansas Pine Bluff'),
    # a bare shared mascot must still claim nothing
    ('49ers',                         None),
]


def main() -> int:
    rc = 0
    for canon, adds in ADDS.items():
        r = requests.get(f'{SB}/rest/v1/ncaab_team_aliases', headers=H,
                         timeout=30,
                         params={'select': 'canonical_name,alt_names',
                                 'canonical_name': f'eq.{canon}'})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            print(f'!! no ncaab_team_aliases row for {canon!r} -- skipping '
                  f'rather than creating one')
            rc = 1
            continue
        cur = list(rows[0].get('alt_names') or [])
        missing = [a for a in adds if a not in cur]
        if not missing:
            print(f'{canon}: already carries {adds}')
            continue
        want = sorted(set(cur) | set(adds))
        print(f'{canon}: + {missing}')
        if not APPLY:
            continue
        pr = requests.patch(f'{SB}/rest/v1/ncaab_team_aliases', headers=H_W,
                            timeout=30,
                            params={'canonical_name': f'eq.{canon}'},
                            data=json.dumps({'alt_names': want}))
        if pr.status_code not in (200, 204):
            print(f'   !! PATCH {pr.status_code}: {pr.text[:200]}')
            rc = 1
            continue
        # A 204 is not a write -- read it back independently.
        rb = requests.get(f'{SB}/rest/v1/ncaab_team_aliases', headers=H,
                          timeout=30,
                          params={'select': 'alt_names',
                                  'canonical_name': f'eq.{canon}'})
        got = list((rb.json() or [{}])[0].get('alt_names') or [])
        still = [a for a in adds if a not in got]
        if still:
            print(f'   !! NOT CONFIRMED -- readback missing {still}')
            rc = 1
        else:
            print(f'   confirmed ({len(got)} alt_names)')

    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return rc

    # Re-assert every mapping through a freshly loaded resolver.
    sys.path.insert(0, str(Path(__file__).parent))
    import ncaab_team_resolver as R
    R._LOADED = False
    R.load_aliases()
    print('\nresolver assertions:')
    for name, want in EXPECT:
        got, how = R.resolve(name, with_method=True)
        ok = got == want
        if not ok:
            rc = 1
        print(f'  {"ok  " if ok else "FAIL"} {name!r:36} -> {str(got)!r:22} '
              f'({how}) want {str(want)!r}')
    return rc


if __name__ == '__main__':
    sys.exit(main())
