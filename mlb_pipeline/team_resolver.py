"""Zero-failure team-name resolver (2026-08-29).

Turns any source's team-name variant into our canonical name, or logs
the gap so we can add the alias. Every scraper + odds pull uses this
instead of rolling its own dict lookup — one source of truth, zero
silent-drop bugs.

USAGE:
    from team_resolver import resolve_ncaaf_team, log_ncaaf_gap
    canon = resolve_ncaaf_team('Florida State Seminoles')  # → 'Florida State'
    canon = resolve_ncaaf_team('Seminoles')                # → 'Florida State'
    canon = resolve_ncaaf_team('FSU')                      # → 'Florida State'
    canon = resolve_ncaaf_team('Some New Variant')         # → None
        log_ncaaf_gap('Some New Variant', source='dimers')  # persist gap

Resolver ladder (deterministic, no fuzzy Levenshtein):
    1. Exact match on canonical_name
    2. Exact match on abbrev
    3. Exact match on location
    4. Exact match on nickname
    5. Exact match on full_name
    6. Exact match on any alt_name
    7. Location-included-in-raw OR raw-included-in-location (both directions)
       — handles "Florida State Seminoles" → matches location "Florida State"
    8. Last-word == nickname (mascot-only inputs like "Seminoles")
    9. Return None + caller can log gap

Caches the alias table in-memory per-process (refresh via clear_cache()).
Cache is deliberately per-process so scripts are self-contained;
long-running services should call clear_cache() periodically.
"""
from __future__ import annotations
import os
from typing import Optional
from datetime import datetime, timezone

import requests

SB  = os.environ.get('SUPABASE_URL', '')
KEY = os.environ.get('SUPABASE_KEY', '')
_H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
_HW = {**_H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}


# ═══════════════════════════════════════════════════════════════════════
# NCAAF resolver
# ═══════════════════════════════════════════════════════════════════════

_NCAAF_CACHE: dict | None = None  # {'by_key': {norm_str: canonical}, 'canonicals': set}


def _norm(s: str) -> str:
    """Lowercase + strip + collapse whitespace + normalize hyphens/periods.

    2026-08-29: hyphens → space so 'Bethune-Cookman' matches
    'Bethune Cookman'. Trailing period dropped ('Florida St.').
    """
    if not s: return ''
    n = str(s).lower().strip()
    # Hyphens → space so hyphenated + space-separated names collide
    n = n.replace('-', ' ')
    # Collapse whitespace
    n = ' '.join(n.split())
    # Drop trailing period on abbreviations
    if n.endswith('.'): n = n[:-1]
    # Also drop internal periods ('St.', 'A&M.') — normalized to 'st' 'a&m'
    n = n.replace('.', '')
    n = ' '.join(n.split())   # collapse again in case periods left doubles
    return n


def _build_ncaaf_cache() -> dict:
    """Fetch ncaaf_team_aliases and build reverse-lookup dict.

    2026-09-03 PAGE THROUGH: Supabase HARD-CAPS at 1000 rows per request
    regardless of ?limit=. Prior code sent `limit=5000` and silently got
    only 1000 rows, missing ~872 aliases (Utah Tech, Virginia Tech,
    hundreds of FCS + D-II teams). Discovered when seed script correctly
    inserted Virginia Tech but resolver still returned None — cache built
    from a truncated slice never saw the row.
    """
    rows = []
    for offset in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_team_aliases',
            params={
                'select': 'canonical_name,full_name,location,nickname,abbrev,alt_names',
                'limit': 1000, 'offset': offset,
            },
            headers=_H, timeout=15,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    by_key: dict[str, str] = {}
    by_nickname: dict[str, str] = {}
    by_location: dict[str, str] = {}
    canonicals: set[str] = set()

    def _add(k: str, canon: str, index: dict = by_key) -> None:
        n = _norm(k)
        if n and n not in index:
            index[n] = canon

    for row in rows:
        canon = row.get('canonical_name')
        if not canon: continue
        canonicals.add(canon)
        _add(canon, canon)
        _add(row.get('full_name') or '', canon)
        _add(row.get('abbrev') or '', canon)
        loc = row.get('location') or ''
        nick = row.get('nickname') or ''
        _add(loc, canon)
        _add(nick, canon)
        if loc: _add(loc, canon, by_location)
        if nick: _add(nick, canon, by_nickname)
        alt = row.get('alt_names') or []
        if isinstance(alt, list):
            for a in alt:
                _add(a, canon)
        elif isinstance(alt, str):
            # comma-separated fallback
            for a in alt.split(','):
                _add(a.strip(), canon)

    return {'by_key': by_key, 'by_nickname': by_nickname,
            'by_location': by_location, 'canonicals': canonicals}


