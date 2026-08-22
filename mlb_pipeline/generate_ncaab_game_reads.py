"""
NCAAB game reads — server-side Jerry.

For each NCAAB game in the next 5 days (CBB cadence is daily so horizon
is tighter than NCAAF's 10), assembles per-game context from
ncaab_game_context (adj_em, four-factors, tempo, projected spread/total,
confluence, sweat, primary_play) + market lines, feeds it to Claude
with the NCAAB prompt template, writes {narrative, struct} to
jerry_cache keyed game_read_<game_id>_<ET date>, sport='ncaab'.

Depends on ncaab_game_context.py having populated projections first.

Mirrors generate_ncaaf_game_reads.py structure. NCAAB rules template
falls back to NBA (both are pace-based basketball; the reads speak the
same language). Cross-sport Jerry audit (queued) will unify all sports'
prompt structure.

Usage: python generate_ncaab_game_reads.py [--force] [--limit N]
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
    """NCAAB rules template falls back to NBA (both spread/total/ML markets +
    pace-based scoring). Cross-sport Jerry audit will unify later."""
    rows = sb_get('prompt_templates', {
        'name': 'in.(game_read_wrapper,game_read_universal,game_read_rules)',
        'is_active': 'is.true',
        'select': 'name,sport,template',
    })
    out = {(r['name'], r['sport']): r['template'] for r in rows}
    wrapper = out.get(('game_read_wrapper', 'ALL'))
    universal = out.get(('game_read_universal', 'ALL'))
    rules = out.get(('game_read_rules', 'NCAAB')) or out.get(('game_read_rules', 'NBA'))
    if not (wrapper and universal and rules):
        print(f'  ⚠ missing prompt_templates rows — have: {list(out.keys())}')
        return None
    return {'wrapper': wrapper, 'universal': universal, 'rules': rules}


def fetch_upcoming_games():
    """ncaab_game_context rows for today + next 5 days (CBB is daily-cadence)."""
    today = today_et()
    horizon = (datetime.now(timezone.utc) + timedelta(days=5) - timedelta(hours=4)).strftime('%Y-%m-%d')
    # PostgREST needs two separate operators, chain manually
    url = (f'{SUPABASE_URL}/rest/v1/ncaab_game_context'
           f'?game_date=gte.{today}&game_date=lte.{horizon}'
           f'&select=*&order=sweat_score.desc.nullslast&limit=80')
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


def _build_casual_summary(ctx):
    """Lean 4-headline summary for the app's collapsed view."""
    headlines = []
    close_sp = _f(ctx.get('close_spread'))
    close_tot = _f(ctx.get('close_total'))
    proj_sp = _f(ctx.get('projected_spread'))
    proj_tot = _f(ctx.get('projected_total'))
    home = ctx.get('home_team') or 'Home'
    away = ctx.get('away_team') or 'Away'
    h_em = _f(ctx.get('home_adj_em'))
    a_em = _f(ctx.get('away_adj_em'))

    # adj_em gap (efficiency headline)
    if h_em is not None and a_em is not None:
        gap = h_em - a_em
        stronger = home if gap > 0 else away
        headlines.append((6, f'✓ KenPom efficiency favors {stronger} by {abs(gap):.1f}'))

    # Model vs market gap (NCAAB: proj_spread positive=home fav, close_spread negative=home fav → edge = proj_sp + close_sp)
    if proj_sp is not None and close_sp is not None:
        edge = proj_sp + close_sp
        if abs(edge) >= 2:
            fav = home if edge > 0 else away
            headlines.append((7, f'⚡ Model likes {fav} by {abs(edge):.1f} more than market'))

    # Total lean
    if proj_tot is not None and close_tot is not None:
        tot_edge = proj_tot - close_tot
        if abs(tot_edge) >= 4:
            side = 'OVER' if tot_edge > 0 else 'UNDER'
            headlines.append((5, f'📈 Total leans {side} ({tot_edge:+.1f})'))

    # Primary play
    pp = ctx.get('primary_play') or {}
    if pp.get('label'):
        tier = pp.get('tier', 'PLAY')
        headlines.append((8, f'🎯 {tier}: {pp["label"]}'))

    # Market snapshot
    if close_sp is not None:
        # Display in casual: negative close_spread means home favored
        home_display = f'{home} {close_sp:+.1f}' if close_sp < 0 else f'{away} {-close_sp:+.1f}'
        headlines.append((3, f'📊 {home_display}, total {close_tot or "N/A"}'))

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
        'commence_time': ctx.get('game_time_et'),
        'season': ctx.get('season'),
        'venue': ctx.get('venue'),
        'is_tournament': ctx.get('is_tournament'),
        'tournament_round': ctx.get('tournament_round'),
        'is_neutral_site': ctx.get('is_neutral_site'),
        'market': {
            'spread': _f(ctx.get('close_spread')),
            'open_spread': _f(ctx.get('open_spread')),
            'total': _f(ctx.get('close_total')),
            'open_total': _f(ctx.get('open_total')),
            'home_ml': ctx.get('home_ml_close'),
            'away_ml': ctx.get('away_ml_close'),
            'spread_convention': 'negative_close_spread_means_home_favored',
        },
        'model': {
            'projected_spread': _f(ctx.get('projected_spread')),
            'projected_total': _f(ctx.get('projected_total')),
            'projected_spread_convention': 'positive_projected_spread_means_home_wins_by_x',
            'model_pred_home_points': _f(ctx.get('model_pred_home_points')),
            'model_pred_away_points': _f(ctx.get('model_pred_away_points')),
            'adj_em_gap': _f(ctx.get('adj_em_gap')),
            'pace_avg': _f(ctx.get('pace_avg')),
        },
        'efficiency': {
            'home': {
                'adj_em': _f(ctx.get('home_adj_em')),
                'adj_oe': _f(ctx.get('home_adj_oe')),
                'adj_de': _f(ctx.get('home_adj_de')),
                'tempo':  _f(ctx.get('home_tempo')),
                'efg_o':  _f(ctx.get('home_efg_o')),
                'efg_d':  _f(ctx.get('home_efg_d')),
                'to_o':   _f(ctx.get('home_to_o')),
                'or_o':   _f(ctx.get('home_or_o')),
                'ftr_o':  _f(ctx.get('home_ftr_o')),
            },
            'away': {
                'adj_em': _f(ctx.get('away_adj_em')),
                'adj_oe': _f(ctx.get('away_adj_oe')),
                'adj_de': _f(ctx.get('away_adj_de')),
                'tempo':  _f(ctx.get('away_tempo')),
                'efg_o':  _f(ctx.get('away_efg_o')),
                'efg_d':  _f(ctx.get('away_efg_d')),
                'to_o':   _f(ctx.get('away_to_o')),
                'or_o':   _f(ctx.get('away_or_o')),
                'ftr_o':  _f(ctx.get('away_ftr_o')),
            },
        },
        'situational': {
            'home_days_rest': ctx.get('home_days_rest'),
            'away_days_rest': ctx.get('away_days_rest'),
            'home_record': ctx.get('home_record'),
            'away_record': ctx.get('away_record'),
            'home_l10': ctx.get('home_l10'),
            'away_l10': ctx.get('away_l10'),
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
        'meta': {
            'game_date': today_et(),
            'game_has_not_been_played': True,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sport': 'NCAAB',
        },
    }
    struct['casual_summary'] = _build_casual_summary(ctx)
    return struct


