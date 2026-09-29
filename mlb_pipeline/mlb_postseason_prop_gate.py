"""Stop MLB props carrying max units on regular-season evidence in October.

Andy 2026-09-29: "Fix MLB issues and get ready for playoffs today."

── WHAT WAS ON THE BOARD ──
Today is Wild Card game 1 of a best-of-3 in all four series (verified against
MLB StatsAPI gameType 'F': PHI@ATL, CWS@HOU, BOS@NYY, CHC@SD). The prop board
carried 16 PRIME and 397 SKIP — no STRONG, no LEAN — and 11 of those 16 PRIME
were PITCHER WORKLOAD props: outs, hits allowed, walks, strikeouts, earned
runs. The Sharp card then published eight of them, seven keyed to how long a
starter lasts, at 2.4-2.8 units each.

Every one of those convictions was computed from regular-season evidence.
mlb_season_type.py already names why that is the wrong baseline: rotations
compress so aces start on short rest and fourth starters do not pitch,
bullpens are available every night instead of managed across 162 games, and
September team stats describe lineups that rested regulars after clinching.

── WHAT THIS DELIBERATELY DOES NOT DO ──
It does not adjust projections, flip sides, or change a line. There is no
postseason sample to calibrate against — eight teams and ~32 games — and
mlb_season_type.py's own docstring says so: "This does not try to make the
models right for October ... pretending otherwise is how the leaked prop
PRIME happened." Inventing a direction here would be exactly that mistake.

So this caps CONFIDENCE, which is the part we know is overstated, and leaves
the pick itself alone. Same shape as nfl_qb_injury_gate: cap tier and
conviction, never touch side/market/line, and record what was capped.

── WHY A CAP AND NOT A SUPPRESSION ──
Dropping playoff props entirely would empty the board on the days subscribers
care most, and would claim we know these props are BAD. We do not. We know our
confidence in them is built on a baseline that shifted. PRIME is the tier that
drives the largest unit sizes on the card, so one notch down (PRIME -> STRONG,
conviction ceiling 72) removes the max-stake claim while keeping the play
visible with its reasoning intact.

Pitcher-workload props get the note naming rotation compression specifically,
because that is the sharpest of the three effects and the one a reader can
check against the lineup card.

Usage:
    python mlb_postseason_prop_gate.py                 # today
    python mlb_postseason_prop_gate.py --date 2026-09-29
    python mlb_postseason_prop_gate.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

# ══ TWO SEVERITIES, BECAUSE THERE ARE TWO DIFFERENT RISKS ══
# The first version of this capped every prop identically — PRIME -> STRONG,
# conviction 72 — and Andy caught what that actually did: the composer's PRIME
# floor is conviction >= 73, so a 72 ceiling does not move props "one notch
# down", it removes the PRIME tier from props entirely for the whole
# postseason. I described it as a notch. It was an elimination.
#
# Worse, it was indiscriminate. Five of the sixteen capped were hits_under —
# BATTER props, which rotation compression has nothing to do with.
#
# WORKLOAD (outs / HA / BB / K / ER): a starter's counting stats resolve on how
#   long he lasts, and a short series is exactly when that changes. Sharp,
#   specific, well-evidenced. Keeps the hard cap.
#
# BATTER (hits / TB / RBI / runs / HR): the only postseason argument is that
#   September form describes lineups resting regulars after clinching. Real,
#   but weak and two-sided — playoff batters face better pitching (helps an
#   under) while playoff lineups are full-strength A-teams (hurts it). Those
#   roughly offset. So: trim the confidence, do not remove the tier. 78 sits
#   ABOVE the PRIME floor of 73, so a genuinely strong batter prop can still
#   surface as PRIME while losing its max-conviction claim.
WORKLOAD_TIER_CAP = 'STRONG'
WORKLOAD_CONVICTION_CAP = 72
BATTER_TIER_CAP = None          # tier untouched — confidence only
BATTER_CONVICTION_CAP = 78
# Ascending-good, identical to nfl_qb_injury_gate._RANK so a cap comparison
# reads the same way in both gates. I first wrote this descending and the
# tier cap silently never fired — the dry run showed conviction moving and
# tier as '-', which is exactly the 'gate reports itself applied while
# changing nothing' failure the conviction comment below warns about.
_RANK = {'SKIP': 0, 'PASS': 0, 'COVERAGE': 1, 'LEAN': 2, 'STRONG': 3, 'PRIME': 4}

# Pitcher counting stats — all a direct function of how long the starter goes,
# which is precisely what a short series changes. Stems are matched before the
# trailing _over/_under.
WORKLOAD_STEMS = {'outs', 'ha', 'bb', 'ks', 'k', 'er'}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _stem(prop_type: str) -> str:
    pt = str(prop_type or '')
    return pt.rsplit('_', 1)[0] if pt.endswith(('_over', '_under')) else pt


def postseason_games(date: str) -> dict:
    """game_id -> season_type for POSTSEASON games on `date`.

    Reads mlb_game_context.season_type rather than calling StatsAPI again:
    upload_game_context writes it on every build as of 2026-09-29, so this
    stays consistent with whatever the card and Jerry saw. A row still
    labelled REGULAR is simply not gated — failing open is correct here,
    since the alternative is capping ordinary June props on a lookup blip.
    """
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H_READ,
                     params={'select': 'game_id,season_type,away_team,home_team',
                             'game_date': f'eq.{date}'},
                     timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ mlb_game_context {r.status_code}: {r.text[:140]}')
        return {}
    out = {}
    for g in r.json():
        st = str(g.get('season_type') or '').upper()
        if st and st not in ('REGULAR', 'SPRING', 'EXHIBITION', 'ALL_STAR'):
            out[g['game_id']] = st
    return out


def run(date: str, dry: bool) -> int:
    if not SB or not KEY:
        print('  ✗ SUPABASE_URL / key missing')
        return 1
    print(f'=== mlb_postseason_prop_gate · {date} ===')
    ps = postseason_games(date)
    if not ps:
        print('  · no postseason games on this date — nothing to gate')
        return 0
    print(f'  postseason games: {len(ps)} ({sorted(set(ps.values()))})')

    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                     headers=H_READ,
                     params={'select': 'id,game_id,player_name,prop_type,'
                                       'direction,prop_line,tier,conviction,signals',
                             'game_date': f'eq.{date}',
                             'limit': '2000'},
                     timeout=30)
    if r.status_code != 200:
        print(f'  ✗ props fetch {r.status_code}: {r.text[:160]}')
        return 1
    props = r.json()
    # Only rows that are actually publishable can be over-confident. A SKIP
    # at conviction 0 needs no protecting.
    live = [p for p in props
            if p.get('game_id') in ps
            and str(p.get('tier') or '').upper() in ('PRIME', 'STRONG', 'LEAN')]
    print(f'  props on postseason games: {len(live)} publishable '
          f'(of {len(props)} total)')

    capped = failed = 0
    for p in live:
        stem = _stem(p.get('prop_type'))
        workload = stem in WORKLOAD_STEMS
        tier_cap = WORKLOAD_TIER_CAP if workload else BATTER_TIER_CAP
        conv_cap = WORKLOAD_CONVICTION_CAP if workload else BATTER_CONVICTION_CAP
        # Always reason from the ORIGINAL values, not the current row. A prior
        # run of this gate may have already capped it — possibly with the old
        # blanket rule — and re-capping the capped value would ratchet
        # downward and could never restore a tier that should not have moved.
        # Reading _postseason_cap back makes the gate idempotent AND
        # self-correcting when these constants change.
        prev = (p.get('signals') or {}).get('_postseason_cap') or {}
        cur = str(prev.get('tier_from') or p.get('tier') or '').upper()
        orig_conv = prev.get('conviction_from')
        if orig_conv is None:
            orig_conv = p.get('conviction')
        patch = {}
        note = {'season_type': ps[p['game_id']],
                'reason': ('pitcher workload prop — rotations compress and '
                           'bullpens run nightly in a short series; '
                           'conviction was built on regular-season evidence'
                           if workload else
                           'postseason — September form describes lineups '
                           'that rested regulars after clinching')}
        if tier_cap and _RANK.get(tier_cap, 9) < _RANK.get(cur, 0):
            note['tier_from'], note['tier_to'] = cur, tier_cap
            patch['tier'] = tier_cap
        elif str(p.get('tier') or '').upper() != cur:
            # Restoring a tier a previous, blunter run took away.
            note['tier_from'], note['tier_to'] = str(p.get('tier')).upper(), cur
            patch['tier'] = cur
        # Clamped independently of the tier: a prop already at STRONG keeps its
        # tier but must not keep a PRIME-sized conviction. The NFL gate shipped
        # once without this and reported itself applied while changing nothing
        # the composer ranks on.
        try:
            if orig_conv is not None:
                # int(): mlb_pipeline_props.conviction is an INTEGER column
                # and PostgREST rejects "78.0" with 22P02. min() on floats
                # produced exactly that, and all five batter-prop restores
                # failed 400 until this cast. The gate reported the failure
                # and exited non-zero rather than claiming success, which is
                # the one part of that episode that worked as intended.
                target = int(min(float(orig_conv), float(conv_cap)))
                if float(p.get('conviction') or 0) != target:
                    note['conviction_from'] = orig_conv
                    note['conviction_to'] = target
                    patch['conviction'] = target
        except (TypeError, ValueError):
            pass
        if not patch:
            continue
        sig = p.get('signals')
        sig = dict(sig) if isinstance(sig, dict) else {}
        sig['_postseason_cap'] = note
        patch['signals'] = sig
        label = (f"{p.get('player_name')} {p.get('prop_type')} "
                 f"{p.get('direction')} {p.get('prop_line')}")
        if dry:
            print(f"  [DRY] {label[:44]:44s} {note.get('tier_from','-')}→"
                  f"{note.get('tier_to','-')} conv "
                  f"{note.get('conviction_from','-')}→{note.get('conviction_to','-')}")
            capped += 1
            continue
        w = requests.patch(f"{SB}/rest/v1/mlb_pipeline_props?id=eq.{p['id']}",
                           headers=H_WRITE, json=patch, timeout=20)
        if w.status_code in (200, 204):
            capped += 1
            print(f"  ✓ {label[:44]:44s} {note.get('tier_from','-')}→"
                  f"{note.get('tier_to','-')} conv "
                  f"{note.get('conviction_from','-')}→{note.get('conviction_to','-')}"
                  f"{'  [workload]' if workload else ''}")
        else:
            failed += 1
            print(f'  ✗ {label[:44]} patch {w.status_code}: {w.text[:120]}')

    print(f'\n  capped {capped} · failed {failed} · untouched {len(live)-capped-failed}')
    # A write failure is a real failure: the board stays over-confident and the
    # card will size off it.
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    sys.exit(run(a.date or today_et(), a.dry_run))


if __name__ == '__main__':
    main()
