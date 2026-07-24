"""
NCAAF game reads — server-side Jerry.

For each NCAAF game in the next 10 days, assembles per-game context
from ncaaf_game_context (SP+, EPA, projected spread/total, confluence,
sweat score, primary_play) + market lines, feeds it to Claude with the
NCAAF prompt template, writes {narrative, struct} to jerry_cache keyed
game_read_<game_id>_<ET date>, sport='ncaaf'.

Depends on ncaaf_game_context.py having populated projections first.

Mirrors generate_nfl_game_reads.py structure. The cross-sport Jerry
audit (queued) will unify all sports' prompt structure — this file
follows the current NFL pattern so it's audit-compatible.

Usage: python generate_ncaaf_game_reads.py [--force] [--limit N]
"""
import argparse
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SB_READ = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
SB_WRITE = {**SB_READ, 'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=minimal'}
MODEL = 'claude-haiku-4-5-20251001'


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def now_et_human():
    d = datetime.now(timezone.utc) - timedelta(hours=4)
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def sb_get(path, params=None):
    qs = '&'.join(f'{k}={v}' for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{path}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


def load_templates():
    """Load wrapper + universal + sport-specific rules templates.
    NCAAF rules template falls back to NFL if NCAAF row doesn't exist
    yet (unified by post-launch Jerry read audit)."""
    rows = sb_get('prompt_templates', {
        'name': 'in.(game_read_wrapper,game_read_universal,game_read_rules)',
        'is_active': 'is.true',
        'select': 'name,sport,template',
    })
    out = {(r['name'], r['sport']): r['template'] for r in rows}
    wrapper = out.get(('game_read_wrapper', 'ALL'))
    universal = out.get(('game_read_universal', 'ALL'))
    # Prefer NCAAF, fall back to NFL rules (structurally similar; both spread/total/ML markets)
    rules = out.get(('game_read_rules', 'NCAAF')) or out.get(('game_read_rules', 'NFL'))
    if not (wrapper and universal and rules):
        print(f'  ⚠ missing prompt_templates rows — have: {list(out.keys())}')
        return None
    return {'wrapper': wrapper, 'universal': universal, 'rules': rules}


def fetch_upcoming_games():
    """Pull ncaaf_game_context rows for games in next 10 days."""
    today = today_et()
    horizon = (datetime.now(timezone.utc) + timedelta(days=10) - timedelta(hours=4)).strftime('%Y-%m-%d')
    return sb_get('ncaaf_game_context', {
        'game_date': f'gte.{today}',
        'game_date': f'lte.{horizon}',   # note: dict overwrites; postgrest also parses both if in URL directly
        'select': '*',
        'order': 'sweat_score.desc.nullslast',
        'limit': '50',
    })


def _build_casual_summary(ctx):
    """Lean 4-headline summary for the app's collapsed view."""
    headlines = []
    close_sp = _f(ctx.get('close_spread'))
    close_tot = _f(ctx.get('close_total'))
    proj_sp = _f(ctx.get('projected_spread'))
    proj_tot = _f(ctx.get('projected_total'))
    home = ctx.get('home_team') or 'Home'
    away = ctx.get('away_team') or 'Away'
    home_sp = _f(ctx.get('home_sp_overall'))
    away_sp = _f(ctx.get('away_sp_overall'))

    # SP+ gap
    if home_sp is not None and away_sp is not None:
        gap = home_sp - away_sp
        stronger = home if gap > 0 else away
        headlines.append((6, f'✓ SP+ favors {stronger} by {abs(gap):.1f}'))

    # Model vs market gap
    if proj_sp is not None and close_sp is not None:
        edge = proj_sp - close_sp
        if abs(edge) >= 3:
            fav = home if edge > 0 else away
            headlines.append((7, f'⚡ Model likes {fav} by {abs(edge):.1f} more than market'))

    # Primary play
    pp = ctx.get('primary_play') or {}
    if pp.get('label'):
        tier = pp.get('tier', 'PLAY')
        headlines.append((8, f'🎯 {tier}: {pp["label"]}'))

    # Market snapshot
    if close_sp is not None:
        headlines.append((3, f'📊 {home} {"+" if close_sp>0 else ""}{close_sp}, total {close_tot or "N/A"}'))

    headlines.sort(key=lambda x: -x[0])
    return {
        'headlines': [h[1] for h in headlines[:4]],
        'bottom_line': (pp.get('sub') if pp else 'Model tracking — no primary play yet'),
    }


def build_struct(ctx):
    home = ctx.get('home_team'); away = ctx.get('away_team')
    struct = {
        'matchup': f'{away} @ {home}',
        'game_id': ctx.get('game_id'),
        'commence_time': ctx.get('kickoff_utc'),
        'season': ctx.get('season'),
        'week': ctx.get('week'),
        'season_type': ctx.get('season_type'),
        'neutral_site': ctx.get('neutral_site'),
        'conference_game': ctx.get('conference_game'),
        'market': {
            'spread': _f(ctx.get('close_spread')),
            'open_spread': _f(ctx.get('open_spread')),
            'total': _f(ctx.get('close_total')),
            'open_total': _f(ctx.get('open_total')),
            'home_ml': ctx.get('close_home_ml'),
            'away_ml': ctx.get('close_away_ml'),
        },
        'model': {
            'projected_spread': _f(ctx.get('projected_spread')),
            'projected_total': _f(ctx.get('projected_total')),
            'model_pred_home_points': _f(ctx.get('model_pred_home_points')),
            'model_pred_away_points': _f(ctx.get('model_pred_away_points')),
            'sp_gap': _f(ctx.get('sp_gap')),
        },
        'efficiency': {
            'home': {
                'sp_overall': _f(ctx.get('home_sp_overall')),
                'off_epa_pp': _f(ctx.get('home_off_epa_pp')),
                'def_epa_pp': _f(ctx.get('home_def_epa_pp')),
            },
            'away': {
                'sp_overall': _f(ctx.get('away_sp_overall')),
                'off_epa_pp': _f(ctx.get('away_off_epa_pp')),
                'def_epa_pp': _f(ctx.get('away_def_epa_pp')),
            },
        },
        'confluence': {
            'net': ctx.get('signal_confluence_net'),
            'breakdown': ctx.get('signal_confluence_breakdown'),
        },
        'primary_play': ctx.get('primary_play'),
        'sweat': {
            'score': ctx.get('sweat_score'),
            'tier': ctx.get('sweat_tier'),
        },
        'cohort_tags': ctx.get('cohort_tags') or [],
        'meta': {
            'game_date': today_et(),
            'game_has_not_been_played': True,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sport': 'NCAAF',
        },
    }
    struct['casual_summary'] = _build_casual_summary(ctx)
    return struct


def render_prompt(templates, struct):
    ss = struct['sweat'].get('score')
    tier = struct['sweat'].get('tier') or '—'
    confidence_tier = f'{tier} — sweat {ss}/100 (SP+ + EPA rich lens)'
    context_block = (
        'NCAAF GAME CONTEXT (authoritative — analyze this, do not search for scores):\n'
        + json.dumps(struct, indent=2, default=str)
    )
    m = struct['market']
    away, home = struct['matchup'].split(' @ ')
    pp = struct.get('primary_play') or {}
    return (
        templates['wrapper']
        .replace('{today_et}', now_et_human())
        .replace('{away_team}', away)
        .replace('{home_team}', home)
        .replace('{commence_time_et}', struct.get('commence_time') or 'soon')
        .replace('{sport}', 'NCAAF')
        .replace('{sweat_score}', str(ss or '—'))
        .replace('{sweat_tier_label}', tier)
        .replace('{spread_str}', str(m.get('spread') or 'N/A'))
        .replace('{total_str}', str(m.get('total') or 'N/A'))
        .replace('{model_lean}', pp.get('label') or 'no primary play')
        .replace('{confidence_tier}', confidence_tier)
        .replace('{tournament_floor_note}', '')
        .replace('{full_score_context}', '')
        .replace('{model_context}', '')
        .replace('{sport_context}', context_block)
        .replace('{sport_rules}', templates['rules'])
        .replace('{universal_rules}', templates['universal'])
        .replace('{data_quality_note}', '')
    )


def call_claude(prompt: str) -> Optional[str]:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={'Content-Type': 'application/json',
                     'x-api-key': ANTHROPIC_API_KEY,
                     'anthropic-version': '2023-06-01'},
            json={'model': MODEL, 'max_tokens': 800,
                  'messages': [{'role': 'user', 'content': prompt}]},
            timeout=30,
        )
        data = r.json()
        if r.status_code != 200:
            print(f'  ⚠ claude {r.status_code}: {str(data)[:200]}')
            return None
        return ''.join(b.get('text', '') for b in (data.get('content') or [])
                       if b.get('type') == 'text').strip() or None
    except Exception as e:
        print(f'  ⚠ claude call failed: {e}')
        return None


