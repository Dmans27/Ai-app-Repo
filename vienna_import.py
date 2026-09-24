"""
Vienna Business Directory Importer
-----------------------------------
Same approach as bartlett_import_v3.py, retargeted at Vienna, Austria.

Unlike Bartlett, there's no single village-run "business directory" page to
scrape for Vienna, so this pulls listings straight from Google Places
(Nearby Search, keyword by keyword, radius-limited around a center point)
instead of scraping a website. That also makes it easy to start small: set
LAT/LNG/RADIUS to just your own Grätzl first, then widen later.

Finding coordinates for a specific neighborhood: right-click the spot on
Google Maps and choose "What's here?" — it shows the lat/lng directly.

Usage:
    pip install requests psycopg2-binary python-slugify
    export DATABASE_URL="your_render_postgres_url"
    export GOOGLE_MAPS_API_KEY="your_key"
    python3 vienna_import.py
"""

import os, time, json, requests, psycopg2
from slugify import slugify

DATABASE_URL = os.environ.get("DATABASE_URL")
GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

# ── Where to search ─────────────────────────────────────────────────────────
# Defaults to central Vienna (Stephansplatz) with a 3km radius, which keeps
# this to a "small scale" pilot rather than trying to cover the whole city
# in one run. Narrow LAT/LNG to your own Grätzl (see docstring above) and/or
# shrink RADIUS further if you want to start even smaller.
LAT, LNG = 48.2082, 16.3738
RADIUS = 3000  # meters

CITY = "Vienna"
STATE = "Wien"  # Austria's states are called Bundesländer; Vienna is its own

# Google Places responses are requested in English so the stored `city`
# value stays "Vienna" (matching what the Discover page's location-based
# switcher detects) rather than the German "Wien".
LANGUAGE = "en"

# Starting narrow on purpose: just pizza places for now. Add more terms
# back in here once the pizza pass looks good (a term is just a Google
# Places keyword search, so "pizzeria" would also work if "pizza" ever
# turns up results that feel off).
SEARCH_TERMS = [
    "pizza",
]

def map_category(types):
    t = " ".join(types or [])
    if any(x in t for x in ["restaurant", "food", "meal", "cafe", "bakery", "bar"]):
        return "restaurants"
    if any(x in t for x in ["store", "shop", "retail", "grocery"]):
        return "shopping"
    if any(x in t for x in ["gym", "fitness", "spa", "beauty", "hair", "salon"]):
        return "health & fitness"
    if any(x in t for x in ["hospital", "doctor", "health", "pharmacy", "dentist"]):
        return "healthcare"
    if any(x in t for x in ["finance", "bank", "insurance", "lawyer"]):
        return "financial & legal"
    return "places"

def get_photo_url(ref, max_width=800):
    if not ref:
        return None
    return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth={max_width}&photo_reference={ref}&key={GOOGLE_API_KEY}"

def get_place_details(place_id):
    """Fetch full details including all photos."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_address,geometry,types,photos,rating,user_ratings_total,formatted_phone_number,website,editorial_summary",
        "language": LANGUAGE,
        "key": GOOGLE_API_KEY
    }
    r = requests.get(url, params=params, timeout=10)
    return r.json().get("result", {})

def search_places(term, page_token=None):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    if page_token:
        params = {"pagetoken": page_token, "language": LANGUAGE, "key": GOOGLE_API_KEY}
    else:
        params = {
            "location": f"{LAT},{LNG}",
            "radius": RADIUS,
            "keyword": term,
            "language": LANGUAGE,
            "key": GOOGLE_API_KEY
        }
    r = requests.get(url, params=params, timeout=10)
    return r.json()

def insert_listing(conn, cur, place):
    name = place.get("name", "")
    if not name:
        return False

    slug = slugify(name)
    cur.execute("SELECT id FROM listings WHERE slug = %s", (slug,))
    if cur.fetchone():
        slug = f"{slug}-vienna"
    cur.execute("SELECT id FROM listings WHERE slug = %s", (slug,))
    if cur.fetchone():
        print(f"  SKIP: {name}")
        return False

    # Fetch full details for more photos
    place_id = place.get("place_id")
    details = get_place_details(place_id) if place_id else {}
    time.sleep(0.15)  # respect rate limit

    geo = place.get("geometry", {}).get("location", {})
    types = place.get("types", [])
    vicinity = place.get("vicinity", "")
    address = details.get("formatted_address", vicinity)
    phone = details.get("formatted_phone_number", "")
    website = details.get("website", "")
    description = details.get("editorial_summary", {}).get("overview", "")
    google_rating = details.get("rating")
    google_rating_count = details.get("user_ratings_total")

    # Use details photos (up to 10) or fall back to nearby search photo
    photos = details.get("photos", place.get("photos", []))
    photo_url = None
    photo_urls_json = None
    card_image_url = None

    if photos:
        all_refs = [p["photo_reference"] for p in photos[:10] if p.get("photo_reference")]
        all_urls = [get_photo_url(r) for r in all_refs]
        photo_url = all_urls[0] if all_urls else None
        card_image_url = photo_url
        photo_urls_json = json.dumps(all_urls)

    cur.execute("""
        INSERT INTO listings (
            name, slug, category, city, state, address,
            phone, website, description,
            photo_url, photo_urls_json, card_image_url,
            latitude, longitude, featured, status,
            place_id, google_rating, google_rating_count
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (slug) DO NOTHING
    """, (
        name, slug, map_category(types), CITY, STATE, address,
        phone, website, description,
        photo_url, photo_urls_json, card_image_url,
        geo.get("lat"), geo.get("lng"),
        0, "published",
        place_id, google_rating, google_rating_count
    ))
    conn.commit()
    photo_count = len(json.loads(photo_urls_json)) if photo_urls_json else 0
    print(f"  ✓ {name} ({photo_count} photos)")
    return True

def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        return
    if not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    seen_ids = set()
    print(f"Connected. Searching within {RADIUS}m of {LAT},{LNG} ({CITY}).\n")

    for term in SEARCH_TERMS:
        print(f"\n── Searching: {term}")
        token = None
        for _ in range(3):
            data = search_places(term, token)
            status = data.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                print(f"  API ERROR for '{term}': {status} — {data.get('error_message', '')}")
                break
            for place in data.get("results", []):
                pid = place.get("place_id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                try:
                    insert_listing(conn, cur, place)
                except Exception as e:
                    print(f"  ERROR inserting {place.get('name', '?')}: {e}")
            token = data.get("next_page_token")
            if not token:
                break
            time.sleep(2)  # next_page_token isn't valid until a short delay passes

    cur.close()
    conn.close()
    print(f"\n✅ Done! Processed {len(seen_ids)} unique places in {CITY}.")

if __name__ == "__main__":
    main()
