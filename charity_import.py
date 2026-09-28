"""
Charity / Nonprofit Importer -- Vienna, Austria ONLY
------------------------------------------------------
Same shape as bartlett_import_v3.py (Google Places Nearby Search + Place
Details for up to 10 photos per listing), but seeds the `listings` table
with charity/nonprofit organizations instead of general businesses, all
tagged category="charity" so the existing Discover map/listing UI picks
them up automatically (no schema changes needed -- `listings` has no
category whitelist).

Scoped on purpose to Vienna, Austria only (same coverage area as
vienna_import.py: a 3km radius Nearby Search around central Vienna, so
results can't come back from anywhere else). Do not repoint this at
another city -- if a different area is needed later, copy this file
into a new script instead of changing LAT/LNG/CITY/STATE here.
"""

import os, time, json, requests, psycopg2
from slugify import slugify

DATABASE_URL = os.environ.get("DATABASE_URL")
GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

LAT, LNG = 48.2082, 16.3738  # same coverage area as vienna_import.py
RADIUS = 3000
CITY, STATE = "Vienna", "Wien"

# Nearby Search keyword terms aimed at charities/nonprofits rather than
# general businesses. Google Places doesn't have a dedicated "charity"
# place type, so this leans on keyword search the same way the other
# import scripts do for "coffee shop", "gym", etc.
SEARCH_TERMS = [
    "charity", "nonprofit organization", "food bank", "food pantry",
    "homeless shelter", "goodwill", "salvation army", "donation center",
    "thrift store", "community foundation", "animal shelter",
    "red cross", "volunteer organization", "soup kitchen",
]

CATEGORY = "charity"


def get_photo_url(ref, max_width=800):
    if not ref:
        return None
    return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth={max_width}&photo_reference={ref}&key={GOOGLE_API_KEY}"


def get_place_details(place_id):
    """Fetch full details including all photos."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_address,geometry,types,photos,rating,formatted_phone_number,website,editorial_summary",
        "key": GOOGLE_API_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    return r.json().get("result", {})


def search_places(term, page_token=None):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    if page_token:
        params = {"pagetoken": page_token, "key": GOOGLE_API_KEY}
    else:
        params = {"location": f"{LAT},{LNG}", "radius": RADIUS, "keyword": term, "key": GOOGLE_API_KEY}
    r = requests.get(url, params=params, timeout=10)
    return r.json()


def insert_listing(conn, cur, place):
    name = place.get("name", "")
    if not name:
        return False

    slug = slugify(name)
    cur.execute("SELECT id FROM listings WHERE slug = %s", (slug,))
    if cur.fetchone():
        slug = f"{slug}-charity"
    cur.execute("SELECT id FROM listings WHERE slug = %s", (slug,))
    if cur.fetchone():
        print(f"  SKIP: {name}")
        return False

    # Fetch full details for more photos
    place_id = place.get("place_id")
    details = get_place_details(place_id) if place_id else {}
    time.sleep(0.15)  # respect rate limit

    geo = place.get("geometry", {}).get("location", {})
    vicinity = place.get("vicinity", "")
    address = details.get("formatted_address", vicinity)
    phone = details.get("formatted_phone_number", "")
    website = details.get("website", "")
    description = details.get("editorial_summary", {}).get("overview", "")

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
            latitude, longitude, featured, status
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (slug) DO NOTHING
    """, (
        name, slug, CATEGORY, CITY, STATE, address,
        phone, website, description,
        photo_url, photo_urls_json, card_image_url,
        geo.get("lat"), geo.get("lng"),
        0, "published"
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
    print("Connected.\n")

    for term in SEARCH_TERMS:
        print(f"\n── Searching: {term}")
        token = None
        for _ in range(3):
            data = search_places(term, token)
            for place in data.get("results", []):
                pid = place.get("place_id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                insert_listing(conn, cur, place)
            token = data.get("next_page_token")
            if not token:
                break
            time.sleep(2)

    cur.close()
    conn.close()
    print(f"\n✅ Done! Processed {len(seen_ids)} unique places.")


if __name__ == "__main__":
    main()
