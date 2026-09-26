"""Turn the team-stats table into the story it already tells, in English.

Andy 2026-09-26:
  "all that data tells a story and that's what Jerry should be talking
   about, all data found in game detail, sharp money flow assessment,
   stats (top rushing team facing bottom run defense), SOS/SOR) It all
   tells a story, sharp money is on team X they are dominant in every
   stat field and fight to cover spreads throughout season, take X team
   laying the points."

WHY THIS IS COMPUTED HERE AND NOT LEFT TO THE MODEL. Jerry is already
handed every one of these numbers. On 2026-09-26 the Vanderbilt @ Auburn
read was given home_sp_overall=11.6, away_sp_overall=9.7, sp_gap=1.9 and
wrote "Auburn's SP+ sits around 24-26 ... Vanderbilt closer to 8-12 —
that 12-15 point efficiency gap mirrors the 10-point spread." Every
downstream sentence ("model and market alignment here is clean") was a
conclusion drawn from a gap that does not exist. The data was not
missing; the model re-derived it from memory because it arrived as raw
JSON rather than as a stated fact.

That is the exact failure the CONFIRMED FACTS mechanism in the read
generators already solves for spreads, totals and moneylines: state the
fact as an English string, hoist it to the top of the prompt, redact the
raw field so there is nothing to re-derive from. This module extends the
same treatment to the stats — so the comparison is arithmetic done in
Python, and Jerry's job is to narrate it rather than to compute it.

Percentiles, not raw ranks, because league_size differs BY STAT (139 for
SP+, 216 for volumetric, 266 for rates) — a 12-place gap is a big edge in
one universe and noise in another. Same correction already made in
GameDetailV2.advantage().

Deliberately NOT a pipeline step. Andy: "these need to be injected in
current processes not adding workflow to confuse system." The read
generators import it.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

# Resolved at CALL time, not import time. generate_ncaaf_game_reads.py
# calls load_dotenv() after its import block, so reading the environment
# at module scope bound SUPABASE_URL to None and every request failed with
# "Invalid URL 'None/rest/v1/...'". The story layer degraded quietly to
# "no story" — the whole point of this module lost, with the card still
# rendering fine. Lazy lookup makes the module independent of when the
# importer happens to load its environment.
def _env():
    sb = os.environ.get('SUPABASE_URL')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY'))
    if not sb or not key:
        raise RuntimeError('SUPABASE_URL / key not in environment yet')
    return sb, {'apikey': key, 'Authorization': f'Bearer {key}'}

# A unit of offense pointed at the unit of defense that has to stop it.
# This is the "top rushing team facing bottom run defense" pairing, stated
# once per sport. (attack_key, defend_key, phrase)
MATCHUP_PAIRS = {
    'NCAAF': [
        ('rush_yds_pg',      'def_rush_epa_allowed',     'run game'),
        ('pass_yds_pg',      'def_epa_per_play',         'passing game'),
        ('off_success_rate', 'def_success_rate_allowed', 'staying on schedule'),
        ('off_epa_per_play', 'def_epa_per_play',         'offense overall'),
    ],
    'NFL': [
        ('rush_yds_pg',      'def_rush_epa_allowed',     'run game'),
        ('pass_yds_pg',      'def_epa_per_play',         'passing game'),
        ('off_success_rate', 'def_success_rate_allowed', 'staying on schedule'),
        ('off_epa_per_play', 'def_epa_per_play',         'offense overall'),
    ],
}

# Stats worth calling out on their own, as a straight head-to-head.
SOLO_KEYS = {
    'NCAAF': ['sp_overall', 'sor', 'sos', 'points_allowed_pg',
              'turnovers_pg', 'third_down_pct'],
    'NFL':   ['sor', 'sos', 'points_allowed_pg', 'turnovers_pg',
              'third_down_pct'],
}

# A mismatch has to be genuinely lopsided before it is worth a sentence.
# Top-30% attacking bottom-30% is the bar; anything tighter is two average
# units and saying so adds nothing.
STRONG_PCT = 0.70
WEAK_PCT = 0.30
# Head-to-head gap that counts as "clearly better" on a solo stat.
SOLO_GAP = 0.25


def _page(path: str, params: dict) -> list:
    sb, headers = _env()
    out, off = [], 0
    while True:
        r = requests.get(f'{sb}/rest/v1/{path}', headers=headers,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:160]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def load_team_stats(sport: str, season: int) -> dict:
    """-> {(team, stat_key): row}. One read for the whole slate."""
    rows = _page('team_stats_rolling', {
        'sport': f'eq.{sport}', 'season': f'eq.{season}',
        'select': 'team,stat_key,raw_value,rank,league_size,direction,'
                  'display_label,unit'})
    return {(r['team'], r['stat_key']): r for r in rows}


def _pct(row: Optional[dict]) -> Optional[float]:
    """Percentile where 1.0 = best in the league, polarity already handled.

    rank is always 1 = best (the matview and the computed table both
    guarantee it), so direction does not need re-applying here.
    """
    if not row or row.get('rank') is None or not row.get('league_size'):
        return None
    try:
        rank, size = int(row['rank']), int(row['league_size'])
    except (TypeError, ValueError):
        return None
    if size <= 1:
        return None
    return 1.0 - (rank - 1) / (size - 1)


def _fmt(row: dict) -> str:
    v = row.get('raw_value')
    unit = (row.get('unit') or '').strip()
    try:
        fv = float(v)
        s = f'{fv:.0f}' if abs(fv) >= 100 else (f'{fv:.1f}' if abs(fv) >= 10 else f'{fv:.3f}')
    except (TypeError, ValueError):
        s = str(v)
    return f'{s}{(" " + unit) if unit else ""}'


def _ord(p: float) -> str:
    n = max(1, min(99, int(round(p * 100))))
    suf = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suf}'


def build_story(sport: str, home: str, away: str, stats: dict) -> dict:
    """-> {'lines': [str], 'dominance': str|None}

    Every string is safe to quote verbatim: the numbers come straight from
    the same rows the app renders, so the read and the card cannot
    disagree.
    """
    lines: list[str] = []

    # ── unit vs unit ────────────────────────────────────────────────────
    for attacker, defender in ((away, home), (home, away)):
        for atk_key, def_key, phrase in MATCHUP_PAIRS.get(sport, []):
            a_row = stats.get((attacker, atk_key))
            d_row = stats.get((defender, def_key))
            a_p, d_p = _pct(a_row), _pct(d_row)
            if a_p is None or d_p is None:
                continue
            if a_p >= STRONG_PCT and d_p <= WEAK_PCT:
                lines.append(
                    f"{attacker}'s {phrase} ({a_row['display_label']} "
                    f"{_fmt(a_row)}, {_ord(a_p)} pct) meets {defender}'s "
                    f"{d_row['display_label']} {_fmt(d_row)} ({_ord(d_p)} pct) "
                    f"— a clear edge for {attacker}")
            elif a_p <= WEAK_PCT and d_p >= STRONG_PCT:
                lines.append(
                    f"{attacker}'s {phrase} ({a_row['display_label']} "
                    f"{_fmt(a_row)}, {_ord(a_p)} pct) runs into {defender}'s "
                    f"{d_row['display_label']} {_fmt(d_row)} ({_ord(d_p)} pct) "
                    f"— a clear problem for {attacker}")

    # ── straight head-to-heads ──────────────────────────────────────────
    for key in SOLO_KEYS.get(sport, []):
        h_row, a_row = stats.get((home, key)), stats.get((away, key))
        h_p, a_p = _pct(h_row), _pct(a_row)
        if h_p is None or a_p is None:
            continue
        if abs(h_p - a_p) < SOLO_GAP:
            continue
        better, worse = ((home, h_row, h_p), (away, a_row, a_p)) if h_p > a_p \
            else ((away, a_row, a_p), (home, h_row, h_p))
        lines.append(
            f"{h_row['display_label']}: {better[0]} {_fmt(better[1])} "
            f"({_ord(better[2])} pct) vs {worse[0]} {_fmt(worse[1])} "
            f"({_ord(worse[2])} pct)")

    # ── who wins the sheet overall ──────────────────────────────────────
    # Andy's "dominant in every stat field" — counted, not asserted.
    dominance = None
    keys = {k for (_t, k) in stats.keys()}
    h_win = a_win = graded = 0
    for key in keys:
        h_p, a_p = _pct(stats.get((home, key))), _pct(stats.get((away, key)))
        if h_p is None or a_p is None or abs(h_p - a_p) < 0.05:
            continue
        graded += 1
        if h_p > a_p:
            h_win += 1
        else:
            a_win += 1
    if graded >= 6:
        lead, n = (home, h_win) if h_win > a_win else (away, a_win)
        if n / graded >= 0.70:
            dominance = (f'{lead} is the better side in {n} of {graded} '
                         f'measured stat categories')
        else:
            dominance = (f'the stat sheet is split — {home} better in '
                         f'{h_win} of {graded} categories, {away} in {a_win}')

    return {'lines': lines, 'dominance': dominance}
