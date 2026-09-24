"""
NHL game reads — server-side Jerry.

2026-09-24. Andy, on finding all 37 NHL reads were the engine's own `sub`
string: "wire generator".

Until now nhl_pipeline.yml had no read generator at all. It pulled odds,
resolved results, built context, pulled externals, computed tendencies and
generated props — and nothing wrote prose. sync_jerry_reads_from_ctx then
backfilled primary_play.sub as the visible short_read, so every NHL card
read like this, 31-33 characters with an empty long_read, attached to a
real moneyline at conviction 60-64:

    "Model conviction on Canucks · 64%"
    "Model conviction on Kraken · 63%"

This is wiring, not new construction. Four sports already share
jerry_reads_dual_write, prompt_templates already holds an ACTIVE NHL
game_read_rules row, and nhl_game_context already carries 56 populated
columns — including precisely what that template asks to lead on:

    away_goalie / home_goalie + *_goalie_confirmed
    *_goalie_sv_pct, *_goalie_gsaa
    *_pp_pct, *_pk_pct                 special teams
    *_xgf_per60, *_xga_per60, *_5v5_cf, *_high_danger_for/against
    *_rest_days, *_back_to_back        fatigue
    close_puckline, close_total, home_ml_close, away_ml_close
    elo_home/elo_away, projected_total, projected_home_wp

The template's own framing is kept deliberately: "Market-based analysis —
no NHL model active yet. Do NOT fabricate model metrics." There is no
trained NHL margin model, so the read leads on goalies and market rather
than inventing model language — the same honesty the NHL rules row was
written with before anyone wired it up.

Mirrors generate_ncaab_game_reads.py structure.

Usage: python generate_nhl_game_reads.py [--force] [--limit N]
"""
import argparse
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

from jerry_reads_dual_write import parse_synthesis, upsert_jerry_read

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


