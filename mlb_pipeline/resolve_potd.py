"""Resolve Play of the Day picks by reading game results.

POTDs live in jerry_cache with cache_key='best_bet_YYYY-MM-DD' and data
JSONB like {leanDisplay, game, score, sport, ...}. This script reads
each unresolved POTD, looks up the corresponding game result, computes
Win/Loss/Push, and writes data.result + data.resolved_at back.

Supported POTD bet types (MLB):
  - NRFI ('NRFI — Score X/100') → match against mlb_game_results.nrfi_result
  - YRFI ('YRFI ...') → same field, inverted
  - Over X.X (v2 edge ...) → mlb_game_results.total_result
  - Under X.X (v2 edge ...) → same field, flipped
  - [Team] ML → home/away comparison against home_win

Skips:
  - POTDs already resolved (data.result already set)
  - POTDs for today or future dates (game not played yet)
  - POTDs where game result not yet logged in mlb_game_results
  - noGames / noPlay marker entries

Idempotent. Safe to run every cron. Backfills any unresolved historical
POTDs automatically.
"""
import os
import sys
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
}
READ_HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def today_et_date():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.date()


def parse_data(d):
    if isinstance(d, str):
        try:
            return json.loads(d)
        except Exception:
            return None
    return d if isinstance(d, dict) else None


def fetch_game_result(date_str, home_team, away_team):
    """Look up mlb_game_results for the given date + matchup."""
    if not home_team or not away_team:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params={
                'game_date': f'eq.{date_str}',
                'home_team': f'eq.{home_team}',
                'away_team': f'eq.{away_team}',
                'select': 'home_score,away_score,home_win,nrfi_result,total_result,run_line_result,spread_result,close_total',
            },
            headers=READ_HEADERS,
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if rows else None
    except Exception as e:
        print(f"  game result lookup failed for {date_str} {away_team} @ {home_team}: {e}")
        return None


def compute_potd_outcome(lean_display, game_result, picked_game):
    """Return ('Win'|'Loss'|'Push'|None, optional_note).

    None means cannot resolve (unknown bet type or missing data).
    """
    if not lean_display or not game_result:
        return None, 'missing data'

    lean = lean_display.strip()
    lean_lower = lean.lower()

    # NRFI / YRFI plays — match nrfi_result
    if 'nrfi' in lean_lower and 'yrfi' not in lean_lower:
        nrfi_result = (game_result.get('nrfi_result') or '').upper()
        if nrfi_result not in ('NRFI', 'YRFI'):
            return None, 'nrfi_result not logged yet'
        return ('Win' if nrfi_result == 'NRFI' else 'Loss'), f'NRFI lean → {nrfi_result}'
    if 'yrfi' in lean_lower:
        nrfi_result = (game_result.get('nrfi_result') or '').upper()
        if nrfi_result not in ('NRFI', 'YRFI'):
            return None, 'nrfi_result not logged yet'
        return ('Win' if nrfi_result == 'YRFI' else 'Loss'), f'YRFI lean → {nrfi_result}'

    # Total Over/Under plays
    if lean_lower.startswith('over'):
        total_result = (game_result.get('total_result') or '')
        if not total_result:
            return None, 'total_result not logged'
        if total_result == 'Over':
            return 'Win', f'Over lean → {total_result}'
        if total_result == 'Under':
            return 'Loss', f'Over lean → {total_result}'
        return 'Push', f'Over lean → {total_result}'
    if lean_lower.startswith('under'):
        total_result = (game_result.get('total_result') or '')
        if not total_result:
            return None, 'total_result not logged'
        if total_result == 'Under':
            return 'Win', f'Under lean → {total_result}'
        if total_result == 'Over':
            return 'Loss', f'Under lean → {total_result}'
        return 'Push', f'Under lean → {total_result}'

    # ML plays — '[Team] ML' or '[Team] ML lean'
    if ' ml' in lean_lower:
        hw = game_result.get('home_win')
        if hw is None:
            return None, 'home_win not logged'
        # Extract picked team name from lean (everything before ' ML')
        picked = lean.split(' ML')[0].strip()
        home_team = (picked_game or {}).get('home_team', '')
        away_team = (picked_game or {}).get('away_team', '')
        # Match by city/mascot — picked team should appear in one of them
        picked_lower = picked.lower()
        is_home_pick = picked_lower in home_team.lower() or home_team.lower().endswith(picked_lower)
        is_away_pick = picked_lower in away_team.lower() or away_team.lower().endswith(picked_lower)
        if not (is_home_pick or is_away_pick):
            return None, f'cannot match {picked!r} to {home_team}/{away_team}'
        if is_home_pick and is_away_pick:
            # Ambiguous (e.g., both teams contain "Sox") — fall back to longer match
            home_score = len(set(picked_lower.split()) & set(home_team.lower().split()))
            away_score = len(set(picked_lower.split()) & set(away_team.lower().split()))
            is_home_pick = home_score >= away_score
            is_away_pick = not is_home_pick
        picked_won = bool(hw) if is_home_pick else (not bool(hw))
        return ('Win' if picked_won else 'Loss'), f'{picked} ML → {"won" if picked_won else "lost"}'

    return None, f'unknown bet type: {lean!r}'


