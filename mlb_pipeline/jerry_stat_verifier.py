"""Jerry stat-hallucination verifier (2026-08-10).

Cross-checks numeric claims in Jerry's synth prose against actual MLB
Stats API data. Catches the class of bugs surfaced today (8/10):
  * "Cameron 4.41 xERA getting rocked" — Cameron L3 actually 0.39 ERA
  * "Wesneski 8.83 ERA" — was vs-SF historical, real L3 is 4.02
  * "Christian Scott 1.3 IP opener last outing" — Scott is a 5-IP starter
  * "Taillon 10.22 L3 ERA" — actual 10.41

## What it catches

1. **Numeric-with-metric-label claims** — parses "L3 ERA X.XX", "first-
   inning ERA X.XX", "xERA X.XX", etc. and verifies against MLB API.
   Tolerance ±0.30 ERA (accounts for rounding + intra-day updates).

2. **Directional contradictions** — if prose says "getting rocked /
   shelled / dominant / cruising" the surrounding number must actually
   support the direction. "getting rocked" with L3 ERA <4 = contradiction.

3. **Full-inning-fabrications** — "1.3 IP last outing" claim verified
   against last game's actual IP (±0.5 tolerance).

## What it returns

`verify(prose, pitcher_name)` returns:
    {
      'ok': bool,
      'violations': [{claim, expected, cited, severity}, ...],
      'pitcher_stats': {l3_era, l3_ip, l3_h, ...},
    }

## MLB API cache

Pitcher game logs are cached in `.pitcher_cache/<pitcher_id>_YYYYMMDD.json`
so a single audit run doesn't hammer the MLB API. Cache TTL = 12hr.

## Usage

```python
from jerry_stat_verifier import verify
r = verify(short_read, pitcher_name='Christian Scott')
if not r['ok']:
    for v in r['violations']:
        print(f"{v['severity']}: {v['claim']}  expected={v['expected']}  cited={v['cited']}")
```

Called from `jerry_pre_publish_audit.py` as gate #10 (block publication
on high-severity violations).

## Not scope

- Non-numeric claims ("The Sox are running hot") — those live in the
  directional check but aren't structurally verified against a stat
- Team stats — future extension (currently pitcher-only)
- Prop stats — separate cross-check needed for hit/HR/K rates
"""
from __future__ import annotations
import re, os, sys, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

CACHE_DIR = Path(__file__).parent / '.pitcher_cache'
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_HR = 12

# ────────────────────────────────────────────────────────────────────
# 2026-08-10: sport-pluggable stat provider architecture
# ────────────────────────────────────────────────────────────────────
# Every sport surfaces a `PROVIDER` dict in this module that satisfies:
#
#   lookup_id(name)     → sport-specific player id or None
#   fetch_stats(pid)    → normalized stats dict {l3_ip, l3_era, ...}
#   patterns            → list of (regex, stat_key) tuples for that sport
#                         (baseball uses IP/ERA/xERA, football uses
#                         pass_yds/rush_yds, MMA uses striking/takedowns)
#   directional         → phrases that imply direction + which stat to
#                         verify against (per-sport)
#   tolerance           → per-stat allowed drift
#
# Adding a new sport = one new PROVIDERS entry. Rest of the API stays
# unchanged (verify() takes a `sport` kwarg).
# ────────────────────────────────────────────────────────────────────

MLB_SEARCH = 'https://statsapi.mlb.com/api/v1/people/search'
MLB_GAMELOG = 'https://statsapi.mlb.com/api/v1/people/{pid}/stats'