def _et_date_from_utc(ts) -> Optional[str]:
    """UTC kickoff -> the ET calendar date the game is played on.

    NHL plays almost entirely at night, so nearly every puck drop crosses
    midnight UTC. Slicing the UTC date would stamp most games a day late —
    which is exactly the bug that produced duplicate NFL cards
    (generate_nfl_game_reads, fixed 2026-09-24). Do it right the first
    time here.
    """
    if not ts:
        return None
    try:
        return (datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                .astimezone(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def sb_get(path, params=None):
    qs = '&'.join(f'{k}={v}' for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{path}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


def load_templates():
    rows = sb_get('prompt_templates', {
        'name': 'in.(game_read_wrapper,game_read_universal,game_read_rules)',
        'is_active': 'is.true',
        'select': 'name,sport,template',
    })
    out = {(r['name'], r['sport']): r['template'] for r in rows}
    wrapper = out.get(('game_read_wrapper', 'ALL'))
    universal = out.get(('game_read_universal', 'ALL'))
    rules = out.get(('game_read_rules', 'NHL'))
    if not (wrapper and universal and rules):
        print(f'  ⚠ missing prompt_templates rows — have: {sorted(out.keys())}')
        return None
    return {'wrapper': wrapper, 'universal': universal, 'rules': rules}


def fetch_upcoming_games():
    """Every nhl_game_context row from today forward.

    Deliberately NOT a fixed day horizon. The first version copied
    NCAAB's "today + 5 days" and immediately left three games stubbed:
    nhl_game_context carried data out to 09-30 while the generator
    stopped at 09-29, so the edge of the window kept the engine-sub
    placeholder the generator exists to replace.

    A second horizon that disagrees with the context table's own reach
    can only ever produce that. Read what context holds; the row cap is
    the safety valve, not an invented date.
    """
    url = (f'{SUPABASE_URL}/rest/v1/nhl_game_context'
           f'?game_date=gte.{today_et()}'
           f'&select=*&order=game_date.asc,sweat_score.desc.nullslast&limit=120')
    r = requests.get(url, headers=SB_READ, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ nhl_game_context fetch {r.status_code}: {r.text[:140]}')
        return []
    return r.json()


def _goalie_block(ctx, side):
    """Goalie is the single most important NHL signal, and whether the
    start is CONFIRMED changes how hard the read may lean on it. Both
    facts travel together so the writer cannot cite one without the other.
    """
    return {
        'name': ctx.get(f'{side}_goalie'),
        'confirmed': ctx.get(f'{side}_goalie_confirmed'),
        'sv_pct': _f(ctx.get(f'{side}_goalie_sv_pct')),
        'gsaa': _f(ctx.get(f'{side}_goalie_gsaa')),
    }


def _build_casual_summary(ctx):
    """Short headline list for the app's collapsed view."""
    out = []
    hg, ag = ctx.get('home_goalie'), ctx.get('away_goalie')
    hc, ac = ctx.get('home_goalie_confirmed'), ctx.get('away_goalie_confirmed')
    if hg and ag:
        tag = 'confirmed' if (hc and ac) else 'projected'
        out.append(f'Goalies ({tag}): {ag} vs {hg}')
    tot = _f(ctx.get('close_total'))
    if tot is not None:
        out.append(f'Total {tot:g}')
    pl = _f(ctx.get('close_puckline'))
    if pl is not None:
        out.append(f'Puck line {pl:+g}')
    for side, label in (('home', ctx.get('home_team')), ('away', ctx.get('away_team'))):
        if ctx.get(f'{side}_back_to_back'):
            out.append(f'{label} on a back-to-back')
    return out[:4]


def build_struct(ctx):
    home, away = ctx.get('home_team'), ctx.get('away_team')
    struct = {
        'matchup': f'{away} @ {home}',
        'game_id': ctx.get('game_id'),
        'commence_time': ctx.get('commence_time'),
        'season': ctx.get('season'),
        'venue': ctx.get('venue'),
        'is_neutral_site': ctx.get('is_neutral_site'),
        'market': {
            'puckline': _f(ctx.get('close_puckline')),
            'total': _f(ctx.get('close_total')),
            'home_ml': ctx.get('home_ml_close'),
            'away_ml': ctx.get('away_ml_close'),
        },
        # No trained NHL margin model exists. Say so in the struct rather
        # than leaving empty keys the writer might fill with invention.
        'model': {
            'status': 'no trained NHL model — market-based read only',
            'projected_total': _f(ctx.get('projected_total')),
            'projected_home_ml': ctx.get('projected_home_ml'),
            'projected_home_wp': _f(ctx.get('projected_home_wp')),
            'elo_home': _f(ctx.get('elo_home')),
            'elo_away': _f(ctx.get('elo_away')),
        },
        'goalies': {'home': _goalie_block(ctx, 'home'),
                    'away': _goalie_block(ctx, 'away')},
        'team_rates': {
            'home': {
                'xgf_per60': _f(ctx.get('home_xgf_per60')),
                'xga_per60': _f(ctx.get('home_xga_per60')),
                'cf_5v5': _f(ctx.get('home_5v5_cf')),
                'high_danger_for': _f(ctx.get('home_high_danger_for')),
                'high_danger_against': _f(ctx.get('home_high_danger_against')),
                'pp_pct': _f(ctx.get('home_pp_pct')),
                'pk_pct': _f(ctx.get('home_pk_pct')),
            },
            'away': {
                'xgf_per60': _f(ctx.get('away_xgf_per60')),
                'xga_per60': _f(ctx.get('away_xga_per60')),
                'cf_5v5': _f(ctx.get('away_5v5_cf')),
                'high_danger_for': _f(ctx.get('away_high_danger_for')),
                'high_danger_against': _f(ctx.get('away_high_danger_against')),
                'pp_pct': _f(ctx.get('away_pp_pct')),
                'pk_pct': _f(ctx.get('away_pk_pct')),
            },
        },
        'situational': {
            'home_rest_days': ctx.get('home_rest_days'),
            'away_rest_days': ctx.get('away_rest_days'),
            'home_back_to_back': ctx.get('home_back_to_back'),
            'away_back_to_back': ctx.get('away_back_to_back'),
            'away_consecutive_road_games': ctx.get('away_consecutive_road_games'),
        },
        'confluence': {
            'net': ctx.get('signal_confluence_net'),
            'breakdown': ctx.get('signal_confluence_breakdown'),
        },
        'primary_play': ctx.get('primary_play'),
        'sweat': {'score': ctx.get('sweat_score'), 'tier': ctx.get('sweat_tier')},
        'meta': {
            'game_date': _et_date_from_utc(ctx.get('commence_time')) or ctx.get('game_date'),
            'game_has_not_been_played': True,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sport': 'NHL',
        },
    }
    struct['casual_summary'] = _build_casual_summary(ctx)
    return struct


def render_prompt(templates, struct):
    ss = struct['sweat'].get('score')
    tier = struct['sweat'].get('tier') or '—'
    confidence_tier = f'{tier} — sweat {ss}/100 (market + goalie lens)'
    context_block = (
        'NHL GAME CONTEXT (authoritative — analyze this, do not search for scores):\n'
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
        .replace('{commence_time_et}', str(struct.get('commence_time') or 'soon'))
        .replace('{sport}', 'NHL')
        .replace('{sweat_score}', str(ss or '—'))
        .replace('{sweat_tier_label}', tier)
        .replace('{spread_str}', str(m.get('puckline') or 'N/A'))
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
        print('  ⚠ ANTHROPIC_API_KEY missing — cannot generate')
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


def write_cache(game_id: str, narrative: str, struct: dict) -> bool:
    payload = {
        'cache_key': f'game_read_{game_id}_{today_et()}',
        'game_id': game_id,
        'sport': 'NHL',
        'narrative': narrative or '',
        'data': struct,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport',
        headers=SB_WRITE, json=payload, timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f'    ⚠ cache write {r.status_code}: {r.text[:140]}')
        return False
    return True


def run(force: bool = False, limit: Optional[int] = None) -> None:
    print(f'=== NHL game reads · {today_et()} ===')
    templates = load_templates()
    if not templates:
        print('  ✗ templates unavailable — abort')
        return
    games = fetch_upcoming_games()
    print(f'  upcoming games: {len(games)}')
    if not games:
        print('  (offseason or no nhl_game_context rows yet)')
        return
    if limit:
        games = games[:limit]

    written = skipped = reads = 0
    for ctx in games:
        matchup = f"{ctx.get('away_team')} @ {ctx.get('home_team')}"
        struct = build_struct(ctx)
        narrative = call_claude(render_prompt(templates, struct))
        if narrative is None:
            print(f'  ✗ {matchup}: claude failed')
            skipped += 1
            continue
        if write_cache(ctx['game_id'], narrative, struct):
            written += 1
        else:
            skipped += 1
        parsed = parse_synthesis(narrative)
        if parsed.get('short_read'):
            # Game date from the ET kickoff, never the run date and never
            # the UTC slice. NHL plays at night so both of those are wrong
            # for most of the schedule.
            gd = (_et_date_from_utc(ctx.get('commence_time'))
                  or ctx.get('game_date') or today_et())
            upsert_jerry_read(
                sport='NHL', game_id=ctx['game_id'], game_date=gd,
                struct=struct, parsed=parsed, narrative=narrative,
                prompt_version='nhl_game_read_v1_2026-09-24',
            )
            reads += 1
        else:
            print(f'  ⚠ {matchup}: no SHORT section parsed — '
                  f'jerry_reads not written')
    print(f'\n✓ cache {written} · jerry_reads {reads} · skipped {skipped}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    run(force=args.force, limit=args.limit)


if __name__ == '__main__':
    main()
