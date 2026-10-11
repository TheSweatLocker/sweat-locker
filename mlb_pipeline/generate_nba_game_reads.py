"""
NBA game reads — server-side Jerry.

══ 2026-10-10 · B74c · NBA HAD NO JERRY READ AT ALL ══
Andy asked for Jerry's by-game record in the receipts tab "across every
sport", and NBA published nothing. The cause was not a wiring gap:
`jerry_reads` held ZERO NBA rows (MLB 812, NCAAF 499, NHL 163, NFL 127,
UFC 88, NBA 0) because THIS FILE DID NOT EXIST. Generators existed for MLB,
NCAAB, NCAAF, NFL, NHL and UFC, with no shared core — each standalone,
320-2,908 lines. NBA opens 2026-10-21.

Modelled on generate_ncaab_game_reads.py (the other basketball sport) and
mapped onto what nba_game_context actually carries. Measured on its 81 rows
before a line of this was written:

  FULLY POPULATED (the basis for a read): home/away off_rating, def_rating,
  net_rating, pace · elo_home/elo_away · projected_spread · projected_total
  · projected_home_wp · sweat_score · sweat_tier · signal_confluence_net ·
  season_type · neutral_site · venue
  PARTIAL: records 77-78/81 · rest_days + b2b 28-29/81 · ATS/ML/OU L10
  26-27/81 · h2h_last5 26/81 · close_spread/close_total/ML 18/81 (the odds
  window is shorter than the schedule horizon) · primary_play 11/81
  EMPTY: mc_probabilities · oddscrowd_snapshot · open_spread/open_total ·
  injuries · season ATS splits

So the read leans on efficiency + elo + projections, and degrades gracefully
where the market or situational blocks are absent rather than printing
"N/A" into prose.

⚠ SIGN CONVENTION — VERIFIED, NOT ASSUMED, AND IT DIFFERS FROM NCAAB.
generate_ncaab_game_reads documents NCAAB as "proj_spread positive = home
fav, close_spread negative = home fav" and therefore adds them. NBA is NOT
that. Measured on the 18 rows carrying both:

    corr(projected_home_wp, projected_spread) = -0.996

A near-perfect NEGATIVE correlation with the home win probability means
projected_spread is NEGATIVE when the HOME team is favoured — the same
convention as close_spread. Confirmed case by case:

    Dallas @ Houston   proj -12.9  close -8.5  homeML -310  wp 0.86
    Charlotte @ Brooklyn proj +7.2  close +3.5  homeML +130  wp 0.26

So the model-vs-market edge is a SUBTRACTION here:
    home_edge = close_spread - projected_spread
(-8.5 - -12.9 = +4.4 → our number likes the home side by 4.4 more than the
market). Copying NCAAB's addition would have named the wrong favourite on
every game, which is the class of error that produced
project_close_spread_sign_bug_914 and the Red River inversion.

Writes {narrative, struct} to jerry_cache keyed
game_read_<game_id>_<ET date> with sport='NBA' (UPPERCASE — the app queries
by sport per sport_registry, and the NCAAB generator shipped lowercase once
and needed a one-time UPDATE), and dual-writes the structured pick to
jerry_reads so the surface_records rollup (pick_game_read) can grade it.

Usage: python generate_nba_game_reads.py [--force] [--limit N] [--dry-run]
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

#: NBA plays nightly, so the read horizon is tight like NCAAB's rather than
#: NCAAF's weekly 10. Beyond ~5 days there are no odds anyway (close_spread
#: is populated on 18 of 81 rows precisely because the odds window is
#: shorter than the schedule).
HORIZON_DAYS = 5


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
    """NBA has its own active game_read_rules row — verified 2026-10-10, so
    unlike NCAAB this needs no cross-sport fallback."""
    rows = sb_get('prompt_templates', {
        'name': 'in.(game_read_wrapper,game_read_universal,game_read_rules)',
        'is_active': 'is.true',
        'select': 'name,sport,template',
    })
    out = {(r['name'], r['sport']): r['template'] for r in rows}
    wrapper = out.get(('game_read_wrapper', 'ALL'))
    universal = out.get(('game_read_universal', 'ALL'))
    rules = out.get(('game_read_rules', 'NBA')) or out.get(('game_read_rules', 'NCAAB'))
    if not (wrapper and universal and rules):
        print(f'  ⚠ missing prompt_templates rows — have: {list(out.keys())}')
        return None
    return {'wrapper': wrapper, 'universal': universal, 'rules': rules}


def fetch_upcoming_games():
    """nba_game_context rows for today + HORIZON_DAYS."""
    today = today_et()
    horizon = (datetime.now(timezone.utc) + timedelta(days=HORIZON_DAYS)
               - timedelta(hours=4)).strftime('%Y-%m-%d')
    url = (f'{SUPABASE_URL}/rest/v1/nba_game_context'
           f'?game_date=gte.{today}&game_date=lte.{horizon}'
           f'&select=*&order=sweat_score.desc.nullslast&limit=80')
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


def home_edge(ctx):
    """Model-vs-market edge in HOME points. Positive = our number likes the
    home side by that much more than the market does.

    SUBTRACTION, not addition — see the sign-convention block in the module
    docstring. Both projected_spread and close_spread are negative-means-
    home-favoured for NBA.
    """
    proj = _f(ctx.get('projected_spread'))
    close = _f(ctx.get('close_spread'))
    if proj is None or close is None:
        return None
    return close - proj


def _build_casual_summary(ctx):
    """Lean 4-headline summary for the app's collapsed view."""
    headlines = []
    close_sp = _f(ctx.get('close_spread'))
    close_tot = _f(ctx.get('close_total'))
    proj_tot = _f(ctx.get('projected_total'))
    home = ctx.get('home_team') or 'Home'
    away = ctx.get('away_team') or 'Away'
    h_net = _f(ctx.get('home_net_rating'))
    a_net = _f(ctx.get('away_net_rating'))

    # Net rating gap — the NBA analogue of NCAAB's adj_em headline.
    if h_net is not None and a_net is not None:
        gap = h_net - a_net
        stronger = home if gap > 0 else away
        headlines.append((6, f'✓ Net rating favors {stronger} by '
                             f'{abs(gap):.1f}'))

    # Model vs market. Negative-means-home-fav on BOTH sides, so subtract.
    edge = home_edge(ctx)
    if edge is not None and abs(edge) >= 2:
        fav = home if edge > 0 else away
        headlines.append((7, f'⚡ Model likes {fav} by {abs(edge):.1f} more '
                             f'than market'))

    if proj_tot is not None and close_tot is not None:
        tot_edge = proj_tot - close_tot
        if abs(tot_edge) >= 4:
            side = 'OVER' if tot_edge > 0 else 'UNDER'
            headlines.append((5, f'📈 Total leans {side} ({tot_edge:+.1f})'))

    # Pace is the NBA total driver worth surfacing when both are known.
    h_pace, a_pace = _f(ctx.get('home_pace')), _f(ctx.get('away_pace'))
    if h_pace is not None and a_pace is not None:
        headlines.append((4, f'🏃 Pace {h_pace:.1f} / {a_pace:.1f}'))

    # Back-to-back is the NBA-specific situational flag (28-29/81 populated).
    if ctx.get('home_is_b2b'):
        headlines.append((6, f'😴 {home} on a back-to-back'))
    if ctx.get('away_is_b2b'):
        headlines.append((6, f'😴 {away} on a back-to-back'))

    pp = ctx.get('primary_play') or {}
    if pp.get('label'):
        headlines.append((8, f'🎯 {pp.get("tier", "PLAY")}: {pp["label"]}'))

    if close_sp is not None:
        # negative close_spread = home favoured
        disp = (f'{home} {close_sp:+.1f}' if close_sp < 0
                else f'{away} {-close_sp:+.1f}')
        headlines.append((3, f'📊 {disp}, total {close_tot or "N/A"}'))

    headlines.sort(key=lambda x: -x[0])
    return {
        'headlines': [h[1] for h in headlines[:4]],
        'bottom_line': (pp.get('sub') if pp
                        else 'Model tracking — no primary play yet'),
    }


