"""Analyst-writeup facts + hallucination cross-referencer.

Two responsibilities:
  1. build_provided_facts(ctx) — assembles a sourced-fact dict from
     team_situational_records + team_stats_rolling + nfl_injuries, so
     the LLM never has to reason about (a) NFL spread-sign conventions
     or (b) which player is out on which team.
  2. scan_hallucinated_stats(prose, facts) — cross-references every
     stat claim in a generated write-up against the facts we provided.
     Three buckets: confirmed_mismatch (real hallucinations),
     unverifiable (parser couldn't resolve), verified (matched).

Used by generate_nfl_game_reads when the analyst_writeup_v1 feature
flag is on. Falls back to current quant-forward path when off, or when
Layer F detects unfixable hallucinations after one retry.

See mlb_pipeline/_analyst_writeup_poc.py for the standalone canary
that validated this end-to-end on GB@NYJ 2026-09-16.
"""
from __future__ import annotations
import os, re, json
from typing import Optional
import requests

SB  = os.environ.get('SUPABASE_URL', '')
KEY = os.environ.get('SUPABASE_KEY', '')
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


# ═══ SOURCED-FACT ASSEMBLY ═══════════════════════════════════════════

def _situational_records(sport: str, team: str, current_season: int) -> dict:
    """Pull (market, filter) → 'W-L(-P)' string per team from
    team_situational_records. Blend-kill migration (20260916a) already
    filters this view to current-season-only for NFL/NCAAF."""
    try:
        r = requests.get(f'{SB}/rest/v1/team_situational_records',
            params={'sport': f'eq.{sport}', 'team': f'eq.{team}',
                    'select': 'market,filter,wins,losses,pushes'},
            headers=H, timeout=10)
        rows = r.json() if r.status_code == 200 and r.text.strip().startswith('[') else []
    except Exception:
        rows = []
    out = {}
    for row in rows:
        key = f"{row.get('market')}_{row.get('filter')}"
        w, l, p = row.get('wins', 0), row.get('losses', 0), row.get('pushes', 0)
        out[key] = f"{w}-{l}" + (f"-{p}" if p else '')
    return out


def _team_stats_rank(sport: str, team: str) -> dict:
    """Pull stat_key → {value, rank} per team from team_stats_rolling."""
    try:
        r = requests.get(f'{SB}/rest/v1/team_stats_rolling',
            params={'sport': f'eq.{sport}', 'team': f'eq.{team}',
                    'select': 'stat_key,raw_value,rank,league_size'},
            headers=H, timeout=10)
        rows = r.json() if r.status_code == 200 and r.text.strip().startswith('[') else []
    except Exception:
        rows = []
    out = {}
    for row in rows:
        rk = row.get('rank')
        ls = row.get('league_size') or 32
        val = row.get('raw_value')
        out[row['stat_key']] = {'value': val,
                                 'rank': f'{rk}/{ls}' if rk else None}
    return out


def _notable_injuries(team: str, season: int, week: Optional[int]) -> list:
    """NFL-only. Pulls Q/D/O/IR injuries for the current week."""
    if week is None: return []
    try:
        r = requests.get(f'{SB}/rest/v1/nfl_injuries',
            params={'team': f'eq.{team}', 'season': f'eq.{season}',
                    'week': f'eq.{week}',
                    'injury_status': 'in.(Questionable,Doubtful,Out,IR)',
                    'select': 'player_name,position,injury_status,body_part'},
            headers=H, timeout=10)
        rows = r.json() if r.status_code == 200 and r.text.strip().startswith('[') else []
    except Exception:
        rows = []
    return [{'name': x.get('player_name'), 'pos': x.get('position'),
             'status': x.get('injury_status'), 'body_part': x.get('body_part')}
            for x in rows if x.get('injury_status')]


