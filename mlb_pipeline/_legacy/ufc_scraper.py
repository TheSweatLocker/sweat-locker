import requests
from bs4 import BeautifulSoup
import os
import time
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

BASE_URL = "http://www.ufcstats.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def get_fighter_urls():
    """Get all fighter URLs from UFCStats alphabetical pages"""
    fighter_urls = []
    for char in 'abcdefghijklmnopqrstuvwxyz':
        try:
            url = f"{BASE_URL}/statistics/fighters?char={char}&page=all"
            r = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.content, 'html.parser')
            table = soup.find('table', class_='b-statistics__table')
            if not table:
                continue
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) < 3:
                    continue
                link = cols[0].find('a', class_='b-link')
                if link and link.get('href'):
                    fighter_urls.append(link['href'])
            time.sleep(0.3)
        except Exception as e:
            print(f"Error fetching letter {char}: {e}")
    print(f"Found {len(fighter_urls)} fighter URLs")
    return fighter_urls

def parse_fighter(url):
    """Parse individual fighter page for stats"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.content, 'html.parser')

        # Name
        name_tag = soup.find('span', class_='b-content__title-highlight')
        if not name_tag:
            return None
        name = name_tag.text.strip()

        # Nickname
        nick_tag = soup.find('p', class_='b-content__Nickname')
        nickname = nick_tag.text.strip() if nick_tag else None

        # Record
        record_tag = soup.find('span', class_='b-content__title-record')
        record = record_tag.text.strip().replace('Record:', '').strip() if record_tag else None

        # Parse wins/losses/draws from record
        total_wins, total_losses, total_draws = 0, 0, 0
        if record:
            parts = record.split('-')
            if len(parts) >= 3:
                try:
                    total_wins = int(parts[0].strip())
                    total_losses = int(parts[1].strip())
                    total_draws = int(parts[2].strip().split('(')[0].strip())
                except:
                    pass

        # Physical stats
        def get_stat(label, soup):
            items = soup.find_all('li', class_='b-list__box-list-item')
            for item in items:
                text = item.text.strip()
                if label in text:
                    val = text.replace(label, '').strip()
                    val = ' '.join(val.split())  # collapse whitespace
                    return val if val and val != '--' else None
            return None

        height = get_stat('Height:', soup)
        weight = get_stat('Weight:', soup)
        reach = get_stat('Reach:', soup)
        stance = get_stat('STANCE:', soup)
        dob = get_stat('DOB:', soup)

        # Career stats
        slpm = get_stat('SLpM:', soup)
        str_acc = get_stat('Str. Acc.:', soup)
        sapm = get_stat('SApM:', soup)
        str_def = get_stat('Str. Def:', soup)
        td_avg = get_stat('TD Avg.:', soup)
        td_acc = get_stat('TD Acc.:', soup)
        td_def = get_stat('TD Def.:', soup)
        sub_avg = get_stat('Sub. Avg.:', soup)

        # Clean percentage values
        def clean_pct(val):
            if val:
                return float(val.replace('%', '').strip()) if val != '--' else None
            return None

        def clean_float(val):
            if val:
                try:
                    return float(val.strip()) if val != '--' else None
                except:
                    return None
            return None

        # Win methods from career stats box
        wins_by_ko, wins_by_sub, wins_by_dec = 0, 0, 0
        method_items = soup.find_all('p', class_='b-list__box-list-item_type_block')
        for item in method_items:
            text = item.text.strip()
            if 'KO/TKO' in text:
                try:
                    wins_by_ko = int(text.split('\n')[-1].strip())
                except:
                    pass
            elif 'SUB' in text:
                try:
                    wins_by_sub = int(text.split('\n')[-1].strip())
                except:
                    pass
            elif 'DEC' in text:
                try:
                    wins_by_dec = int(text.split('\n')[-1].strip())
                except:
                    pass

        # Finishing rate
        finishing_rate = None
        if total_wins > 0:
            finishing_rate = round((wins_by_ko + wins_by_sub) / total_wins * 100, 1)

        return {
            "fighter_name": name,
            "nickname": nickname,
            "record": record,
            "height": height,
            "weight": weight,
            "reach": reach,
            "stance": stance,
            "dob": dob,
            "slpm": clean_float(slpm),
            "str_acc": clean_pct(str_acc),
            "sapm": clean_float(sapm),
            "str_def": clean_pct(str_def),
            "td_avg": clean_float(td_avg),
            "td_acc": clean_pct(td_acc),
            "td_def": clean_pct(td_def),
            "sub_avg": clean_float(sub_avg),
            "wins_by_ko": wins_by_ko,
            "wins_by_sub": wins_by_sub,
            "wins_by_dec": wins_by_dec,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "total_draws": total_draws,
            "finishing_rate": finishing_rate,
            "fighter_url": url,
            "updated_at": datetime.now().isoformat()
        }
    except Exception as e:
        return None

def upload_fighter(record):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ufc_fighter_stats?on_conflict=fighter_name",
        headers=headers,
        json=record
    )
    if r.status_code not in [200, 201, 204]:
        return False
    return True

def run():
    print("Starting UFC fighter scrape...")
    urls = get_fighter_urls()

    if not urls:
        print("No fighter URLs found")
        return

    success = 0
    errors = 0
    skip = 0

    for i, url in enumerate(urls):
        try:
            fighter = parse_fighter(url)
            if not fighter:
                skip += 1
                continue

            # Only upload active fighters with real records
            if fighter['total_wins'] == 0 and fighter['total_losses'] == 0:
                skip += 1
                continue

            if upload_fighter(fighter):
                success += 1
                if success % 50 == 0:
                    print(f"✅ {success} fighters uploaded... ({fighter['fighter_name']})")
            else:
                errors += 1

            time.sleep(0.2)

        except Exception as e:
            errors += 1

    print(f"\nDone! ✅ {success} fighters, ❌ {errors} errors, ⏭ {skip} skipped")

if __name__ == "__main__":
    run()