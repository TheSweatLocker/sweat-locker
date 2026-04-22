"""
Pipeline-driven prop generator for MLB.

Scores batter Hits O/U 0.5 and pitcher Ks O/U based on pipeline matchup data
(platoon splits, xERA, L3 form, park, weather, umpire, catcher framing).

No EV scanning — conviction comes from proprietary signal alignment.

Runs after game_context.py (has everything we need already in mlb_game_context
+ mlb_team_offense + mlb_pitcher_stats + mlb_catcher_framing).

Writes top 15 props by conviction to mlb_pipeline_props table.

Table schema:
  CREATE TABLE mlb_pipeline_props (
    id BIGSERIAL PRIMARY KEY,
    game_date DATE NOT NULL,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    player_team TEXT,
    matchup TEXT,
    prop_type TEXT NOT NULL,        -- 'hits_over' or 'ks_over'
    prop_line NUMERIC,
    direction TEXT NOT NULL,        -- 'over' / 'under'
    conviction INTEGER NOT NULL,
    tier TEXT NOT NULL,             -- 'PRIME' / 'STRONG' / 'LEAN'
    signals JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX idx_pipeline_props_date ON mlb_pipeline_props(game_date DESC, conviction DESC);
"""
import os
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}

TOP_N = 15

def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')

def fetch_todays_games():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context"
        f"?game_date=eq.{gd}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []

def fetch_team_offense(team):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_team_offense?team=eq.{requests.utils.quote(team)}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=10
    )
    data = r.json()
    return data[0] if data else None

def fetch_pitcher(name):
    if not name:
        return None
    last = name.split()[-1]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.*{requests.utils.quote(last)}*&select=*&limit=3",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=10
    )
    data = r.json() if r.status_code == 200 else []
    for p in data:
        if p.get('player_name') and last.lower() in p['player_name'].lower():
            return p
    return data[0] if data else None

def fetch_catcher_framing(name):
    if not name:
        return None
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_catcher_framing?player_name=ilike.*{requests.utils.quote(name)}*&select=framing_runs&limit=1",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=5
    )
    data = r.json() if r.status_code == 200 else []
    return float(data[0]['framing_runs']) if data and data[0].get('framing_runs') is not None else None

def tier_for(conviction):
    if conviction >= 80: return 'PRIME'
    if conviction >= 65: return 'STRONG'
    if conviction >= 50: return 'LEAN'
    return 'SKIP'

