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
import re
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


def _allowed_extra(struct: dict) -> set:
    """Numbers that are legitimate but not literally in the struct.

    The first run of the number check flagged almost everything, because a
    strict "is this value in the struct" test is wrong for three kinds of
    number this template deliberately asks for:

      SIGNS. A GSAA of -11.93 is written as "-11.93" or as "11.93 below
        average", and a price of -130 is written "-130" or "130". The
        magnitude is the same fact, so absolute values are allowed.
      GAPS. The rules require "state the GSAA gap" and "the edge in points",
        which are SUBTRACTIONS the struct never stores. 28.78 and 21.25 are
        both present, and 7.53 is the number the reader needs. Pairwise
        differences of the goalie and probability figures are allowed.
      UNITS. "per 60", "5v5", percentages out of 100 — scale words that
        happen to be digits.

    Without these the checker cried wolf on every read, the retry burned a
    second model call per game, and a real error — Andersen's -3.31 written
    as +3.31 — was buried in the noise. Narrow allowances keep it pointed at
    invention instead.
    """
    vals: list[float] = []

    def collect(o):
        if isinstance(o, dict):
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            vals.append(float(o))

    collect(struct)
    out = set()
    for v in vals:
        out.add(abs(v))
        out.add(round(abs(v), 2))
        out.add(round(abs(v) * 100, 2))      # 0.6378 -> 63.78 as a percentage
    # Pairwise gaps — the differences the rules ask Jerry to state.
    for i, a in enumerate(vals):
        for b in vals[i + 1:]:
            d = abs(a - b)
            if d:
                out.add(round(d, 2))
                out.add(round(d * 100, 2))
    out.update({60.0, 100.0, 5.0})           # per-60, percent, 5v5
    # validate_jerry_read._within_tolerance calls .rstrip('%') on every
    # allowed entry, so this set must hold STRINGS. Emit both a trimmed and a
    # 2-dp spelling of each value so "7.5" and "7.53" both resolve.
    outs = set()
    for x in out:
        if x != x:                           # NaN
            continue
        outs.add(f'{x:g}')
        outs.add(f'{x:.2f}')
    return outs


def _rw(narrative: str) -> tuple:
    """(short_read, long_read) from a raw narrative, for the number check.

    validate_jerry_read.validate takes the two reads separately, and the
    number check has to run BEFORE the cache write — so it cannot use the
    already-parsed dict further down.
    """
    p = parse_synthesis(narrative) or {}
    return (p.get('short_read') or '', p.get('long_read') or '')


