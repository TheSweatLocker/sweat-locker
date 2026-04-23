"""
Dawg of the Day — single underdog ML pick per slate, model-identified.

A Dawg = a market underdog that our model likes more than the market does.
We identify games where sign(spread_delta) == sign(close_spread) —
meaning the model leans toward the side that market has as the underdog.

Stored in daily_dawg (one row per date). All users read the same record.

Table schema:
  CREATE TABLE daily_dawg (
    game_date DATE PRIMARY KEY,
    team TEXT NOT NULL,
    matchup TEXT NOT NULL,
    game_id TEXT NOT NULL,
    spread_delta NUMERIC NOT NULL,
    close_spread NUMERIC,
    conviction INT NOT NULL,
    tier TEXT NOT NULL,
    signals JSONB NOT NULL,
    narrative TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
"""
import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
}

MIN_DELTA = 1.5  # threshold to be considered a Dawg worth surfacing


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def _f(v):
    try: return float(v)
    except: return None


def fetch_todays_games():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{gd}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []


def score_dawg(g, diag=None):
    """Evaluate a game for Dawg candidacy. Returns dict or None if not a Dawg.

    Conventions:
      close_spread = home team's posted sportsbook spread (home -1.5 = favorite, home +1.5 = dog)
      projected_spread = model's projected home run differential (positive = home wins by X)
      spread_delta = projected_spread - close_spread (positive = home overvalued by market / away undervalued)

    A Dawg is the MARKET UNDERDOG that the model views as more competitive than the line.
    Metric: 'dog_edge' = model's differential from the dog's perspective + the points they're getting.
    Example: cs=-1.5 (home -1.5 fav, away is dog), ps=+0.9 (home wins by 0.9 in model).
             From away dog's view: model has them losing by 0.9, market has them losing by 1.5.
             dog_edge = (-ps) + |cs| = -0.9 + 1.5 = 0.6 runs edge on the away dog.
    Higher edge = bigger Dawg.
    """
    # Prefer close_spread (closing line, afternoon run) but fall back to open_spread
    # so Dawg works even on morning runs before close is set
    cs = _f(g.get('close_spread'))
    if cs is None:
        cs = _f(g.get('open_spread'))
    ps = _f(g.get('projected_spread'))
    matchup_label = f"{g.get('away_team')} @ {g.get('home_team')}"

    if cs is None or ps is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: missing spread (close={g.get('close_spread')}, open={g.get('open_spread')}) or projected_spread={ps}")
        return None

    # Require BOTH starters have xERA — otherwise projected_spread came from
    # the fallback team R/G calc and creates artifact-driven huge deltas.
    # A Dawg pick built on missing pitcher data isn't a real edge.
    home_xera = _f(g.get('home_sp_xera'))
    away_xera = _f(g.get('away_sp_xera'))
    if home_xera is None or away_xera is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: missing pitcher xERA (home={home_xera}, away={away_xera}) — projection unreliable")
        return None

    if abs(cs) < 0.5:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: pick'em line (cs={cs:+.1f}) — no dog/fav")
        return None

    # Identify market dog and compute model's view of that dog's differential
    if cs > 0:
        # Home +X → home is market dog
        is_home_dawg = True
        dog_differential = ps  # positive = home wins
    else:
        # Home -X → home favorite, AWAY is market dog
        is_home_dawg = False
        dog_differential = -ps  # flip so positive = away wins

    # dog_edge = how much better the dog is in the model vs what market priced
    # dog's line in market: -|cs| (e.g., away +1.5 as dog = dog "loses by 1.5" expected per line)
    dog_edge = dog_differential + abs(cs)

    MIN_EDGE = 2.0  # require at least 2 runs of "better than market" on the dog side
    if dog_edge < MIN_EDGE:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: dog_edge={dog_edge:+.2f} (cs={cs:+.1f}, ps={ps:+.1f}) — model agrees with market")
        return None

    team = g.get('home_team') if is_home_dawg else g.get('away_team')
    opp_team = g.get('away_team') if is_home_dawg else g.get('home_team')
    sd = _f(g.get('spread_delta')) or 0

    # Team stats — pick the Dawg's side data
    prefix = 'home' if is_home_dawg else 'away'
    opp_prefix = 'away' if is_home_dawg else 'home'

    wrc_vs_hand = _f(g.get(f'{prefix}_wrc_vs_opp_hand'))
    wrc_season = _f(g.get(f'{prefix}_wrc_plus')) or 100
    team_wrc = wrc_vs_hand if wrc_vs_hand is not None else wrc_season
    bullpen = _f(g.get(f'{prefix}_bullpen_era'))
    opp_bullpen = _f(g.get(f'{opp_prefix}_bullpen_era'))
    starter = g.get(f'{prefix}_pitcher')
    opp_starter = g.get(f'{opp_prefix}_pitcher')
    xera = _f(g.get(f'{prefix}_sp_xera'))
    opp_xera = _f(g.get(f'{opp_prefix}_sp_xera'))
    opp_l3_era = _f(g.get(f'{opp_prefix}_pitcher_last_3_era'))
    l3_era = _f(g.get(f'{prefix}_pitcher_last_3_era'))

    signals = {}
    conviction = 40  # base for being a model-identified dawg

    # Dog edge — how much better the dog is per model vs market's line
    edge_bump = min(35, int(dog_edge * 8))
    conviction += edge_bump
    dog_fate = "winning outright" if dog_differential > 0 else f"losing by only {abs(dog_differential):.1f}"
    signals['model_view'] = f"Market has {team.split()[-1]} as {abs(cs):.1f}-run dog — model sees them {dog_fate} ({dog_edge:+.1f} run edge)"

    # Team offense vs opposing hand
    if team_wrc >= 110:
        conviction += 8
        signals['offense'] = f"{team.split()[-1]} wRC+ {team_wrc:.0f} vs opp hand — elite bat"
    elif team_wrc >= 100:
        conviction += 4
        signals['offense'] = f"{team.split()[-1]} wRC+ {team_wrc:.0f} vs opp hand — above avg"

    # Starter edge — Dawg's pitcher having a better matchup than expected
    if xera is not None and opp_xera is not None and opp_xera - xera >= 1.0:
        conviction += 8
        signals['sp_edge'] = f"{starter} ({xera:.2f} xERA) vs {opp_starter} ({opp_xera:.2f}) — pitching advantage"
    elif l3_era is not None and l3_era <= 3.0 and xera is not None and xera <= 4.0:
        conviction += 6
        signals['sp_form'] = f"{starter} L3 ERA {l3_era:.2f} — locked in"

    # Opposing starter form drift (weakness for the opposition)
    if opp_l3_era is not None and opp_l3_era >= 5.5:
        conviction += 6
        signals['opp_form'] = f"{opp_starter} L3 ERA {opp_l3_era:.2f} — trending wrong way"

    # Bullpen edge
    if bullpen is not None and opp_bullpen is not None and opp_bullpen - bullpen >= 0.8:
        conviction += 5
        signals['bullpen'] = f"{team.split()[-1]} BP {bullpen:.2f} vs {opp_team.split()[-1]} BP {opp_bullpen:.2f}"

    # Home dog bonus — home field counts for dogs
    if is_home_dawg:
        conviction += 5
        signals['venue'] = f"Home dog advantage ({g.get('venue')})"

    conviction = max(0, min(100, conviction))
    tier = 'PRIME' if conviction >= 80 else 'STRONG' if conviction >= 65 else 'LEAN'

    return {
        'team': team,
        'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
        'game_id': g.get('game_id'),
        'spread_delta': sd,
        'close_spread': cs,
        'conviction': conviction,
        'tier': tier,
        'signals': signals,
        'venue': g.get('venue'),
        'opp_team': opp_team,
        'starter': starter,
        'opp_starter': opp_starter,
    }