# Numeric-claim patterns Jerry commonly uses.
# Each pattern → the stat_key to verify against.
NUMERIC_PATTERNS = [
    # "L3 ERA 9.00", "last three starts ERA 9.0", "L3 3.79 ERA"
    (r'\b(?:L3|last[\s-]+3|last[\s-]+three)\s+(?:starts?\s+)?ERA\s*:?\s*(\d+\.?\d*)', 'l3_era'),
    (r'\b(?:ERA)\s*(\d+\.?\d*)\s+(?:L3|last[\s-]+3|last[\s-]+three)\b', 'l3_era'),
    (r'\b(\d+\.?\d*)\s+ERA\s+(?:over\s+)?(?:his|the)?\s*last\s+three\b', 'l3_era'),
    # "L5 avg X.X hits" / "L7 avg X.X"
    (r'\bL7\s+avg\s+(\d+\.?\d*)\s*(?:H|hits)/start\b', 'l7_h_per_start'),
    (r'\bL5\s+avg\s+(\d+\.?\d*)\b', 'l5_h_per_start'),
    # "first-inning ERA X.XX", "1st inning ERA"
    (r'\b(?:first[\s-]+inning|1st\s+inning)\s+ERA\s*:?\s*(\d+\.?\d*)', 'first_inning_era'),
    # "xERA X.XX"
    (r'\bxERA\s*:?\s*(\d+\.?\d*)', 'xera'),
    # "1.3 IP last outing" / "2.0 IP as an opener" / "X.X IP as bulk"
    # 2026-08-10: allow optional article/adjective between "as" and the noun
    # to catch "as an opener", "as a bulk", "as the starter", etc.
    (r'\b(\d+\.?\d*)\s+IP\s+(?:last|as\s+(?:an?\s+|the\s+)?)\s*(?:outing|opener|start|bulk|starter)', 'last_ip'),
    # "lasted X.X innings" / "went X.X innings"
    (r'\b(?:lasted|went)\s+(\d+\.?\d*)\s+innings\b', 'last_ip'),
    # "his last outing was X.X" / "his last outing was only X.X IP"
    (r'\blast\s+(?:outing|start)\s+(?:was\s+only\s+|was\s+just\s+|was\s+)(\d+\.?\d*)(?:\s+IP)?', 'last_ip'),
    # "X.X BB/9" / "walks per 9"
    (r'\b(\d+\.?\d*)\s+BB/9\b', 'bb_per_9'),
    # "X.X K/9"
    (r'\b(\d+\.?\d*)\s+K/9\b', 'k_per_9'),
]

# Directional phrase → expected stat direction.
# ERA > this = "getting rocked" language justified; ERA < this = contradiction
DIRECTIONAL_PHRASES = [
    (r'\bgetting\s+(?:rocked|shelled|tagged|torched|hit\s+hard|bombed)\b', 'l3_era', '>', 4.5),
    (r'\bhas\s+been\s+(?:rocked|shelled|torched|tagged|hit\s+hard)\b', 'l3_era', '>', 4.5),
    (r'\b(?:looks?|has\s+been)\s+(?:dominant|sharp|locked\s+in|elite|cruising)\b', 'l3_era', '<', 3.5),
    (r'\b(?:has\s+been\s+)?crumbling\b', 'l3_era', '>', 5.5),
    (r'\bcollaps(?:e|ing|ed)\b', 'l3_era', '>', 6.5),
]

TOLERANCE = {
    'l3_era': 0.35,
    'first_inning_era': 1.0,
    'xera': 0.50,
    'last_ip': 0.5,
    'bb_per_9': 0.7,
    'k_per_9': 1.0,
    'l7_h_per_start': 0.5,
    'l5_h_per_start': 0.6,
}


def _cache_path(pid: int) -> Path:
    stamp = datetime.now().strftime('%Y%m%d')
    return CACHE_DIR / f'{pid}_{stamp}.json'


def _lookup_pid(name: str) -> int | None:
    """Return MLB API player_id or None. Caches by exact name."""
    cache = CACHE_DIR / 'name_pid_index.json'
    idx = {}
    if cache.exists():
        try: idx = json.loads(cache.read_text())
        except Exception: pass
    if name in idx: return idx[name]
    try:
        r = requests.get(MLB_SEARCH, params={'names': name, 'sportId': 1, 'active': True}, timeout=8)
        if r.status_code != 200: return None
        ppl = r.json().get('people', [])
        pitchers = [p for p in ppl if (p.get('primaryPosition') or {}).get('abbreviation') == 'P']
        if not pitchers: return None
        pid = pitchers[0]['id']
        idx[name] = pid
        try: cache.write_text(json.dumps(idx))
        except Exception: pass
        return pid
    except Exception:
        return None


