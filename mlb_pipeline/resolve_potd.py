"""Auto-resolve Play of the Day picks against daily_best_bet_history.

Reads each row of daily_best_bet_history, looks up the actual game
result via mlb_game_results, computes Win/Loss/Push based on the lean
text, and writes the authoritative result back. Backfills any rows
that are missing results or have stale manual entries.

Supported bet types (MLB):
  - NRFI / YRFI → mlb_game_results.nrfi_result
  - Over X.X / Under X.X → mlb_game_results.total_result
  - [Team] ML → home_win + team-name match
  - [Team] -1.5 / +1.5 (run line) → mlb_game_results.spread_result
    Added 2026-05-28 after 5/27 LAD -1.5 POTD stayed Pending — the
    chalk-ML-alt POTD type was unsupported, so every run-line pick
    silently failed resolution.

Skips:
  - Rows where mlb_game_results.{nrfi_result/total_result/home_win/
    spread_result} isn't logged yet (game pending or game-results
    pipeline lagging)
  - Future-dated rows (game not played)
  - NBA POTDs (separate resolution path)

Also writes the POTD into daily_best_bet_history when the row is
missing — keeps the table current without manual data entry.

CLI:
  python resolve_potd.py          # backfill + resolve as needed
  python resolve_potd.py --report # dry-run, show all discrepancies
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

DRY_RUN = '--report' in sys.argv or '--dry-run' in sys.argv


def today_et_date():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.date()


def parse_game_string(game):
    """Parse 'Away Team @ Home Team' → (away, home)."""
    if not game or ' @ ' not in game:
        return None, None
    parts = game.split(' @ ', 1)
    return parts[0].strip(), parts[1].strip()


def fetch_game_result(date_str, home_team, away_team):
    if not home_team or not away_team:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params={
                'game_date': f'eq.{date_str}',
                'home_team': f'eq.{home_team}',
                'away_team': f'eq.{away_team}',
                'select': 'home_score,away_score,home_win,nrfi_result,total_result,close_total,spread_result',
            },
            headers=READ_HEADERS,
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if rows else None
    except Exception as e:
        print(f"  lookup failed for {date_str} {away_team} @ {home_team}: {e}")
        return None


def compute_outcome(lean, gr, away_team, home_team):
    """Return ('Win'|'Loss'|'Push'|None, note)."""
    if not lean or not gr:
        return None, 'missing data'
    lean_lower = lean.lower()

    if 'nrfi' in lean_lower and 'yrfi' not in lean_lower:
        nr = (gr.get('nrfi_result') or '').upper()
        if nr not in ('NRFI', 'YRFI'):
            return None, 'nrfi_result not logged'
        return ('Win' if nr == 'NRFI' else 'Loss'), f'NRFI lean → {nr}'

    if 'yrfi' in lean_lower:
        nr = (gr.get('nrfi_result') or '').upper()
        if nr not in ('NRFI', 'YRFI'):
            return None, 'nrfi_result not logged'
        return ('Win' if nr == 'YRFI' else 'Loss'), f'YRFI lean → {nr}'

    if lean_lower.startswith('over'):
        tr = gr.get('total_result') or ''
        if not tr:
            return None, 'total_result not logged'
        if tr == 'Over':
            return 'Win', f'Over lean → {tr}'
        if tr == 'Under':
            return 'Loss', f'Over lean → {tr}'
        return 'Push', f'Over lean → {tr}'

    if lean_lower.startswith('under'):
        tr = gr.get('total_result') or ''
        if not tr:
            return None, 'total_result not logged'
        if tr == 'Under':
            return 'Win', f'Under lean → {tr}'
        if tr == 'Over':
            return 'Loss', f'Under lean → {tr}'
        return 'Push', f'Under lean → {tr}'

    # Run-line / spread MUST be checked before ML because run-line POTDs often
    # mention "ML" in parentheticals ("LAD -1.5 (chalk ML alt — ML -424 too
    # juiced)") which would false-match the ML branch and fail name resolution.
    # spread_result lives on mlb_game_results as 'home_covered'/'away_covered'/'push'.
    import re
    rl_match = re.search(r'^(.+?)\s+([+\-]1\.5)\b', lean.strip())
    if rl_match:
        sr = (gr.get('spread_result') or '').lower()
        if sr not in ('home_covered', 'away_covered', 'push'):
            return None, 'spread_result not logged'
        if sr == 'push':
            return 'Push', f'run line → push'
        picked = rl_match.group(1).strip()
        sign = rl_match.group(2)  # '-1.5' or '+1.5'
        picked_lower = picked.lower()
        is_home = picked_lower in (home_team or '').lower() or (home_team or '').lower().endswith(picked_lower)
        is_away = picked_lower in (away_team or '').lower() or (away_team or '').lower().endswith(picked_lower)
        if not (is_home or is_away):
            return None, f'cannot match {picked!r} to {home_team}/{away_team}'
        if is_home and is_away:
            home_score = len(set(picked_lower.split()) & set((home_team or '').lower().split()))
            away_score = len(set(picked_lower.split()) & set((away_team or '').lower().split()))
            is_home = home_score >= away_score
            is_away = not is_home
        # Did the picked side cover?
        picked_covered = (sr == 'home_covered') if is_home else (sr == 'away_covered')
        return ('Win' if picked_covered else 'Loss'), f'{picked} {sign} → {"covered" if picked_covered else "didnt cover"} (spread_result={sr})'

    if ' ml' in lean_lower or lean_lower.endswith(' ml'):
        hw = gr.get('home_win')
        if hw is None:
            return None, 'home_win not logged'
        picked = lean.split(' ML')[0].strip()
        picked_lower = picked.lower()
        is_home = picked_lower in (home_team or '').lower() or (home_team or '').lower().endswith(picked_lower)
        is_away = picked_lower in (away_team or '').lower() or (away_team or '').lower().endswith(picked_lower)
        if not (is_home or is_away):
            return None, f'cannot match {picked!r} to {home_team}/{away_team}'
        if is_home and is_away:
            home_score = len(set(picked_lower.split()) & set((home_team or '').lower().split()))
            away_score = len(set(picked_lower.split()) & set((away_team or '').lower().split()))
            is_home = home_score >= away_score
            is_away = not is_home
        picked_won = bool(hw) if is_home else (not bool(hw))
        return ('Win' if picked_won else 'Loss'), f'{picked} ML → {"won" if picked_won else "lost"}'

    return None, f'unknown bet type: {lean!r}'


def resolve_all():
    today = today_et_date()

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/daily_best_bet_history",
        params={
            'select': 'id,bet_date,sport,game,lean,result',
            'order': 'bet_date.desc',
            'limit': '500',
        },
        headers=READ_HEADERS,
        timeout=20,
    )
    rows = r.json() if r.status_code == 200 else []
    print(f"Pulled {len(rows)} daily_best_bet_history rows")
    if DRY_RUN:
        print('DRY RUN — no writes')

    correct = 0
    fixed = 0
    new_wins = 0
    new_losses = 0
    pushes = 0
    still_pending = 0
    skipped_future = 0
    skipped_nba = 0
    discrepancies = []

    for row in rows:
        bet_date = row.get('bet_date')
        if not bet_date:
            continue
        try:
            d = datetime.strptime(bet_date, '%Y-%m-%d').date()
        except ValueError:
            continue
        if d >= today:
            skipped_future += 1
            continue
        sport = (row.get('sport') or '').upper()
        if sport and sport != 'MLB':
            skipped_nba += 1
            continue

        away, home = parse_game_string(row.get('game') or '')
        gr = fetch_game_result(bet_date, home, away)
        if not gr:
            still_pending += 1
            continue

        outcome, note = compute_outcome(row.get('lean'), gr, away, home)
        if outcome is None:
            still_pending += 1
            continue

        existing = row.get('result')
        if existing == outcome:
            correct += 1
        else:
            discrepancies.append({
                'date': bet_date,
                'game': row.get('game'),
                'lean': row.get('lean'),
                'manual': existing,
                'auto': outcome,
                'note': note,
            })

        if outcome == 'Win':
            new_wins += 1
        elif outcome == 'Loss':
            new_losses += 1
        else:
            pushes += 1

        if not DRY_RUN and existing != outcome:
            patch = {'result': outcome, 'resolved_at': datetime.now(timezone.utc).isoformat()}
            pr = requests.patch(
                f"{SUPABASE_URL}/rest/v1/daily_best_bet_history",
                params={'id': f'eq.{row["id"]}'},
                headers={**HEADERS, 'Prefer': 'return=minimal'},
                json=patch,
                timeout=15,
            )
            if pr.status_code in (200, 204):
                fixed += 1

    total_resolved = new_wins + new_losses + pushes
    win_pct = (new_wins / total_resolved * 100) if total_resolved else 0

    print()
    print('=' * 70)
    print(f'AUTO-COMPUTED POTD RECORD')
    print('=' * 70)
    print(f'Resolved games:    {total_resolved}')
    print(f'  Wins:            {new_wins}')
    print(f'  Losses:          {new_losses}')
    print(f'  Pushes:          {pushes}')
    print(f'  Win rate:        {win_pct:.1f}%')
    print()
    print(f'Already correct:   {correct}')
    print(f'Manual was wrong:  {len(discrepancies)}')
    print(f'Still pending:     {still_pending}')
    print(f'Future-dated:      {skipped_future}')

    if discrepancies:
        print()
        print('=' * 70)
        print(f'DISCREPANCIES (manual vs auto)')
        print('=' * 70)
        for d in discrepancies:
            print(f"  {d['date']} | {d['game']}")
            print(f"    lean: {d['lean']}")
            print(f"    manual: {d['manual']:6} | auto: {d['auto']} | {d['note']}")

    if not DRY_RUN and fixed:
        print(f'\nPatched {fixed} row(s) in daily_best_bet_history')

    print()
    print(f"Honest POTD record: {new_wins}-{new_losses}{' ('+str(pushes)+'P)' if pushes else ''} = {win_pct:.1f}% on n={total_resolved}")


if __name__ == '__main__':
    resolve_all()