def write_cache(game_id: str, sport: str, narrative: str, struct: dict) -> bool:
    cache_key = f'game_read_{game_id}_{today_et()}'
    payload = {
        'cache_key': cache_key,
        'game_id': game_id,
        'sport': sport,
        'narrative': narrative or '',
        'data': struct,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key',
        headers=SB_WRITE, json=payload, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def run(force: bool = False, limit: Optional[int] = None) -> None:
    print(f'=== NCAAF game reads · {today_et()} ===')
    templates = load_templates()
    if not templates:
        print('  ✗ templates unavailable — abort (populate prompt_templates table first)')
        return
    games = fetch_upcoming_games()
    print(f'  upcoming games: {len(games)}')
    if not games:
        print('  (no NCAAF games in horizon — offseason or slate not loaded)')
        return
    if limit:
        games = games[:limit]

    success = 0
    for ctx in games:
        gid = ctx.get('game_id')
        away = ctx.get('away_team'); home = ctx.get('home_team')
        print(f'\n  {away} @ {home}  ({gid})')
        struct = build_struct(ctx)
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if not narrative:
            print(f'    ⚠ claude returned empty — skipping cache write')
            continue
        ok = write_cache(gid, 'ncaaf', narrative, struct)
        if ok:
            success += 1
            print(f'    ✓ wrote cache (narrative {len(narrative)} chars)')
        else:
            print(f'    ⚠ cache write failed')

    print(f'\n✓ {success}/{len(games)} game reads generated')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    run(force=args.force, limit=args.limit)


if __name__ == '__main__':
    main()
