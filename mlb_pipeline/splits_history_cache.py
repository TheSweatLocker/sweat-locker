"""Per-capture public-splits history: loader, capture clustering, movement features.

WHY THIS EXISTS (2026-11-27)
════════════════════════════
On 2026-11-27 I told Andy that NCAAB money-flow history did not exist, that
`splits_summary` was one aggregated snapshot per game, and that persisting a
per-capture history table was the highest-value data action available and
needed a migration.

That was wrong, and it is the trap already written down in
`feedback_a_zero_fill_column_can_have_a_populated_twin` and
`feedback_list_the_columns_before_declaring_data_lost`. The history has been
banked since 2026-08-23:

    public_splits_v2        791,942 rows    6 sports
      MLB    298,448    2026-08-23 -> 2026-10-28
      NCAAF  224,643    2026-08-23 -> 2026-11-26
      NFL    151,147    2026-09-03 -> 2026-11-27
      NHL     45,736    2026-09-24 -> 2026-11-27
      NCAAB   41,867    2026-10-30 -> 2026-11-27
      NBA     30,101    2026-10-03 -> 2026-11-27

    money_pct + bets_pct only, cz excluded:  482,922 usable rows

`public_splits_v2` carries `snapshot_ts` INSIDE its unique constraint
(game_id, market, side, source, metric, snapshot_ts), so every capture was
always kept. What collapses it is the AGGREGATOR: `compute_splits_summary`
keeps only the most recent value per (market, side, source, metric) and
PATCHes one JSONB onto game_context. The money-flow study measured that
collapsed column, so it saw one observation per game where the table holds
several, and it never saw movement at all.

Nothing here needs a migration. This module reads what is already there.

WHAT MOVEMENT BUYS THAT A SNAPSHOT CANNOT
─────────────────────────────────────────
A single capture answers "where is the money now". The history answers
"where did it come from", which is the question the sharp-money literature
actually turns on: steam, reverse line movement, and late money all require
two observations. `d_money` (last pre-game minus first) is the feature the
aggregator was discarding as it arrived.

TWO THINGS THAT WILL CORRUPT THIS IF IGNORED
────────────────────────────────────────────
1. POST-KICKOFF CAPTURES ARE IN THE TABLE. The 2026-09-29 guard in
   `splits_v2_pipeline.write_summary_to_ctx` refuses a post-kickoff PATCH of
   the summary, but it never deleted or filtered the long-form rows, and it
   measured how common they are: NCAAF 272 of 334 captures (81%) landed
   after kickoff, median 6.6h late; NFL 47 of 63 (75%), median 7.6h late.
   A "money flow" reading taken from a game in progress is a leak, not a
   signal. Every feature here is computed from captures STRICTLY BEFORE
   kickoff, and a game whose kickoff cannot be resolved is dropped rather
   than assumed.

2. ONE CRON RUN WRITES SEVERAL DISTINCT TIMESTAMPS. `snapshot_ts` defaults
   to NOW() per row, so sources in the same run land seconds apart - a real
   NCAAB game carried 16:38:21 and 16:38:43 from one pass. Counting distinct
   timestamps as captures therefore overcounts, and "first vs last" inside a
   single run is noise rather than movement. Timestamps are clustered with a
   gap threshold (CAPTURE_GAP_MIN) into capture events.

`cz` IS EXCLUDED, for the reasons the aggregator already documents: it
saturates at 0/100 on 24.3% of values, reports both sides of the same game
at 100%, and duplicates its ml row into rl verbatim. Excluded from the
averages, not from the table.

Usage:
    from splits_history_cache import load_history, load_kickoffs, build_features
    rows = load_history('NCAAF')              # cached to _splits_hist_NCAAF.pkl
    ko   = load_kickoffs('NCAAF')
    feat = build_features(rows, ko)
"""
from __future__ import annotations

import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get("SUPABASE_URL")
K = os.environ.get("SUPABASE_KEY")
H = {"apikey": K, "Authorization": f"Bearer {K}"}

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE = 1000                 # PostgREST caps a response at 1000 rows
CAPTURE_GAP_MIN = 20        # timestamps closer than this belong to one run
UNRELIABLE_SOURCES = {"cz"}
MARKETS = ("ml", "rl", "total")
METRICS = ("money_pct", "bets_pct")