def build_provided_facts_mlb(ctx: dict) -> dict:
    """Assemble PROVIDED_FACTS for an MLB game.

    2026-09-16: MLB analyst v1.1 port. Same architecture as NFL —
    humanize market side, pull sourced pitcher / bullpen / lineup /
    park / weather / umpire facts so the LLM cites from a bounded set
    and Layer F can cross-reference every claim.

    Unlike NFL, MLB analyst facts center on the starting-pitcher matchup
    (xERA, K/9, L3 form, splits vs opposing side) + bullpen edge, since
    those move the needle more than team-level ATS records in baseball.
    """
    if not ctx: return {}
    home = ctx.get('home_team') or ''
    away = ctx.get('away_team') or ''

    # Humanize market side — MLB spread sign convention is OPPOSITE NFL:
    # negative close_spread = home fav in MLB. (Documented in
    # project_close_spread_sign_bug_914.)
    cs = ctx.get('close_spread')
    ct = ctx.get('close_total')
    hml = ctx.get('home_ml_close') or ctx.get('close_home_ml')
    aml = ctx.get('away_ml_close') or ctx.get('close_away_ml')
    market: dict = {'close_total': ct}
    if hml is not None and aml is not None:
        # Favorite is whoever has more negative ML
        if hml < 0 and (aml is None or hml < aml):
            market['favorite'] = home
            market['favorite_ml'] = hml
            market['underdog'] = away
            market['underdog_ml'] = aml
        elif aml < 0 and (hml is None or aml < hml):
            market['favorite'] = away
            market['favorite_ml'] = aml
            market['underdog'] = home
            market['underdog_ml'] = hml

    pp = ctx.get('primary_play') or {}
    if isinstance(pp, str):
        try: pp = json.loads(pp)
        except Exception: pp = {}

    def _sp_block(side: str) -> dict:
        return {
            'name': ctx.get(f'{side}_pitcher'),
            'xera': ctx.get(f'{side}_sp_xera'),
            'era_last_3': ctx.get(f'{side}_pitcher_last_3_era'),
            'k_pct_last_3': ctx.get(f'{side}_pitcher_last_3_k_pct'),
            'first_inning_era': ctx.get(f'{side}_first_inning_era'),
            'first_inning_whip': ctx.get(f'{side}_first_inning_whip'),
            'vs_opp_team_era_career': ctx.get(f'{side}_pitcher_vs_team_era'),
            'vs_opp_team_avg_career': ctx.get(f'{side}_pitcher_vs_team_avg'),
            'vs_opp_team_k9_career': ctx.get(f'{side}_pitcher_vs_team_k_per_9'),
            'vs_opp_team_ip_career': ctx.get(f'{side}_pitcher_vs_team_ip'),
            'projected_ks': ctx.get(f'{side}_pitcher_projected_ks'),
            'projected_bb': ctx.get(f'{side}_pitcher_projected_bb'),
            'projected_hits': ctx.get(f'{side}_pitcher_projected_hits'),
            'projected_outs': ctx.get(f'{side}_pitcher_projected_outs'),
            'home_era': ctx.get(f'{side}_pitcher_home_era'),
            'away_era': ctx.get(f'{side}_pitcher_away_era'),
        }

    def _lineup_block(side: str) -> dict:
        return {
            'wrc_plus_season': ctx.get(f'{side}_wrc_plus'),
            'wrc_plus_vs_opp_hand': ctx.get(f'{side}_wrc_vs_opp_hand'),
            'wrc_proxy_l14': ctx.get(f'{side}_wrc_proxy_l14'),
            'barrel_pct_team': ctx.get(f'{side}_team_barrel_pct'),
        }

    return {
        'matchup': f'{away} @ {home}',
        'sport': 'MLB',
        'market': market,
        'primary_pick': {
            'label': pp.get('label'), 'tier': pp.get('tier'),
            'conviction': pp.get('conviction'), 'type': pp.get('type'),
            'side': pp.get('side'),
        },
        'model_projections': {
            'projected_spread': ctx.get('projected_spread'),
            'projected_total': ctx.get('projected_total'),
        },
        'starting_pitchers': {home: _sp_block('home'), away: _sp_block('away')},
        'bullpens': {
            home: {'era': ctx.get('home_bullpen_era'),
                   'relievers_3d': ctx.get('home_bp_relievers_3d')},
            away: {'era': ctx.get('away_bullpen_era'),
                   'relievers_3d': ctx.get('away_bp_relievers_3d')},
        },
        'lineups': {home: _lineup_block('home'), away: _lineup_block('away')},
        'venue': {
            'park_run_factor': ctx.get('park_run_factor'),
            'temperature': ctx.get('temperature'),
            'wind_speed': ctx.get('wind_speed'),
            'wind_direction': ctx.get('wind_direction'),
            'wind_blowing_in': ctx.get('wind_blowing_in'),
        },
        'umpire': {
            'name': ctx.get('umpire'),
            'note': ctx.get('umpire_note'),
        },
        'team_state': {
            home: {'days_rest': ctx.get('home_days_rest'),
                   'streak': ctx.get('home_streak')},
            away: {'days_rest': ctx.get('away_days_rest'),
                   'streak': ctx.get('away_streak')},
        },
        'sample_note': ('MLB pitcher xERA and L3 form are the reliable '
                        'signals; team-level records are noisy in-season.'),
    }


