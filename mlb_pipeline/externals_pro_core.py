"""Shared external-pick puller core for the pro leagues (NBA, NHL).

Andy 2026-09-21: "I want to work on getting nhl and nba models good to
go — externals, money flow, all things populated in game card."

There were no NBA or NHL external pullers at all. The shared fetcher
modules were already parameterised for both and their docstrings even
name `pull_externals_{nba,nhl}.py` as expected callers — the pullers
themselves were simply never written.

Rather than fork the 729-line NCAAB puller twice, the scaffolding lives
here once and each sport supplies a small config. NBA and NHL are a good
pair for this: both are 30-32 team leagues on a daily cadence, so they
share slate shape, matching rules and cron pattern.

Three sources work out of the box because their generic modules take a
league slug:
    oddscrowd      -> money flow (money% / bets% / divergence)
    scoresandodds  -> consensus
    pickdawgz      -> free handicapper picks

oddscrowd is the one that matters most for the ask: it is where the
money-flow numbers come from, and NBA context has no oddscrowd_snapshot
column populated today.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


@dataclass
class ExternalPick:
    game_id: str
    sport: str
    game_date: str
    source: str
    surface: str
    pick_side: Optional[str] = None
    pick_line: Optional[float] = None
    odds_american: Optional[int] = None
    confidence: Optional[str] = None
    raw_text: Optional[str] = None
    source_url: Optional[str] = None
    fade_flag: Optional[str] = None


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def today_et() -> str:
    return _et_now().strftime('%Y-%m-%d')


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=os.path.dirname(__file__) or '.',
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return 'unknown'


# ══ 2026-09-25 · COLUMN NAMES WERE WRONG, AND THE ERRORS WERE SWALLOWED ══
# Three of the fields written below did not exist on external_pull_log:
#     git_sha       -> agent_version
#     picks_written -> picks_pulled
#     error_text    -> error_message
# PostgREST rejects the whole insert on one unknown column (42703), the bare
# `except: pass` turned that into a None pull_id, and complete_pull_log then
# returns immediately on a None id. Net effect: SportPuller — which is the
# ONLY puller for NHL and NBA — has never written a single external_pull_log
# row. NFL/NCAAF/MLB were unaffected because their pullers log inline.
#
# That is why watchdog_external_sources reports "0 pull attempts" for NHL: it
# reads external_pull_log, so it was structurally blind to both sports. A
# broken source on the Oct 8 opener would have looked exactly like a healthy
# offseason one.
#
# The excepts now report instead of passing silently. A logging failure still
# must not abort a pull — but it must not be invisible either.
def start_pull_log(source: str, sport: str, triggered_by: str) -> Optional[str]:
    try:
        r = requests.post(
            f'{SB}/rest/v1/external_pull_log',
            headers={**H_WRITE, 'Prefer': 'return=representation'},
            json={'source': source, 'sport': sport,
                  'scheduled_at': _et_now().isoformat(),
                  'started_at': _et_now().isoformat(),
                  'status': 'running', 'triggered_by': triggered_by,
                  'agent_version': _git_sha()}, timeout=15)
        if r.status_code in (200, 201) and r.json():
            return r.json()[0].get('id')
        print(f'  ⚠ pull_log insert -> {r.status_code}: {(r.text or "")[:160]}')
    except Exception as e:
        print(f'  ⚠ pull_log insert failed: {e}')
    return None


def complete_pull_log(pull_id, status, picks=0, games=0, err=None, ms=None):
    if not pull_id:
        return
    body = {'status': status, 'completed_at': _et_now().isoformat(),
            'picks_pulled': picks, 'games_covered': games}
    if err:
        body['error_message'] = str(err)[:500]
    if ms is not None:
        body['duration_ms'] = ms
    try:
        r = requests.patch(f'{SB}/rest/v1/external_pull_log?id=eq.{pull_id}',
                           headers={**H_WRITE, 'Prefer': 'return=minimal'},
                           json=body, timeout=15)
        if r.status_code not in (200, 204):
            print(f'  ⚠ pull_log update -> {r.status_code}: {(r.text or "")[:160]}')
    except Exception as e:
        print(f'  ⚠ pull_log update failed: {e}')


def write_picks(picks: list, pull_id) -> int:
    if not picks:
        return 0
    payload = []
    for p in picks:
        d = asdict(p) if not isinstance(p, dict) else dict(p)
        d['pull_id'] = pull_id
        payload.append(d)
    try:
        r = requests.post(
            f'{SB}/rest/v1/external_picks'
            f'?on_conflict=source,game_id,surface,game_date,pick_side',
            headers={**H_WRITE,
                     'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json=payload, timeout=25)
        if r.status_code not in (200, 201, 204):
            print(f'  ⚠ picks write {r.status_code}: {r.text[:160]}')
            return 0
        return len(payload)
    except Exception as e:
        print(f'  ⚠ picks write exception: {e}')
        return 0


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', str(s or '').lower()).strip()


def team_side(hint: str, home: str, away: str) -> Optional[str]:
    """Which side `hint` names, or None when ambiguous.

    Exactness first. NBA and NHL both carry same-city pairs that a naive
    substring match gets wrong — Rangers/Islanders, Lakers/Clippers,
    Kings (LA NHL) vs Kings (Sacramento NBA within their own league) —
    so a hint that fits BOTH sides is refused rather than guessed. Same
    rule as externals_pickdawgz._side_for, which exists because 'Iowa'
    once matched 'Northern Iowa' and cost a real grade.
    """
    t, h, a = _norm(hint), _norm(home), _norm(away)
    if not t:
        return None
    if t == h and t != a:
        return 'HOME'
    if t == a and t != h:
        return 'AWAY'
    ch, ca = bool(h) and h in t, bool(a) and a in t
    if ch and not ca:
        return 'HOME'
    if ca and not ch:
        return 'AWAY'
    sh, sa = bool(h) and t in h, bool(a) and t in a
    if sh and not sa:
        return 'HOME'
    if sa and not sh:
        return 'AWAY'
    hw = h.split()[-1] if h else ''
    aw = a.split()[-1] if a else ''
    nh = bool(hw) and re.search(rf'\b{re.escape(hw)}\b', t) is not None
    na = bool(aw) and re.search(rf'\b{re.escape(aw)}\b', t) is not None
    if nh and not na:
        return 'HOME'
    if na and not nh:
        return 'AWAY'
    return None


class SportPuller:
    """One pro league's external pull."""

    def __init__(self, sport: str, ctx_table: str, results_table: str,
                 oddscrowd_sport_slug: str, league_slug: str,
                 horizon_days: int = 2):
        self.sport = sport.upper()
        self.ctx_table = ctx_table
        self.results_table = results_table
        self.oddscrowd_sport_slug = oddscrowd_sport_slug
        self.league_slug = league_slug
        self.horizon_days = horizon_days

    # ── slate ────────────────────────────────────────────────────────
    def load_slate(self, game_date: str) -> list:
        """Upcoming games in a forward window. Context first (carries the
        model row), results table as fallback before context is built."""
        hi = (datetime.fromisoformat(game_date)
              + timedelta(days=self.horizon_days)).date().isoformat()
        for tbl in (self.ctx_table, self.results_table):
            # A dict cannot carry two `game_date` keys — the second
            # silently overwrites the first, leaving only the upper bound
            # and returning the oldest 1000 rows in the table. Use a list
            # of tuples so BOTH bounds reach PostgREST.
            params = [('select', 'game_id,home_team,away_team,game_date'),
                      ('game_date', f'gte.{game_date}'),
                      ('game_date', f'lte.{hi}'),
                      ('order', 'game_date.asc'),
                      ('limit', '500')]
            try:
                r = requests.get(f'{SB}/rest/v1/{tbl}', params=params,
                                 headers=H_READ, timeout=20)
            except requests.RequestException:
                continue
            if r.status_code == 200 and r.json():
                return r.json()
        return []

    def find_game_id(self, slate: list, home_hint: str, away_hint: str):
        """Both sides must resolve to the SAME game and to EXACTLY ONE game.

        Returning the first match is not good enough here. A hint like
        "New York" is unambiguous *within* Rangers-vs-Boston — the other
        side is Boston — but the slate can hold Rangers AND Islanders on
        the same night, so first-match silently attaches the pick to
        whichever game happens to be ordered first. Same for a Lakers /
        Clippers night. Collect every candidate and refuse unless exactly
        one survives; a wrong attribution is worse than no pick.
        """
        hits = []
        for g in slate:
            h, a = g.get('home_team'), g.get('away_team')
            if team_side(home_hint, h, a) == 'HOME' and \
               team_side(away_hint, h, a) == 'AWAY':
                hits.append(g['game_id'])
        return hits[0] if len(hits) == 1 else None

    # ── fetchers (all three are shared generics) ─────────────────────
    def fetch_oddscrowd(self, slate, game_date):
        from externals_oddscrowd import fetch_oddscrowd_generic
        dicts, status = fetch_oddscrowd_generic(
            sport_url_slug=self.oddscrowd_sport_slug,
            league_slug=self.league_slug, sport_code=self.sport,
            game_date=game_date, slate=slate,
            find_game_id_fn=lambda s, home_hint, away_hint:
                self.find_game_id(s, home_hint, away_hint))
        return [ExternalPick(**{k: d.get(k) for k in
                                ('game_id', 'sport', 'game_date', 'source',
                                 'surface', 'pick_side', 'pick_line',
                                 'odds_american', 'confidence', 'raw_text',
                                 'source_url', 'fade_flag')})
                for d in dicts], status

    def fetch_scoresandodds(self, slate, game_date):
        from externals_scoresandodds import fetch_scoresandodds_generic
        dicts, status = fetch_scoresandodds_generic(
            league_slug=self.league_slug, sport_code=self.sport,
            game_date=game_date, slate=slate,
            find_game_id_fn=lambda s, home_hint, away_hint:
                self.find_game_id(s, home_hint, away_hint))
        return [ExternalPick(**{k: d.get(k) for k in
                                ('game_id', 'sport', 'game_date', 'source',
                                 'surface', 'pick_side', 'pick_line',
                                 'odds_american', 'confidence', 'raw_text',
                                 'source_url', 'fade_flag')})
                for d in dicts], status

    def fetch_pickdawgz(self, slate, game_date):
        from externals_pickdawgz import fetch_pickdawgz_generic
        return fetch_pickdawgz_generic(
            sport=self.sport, game_date=game_date, slate=slate,
            find_game_id_fn=lambda s, home_hint, away_hint:
                self.find_game_id(s, home_hint, away_hint),
            make_pick_fn=ExternalPick)

    def fetchers(self) -> dict:
        return {'oddscrowd': self.fetch_oddscrowd,
                'scoresandodds': self.fetch_scoresandodds,
                'pickdawgz': self.fetch_pickdawgz}

    # ── run ──────────────────────────────────────────────────────────
    def run(self, game_date: Optional[str] = None, sources=None,
            triggered_by: str = 'manual', dry_run: bool = False) -> int:
        gd = game_date or today_et()
        slate = self.load_slate(gd)
        print(f'=== {self.sport} external pull · {gd} · {triggered_by} ===')
        print(f'  slate: {len(slate)} games in a {self.horizon_days}d window')
        if not slate:
            print(f'  no {self.sport} games — nothing to pull '
                  f'(preseason or off-day, not an error)')
            return 0
        chosen = sources or list(self.fetchers().keys())
        total = 0
        ok = fail = 0
        for name in chosen:
            fn = self.fetchers().get(name)
            if not fn:
                print(f'  ⚠ unknown source {name}')
                continue
            pull_id = None if dry_run else start_pull_log(name, self.sport, triggered_by)
            t0 = _et_now()
            try:
                picks, status = fn(slate, gd)
            except Exception as e:
                fail += 1
                print(f'  ✗ {name}: {type(e).__name__}: {e}')
                complete_pull_log(pull_id, 'failed', err=e)
                continue
            ms = int((_et_now() - t0).total_seconds() * 1000)
            games = len({p.game_id for p in picks})
            if dry_run:
                print(f'  [DRY] {name}: {len(picks)} picks / {games} games ({ms}ms)')
                total += len(picks)
                ok += 1
                continue
            n = write_picks(picks, pull_id)
            # 2026-09-25: was 'ok' / 'partial'. external_pull_log has a CHECK
            # constraint permitting only success / failed / running, so every
            # completion PATCH was rejected with 23514 and each row stayed
            # 'running' forever. Paired with the wrong column names above,
            # this is why SportPuller sports never appeared in the watchdog.
            # A non-200 fetch is 'failed', not a softer word the table would
            # reject — the constraint is the contract.
            complete_pull_log(pull_id, 'success' if status == 200 else 'failed',
                              picks=n, games=games, ms=ms)
            print(f'  ✓ {name}: {n} picks / {games} games ({ms}ms)')
            total += n
            ok += 1
        print(f'\n=== Summary === sources ok/fail: {ok}/{fail} · picks: {total}')
        return total