def build_struct(ctx):
    home = ctx.get('home_team'); away = ctx.get('away_team')
    struct = {
        'matchup': f'{away} @ {home}',
        'game_id': ctx.get('game_id'),
        'commence_time': ctx.get('commence_time_utc'),
        'season': ctx.get('season'),
        'season_type': ctx.get('season_type'),
        'venue': ctx.get('venue'),
        'is_neutral_site': ctx.get('neutral_site'),
        'market': {
            'spread': _f(ctx.get('close_spread')),
            'total': _f(ctx.get('close_total')),
            'home_ml': ctx.get('home_ml_close'),
            'away_ml': ctx.get('away_ml_close'),
            'spread_convention': 'negative_close_spread_means_home_favored',
        },
        'model': {
            'projected_spread': _f(ctx.get('projected_spread')),
            'projected_total': _f(ctx.get('projected_total')),
            'projected_home_wp': _f(ctx.get('projected_home_wp')),
            # Stated explicitly for the LLM because it is the OPPOSITE of
            # NCAAB's and a read that mixes them names the wrong favourite.
            'projected_spread_convention':
                'negative_projected_spread_means_home_favored',
            'home_edge_vs_market': home_edge(ctx),
            'home_edge_convention':
                'close_spread_minus_projected_spread; positive favors HOME',
        },
        'efficiency': {
            'home': {
                'off_rating': _f(ctx.get('home_off_rating')),
                'def_rating': _f(ctx.get('home_def_rating')),
                'net_rating': _f(ctx.get('home_net_rating')),
                'pace':       _f(ctx.get('home_pace')),
                'elo':        _f(ctx.get('elo_home')),
            },
            'away': {
                'off_rating': _f(ctx.get('away_off_rating')),
                'def_rating': _f(ctx.get('away_def_rating')),
                'net_rating': _f(ctx.get('away_net_rating')),
                'pace':       _f(ctx.get('away_pace')),
                'elo':        _f(ctx.get('elo_away')),
            },
        },
        'situational': {
            'home_rest_days': ctx.get('home_rest_days'),
            'away_rest_days': ctx.get('away_rest_days'),
            'home_is_b2b': ctx.get('home_is_b2b'),
            'away_is_b2b': ctx.get('away_is_b2b'),
            'home_record': ctx.get('home_record'),
            'away_record': ctx.get('away_record'),
            'home_ats_last10': ctx.get('home_ats_last10'),
            'away_ats_last10': ctx.get('away_ats_last10'),
            'home_covers_as_fav_pct': ctx.get('home_covers_as_fav_pct'),
            'away_covers_as_dog_pct': ctx.get('away_covers_as_dog_pct'),
        },
        'h2h_last5': {
            'games_played': ctx.get('h2h_last5_games_played'),
            'avg_margin': _f(ctx.get('h2h_last5_avg_margin')),
            'avg_total': _f(ctx.get('h2h_last5_avg_total')),
            'home_wins': ctx.get('h2h_last5_home_wins'),
            'overs': ctx.get('h2h_last5_overs'),
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
            'game_date': ctx.get('game_date') or today_et(),
            'game_has_not_been_played': True,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sport': 'NBA',
            # Honest about what is missing so the read does not invent it.
            'absent_blocks': [k for k, present in (
                ('monte_carlo', bool(ctx.get('mc_probabilities'))),
                ('money_flow', bool(ctx.get('oddscrowd_snapshot'))),
                ('injuries', bool(ctx.get('home_starters_out')
                                  or ctx.get('away_starters_out'))),
                ('market_lines', _f(ctx.get('close_spread')) is not None),
            ) if not present],
        },
    }
    struct['casual_summary'] = _build_casual_summary(ctx)
    return struct


