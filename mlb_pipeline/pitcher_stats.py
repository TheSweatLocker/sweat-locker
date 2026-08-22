import requests
import pandas as pd
import traceback
import sys
from pybaseball import pitching_stats, pitching_stats_range
import warnings
import os
import time
from dotenv import load_dotenv
from datetime import datetime, timedelta
warnings.filterwarnings('ignore')

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def fetch_pitcher_stats():
    print("Fetching 2026 pitcher stats...")
    # 2026-08-22: skip FanGraphs by default. leaders-legacy endpoint has
    # been returning 403 consistently in GHA (Cloudflare blocks the
    # runner IPs). Every pipeline run was wasting ~5-10s on the failed
    # attempt + print. Set MLB_PIPELINE_TRY_FANGRAPHS=1 to re-enable
    # once FanGraphs cooperates or we route through a proxy.
    if os.environ.get('MLB_PIPELINE_TRY_FANGRAPHS', '0') == '1':
        try:
            stats = pitching_stats(2026, qual=1)
            print(f"Fetched {len(stats)} pitchers from FanGraphs")
            return stats, 'fangraphs'
        except Exception as e:
            print(f"FanGraphs failed: {e}")

    # Primary: MLB Stats API — free, never blocks, has K%, BB%, ERA, WHIP
    # (FanGraphs would add xERA + advanced but Baseball Savant fills
    # xERA separately in savant_enrichment.py so we're not losing signal.)
    print("Fetching from MLB Stats API...")
    try:
        all_pitchers = []
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=15)
        teams = teams_resp.json().get('teams', [])
        for team in teams:
            try:
                roster_resp = requests.get(
                    f'https://statsapi.mlb.com/api/v1/teams/{team["id"]}/roster',
                    params={'rosterType': 'active', 'season': 2026},
                    timeout=10
                )
                for player in roster_resp.json().get('roster', []):
                    pos = player.get('position', {}).get('abbreviation')
                    # Include Two-Way Players (Ohtani) alongside pitchers
                    if pos not in ('P', 'TWP'):
                        continue
                    pid = player['person']['id']
                    name = player['person']['fullName']
                    try:
                        stats_resp = requests.get(
                            f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                            params={'stats': 'season', 'group': 'pitching', 'season': 2026},
                            timeout=10
                        )
                        splits = stats_resp.json().get('stats', [])
                        if not splits or not splits[0].get('splits'):
                            continue
                        s = splits[0]['splits'][0]['stat']
                        ip = float(s.get('inningsPitched', '0').replace('.1', '.33').replace('.2', '.67') or '0')
                        if ip < 3:
                            continue  # skip pitchers with very few innings
                        pa = int(s.get('battersFaced', 0) or 0)
                        so = int(s.get('strikeOuts', 0) or 0)
                        bb = int(s.get('baseOnBalls', 0) or 0)
                        gb = int(s.get('groundOuts', 0) or 0)
                        fb_outs = int(s.get('airOuts', 0) or 0)
                        total_outs = gb + fb_outs if (gb + fb_outs) > 0 else 1
                        all_pitchers.append({
                            'Name': name,
                            'Team': team.get('abbreviation', ''),
                            'ERA': float(s.get('era', '4.50') or '4.50'),
                            'xERA': None,  # MLB API doesn't have xERA — will be supplemented
                            'K%': round(so / pa, 3) if pa > 0 else 0.20,
                            'BB%': round(bb / pa, 3) if pa > 0 else 0.08,
                            'GB%': round(gb / total_outs, 3) if total_outs > 0 else 0.45,
                            'FB%': round(fb_outs / total_outs, 3) if total_outs > 0 else 0.35,
                            'WHIP': float(s.get('whip', '1.30') or '1.30'),
                            'Hard%': None,
                            'Barrel%': None,
                            'Whiff%': None,
                            'SwStr%': None,
                            'LOB%': None,
                            'FBv': None,
                            'vFB': None,
                            'AVG': float(s.get('avg', '.250') or '.250'),
                            'BA': float(s.get('avg', '.250') or '.250'),
                            'IP': ip,
                        })
                    except:
                        continue
            except:
                continue
            time.sleep(0.2)
        if all_pitchers:
            print(f"Fetched {len(all_pitchers)} pitchers from MLB Stats API")
            return pd.DataFrame(all_pitchers), 'mlb_api'
    except Exception as e:
        print(f"MLB Stats API failed: {e}")

    # Last resort — 2025 FanGraphs data
    try:
        stats = pitching_stats(2025, qual=20)
        print(f"Fetched {len(stats)} pitchers from 2025 fallback")
        return stats, 'fangraphs'
    except Exception as e2:
        print(f"All sources failed: {e2}")
        return None, None

