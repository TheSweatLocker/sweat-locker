#!/usr/bin/env python3
"""Check every number in a generated game read against the numbers we hold.

WHY (2026-10-03). A JAX @ CIN read stated "Jacksonville generates minus-3.83
defensive rush EPA (8th)". Jacksonville is -2.188 and 11th. **-3.83 is Las
Vegas's number.** Two panels below, the app rendered the correct value from
team_computed_stats — so one card asserted a figure and disproved it.

Three other citations in the same read were correct, which is what makes this
dangerous: a reader cannot tell the wrong sentences from the right ones, and
the brand is "we show the receipts".

THE RULE THIS ENFORCES
    Every number in the prose must be traceable to a value we actually store
    for one of the two teams in THIS game.

It deliberately does not try to parse "which stat does this sentence mean" —
that is a harder problem than the one worth solving, and a parser that
half-understands prose fails silently in the same way the read does. Instead
it asks the question we can answer exactly: is this number one of ours?
A number that appears nowhere in the game's own data is either another team's
or invented, and both are defects.

Ranks are checked the same way, against ranks we computed.

EXIT CODES
    0  clean, or report-only mode
    1  at least one untraceable number and --strict was passed

    python validate_read_numbers.py --sport NFL --date 2026-10-04
    python validate_read_numbers.py --sport NFL --date 2026-10-04 --strict
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _line in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX_TABLE = {
    'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
    'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context',
    'NBA': 'nba_game_context', 'NCAAB': 'ncaab_game_context',
}

# Numbers that are never a factual claim about a team.
_STOPWORDS = {
    # ordinals that are almost always calendar or quarter references
    '1', '2', '3', '4',
}

# "minus-3.83", "-3.83", "3.83", "208.6", "12.0"
_NUM = re.compile(r'(?:minus[-\s]|negative\s|[-−])?\d+(?:\.\d+)?')
# "8th", "23rd", "1st", "ranks 11th"
_ORD = re.compile(r'\b(\d+)(?:st|nd|rd|th)\b')


def _pull(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        q = dict(params)
        q.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q, timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} -> {r.status_code} {r.text[:120]}')
            return out
        chunk = r.json()
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def _numeric_values(obj) -> set:
    """Every float we can reach inside a row, at 2dp and 1dp and 0dp."""
    vals = set()

    def add(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        for nd in (0, 1, 2, 3):
            vals.add(round(abs(f), nd))

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)):
            add(o)
        elif isinstance(o, str):
            # numbers embedded in jsonb text still count as ours
            for m in re.finditer(r'-?\d+(?:\.\d+)?', o):
                add(m.group())
    walk(obj)
    return vals


def build_allowlist(sport: str, game: dict, computed: list[dict],
                    results: list[dict], players: list[dict]) -> tuple[set, set]:
    """-> (allowed values, allowed ranks) for this one game.

    The allowlist has to span every table the READ GENERATOR reads, or the
    check cries wolf and gets ignored — which is worse than not running it.
    generate_nfl_game_reads pulls nfl_game_context, team_stats_rolling
    (which UNIONs team_computed_stats), nfl_player_stats and nfl_injuries,
    so all of those are ours.
    """
    vals = _numeric_values(game)
    ranks = set()
    teams = {str(game.get('home_team') or '').upper(),
             str(game.get('away_team') or '').upper()}
    for row in computed:
        if str(row.get('team') or '').upper() not in teams:
            continue
        vals |= _numeric_values(row.get('raw_value'))
        if row.get('rank') is not None:
            try:
                ranks.add(int(row['rank']))
            except (TypeError, ValueError):
                pass
        if row.get('league_size') is not None:
            try:
                vals.add(float(row['league_size']))
            except (TypeError, ValueError):
                pass
    for row in results:
        if (str(row.get('home_team') or '').upper() in teams
                or str(row.get('away_team') or '').upper() in teams):
            vals |= _numeric_values(row)
    # Player lines get cited constantly ("averaged 7.36 yards per attempt"),
    # and per-attempt rates are derived, not stored — so admit the ratios of
    # the counting stats we do store for players on these two teams.
    for row in players:
        t = str(row.get('recent_team') or row.get('team') or '').upper()
        if t and t not in teams:
            continue
        vals |= _numeric_values(row)
        for a, b in (('passing_yards', 'attempts'),
                     ('rushing_yards', 'carries'),
                     ('receiving_yards', 'receptions'),
                     ('passing_tds', 'attempts')):
            try:
                num, den = float(row.get(a)), float(row.get(b))
                if den:
                    for nd in (1, 2, 3):
                        vals.add(round(abs(num / den), nd))
            except (TypeError, ValueError):
                pass
    return vals, ranks


def _claims(text: str) -> tuple[list, list]:
    """-> (numeric claims, ordinal claims) found in prose."""
    nums = []
    for m in _NUM.finditer(text or ''):
        raw = m.group()
        cleaned = re.sub(r'^(?:minus[-\s]|negative\s|[-−])', '', raw)
        try:
            f = float(cleaned)
        except ValueError:
            continue
        nums.append((raw.strip(), abs(f)))
    ords_ = [(m.group(), int(m.group(1))) for m in _ORD.finditer(text or '')]
    return nums, ords_


def check_read(read: dict, vals: set, ranks: set, tol: float) -> list[str]:
    bad = []
    for field in ('short_read', 'long_read', 'call_text'):
        text = read.get(field)
        if not text:
            continue
        nums, ords_ = _claims(str(text))
        for raw, f in nums:
            if raw.lstrip('-−') in _STOPWORDS:
                continue
            if any(abs(f - v) <= tol for v in vals):
                continue
            bad.append(f'{field}: value {raw} is not a number we hold for '
                       f'either team')
        for raw, r in ords_:
            if r in ranks:
                continue
            # a rank we never computed; could still be a legitimate league rank
            # we do not store, so word it as unverifiable rather than wrong
            bad.append(f'{field}: rank {raw} does not match any rank we '
                       f'computed for either team')
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX_TABLE))
    ap.add_argument('--date', required=True, help='YYYY-MM-DD')
    ap.add_argument('--tol', type=float, default=0.051,
                    help='absolute tolerance when matching a prose number')
    ap.add_argument('--strict', action='store_true',
                    help='exit 1 when any number is untraceable')
    ap.add_argument('--quiet-ranks', action='store_true',
                    help='only report value mismatches, not rank mismatches')
    args = ap.parse_args()

    table = CTX_TABLE[args.sport]
    games = _pull(table, {'select': '*', 'game_date': f'eq.{args.date}'})
    if not games:
        print(f'no {args.sport} games on {args.date}')
        return 0
    reads = _pull('jerry_reads', {
        'select': 'game_id,game_date,short_read,long_read,call_text',
        'sport': f'eq.{args.sport}', 'game_date': f'eq.{args.date}'})
    by_game = {}
    for r in reads:
        by_game.setdefault(str(r['game_id']), []).append(r)
    computed = _pull('team_computed_stats', {'select': '*',
                                             'sport': f'eq.{args.sport}'})
    results = _pull(f'{args.sport.lower()}_game_results',
                    {'select': '*', 'game_date': f'lte.{args.date}',
                     'order': 'game_date.desc'})[:400]
    rolling = _pull('team_stats_rolling', {'select': '*',
                                           'sport': f'eq.{args.sport}'})
    computed = computed + rolling
    players = _pull(f'{args.sport.lower()}_player_stats', {'select': '*'})

    print(f'=== read number audit · {args.sport} · {args.date} ===')
    print(f'  {len(games)} games · {len(reads)} reads · '
          f'{len(computed)} computed stat rows\n')

    total_bad = 0
    checked = 0
    for g in games:
        rds = by_game.get(str(g.get('game_id')), [])
        if not rds:
            continue
        vals, ranks = build_allowlist(args.sport, g, computed, results, players)
        for rd in rds:
            checked += 1
            bad = check_read(rd, vals, ranks, args.tol)
            if args.quiet_ranks:
                bad = [b for b in bad if ' rank ' not in b]
            if bad:
                total_bad += len(bad)
                print(f'{g.get("away_team")} @ {g.get("home_team")}')
                for b in bad:
                    print(f'    x {b}')
    print(f'\nchecked {checked} reads · {total_bad} untraceable figure(s)')
    if total_bad and args.strict:
        print('STRICT: failing. A number we cannot trace is a number we must '
              'not publish.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