def score_pitcher_ks(pitcher_name, pitcher_stats, opp_team_offense, game_ctx, framing_runs):
    """Score a pitcher K prop. Returns (conviction, signals_dict, suggested_line)."""
    if not pitcher_stats or not pitcher_stats.get('xera'):
        return None

    signals = {}
    conviction = 30

    xera = float(pitcher_stats.get('xera') or 4.5)
    k_pct = float(pitcher_stats.get('k_pct') or 20)
    throws = pitcher_stats.get('throws') or 'R'
    first_inn_era = pitcher_stats.get('first_inning_era')
    l3_k = pitcher_stats.get('last_3_k_pct')

    # Opposing team K rate vs this pitcher's hand
    opp_k_key = f'k_pct_vs_{"rhp" if throws == "R" else "lhp"}'
    opp_k_vs_hand = None
    if opp_team_offense:
        opp_k_vs_hand = opp_team_offense.get(opp_k_key) or opp_team_offense.get('k_pct')
    opp_k = float(opp_k_vs_hand) if opp_k_vs_hand is not None else 22
    opp_wrc = float(opp_team_offense.get('wrc_plus') or 100) if opp_team_offense else 100

    # K rate gap
    k_gap = k_pct - opp_k
    if k_gap >= 8:
        conviction += 20
        signals['k_gap'] = f'{pitcher_name.split()[-1]} {k_pct:.1f}% K vs lineup {opp_k:.1f}% — +{k_gap:.1f}pt edge'
    elif k_gap >= 4:
        conviction += 10
        signals['k_gap'] = f'{k_gap:.1f}pt K rate advantage'

    # xERA elite
    if xera <= 3.0:
        conviction += 15
        signals['xera'] = f'Elite xERA {xera:.2f}'
    elif xera <= 3.75:
        conviction += 8
        signals['xera'] = f'Above-avg xERA {xera:.2f}'

    # L3 form drift — hot streak
    if l3_k is not None and float(l3_k) - k_pct >= 3:
        conviction += 10
        signals['form'] = f'L3 K% {float(l3_k):.1f}% vs season {k_pct:.1f}% — heater'

    # Opposing elite offense penalty
    if opp_wrc >= 120:
        conviction -= 15
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — elite offense grinds ABs'
    elif opp_wrc <= 85:
        conviction += 8
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — weak lineup'

    # Opposing team K% (swing-and-miss lineup)
    if opp_k >= 26:
        conviction += 10
        signals['opp_k_rate'] = f'Opp K% {opp_k:.1f}% — whiff-prone'
    elif opp_k <= 18:
        conviction -= 8

    # Catcher framing
    if framing_runs is not None and framing_runs >= 2:
        conviction += 8
        signals['framing'] = f'Catcher +{framing_runs:.1f} framing runs — expands zone'
    elif framing_runs is not None and framing_runs <= -2:
        conviction -= 5

    # 1st inning ERA — slow starters eat pitches before Ks
    if first_inn_era is not None and float(first_inn_era) >= 5.0:
        conviction -= 10
        signals['slow_start'] = f'1st inn ERA {float(first_inn_era):.1f} — eats pitches early'

    # Umpire K tendency
    ump_note = (game_ctx.get('umpire_note') or '').lower()
    if 'k-friendly' in ump_note:
        conviction += 8
        signals['umpire'] = 'K-friendly umpire behind plate'

    conviction = max(0, min(100, conviction))

    # Suggested line based on K rate + typical IP
    typical_ip = 5.5
    suggested_line = round((k_pct / 100) * (typical_ip * 4.5), 1) - 0.5  # .5 below expected total = Over
    suggested_line = max(3.5, min(8.5, suggested_line))

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
    }