def build_narrative(dawg):
    """One-sentence Jerry take on why the dog is barking."""
    if not ANTHROPIC_API_KEY:
        return f"Market's got {dawg['team'].split()[-1]} as a {abs(dawg['close_spread']):+.1f} dog, but Jerry sees this one closer to a coin flip — value's on the dog."

    signals_text = " | ".join(dawg['signals'].values())
    prompt = f"""You are Jerry — sharp, energetic, slightly degenerate but always analytically grounded. Today's Dawg of the Day is {dawg['team']} ML vs {dawg['opp_team']}.

What the model sees:
{signals_text}

Write ONE paragraph (3-4 sentences) in Jerry's voice — confident, data-specific, a touch of swagger, acknowledging they're the underdog but explaining why the model loves them anyway. Close with something like "That's why this dog is barking today." or similar — vary the close, don't template.

Rules:
- Start immediately with analysis (no "Let me look at..." preamble)
- Reference specific data points from what the model sees
- Sound like a sharp friend, not a marketing pitch
- Never say "bet" or "must play" or "lock it in"
- High energy but data-backed"""

    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 260,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=10
        )
        data = r.json()
        text = ''.join(
            b.get('text', '') for b in (data.get('content') or [])
            if b.get('type') == 'text'
        )
        return text.strip() or f"Market's got {dawg['team'].split()[-1]} as a dog, but the model disagrees across multiple signals. This one's barking."
    except Exception as e:
        print(f"  ⚠️ narrative failed: {e}")
        return f"Market's got {dawg['team'].split()[-1]} as a {abs(dawg['close_spread']):+.1f} dog, but Jerry sees this one closer to a coin flip — value's on the dog."


