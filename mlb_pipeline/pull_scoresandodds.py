"""ScoresAndOdds public-splits scraper (2026-08-23 Phase 2).

Adds ScoresAndOdds as the 4th source alongside OddsCrowd + Fadereport + Cleatz.
Writes directly to public_splits_v2 (long-form, source='so').

Page structure per game per market:
    <div class="trend-card consensus consensus-table-{market}--{gameIdx} active"
         data-group="consensus-table--{gameIdx}">
      <div class="event-header">
        <div class="team-pennant left small"><span class="team-name">Cardinals</span></div>
        <div class="event-info"><a href="/mlb/cardinals-vs-phillies">...</a>
             <span data-role="localtime" data-value="2026-08-23T17:35:00Z"></span></div>
        <div class="team-pennant right small"><span class="team-name">Phillies</span></div>
      </div>
      <div class="module-body"><ul class="trend-graphs">
        <li class="consensus active">
          <span class="trend-graph-chart" data-content='<div data-event="mlb/202088450" data-market="moneyline" ...'>
            <span class="trend-graph-sides"><strong>STL</strong><span>% of Bets</span><strong>PHI</strong></span>
            <span class="trend-graph-percentage">
              <span class="percentage-a">14%</span>
              <span class="percentage-b">86%</span>
            </span>
            <span class="trend-graph-percentage" style="...second row is money">
              <span class="percentage-a">45%</span>
              <span class="percentage-b">55%</span>
            </span>
          </span>
        </li>
      </ul></div>
    </div>

For markets='total', sides are OVER/AWAY-side and UNDER/HOME-side (not team names).

Usage:
    python pull_scoresandodds.py                        # MLB today, all markets
    python pull_scoresandodds.py --sport MLB --date 2026-08-23
    python pull_scoresandodds.py --dry-run
"""
import argparse, os, sys, re, json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get("SUPABASE_URL")
K = os.environ.get("SUPABASE_KEY")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