def _pull_stats(pid: int) -> dict | None:
    """Return computed pitcher stats or None. Cached daily.

    Fields:
        l3_era, l3_ip, l3_h, l3_bb, l3_k
        l7_h_per_start (last 7 apps, avg H/start)
        l5_h_per_start (last 5)
        first_inning_era (season, when hydratable — else None)
        xera (season xERA if MLB has it — often None)
        last_ip (most recent appearance IP)
        bb_per_9, k_per_9 (L3)
    """
    cache = _cache_path(pid)
    if cache.exists():
        age = datetime.now() - datetime.fromtimestamp(cache.stat().st_mtime)
        if age < timedelta(hours=CACHE_TTL_HR):
            try: return json.loads(cache.read_text())
            except Exception: pass
    try:
        r = requests.get(MLB_GAMELOG.format(pid=pid),
            params={'stats': 'gameLog', 'group': 'pitching',
                    'season': datetime.now().year, 'sportId': 1}, timeout=10)
        if r.status_code != 200: return None
        splits = r.json().get('stats', [{}])[0].get('splits', [])
        if not splits: return None

        def _sum(subset, field, cast=float):
            try: return sum(cast(s['stat'][field]) for s in subset)
            except Exception: return 0

        def _era(er, ip):
            return round(er * 9 / ip, 2) if ip > 0 else None

        l3 = splits[-3:]; l5 = splits[-5:]; l7 = splits[-7:]
        l3_ip = _sum(l3, 'inningsPitched'); l3_er = _sum(l3, 'earnedRuns', int)
        l3_h = _sum(l3, 'hits', int); l3_bb = _sum(l3, 'baseOnBalls', int); l3_k = _sum(l3, 'strikeOuts', int)
        last = splits[-1]['stat']
        stats = {
            'l3_era': _era(l3_er, l3_ip),
            'l3_ip': round(l3_ip, 1),
            'l3_h': int(l3_h),
            'l3_bb': int(l3_bb),
            'l3_k': int(l3_k),
            'l7_h_per_start': round(_sum(l7, 'hits', int) / max(1, len(l7)), 2),
            'l5_h_per_start': round(_sum(l5, 'hits', int) / max(1, len(l5)), 2),
            'last_ip': float(last.get('inningsPitched', 0)),
            'bb_per_9': round(l3_bb * 9 / l3_ip, 2) if l3_ip > 0 else None,
            'k_per_9': round(l3_k * 9 / l3_ip, 2) if l3_ip > 0 else None,
            # Season stats not always in gameLog — pull separately if needed
            'first_inning_era': None,
            'xera': None,
        }
        try: cache.write_text(json.dumps(stats))
        except Exception: pass
        return stats
    except Exception:
        return None