def fetch_savant_xera(year=2026):
    """Fetch xERA and expected stats directly from Baseball Savant CSV endpoint"""
    try:
        print(f"Fetching xERA from Baseball Savant ({year})...")
        url = f"https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={year}&position=&team=&min=1&csv=true"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)
        if r.status_code != 200:
            print(f"  Savant returned {r.status_code}")
            return {}

        import io
        df = pd.read_csv(io.StringIO(r.text))
        print(f"  Fetched {len(df)} pitchers from Baseball Savant")
        print(f"  Savant columns: {list(df.columns[:20])}")
        if len(df) > 0:
            print(f"  Sample row: {dict(df.iloc[0])}")

        # Build lookup — try multiple column name patterns
        xera_map = {}
        for _, row in df.iterrows():
            # Name columns vary: 'last_name', 'player_name', 'last_name, first_name', etc.
            first = str(row.get('first_name', '') or row.get('name_first', '') or '')
            last = str(row.get('last_name', '') or row.get('name_last', '') or '')
            player_name = str(row.get('player_name', '') or '')

            if first and last:
                full_name = f"{first} {last}".strip()
            elif player_name:
                full_name = player_name.strip()
            elif 'last_name, first_name' in row.index:
                combo = str(row.get('last_name, first_name', ''))
                parts = combo.split(', ')
                full_name = f"{parts[1]} {parts[0]}".strip() if len(parts) == 2 else combo
            else:
                continue

            last_name = last.strip().lower() if last else full_name.split(' ')[-1].lower()

            # xERA column varies: 'est_era', 'xera', 'xERA', 'expected_era'
            xera = None
            for col in ['est_era', 'xera', 'xERA', 'expected_era']:
                val = row.get(col)
                if val is not None and str(val) != 'nan' and str(val) != '':
                    try:
                        xera = float(val)
                        break
                    except:
                        pass

            xba = None
            for col in ['est_ba', 'xba', 'xBA', 'expected_ba']:
                val = row.get(col)
                if val is not None and str(val) != 'nan' and str(val) != '':
                    try:
                        xba = float(val)
                        break
                    except:
                        pass

            xwoba = None
            for col in ['est_woba', 'xwoba', 'xwOBA', 'expected_woba']:
                val = row.get(col)
                if val is not None and str(val) != 'nan' and str(val) != '':
                    try:
                        xwoba = float(val)
                        break
                    except:
                        pass

            if full_name and xera is not None:
                try:
                    xera_map[full_name.lower()] = {
                        'xERA': round(float(xera), 2),
                        'xBA': round(float(xba), 3) if xba is not None else None,
                        'xwOBA': round(float(xwoba), 3) if xwoba is not None else None,
                    }
                    # Also key by last name for fuzzy matching
                    if last_name:
                        xera_map[last_name] = xera_map[full_name.lower()]
                except:
                    pass

        print(f"  Built xERA lookup for {len([k for k in xera_map if ' ' in k])} pitchers")
        return xera_map
    except Exception as e:
        print(f"  Baseball Savant xERA fetch failed: {e}")
        return {}

