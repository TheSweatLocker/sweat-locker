"""Flag a game whose listed starting QB is ruled out, and cap its tier.

Andy's QA on PHI @ CHI, 2026-09-28: "Missing QB-injury gate. A starting QB
ruled out should suppress or flag every model output. Right now the models
and the Over pick are running on Williams-era data."

── WE ALREADY KNEW. NOTHING READ IT. ──
nfl_injuries has carried the answer since yesterday:

    Caleb Williams | QB | Out | Hamstring (Strain) | report_date 2026-09-27

And on the same card, at the same moment:

    nfl_game_context.home_qb_name  'Caleb Williams'
    panel_injury_outs              0
    MC projection                  CHI by 6.3, total 50.5
    Jerry's read                   no mention of Williams at all

So the ingest worked, the row was correct and current, and every consumer
looked past it. That is the same shape as the moneyline column split, the
dead MC dissent gate and the discarded blend label — the information exists
and the reader does not look.

── WHY THIS IS THE MOST EXPENSIVE VERSION OF THAT MISTAKE ──
A starting quarterback is the largest single predictive input in football.
Every projection on that card — V3, MC, Panel, the total, the Over pick —
was computed from team efficiency accumulated with Williams playing. The
market had already moved six points on the spread and five on the total.
Our models had not moved at all, so the "value" they showed on CHI and the
Over was an artefact of stale inputs, not an edge.

── WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT DO ──
It CAPS and FLAGS. It does not suppress: Andy, repeatedly, "i just dont
want to tread the line of engine passing on eveythign." A game with a
backup QB is still a game with a number on it.

But the cap is harder than the other gates, because this is not
uncertainty about a projection — the projection's INPUTS are known to
describe a player who is not playing. A model that cannot see the change
should not be allowed to claim conviction about it.

STATUS HANDLING. 'Out' and 'Doubtful' are treated as not playing.
'Questionable' is a softer flag — it caps less and says so — because
questionable QBs play most of the time. A missing or unparseable status is
treated as PLAYING, so a lookup failure can never invent an injury.

MOST RECENT REPORT WINS, AND `week` IS HOW WE KNOW. nfl_injuries holds one
row per player per week, and Williams appears five times:

    week 4  Out   Hamstring (Strain)   report_date 2026-09-27
    week 3  Out   Hamstring            report_date NULL
    week 2  Full                       report_date 2026-09-16
    week 1  Full                       report_date 2026-09-09
    week 0  Full                       report_date 2026-09-01

Reading any row but the newest returns 'Full' for a player who is out. The
ordering key is `week`, not `report_date`: week is populated on all 1000
rows sampled, report_date is NULL on 38 of them — including the week-3 'Out'.
report_date only breaks ties.

NO HARD WEEK FILTER, deliberately. nfl_game_context says this game is week 3
while the injury feed's freshest Williams row says week 4 — the two feeds
disagree by one. Pinning the lookup to the game's week would have read the
week-3 row here (harmless, also 'Out') but is brittle in general and would
miss the fresher report. So we take the newest row for that player and
TEAM, then record how far it sits from the game's week and soften to a
caveat once it is more than a week stale.

TEAM DISAMBIGUATES. Name alone collides across a 2,000-player league, and
nfl_injuries carries `team`, so we match on both when the context gives us
one.

── A SECOND, QUIETER LEAK IN THE SAME GAME ──
home_qb_madden_ovr is 90.0 and madden_qb_delta_home is +8.0, both keyed to
Williams. So the models are not merely using stale team efficiency; they are
actively crediting Chicago an eight-point quarterback-rating advantage that
belongs to a player who is not dressing. That is worth naming in the read
separately from the projection staleness, and it is why the cap here is
harder than the MC-dissent cap.
"""
from __future__ import annotations

from typing import Optional

import requests

# Statuses that mean the player is not taking the field.
OUT_STATUSES = {'out', 'doubtful', 'injured reserve', 'ir', 'pup',
                'suspended', 'did not participate in practice'}
SOFT_STATUSES = {'questionable', 'limited', 'limited participation in practice'}

# Tier ceilings. Harder than the MC-dissent gate on purpose — see module note.
OUT_TIER_CAP = 'COVERAGE'
OUT_CONVICTION_CAP = 45
SOFT_TIER_CAP = 'LEAN'
SOFT_CONVICTION_CAP = 60

_RANK = {'PASS': 0, 'COVERAGE': 1, 'LEAN': 2, 'STRONG': 3, 'PRIME': 4}
_CACHE: dict = {}


def _norm(name: str) -> str:
    return ' '.join(str(name or '').lower().replace('.', '').split())


