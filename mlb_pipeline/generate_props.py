"""
Pipeline-driven prop generator for MLB.

Scores batter Hits O/U 0.5 and pitcher Ks O/U based on pipeline matchup data.
No EV scanning — conviction comes from proprietary signal alignment.

All signals read directly from mlb_game_context (populated upstream by
game_context.py, team_stats.py, pitcher_stats.py, savant_enrichment.py).

Writes top N props by conviction to mlb_pipeline_props table.
"""
import os
import re
import sys
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
K_CUTOFF = 50
HITS_CUTOFF = 55


def _f(v):
    try: return float(v)
    except: return None


def _i(v):
    try: return int(float(v))
    except: return None


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def fetch_todays_games():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{gd}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []


def parse_pitcher_k_pct_from_context(pitcher_context, pitcher_name):
    """Parse season K% from the 'pitcher_context' field format:
    'Name1 (RHP): xERA X, K% Y%, ... | Name2 (RHP): xERA Z, K% W%, ...'"""
    if not pitcher_context or not pitcher_name:
        return None
    last = pitcher_name.split()[-1]
    for segment in pitcher_context.split('|'):
        if last.lower() in segment.lower():
            m = re.search(r'K%\s+([\d.]+)', segment)
            if m:
                try: return float(m.group(1))
                except: return None
    return None


def tier_for(conviction):
    if conviction >= 80: return 'PRIME'
    if conviction >= 65: return 'STRONG'
    if conviction >= 50: return 'LEAN'
    return 'SKIP'


