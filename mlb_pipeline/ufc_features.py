"""UFC v1 feature pipeline.

Builds a per-fight feature vector from two fighters + context. Pre-fight
snapshot logic queries ufc_fighter_history for record/method counts as of
fight_date. Career-level striking/grappling rates pulled from ufc_fighter_stats.

Used by:
  - ufc_train.py (build training matrix from ufc_fight_results)
  - ufc_predict.py (score upcoming card)
"""
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


# Cache fighter snapshots within a single training run to avoid re-querying
_HISTORY_CACHE = {}
_STATS_CACHE = {}


def fetch_fighter_history(fighter_url):
    """Pull all fights for a fighter, sorted by date asc."""
    if fighter_url in _HISTORY_CACHE:
        return _HISTORY_CACHE[fighter_url]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/ufc_fighter_history"
        f"?fighter_url=eq.{requests.utils.quote(fighter_url)}"
        f"&select=fight_date,result,method,round&order=fight_date.asc",
        headers=HEADERS, timeout=15,
    )
    history = r.json() if r.status_code == 200 else []
    _HISTORY_CACHE[fighter_url] = history
    return history


def fetch_fighter_career_stats(fighter_url):
    """Career-level striking/grappling rates from ufc_fighter_stats."""
    if fighter_url in _STATS_CACHE:
        return _STATS_CACHE[fighter_url]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/ufc_fighter_stats"
        f"?fighter_url=eq.{requests.utils.quote(fighter_url)}&limit=1",
        headers=HEADERS, timeout=15,
    )
    data = r.json() if r.status_code == 200 else []
    out = data[0] if data else {}
    _STATS_CACHE[fighter_url] = out
    return out


def compute_pre_fight_record(fighter_url, target_date):
    """Compute W/L/D + method history strictly BEFORE target_date.

    Returns:
      {wins, losses, draws, no_contests, wins_by_ko, wins_by_sub, wins_by_dec,
       total_fights, finishing_rate, recent_form (L5 win%)}
    """
    history = fetch_fighter_history(fighter_url)
    if not history:
        return {
            "wins": 0, "losses": 0, "draws": 0, "no_contests": 0,
            "wins_by_ko": 0, "wins_by_sub": 0, "wins_by_dec": 0,
            "total_fights": 0, "finishing_rate": 0.0, "recent_form": 0.5,
        }

    target = target_date if isinstance(target_date, str) else target_date.isoformat()
    prior = [h for h in history if h.get("fight_date") and h["fight_date"] < target]
    if not prior:
        # Pro debut — return zeros, model can use 'is_debut' flag if added
        return {
            "wins": 0, "losses": 0, "draws": 0, "no_contests": 0,
            "wins_by_ko": 0, "wins_by_sub": 0, "wins_by_dec": 0,
            "total_fights": 0, "finishing_rate": 0.0, "recent_form": 0.5,
        }

    wins = sum(1 for h in prior if h.get("result") == "win")
    losses = sum(1 for h in prior if h.get("result") == "loss")
    draws = sum(1 for h in prior if h.get("result") == "draw")
    no_contests = sum(1 for h in prior if h.get("result") == "no_contest")

    wins_by_ko = sum(1 for h in prior if h.get("result") == "win" and h.get("method") == "KO/TKO")
    wins_by_sub = sum(1 for h in prior if h.get("result") == "win" and h.get("method") == "SUB")
    wins_by_dec = sum(1 for h in prior if h.get("result") == "win" and (h.get("method") or "").endswith("DEC"))

    total = wins + losses + draws + no_contests
    finishing_rate = (wins_by_ko + wins_by_sub) / wins if wins > 0 else 0.0

    # Recent form: last 5 fights, % win rate
    last_5 = prior[-5:]
    last_5_wins = sum(1 for h in last_5 if h.get("result") == "win")
    recent_form = last_5_wins / len(last_5) if last_5 else 0.5

    return {
        "wins": wins, "losses": losses, "draws": draws, "no_contests": no_contests,
        "wins_by_ko": wins_by_ko, "wins_by_sub": wins_by_sub, "wins_by_dec": wins_by_dec,
        "total_fights": total, "finishing_rate": finishing_rate, "recent_form": recent_form,
    }