def upsert_dawg(gd, dawg, narrative):
    payload = {
        'game_date': gd,
        'team': dawg['team'],
        'matchup': dawg['matchup'],
        'game_id': dawg['game_id'],
        'spread_delta': dawg['spread_delta'],
        'close_spread': dawg['close_spread'],
        'conviction': dawg['conviction'],
        'tier': dawg['tier'],
        'signals': dawg['signals'],
        'narrative': narrative,
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/daily_dawg?on_conflict=game_date",
        headers=HEADERS,
        json=payload,
        timeout=15
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return False
    return True


def run():
    gd = today_et()
    print(f"=== Dawg of the Day {gd} ===")

    # Overwrite guard — if today's row already exists, don't regenerate unless --force
    force = '--force' in sys.argv
    if not force:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/daily_dawg?game_date=eq.{gd}&select=team,conviction",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10
        )
        if r.status_code == 200 and r.json():
            existing = r.json()[0]
            print(f"  Dawg already exists for {gd}: {existing.get('team')} (conviction {existing.get('conviction')})")
            print(f"  Skipping — pass --force to overwrite")
            return

    games = fetch_todays_games()
    print(f"  Evaluating {len(games)} games...")

    dawg_candidates = []
    diag = []
    for g in games:
        d = score_dawg(g, diag=diag)
        if d:
            dawg_candidates.append(d)

    if not dawg_candidates:
        print("  No Dawg candidates today — diag:")
        for line in diag[:20]:
            print(line)
        return

    dawg_candidates.sort(key=lambda d: d['conviction'], reverse=True)
    top = dawg_candidates[0]

    print(f"\n🐕 Dawg of the Day: {top['team']} ({top['tier']} {top['conviction']})")
    print(f"  {top['matchup']}")
    print(f"  Model delta {top['spread_delta']:+.1f} vs market close {top['close_spread']:+.1f}")
    for s in top['signals'].values():
        print(f"      · {s}")

    if len(dawg_candidates) > 1:
        print(f"\n  Runners-up:")
        for d in dawg_candidates[1:4]:
            print(f"    [{d['conviction']}] {d['team']} — {d['matchup']}")

    print(f"\n  Building Jerry narrative...")
    narrative = build_narrative(top)
    print(f"  Narrative: {narrative[:200]}...")

    if upsert_dawg(gd, top, narrative):
        print(f"\n✅ Dawg of the Day stored for {gd}")


if __name__ == "__main__":
    run()