def score_batter_hits(batter_name, batter_team_offense, opp_pitcher_stats, opp_bullpen_era,
                     park_factor, temperature, wind_speed, wind_dir, ump_note):
    """Score a batter Hits Over 0.5 prop. Returns (conviction, signals_dict)."""
    if not batter_team_offense:
        return None

    signals = {}
    conviction = 30

    throws = opp_pitcher_stats.get('throws') if opp_pitcher_stats else 'R'
    hand_key = f'wrc_plus_vs_{"rhp" if throws == "R" else "lhp"}'
    team_wrc_vs_hand = batter_team_offense.get(hand_key) or batter_team_offense.get('wrc_plus')
    team_wrc = float(team_wrc_vs_hand) if team_wrc_vs_hand is not None else 100

    # Team platoon-adjusted offense (proxy for batter expected performance)
    if team_wrc >= 115:
        conviction += 15
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {throws}HP — elite'
    elif team_wrc >= 105:
        conviction += 8
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {throws}HP — above avg'
    elif team_wrc <= 85:
        conviction -= 10
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {throws}HP — weak'

    # Opposing pitcher xERA
    if opp_pitcher_stats and opp_pitcher_stats.get('xera'):
        opp_xera = float(opp_pitcher_stats['xera'])
        opp_k = float(opp_pitcher_stats.get('k_pct') or 20)
        if opp_xera >= 4.75:
            conviction += 15
            signals['opp_starter'] = f'Opp starter {opp_xera:.2f} xERA — soft'
        elif opp_xera >= 4.25:
            conviction += 8
            signals['opp_starter'] = f'Opp starter {opp_xera:.2f} xERA — below avg'
        elif opp_xera <= 3.0:
            conviction -= 12
            signals['opp_starter'] = f'Opp starter {opp_xera:.2f} xERA — elite arm'
        # K-heavy starter penalty
        if opp_k >= 28:
            conviction -= 8
            signals['opp_k_heavy'] = f'Opp K% {opp_k:.1f}% — strikeout artist'
        # L3 form drift
        opp_l3 = opp_pitcher_stats.get('last_3_era')
        if opp_l3 is not None and float(opp_l3) >= 5.5:
            conviction += 8
            signals['opp_form'] = f'Opp L3 ERA {float(opp_l3):.2f} — trending wrong way'

    # Opposing bullpen (matters when starter gets chased)
    if opp_bullpen_era is not None:
        bpe = float(opp_bullpen_era)
        if bpe >= 4.5:
            conviction += 8
            signals['opp_bullpen'] = f'Opp BP ERA {bpe:.2f} — soft pen'
        elif bpe <= 3.0:
            conviction -= 5

    # Park factor
    if park_factor:
        pf = int(park_factor)
        if pf >= 108:
            conviction += 10
            signals['park'] = f'Park factor {pf} — hitter friendly'
        elif pf >= 103:
            conviction += 5
            signals['park'] = f'Park factor {pf} — slight hitter tilt'
        elif pf <= 93:
            conviction -= 8
            signals['park'] = f'Park factor {pf} — pitcher park'

    # Wind blowing out
    if wind_speed and wind_dir:
        try:
            ws = int(wind_speed)
            wd = (wind_dir or '').upper()
            if ws >= 10 and wd in ('S', 'SW', 'SSW', 'SSE', 'SE'):
                conviction += 5
                signals['wind'] = f'Wind {ws}mph {wd} — blowing out'
            elif ws >= 12 and wd in ('N', 'NW', 'NNW', 'NNE', 'NE'):
                conviction -= 5
        except: pass

    # Temperature — hot air carries
    if temperature:
        try:
            t = int(temperature)
            if t >= 80:
                conviction += 3
        except: pass

    conviction = max(0, min(100, conviction))
    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': 0.5,
    }

def wipe_todays_props():
    gd = today_et()
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{gd}",
        headers=HEADERS,
        timeout=15
    )
    return r.status_code in (200, 204)

def upsert_props(props):
    if not props:
        return 0
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
        headers=HEADERS,
        json=props,
        timeout=20
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return 0
    return len(props)

