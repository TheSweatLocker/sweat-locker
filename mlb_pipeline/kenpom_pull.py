"""Pull KenPom server-side so the key stops shipping in the app.

Andy 2026-09-28, after a leaked Anthropic key cost $548 in a day: "We need to
pull kenpom form client side then this top pirotiory."

KenPom was the last provider credential still in the binary.
EXPO_PUBLIC_KENPOM_KEY is inlined into the JS bundle by Expo exactly like the
Anthropic key was, and five client call sites send it to kenpom.com in an
Authorization header. Anyone who downloads the app can read it out.

It could not simply be deleted, because nothing server-side filled the cache
the client reads — the CLIENT was the only writer. A cache-only client would
have starved itself. This is that missing writer.

── WHAT IT REPLACES, AND WHY THE SHAPE IS COPIED EXACTLY ──
The app already reads kenpom_cache and only calls KenPom when the cache misses
or is stale. So the whole fix is to keep that cache warm from here and delete
the fallback. That works only if what we write is byte-for-byte what the
client expects, so the field mapping below is transcribed from
app/index.tsx fetchBartData — same keys, same parseFloat/parseInt coercions,
same defaults (109.4 for adjOE/adjDE, 68.0 for tempo). A near-miss here shows
up as a card full of zeroes, not an error.

  ratings + four-factors  ->  cache_key 'kenpom_ratings_ff_2026'
                              (the constant the client already queries)
  fanmatch                ->  cache_key 'kenpom_fanmatch_<YYYY-MM-DD>'

── FANMATCH WAS WORSE THAN THE RATINGS CALL ──
fetchBartData at least had a shared Supabase cache, so only the first device
each day hit KenPom. Fanmatch had NO server cache — only AsyncStorage, which
is per-device — so every user's phone called KenPom directly with the key on
every cold start. That is both the credential exposure and a quota problem:
one subscription being hit once per install per day.

── SEASON ──
The client hardcodes y=2026. Kept, but as a CLI flag so the rollover is a
workflow edit rather than an app release. NCAAB opens 2026-11-03.

Usage:
    python kenpom_pull.py                    # ratings + four-factors + fanmatch
    python kenpom_pull.py --season 2026
    python kenpom_pull.py --fanmatch-days 2  # today + tomorrow
    python kenpom_pull.py --dry-run
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
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
# ══ 2026-10-06 · THE FALLBACK CONTRADICTED THE COMMENT ABOVE IT ══
# This read:
#
#   # Server-side only. Never EXPO_PUBLIC_*, which Expo would bundle.
#   KENPOM_KEY = os.environ.get('KENPOM_KEY') or os.environ.get('EXPO_PUBLIC_KENPOM_KEY')
#
# saying "never EXPO_PUBLIC_*" and then falling back to it on the next line.
# That fallback is WHY the dangerously-named secret still exists: nothing ever
# forced the rename, because the server kept working either way. Meanwhile
# app.config.js BLOCKS that exact name and fails the build on it, so the two
# files actively disagreed about whether it should exist.
#
# Accepting only the server-side name makes the rename mandatory. If the key
# is missing the puller says so (see main()) instead of silently depending on
# a variable the app build refuses to allow.
#
# Note this key must be ROTATED regardless: app/index.tsx records that five
# client call sites sent it to kenpom.com before 2026-09-28, so it was inlined
# into every build up to v1.0.1 and is extractable from those binaries.
KENPOM_KEY = os.environ.get('KENPOM_KEY')

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

API = 'https://kenpom.com/api.php'
RATINGS_CACHE_KEY = 'kenpom_ratings_ff_{season}'
FANMATCH_CACHE_KEY = 'kenpom_fanmatch_{date}'


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def kp_get(endpoint: str, **params):
    r = requests.get(API, params={'endpoint': endpoint, **params},
                     headers={'Authorization': f'Bearer {KENPOM_KEY}'},
                     timeout=30)
    # 404 "no games" on fanmatch is an OFF-DAY, not a fault — most of the
    # calendar is off-days outside the season, and raising there would make a
    # normal Tuesday look like a broken integration.
    if r.status_code == 404 and 'no games' in (r.text or '').lower():
        return []
    if r.status_code != 200:
        raise RuntimeError(f'kenpom {endpoint} -> {r.status_code}: {r.text[:160]}')
    data = r.json()
    return data if isinstance(data, list) else []


def build_ratings(season: int) -> list:
    """ratings + four-factors merged into the client's exact row shape."""
    ratings = kp_get('ratings', y=season)
    ff = kp_get('four-factors', y=season)
    ff_map = {t.get('TeamName'): t for t in ff}
    out = []
    for t in ratings:
        f = ff_map.get(t.get('TeamName'), {})
        # Defaults mirror the client's `|| 109.4` / `|| 68.0` fallbacks so a
        # missing field renders identically to how it does today.
        out.append({
            'team': t.get('TeamName'),
            'adjOE': _f(t.get('AdjOE'), 109.4),
            'adjDE': _f(t.get('AdjDE'), 109.4),
            'adjEM': _f(t.get('AdjOE')) - _f(t.get('AdjDE')),
            'adjOERank': _i(t.get('RankAdjOE')),
            'adjDERank': _i(t.get('RankAdjDE')),
            'tempo': _f(t.get('AdjTempo'), 68.0),
            'tempoRank': _i(t.get('RankAdjTempo')),
            'eFG_O': _f(f.get('eFG_Pct')),
            'eFG_O_rank': _i(f.get('RankeFG_Pct')),
            'to_O': _f(f.get('TO_Pct')),
            'to_O_rank': _i(f.get('RankTO_Pct')),
            'or_O': _f(f.get('OR_Pct')),
            'or_O_rank': _i(f.get('RankOR_Pct')),
            'ftr_O': _f(f.get('FT_Rate')),
            'ftr_O_rank': _i(f.get('RankFT_Rate')),
            'eFG_D': _f(f.get('DeFG_Pct')),
            'eFG_D_rank': _i(f.get('RankDeFG_Pct')),
            'to_D': _f(f.get('DTO_Pct')),
            'to_D_rank': _i(f.get('RankDTO_Pct')),
            'or_D': _f(f.get('DOR_Pct')),
            'or_D_rank': _i(f.get('RankDOR_Pct')),
            'ftr_D': _f(f.get('DFT_Rate')),
            'ftr_D_rank': _i(f.get('RankDFT_Rate')),
            'wins': _i(t.get('Wins')),
            'losses': _i(t.get('Losses')),
            'conf': t.get('ConfShort') or '',
            'seed': t.get('Seed'),
            'luck': _f(t.get('Luck')),
            'sos': _f(t.get('SOS')),
            'coach': t.get('Coach') or '',
        })
    return out