def build_provided_facts(ctx: dict, sport: str = 'NFL',
                          current_season: Optional[int] = None) -> dict:
    """Assemble the PROVIDED_FACTS payload the LLM should cite from.

    NFL-focused today; NCAAF wire-up follows same schema minus
    injuries block (no NCAAF injury feed yet).
    """
    if not ctx: return {}
    home = ctx.get('home_team') or ''
    away = ctx.get('away_team') or ''
    season = current_season or int(ctx.get('season') or 2026)
    week = ctx.get('week')

    # Humanize market side BEFORE the LLM sees it — kills the NFL sign
    # convention hallucination class (project_close_spread_sign_bug_914).
    cs = ctx.get('close_spread')
    ct = ctx.get('close_total')
    market: dict = {'close_total': ct}
    if cs is not None:
        # NFL: positive close_spread = home fav.
        if cs > 0:
            market['favorite'] = home
            market['favorite_spread'] = -abs(cs)
            market['underdog'] = away
            market['underdog_spread'] = +abs(cs)
        elif cs < 0:
            market['favorite'] = away
            market['favorite_spread'] = -abs(cs)
            market['underdog'] = home
            market['underdog_spread'] = +abs(cs)
        else:
            market['favorite'] = None
            market['underdog'] = None
            market['pickem_note'] = 'Line is pk (no favorite).'

    pp = ctx.get('primary_play') or {}
    if isinstance(pp, str):
        try: pp = json.loads(pp)
        except Exception: pp = {}

    facts = {
        'matchup': f'{away} @ {home}',
        'market': market,
        'primary_pick': {
            'label': pp.get('label'),
            'tier': pp.get('tier'),
            'conviction': pp.get('conviction'),
            'type': pp.get('type'),
            'side': pp.get('side'),
        },
        'model_projections': {
            'v3_spread': ctx.get('projected_spread'),
            'v3_total':  ctx.get('projected_total'),
            'v4_spread': ctx.get('v4_spread'),
            'v4_total':  ctx.get('v4_total'),
        },
        'situational_records': {
            home: _situational_records(sport, home, season),
            away: _situational_records(sport, away, season),
        },
        'team_stats_rank': {
            home: _team_stats_rank(sport, home),
            away: _team_stats_rank(sport, away),
        },
        'game_context': {
            'roof': ctx.get('roof'),
            'wind': ctx.get('wind'),
            'temp': ctx.get('temp'),
            'div_game': ctx.get('div_game'),
            'home_rest': ctx.get('home_rest'),
            'away_rest': ctx.get('away_rest'),
            'cohort_tags': ctx.get('cohort_tags'),
        },
        'sample_note': ('Week 1-2 — thin per-team sample (n=1..2). '
                        'Cite records but flag the small-n honestly.'),
    }

    if sport == 'NFL':
        facts['injuries_notable'] = {
            home: _notable_injuries(home, season, week),
            away: _notable_injuries(away, season, week),
        }

    return facts


def build_facts(ctx: dict, sport: str) -> dict:
    """Sport-dispatched facts builder. NFL uses the team-stat/rank
    architecture; MLB uses pitcher / bullpen / lineup / park."""
    sport = (sport or 'NFL').upper()
    if sport == 'MLB':
        return build_provided_facts_mlb(ctx)
    return build_provided_facts(ctx, sport=sport)


# ═══ LAYER F — STRICT CROSS-REFERENCE ════════════════════════════════

NUMBER_ORDINAL = {'first':1,'second':2,'third':3,'fourth':4,'fifth':5,
                  'sixth':6,'seventh':7,'eighth':8,'ninth':9,'tenth':10,
                  'eleventh':11,'twelfth':12}