def latest_status(sb: str, headers: dict, player_name: str,
                  team: Optional[str] = None,
                  max_week: Optional[int] = None,
                  as_of: Optional[str] = None) -> Optional[dict]:
    """Newest nfl_injuries row for a player AS OF the game, or None.

    Ordered (season, week) DESC — week is complete where report_date is not,
    so it is the recency key and report_date only breaks ties. Williams
    carries five rows across two statuses; reading any but the newest
    returns 'Full' for a player who is out.

    as_of / max_week ARE LEAK GUARDS, and they are not optional in practice.
    Without them this returns the freshest row in the table, which on a
    backtest means injury news from AFTER the game. Caught on the first
    regression run: a week-2 game was being gated by Jaxson Dart's week-4
    knee surgery, turning a PRIME into a COVERAGE on information nobody had
    at kickoff — the same defect as the prop L5 lookback that saw the game
    it was predicting.

    TWO GUARDS, BECAUSE WEEK ALONE IS TOO COARSE. Filtering on week with one
    week of slack (the feeds disagree by one, see the module note) still let
    2 of 22 rows through with a report_date AFTER their game — Dart reported
    09-23 against a 09-21 game, Cooper Rush 09-27 against 09-24. So
    report_date is authoritative whenever it exists and the week bound
    applies only to the 38-odd rows where it is NULL. Exact where we can be,
    coarse only where we must.
    """
    key = (_norm(player_name), (team or '').upper(), max_week, as_of)
    if not key[0]:
        return None
    if key in _CACHE:
        return _CACHE[key]
    params = {'select': 'player_name,team,position,injury_status,'
                        'practice_status,body_part,report_date,week,season',
              'player_name': f'ilike.{player_name}',
              'order': 'season.desc,week.desc,report_date.desc.nullslast',
              'limit': '25'}
    if team:
        params['team'] = f'eq.{team.upper()}'
    if max_week is not None:
        # Bounds only the NULL-report_date rows; dated rows are bounded below.
        params['week'] = f'lte.{int(max_week) + 1}'
    if as_of:
        params['or'] = f'(report_date.is.null,report_date.lte.{as_of})'
    try:
        r = requests.get(f'{sb}/rest/v1/nfl_injuries', headers=headers,
                         params=params, timeout=15)
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list):
            rows = []
    except Exception:
        rows = []
    hit = None
    for row in rows:
        # ilike is a loose match; require the name to be the same player.
        if _norm(row.get('player_name')) == key[0]:
            hit = row
            break
    _CACHE[key] = hit
    return hit


def assess(ctx: dict, sb: str, headers: dict) -> Optional[dict]:
    """-> verdict dict, or None when both listed QBs are available.

    verdict = {severity: 'out'|'questionable', side: 'home'|'away',
               qb, status, body_part, report_date, note}
    """
    if not isinstance(ctx, dict):
        return None
    game_week = ctx.get('week') if ctx.get('week') is not None else ctx.get('season_week')
    findings = []
    for side in ('home', 'away'):
        qb = ctx.get(f'{side}_qb_name')
        if not qb:
            continue
        team = ctx.get(f'{side}_team')
        row = latest_status(sb, headers, qb, team, game_week,
                            ctx.get('game_date'))
        if not row:
            continue
        status = str(row.get('injury_status') or '').strip().lower()
        practice = str(row.get('practice_status') or '').strip().lower()
        if status in OUT_STATUSES or practice in OUT_STATUSES:
            sev = 'out'
        elif status in SOFT_STATUSES or practice in SOFT_STATUSES:
            sev = 'questionable'
        else:
            continue          # Full, or anything unrecognised -> playing
        # How far back is this report? The two feeds disagree by a week, so
        # only a gap WIDER than that counts as stale.
        stale = None
        try:
            if game_week is not None and row.get('week') is not None:
                stale = int(game_week) - int(row['week'])
        except (TypeError, ValueError):
            stale = None
        if sev == 'out' and stale is not None and stale > 1:
            sev = 'questionable'   # too old to rule a starter out on
        findings.append({
            'severity': sev, 'side': side, 'team': team or side, 'qb': qb,
            'status': row.get('injury_status'),
            'practice_status': row.get('practice_status'),
            'body_part': row.get('body_part'),
            'report_date': row.get('report_date'),
            'report_week': row.get('week'), 'game_week': game_week,
            'weeks_stale': stale,
            'qb_ovr': ctx.get(f'{side}_qb_madden_ovr'),
        })
    if not findings:
        return None
    # An OUT outranks a QUESTIONABLE when both teams have a flag.
    findings.sort(key=lambda f: 0 if f['severity'] == 'out' else 1)
    top = dict(findings[0])
    # `all` holds COPIES, and top is a copy of findings[0] rather than
    # findings[0] itself. Assigning top['all'] = findings made the verdict
    # contain itself — findings[0] is top, so top['all'][0] is top — and
    # json.dumps raised "Circular reference detected" the moment the verdict
    # reached Jerry's prompt serializer, killing the whole read run.
    top['all'] = [dict(f) for f in findings]
    top['note'] = prose_note(top)
    return top