def fetch_savant_arsenal_stats(year=2026):
    """Fetch per-pitcher Statcast whiff% + hard-hit% from Baseball Savant
    pitch-arsenal-stats CSV. Aggregates per-pitch-type rows into
    per-pitcher totals weighted by pitch count.

    Added 2026-07-25 to fill the 627 nulled pitcher_stats Statcast
    fields (see project_system_integrity_sweep_725). Previous defaults
    were hardcoded 10.0/35.0/6.0/93.0/72.0 which corrupted downstream
    K-prop scoring (Miller 99-conv 0K disaster). This function pulls
    real values so the whiff gate in generate_props.py can fire.

    Returns dict {full_name.lower(): {whiff_rate, hard_hit_pct, k_pct}}
    where values are actual percentages (whiff 12.5 = 12.5%).
    """
    try:
        print(f"Fetching Savant pitch-arsenal-stats ({year})...")
        import io as _io
        url = f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&year={year}&csv=true"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)
        if r.status_code != 200:
            print(f"  Savant arsenal returned {r.status_code}")
            return {}

        df = pd.read_csv(_io.StringIO(r.text))
        print(f"  Fetched {len(df)} per-pitch-type rows")

        # Aggregate per pitcher (weighted by pitches thrown)
        arsenal_map = {}
        # Group by (last_name, first_name) — the leading BOM sometimes
        # corrupts the column name, so try both.
        name_col = None
        for c in df.columns:
            if 'last_name' in c.lower():
                name_col = c
                break
        if not name_col:
            print("  Could not find name column")
            return {}

        for name, grp in df.groupby(name_col):
            # Parse "Last, First" → "First Last"
            try:
                parts = str(name).split(', ')
                full = f"{parts[1]} {parts[0]}".strip() if len(parts) == 2 else str(name).strip()
            except Exception:
                continue

            # Weighted average by pitch count
            pitches = grp['pitches'].astype(float)
            total_p = pitches.sum()
            if total_p < 100:  # too few pitches for meaningful whiff rate
                continue

            def _weighted(col):
                if col not in grp.columns: return None
                try:
                    vals = grp[col].astype(float)
                    result = round((vals * pitches).sum() / total_p, 2)
                    return float(result)  # cast np.float64 → plain float for Supabase
                except Exception:
                    return None

            arsenal_map[full.lower()] = {
                'whiff_rate': _weighted('whiff_percent'),
                'hard_hit_pct': _weighted('hard_hit_percent'),
                'k_pct_arsenal': _weighted('k_percent'),
            }
            # Also key by last name for fuzzy matching
            if len(parts) == 2:
                arsenal_map[parts[0].lower()] = arsenal_map[full.lower()]

        n_with_whiff = sum(1 for v in arsenal_map.values() if v.get('whiff_rate') is not None)
        print(f"  Built arsenal lookup for {n_with_whiff} pitchers w/ whiff data")
        return arsenal_map
    except Exception as e:
        print(f"  Baseball Savant arsenal fetch failed: {e}")
        return {}


def fetch_recent_pitcher_stats():
    print("Fetching recent pitcher stats (last 30 days)...")
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        recent = pitching_stats_range(start_date, end_date)
        if recent is None or len(recent) == 0:
            print("No recent stats available yet — early season")
            return None
        print(f"Fetched recent stats for {len(recent)} pitchers")
        return recent
    except Exception as e:
        print(f"Recent stats not available yet: {e}")
        return None

def fetch_last5_era(pitcher_name, recent_stats):
    if recent_stats is None or pitcher_name is None:
        return None
    try:
        match = recent_stats[recent_stats['Name'].str.lower() == pitcher_name.lower()]
        if match.empty:
            match = recent_stats[recent_stats['Name'].str.lower().str.contains(pitcher_name.lower().split(' ')[-1])]
        if match.empty:
            return None
        return float(match.iloc[0].get('ERA', None))
    except:
        return None

def safe_float(val, default):
    try:
        f = float(val)
        return f if not pd.isna(f) else default
    except:
        return default

def get_pitcher_handedness(player_name):
    """Fetch pitcher throwing hand from MLB Stats API"""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={
                "names": player_name,
                "sportId": 1
            }
        )
        data = r.json()
        people = data.get("people", [])
        if not people:
            return None
        person = people[0]
        hand = person.get("pitchHand", {}).get("code", None)
        return hand  # "R" or "L"
    except Exception as e:
        return None

