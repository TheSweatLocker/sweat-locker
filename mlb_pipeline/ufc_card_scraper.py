import requests
from bs4 import BeautifulSoup
import os
import time
import json
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

BASE_URL = "http://www.ufcstats.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal"
}

def get_upcoming_card():
    """Fetch the next UFC event and its fight card from UFCStats.com"""
    try:
        r = requests.get(f"{BASE_URL}/statistics/events/upcoming", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.content, 'html.parser')

        # Find the first upcoming event link
        table = soup.find('table', class_='b-statistics__table-events')
        if not table:
            print("No events table found")
            return None, None, []

        rows = table.find_all('tr', class_='b-statistics__table-row')
        event_link = None
        event_name = None
        event_date = None

        for row in rows:
            link = row.find('a', class_='b-link')
            if link and link.get('href'):
                event_link = link['href']
                event_name = link.text.strip()
                # Get date from the row
                date_cell = row.find('span', class_='b-statistics__date')
                if date_cell:
                    event_date = date_cell.text.strip()
                break

        if not event_link:
            print("No upcoming event found")
            return None, None, []

        print(f"Next event: {event_name} ({event_date})")
        print(f"Event URL: {event_link}")

        # Scrape the event page for fight card
        r2 = requests.get(event_link, headers=HEADERS, timeout=15)
        soup2 = BeautifulSoup(r2.content, 'html.parser')

        fights = []
        fighter_names = set()
        fight_rows = soup2.find_all('tr', class_='b-fight-details__table-row')

        for row in fight_rows:
            links = row.find_all('a', class_='b-link')
            fighter_links = []
            for link in links:
                href = link.get('href', '')
                if '/fighter-details/' in href:
                    fighter_links.append({
                        'name': link.text.strip(),
                        'url': href
                    })

            if len(fighter_links) >= 2:
                fights.append({
                    'fighter1': fighter_links[0]['name'],
                    'fighter2': fighter_links[1]['name'],
                    'fighter1_url': fighter_links[0]['url'],
                    'fighter2_url': fighter_links[1]['url'],
                })
                fighter_names.add(fighter_links[0]['name'])
                fighter_names.add(fighter_links[1]['name'])

        print(f"Found {len(fights)} fights, {len(fighter_names)} fighters")
        return event_name, event_date, fights

    except Exception as e:
        print(f"Error fetching upcoming card: {e}")
        return None, None, []

def parse_fighter(url):
    """Parse individual fighter page for stats (reused from ufc_scraper.py)"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.content, 'html.parser')

        name_tag = soup.find('span', class_='b-content__title-highlight')
        if not name_tag:
            return None
        name = name_tag.text.strip()

        nick_tag = soup.find('p', class_='b-content__Nickname')
        nickname = nick_tag.text.strip() if nick_tag else None

        record_tag = soup.find('span', class_='b-content__title-record')
        record = record_tag.text.strip().replace('Record:', '').strip() if record_tag else None

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

        def get_stat(label, soup):
            items = soup.find_all('li', class_='b-list__box-list-item')
            for item in items:
                text = item.text.strip()
                if label in text:
                    val = text.replace(label, '').strip()
                    val = ' '.join(val.split())
                    return val if val and val != '--' else None
            return None

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

        height = get_stat('Height:', soup)
        weight = get_stat('Weight:', soup)
        reach = get_stat('Reach:', soup)
        stance = get_stat('STANCE:', soup)
        dob = get_stat('DOB:', soup)
        slpm = get_stat('SLpM:', soup)
        str_acc = get_stat('Str. Acc.:', soup)
        sapm = get_stat('SApM:', soup)
        str_def = get_stat('Str. Def:', soup)
        td_avg = get_stat('TD Avg.:', soup)
        td_acc = get_stat('TD Acc.:', soup)
        td_def = get_stat('TD Def.:', soup)
        sub_avg = get_stat('Sub. Avg.:', soup)

        wins_by_ko, wins_by_sub, wins_by_dec = 0, 0, 0
        method_items = soup.find_all('p', class_='b-list__box-list-item_type_block')
        for item in method_items:
            text = item.text.strip()
            if 'KO/TKO' in text:
                try: wins_by_ko = int(text.split('\n')[-1].strip())
                except: pass
            elif 'SUB' in text:
                try: wins_by_sub = int(text.split('\n')[-1].strip())
                except: pass
            elif 'DEC' in text:
                try: wins_by_dec = int(text.split('\n')[-1].strip())
                except: pass

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
        print(f"  Error parsing fighter: {e}")
        return None

def upload_fighter(record):
    """Upsert fighter stats to Supabase"""
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ufc_fighter_stats?on_conflict=fighter_name",
        headers=SUPABASE_HEADERS,
        json=record
    )
    return r.status_code in [200, 201, 204]

def upload_event_context(event_name, event_date, fights):
    """Store upcoming event and fight card in Supabase"""
    try:
        fight_card = [{
            'fighter1': f['fighter1'],
            'fighter2': f['fighter2'],
        } for f in fights]

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/ufc_upcoming_event?on_conflict=event_name",
            headers=SUPABASE_HEADERS,
            json={
                "event_name": event_name,
                "event_date": event_date,
                "fight_card": json.dumps(fight_card),
                "updated_at": datetime.now().isoformat()
            }
        )
        if r.status_code in [200, 201, 204]:
            print(f"✅ Event context saved: {event_name}")
        else:
            print(f"⚠️ Event upload failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Error uploading event: {e}")

def run():
    print("UFC Card Scraper — Targeting upcoming event only")

    event_name, event_date, fights = get_upcoming_card()
    if not fights:
        print("No upcoming fights found — exiting")
        return

    # Upload event context
    upload_event_context(event_name, event_date, fights)

    # Scrape and upload each fighter
    success = 0
    errors = 0
    seen = set()

    for fight in fights:
        for key in ['fighter1_url', 'fighter2_url']:
            url = fight[key]
            name = fight['fighter1'] if key == 'fighter1_url' else fight['fighter2']

            if url in seen:
                continue
            seen.add(url)

            time.sleep(0.5)
            fighter = parse_fighter(url)
            if fighter:
                result = upload_fighter(fighter)
                if result:
                    success += 1
                    print(f"  ✅ {fighter['fighter_name']} ({fighter['record']}) — finishing rate {fighter['finishing_rate']}%")
                else:
                    errors += 1
                    print(f"  ❌ Upload failed: {name}")
            else:
                errors += 1
                print(f"  ❌ Parse failed: {name}")

    print(f"\nDone! ✅ {success} fighters updated, ❌ {errors} errors")
    print(f"Card: {len(fights)} fights for {event_name}")

if __name__ == '__main__':
    run()