def render_prompt(templates, struct):
    ss = struct['sweat'].get('score')
    tier = struct['sweat'].get('tier') or '—'
    confidence_tier = (f'{tier} — sweat {ss}/100 '
                       f'(net rating + pace + elo lens)')
    context_block = (
        'NBA GAME CONTEXT (authoritative — analyze this, do not search for '
        'scores):\n' + json.dumps(struct, indent=2, default=str)
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
        .replace('{sport}', 'NBA')
        .replace('{sweat_score}', str(ss or '—'))
        .replace('{sweat_tier_label}', tier)
        .replace('{spread_str}', str(m.get('spread') if m.get('spread') is not None else 'N/A'))
        .replace('{total_str}', str(m.get('total') if m.get('total') is not None else 'N/A'))
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
    # Routed through anthropic_guard like every other generator: fatal vs
    # transient, and the provider seam. NBA opens with ZERO graded history,
    # so a silent LLM failure would ship a brand-new sport with blank reads —
    # exactly the reason the NCAAB generator was wired this way.
    from anthropic_guard import call as _llm_call, FatalLLMError
    try:
        return _llm_call(prompt, model=MODEL, max_tokens=800,
                         timeout=30, label='nba_game_reads')
    except FatalLLMError:
        raise
    except Exception as e:
        print(f'  ⚠ claude call failed: {e}')
        return None


def write_cache(game_id: str, narrative: str, struct: dict) -> bool:
    payload = {
        'cache_key': f'game_read_{game_id}_{today_et()}',
        'game_id': game_id,
        'sport': 'NBA',
        'narrative': narrative or '',
        'data': struct,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport',
        headers=SB_WRITE, json=payload, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def run(force: bool = False, limit: Optional[int] = None,
        dry_run: bool = False) -> None:
    print(f'=== NBA game reads · {today_et()}'
          f'{" (DRY)" if dry_run else ""} ===')
    templates = load_templates()
    if not templates:
        print('  ✗ templates unavailable — abort '
              '(populate prompt_templates first)')
        return
    games = fetch_upcoming_games()
    print(f'  upcoming games: {len(games)}')
    if not games:
        print('  (preseason gap or no nba_game_context rows in the horizon)')
        return
    if limit:
        games = games[:limit]

    written = skipped = 0
    for ctx in games:
        struct = build_struct(ctx)
        if dry_run:
            cs = struct['casual_summary']
            print(f'\n  [DRY] {struct["matchup"]}  '
                  f'sweat={struct["sweat"]["score"]} '
                  f'edge={struct["model"]["home_edge_vs_market"]}')
            for h in cs['headlines']:
                print(f'        {h}')
            if struct['meta']['absent_blocks']:
                print(f'        absent: {struct["meta"]["absent_blocks"]}')
            continue
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if narrative is None:
            print(f'  ✗ claude failed for {ctx.get("game_id")}')
            skipped += 1
            continue
        if write_cache(ctx['game_id'], narrative, struct):
            written += 1
        else:
            skipped += 1

        # Dual-write the structured pick so pick_game_read can roll it up.
        # game_date comes from the CONTEXT row, not today_et() — stamping the
        # run date instead of the game date is what made the grader miss
        # everything generated ahead of game day (project_ncaaf_grading_gap_908).
        parsed = parse_synthesis(narrative)
        if parsed.get('short_read'):
            upsert_jerry_read(
                sport='NBA', game_id=ctx['game_id'],
                game_date=ctx.get('game_date') or today_et(),
                struct=struct, parsed=parsed, narrative=narrative,
                prompt_version='nba_game_read_v1_2026-10-10',
            )

    if dry_run:
        print(f'\n[DRY] {len(games)} game(s) rendered, nothing written')
    else:
        print(f'\n✓ wrote {written} · skipped {skipped}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(force=args.force, limit=args.limit, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NBA')
    except ImportError:
        pass
    main()