H_READ = {"apikey": K, "Authorization": f"Bearer {K}"}
H_WRITE = {**H_READ, "Content-Type": "application/json",
           "Prefer": "resolution=merge-duplicates,return=minimal"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

SPORT_URL = {
    "MLB":   "https://www.scoresandodds.com/mlb/consensus-picks",
    "NFL":   "https://www.scoresandodds.com/nfl/consensus-picks",
    "NCAAF": "https://www.scoresandodds.com/ncaaf/consensus-picks",
    "NCAAB": "https://www.scoresandodds.com/ncaab/consensus-picks",
    "NBA":   "https://www.scoresandodds.com/nba/consensus-picks",
    "NHL":   "https://www.scoresandodds.com/nhl/consensus-picks",
}

SPORT_CTX_TABLE = {
    "MLB":   "mlb_game_context",
    "NFL":   "nfl_game_context",
    "NCAAF": "ncaaf_game_context",
    "NCAAB": "ncaab_game_context",
    "NBA":   "nba_game_context",
    "NHL":   "nhl_game_context",
}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def fetch_page(sport: str) -> str | None:
    url = SPORT_URL.get(sport)
    if not url: return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        if r.status_code != 200:
            print(f"  ⚠ HTTP {r.status_code} from {url}")
            return None
        return r.text
    except Exception as e:
        print(f"  ⚠ fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# PARSER — regex-based to avoid BeautifulSoup dependency drift
# ═══════════════════════════════════════════════════════════════════════

# One trend-card per (market, game-index). Grabs the full inner up to next
# trend-card or end of container. The `market` and `gameIdx` come from
# the class suffix, e.g. consensus-table-moneyline--0
CARD_START_RE = re.compile(
    r'<div\s+class="trend-card consensus consensus-table-(?P<market>moneyline|spread|total)--(?P<idx>\d+)[^"]*"[^>]*>',
    re.IGNORECASE,
)
# Team-pennant blocks — extract team-name text (left/right)
TEAM_NAME_RE = re.compile(
    r'<div\s+class="team-pennant\s+(?P<pos>left|right)\s+small">\s*'
    r'(?:<span[^>]*team-flag[^>]*>.*?</span>\s*)?'
    r'<span\s+class="team-name">\s*<span>\s*(?P<name>[^<]+)\s*</span>',
    re.IGNORECASE | re.DOTALL,
)
# ..and where pennant is right-side (name comes first)
TEAM_NAME_RIGHT_RE = re.compile(
    r'<div\s+class="team-pennant\s+right\s+small">\s*'
    r'<span\s+class="team-name">\s*<span>\s*(?P<name>[^<]+)\s*</span>',
    re.IGNORECASE | re.DOTALL,
)
# Event slug (helps match to internal game_id)
EVENT_SLUG_RE = re.compile(
    r'<a\s+href="/(?:mlb|nfl|ncaaf|ncaab|nba|nhl)/([a-z0-9-]+)"',
    re.IGNORECASE,
)
# SO's internal event id  data-event="mlb/202088450"
DATA_EVENT_RE = re.compile(r'data-event="([^"]+)"', re.IGNORECASE)
# Two consecutive trend-graph-percentage spans per card: first row = bets, second = money
PCT_RE = re.compile(
    r'<span\s+class="trend-graph-percentage"[^>]*>\s*'
    r'<span\s+class="percentage-a"[^>]*>(?P<a>\d+(?:\.\d+)?)%</span>\s*'
    r'<span\s+class="percentage-b"[^>]*>(?P<b>\d+(?:\.\d+)?)%</span>\s*'
    r'</span>',
    re.IGNORECASE | re.DOTALL,
)


def _find_card_blocks(html: str) -> list[tuple[str, int, str]]:
    """Return list of (market, gameIdx, card_html)."""
    out = []
    starts = list(CARD_START_RE.finditer(html))
    for i, m in enumerate(starts):
        start = m.start()
        end = starts[i+1].start() if i+1 < len(starts) else len(html)
        out.append((m.group("market").lower(), int(m.group("idx")), html[start:end]))
    return out


def parse_card(market: str, idx: int, card_html: str, sport: str) -> dict | None:
    """Return parsed dict or None on parse error. Structure:
      {sport, so_slug, so_event, market, teams:{left,right}, sides:{
        HOME/OVER: {bets_pct, money_pct}, AWAY/UNDER: {bets_pct, money_pct}}}"""
    slug_m = EVENT_SLUG_RE.search(card_html)
    slug = slug_m.group(1) if slug_m else None
    event_m = DATA_EVENT_RE.search(card_html)
    so_event = event_m.group(1) if event_m else None

    # Team names left/right
    left = right = None
    for tm in TEAM_NAME_RE.finditer(card_html):
        if tm.group("pos") == "left" and not left:
            left = tm.group("name").strip()
    rm = TEAM_NAME_RIGHT_RE.search(card_html)
    if rm: right = rm.group("name").strip()

    # Two percentage rows: bets, money
    pcts = list(PCT_RE.finditer(card_html))
    if len(pcts) < 2:
        return None
    bets_a = float(pcts[0].group("a")); bets_b = float(pcts[0].group("b"))
    money_a = float(pcts[1].group("a")); money_b = float(pcts[1].group("b"))

    # Map A/B to canonical sides per market convention.
    # For SO: left team is AWAY, right team is HOME (standard). For totals:
    # A = OVER, B = UNDER (SO convention on their consensus page).
    if market in ("moneyline", "spread"):
        side_a, side_b = "AWAY", "HOME"
    elif market == "total":
        side_a, side_b = "OVER", "UNDER"
    else:
        return None

    return {
        "sport": sport, "so_slug": slug, "so_event": so_event,
        "market_native": market,  # keep for debug
        "market": {"moneyline": "ml", "spread": "rl", "total": "total"}[market],
        "teams": {"left": left, "right": right},
        "sides": {
            side_a: {"bets_pct": bets_a, "money_pct": money_a},
            side_b: {"bets_pct": bets_b, "money_pct": money_b},
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# TEAM NAME RESOLUTION — join SO's slug/name to our game_id
# ═══════════════════════════════════════════════════════════════════════

# SO uses short team names ("Cardinals", "Phillies"). Our ctx tables use
# full names ("St. Louis Cardinals", "Philadelphia Phillies"). Lookup:
# for each game_id today, take home_team/away_team and match by
# endswith(so_short) or substring. Cheap + robust for MLB. Cross-sport
# extension will need per-sport alias tables (Phase 3).

def _load_game_id_index(sport: str, game_date: str) -> dict:
    """Return {(away_short, home_short): game_id} for games in a horizon
    starting at game_date. SO shows the upcoming slate (next week), not just
    today, so we look up a window of ctx games to match against.
    2026-08-23: extended from single-date to 10-day horizon so preview-mode
    NCAAF cards (opener Aug 30 shown Aug 23) can correlate."""
    tbl = SPORT_CTX_TABLE.get(sport)
    if not tbl: return {}
    from datetime import datetime as _dt, timedelta as _td
    try:
        start = _dt.strptime(game_date, "%Y-%m-%d").date()
    except ValueError:
        start = _dt.now().date()
    end = (start + _td(days=10)).isoformat()
    r = requests.get(f"{SB}/rest/v1/{tbl}",
                     headers=H_READ,
                     params={"and": f"(game_date.gte.{start.isoformat()},game_date.lte.{end})",
                             "select": "game_id,home_team,away_team",
                             "limit": 200},
                     timeout=15)
    if r.status_code != 200: return {}
    idx = {}
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        h = row.get("home_team",""); a = row.get("away_team","")
        gid = row.get("game_id")
        if h and a and gid:
            # Store multiple keys: full name, last word (short), lower
            idx[(a.split()[-1].lower(), h.split()[-1].lower())] = gid
            idx[(a.lower(), h.lower())] = gid
    return idx


def _resolve_game_id(idx: dict, away_short: str, home_short: str) -> str | None:
    if not away_short or not home_short: return None
    return (idx.get((away_short.lower(), home_short.lower()))
            or idx.get((away_short.split()[-1].lower(), home_short.split()[-1].lower())))


def to_v2_rows(cards: list[dict], gid_idx: dict, sport: str,
               snapshot_ts: str) -> list[dict]:
    """Convert parsed cards → public_splits_v2 rows."""
    rows: list[dict] = []
    for c in cards:
        teams = c.get("teams") or {}
        left = teams.get("left"); right = teams.get("right")
        # For ML/RL, left=AWAY, right=HOME → we resolve game_id via (left, right)
        if c["market"] in ("ml", "rl"):
            gid = _resolve_game_id(gid_idx, left or "", right or "")
        else:
            # Totals — try both orderings, SO events sometimes flip
            gid = (_resolve_game_id(gid_idx, left or "", right or "")
                   or _resolve_game_id(gid_idx, right or "", left or ""))
        if not gid:
            continue  # can't correlate — skip
        for side, metrics in (c.get("sides") or {}).items():
            for metric, val in metrics.items():
                if val is None: continue
                rows.append({
                    "snapshot_ts": snapshot_ts,
                    "sport": sport,
                    "game_id": gid,
                    "market": c["market"],
                    "side": side,
                    "source": "so",
                    "metric": metric,
                    "value": float(val),
                    "source_url": f"https://www.scoresandodds.com/{sport.lower()}/consensus-picks",
                    "raw_scrape": {"so_slug": c.get("so_slug"), "so_event": c.get("so_event"),
                                   "left_team": left, "right_team": right},
                })
    return rows


def upsert_v2(rows: list[dict], dry: bool) -> int:
    if not rows: return 0
    # 2026-08-23 batch dedup: SO's DOM repeats each trend-card ~15x (nested
    # wrappers). Parser produces duplicate rows per (game_id, market, side,
    # source, metric, snapshot_ts). Prior version 500-ed on the unique
    # constraint. Same fix as splits_v2_pipeline: keep the last occurrence
    # per key.
    deduped: dict[tuple, dict] = {}
    for row in rows:
        key = (row.get("game_id"), row.get("market"), row.get("side"),
               row.get("source"), row.get("metric"), row.get("snapshot_ts"))
        deduped[key] = row
    rows = list(deduped.values())
    if dry:
        print(f"  [DRY] would upsert {len(rows)} v2 rows (deduped)")
        return 0
    written = 0; CHUNK = 500
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i+CHUNK]
        r = requests.post(f"{SB}/rest/v1/public_splits_v2?on_conflict=game_id,market,side,source,metric,snapshot_ts",
                          headers=H_WRITE, json=batch, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(batch)
        else:
            print(f"  ⚠ upsert HTTP {r.status_code}: {r.text[:150]}")
            break
    return written


def run(sport: str, game_date: str, dry: bool) -> int:
    print(f"\n=== ScoresAndOdds · {sport} · {game_date} ===")
    html = fetch_page(sport)
    if not html:
        print("  no HTML fetched — abort")
        return 0

    cards_raw = _find_card_blocks(html)
    print(f"  found {len(cards_raw)} trend-cards ({len({(m,i) for m,i,_ in cards_raw})} unique (market,idx) pairs)")
    parsed = []
    for market, idx, card_html in cards_raw:
        c = parse_card(market, idx, card_html, sport)
        if c: parsed.append(c)
    print(f"  parsed {len(parsed)} cards successfully")

    gid_idx = _load_game_id_index(sport, game_date)
    print(f"  {len(gid_idx)//2} games in {SPORT_CTX_TABLE.get(sport)} today (index has {len(gid_idx)} lookup keys)")

    snapshot_ts = datetime.now(timezone.utc).isoformat()
    rows = to_v2_rows(parsed, gid_idx, sport, snapshot_ts)
    print(f"  correlated {len(rows)} rows to game_ids")

    written = upsert_v2(rows, dry)
    print(f"  → v2 upserted: {written}")
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sport", default="MLB", choices=list(SPORT_URL.keys()) + ["ALL"])
    p.add_argument("--date", default=today_et())
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    sports = list(SPORT_URL.keys()) if args.sport == "ALL" else [args.sport]
    total = 0
    for s in sports:
        total += run(s, args.date, args.dry_run)
    print(f"\nTOTAL v2 rows: {total}")


if __name__ == "__main__":
    main()