def score_pitcher_ks(g, side):
    """Score a starter's Ks O/U prop. side = 'home' or 'away'."""
    pitcher = g.get(f'{side}_pitcher')
    xera = _f(g.get(f'{side}_sp_xera'))
    if not pitcher or xera is None:
        return None

    # Opener / small-sample filter — don't project Ks for relievers going 1 inning
    last_ip = _f(g.get(f'{side}_last_ip'))
    last_pitches = _f(g.get(f'{side}_last_pitch_count'))
    if last_ip is not None and last_ip <= 1.5 and (last_pitches is None or last_pitches <= 35):
        return None  # opener / bullpen arm — not a starter prop

    opp_side = 'away' if side == 'home' else 'home'
    k_gap = _f(g.get(f'{side}_k_gap'))  # pre-computed: pitcher K% - opp team K% vs hand
    opp_wrc = _f(g.get(f'{opp_side}_wrc_plus')) or 100
    opp_k_pct = _f(g.get(f'{opp_side}_team_k_pct')) or 22
    l3_era = _f(g.get(f'{side}_pitcher_last_3_era'))
    l3_k = _f(g.get(f'{side}_pitcher_last_3_k_pct'))
    first_inn_era = _f(g.get(f'{side}_first_inning_era'))
    # Pitcher's catcher's framing = own side's catcher
    framing = _f(g.get(f'{side}_catcher_framing'))
    throws = g.get(f'{side}_throws') or 'R'
    parsed_k_pct = parse_pitcher_k_pct_from_context(g.get('pitcher_context'), pitcher)
    # Sanitize: MLB K% caps around ~40% (historical max ~42% for elite short-stint relievers).
    # Values above 40 are small-sample noise — fall back to a conservative default.
    pitcher_k_pct = parsed_k_pct if parsed_k_pct is not None and 5 <= parsed_k_pct <= 40 else None
    ump_note = (g.get('umpire_note') or '').lower()

    signals = {}
    conviction = 30

    # K rate gap vs opposing lineup (already computed upstream)
    if k_gap is not None:
        if k_gap >= 8:
            conviction += 22
            signals['k_gap'] = f'{pitcher.split()[-1]} K% vs lineup: +{k_gap:.1f}pt advantage'
        elif k_gap >= 4:
            conviction += 12
            signals['k_gap'] = f'+{k_gap:.1f}pt K rate advantage vs lineup'
        elif k_gap <= -5:
            conviction -= 10
            signals['k_gap_neg'] = f'{k_gap:.1f}pt K rate disadvantage'

    # xERA tier
    if xera <= 3.0:
        conviction += 15
        signals['xera'] = f'Elite xERA {xera:.2f}'
    elif xera <= 3.75:
        conviction += 8
        signals['xera'] = f'Above-avg xERA {xera:.2f}'
    elif xera >= 5.0:
        conviction -= 8

    # Absolute season K% signal (high K pitcher trending)
    if pitcher_k_pct is not None and pitcher_k_pct >= 28:
        conviction += 10
        signals['k_artist'] = f'Season K% {pitcher_k_pct:.1f}% — strikeout artist'
    elif pitcher_k_pct is not None and pitcher_k_pct <= 17:
        conviction -= 8

    # L3 form — hot streak on K rate
    if l3_k is not None and pitcher_k_pct is not None and l3_k - pitcher_k_pct >= 3:
        conviction += 8
        signals['form_hot'] = f'L3 K% {l3_k:.1f}% vs season {pitcher_k_pct:.1f}% — heater'
    elif l3_era is not None and l3_era >= 6.0:
        conviction -= 8
        signals['form_cold'] = f'L3 ERA {l3_era:.2f} — struggling'

    # Opposing offense quality
    if opp_wrc >= 120:
        conviction -= 15
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — elite offense grinds ABs'
    elif opp_wrc <= 85:
        conviction += 8
        signals['opp_offense'] = f'Opp wRC+ {opp_wrc:.0f} — weak lineup'

    # Opposing team swing-and-miss tendency
    if opp_k_pct >= 26:
        conviction += 10
        signals['opp_k_rate'] = f'Opp K% {opp_k_pct:.1f}% — whiff-prone'
    elif opp_k_pct <= 18:
        conviction -= 6

    # Catcher framing behind the plate helps the pitcher
    if framing is not None and framing >= 2:
        conviction += 8
        signals['framing'] = f'Catcher +{framing:.1f} framing runs — expands zone'
    elif framing is not None and framing <= -2:
        conviction -= 5

    # 1st inning trouble hurts K volume (eats pitches early)
    if first_inn_era is not None and first_inn_era >= 5.0:
        conviction -= 10
        signals['slow_start'] = f'1st inn ERA {first_inn_era:.1f} — eats pitches early'

    # Umpire
    if 'k-friendly' in ump_note:
        conviction += 8
        signals['umpire'] = 'K-friendly umpire'

    conviction = max(0, min(100, conviction))

    # Suggested line: conservative projection with realistic caps.
    # Books rarely post pitcher K lines above 7.5 even for elite arms —
    # matching that distribution keeps our suggested line credible.
    # Small-sample noise cap: if K% > 30, use 28 as ceiling for projection
    # (prevents rookie/tiny-sample pitchers from getting 8+ K lines).
    raw_k = pitcher_k_pct if pitcher_k_pct is not None else 22
    k_pct_for_line = min(raw_k, 28)  # cap small-sample spikes
    typical_ip = 5.0  # realistic average starter IP (not aspirational)
    est_ks = (k_pct_for_line / 100) * (typical_ip * 4.0)  # 4.0 BF/IP for quality starts
    # Tier-based line caps — mirrors book distribution
    if raw_k >= 32:
        line_cap = 7.0  # elite K guys max out around 7.0 on books
    elif raw_k >= 28:
        line_cap = 6.5
    elif raw_k >= 24:
        line_cap = 5.5
    else:
        line_cap = 5.0
    suggested_line = max(3.5, min(line_cap, round(est_ks - 0.5, 1)))

    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': suggested_line,
        'throws': throws,
    }