def get_last_3_starts(player_name, season=2026):
    """Fetch pitcher's last 3 starts ERA + K% from MLB Stats API gameLog.

    If the current season has <3 starts (early-season call-ups, IL returns,
    rookies), walks back through 2025 + 2024 to fill out the 3-start sample.
    Without this fallback, a pitcher with 1 start in 2026 would have their
    L3 ERA calculated off that single line — same thin-sample class of bug
    as the 5/27 Matz vs-team incident.
    """
    try:
        search_resp = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=10
        )
        people = search_resp.json().get("people", [])
        if not people:
            return None
        player_id = people[0]["id"]

        starts = []
        for yr in (season, season - 1, season - 2):
            try:
                stats_resp = requests.get(
                    f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
                    params={"stats": "gameLog", "group": "pitching", "season": yr},
                    timeout=10
                )
                splits = stats_resp.json().get("stats", [])
                if splits and splits[0].get("splits"):
                    games = splits[0]["splits"]
                    starts.extend([g for g in games if (g.get("stat", {}).get("gamesStarted") or 0) == 1])
            except Exception:
                continue
            # Stop walking back once we have 3 starts
            if len(starts) >= 3:
                break

        if len(starts) == 0:
            return None

        starts.sort(key=lambda g: g.get("date", ""), reverse=True)
        last_3 = starts[:3]

        # Sample-size gate: require 3 actual starts before reporting L3
        if len(last_3) < 3:
            return None

        # 2026-08-14 FRESHNESS GATE (stat-date audit finding):
        # Bassitt 8/14 case — L3 stats correct (5.14 ERA verified live)
        # but most recent start was 2026-06-03, 72 days ago. Pipeline
        # projected 5.60 hits allowed for today with no warning. Log
        # staleness to data_quality_events so audit dashboard surfaces
        # any pitcher whose L3 window is >30 days old (typically IL,
        # trade, or role change). Downstream scorer still gets the data
        # (no behavior change) — this is visibility, not gating, so we
        # don't accidentally suppress a legit returning starter.
        latest_start_date = last_3[0].get("date")
        if latest_start_date:
            try:
                from data_quality import DQ
                _dq = DQ(source='pitcher_stats.get_pitcher_l3', sport='MLB')
                _dq.assert_freshness_days(
                    latest_start_date, max_days=30,
                    check_name='pitcher_l3_last_start_age',
                    context={'pitcher': player_name,
                             'latest_start': latest_start_date,
                             'starts_in_l3': len(last_3)},
                    severity='warn')
            except Exception:
                pass  # DQ never blocks pipeline

        total_er = sum(int(g["stat"].get("earnedRuns", 0) or 0) for g in last_3)
        total_ip_str = [str(g["stat"].get("inningsPitched", "0") or "0") for g in last_3]
        total_ip = 0.0
        for ip_str in total_ip_str:
            # Convert "5.2" (5 IP + 2 outs) → 5.667
            if "." in ip_str:
                whole, frac = ip_str.split(".")
                total_ip += int(whole) + (int(frac) / 3)
            else:
                total_ip += float(ip_str)
        total_so = sum(int(g["stat"].get("strikeOuts", 0) or 0) for g in last_3)
        total_bf = sum(int(g["stat"].get("battersFaced", 0) or 0) for g in last_3)

        era = round((total_er * 9) / total_ip, 2) if total_ip > 0 else None
        k_pct = round((total_so / total_bf) * 100, 1) if total_bf > 0 else None

        # Days-since-last-start for downstream freshness checks (2026-08-14)
        days_since = None
        if latest_start_date:
            try:
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                latest = _dt.strptime(latest_start_date[:10], '%Y-%m-%d').date()
                now_et = (_dt.now(_tz.utc) - _td(hours=4)).date()
                days_since = (now_et - latest).days
            except Exception:
                pass

        return {
            "last_3_era": era,
            "last_3_k_pct": k_pct,
            "last_3_ip": round(total_ip, 1),
            "last_start_date": latest_start_date,
            "days_since_last_start": days_since,
        }
    except Exception:
        return None

def get_first_inning_splits(player_name):
    """Fetch pitcher's 1st inning ERA, WHIP, and batting avg allowed from MLB Stats API"""
    try:
        # Look up player ID
        search_resp = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=10
        )
        people = search_resp.json().get("people", [])
        if not people:
            return None
        player_id = people[0]["id"]

        def try_season(year):
            resp = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
                params={
                    "stats": "statSplits",
                    "group": "pitching",
                    "season": year,
                    "sitCodes": "i01"  # 1st inning (MLB API now expects zero-padded)
                },
                timeout=10
            )
            return resp.json().get("stats", [])

        # Try 2026, fallback to 2025
        splits = try_season(2026)
        if not splits or not splits[0].get("splits"):
            splits = try_season(2025)
            if not splits or not splits[0].get("splits"):
                # One-shot diagnostic for debugging — only fires on first pitcher
                if not hasattr(get_first_inning_splits, '_logged_once'):
                    get_first_inning_splits._logged_once = True
                    print(f"  DIAG (first inning): No splits for {player_name}. Raw response sample below:")
                    try:
                        test = requests.get(
                            f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
                            params={"stats": "statSplits", "group": "pitching", "season": 2025},
                            timeout=10
                        ).json()
                        # Show first few split codes available
                        avail = [s.get('split', {}).get('code') for g in test.get('stats', []) for s in g.get('splits', [])][:10]
                        print(f"    available sitCodes for {player_name}: {avail}")
                    except Exception as e:
                        print(f"    diag call failed: {e}")
                return None

        split_data = splits[0]["splits"][0].get("stat", {})
        ip_raw = split_data.get("inningsPitched", "0") or "0"
        # IP may be "5.2" format (5 IP + 2 outs) — convert to decimal
        try:
            if "." in str(ip_raw):
                whole, frac = str(ip_raw).split(".")
                innings_pitched = int(whole) + (int(frac) / 3)
            else:
                innings_pitched = float(ip_raw)
        except (ValueError, TypeError):
            innings_pitched = 0.0

        # Need at least 2 first innings for early season (was 5 — too strict for April)
        if innings_pitched < 2:
            return None

        era = float(split_data.get("era", "0") or "0")
        whip = float(split_data.get("whip", "0") or "0")
        avg = float(split_data.get("avg", "0") or "0")
        hits = int(split_data.get("hits", 0) or 0)
        strikeouts = int(split_data.get("strikeOuts", 0) or 0)
        walks = int(split_data.get("baseOnBalls", 0) or 0)
        home_runs = int(split_data.get("homeRuns", 0) or 0)

        return {
            "first_inning_era": round(era, 2),
            "first_inning_whip": round(whip, 2),
            "first_inning_avg": round(avg, 3),
            "first_inning_k": strikeouts,
            "first_inning_bb": walks,
            "first_inning_hr": home_runs,
            "first_inning_ip": round(innings_pitched, 1),
        }
    except Exception as e:
        return None