def _f(v, default=None):
    if v is None or v == '':
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_height_inches(height_str):
    """'5' 11"' → 71."""
    if not height_str or "'" not in height_str:
        return None
    try:
        ft, inch = height_str.replace('"', '').split("'")
        return int(ft.strip()) * 12 + int(inch.strip())
    except Exception:
        return None


def _parse_reach_inches(reach_str):
    """'72.0\"' → 72.0."""
    if not reach_str:
        return None
    try:
        return float(str(reach_str).replace('"', '').strip())
    except Exception:
        return None


def _age_at_date(dob_str, target_date):
    """DOB string → age in years at target_date."""
    if not dob_str:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            dob = datetime.strptime(dob_str.replace(".", "").strip(), fmt)
            target = datetime.strptime(target_date, "%Y-%m-%d") if isinstance(target_date, str) else target_date
            return (target - dob).days / 365.25
        except (TypeError, ValueError):
            continue
    return None


def build_features(fighter_a_url, fighter_b_url, fight_date, total_rounds_scheduled=3):
    """Build feature vector for a fight between two fighters on fight_date.

    Returns (feature_dict, ok_flag). ok_flag=False if essential data missing.
    """
    a_stats = fetch_fighter_career_stats(fighter_a_url)
    b_stats = fetch_fighter_career_stats(fighter_b_url)
    a_record = compute_pre_fight_record(fighter_a_url, fight_date)
    b_record = compute_pre_fight_record(fighter_b_url, fight_date)

    # Fail if either fighter has zero pre-fight history (debut) — too noisy for v1
    if a_record["total_fights"] == 0 or b_record["total_fights"] == 0:
        return None, False

    # Fail if either is missing essential career stats
    a_slpm = _f(a_stats.get("slpm"), 0)
    b_slpm = _f(b_stats.get("slpm"), 0)
    if a_slpm == 0 or b_slpm == 0:
        return None, False

    a_str_acc = _f(a_stats.get("str_acc"), 40) / 100
    b_str_acc = _f(b_stats.get("str_acc"), 40) / 100
    a_sapm = _f(a_stats.get("sapm"), 4)
    b_sapm = _f(b_stats.get("sapm"), 4)
    a_str_def = _f(a_stats.get("str_def"), 50) / 100
    b_str_def = _f(b_stats.get("str_def"), 50) / 100
    a_td_avg = _f(a_stats.get("td_avg"), 1)
    b_td_avg = _f(b_stats.get("td_avg"), 1)
    a_td_acc = _f(a_stats.get("td_acc"), 35) / 100
    b_td_acc = _f(b_stats.get("td_acc"), 35) / 100
    a_td_def = _f(a_stats.get("td_def"), 50) / 100
    b_td_def = _f(b_stats.get("td_def"), 50) / 100
    a_sub_avg = _f(a_stats.get("sub_avg"), 0.3)
    b_sub_avg = _f(b_stats.get("sub_avg"), 0.3)

    a_height = _parse_height_inches(a_stats.get("height"))
    b_height = _parse_height_inches(b_stats.get("height"))
    a_reach = _parse_reach_inches(a_stats.get("reach"))
    b_reach = _parse_reach_inches(b_stats.get("reach"))
    a_age = _age_at_date(a_stats.get("dob"), fight_date)
    b_age = _age_at_date(b_stats.get("dob"), fight_date)

    a_stance = (a_stats.get("stance") or "Orthodox").lower()
    b_stance = (b_stats.get("stance") or "Orthodox").lower()

    # Engineered diff features
    feat = {
        # Per-fighter raw
        "a_slpm": a_slpm, "b_slpm": b_slpm,
        "a_str_acc": a_str_acc, "b_str_acc": b_str_acc,
        "a_sapm": a_sapm, "b_sapm": b_sapm,
        "a_str_def": a_str_def, "b_str_def": b_str_def,
        "a_td_avg": a_td_avg, "b_td_avg": b_td_avg,
        "a_td_acc": a_td_acc, "b_td_acc": b_td_acc,
        "a_td_def": a_td_def, "b_td_def": b_td_def,
        "a_sub_avg": a_sub_avg, "b_sub_avg": b_sub_avg,

        # Pre-fight records
        "a_wins": a_record["wins"], "b_wins": b_record["wins"],
        "a_losses": a_record["losses"], "b_losses": b_record["losses"],
        "a_total_fights": a_record["total_fights"], "b_total_fights": b_record["total_fights"],
        "a_finishing_rate": a_record["finishing_rate"], "b_finishing_rate": b_record["finishing_rate"],
        "a_ko_wins": a_record["wins_by_ko"], "b_ko_wins": b_record["wins_by_ko"],
        "a_sub_wins": a_record["wins_by_sub"], "b_sub_wins": b_record["wins_by_sub"],
        "a_dec_wins": a_record["wins_by_dec"], "b_dec_wins": b_record["wins_by_dec"],
        "a_recent_form": a_record["recent_form"], "b_recent_form": b_record["recent_form"],

        # Differentials (engineered)
        "reach_diff": (a_reach - b_reach) if (a_reach and b_reach) else 0,
        "height_diff": (a_height - b_height) if (a_height and b_height) else 0,
        "age_diff": (a_age - b_age) if (a_age and b_age) else 0,
        "slpm_diff": a_slpm - b_slpm,
        "str_def_diff": a_str_def - b_str_def,
        "td_def_diff": a_td_def - b_td_def,
        "sub_threat_diff": a_sub_avg - b_sub_avg,
        "finishing_rate_diff": a_record["finishing_rate"] - b_record["finishing_rate"],
        "experience_diff": a_record["total_fights"] - b_record["total_fights"],
        "form_diff": a_record["recent_form"] - b_record["recent_form"],

        # Cross-fighter interactions
        "a_striking_dominance": a_slpm * (1 - b_str_def),  # expected strikes a lands per minute
        "b_striking_dominance": b_slpm * (1 - a_str_def),
        "a_grappling_threat": a_td_avg * (1 - b_td_def),  # expected takedowns by a per 15min
        "b_grappling_threat": b_td_avg * (1 - a_td_def),

        # Stance matchup
        "stance_southpaw_a": 1 if "southpaw" in a_stance else 0,
        "stance_southpaw_b": 1 if "southpaw" in b_stance else 0,
        "stance_mismatch": 1 if (("southpaw" in a_stance) != ("southpaw" in b_stance)) else 0,

        # Fight context
        "is_5_round": 1 if total_rounds_scheduled >= 5 else 0,
    }
    return feat, True


def label_for_fight(fight_row):
    """Extract training labels from a ufc_fight_results row.

    Returns:
      {
        winner_a: 1 if fighter_a won, 0 otherwise (skips draws/NC)
        method_class: 0=KO/TKO, 1=SUB, 2=DEC (incl all DEC types)
        went_distance: 0/1
        finish_round: 1-5 (or None if went distance)
      }
      or None if fight ineligible (draw, no_contest, cancelled).
    """
    winner = fight_row.get("winner")
    if winner not in ("a", "b"):
        return None

    method = fight_row.get("method") or ""
    if "KO" in method or "TKO" in method:
        method_class = 0  # KO/TKO
    elif "SUB" in method:
        method_class = 1  # SUB
    elif "DEC" in method:
        method_class = 2  # DEC
    else:
        return None  # DQ or unknown — skip

    went = bool(fight_row.get("went_distance"))
    rd = fight_row.get("round")

    return {
        "winner_a": 1 if winner == "a" else 0,
        "method_class": method_class,
        "went_distance": 1 if went else 0,
        "finish_round": rd if (rd and not went) else None,
    }