SPORTS = ("MLB", "NFL", "NCAAF", "NCAAB", "NBA", "NHL")

# Kickoff resolution, per sport, verified 2026-11-27 rather than assumed.
# Each entry is (table, column) joined on public_splits_v2.game_id directly.
# Measured resolution on the most recent games of each sport:
#     NCAAF 33/33   NFL 15/15   NBA 20/20   NHL 29/29
#
# The key spaces are NOT uniform, which is why this map is explicit:
#     MLB    md5 hash         NFL   md5 hash        NCAAB md5 hash
#     NCAAF  composed slug    NBA   ESPN game id    NHL   NHL game id
# NFL and NCAAB splits ids are md5 and the RESULTS tables for those sports
# key on a slug, so a game_id -> game_id join to results returns zero rows in
# silence (feedback_two_tables_two_key_spaces). Kickoff is read from the
# per-sport game_context, which shares the md5 space, and football carries an
# explicit `results_game_id` bridge for the results join.
KICKOFF_SOURCE = {
    "NCAAF": ("ncaaf_game_results", "kickoff_utc"),
    "NFL":   ("nfl_game_context",   "kickoff_utc"),
    "NBA":   ("nba_game_context",   "commence_time_utc"),
    "NHL":   ("nhl_game_context",   "commence_time"),
    # NCAAB names its start-time column `game_time_et`, and the name lies:
    # the values are tz-aware UTC ISO timestamps (game_date 2026-12-02 ->
    # 2026-12-03T02:15:00+00:00, i.e. 21:15 ET the evening of the 2nd), not
    # ET wall clock. Filled 1195/1195. Parsed as UTC, which is what it is.
    #
    # Worth recording: `assert_slate_freshness.TIME_COL` maps NCAAB to
    # `kickoff_utc` on this table, and that column does not exist - a direct
    # select errors 42703. That freshness gate cannot be reading a start time
    # for NCAAB at all. `game_time_et` is the column it wants.
    "NCAAB": ("ncaab_game_context", "game_time_et"),
    # MLB has no start-time column on game_context OR game_results, and
    # line_history holds ZERO MLB rows. The pre-kickoff guard cannot be
    # honoured for MLB, so MLB is not served here rather than served wrong.
    "MLB":   (None, None),
}


def _p(msg: str) -> None:
    print(msg, flush=True)


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"_splits_hist_{name}.pkl")


def _getall(table: str, select: str, params: dict, label: str,
            key: str = "id") -> list[dict]:
    """Keyset-paged fetch over an ascending unique key.

    Keyset, not offset: the key must be unique and stable. Offset paging
    would both time out at depth and risk fabricating rows when the ordering
    key is not unique (feedback_offset_paging_on_a_nonunique_key_fabricates,
    feedback_deep_offset_paging_times_out).

    `key` is explicit because the tables here do NOT share one.
    `public_splits_v2` and `line_history` have a BIGSERIAL `id`; the
    per-sport `*_game_results` and `*_game_context` tables have none at all
    and are keyed on `game_id` (42703 on `id`, which is how this was found).
    A text key paginates lexicographically, which is fine for a keyset as
    long as it is unique - game_id is the primary key on those tables.
    """
    out: list[dict] = []
    last: object = None
    while True:
        q = dict(params)
        q.update({"select": select, "order": f"{key}.asc", "limit": str(PAGE)})
        if last is not None:
            # First page carries no key filter. A text keyset has no safe
            # "below everything" sentinel to seed with - `gt.` on an empty
            # value is not a comparison PostgREST can be trusted to make.
            q[key] = f"gt.{last}"
        r = requests.get(f"{SB}/rest/v1/{table}", params=q, headers=H, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"{table} {r.status_code}: {r.text[:200]}")
        batch = r.json() or []
        if not batch:
            break
        out.extend(batch)
        last = batch[-1][key]
        if len(out) % 20000 < PAGE:
            _p(f"    {label}: {len(out):,} rows")
        if len(batch) < PAGE:
            break
    return out


