"""Sharp scenario lookup — Jerry-facing helper.

Consumed by:
  * generate_prop_jerry_synthesis.py — inject matches into prompt
  * generate_jerry_synthesis.py — same
  * apply_refit_verdict_override.py — auto-adjust conviction per matches

Usage:
    from sharp_scenario_lookup import matches_for_game, format_for_prompt
    matches = matches_for_game(game_id)
    prompt_text = format_for_prompt(matches)
"""
import os, requests
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

_SB = os.environ.get('SUPABASE_URL')
_KEY = os.environ.get('SUPABASE_KEY')
_H = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'} if _KEY else {}

# Per-date cache to avoid hammering PostgREST when generating many reads
_CACHE = {}


def matches_for_game(game_id: str, game_date: str = None) -> list:
    """Return list of scenario match dicts for a game_id. Cached per-date.
    Returns empty list on any failure — never raises."""
    if not game_id or not _SB: return []
    # Use date-scoped cache
    cache_key = game_date or 'nodate'
    if cache_key not in _CACHE:
        try:
            params = {'select': 'game_id,market,scenario_key,side,hit_rate,n,back_or_fade,jerry_hint,hint_confidence'}
            if game_date:
                params['game_date'] = f'eq.{game_date}'
            r = requests.get(f'{_SB}/rest/v1/sharp_scenario_game_matches',
                             headers=_H, params=params, timeout=8)
            rows = r.json() if r.status_code == 200 else []
            by_gid = {}
            for row in rows if isinstance(rows, list) else []:
                by_gid.setdefault(row['game_id'], []).append(row)
            _CACHE[cache_key] = by_gid
        except Exception:
            _CACHE[cache_key] = {}
    return _CACHE[cache_key].get(game_id, [])


def format_for_prompt(matches: list, max_shown: int = 6) -> str:
    """Format scenario matches as a compact prose block Jerry can read.
    Prioritizes BACK/FADE actionable matches over NEUTRAL."""
    if not matches: return ''
    # Sort by hint confidence desc, filter to actionable
    actionable = [m for m in matches if m.get('back_or_fade') in ('BACK', 'FADE')]
    actionable.sort(key=lambda m: -(m.get('hint_confidence') or 0))
    if not actionable: return ''
    lines = []
    for m in actionable[:max_shown]:
        emoji = '✅' if m['back_or_fade'] == 'BACK' else '🚨'
        lines.append(f'  {emoji} {m["market"].upper()} {m["side"]}: '
                     f'historical {m.get("hit_rate")}% (n={m.get("n")}) → {m["jerry_hint"]}')
    return 'SHARP SCENARIO MATCHES (historical patterns firing on this game):\n' + '\n'.join(lines)


def summary_for_market(matches: list, market: str) -> dict:
    """Return {net_score, dominant_side, backing_signals, fading_signals} for
    a specific market ('ml' or 'total') based on all matches for a game.

    net_score: +N BACK votes minus FADE votes for the HOME/OVER side.
    Positive = HOME/OVER lean, negative = AWAY/UNDER lean."""
    mm = [m for m in matches if m.get('market') == market]
    home_or_over_votes = 0
    away_or_under_votes = 0
    for m in mm:
        side = str(m.get('side') or '').upper()
        vote_weight = 1 if m.get('back_or_fade') == 'BACK' else -1 if m.get('back_or_fade') == 'FADE' else 0
        # BACK on HOME/OVER = +1 for that side
        # FADE on HOME/OVER = -1 for HOME/OVER (i.e., BACK AWAY/UNDER)
        if side in ('HOME', 'OVER'):
            if vote_weight == 1: home_or_over_votes += 1
            elif vote_weight == -1: away_or_under_votes += 1
        elif side in ('AWAY', 'UNDER'):
            if vote_weight == 1: away_or_under_votes += 1
            elif vote_weight == -1: home_or_over_votes += 1
    net = home_or_over_votes - away_or_under_votes
    dominant = None
    if abs(net) >= 2:
        dominant = ('HOME' if market == 'ml' else 'OVER') if net > 0 else ('AWAY' if market == 'ml' else 'UNDER')
    return {
        'net_score': net,
        'dominant_side': dominant,
        'home_or_over_votes': home_or_over_votes,
        'away_or_under_votes': away_or_under_votes,
    }


def reset_cache():
    """Clear per-date cache (call between different runs if needed)."""
    global _CACHE
    _CACHE = {}