def run():
    print(f"=== Pipeline prop generator {today_et()} ===")

    games = fetch_todays_games()
    if not games:
        print("No games today in mlb_game_context")
        return

    print(f"Scoring props across {len(games)} games...")
    all_props = []
    diag = {'k_scored': 0, 'k_kept': 0, 'b_scored': 0, 'b_kept': 0, 'top_scores': []}

    for g in games:
        game_id = g.get('game_id')
        game_date = g.get('game_date')
        home_team = g.get('home_team')
        away_team = g.get('away_team')
        home_pitcher = g.get('home_sp_name')
        away_pitcher = g.get('away_sp_name')
        park = g.get('park_run_factor')
        temp = g.get('temperature')
        wind_speed = g.get('wind_mph')
        wind_dir = g.get('wind_direction')
        ump_note = g.get('umpire_note') or ''
        matchup = f"{away_team} @ {home_team}"

        home_offense = fetch_team_offense(home_team)
        away_offense = fetch_team_offense(away_team)

        # Pitcher K props — both starters
        for pitcher, pitcher_team, opp_team, opp_offense, opp_catcher in [
            (home_pitcher, home_team, away_team, away_offense, g.get('home_catcher_framing')),
            (away_pitcher, away_team, home_team, home_offense, g.get('away_catcher_framing')),
        ]:
            if not pitcher:
                continue
            pstats = fetch_pitcher(pitcher)
            if not pstats:
                continue
            framing_runs = float(opp_catcher) if opp_catcher is not None else None
            result = score_pitcher_ks(pitcher, pstats, opp_offense, g, framing_runs)
            if not result:
                continue
            diag['k_scored'] += 1
            diag['top_scores'].append((result['conviction'], 'Ks', pitcher))
            if result['conviction'] < 45:
                continue
            diag['k_kept'] += 1
            all_props.append({
                'game_date': game_date,
                'game_id': game_id,
                'player_name': pitcher,
                'player_team': pitcher_team,
                'matchup': matchup,
                'prop_type': 'ks_over',
                'prop_line': result['prop_line'],
                'direction': 'over',
                'conviction': result['conviction'],
                'tier': tier_for(result['conviction']),
                'signals': result['signals'],
            })

        # Batter Hits O/U 0.5 — only when lineup confirmed
        if not g.get('lineup_confirmed'):
            continue
        home_bp = g.get('home_bullpen_era')
        away_bp = g.get('away_bullpen_era')
        home_lineup = (g.get('home_lineup') or '').split(', ')
        away_lineup = (g.get('away_lineup') or '').split(', ')
        home_pitcher_stats = fetch_pitcher(home_pitcher)
        away_pitcher_stats = fetch_pitcher(away_pitcher)

        # Home batters face away pitcher
        for batter in [b.strip() for b in home_lineup if b.strip()][:9]:
            result = score_batter_hits(
                batter, home_offense, away_pitcher_stats, away_bp,
                park, temp, wind_speed, wind_dir, ump_note,
            )
            if not result:
                continue
            diag['b_scored'] += 1
            diag['top_scores'].append((result['conviction'], 'Hits', batter))
            if result['conviction'] < 50:
                continue
            diag['b_kept'] += 1
            all_props.append({
                'game_date': game_date,
                'game_id': game_id,
                'player_name': batter,
                'player_team': home_team,
                'matchup': matchup,
                'prop_type': 'hits_over',
                'prop_line': 0.5,
                'direction': 'over',
                'conviction': result['conviction'],
                'tier': tier_for(result['conviction']),
                'signals': result['signals'],
            })

        # Away batters face home pitcher
        for batter in [b.strip() for b in away_lineup if b.strip()][:9]:
            result = score_batter_hits(
                batter, away_offense, home_pitcher_stats, home_bp,
                park, temp, wind_speed, wind_dir, ump_note,
            )
            if not result:
                continue
            diag['b_scored'] += 1
            diag['top_scores'].append((result['conviction'], 'Hits', batter))
            if result['conviction'] < 50:
                continue
            diag['b_kept'] += 1
            all_props.append({
                'game_date': game_date,
                'game_id': game_id,
                'player_name': batter,
                'player_team': away_team,
                'matchup': matchup,
                'prop_type': 'hits_over',
                'prop_line': 0.5,
                'direction': 'over',
                'conviction': result['conviction'],
                'tier': tier_for(result['conviction']),
                'signals': result['signals'],
            })

    # Sort by conviction, keep top N
    all_props.sort(key=lambda p: p['conviction'], reverse=True)
    top = all_props[:TOP_N]

    # Diagnostic summary
    diag['top_scores'].sort(reverse=True)
    print(f"\n  Diag: scored Ks={diag['k_scored']}, Hits={diag['b_scored']}  |  kept Ks={diag['k_kept']}, Hits={diag['b_kept']}")
    print(f"  Top 10 computed convictions (kept or not):")
    for sc, ptype, pname in diag['top_scores'][:10]:
        print(f"    [{sc}] {ptype}: {pname}")

    # Wipe today's rows and replace
    wipe_todays_props()
    saved = upsert_props(top)
    print(f"\n✅ Stored {saved} top props")
    for p in top[:5]:
        print(f"  [{p['conviction']}] {p['player_name']} {p['prop_type']} {p['prop_line']} — {p['tier']}")
        for k, v in p['signals'].items():
            print(f"    · {k}: {v}")

if __name__ == "__main__":
    run()