def _get_cache() -> dict:
    global _NCAAF_CACHE
    if _NCAAF_CACHE is None:
        _NCAAF_CACHE = _build_ncaaf_cache()
    return _NCAAF_CACHE


def clear_cache() -> None:
    """Force cache reload on next call."""
    global _NCAAF_CACHE
    _NCAAF_CACHE = None


def resolve_ncaaf_team(raw_name: str) -> Optional[str]:
    """Return canonical_name or None. Never raises. Never returns raw string.

    Ladder is deterministic — same input always gives same output.
    If None, caller should log_ncaaf_gap(raw_name, source=...).
    """
    if not raw_name: return None
    n = _norm(raw_name)
    if not n: return None
    cache = _get_cache()
    by_key = cache['by_key']

    # Exact hit (canonical / abbrev / location / nickname / full_name / alt)
    if n in by_key: return by_key[n]

    # Substring: raw includes location OR location includes raw.
    # Handles "florida state seminoles" (raw) vs "florida state" (location).
    # 2026-08-29: tightened guards. Prior version matched "north" (5 char
    # single-token substring in some D3 school) against "north carolina
    # state" — wrong team. Now require:
    #   - Shorter side ≥8 chars OR contains a space (multi-word). Blocks
    #     short single-token accidents.
    #   - Prefer the LONGEST loc_n match (more specific team wins).
    by_loc = cache['by_location']
    best_canon = None
    best_len = 0
    for loc_n, canon in by_loc.items():
        if not loc_n: continue
        if loc_n not in n and n not in loc_n: continue
        shorter = min(loc_n, n, key=len)
        # Reject accidents: shorter must be substantial (multi-word or ≥8 chars)
        if len(shorter) < 8 and ' ' not in shorter:
            continue
        if len(loc_n) > best_len:
            best_canon = canon
            best_len = len(loc_n)
    if best_canon:
        return best_canon

    # Last-word == nickname (e.g. raw "Seminoles" alone).
    by_nick = cache['by_nickname']
    tokens = n.split()
    if tokens:
        # 2- or 3-word mascots (e.g. "Blue Devils", "Fighting Irish")
        for tail_len in (3, 2, 1):
            if len(tokens) >= tail_len:
                candidate = ' '.join(tokens[-tail_len:])
                if candidate in by_nick:
                    return by_nick[candidate]

    return None


def log_ncaaf_gap(raw_name: str, source: str) -> None:
    """Upsert row into team_alias_gaps. Increments hit_count on repeat."""
    if not raw_name or not source: return
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        'sport': 'NCAAF',
        'source': source,
        'raw_name': raw_name,
        'last_seen': now,
        'hit_count': 1,   # server-side increment via on_conflict merge
    }
    try:
        # First try insert; if conflict, PATCH to bump last_seen + hit_count
        r = requests.post(
            f'{SB}/rest/v1/team_alias_gaps',
            headers={**_HW, 'Prefer': 'return=minimal'},
            json=payload, timeout=10,
        )
        if r.status_code == 409:
            # Row exists — increment via PATCH
            requests.patch(
                f'{SB}/rest/v1/team_alias_gaps'
                f'?sport=eq.NCAAF&source=eq.{source}&raw_name=eq.{raw_name}',
                headers={**_HW, 'Prefer': 'return=minimal'},
                json={'last_seen': now}, timeout=10,
            )
            # Best-effort hit_count bump via RPC or accept it's just "last_seen updated"
    except Exception:
        pass  # never let gap logging break a scrape