# Prose phrase → team_stats_rolling stat_key(s). Longest match wins per pattern.
# 2026-09-16: EPA + yards patterns require an explicit off/def qualifier.
# Andy caught the KC ML writeup that said "ranked 2nd in pass EPA" —
# KC is 2nd in DEF pass EPA (elite) but 19th in OFF pass EPA (bad).
# Bare "pass EPA" without a qualifier is ambiguous → the reader
# defaults to the wrong interpretation. Fix: only map explicitly
# qualified phrases. Bare EPA/yards get caught by AMBIGUOUS_PATTERNS
# below and flagged as confirmed_mismatch (forces regen).
STAT_ALIASES = [
    # Yards — must say "allowed" for defensive
    (r'(?:allowing|allow(?:ed|s)?|d\s+allow(?:ed|s|ing)?|defensive|defense)\s+(?:just\s+)?(?:pass(?:ing)?\s+)?(?:yards?)(?:\s+per\s+game)?', {
        'def': 'pass_yds_allowed_pg'}),
    (r'pass(?:ing)?\s+yards?(?:\s+per\s+game)?\s+allowed', {'def': 'pass_yds_allowed_pg'}),
    (r'pass(?:ing)?\s+yards?(?:\s+per\s+game)?(?!\s+allowed)', {'off': 'pass_yds_pg'}),
    (r'rush(?:ing)?\s+yards?(?:\s+per\s+game)?\s+allowed', {'def': 'rush_yds_allowed_pg'}),
    (r'rush(?:ing)?\s+yards?(?:\s+per\s+game)?(?!\s+allowed)', {'off': 'rush_yds_pg'}),
    (r'total\s+(?:offense|yards?(?:\s+per\s+game)?)', {'off': 'total_yds_pg'}),
    (r'(?:total\s+)?yards?\s+allowed', {'def': 'yds_allowed_pg'}),
    (r'points?\s+allowed(?:\s+per\s+game)?', {'def': 'points_allowed_pg'}),
    (r'pass(?:ing)?\s+td[s]?(?:\s+per\s+game)?', {'off': 'pass_tds_pg'}),
    (r'rush(?:ing)?\s+td[s]?(?:\s+per\s+game)?', {'off': 'rush_tds_pg'}),
    # EPA — must have off/def qualifier or "allowed"
    (r'def(?:ensive)?\s+pass(?:ing)?\s+EPA(?:/play)?', {'def': 'def_pass_epa'}),
    (r'pass(?:ing)?\s+EPA(?:/play)?\s+allowed', {'def': 'def_pass_epa'}),
    (r'off(?:ensive)?\s+pass(?:ing)?\s+EPA(?:/play)?', {'off': 'off_pass_epa'}),
    (r'def(?:ensive)?\s+rush(?:ing)?\s+EPA(?:/play)?', {'def': 'def_rush_epa'}),
    (r'rush(?:ing)?\s+EPA(?:/play)?\s+allowed', {'def': 'def_rush_epa'}),
    (r'off(?:ensive)?\s+rush(?:ing)?\s+EPA(?:/play)?', {'off': 'off_rush_epa'}),
    (r'sacks?(?:\s+suffered)?(?:\s+per\s+game)?', {'off': 'sacks_suffered_pg'}),
    (r'int(?:erception)?s?(?:\s+per\s+game)?', {'off': 'ints_pg'}),
    (r'penalty\s+yards?(?:\s+per\s+game)?', {'off': 'penalty_yds_pg'}),
]

# Ambiguous phrases the LLM MUST NOT emit — "pass EPA" without off/def
# qualifier reads different ways to different bettors. Layer F flags
# these as confirmed_mismatch to force regen with a clearer citation.
AMBIGUOUS_PATTERNS = [
    (re.compile(r'\b(?<!off\s)(?<!offensive\s)(?<!def\s)(?<!defensive\s)pass(?:ing)?\s+EPA\b(?!\s+allowed)', re.IGNORECASE),
     'bare "pass EPA" — must specify "offensive", "defensive", or "allowed"'),
    (re.compile(r'\b(?<!off\s)(?<!offensive\s)(?<!def\s)(?<!defensive\s)rush(?:ing)?\s+EPA\b(?!\s+allowed)', re.IGNORECASE),
     'bare "rush EPA" — must specify "offensive", "defensive", or "allowed"'),
]

RANK_CLAIM_RES = [
    # "GB is 2nd in passing yards", "Green Bay ranks 5th in ..."
    re.compile(r'(?P<team>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*|[A-Z]{2,4})\s+(?:is|ranks?|sits?)\s+(?:the\s+)?(?P<rank>\d+(?:st|nd|rd|th)|#?\d+|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|dead\s+last)\s*(?:-?ranked)?\s+in\s+(?P<stat>[a-z][a-z\s/]+?)(?=[.,;]|\s+(?:and|but|allowing|which)|\s*$)', re.IGNORECASE),
    # "Green Bay's pass attack (2nd in passing yards)"
    re.compile(r"(?P<team>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*|[A-Z]{2,4})(?:'s)?\s+(?:[a-z]+\s+){0,4}\((?P<rank>\d+(?:st|nd|rd|th)|#?\d+|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|dead\s+last)\s+in\s+(?P<stat>[a-z][a-z\s/]+?)\)", re.IGNORECASE),
]

RECORD_RE = re.compile(
    r'(?P<team>[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*|[A-Z]{2,4})\s+(?:is|are|sits?|went)\s+(?P<w>\d+)-(?P<l>\d+)(?:-(?P<p>\d+))?\s+(?P<ctx>ATS|SU|O/U|straight[- ]up|against\s+the\s+spread|(?:as\s+a?\s+)?(?:home\s+)?(?:road\s+)?(?:favorite|fav|underdog|dog|host|visitor)|on\s+the\s+road|at\s+home)?',
    re.IGNORECASE)