def load_history(sport: str, force: bool = False) -> list[dict]:
    """All usable per-capture split rows for a sport, cached to a pickle.

    Filtered at the server to the two metrics that carry a share
    (money_pct, bets_pct) and to the three real markets, with the
    unreliable source excluded. A re-run costs no DB.
    """
    path = _cache_path(sport)
    if os.path.exists(path) and not force:
        with open(path, "rb") as f:
            return pickle.load(f)
    _p(f"  loading {sport} splits history...")
    # Only `sport` is filtered server-side. Stacking metric/market/source/
    # value predicates on top of the keyset scan made Postgres abandon the
    # primary-key plan and time out (57014) on NBA, so the remaining
    # predicates are applied here instead. The extra rows transferred are
    # marginal (NBA 30,101 raw vs 24,772 filtered) and the query is stable.
    raw = _getall(
        "public_splits_v2",
        "id,snapshot_ts,game_id,market,side,source,metric,value",
        {"sport": f"eq.{sport}"}, sport)
    rows = [r for r in raw
            if r.get("metric") in METRICS
            and r.get("market") in MARKETS
            and r.get("source") not in UNRELIABLE_SOURCES
            and r.get("value") is not None]
    with open(path, "wb") as f:
        pickle.dump(rows, f)
    _p(f"  {sport}: {len(rows):,} usable rows cached (of {len(raw):,} pulled)")
    return rows


def load_kickoffs(sport: str, force: bool = False) -> dict[str, datetime]:
    """game_id -> kickoff (UTC, tz-aware). Empty when the sport has no source.

    A game absent from this map is DROPPED by build_features. That is
    deliberate: without a start time there is no way to tell a pre-game
    capture from an in-progress one, and an in-progress one is a leak.
    """
    tbl, col = KICKOFF_SOURCE.get(sport.upper(), (None, None))
    if not tbl:
        return {}
    path = _cache_path(f"{sport}_ko")
    if os.path.exists(path) and not force:
        with open(path, "rb") as f:
            return pickle.load(f)
    rows = _getall(tbl, f"game_id,{col}", {f"{col}": "not.is.null"},
                   f"{sport} kickoffs", key="game_id")
    out: dict[str, datetime] = {}
    for x in rows:
        t = _parse(x.get(col))
        if t and x.get("game_id"):
            out[str(x["game_id"])] = t
    with open(path, "wb") as f:
        pickle.dump(out, f)
    _p(f"  {sport}: {len(out):,} kickoffs")
    return out