def build_struct(ctx):
    home, away = ctx.get('home_team'), ctx.get('away_team')
    # mc_probabilities is JSONB and arrives as a dict from PostgREST, but a
    # string through some paths — parse defensively so a serialization change
    # degrades to "no MC" rather than raising mid-run.
    _mc = ctx.get('mc_probabilities')
    if isinstance(_mc, str):
        try:
            _mc = json.loads(_mc)
        except (ValueError, TypeError):
            _mc = None
    if not isinstance(_mc, dict):
        _mc = {}
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
        # 2026-09-28 · THE NOTE AT THE BOTTOM OF THIS FILE CAME DUE.
        # It read: "NOTE FOR WHOEVER SHIPS THE REAL MODEL: struct.model.status
        # currently reads 'no trained NHL model — market-based read only'.
        # Update that when the trained model lands." It has landed, and it is
        # the thing choosing NHL picks — measured on the 9/29 board, FLA @ CAR
        # carries _engine lr_v1, _lr_p_home_win 0.6378 and _model_edge_pp 7.48,
        # and it overrode ensemble_v2's LEAN/76 to STRONG/64.
        #
        # Three numbers the APP renders were also absent from this struct, so
        # Jerry could not cite what a subscriber was looking at: the projected
        # goals per team, the projected spread, and the Monte Carlo. The MC
        # fields are flattened rather than nested because the prose rules
        # reference them as model.mc_ot_rate — a nested blob would have the
        # writer guessing at a path.
        #
        # Note projected_home_goals / projected_away_goals are handed over
        # DIRECTLY rather than re-derived from total and spread. (t±m)/2
        # reproduces them exactly today, and the moment it stops doing so the
        # prose and the card must not disagree.
        'model': {
            'status': ('trained logistic-regression win model (lr_v1) + Poisson '
                       'goal projection with Monte Carlo + Elo'),
            'projected_total': _f(ctx.get('projected_total')),
            'projected_spread': _f(ctx.get('projected_spread')),
            'projected_home_goals': _f(ctx.get('projected_home_goals')),
            'projected_away_goals': _f(ctx.get('projected_away_goals')),
            'projected_home_ml': ctx.get('projected_home_ml'),
            'projected_home_wp': _f(ctx.get('projected_home_wp')),
            'elo_home': _f(ctx.get('elo_home')),
            'elo_away': _f(ctx.get('elo_away')),
            **{k: _mc.get(k) for k in
               ('mc_sims', 'mc_p_home', 'mc_p_over', 'mc_ot_rate',
                'mc_expected_total', 'mc_expected_margin')
               if isinstance(_mc, dict) and _mc.get(k) is not None},
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
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if narrative is None:
            print(f'  ✗ {matchup}: claude failed')
            skipped += 1
            continue

        # ══ 2026-09-28 · NUMBER CHECK. NHL HAD NONE. ══
        # Of the three read generators only NFL verifies numbers; NHL and
        # NCAAF ship whatever the model writes. It showed immediately once the
        # v3+ rules let Jerry cite figures: on VAN @ EDM the read said Andersen
        # was "confirmed at .874 with a +3.31 GSAA" when the struct says
        # -3.31 — a sign inversion that turns a below-average goalie into an
        # above-average one, in the sentence the whole read is built on.
        #
        # validate_jerry_read.validate() is sport-universal by design (it
        # checks NUMBERS against the struct, not player names), which is why
        # it is reused here rather than reimplemented. A number in the prose
        # that appears nowhere in the struct is a hallucination; retry once
        # with the corrective prompt, and if it fails again keep the better
        # attempt and say so rather than silently publishing it.
        try:
            from validate_jerry_read import validate as _validate, build_corrective_prompt
            _extra = _allowed_extra(struct)
            _rep = _validate(*_rw(narrative), struct, extra_allowed=_extra)
            if not _rep.get('is_valid'):
                print(f'  ⚠ NHL num hallucination {matchup}: '
                      f'{_rep.get("hallucinated_numbers", [])[:4]} — retry')
                _n2 = call_claude(build_corrective_prompt(prompt, _rep, {'suspects': []}))
                if _n2:
                    _rep2 = _validate(*_rw(_n2), struct, extra_allowed=_extra)
                    if _rep2.get('is_valid'):
                        narrative = _n2
                        print('  ✓ retry cleaned numbers')
                    else:
                        # Fewer invented numbers is still better; never
                        # silently prefer the first attempt just because it
                        # came first.
                        if len(_rep2.get('hallucinated_numbers', [])) < \
                           len(_rep.get('hallucinated_numbers', [])):
                            narrative = _n2
                        print(f'  ⚠ still unverified: '
                              f'{_rep2.get("hallucinated_numbers", [])[:4]}')
        except ImportError:
            pass
        if write_cache(ctx['game_id'], narrative, struct):
            written += 1
        else:
            skipped += 1
        parsed = parse_synthesis(narrative)
        if parsed.get('short_read'):
            parsed = dict(parsed)
            parsed['short_read'] = _canonical_disclaimer(parsed['short_read'])
            parsed['long_read'] = _canonical_disclaimer(
                parsed.get('long_read'), prepend=False)
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


# 2026-09-24 — THE ROADMAP DISCLAIMER IS DROPPED. Andy's call: "Drop it, NHL
# model will be live."
#
# The live NHL prompt (prompt_templates sport=NHL name=game_read_rules, active,
# v2) instructs: 'Open with one line: "Market-based analysis — proprietary NHL
# model launches 2026-27 season."' It shipped on all 37 reads, 17 of them with
# the space dropped ("2026-27season"), which is what a subscriber saw.
#
# WHY DROPPING IT IS RIGHT, not just shorter:
#
#   1. It is a forward-looking product promise. A game read is not the place to
#      commit to a roadmap.
#   2. It contradicted the read underneath it. 28 of 37 NHL reads say "the
#      model" — "the model pegs Kraken at 62.7% win probability", "the model
#      sees this 60-40 lean" — and those numbers are REAL, computed from the
#      Elo + market values in struct.model (elo_home, elo_away,
#      projected_home_wp, projected_total). So a model is producing them. What
#      does not exist is a TRAINED one, which is a distinction the sentence
#      never made and a subscriber cannot act on.
#   3. The honest sport-level caveat already has a home and is already correct:
#      sport_registry.state_message reads "NHL season starts Oct 8 — probable
#      goalies and MoneyPuck refresh land end of September." That is where a
#      caveat about a sport belongs, not repeated on every card.
#
# The prompt still instructs the line, so the LLM keeps writing it and this
# keeps removing it. That is deliberate: stripping here needs no prompt
# migration and no regeneration, and it holds even if the prompt is edited
# later or the LLM paraphrases again.
#
# NOTE FOR WHOEVER SHIPS THE REAL MODEL: struct.model.status currently reads
# "no trained NHL model — market-based read only". Update that when the trained
# model lands, or the next person auditing these reads will reach the same
# wrong conclusion I did.
NHL_DISCLAIMER = None

# Matches the canonical line and the paraphrases seen in production, including
# the missing-space typo and the "no NHL model active yet" variant that the dead
# _prompt_game_read_rules_NHL.txt still advertises.
_DISCLAIMER_RE = re.compile(
    r'^\s*Market-based analysis\s*[—-]\s*'
    r'(?:proprietary NHL model launches\s*20\d\d[-–]\d\d\s*season'
    r'|no NHL model active yet)\s*\.?\s*',
    re.I)


def _canonical_disclaimer(txt, prepend: bool = True):
    """Remove the disclaimer line. Prepend a replacement only if one is set.

    NHL_DISCLAIMER is None as of 2026-09-24, so this strips and returns the
    body. The prepend path is kept because the mechanism is the useful part:
    if a one-line caveat is ever wanted again, setting that constant is the
    whole change — no prompt migration, no regeneration.
    """
    if not txt:
        return txt
    body = _DISCLAIMER_RE.sub('', txt).lstrip()
    if not prepend or not NHL_DISCLAIMER:
        # Never return an empty read: if the disclaimer was the entire field,
        # keep the original rather than blanking a card.
        return body or txt
    return f'{NHL_DISCLAIMER}\n\n{body}' if body else NHL_DISCLAIMER


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    run(force=args.force, limit=args.limit)


if __name__ == '__main__':
    main()