def get_inning_bucket_splits(player_name):
    """Fetch pitcher inning splits and aggregate to 1-3, 4-6, 7-9 buckets.
    Returns ERA/WHIP/K% per bucket plus IP and batters faced for sample-size context.

    2026-08-22: bumped timeouts 10s -> 30s on both statsapi calls. Root
    cause of 6 pitchers needing inline auto-refresh in game_context: this
    call was timing out silently, pitcher_stats uploaded without buckets,
    game_context then refreshed inline (adding 3-6 min to pipeline per
    slate). Stats API statSplits endpoint is genuinely slow for pitchers
    with many season splits — 10s was too aggressive."""
    try:
        search_resp = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=30
        )
        people = search_resp.json().get("people", [])
        if not people:
            return None
        player_id = people[0]["id"]

        def try_season(year):
            resp = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
                params={
                    "stats": "statSplits",
                    "group": "pitching",
                    "season": year,
                    "sitCodes": "i01,i02,i03,i04,i05,i06,i07,i08,i09,ig07"
                },
                timeout=30
            )
            return resp.json().get("stats", [])

        splits_payload = try_season(2026)
        if not splits_payload or not splits_payload[0].get("splits"):
            splits_payload = try_season(2025)
            if not splits_payload or not splits_payload[0].get("splits"):
                return None

        # Index split rows by sitCode
        rows = {}
        for s in splits_payload[0]["splits"]:
            code = s.get("split", {}).get("code")
            if code:
                rows[code] = s.get("stat", {})

        def ip_to_decimal(ip_raw):
            try:
                if "." in str(ip_raw):
                    whole, frac = str(ip_raw).split(".")
                    return int(whole) + (int(frac) / 3)
                return float(ip_raw)
            except (ValueError, TypeError):
                return 0.0

        def aggregate(codes):
            ip = 0.0
            er = 0
            k = 0
            bb = 0
            h = 0
            hr = 0
            bf = 0
            for code in codes:
                if code not in rows:
                    continue
                stat = rows[code]
                ip += ip_to_decimal(stat.get("inningsPitched", "0") or "0")
                er += int(stat.get("earnedRuns", 0) or 0)
                k += int(stat.get("strikeOuts", 0) or 0)
                bb += int(stat.get("baseOnBalls", 0) or 0)
                h += int(stat.get("hits", 0) or 0)
                hr += int(stat.get("homeRuns", 0) or 0)
                bf += int(stat.get("battersFaced", 0) or 0)
            if ip < 1:
                return None
            era = round((er * 9) / ip, 2)
            whip = round((bb + h) / ip, 2)
            k_pct = round((k / bf) * 100, 1) if bf else None
            bb_pct = round((bb / bf) * 100, 1) if bf else None
            hr_per_9 = round((hr * 9) / ip, 2)
            return {"era": era, "whip": whip, "k_pct": k_pct, "bb_pct": bb_pct,
                    "hr_per_9": hr_per_9, "ip": round(ip, 1), "bf": bf}

        bucket_1_3 = aggregate(["i01", "i02", "i03"])
        bucket_4_6 = aggregate(["i04", "i05", "i06"])
        # 7-9 bucket: prefer ig07 (innings 7+) for cleanest sample, fallback to i07+i08+i09
        if "ig07" in rows:
            bucket_7_9 = aggregate(["ig07"])
        else:
            bucket_7_9 = aggregate(["i07", "i08", "i09"])

        result = {}
        for label, b in (("innings_1_3", bucket_1_3), ("innings_4_6", bucket_4_6), ("innings_7_9", bucket_7_9)):
            if b:
                result[f"{label}_era"] = b["era"]
                result[f"{label}_whip"] = b["whip"]
                result[f"{label}_k_pct"] = b["k_pct"]
                result[f"{label}_bb_pct"] = b["bb_pct"]
                result[f"{label}_hr_per_9"] = b["hr_per_9"]
                result[f"{label}_ip"] = b["ip"]
                result[f"{label}_bf"] = b["bf"]
            else:
                result[f"{label}_era"] = None
                result[f"{label}_whip"] = None
                result[f"{label}_k_pct"] = None
                result[f"{label}_bb_pct"] = None
                result[f"{label}_hr_per_9"] = None
                result[f"{label}_ip"] = None
                result[f"{label}_bf"] = None
        return result
    except Exception as e:
        print(f"  Inning bucket fetch error for {player_name}: {e}")
        return None