# NFL name/abbrev fallback map (only for parser resolution)
_NFL_TEAM_ALIASES = {
    'gb':'GB','packers':'GB','green bay':'GB','green bay packers':'GB',
    'nyj':'NYJ','jets':'NYJ','new york jets':'NYJ',
    'kc':'KC','chiefs':'KC','kansas city':'KC','kansas city chiefs':'KC',
    'buf':'BUF','bills':'BUF','buffalo':'BUF','buffalo bills':'BUF',
    'det':'DET','lions':'DET','detroit':'DET','detroit lions':'DET',
    'bal':'BAL','ravens':'BAL','baltimore':'BAL','baltimore ravens':'BAL',
    'no':'NO','saints':'NO','new orleans':'NO','new orleans saints':'NO',
    'cin':'CIN','bengals':'CIN','cincinnati':'CIN','cincinnati bengals':'CIN',
    'hou':'HOU','texans':'HOU','houston':'HOU','houston texans':'HOU',
    'lac':'LAC','chargers':'LAC','los angeles chargers':'LAC',
    'lv':'LV','raiders':'LV','las vegas':'LV','las vegas raiders':'LV',
    'was':'WAS','commanders':'WAS','washington':'WAS','washington commanders':'WAS',
    'dal':'DAL','cowboys':'DAL','dallas':'DAL','dallas cowboys':'DAL',
    'cle':'CLE','browns':'CLE','cleveland':'CLE','cleveland browns':'CLE',
    'tb':'TB','buccaneers':'TB','bucs':'TB','tampa bay':'TB','tampa bay buccaneers':'TB',
    'min':'MIN','vikings':'MIN','minnesota':'MIN','minnesota vikings':'MIN',
    'chi':'CHI','bears':'CHI','chicago':'CHI','chicago bears':'CHI',
    'phi':'PHI','eagles':'PHI','philadelphia':'PHI','philadelphia eagles':'PHI',
    'ten':'TEN','titans':'TEN','tennessee':'TEN','tennessee titans':'TEN',
    'sea':'SEA','seahawks':'SEA','seattle':'SEA','seattle seahawks':'SEA',
    'ari':'ARI','cardinals':'ARI','arizona':'ARI','arizona cardinals':'ARI',
    'car':'CAR','panthers':'CAR','carolina':'CAR','carolina panthers':'CAR',
    'atl':'ATL','falcons':'ATL','atlanta':'ATL','atlanta falcons':'ATL',
    'pit':'PIT','steelers':'PIT','pittsburgh':'PIT','pittsburgh steelers':'PIT',
    'ne':'NE','patriots':'NE','new england':'NE','new england patriots':'NE',
    'mia':'MIA','dolphins':'MIA','miami':'MIA','miami dolphins':'MIA',
    'sf':'SF','49ers':'SF','san francisco':'SF','san francisco 49ers':'SF',
    'nyg':'NYG','giants':'NYG','new york giants':'NYG',
    'la':'LA','rams':'LA','los angeles rams':'LA',
    'ind':'IND','colts':'IND','indianapolis':'IND','indianapolis colts':'IND',
    'jax':'JAX','jaguars':'JAX','jacksonville':'JAX','jacksonville jaguars':'JAX',
    'den':'DEN','broncos':'DEN','denver':'DEN','denver broncos':'DEN',
}


def _norm_rank(txt):
    if not txt: return None
    t = str(txt).lower().strip().replace('#', '')
    if t == 'dead last': return 32
    if t in NUMBER_ORDINAL: return NUMBER_ORDINAL[t]
    m = re.match(r'(\d+)', t)
    return int(m.group(1)) if m else None


def _resolve_team_key(team_ref: str, provided_facts: dict) -> Optional[str]:
    """Match 'Green Bay' or 'GB' or 'Packers' to the team-abbrev key in
    provided_facts.team_stats_rank."""
    if not team_ref: return None
    tref = team_ref.lower().strip()
    ranks = provided_facts.get('team_stats_rank') or {}
    # Exact / plural strip
    for team_key in ranks:
        if tref == team_key.lower() or tref == team_key.lower().rstrip('s'):
            return team_key
    # Alias table
    cand = _NFL_TEAM_ALIASES.get(tref)
    if cand and cand in ranks:
        return cand
    # Try substring against team keys (e.g. LLM says "Buffalo Bills", key is "BUF")
    for team_key in ranks:
        if team_key.lower() in tref or tref in team_key.lower():
            return team_key
    return None


def _resolve_stat_keys(stat_phrase: str) -> list:
    """Free-form stat phrase → possible team_stats_rolling stat_key(s)."""
    if not stat_phrase: return []
    s = stat_phrase.lower().strip()
    matches = []
    for pattern, keymap in STAT_ALIASES:
        if re.search(pattern, s, re.IGNORECASE):
            for k in keymap.values():
                if k not in matches:
                    matches.append(k)
    return matches


