"""Starter-change detection — diff afternoon-refresh probable pitchers
against what mlb_game_context says we have.

Background
----------
2026-06-20 incident: I told the user Skenes was tonight's PIT starter
based on morning data. Afternoon refresh changed it to Skenes vs Sugano
(not the morning's Chandler vs Freeland). The starter swap silently
invalidated every pitcher-prop pick we'd surfaced. Saved-feedback rule
says ALWAYS verify pitcher attribution before claiming a pick. This is
the automated version of that check.

How it works
------------
1. Pull today's mlb_game_context — that's what the resolver + prop
   pipeline + jerry reads are all running on.
2. Pull MLB Stats API probable pitchers (hydrate=probablePitcher).
3. For each game today, diff (mlb_game_context.away_pitcher,
   .home_pitcher) vs (api.away.probable, api.home.probable).
4. Any mismatch → log a row to jerry_cache under
   starter_changes_<date> so the morning audit, the engine_tier_brief
   script, and any downstream tool can see "data is stale, refresh
   before publishing." Also prints to console for the cron run log.

Cron target
-----------
Run after the 2pm ET refresh and before the evening cards lock. Catches
the same-day starter swaps that get us. Idempotent: each run upserts the
full diff list, no stale entries pile up.
"""
import io
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {
    'apikey': SK,
    'Authorization': f'Bearer {SK}',
    'Content-Type': 'application/json',
}


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _norm(name):
    if not name:
        return ''
    # Light normalization — surnames are sufficient for diffs. Strip
    # accents + case-fold so "Yoshinobu Yamamoto" vs "Yoshinobu Yamamoto."
    # don't false-positive.
    import unicodedata
    n = ''.join(c for c in unicodedata.normalize('NFD', name)
                if unicodedata.category(c) != 'Mn')
    return n.strip().lower()


def fetch_context_pitchers(game_date):
    import urllib.request, urllib.parse
    qs = urllib.parse.urlencode({
        'game_date': f'eq.{game_date}',
        'select': 'game_id,away_team,home_team,away_pitcher,home_pitcher',
    })
    req = urllib.request.Request(
        f'{SU}/rest/v1/mlb_game_context?{qs}',
        headers={'apikey': SK, 'Authorization': f'Bearer {SK}'},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read())
    return {(r['away_team'], r['home_team']): r for r in rows if isinstance(r, dict)}


def fetch_api_pitchers(game_date):
    url = (f'https://statsapi.mlb.com/api/v1/schedule/games/?sportId=1'
           f'&date={game_date}&hydrate=probablePitcher')
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    out = {}
    for d in data.get('dates', []):
        for g in d.get('games', []):
            away = g.get('teams', {}).get('away', {}).get('team', {}).get('name')
            home = g.get('teams', {}).get('home', {}).get('team', {}).get('name')
            ap = g.get('teams', {}).get('away', {}).get('probablePitcher', {}) or {}
            hp = g.get('teams', {}).get('home', {}).get('probablePitcher', {}) or {}
            status = g.get('status', {}).get('detailedState', '')
            if away and home:
                out[(away, home)] = {
                    'away_pitcher': ap.get('fullName'),
                    'home_pitcher': hp.get('fullName'),
                    'status': status,
                    'game_pk': g.get('gamePk'),
                }
    return out


def main():
    today = _today_et()
    ctx = fetch_context_pitchers(today)
    api = fetch_api_pitchers(today)

    print(f'Starter-change check — {today}')
    print(f'  context games: {len(ctx)} | api games: {len(api)}')
    print('=' * 80)

    changes = []
    for key, c in ctx.items():
        a = api.get(key)
        if not a:
            # Game in context but not in API — likely a name mismatch or
            # a postponement. Flag it.
            changes.append({
                'away_team': key[0],
                'home_team': key[1],
                'kind': 'missing_api',
                'pipeline_away': c['away_pitcher'],
                'pipeline_home': c['home_pitcher'],
                'api_away': None,
                'api_home': None,
                'status': None,
            })
            continue
        away_changed = _norm(c['away_pitcher']) != _norm(a['away_pitcher'])
        home_changed = _norm(c['home_pitcher']) != _norm(a['home_pitcher'])
        if away_changed or home_changed:
            changes.append({
                'away_team': key[0],
                'home_team': key[1],
                'kind': 'starter_change',
                'pipeline_away': c['away_pitcher'],
                'pipeline_home': c['home_pitcher'],
                'api_away': a['away_pitcher'],
                'api_home': a['home_pitcher'],
                'status': a['status'],
                'away_changed': away_changed,
                'home_changed': home_changed,
            })

    if not changes:
        print('✅ No starter changes detected — pipeline matches MLB API.')
    else:
        print(f'⚠️  {len(changes)} discrepancies:')
        for c in changes:
            tag = '⚠️ ' if c['kind'] == 'starter_change' else '❓ '
            print(f"  {tag}{c['away_team']} @ {c['home_team']}  [{c.get('status') or '?'}]")
            if c.get('away_changed'):
                print(f"      AWAY:  pipeline={c['pipeline_away']!r}  →  api={c['api_away']!r}")
            if c.get('home_changed'):
                print(f"      HOME:  pipeline={c['pipeline_home']!r}  →  api={c['api_home']!r}")
            if c['kind'] == 'missing_api':
                print(f"      (game not found in MLB API hydrate response)")

    # Upsert into jerry_cache under a date-keyed entry so the morning
    # audit + engine_tier_brief can see what changed. Even an empty diff
    # gets logged so downstream tools can tell "we checked and found
    # nothing" vs "we never ran the check today".
    payload = {
        'cache_key': f'starter_changes_{today}',
        'game_id': f'starter_changes_{today}',
        'sport': 'mlb',
        'narrative': (f'{len(changes)} starter discrepancies on {today}'
                      if changes else f'No starter changes detected on {today}'),
        'data': {'date': today, 'count': len(changes), 'changes': changes,
                 'checked_at': datetime.now(timezone.utc).isoformat()},
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        req = urllib.request.Request(
            f'{SU}/rest/v1/jerry_cache?on_conflict=game_id,sport',
            data=json.dumps(payload).encode('utf-8'),
            headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15):
            print(f'\n✅ Logged to jerry_cache.starter_changes_{today}')
    except Exception as e:
        print(f'\n⚠️  jerry_cache upsert failed (non-fatal): {e}')


if __name__ == '__main__':
    main()
