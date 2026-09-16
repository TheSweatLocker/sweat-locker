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


# ═══ LAYER F — STRICT CROSS-REFERENCE ════════════════════════════════

NUMBER_ORDINAL = {'first':1,'second':2,'third':3,'fourth':4,'fifth':5,
                  'sixth':6,'seventh':7,'eighth':8,'ninth':9,'tenth':10,
                  'eleventh':11,'twelfth':12}

# Prose phrase → team_stats_rolling stat_key(s). Longest match wins per pattern.
STAT_ALIASES = [
    (r'pass(?:ing)?\s+yards?(?:\s+per\s+game)?(?:\s+allowed)?', {
        'off': 'pass_yds_pg', 'def': 'pass_yds_allowed_pg'}),
    (r'rush(?:ing)?\s+yards?(?:\s+per\s+game)?(?:\s+allowed)?', {
        'off': 'rush_yds_pg', 'def': 'rush_yds_allowed_pg'}),
    (r'total\s+(?:offense|yards?(?:\s+per\s+game)?)', {'off': 'total_yds_pg'}),
    (r'(?:total\s+)?yards?\s+allowed', {'def': 'yds_allowed_pg'}),
    (r'points?\s+allowed(?:\s+per\s+game)?', {'def': 'points_allowed_pg'}),
    (r'pass(?:ing)?\s+td[s]?(?:\s+per\s+game)?', {'off': 'pass_tds_pg'}),
    (r'rush(?:ing)?\s+td[s]?(?:\s+per\s+game)?', {'off': 'rush_tds_pg'}),
    (r'pass(?:ing)?\s+EPA(?:/play)?', {'off': 'off_pass_epa', 'def': 'def_pass_epa'}),
    (r'rush(?:ing)?\s+EPA(?:/play)?', {'off': 'off_rush_epa', 'def': 'def_rush_epa'}),
    (r'sacks?(?:\s+suffered)?(?:\s+per\s+game)?', {'off': 'sacks_suffered_pg'}),
    (r'int(?:erception)?s?(?:\s+per\s+game)?', {'off': 'ints_pg'}),
    (r'penalty\s+yards?(?:\s+per\s+game)?', {'off': 'penalty_yds_pg'}),
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