def scan_hallucinated_stats(prose: str, provided_facts: dict) -> dict:
    """Strict cross-reference of every rank/record claim in prose against
    provided_facts. Returns three buckets:
      - confirmed_mismatch: real hallucinations to fix
      - unverifiable: parser couldn't resolve (noise, not a fault)
      - verified: cited claim matched a fact
    """
    confirmed_mismatch, unverifiable, verified = [], [], []
    team_ranks = provided_facts.get('team_stats_rank') or {}
    situational = provided_facts.get('situational_records') or {}

    # 2026-09-16 EPA-ambiguity check. Bare "pass EPA" / "rush EPA" without
    # off/def qualifier is a hallucination trigger — reader defaults to
    # wrong interpretation (KC ML writeup 9/16 said "2nd in pass EPA"
    # meaning DEF, but reads as OFF where KC is 19th). Flag as
    # confirmed_mismatch to force regen with a clearer citation.
    for pattern, reason in AMBIGUOUS_PATTERNS:
        for m in pattern.finditer(prose):
            confirmed_mismatch.append((m.group(0), reason))

    for pattern in RANK_CLAIM_RES:
        for m in pattern.finditer(prose):
            team_ref = m.group('team')
            cited_rank = _norm_rank(m.group('rank'))
            stat_phrase = m.group('stat')
            if not cited_rank: continue
            team_key = _resolve_team_key(team_ref, provided_facts)
            if not team_key:
                unverifiable.append((m.group(0), f'team "{team_ref}" not resolvable'))
                continue
            stat_keys = _resolve_stat_keys(stat_phrase)
            if not stat_keys:
                unverifiable.append((m.group(0), f'stat phrase "{stat_phrase}" unmapped'))
                continue
            team_stats = team_ranks.get(team_key, {})
            matched = False
            actual_ranks = []
            for sk in stat_keys:
                actual = team_stats.get(sk, {}).get('rank')
                if actual:
                    actual_int = int(str(actual).split('/')[0])
                    actual_ranks.append(f'{sk}={actual}')
                    if actual_int == cited_rank:
                        matched = True
                        verified.append((m.group(0), f'{team_key} {sk}={actual}'))
                        break
            if not matched:
                if actual_ranks:
                    confirmed_mismatch.append((m.group(0),
                        f'claimed rank {cited_rank}, actual: {actual_ranks}'))
                else:
                    unverifiable.append((m.group(0),
                        f'{team_key} has no rank for any of {stat_keys}'))

    for m in RECORD_RE.finditer(prose):
        team_ref = m.group('team')
        w = m.group('w'); l = m.group('l'); p = m.group('p') or '0'
        ctx = (m.group('ctx') or '').lower()
        team_key = _resolve_team_key(team_ref, provided_facts)
        if not team_key: continue
        team_recs = situational.get(team_key, {})
        market_hint = ('spread' if 'ats' in ctx or 'spread' in ctx else
                       'ml' if 'su' in ctx or 'straight' in ctx else
                       'total' if 'o/u' in ctx or 'over' in ctx or 'under' in ctx else None)
        expected_recs = []
        for rec_key, rec_val in team_recs.items():
            if market_hint and market_hint not in rec_key: continue
            expected_recs.append(f'{rec_key}={rec_val}')
        claimed = f'{w}-{l}' + (f'-{p}' if p != '0' else '')
        if not expected_recs:
            unverifiable.append((m.group(0), f'no {market_hint} record for {team_key}'))
        elif any(claimed in ev for ev in expected_recs):
            verified.append((m.group(0), f'{team_key} matches {expected_recs[:2]}'))
        else:
            confirmed_mismatch.append((m.group(0),
                f'claimed {claimed}, actual: {expected_recs[:3]}'))

    return {'confirmed_mismatch': confirmed_mismatch,
            'unverifiable': unverifiable,
            'verified': verified}


# ═══ AUTO-REPAIR — off/def qualifier inline patcher ══════════════════
#
# Deterministic fix for the bare "pass EPA" / "rush EPA" ambiguity.
# For each occurrence in prose, scan the surrounding window for the
# team reference + a rank number, look up whether the rank matches
# off_pass_epa or def_pass_epa for that team, and inline-inject the
# correct qualifier. Beats LLM retry for this class of ambiguity —
# no roundtrip, guaranteed correct outcome when the rank matches
# exactly one side. Falls through (leaves untouched) when both sides
# match the rank or neither does.
#
# Called from generate_nfl_game_reads.py Layer F block BEFORE the
# corrective-retry step. Repaired prose re-scans clean and ships as
# analyst voice. Prevents "pass EPA" ambiguity from forcing games
# down to the quant-template fallback.

_EPA_INLINE_RES = [
    (re.compile(r'\bpass(?:ing)?\s+EPA\b(?!\s+allowed)(?!/play)', re.IGNORECASE),
     'pass_epa', 'pass EPA'),
    (re.compile(r'\brush(?:ing)?\s+EPA\b(?!\s+allowed)(?!/play)', re.IGNORECASE),
     'rush_epa', 'rush EPA'),
]