def resolve_all():
    today = today_et_date()
    cutoff = (today - timedelta(days=14)).strftime('%Y-%m-%d')

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={
            'cache_key': f'like.best_bet_*',
            'select': 'id,cache_key,data,sport',
            'order': 'cache_key.desc',
            'limit': '50',
        },
        headers=READ_HEADERS,
        timeout=20,
    )
    rows = r.json() if r.status_code == 200 else []
    print(f"Pulled {len(rows)} POTD rows")

    resolved = 0
    skipped_pending = 0
    skipped_already = 0
    skipped_off = 0
    errored = 0

    for row in rows:
        key = row.get('cache_key', '')
        date_str = key.replace('best_bet_', '')
        try:
            d_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            continue
        if date_str < cutoff:
            continue  # too old, don't bother
        if d_obj >= today:
            continue  # today or future — game not played yet

        data = parse_data(row.get('data')) or {}
        if data.get('result') in ('Win', 'Loss', 'Push'):
            skipped_already += 1
            continue
        # Skip no-play / no-games markers
        if data.get('noGames') or data.get('noPlay'):
            skipped_off += 1
            continue

        game = data.get('game') or {}
        home_team = game.get('home_team')
        away_team = game.get('away_team')
        lean = data.get('leanDisplay') or ''
        sport = data.get('sport') or row.get('sport')

        # NBA POTDs use NBA-side resolution (separate table) — skip MLB resolver
        if sport and str(sport).upper() != 'MLB':
            continue

        gr = fetch_game_result(date_str, home_team, away_team)
        if not gr:
            print(f"  {date_str} {away_team} @ {home_team}: no game result yet")
            skipped_pending += 1
            continue

        outcome, note = compute_potd_outcome(lean, gr, game)
        if outcome is None:
            print(f"  {date_str} {away_team} @ {home_team}: cannot resolve — {note}")
            errored += 1
            continue

        # Patch jerry_cache row
        new_data = dict(data)
        new_data['result'] = outcome
        new_data['resolved_at'] = datetime.now(timezone.utc).isoformat()
        new_data['resolution_note'] = note

        pr = requests.patch(
            f"{SUPABASE_URL}/rest/v1/jerry_cache",
            params={'id': f'eq.{row["id"]}'},
            headers={**HEADERS, 'Prefer': 'return=minimal'},
            json={'data': new_data},
            timeout=15,
        )
        if pr.status_code in (200, 204):
            print(f"  ✓ {date_str} {away_team} @ {home_team}: {outcome} ({note})")
            resolved += 1
        else:
            print(f"  ⚠ {date_str} patch failed: HTTP {pr.status_code}")
            errored += 1

    print(f"\nResolved: {resolved}")
    print(f"Skipped — pending game results: {skipped_pending}")
    print(f"Skipped — already resolved: {skipped_already}")
    print(f"Skipped — no-play markers: {skipped_off}")
    if errored:
        print(f"Errored: {errored}")


if __name__ == '__main__':
    resolve_all()