def prose_note(v: dict) -> str:
    """The sentence Jerry and the card should carry. Names the consequence."""
    if not v:
        return ''
    part = f" ({v['body_part']})" if v.get('body_part') else ''
    when = (f", reported {str(v['report_date'])[:10]}" if v.get('report_date')
            else (f", week {v['report_week']} report" if v.get('report_week') is not None else ''))
    ovr = ''
    if v.get('qb_ovr'):
        ovr = (f" The models also still carry his {v['qb_ovr']:.0f} QB rating "
               f"as a team input, so part of the projected edge is a "
               f"quarterback advantage that will not be on the field.")
    if v['severity'] == 'out':
        return (
            f"{v['team']} starting QB {v['qb']} is listed {v['status']}"
            f"{part}{when}. EVERY model number on this game — projected "
            f"score, total, margin and the Monte Carlo — was computed from "
            f"team efficiency accumulated WITH him playing, so those inputs "
            f"describe a team that will not take the field.{ovr} Treat "
            f"model-vs-market gaps as stale rather than as edge, and say so "
            f"in the read. The market has almost certainly already moved."
        )
    stale = ''
    if v.get('weeks_stale') is not None and v['weeks_stale'] > 1:
        stale = (f" Note the report is from week {v['report_week']} against a "
                 f"week {v['game_week']} game, so it may simply be out of "
                 f"date rather than current.")
    return (
        f"{v['team']} starting QB {v['qb']} is listed {v['status']}"
        f"{part}{when}. Model inputs assume he plays.{ovr}{stale} "
        f"Questionable quarterbacks usually do play, so this is a caveat "
        f"rather than a disqualifier, but the read should name it."
    )


def apply_to_pick(pp: Optional[dict], ctx: Optional[dict],
                  sb: str, headers: dict) -> Optional[dict]:
    """Cap tier + attach the verdict, in place. Returns pp. Never raises."""
    if not isinstance(pp, dict) or not isinstance(ctx, dict):
        return pp
    try:
        v = assess(ctx, sb, headers)
        if not v:
            return pp
        pp['_qb_injury'] = {k: v.get(k) for k in
                            ('severity', 'side', 'team', 'qb', 'status',
                             'body_part', 'report_date', 'report_week',
                             'game_week', 'weeks_stale', 'note')}
        cap, cconv = ((OUT_TIER_CAP, OUT_CONVICTION_CAP)
                      if v['severity'] == 'out'
                      else (SOFT_TIER_CAP, SOFT_CONVICTION_CAP))
        cur = str(pp.get('tier') or '').upper()
        capped = {'reason': f"{v['qb']} {v['status']}"}
        if _RANK.get(cap, 9) < _RANK.get(cur, 0):
            capped['tier_from'], capped['tier_to'] = cur, cap
            pp['tier'] = cap
        # Conviction is clamped INDEPENDENTLY of the tier. Today's PHI @ CHI
        # was already COVERAGE, so the tier never moved and an earlier version
        # of this left conviction at 79 under a 45 ceiling — a gate that
        # reported itself as applied while changing nothing the composer
        # ranks on.
        try:
            if pp.get('conviction') is not None and float(pp['conviction']) > cconv:
                capped['conviction_from'] = pp['conviction']
                capped['conviction_to'] = cconv
                pp['conviction'] = cconv
        except (TypeError, ValueError):
            pass
        if len(capped) > 1:
            pp['_qb_injury_cap'] = capped
        # Surface it in the sub-line the user actually reads.
        sub = str(pp.get('sub') or '')
        if 'QB' not in sub and v.get('qb'):
            tag = (f"⚠ {v['team']} QB {v['qb']} listed {v['status']} — "
                   f"model inputs predate the change")
            pp['sub'] = f'{sub} · {tag}' if sub else tag
    except Exception:
        pass
    return pp


if __name__ == '__main__':
    import os
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _e = Path(__file__).parent / '.env'
    for _l in _e.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())
    SB = os.environ['SUPABASE_URL']
    KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ['SUPABASE_KEY'])
    H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    date = sys.argv[1] if len(sys.argv) > 1 else '2026-09-28'
    games = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H,
                         params={'select': 'game_date,week,season_week,home_team,'
                                           'away_team,home_qb_name,away_qb_name,'
                                           'home_qb_madden_ovr,away_qb_madden_ovr,'
                                           'primary_play',
                                 'game_date': f'eq.{date}'}, timeout=30).json()
    print(f'=== QB injury gate · {date} · {len(games)} games ===')
    for g in games:
        v = assess(g, SB, H)
        head = f"  {g['away_team']} @ {g['home_team']}"
        if not v:
            print(f'{head}: both QBs available '
                  f'({g.get("away_qb_name")} / {g.get("home_qb_name")})')
            continue
        print(f'{head}: {v["severity"].upper()} — {v["team"]} {v["qb"]} '
              f'{v["status"]} ({v.get("body_part")}) '
              f'· report wk {v.get("report_week")} vs game wk {v.get("game_week")}')
        print(f'      {v["note"]}')
        pp = dict(g.get('primary_play') or {})
        if pp:
            before = (pp.get('tier'), pp.get('conviction'))
            apply_to_pick(pp, g, SB, H)
            print(f'      pick: {before[0]} conv {before[1]} '
                  f'-> {pp.get("tier")} conv {pp.get("conviction")}')