# Find a rank ("2nd", "#4", "ranked 8", etc.) in the same clause as
# the bare EPA phrase. Look up to 80 chars before and 40 after.
_RANK_NEAR_RE = re.compile(
    r'\b(?:ranks?|ranked|is|sits?|#)\s*(?:the\s+)?(\d+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|dead\s+last)\b',
    re.IGNORECASE)

def _find_rank_near(prose: str, match_start: int, match_end: int) -> Optional[int]:
    """Look for a rank number within +/- window of the EPA phrase."""
    left = max(0, match_start - 80)
    right = min(len(prose), match_end + 40)
    window = prose[left:right]
    rank_matches = list(_RANK_NEAR_RE.finditer(window))
    if not rank_matches: return None
    # Pick the rank closest to the EPA phrase position within the window.
    # match_start relative to window = match_start - left.
    epa_pos_in_window = match_start - left
    best = min(rank_matches, key=lambda m: abs(m.start() - epa_pos_in_window))
    return _norm_rank(best.group(1))

def _find_team_near(prose: str, match_start: int, provided_facts: dict) -> Optional[str]:
    """Scan back from EPA phrase for the nearest team reference that
    resolves to a key in provided_facts.team_stats_rank. Look at up to
    120 chars before to catch subject-precedes-verb patterns."""
    window_start = max(0, match_start - 120)
    window = prose[window_start:match_start]
    # Scan every capitalized-word sequence + known alias, prefer nearest.
    candidates = list(re.finditer(
        r'\b(?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*|[A-Z]{2,4}|(?:'
        + '|'.join(re.escape(k) for k in _NFL_TEAM_ALIASES)
        + r'))\b', window, re.IGNORECASE))
    for m in reversed(candidates):  # closest to EPA first
        team_key = _resolve_team_key(m.group(0), provided_facts)
        if team_key: return team_key
    return None

def auto_repair_epa_ambiguity(prose: str, provided_facts: dict) -> tuple:
    """Inline-patch bare "pass EPA" / "rush EPA" with the correct off/def
    qualifier when the surrounding rank unambiguously matches one side.

    Returns (repaired_prose, list_of_repairs) where each repair is
    (original_phrase, injected_qualifier, reason). If auto-repair
    can't resolve (rank matches both or neither, or no team/rank
    context), the phrase is left as-is for Layer F to flag.

    2026-09-16: shipped per Andy directive "I need every writeup to
    be solid this week" — trades LLM retry roundtrip for a
    deterministic string substitution when the fact resolves cleanly.
    """
    if not prose: return prose, []
    team_ranks = provided_facts.get('team_stats_rank') or {}
    repairs = []
    # Process in reverse position order so earlier substitutions don't
    # invalidate later match spans.
    all_hits = []
    for pattern, kind, orig_phrase in _EPA_INLINE_RES:
        for m in pattern.finditer(prose):
            all_hits.append((m.start(), m.end(), kind, m.group(0)))
    all_hits.sort(key=lambda x: -x[0])  # right-to-left

    out = prose
    for start, end, kind, matched_text in all_hits:
        rank_cited = _find_rank_near(out, start, end)
        team_key = _find_team_near(out, start, provided_facts)
        if rank_cited is None or team_key is None:
            continue  # not enough context — leave for Layer F flag
        team_stats = team_ranks.get(team_key, {})
        off_key = f'off_{kind}'   # off_pass_epa / off_rush_epa
        def_key = f'def_{kind}'   # def_pass_epa / def_rush_epa
        off_rank = team_stats.get(off_key, {}).get('rank')
        def_rank = team_stats.get(def_key, {}).get('rank')
        off_int = int(str(off_rank).split('/')[0]) if off_rank else None
        def_int = int(str(def_rank).split('/')[0]) if def_rank else None
        matches_off = off_int == rank_cited
        matches_def = def_int == rank_cited
        if matches_off and not matches_def:
            qualifier = 'offensive '
        elif matches_def and not matches_off:
            qualifier = 'defensive '
        else:
            # Ambiguous (both match, or neither) — leave for Layer F to flag
            continue
        # Inject the qualifier just before the "pass EPA" / "rush EPA"
        # preserving the original casing of the phrase.
        replaced_phrase = qualifier + matched_text
        out = out[:start] + replaced_phrase + out[end:]
        repairs.append((matched_text, qualifier.strip(),
                        f'{team_key} rank {rank_cited} → {qualifier.strip()} '
                        f'({off_key}={off_rank}, {def_key}={def_rank})'))
    return out, repairs


# ═══ MLB LAYER F — numeric-claim cross-reference ═════════════════════
#
# MLB doesn't have the team-rank architecture NFL uses; its facts are
# pitcher-centric (xERA, ERA, K/9). Layer F for MLB flattens the facts
# dict into a numeric-value set and scans prose for cited numbers that
# don't appear anywhere in facts. Simpler than NFL's rank-vs-team
# match but catches the class of "invented pitcher xERA" hallucination.
#
# Number patterns: floats with 1-3 decimals (xERA 2.51, K/9 9.4),
# integers with common MLB units (K, IP, ERA formatted as %).