def score_batter_hits(g, batter, side, lineup_position=None):
    """Score a batter's Hits Over 0.5 prop. side = 'home' or 'away' (batter's side).
    lineup_position: 1-indexed spot in the confirmed lineup (1-9)."""
    opp_side = 'away' if side == 'home' else 'home'

    team_wrc_vs_hand = _f(g.get(f'{side}_wrc_vs_opp_hand'))
    team_wrc_season = _f(g.get(f'{side}_wrc_plus')) or 100
    team_wrc = team_wrc_vs_hand if team_wrc_vs_hand is not None else team_wrc_season
    team_ops_vs_hand = _f(g.get(f'{side}_ops_vs_opp_hand'))
    opp_xera = _f(g.get(f'{opp_side}_sp_xera'))
    opp_l3 = _f(g.get(f'{opp_side}_pitcher_last_3_era'))
    opp_bp = _f(g.get(f'{opp_side}_bullpen_era'))
    opp_throws = g.get(f'{opp_side}_throws') or 'R'
    opp_pitcher = g.get(f'{opp_side}_pitcher') or 'opposing SP'
    opp_pitcher_k_pct = parse_pitcher_k_pct_from_context(g.get('pitcher_context'), opp_pitcher)
    park = _i(g.get('park_run_factor'))
    temp = _i(g.get('temperature'))
    wind_speed = _i(g.get('wind_speed'))
    wind_dir = (g.get('wind_direction') or '').upper()
    ump_note = (g.get('umpire_note') or '').lower()

    signals = {}
    conviction = 30

    # Team platoon-adjusted offense
    if team_wrc >= 115:
        conviction += 15
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — elite'
    elif team_wrc >= 105:
        conviction += 8
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — above avg'
    elif team_wrc <= 85:
        conviction -= 10
        signals['team_offense'] = f'Team wRC+ {team_wrc:.0f} vs {opp_throws}HP — weak'

    # Opposing starter quality — fall back to L3 ERA when xERA is null (early season, suspicious values capped upstream)
    opp_quality = opp_xera if opp_xera is not None else opp_l3
    opp_quality_label = 'xERA' if opp_xera is not None else 'L3 ERA'
    if opp_quality is not None:
        if opp_quality >= 5.0:
            conviction += 18
            signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — very soft'
        elif opp_quality >= 4.25:
            conviction += 10
            signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — below avg'
        elif opp_quality <= 2.75:
            conviction -= 12
            signals['opp_starter'] = f'Opp starter {opp_quality:.2f} {opp_quality_label} — elite arm'

    # K-heavy starter punishes hit props
    if opp_pitcher_k_pct is not None and opp_pitcher_k_pct >= 28:
        conviction -= 8
        signals['opp_k_heavy'] = f'Opp K% {opp_pitcher_k_pct:.1f}% — strikeout artist'

    # L3 opposing pitcher form drift
    if opp_l3 is not None and opp_l3 >= 5.5:
        conviction += 8
        signals['opp_form'] = f'Opp L3 ERA {opp_l3:.2f} — trending wrong way'
    elif opp_l3 is not None and opp_l3 <= 2.5 and opp_xera is not None and opp_xera <= 3.5:
        conviction -= 6
        signals['opp_form_hot'] = f'Opp L3 ERA {opp_l3:.2f} — locked in'

    # Opposing bullpen — matters for hits 2+ and late-game
    if opp_bp is not None:
        if opp_bp >= 4.5:
            conviction += 8
            signals['opp_bullpen'] = f'Opp BP ERA {opp_bp:.2f} — soft pen'
        elif opp_bp <= 3.0:
            conviction -= 5

    # Park factor
    if park is not None:
        if park >= 108:
            conviction += 10
            signals['park'] = f'Park factor {park} — hitter friendly'
        elif park >= 103:
            conviction += 5
            signals['park'] = f'Park factor {park} — slight hitter tilt'
        elif park <= 93:
            conviction -= 8
            signals['park'] = f'Park factor {park} — pitcher park'

    # Wind blowing out
    if wind_speed and wind_dir:
        if wind_speed >= 10 and wind_dir in ('S', 'SW', 'SSW', 'SSE', 'SE'):
            conviction += 5
            signals['wind'] = f'Wind {wind_speed}mph {wind_dir} — blowing out'
        elif wind_speed >= 12 and wind_dir in ('N', 'NW', 'NNW', 'NNE', 'NE'):
            conviction -= 5

    # Hot weather
    if temp is not None and temp >= 80:
        conviction += 3

    # K-friendly ump hurts contact-reliant hitters
    if 'k-friendly' in ump_note:
        conviction -= 5

    # Lineup position bonus — top of order sees more PAs = higher hit probability
    if lineup_position is not None:
        if lineup_position <= 2:
            conviction += 6
            signals['lineup_spot'] = f'Hitting {lineup_position} — leadoff/2-hole (4-5 PAs)'
        elif lineup_position <= 5:
            conviction += 3
            signals['lineup_spot'] = f'Hitting {lineup_position} — heart of order (4+ PAs)'
        elif lineup_position >= 8:
            conviction -= 4
            signals['lineup_spot'] = f'Hitting {lineup_position} — bottom of order (3-4 PAs)'

    conviction = max(0, min(100, conviction))
    return {
        'conviction': conviction,
        'signals': signals,
        'prop_line': 0.5,
    }


