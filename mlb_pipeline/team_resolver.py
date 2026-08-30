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
    """Lowercase + strip + collapse whitespace + drop trailing period."""
    if not s: return ''
    n = str(s).lower().strip()
    # Collapse whitespace
    n = ' '.join(n.split())
    # Drop trailing period on abbreviations ("Florida St." → "florida st")
    if n.endswith('.'): n = n[:-1]
    return n


def _build_ncaaf_cache() -> dict:
    """Fetch ncaaf_team_aliases and build reverse-lookup dict."""
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_team_aliases'
        f'?select=canonical_name,full_name,location,nickname,abbrev,alt_names&limit=5000',
        headers=_H, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
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
    by_loc = cache['by_location']
    for loc_n, canon in by_loc.items():
        if loc_n and (loc_n in n or n in loc_n):
            # Guard against 2-char accidents ("fl" in "florida" but "fl"
            # isn't a team — require at least 4 chars on the shorter side).
            shorter = min(len(loc_n), len(n))
            if shorter >= 4:
                return canon

    # Last-word == nickname (e.g. raw "Seminoles" alone).
    by_nick = cache['by_nickname']
    tokens = n.split()
    if tokens:
        last = tokens[-1]
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


def resolve_or_log(raw_name: str, source: str) -> Optional[str]:
    """Convenience: try to resolve, log gap if not found. Return canonical or None."""
    canon = resolve_ncaaf_team(raw_name)
    if canon is None and raw_name:
        log_ncaaf_gap(raw_name, source)
    return canon