def upload_pitcher(pitcher_data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    # Use upsert with on_conflict
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?on_conflict=player_name,season",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        },
        json=pitcher_data
    )
    if response.status_code not in [200, 201, 204]:
        # Log first few failures for diagnosis
        if not hasattr(upload_pitcher, '_err_count'):
            upload_pitcher._err_count = 0
        upload_pitcher._err_count += 1
        if upload_pitcher._err_count <= 5:
            print(f"  Upload failed {response.status_code}: {response.text[:300]}")
        return False
    return True
def get_todays_starters():
    """Fetch today's probable starters from MLB Stats API"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={'sportId': 1, 'date': today, 'hydrate': 'probablePitcher'},
            timeout=15
        )
        starters = set()
        for d in r.json().get('dates', []):
            for game in d.get('games', []):
                home_p = game.get('teams', {}).get('home', {}).get('probablePitcher', {}).get('fullName')
                away_p = game.get('teams', {}).get('away', {}).get('probablePitcher', {}).get('fullName')
                if home_p: starters.add(home_p)
                if away_p: starters.add(away_p)
        print(f"Today's probable starters: {len(starters)}")
        return starters
    except Exception as e:
        print(f"Error fetching starters: {e}")
        return set()

def build_pitcher_record(row, name, recent_stats, is_fangraphs=True, is_starter=False, is_full_refresh=False):
    """Build pitcher record from either FanGraphs or Statcast data"""
    last5_era = fetch_last5_era(name, recent_stats) if recent_stats is not None else None
    # Handedness + first inning + last 3 starts: always on full refresh (Monday), starters-only on daily
    fetch_api = is_starter or is_full_refresh
    throws = get_pitcher_handedness(name) if fetch_api else None
    first_inn = get_first_inning_splits(name) if fetch_api else None
    last_3 = get_last_3_starts(name) if fetch_api else None
    inning_buckets = get_inning_bucket_splits(name) if fetch_api else None

    if is_fangraphs:
        # 2026-07-25 DQ fix: Statcast fields (whiff/hard/barrel/velo/lob)
        # previously defaulted to hardcoded values when FanGraphs returned
        # None — resulted in 627/798 pitchers stuck at whiff=10.0,
        # hard_hit=35.0, barrel=6.0, velo=93.0, lob=72.0. This silently
        # corrupted the Miller K prop (99-conv → 0 K's) because whiff-
        # rate gates couldn't distinguish real 10% from default 10%.
        # NOW: default to None. Downstream code (score_ks_over etc.)
        # skips the gate when value is None — accurate signal handling.
        # For the remaining basics (K%, BB%, GB%, FB%, ERA, xERA) we keep
        # league-avg defaults since those NEVER come back None from FG.
        pitcher = {
            "player_name": name,
            "team": str(row.get('Team', '')),
            "throws": throws or 'R',
            "xera": safe_float(row.get('xERA', row.get('ERA')), 4.50),
            "gb_pct": safe_float(row.get('GB%'), 45.0),
            "fb_pct": safe_float(row.get('FB%'), 35.0),
            "lob_pct": safe_float(row.get('LOB%'), None),
            "k_pct": safe_float(row.get('K%'), 20.0),
            "bb_pct": safe_float(row.get('BB%'), 8.0),
            "whiff_rate": safe_float(row.get('Whiff%', row.get('SwStr%')), None),
            "hard_hit_pct": safe_float(row.get('Hard%'), None),
            "barrel_pct": safe_float(row.get('Barrel%', row.get('Barrels')), None),
            "avg_fastball_velo": safe_float(row.get('FBv', row.get('vFB')), None),
            "last_5_era": last5_era if last5_era else safe_float(row.get('ERA'), 4.50),
            "baa_allowed": safe_float(row.get('AVG', row.get('BA')), None),
            "xba_allowed": safe_float(row.get('xBA', row.get('xAVG')), None),
            "hard_hit_pct_allowed": safe_float(row.get('Hard%'), None),
        }
    else:
        # Statcast exit velo/barrels format — different column names
        pitcher = {
            "player_name": name,
            "team": '',
            "throws": throws or 'R',
            "xera": None,  # not in Statcast exit velo data
            "gb_pct": None,
            "fb_pct": None,
            "lob_pct": None,
            "k_pct": None,
            "bb_pct": None,
            "whiff_rate": None,
            "hard_hit_pct": safe_float(row.get('hard_hit_percent', row.get('ev95percent')), None),
            "barrel_pct": safe_float(row.get('brl_percent'), None),
            "avg_fastball_velo": None,
            "last_5_era": last5_era,
            "baa_allowed": safe_float(row.get('ba'), None),
            "xba_allowed": safe_float(row.get('xba'), None),
            "hard_hit_pct_allowed": safe_float(row.get('hard_hit_percent'), None),
        }

    # First inning splits — same for both sources
    pitcher["first_inning_era"] = first_inn["first_inning_era"] if first_inn else None
    pitcher["first_inning_whip"] = first_inn["first_inning_whip"] if first_inn else None
    pitcher["first_inning_avg"] = first_inn["first_inning_avg"] if first_inn else None
    pitcher["first_inning_k"] = first_inn["first_inning_k"] if first_inn else None
    pitcher["first_inning_bb"] = first_inn["first_inning_bb"] if first_inn else None
    pitcher["first_inning_hr"] = first_inn["first_inning_hr"] if first_inn else None
    pitcher["first_inning_ip"] = first_inn["first_inning_ip"] if first_inn else None
    pitcher["last_3_era"] = last_3["last_3_era"] if last_3 else None
    pitcher["last_3_k_pct"] = last_3["last_3_k_pct"] if last_3 else None
    pitcher["last_3_ip"] = last_3["last_3_ip"] if last_3 else None
    # Inning bucket splits (1-3, 4-6, 7-9) — for Hard Rock-style inning bucket bets
    if inning_buckets:
        for k, v in inning_buckets.items():
            pitcher[k] = v
    pitcher["season"] = "2026"
    pitcher["updated_at"] = "now()"
    return pitcher

def run():
    # Determine if full refresh or daily starters only — use ET not UTC
    from datetime import timezone
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    is_monday = et_now.weekday() == 0
    force_full = '--full-refresh' in sys.argv
    if force_full:
        print("⚙️  --full-refresh flag set — treating as full Monday refresh")
        is_monday = True
    print(f"ET day: {et_now.strftime('%A %Y-%m-%d %H:%M')}, is_monday: {is_monday}")
    todays_starters = get_todays_starters()

    stats, source = fetch_pitcher_stats()
    if stats is None:
        print("Could not fetch pitcher stats")
        return

    # Fetch xERA from Baseball Savant — supplement MLB API data which lacks xERA
    xera_map = {}
    xera_map_prior = {}
    if source == 'mlb_api':
        xera_map = fetch_savant_xera(year=2026)
        # Pull 2025 as fallback for pitchers with limited 2026 samples (rehab returns, rookies, etc)
        xera_map_prior = fetch_savant_xera(year=2025)

    # ─── Savant arsenal stats (added 2026-07-25) ─────────────────────
    # Fetch whiff% + hard_hit% from Savant's pitch-arsenal-stats CSV.
    # Runs ALWAYS (not just MLB API fallback) because FanGraphs data
    # ALSO has missing Statcast fields for ~78% of pitchers — the
    # pitcher_stats.py:589 default-of-10.0 bug that corrupted Miller's
    # 99-conv K-over prop. See project_system_integrity_sweep_725.
    arsenal_map = fetch_savant_arsenal_stats(year=2026)
    arsenal_map_prior = {}
    if not arsenal_map or len(arsenal_map) < 100:
        arsenal_map_prior = fetch_savant_arsenal_stats(year=2025)

    # Detect data format based on source
    is_fangraphs = source == 'fangraphs' or (hasattr(stats, 'columns') and 'Name' in stats.columns)
    name_col = 'Name' if is_fangraphs else 'last_name'

    if source == 'mlb_api':
        # MLB Stats API returns a DataFrame with 'Name' column
        is_fangraphs = True  # same column format as FanGraphs
        name_col = 'Name'
        print(f"Using MLB Stats API format — {len(stats)} pitchers")

    if not is_fangraphs:
        # Statcast format — column names vary by endpoint
        print(f"Statcast columns: {list(stats.columns[:15])}")
        if 'first_name' in stats.columns and 'last_name' in stats.columns:
            stats['full_name'] = stats['first_name'].astype(str) + ' ' + stats['last_name'].astype(str)
            name_col = 'full_name'
        elif 'last_name, first_name' in stats.columns:
            # Combined "Last, First" column — split and reverse
            stats['full_name'] = stats['last_name, first_name'].apply(
                lambda x: ' '.join(reversed(str(x).split(', '))) if ', ' in str(x) else str(x)
            )
            name_col = 'full_name'
        elif 'player_name' in stats.columns:
            name_col = 'player_name'
        else:
            # Last resort — find any column with names
            for col in stats.columns:
                if 'name' in col.lower():
                    name_col = col
                    break
        print(f"Using Statcast format — name column: '{name_col}', sample: {stats[name_col].iloc[0] if name_col in stats.columns else 'NOT FOUND'}")

    recent_stats = fetch_recent_pitcher_stats()
    success = 0
    errors = 0
    skipped = 0

    for _, row in stats.iterrows():
        try:
            name = str(row.get(name_col, ''))
            if not name or name == 'nan':
                continue

            # Check if this pitcher is starting today
            pitcher_last = name.split(' ')[-1].lower()
            is_starter = any(pitcher_last in s.lower() for s in todays_starters) if todays_starters else False

            # Daily runs: only update today's starters (unless Monday = full refresh)
            if not is_monday and todays_starters and not is_starter:
                skipped += 1
                continue

            # Rate limit — lighter since we're processing fewer pitchers
            if (success + errors) % 10 == 0 and (success + errors) > 0:
                time.sleep(0.3)

            pitcher = build_pitcher_record(row, name, recent_stats, is_fangraphs, is_starter, is_monday)

            # Supplement with Baseball Savant xERA if MLB API source
            if source == 'mlb_api':
                savant = None
                if xera_map:
                    savant = xera_map.get(name.lower()) or xera_map.get(name.split(' ')[-1].lower())
                # 2025 fallback for pitchers not in 2026 Savant leaderboard (rehab, rookies, low sample)
                if (not savant or not savant.get('xERA')) and xera_map_prior:
                    savant = xera_map_prior.get(name.lower()) or xera_map_prior.get(name.split(' ')[-1].lower())
                if savant and savant.get('xERA'):
                    pitcher['xera'] = savant['xERA']
                    if savant.get('xBA'):
                        pitcher['xba_allowed'] = savant['xBA']

            # ─── Savant arsenal supplement (7/25) — ALWAYS runs ────
            # Whiff + hard_hit% from pitch-arsenal-stats. Only overrides
            # when pitcher's existing value is None (post-DQ-fix state)
            # so we don't stomp on legitimate FanGraphs values.
            arsenal = None
            if arsenal_map:
                arsenal = arsenal_map.get(name.lower()) or arsenal_map.get(name.split(' ')[-1].lower())
            if not arsenal and arsenal_map_prior:
                arsenal = arsenal_map_prior.get(name.lower()) or arsenal_map_prior.get(name.split(' ')[-1].lower())
            if arsenal:
                if pitcher.get('whiff_rate') is None and arsenal.get('whiff_rate') is not None:
                    pitcher['whiff_rate'] = arsenal['whiff_rate']
                if pitcher.get('hard_hit_pct') is None and arsenal.get('hard_hit_pct') is not None:
                    pitcher['hard_hit_pct'] = arsenal['hard_hit_pct']

            result = upload_pitcher(pitcher)
            if result:
                success += 1
                if success % 20 == 0:
                    print(f"✅ Uploaded {success} pitchers...")
            else:
                errors += 1

        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"Error on {row.get(name_col, '?')}: {e}")
                traceback.print_exc()
            continue

    print(f"\nDone! ✅ {success} uploaded, ❌ {errors} errors, ⏭️ {skipped} skipped (not starting today)")
    if is_monday:
        print("📋 Full Monday refresh completed")
    else:
        print(f"📋 Daily starters update — {len(todays_starters)} starters targeted")

if __name__ == "__main__":
    run()