def _parse(v) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def cluster_captures(stamps: list[datetime]) -> list[list[datetime]]:
    """Group timestamps into capture events, splitting on a gap.

    One cron pass writes a row per (source, market, side, metric), each with
    its own NOW(), so a single pass spans tens of seconds. Without this,
    "first vs last capture" can compare two sources inside one run and call
    the difference movement.
    """
    if not stamps:
        return []
    s = sorted(stamps)
    groups = [[s[0]]]
    for t in s[1:]:
        if (t - groups[-1][-1]) <= timedelta(minutes=CAPTURE_GAP_MIN):
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def build_features(rows: list[dict], kickoffs: dict[str, datetime]) -> dict:
    """(game_id, market, side) -> movement features from pre-kickoff captures.

    Returns a dict whose values carry:
        n_cap        capture events strictly before kickoff
        first_money  cross-source mean money_pct at the first such capture
        last_money   ... at the last such capture
        d_money      last_money - first_money          <- the new feature
        first_bets / last_bets / d_bets    same for bets_pct
        first_div / last_div / d_div       money_pct - bets_pct
        span_h       hours between first and last capture used
        lead_h       hours from the last capture used to kickoff
        n_src        sources contributing to the last capture
    A pair with a single pre-game capture keeps its level features and gets
    d_* = None, so a level test and a movement test can run on the same rows
    without the movement test silently inheriting zeros.
    """
    # (gid, market, side, metric) -> {ts: {source: value}}
    series: dict[tuple, dict] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        gid = str(r.get("game_id") or "")
        ko = kickoffs.get(gid)
        if not ko:
            continue                       # no start time -> cannot guard -> drop
        ts = _parse(r.get("snapshot_ts"))
        if not ts or ts >= ko:
            continue                       # post-kickoff capture is a leak
        src = r.get("source")
        if src in UNRELIABLE_SOURCES:
            continue
        try:
            val = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        series[(gid, r["market"], r["side"], r["metric"])][ts][src] = val

    # collapse timestamps into capture events, averaging across sources
    # (the aggregator averages across sources too; this keeps the comparison
    #  like-for-like and only changes the collapse over TIME)
    levels: dict[tuple, list[tuple[datetime, float, int]]] = {}
    for key, by_ts in series.items():
        groups = cluster_captures(list(by_ts.keys()))
        seq = []
        for g in groups:
            vals, srcs = [], set()
            for t in g:
                for s, v in by_ts[t].items():
                    vals.append(v)
                    srcs.add(s)
            if vals:
                seq.append((g[-1], sum(vals) / len(vals), len(srcs)))
        if seq:
            levels[key] = seq

    out: dict[tuple, dict] = {}
    pairs = {(g, m, s) for (g, m, s, _) in levels}
    for (gid, mkt, side) in pairs:
        mon = levels.get((gid, mkt, side, "money_pct")) or []
        bet = levels.get((gid, mkt, side, "bets_pct")) or []
        if not mon and not bet:
            continue
        ko = kickoffs.get(gid)
        base = mon or bet
        f: dict = {"n_cap": len(base), "n_src": base[-1][2],
                   "span_h": round((base[-1][0] - base[0][0]).total_seconds() / 3600.0, 2),
                   "lead_h": round((ko - base[-1][0]).total_seconds() / 3600.0, 2) if ko else None}
        for nm, seq in (("money", mon), ("bets", bet)):
            if seq:
                f[f"first_{nm}"] = round(seq[0][1], 2)
                f[f"last_{nm}"] = round(seq[-1][1], 2)
                f[f"d_{nm}"] = round(seq[-1][1] - seq[0][1], 2) if len(seq) > 1 else None
            else:
                f[f"first_{nm}"] = f[f"last_{nm}"] = f[f"d_{nm}"] = None
        for when in ("first", "last"):
            m, b = f.get(f"{when}_money"), f.get(f"{when}_bets")
            f[f"{when}_div"] = round(m - b, 2) if (m is not None and b is not None) else None
        f["d_div"] = (round(f["last_div"] - f["first_div"], 2)
                      if (f.get("last_div") is not None and f.get("first_div") is not None
                          and len(base) > 1) else None)
        out[(gid, mkt, side)] = f
    return out


def summarize(sport: str, force: bool = False) -> dict:
    """Coverage report for one sport. Prints the join rates, returns features."""
    rows = load_history(sport, force=force)
    ko = load_kickoffs(sport, force=force)
    games_in_rows = {str(r.get("game_id")) for r in rows}
    feat = build_features(rows, ko) if ko else {}
    games_kept = {g for (g, _, _) in feat}
    multi = sum(1 for f in feat.values() if (f.get("n_cap") or 0) > 1)
    _p(f"\n{sport}: {len(rows):,} rows | {len(games_in_rows):,} games in splits")
    if not ko:
        _p(f"  NO START TIME SOURCE -> cannot guard pre-kickoff -> 0 usable")
        return feat
    _p(f"  kickoffs known: {len(ko):,} | games with a pre-kickoff capture: "
       f"{len(games_kept):,} ({100.0*len(games_kept)/max(len(games_in_rows),1):.1f}%)")
    _p(f"  (game,market,side) pairs: {len(feat):,} | with 2+ captures: {multi:,} "
       f"({100.0*multi/max(len(feat),1):.1f}%)")
    return feat


if __name__ == "__main__":
    want = [a.upper() for a in sys.argv[1:]] or list(SPORTS)
    force = "--FORCE" in want
    want = [w for w in want if not w.startswith("--")]
    for s in want:
        try:
            summarize(s, force=force)
        except Exception as e:
            _p(f"{s}: FAILED {type(e).__name__}: {e}")