def write_cache(cache_key: str, data, dry: bool) -> bool:
    if dry:
        print(f'  [DRY] would write {cache_key} ({len(data)} rows)')
        return True
    r = requests.post(f'{SB}/rest/v1/kenpom_cache?on_conflict=cache_key',
                      headers=H_WRITE,
                      json={'cache_key': cache_key, 'data': data,
                            'fetched_at': datetime.now(timezone.utc).isoformat()},
                      timeout=40)
    ok = r.status_code in (200, 201, 204)
    if not ok:
        print(f'  ⚠ kenpom_cache write {r.status_code}: {r.text[:180]}')
    return ok


def run(season: int, fanmatch_days: int, dry: bool,
        do_ratings: bool = True) -> int:
    if not KENPOM_KEY:
        print('  ✗ KENPOM_KEY missing — set it as a server-side secret')
        return 1
    print(f'=== kenpom_pull · season {season} ===')
    rc = 0

    if not do_ratings:
        print('  · ratings/four-factors skipped (--no-ratings)')
    try:
        rows = build_ratings(season) if do_ratings else None
        if rows is None:
            raise StopIteration
        if not rows:
            # Empty is a failure, not a quiet success: writing [] would blank
            # the NCAAB cards and look like a rendering bug.
            print('  ✗ ratings/four-factors returned 0 teams — refusing to '
                  'overwrite the cache with an empty list')
            rc = 1
        else:
            key = RATINGS_CACHE_KEY.format(season=season)
            ok = write_cache(key, rows, dry)
            print(f'  {"✓" if ok else "✗"} {key}: {len(rows)} teams')
            rc = rc or (0 if ok else 1)
    except StopIteration:
        pass
    except Exception as e:
        print(f'  ✗ ratings pull failed: {e}')
        rc = 1

    now = datetime.now(timezone.utc) - timedelta(hours=4)   # ET
    for d in range(fanmatch_days):
        day = (now - timedelta(days=d)).strftime('%Y-%m-%d')
        try:
            games = kp_get('fanmatch', d=day)
            key = FANMATCH_CACHE_KEY.format(date=day)
            if not games:
                print(f'  · {key}: no games (off-day) — skipped')
                continue
            ok = write_cache(key, games, dry)
            print(f'  {"✓" if ok else "✗"} {key}: {len(games)} games')
            rc = rc or (0 if ok else 1)
        except Exception as e:
            # A single day failing must not lose the ratings write above.
            print(f'  ⚠ fanmatch {day} failed: {e}')
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    # KenPom refuses future dates outright — "d (date) parameter cannot be
    # beyond <today>" — so asking for tomorrow is a guaranteed 400. Today, and
    # optionally days BACK.
    ap.add_argument('--fanmatch-days', type=int, default=1,
                    help='today, plus N-1 days BACK (KenPom serves no future dates)')
    # ncaab_pipeline.yml deliberately refreshes RATINGS weekly only —
    # "KenPom ratings converge slowly during season; weekly refresh is
    # sufficient. This halves paid-API usage." That discipline is kept: the
    # daily run passes --no-ratings and pulls only fanmatch, which is a
    # different thing entirely (it IS today's slate, so it has to be daily).
    ap.add_argument('--no-ratings', action='store_true',
                    help='skip ratings/four-factors; pull fanmatch only')
    ap.add_argument('--no-fanmatch', action='store_true',
                    help='skip fanmatch; pull ratings/four-factors only')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    sys.exit(run(a.season, 0 if a.no_fanmatch else a.fanmatch_days,
                 a.dry_run, do_ratings=not a.no_ratings))


if __name__ == '__main__':
    main()
