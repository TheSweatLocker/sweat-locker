"""Signal Registry lookup — Playbook-aware helper.

Reads signal_registry (populated by seed_signal_registry.py from
docs/PLAYBOOK.md) and exposes it to consumers:
  * generate_jerry_synthesis.py — inject relevant tier + weight rows so
    Jerry cites its own historical evidence in the read.
  * potd_universal_pool.py + prop scorer — block ANTI_VALIDATED signals
    from being primary basis for PRIME tier; multiply signal contributions
    by recommended_weight.

Usage:
    from signal_registry_lookup import (
        get_signal, signals_for_scope, format_for_prompt,
        is_anti_validated, weight_of
    )
    entries = signals_for_scope(sport='MLB', market_scope='total')
    prompt_block = format_for_prompt(entries)

Cached per-run (registry rarely changes intra-run). Zero writes.
"""
from __future__ import annotations
import os, requests
from pathlib import Path
from typing import Optional

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

_SB = os.environ.get('SUPABASE_URL')
_KEY = os.environ.get('SUPABASE_KEY')
_H = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'} if _KEY else {}

# Simple singleton cache — registry is small (~30 rows) + changes daily at most
_REGISTRY_CACHE: Optional[list] = None


def _load() -> list:
    """One-shot fetch of the whole registry. Cached until process exit."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    if not _SB:
        _REGISTRY_CACHE = []
        return _REGISTRY_CACHE
    try:
        r = requests.get(
            f'{_SB}/rest/v1/signal_registry',
            headers=_H,
            params={'select': 'signal_name,category,sport,market_scope,description,'
                              'hit_rate,sample_n,edge_pp,tier,recommended_weight,'
                              'direction_hint,notes'},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        _REGISTRY_CACHE = rows if isinstance(rows, list) else []
    except Exception:
        _REGISTRY_CACHE = []
    return _REGISTRY_CACHE


def get_signal(signal_name: str) -> Optional[dict]:
    """Return the registry row for a signal_name, or None."""
    for r in _load():
        if r.get('signal_name') == signal_name:
            return r
    return None


def signals_for_scope(sport: Optional[str] = None,
                      market_scope: Optional[str] = None,
                      min_tier: Optional[str] = None) -> list:
    """Return registry rows filtered by sport (or '*' universal) and
    market_scope (or 'multi'). Optionally filter to a minimum tier.

    Tier ranking (best to worst):
      VALIDATED > DISCOVERY > UNVALIDATED > ANTI_VALIDATED
    """
    tier_rank = {'VALIDATED': 0, 'DISCOVERY': 1,
                 'UNVALIDATED': 2, 'ANTI_VALIDATED': 3}
    min_rank = tier_rank.get(min_tier, 3) if min_tier else 3

    out = []
    for r in _load():
        # Sport match: exact or '*' universal or None means any
        if sport is not None:
            row_sport = r.get('sport') or '*'
            if row_sport != '*' and row_sport != sport:
                continue
        # Market scope match: exact, 'multi', or None means any
        if market_scope is not None:
            row_scope = r.get('market_scope') or 'multi'
            if row_scope != 'multi' and row_scope != market_scope:
                continue
        # Tier gate
        if tier_rank.get(r.get('tier') or 'UNVALIDATED', 3) > min_rank:
            continue
        out.append(r)

    # Sort by tier rank asc, then hit_rate desc
    out.sort(key=lambda x: (
        tier_rank.get(x.get('tier') or 'UNVALIDATED', 3),
        -(x.get('hit_rate') or 0),
    ))
    return out


def is_anti_validated(signal_name: str) -> bool:
    """True if the signal is tagged ANTI_VALIDATED (below-baseline hit
    rate) — consumers should NOT use it as primary basis for PRIME picks.
    Missing signals return False (unknown ≠ bad)."""
    r = get_signal(signal_name)
    return r is not None and r.get('tier') == 'ANTI_VALIDATED'


def weight_of(signal_name: str, default: float = 1.0) -> float:
    """Return recommended_weight for a signal, or `default` if not in
    registry. Callers multiply their signal contribution by this weight."""
    r = get_signal(signal_name)
    if r is None or r.get('recommended_weight') is None:
        return default
    try:
        return float(r['recommended_weight'])
    except (TypeError, ValueError):
        return default


def format_for_prompt(entries: list, max_shown: int = 10) -> str:
    """Format registry rows as a compact evidence block Jerry can read.
    Leads with VALIDATED signals, calls out ANTI_VALIDATED as fade-not-follow.
    Returns empty string when entries is empty (no block injected)."""
    if not entries:
        return ''

    validated = [e for e in entries if e.get('tier') == 'VALIDATED'][:max_shown]
    discovery = [e for e in entries if e.get('tier') == 'DISCOVERY'][:max_shown]
    anti      = [e for e in entries if e.get('tier') == 'ANTI_VALIDATED'][:max_shown]

    lines = ['SIGNAL PLAYBOOK (audited evidence — cite by name when relevant):']

    if validated:
        lines.append('')
        lines.append('VALIDATED (real edge, use freely):')
        for e in validated:
            hr = e.get('hit_rate'); n = e.get('sample_n')
            hint = e.get('direction_hint') or 'FOLLOW'
            hr_txt = f'{hr}% n={n}' if hr is not None and n else '(qualitative)'
            lines.append(f'  - {e["signal_name"]}: {hr_txt} · {hint} · {e.get("description") or ""}'.rstrip())

    if discovery:
        lines.append('')
        lines.append('DISCOVERY (positive edge, half-weight until n grows):')
        for e in discovery:
            hr = e.get('hit_rate'); n = e.get('sample_n')
            hr_txt = f'{hr}% n={n}' if hr is not None and n else '(qualitative)'
            lines.append(f'  - {e["signal_name"]}: {hr_txt} · {e.get("description") or ""}'.rstrip())

    if anti:
        lines.append('')
        lines.append('ANTI-VALIDATED (below baseline — treat as fade signal or DROP):')
        for e in anti:
            hr = e.get('hit_rate'); n = e.get('sample_n')
            hr_txt = f'{hr}% n={n}' if hr is not None and n else '(qualitative)'
            hint = e.get('direction_hint') or ''
            note = e.get('notes') or ''
            lines.append(f'  - {e["signal_name"]}: {hr_txt} · {hint} · {note}'.rstrip())

    lines.append('')
    lines.append('RULE: never use an ANTI_VALIDATED signal as your primary basis '
                 'for a PRIME pick. Cite validated signals by name.')
    return '\n'.join(lines)