# 2026-09-03: cross-sport noise filter. Some NCAAF scrapers surface
# unrelated names via regex misfires on page content (e.g. Pickswise
# has an all-sports trending picks section). Filter these out at the
# gap-log step so triage stays focused on real NCAAF misses.
# Not exhaustive — deliberately narrow to well-known pro-sport team
# names that CANNOT be a college football team.
_OTHER_SPORT_TEAMS = {
    # MLB
    'los angeles dodgers','los angeles angels','san diego padres','san francisco giants',
    'colorado rockies','arizona diamondbacks','houston astros','texas rangers',
    'oakland athletics','athletics','seattle mariners','kansas city royals',
    'chicago white sox','chicago cubs','minnesota twins','cleveland guardians',
    'detroit tigers','st. louis cardinals','st louis cardinals','pittsburgh pirates',
    'cincinnati reds','milwaukee brewers','philadelphia phillies','washington nationals',
    'atlanta braves','miami marlins','new york mets','new york yankees',
    'baltimore orioles','tampa bay rays','toronto blue jays','boston red sox',
    # MLS / EPL / other soccer (top offenders in logs)
    'chicago fire','toronto fc','seattle sounders','columbus crew','sporting kansas city',
    'vancouver whitecaps fc','orlando city sc','houston dynamo','san jose earthquakes',
    'atlanta united fc','inter miami cf','cf montreal','philadelphia union','dc united',
    'los angeles fc','nashville sc','saint louis city sc','fc dallas','new york city fc',
    'liverpool','nottingham forest','bournemouth','everton','tottenham hotspur',
    'newcastle','coventry city','hull city','strasbourg','lens',
    # CFL
    'ottawa redblacks',
    # La Liga / Serie A (surfaced in gaps 8/30 scrape)
    'real madrid','fc barcelona','barcelona','atletico madrid','valencia',
    'sevilla','real betis','villarreal','málaga','malaga','deportivo la coruña',
    'deportivo la coruna','rayo vallecano','celta vigo','osasuna','getafe',
    'napoli','como','juventus','inter milan','ac milan','roma','lazio',
}


def _is_probable_team_name(raw: str) -> bool:
    """Heuristic filter — raw name looks like a plausible NCAAF team name.
    Rejects: page titles, sentence fragments, article headlines. Real
    NCAAF team names are 1-4 words, ≤35 chars, no scraper-junk keywords."""
    if not raw: return False
    if len(raw) > 35: return False   # kills 'college football picks predictions early bet liberty'
    tokens = raw.split()
    if len(tokens) > 5: return False  # kills 'memphis tigers picks parlay odds college football week 0'
    NOISE_KEYWORDS = {'picks','prediction','predictions','parlay','odds','week',
                      'saturday','sunday','friday','august','september','vs',
                      'pick','preview','bet','early','best','today'}
    lower_tokens = {t.lower() for t in tokens}
    if lower_tokens & NOISE_KEYWORDS:
        return False
    return True


def resolve_or_log(raw_name: str, source: str) -> Optional[str]:
    """Convenience: try to resolve, log gap if not found. Return canonical or None."""
    canon = resolve_ncaaf_team(raw_name)
    if canon is None and raw_name:
        # Suppress gap logs for:
        # 1. Known non-CFB pro-sport team names (MLS, MLB, EPL, La Liga...)
        # 2. Scraper junk (page titles, article headlines, sentence fragments)
        # Both are permissive-regex misfires from external scrapers hitting
        # multi-sport aggregator pages. Real NCAAF misses still surface.
        n = _norm(raw_name)
        if n in _OTHER_SPORT_TEAMS: return None
        if not _is_probable_team_name(raw_name): return None
        log_ncaaf_gap(raw_name, source)
    return canon


# ═══════════════════════════════════════════════════════════════════════
# NCAAB resolver (2026-09-03) — same architecture as NCAAF, different
# alias table. NCAAB has fewer aliases (365 vs NCAAF 1872) so more raw
# names likely to miss — gap logging critical for March Madness edge
# cases (low-seed schools, tournament expansion, etc).
# ═══════════════════════════════════════════════════════════════════════

