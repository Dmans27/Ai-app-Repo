"""
Bartlett Business Directory Importer
-------------------------------------
1. Scrapes all 19 pages of bartlettil.gov/doing-business/bartlett-businesses
2. Looks up each business via Google Places API (name + city)
3. Inserts into your listings table (PostgreSQL via DATABASE_URL)

Usage:
    pip install requests beautifulsoup4 psycopg2-binary python-slugify
    export DATABASE_URL="your_render_postgres_url"
    export GOOGLE_MAPS_API_KEY="your_key"
    python3 bartlett_import.py
"""

import os
import re
import time
import json
import requests
import psycopg2
from bs4 import BeautifulSoup
from slugify import slugify

DATABASE_URL = os.environ.get("DATABASE_URL")
GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
BASE_URL = "https://www.bartlettil.gov/doing-business/bartlett-businesses"
TOTAL_PAGES = 19
CITY = "Bartlett"
STATE = "IL"

# ── Category mapping from Google Places types ──────────────────────────────
def map_category(types):
    if not types:
        return "places"
    t = " ".join(types)
    if any(x in t for x in ["restaurant", "food", "meal", "cafe", "bakery", "bar"]):
        return "restaurants"
    if any(x in t for x in ["store", "shop", "retail", "clothing", "supermarket", "grocery"]):
        return "shopping"
    if any(x in t for x in ["gym", "fitness", "spa", "beauty", "hair", "salon"]):
        return "health & fitness"
    if any(x in t for x in ["hospital", "doctor", "health", "pharmacy", "dentist", "medical"]):
        return "healthcare"
    if any(x in t for x in ["finance", "bank", "insurance", "accounting", "lawyer", "legal"]):
        return "financial & legal"
    return "places"

# ── Scrape one page ────────────────────────────────────────────────────────
def scrape_page(page_num):
    if page_num == 1:
        url = BASE_URL
    else:
        url = f"{BASE_URL}/-npage-{page_num}"
    
    resp = requests.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    businesses = []

    for h2 in soup.select("h2 a"):
        item = {"name": h2.get_text(strip=True), "phone": "", "website": "", "address": ""}
        parent = h2.find_parent("li")
        if not parent:
            businesses.append(item)
            continue
        for li in parent.select("li"):
            text = li.get_text(" ", strip=True)
            if text.startswith("Address:"):
                item["address"] = text.replace("Address:", "").strip()
            elif text.startswith("Phone:"):
                parts = text.replace("Phone:", "").strip()
                # website may be on same line
                if "Website:" in parts:
                    phone_part, web_part = parts.split("Website:", 1)
                    item["phone"] = phone_part.strip()
                    item["website"] = web_part.strip()
                else:
                    item["phone"] = parts
            elif text.startswith("Website:"):
                item["website"] = text.replace("Website:", "").strip()
        # also grab website from <a> tags
        if not item["website"]:
            for a in parent.select("a[href^='http']"):
                href = a["href"]
                if "bartlettil.gov" not in href and "google.com/maps" not in href:
                    item["website"] = href
                    break
        businesses.append(item)
    return businesses

# ── Google Places lookup ───────────────────────────────────────────────────
def lookup_place(name, address):
    query = f"{name} {address or (CITY + ' ' + STATE)}"
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    params = {
        "input": query,
        "inputtype": "textquery",
        "fields": "place_id,name,formatted_address,geometry,types,photos,rating,formatted_phone_number,website",
        "locationbias": "circle:10000@41.9775,-88.1859",
        "key": GOOGLE_API_KEY
    }
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    place_id = candidates[0].get("place_id")
    if not place_id:
        return None

    # Get full details
    detail_url = "https://maps.googleapis.com/maps/api/place/details/json"
    detail_params = {
        "place_id": place_id,
        "fields": "name,formatted_address,geometry,types,photos,rating,formatted_phone_number,website,editorial_summary",
        "key": GOOGLE_API_KEY
    }
    dr = requests.get(detail_url, params=detail_params, timeout=10)
    return dr.json().get("result", {})

# ── Build photo URL ────────────────────────────────────────────────────────
def get_photo_url(photo_reference, max_width=800):
    if not photo_reference:
        return None
    return (
        f"https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={max_width}&photo_reference={photo_reference}&key={GOOGLE_API_KEY}"
    )

# ── Insert into DB ─────────────────────────────────────────────────────────
def insert_listing(conn, cur, biz, place):
    name = biz["name"]
    base_slug = slugify(name)
    
    # Check for duplicate slug
    cur.execute("SELECT id FROM listings WHERE slug = %s", (base_slug,))
    if cur.fetchone():
        base_slug = f"{base_slug}-bartlett"
    cur.execute("SELECT id FROM listings WHERE slug = %s", (base_slug,))
    if cur.fetchone():
        print(f"  SKIP (duplicate): {name}")
        return

    # Extract from Google Places result
    lat = lng = None
    photo_url = None
    photo_urls_json = None
    description = None
    category = "places"
    address = biz.get("address", "")
    phone = biz.get("phone", "")
    website = biz.get("website", "")

    if place:
        geo = place.get("geometry", {}).get("location", {})
        lat = geo.get("lat")
        lng = geo.get("lng")
        
        types = place.get("types", [])
        category = map_category(types)
        
        # Description
        editorial = place.get("editorial_summary", {})
        description = editorial.get("overview", "")
        
        # Phone/website from Google if not scraped
        if not phone:
            phone = place.get("formatted_phone_number", "")
        if not website:
            website = place.get("website", "")

        # Photos
        photos = place.get("photos", [])
        if photos:
            photo_url = get_photo_url(photos[0].get("photo_reference"))
            all_refs = [p.get("photo_reference") for p in photos[:6] if p.get("photo_reference")]
            photo_urls_json = json.dumps([get_photo_url(r) for r in all_refs])

    cur.execute("""
        INSERT INTO listings 
            (name, slug, category, city, state, address, phone, website,
             description, latitude, longitude, photo_url, photo_urls_json,
             card_image_url, featured, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (slug) DO NOTHING
    """, (
        name, base_slug, category, CITY, STATE,
        address, phone, website,
        description, lat, lng,
        photo_url, photo_urls_json, photo_url,
        0, "published"
    ))
    conn.commit()
    print(f"  ✓ {name} ({category})")

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        return
    if not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    print("Connected to database.\n")

    total_inserted = 0
    total_skipped = 0

    for page in range(1, TOTAL_PAGES + 1):
        print(f"\n── Page {page}/{TOTAL_PAGES} ──────────────────")
        businesses = scrape_page(page)
        print(f"Found {len(businesses)} businesses")

        for biz in businesses:
            print(f"  Looking up: {biz['name']}")
            try:
                place = lookup_place(biz["name"], biz.get("address", ""))
                insert_listing(conn, cur, biz, place)
                total_inserted += 1
            except Exception as e:
                print(f"  ERROR: {biz['name']} — {e}")
                total_skipped += 1
            time.sleep(0.3)  # be polite to the API

        time.sleep(1)  # pause between pages

    cur.close()
    conn.close()
    print(f"\n✅ Done! Inserted: {total_inserted}, Skipped: {total_skipped}")

if __name__ == "__main__":
    main()