_MLB_NUMBER_RE = re.compile(r'\b\d+(?:\.\d{1,3})?\b')
_MLB_STAT_HINT_RE = re.compile(
    r'\b(?:xERA|ERA|K/9|BB/9|WHIP|K%|BB%|wRC\+?|BABIP|barrel%|K/BB|OPS)\b',
    re.IGNORECASE)


def _flatten_numeric_facts(facts: dict) -> set:
    """Collect every numeric value from provided_facts into a set of
    string representations for O(1) presence checks. Includes both
    the raw float and rounded variants (2, 2.5, 2.51) so LLM's
    typical rounding doesn't false-flag."""
    out = set()
    def _add(v):
        if v is None: return
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        out.add(str(int(f)) if f == int(f) else str(f))
        # Common rounding variants
        for prec in (1, 2, 3):
            r = round(f, prec)
            out.add(f'{r:.{prec}f}')
            if r == int(r): out.add(str(int(r)))
    def _walk(o):
        if isinstance(o, dict):
            for v in o.values(): _walk(v)
        elif isinstance(o, list):
            for v in o: _walk(v)
        else: _add(o)
    _walk(facts)
    return out


def scan_hallucinated_stats_mlb(prose: str, provided_facts: dict) -> dict:
    """MLB Layer F. Scans prose for numeric claims that don't appear
    anywhere in provided_facts. Returns three buckets matching the
    NFL scanner shape so the caller stays sport-agnostic:
      - confirmed_mismatch: numbers cited that aren't in facts
      - unverifiable: parser noise / claims without stat hint nearby
      - verified: numbers cited that DO appear in facts
    """
    confirmed_mismatch, unverifiable, verified = [], [], []
    if not prose: return {'confirmed_mismatch': [], 'unverifiable': [], 'verified': []}
    facts_nums = _flatten_numeric_facts(provided_facts)
    # Walk each numeric hit, look at surrounding 40 chars for a stat
    # hint (xERA / ERA / K/9 etc.). If a hint is present and the number
    # isn't in facts → likely hallucinated. If no hint nearby → treat
    # as filler (year, ordinal, etc.) and skip.
    for m in _MLB_NUMBER_RE.finditer(prose):
        num_str = m.group(0)
        # Skip obviously safe numbers: years, single-digit small ints,
        # game-day dates
        try:
            f = float(num_str)
        except ValueError:
            continue
        if 1900 <= f <= 2100 and f == int(f): continue  # year
        if 0 <= f <= 5 and f == int(f): continue  # small ints (# games, # runs)
        left = max(0, m.start() - 40); right = min(len(prose), m.end() + 40)
        window = prose[left:right]
        if not _MLB_STAT_HINT_RE.search(window):
            continue  # no stat context — not a claim
        if num_str in facts_nums:
            verified.append((f'{num_str} @ ...{window[-40:]}', 'in facts'))
        else:
            confirmed_mismatch.append(
                (f'{num_str} in "{window.strip()[-60:]}"',
                 f'not in provided_facts numeric set'))
    return {'confirmed_mismatch': confirmed_mismatch,
            'unverifiable': unverifiable, 'verified': verified}


def scan_stats(prose: str, provided_facts: dict, sport: str) -> dict:
    """Sport-dispatched Layer F scanner."""
    sport = (sport or 'NFL').upper()
    if sport == 'MLB':
        return scan_hallucinated_stats_mlb(prose, provided_facts)
    return scan_hallucinated_stats(prose, provided_facts)


def feature_enabled(sport: str, feature: str) -> bool:
    """Check feature_flags row (sport, feature)=enabled. Default false —
    every rollout is opt-in. Feature flag rows land via SQL migration
    or direct insert."""
    try:
        r = requests.get(f'{SB}/rest/v1/feature_flags',
            params={'sport': f'eq.{sport}', 'feature': f'eq.{feature}',
                    'select': 'enabled'},
            headers=H, timeout=5)
        rows = r.json() if r.status_code == 200 and r.text.strip().startswith('[') else []
        return bool(rows and rows[0].get('enabled'))
    except Exception:
        return False


def analyst_gate(sport: str, game_id: str) -> bool:
    """Two-layer gate: global feature flag + optional per-game override.

    Enables canary rollout: turn on for one game_id, verify, then flip
    the sport-wide flag. Per-game row = feature 'analyst_writeup_v1_gid_<gid>'.
    """
    if feature_enabled(sport, f'analyst_writeup_v1_gid_{game_id}'):
        return True
    return feature_enabled(sport, 'analyst_writeup_v1')