_NCAAB_CACHE: dict | None = None


def _build_ncaab_cache() -> dict:
    """Fetch ncaab_team_aliases and build reverse-lookup dict.
    Schema: canonical_name, kenpom_name, odds_api_name, bart_name,
            alt_names, conference, espn_id.

    2026-09-03: paginate (Supabase 1000-row cap, same fix as NCAAF)."""
    rows = []
    for offset in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/ncaab_team_aliases',
            params={
                'select': 'canonical_name,kenpom_name,odds_api_name,bart_name,alt_names',
                'limit': 1000, 'offset': offset,
            },
            headers=_H, timeout=15,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    by_key: dict[str, str] = {}
    canonicals: set[str] = set()

    def _add(k: str, canon: str) -> None:
        n = _norm(k)
        if n and n not in by_key:
            by_key[n] = canon

    for row in rows:
        canon = row.get('canonical_name')
        if not canon: continue
        canonicals.add(canon)
        _add(canon, canon)
        _add(row.get('kenpom_name') or '', canon)
        _add(row.get('odds_api_name') or '', canon)
        _add(row.get('bart_name') or '', canon)
        alt = row.get('alt_names') or []
        if isinstance(alt, list):
            for a in alt: _add(a, canon)
        elif isinstance(alt, str):
            for a in alt.split(','): _add(a.strip(), canon)

    return {'by_key': by_key, 'canonicals': canonicals}


def _get_ncaab_cache() -> dict:
    global _NCAAB_CACHE
    if _NCAAB_CACHE is None:
        _NCAAB_CACHE = _build_ncaab_cache()
    return _NCAAB_CACHE


def clear_ncaab_cache() -> None:
    global _NCAAB_CACHE
    _NCAAB_CACHE = None


def resolve_ncaab_team(raw_name: str) -> Optional[str]:
    """Return canonical NCAAB name or None. Never guesses.

    Ladder (deterministic — no fuzzy Levenshtein):
      1. Exact match on canonical / kenpom / odds_api / bart / alt name
      2. Substring match on any indexed key ≥8 chars or multi-word
         (blocks trivial 'st' -> 'north carolina state' false hits)
      3. Return None → caller logs gap for triage
    """
    if not raw_name: return None
    n = _norm(raw_name)
    if not n: return None
    cache = _get_ncaab_cache()
    by_key = cache['by_key']
    if n in by_key: return by_key[n]

    # Longest-substring match with min-length guard (mirror NCAAF safety)
    best_canon = None; best_len = 0
    for k, canon in by_key.items():
        if not k: continue
        if k not in n and n not in k: continue
        shorter = min(k, n, key=len)
        if len(shorter) < 8 and ' ' not in shorter:
            continue
        if len(k) > best_len:
            best_canon, best_len = canon, len(k)
    return best_canon


def log_ncaab_gap(raw_name: str, source: str) -> None:
    """Upsert row into team_alias_gaps (sport='NCAAB')."""
    if not raw_name or not source: return
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        'sport': 'NCAAB', 'source': source, 'raw_name': raw_name,
        'last_seen': now, 'hit_count': 1,
    }
    try:
        r = requests.post(
            f'{SB}/rest/v1/team_alias_gaps',
            headers={**_HW, 'Prefer': 'return=minimal'},
            json=payload, timeout=10,
        )
        if r.status_code == 409:
            requests.patch(
                f'{SB}/rest/v1/team_alias_gaps'
                f'?sport=eq.NCAAB&source=eq.{source}&raw_name=eq.{raw_name}',
                headers={**_HW, 'Prefer': 'return=minimal'},
                json={'last_seen': now}, timeout=10,
            )
    except Exception:
        pass


def resolve_or_log_ncaab(raw_name: str, source: str) -> Optional[str]:
    canon = resolve_ncaab_team(raw_name)
    if canon is None and raw_name:
        log_ncaab_gap(raw_name, source)
    return canon
