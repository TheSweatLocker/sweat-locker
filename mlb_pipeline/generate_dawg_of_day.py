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
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')

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


def fetch_ml_odds_map():
    """Fetch today's MLB moneyline odds from Odds API, return {(home,away): {home_ml,away_ml}}."""
    ml_map = {}
    if not ODDS_API_KEY:
        return ml_map
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            },
            timeout=15
        )
        if r.status_code != 200:
            return ml_map
        for g in r.json():
            home = g.get("home_team")
            away = g.get("away_team")
            if not home or not away:
                continue
            for bm in g.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    home_ml = None
                    away_ml = None
                    for o in mkt.get("outcomes", []):
                        if o.get("name") == home:
                            home_ml = o.get("price")
                        elif o.get("name") == away:
                            away_ml = o.get("price")
                    if home_ml and away_ml:
                        ml_map[(home, away)] = {"home_ml": home_ml, "away_ml": away_ml}
                        break
                if (home, away) in ml_map:
                    break
    except Exception as e:
        print(f"  ⚠️ ML odds fetch failed: {e}")
    return ml_map


def score_dawg(g, diag=None, ml_map=None):
    """Evaluate a game for Dawg candidacy. Returns dict or None if not a Dawg.

    Source of truth: MONEYLINE ODDS (not close_spread sign).
    The ML dog is whichever team has ML >= +100. Run line spread sign in our
    storage has been unreliable, and run line ≠ ML status anyway (a team can
    be -1.5 RL but -110 ML, or +1.5 RL but ML favorite).

    Metric: dog_edge = model's projected differential for the dog + 1.5
            (MLB run line is always 1.5). Positive = model thinks the dog
            covers the run line by more than market expects.
    """
    matchup_label = f"{g.get('away_team')} @ {g.get('home_team')}"
    ps = _f(g.get('projected_spread'))  # positive = home wins by X

    if ps is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: no projected_spread")
        return None

    # Pitcher xERA gate — RELAXED 2026-04-25:
    # Originally rejected ANY game with one nulled pitcher because projected_spread
    # falls back to team R/G and creates artifact deltas. Now we allow the game IF
    # signal_confluence_net >= 2 (STRONG+) provides independent multi-signal evidence
    # that doesn't depend on projected_spread magnitude.
    # Reject only when BOTH pitchers null AND no confluence support.
    home_xera = _f(g.get('home_sp_xera'))
    away_xera = _f(g.get('away_sp_xera'))
    confluence_net_raw = g.get('signal_confluence_net')
    try:
        confluence_net = int(confluence_net_raw) if confluence_net_raw is not None else 0
    except (TypeError, ValueError):
        confluence_net = 0
    if home_xera is None and away_xera is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: both pitchers missing xERA — no projection")
        return None
    if (home_xera is None or away_xera is None) and confluence_net < 2:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: missing pitcher xERA AND no confluence support (net {confluence_net:+d})")
        return None

    # ML odds REQUIRED — no ML odds = no Dawg eligibility (we can't verify dog status)
    if not ml_map:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: no ML map loaded")
        return None
    ml_entry = ml_map.get((g.get('home_team'), g.get('away_team')))
    if not ml_entry:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: ML odds not found for this matchup")
        return None
    home_ml = ml_entry.get('home_ml')
    away_ml = ml_entry.get('away_ml')
    if home_ml is None or away_ml is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: incomplete ML odds (home={home_ml}, away={away_ml})")
        return None

    # Identify dog by ML — whichever team is plus money. If both negative
    # (rare pick'em with juice), skip — no clear dog.
    if home_ml >= 100 and away_ml < 0:
        is_home_dawg = True
        team_ml = home_ml
    elif away_ml >= 100 and home_ml < 0:
        is_home_dawg = False
        team_ml = away_ml
    else:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: no ML dog (home {home_ml:+d} / away {away_ml:+d})")
        return None

    team = g.get('home_team') if is_home_dawg else g.get('away_team')
    opp_team = g.get('away_team') if is_home_dawg else g.get('home_team')

    # Compute dog's projected differential: positive = dog wins
    dog_differential = ps if is_home_dawg else -ps

    # MLB run line is always 1.5. dog_edge = how much the dog beats the +1.5 cover line.
    # If model says dog wins by 0.5 (dog_diff=+0.5), dog_edge = 0.5 + 1.5 = 2.0
    # If model says dog loses by 0.8 (dog_diff=-0.8), dog_edge = -0.8 + 1.5 = 0.7
    dog_edge = dog_differential + 1.5

    MIN_EDGE = 1.3  # loosened so a Dawg surfaces most days; conviction score picks winner
    if dog_edge < MIN_EDGE:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: {team.split()[-1]} dog_edge={dog_edge:+.2f} (ps={ps:+.1f}, ML {team_ml:+d}) — model agrees")
        return None

    # close_spread for display only — may be wrong sign but we'll show it
    cs = _f(g.get('close_spread'))
    if cs is None:
        cs = _f(g.get('open_spread'))
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

    # Dog edge — how much better the dog is per model vs market's run line
    edge_bump = min(35, int(dog_edge * 8))
    conviction += edge_bump
    dog_fate = "winning outright" if dog_differential > 0 else f"losing by only {abs(dog_differential):.1f}"
    signals['model_view'] = f"{team.split()[-1]} ML {team_ml:+d} — model sees them {dog_fate} ({dog_edge:+.1f} runs vs +1.5 RL)"

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

    # Signal confluence bump (added 2026-04-25) — when multiple independent signals
    # back the model's pick (= the dog, since model picked the dog), that's stronger
    # evidence than dog_edge alone. Each net confluence point worth ~3 conviction.
    if confluence_net >= 4:
        conviction += 12
        signals['confluence'] = f"PRIME confluence (+{confluence_net} signals stack on {team.split()[-1]})"
    elif confluence_net >= 2:
        conviction += 8
        signals['confluence'] = f"STRONG confluence (+{confluence_net} signals stack on {team.split()[-1]})"
    elif confluence_net >= 1:
        conviction += 3
        signals['confluence'] = f"LEAN confluence (+{confluence_net} signal edge on {team.split()[-1]})"

    conviction = max(0, min(100, conviction))
    tier = 'PRIME' if conviction >= 80 else 'STRONG' if conviction >= 65 else 'LEAN'

    return {
        'team': team,
        'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
        'game_id': g.get('game_id'),
        'spread_delta': sd,
        'close_spread': cs,
        'team_ml': team_ml,
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
        return f"Market's got {dawg['team'].split()[-1]} at {dawg.get('team_ml', 0):+d} ML, but Jerry sees this one closer to a coin flip — value's on the dog."

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
        return f"Market's got {dawg['team'].split()[-1]} at {dawg.get('team_ml', 0):+d} ML, but Jerry sees this one closer to a coin flip — value's on the dog."


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

    ml_map = fetch_ml_odds_map()
    if ml_map:
        print(f"  Loaded ML odds for {len(ml_map)} games")
    else:
        print("  ⚠️ No ML odds available — Dawg filter will reject all candidates")

    dawg_candidates = []
    diag = []
    for g in games:
        d = score_dawg(g, diag=diag, ml_map=ml_map)
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
    print(f"  ML {top.get('team_ml', 0):+d} | Model delta {top['spread_delta']:+.1f}")
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