def render_prompt(templates, struct):
    ss = struct['sweat'].get('score')
    tier = struct['sweat'].get('tier') or '—'
    confidence_tier = f'{tier} — sweat {ss}/100 (KenPom + four-factors lens)'
    context_block = (
        'NCAAB GAME CONTEXT (authoritative — analyze this, do not search for scores):\n'
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
        .replace('{sport}', 'NCAAB')
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
    print(f'=== NCAAB game reads · {today_et()} ===')
    templates = load_templates()
    if not templates:
        print('  ✗ templates unavailable — abort (populate prompt_templates table first)')
        return
    games = fetch_upcoming_games()
    print(f'  upcoming games: {len(games)}')
    if not games:
        print('  (offseason or no ncaab_game_context rows yet)'); return
    if limit: games = games[:limit]

    written = 0; skipped = 0
    for ctx in games:
        struct = build_struct(ctx)
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if narrative is None:
            print(f'  ✗ claude failed for {ctx.get("game_id")}'); skipped += 1; continue
        ok = write_cache(ctx['game_id'], 'ncaab', narrative, struct)
        if ok:
            written += 1
        else:
            skipped += 1

    print(f'\n✓ wrote {written} · skipped {skipped}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    run(force=args.force, limit=args.limit)


if __name__ == '__main__':
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NCAAB')
    except ImportError:
        pass
    main()