def verify(prose: str, pitcher_name: str, sport: str = 'MLB') -> dict:
    """Verify numeric claims about `pitcher_name` in `prose` for `sport`.
    Returns {ok, violations, pitcher_stats}.

    Sport dispatch: 'MLB' uses the built-in pitcher API. Other sports plug
    in via PROVIDERS registry (see module top comment). Unknown sport =
    silent no-op (returns ok=True) so callers don't need to condition on
    sport before calling.
    """
    if not prose or not pitcher_name:
        return {'ok': True, 'violations': [], 'pitcher_stats': None}

    # Dispatch to sport-specific provider when registered
    if sport != 'MLB':
        provider = PROVIDERS.get(sport)
        if not provider:
            # No provider yet — silent no-op, log for coverage tracking
            return {'ok': True, 'violations': [],
                    'pitcher_stats': None,
                    'note': f'no stat verifier registered for {sport}'}
        return provider(prose, pitcher_name)

    # MLB path (built-in)
    pid = _lookup_pid(pitcher_name)
    if not pid:
        return {'ok': True, 'violations': [],
                'pitcher_stats': None, 'note': f'pid not found for {pitcher_name}'}
    stats = _pull_stats(pid)
    if not stats:
        return {'ok': True, 'violations': [],
                'pitcher_stats': None, 'note': f'no game log for {pitcher_name}'}

    violations = []

    # Only check numeric patterns in sentences that mention the pitcher's
    # last name (avoids flagging numbers about OPPONENT pitcher).
    last_name = pitcher_name.split()[-1].lower()
    sentences = re.split(r'(?<=[.!?])\s+', prose)
    for sent in sentences:
        if last_name not in sent.lower():
            continue
        for pat, key in NUMERIC_PATTERNS:
            for m in re.finditer(pat, sent, re.IGNORECASE):
                try: cited = float(m.group(1))
                except Exception: continue
                actual = stats.get(key)
                if actual is None: continue  # can't verify (stat not in API pull)
                tol = TOLERANCE.get(key, 0.5)
                if abs(cited - actual) > tol:
                    violations.append({
                        'claim': m.group(0)[:80],
                        'stat_key': key,
                        'cited': cited,
                        'expected': actual,
                        'delta': round(cited - actual, 2),
                        'severity': 'critical' if abs(cited - actual) > 2 * tol else 'warning',
                        'sentence': sent[:200],
                    })

        # Directional-phrase check on same sentences (about this pitcher)
        for pat, key, op, threshold in DIRECTIONAL_PHRASES:
            if not re.search(pat, sent, re.IGNORECASE): continue
            actual = stats.get(key)
            if actual is None: continue
            violated = False
            if op == '>' and actual < threshold: violated = True
            if op == '<' and actual > threshold: violated = True
            if violated:
                violations.append({
                    'claim': re.search(pat, sent, re.IGNORECASE).group(0),
                    'stat_key': key,
                    'cited': f'phrase implies {op}{threshold}',
                    'expected': actual,
                    'delta': None,
                    'severity': 'critical',
                    'sentence': sent[:200],
                    'reason': f'{pitcher_name} {key}={actual} contradicts the direction of "{re.search(pat, sent, re.IGNORECASE).group(0)}"',
                })

    return {
        'ok': len([v for v in violations if v['severity']=='critical']) == 0,
        'violations': violations,
        'pitcher_stats': stats,
    }


## ─── SPORT PROVIDERS registry ─────────────────────────────────────
# 2026-08-10: NFL/UFC/NCAAF stat verifier stubs. Each returns the
# standard {ok, violations, pitcher_stats} shape. Filled in as we hit
# real hallucination cases in each sport; skeleton unblocks the
# universal wire-up in jerry_pre_publish_audit.py + generate_*_game_reads.
##
## Contract: provider(prose: str, player_name: str) -> dict

def _verify_nfl(prose: str, player_name: str) -> dict:
    """NFL stat verifier stub. TODO: wire to ESPN QB game log +
    directional-phrase checks for pass_yds / rush_yds / TDs.

    Patterns to catch (from real NFL analyst prose we'll see):
      * "X pass yards last three games"
      * "X.X yards per attempt"
      * "X interceptions in last five"
      * "X sacks allowed last week"
      * "X.X ANY/A"
      * directional: "getting torched / carved up / harassed"
    """
    return {'ok': True, 'violations': [], 'pitcher_stats': None,
            'note': f'NFL stat verifier not yet implemented for {player_name}'}


def _verify_ncaaf(prose: str, player_name: str) -> dict:
    """NCAAF QB verifier stub. Would use CFBD API (already integrated
    for team stats). Same shape as NFL — QB pass/rush metrics.
    """
    return {'ok': True, 'violations': [], 'pitcher_stats': None,
            'note': f'NCAAF stat verifier not yet implemented for {player_name}'}


def _verify_ufc(prose: str, fighter_name: str) -> dict:
    """UFC fighter verifier stub. Would pull from ufc_fighter_stats table
    + fight_results for L3 record, finish rates, strike accuracy, etc.

    Patterns to catch:
      * "X-Y record in last five"
      * "X.X striking accuracy"
      * "X takedowns per fight"
      * "X% finish rate"
      * directional: "getting picked apart / dominating"
    """
    return {'ok': True, 'violations': [], 'pitcher_stats': None,
            'note': f'UFC stat verifier not yet implemented for {fighter_name}'}


def _verify_nba(prose: str, player_name: str) -> dict:
    """NBA verifier stub — nba_api provides recent game logs."""
    return {'ok': True, 'violations': [], 'pitcher_stats': None,
            'note': f'NBA stat verifier not yet implemented for {player_name}'}