def wipe_todays_props():
    gd = today_et()
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{gd}",
        headers=HEADERS,
        timeout=15
    )


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
    gd = today_et()
    print(f"=== Pipeline prop generator {gd} ===")

    # Overwrite guard — skip if today's props already generated
    force = '--force' in sys.argv
    if not force:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{gd}&select=id&limit=1",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10
        )
        if r.status_code == 200 and r.json():
            count_r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{gd}&select=id",
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}', 'Prefer': 'count=exact'},
                timeout=10
            )
            n = len(count_r.json()) if count_r.status_code == 200 else '?'
            print(f"  {n} pipeline props already exist for {gd}. Pass --force to overwrite.")
            return

    games = fetch_todays_games()
    if not games:
        print("No games today in mlb_game_context")
        return

    print(f"Scoring props across {len(games)} games...")
    all_props = []

    for g in games:
        game_id = g.get('game_id')
        game_date = g.get('game_date')
        home_team = g.get('home_team')
        away_team = g.get('away_team')
        matchup = f"{away_team} @ {home_team}"

        # Pitcher K props — both starters
        for side in ('home', 'away'):
            pitcher = g.get(f'{side}_pitcher')
            if not pitcher:
                continue
            result = score_pitcher_ks(g, side)
            if not result or result['conviction'] < K_CUTOFF:
                continue
            all_props.append({
                'game_date': game_date,
                'game_id': game_id,
                'player_name': pitcher,
                'player_team': g.get(f'{side}_team'),
                'matchup': matchup,
                'prop_type': 'ks_over',
                'prop_line': result['prop_line'],
                'direction': 'over',
                'conviction': result['conviction'],
                'tier': tier_for(result['conviction']),
                'signals': result['signals'],
            })

        # Batter Hits props — requires confirmed lineup
        if not g.get('lineup_confirmed'):
            continue
        for side, lineup_field in (('home', 'home_lineup'), ('away', 'away_lineup')):
            lineup_str = g.get(lineup_field) or ''
            batters = [b.strip() for b in lineup_str.split(',') if b.strip()][:9]
            team_name = g.get(f'{side}_team')
            for idx, batter in enumerate(batters):
                lineup_position = idx + 1  # 1-indexed
                result = score_batter_hits(g, batter, side, lineup_position)
                if not result or result['conviction'] < HITS_CUTOFF:
                    continue
                all_props.append({
                    'game_date': game_date,
                    'game_id': game_id,
                    'player_name': batter,
                    'player_team': team_name,
                    'matchup': matchup,
                    'prop_type': 'hits_over',
                    'prop_line': 0.5,
                    'direction': 'over',
                    'conviction': result['conviction'],
                    'tier': tier_for(result['conviction']),
                    'signals': result['signals'],
                })

    all_props.sort(key=lambda p: p['conviction'], reverse=True)

    # Cap per game so one juicy matchup doesn't flood the board.
    # Max 3 hits props per game + 1 K prop per pitcher is implicit (only 2 starters).
    hits_per_game = {}
    capped = []
    for p in all_props:
        if p['prop_type'] == 'hits_over':
            key = p['game_id']
            hits_per_game[key] = hits_per_game.get(key, 0) + 1
            if hits_per_game[key] > 3:
                continue
        capped.append(p)
    top = capped[:TOP_N]

    wipe_todays_props()
    saved = upsert_props(top)
    print(f"\n✅ Stored {saved} top props (of {len(all_props)} passing threshold)")
    for p in top[:8]:
        print(f"  [{p['conviction']}] {p['player_name']} {p['prop_type']} {p['prop_line']} ({p['tier']}) — {p['matchup']}")
        for k, v in p['signals'].items():
            print(f"      · {v}")

if __name__ == "__main__":
    run()