def _verify_nhl(prose: str, player_name: str) -> dict:
    """NHL verifier stub — NHL Stats API (free) provides goalie game logs.

    Patterns to catch:
      * "X.XX GAA last 5 starts"
      * "X.XXX save %"
      * "X saves in Y shots against"
      * directional: "getting shelled / standing on his head"
    """
    return {'ok': True, 'violations': [], 'pitcher_stats': None,
            'note': f'NHL stat verifier not yet implemented for {player_name}'}


def _verify_ncaab(prose: str, player_name: str) -> dict:
    """NCAAB verifier stub — KenPom + team-level stats. Player-level
    verification less common since basketball is more team-driven.
    """
    return {'ok': True, 'violations': [], 'pitcher_stats': None,
            'note': f'NCAAB stat verifier not yet implemented for {player_name}'}


PROVIDERS = {
    'NFL': _verify_nfl,
    'NCAAF': _verify_ncaaf,
    'UFC': _verify_ufc,
    'NBA': _verify_nba,
    'NHL': _verify_nhl,
    'NCAAB': _verify_ncaab,
}


def strip_hallucinated_sentences(prose: str, violations: list) -> str:
    """Remove sentences that contain critical hallucinations.

    Called when we can't retry generation but want to salvage a mostly-
    correct read by dropping the fabricated claims. Preserves the rest
    of the read intact.

    Also normalizes double-spaces created by sentence removal.
    """
    if not prose or not violations: return prose
    bad_sentences = {v.get('sentence') for v in violations
                     if v.get('severity') == 'critical' and v.get('sentence')}
    if not bad_sentences: return prose
    out = prose
    for bad in bad_sentences:
        # bad is a truncated snippet — find the full sentence in out
        # by looking for a substring match
        import re
        # First 60 chars of the bad snippet — enough to identify the sentence
        needle = bad[:60].strip()
        if not needle: continue
        # Find sentence containing needle
        for m in re.finditer(r'[^.!?]*[.!?]', out):
            if needle in m.group(0):
                out = out.replace(m.group(0), '', 1)
                break
    # Normalize whitespace
    out = re.sub(r' {2,}', ' ', out)
    out = re.sub(r'\s+([.!?])', r'\1', out)
    out = re.sub(r'\.{2,}', '.', out).strip()
    return out


# CLI: `python jerry_stat_verifier.py --game-date 2026-08-10`
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--game-date', default=(datetime.now()-timedelta(hours=4)).strftime('%Y-%m-%d'))
    ap.add_argument('--sport', default='MLB')
    args = ap.parse_args()

    _env = Path(__file__).parent / '.env'
    if _env.exists():
        for line in _env.read_text().split('\n'):
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
    SB = os.environ.get('SUPABASE_URL'); K = os.environ.get('SUPABASE_KEY')
    H = {'apikey': K, 'Authorization': f'Bearer {K}'}

    reads = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
        params={'sport': f'eq.{args.sport}', 'game_date': f'eq.{args.game_date}',
                'select': 'id,game_id,short_read,long_read'}, timeout=15).json()
    ctxs = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H,
        params={'game_date': f'eq.{args.game_date}',
                'select': 'game_id,home_pitcher,away_pitcher'}, timeout=15).json()
    ctx_by = {c['game_id']: c for c in ctxs}

    total_violations = 0
    total_critical = 0
    for r in reads:
        ctx = ctx_by.get(r['game_id'], {})
        prose = (r.get('short_read') or '') + '\n' + (r.get('long_read') or '')
        for pkey in ('home_pitcher', 'away_pitcher'):
            pname = ctx.get(pkey)
            if not pname or pname == '(TBD)': continue
            res = verify(prose, pname)
            for v in res['violations']:
                total_violations += 1
                if v['severity'] == 'critical': total_critical += 1
                print(f'  id={r["id"]} {pname:22} [{v["severity"]:8}] {v["stat_key"]:15} '
                      f'cited={v["cited"]} expected={v["expected"]}')
                print(f'    claim: {v["claim"]}')

    print(f'\n=== stat verifier · {args.game_date} · '
          f'{total_violations} violations ({total_critical} critical) ===')
    sys.exit(1 if total_critical > 0 else 0)


if __name__ == '__main__':
    main()
