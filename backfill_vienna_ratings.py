"""
Vienna Ratings Backfill
------------------------
One-time pass for listings that were already imported by vienna_import.py
BEFORE it started capturing Google's rating (and place_id). Looks each one
back up on Google Places by name + address, then fills in place_id,
google_rating, and google_rating_count on the existing row.

Safe to re-run: it only touches listings where google_rating IS NULL, so
anything it successfully backfills is skipped on the next run, and nothing
it can't find on Google is touched at all (no row is deleted or overwritten
with blanks).

Usage:
    pip install requests psycopg2-binary
    export DATABASE_URL="your_render_postgres_url"
    export GOOGLE_MAPS_API_KEY="your_key"
    python3 backfill_vienna_ratings.py
"""

import os, time, requests, psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")
GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

LAT, LNG = 48.2082, 16.3738
RADIUS = 3000  # meters -- same search area vienna_import.py used
CITY = "Vienna"
LANGUAGE = "en"


def find_place_id(name, address):
    """Look a business up by name + address to get its Google place_id."""
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    params = {
        "input": f"{name}, {address}" if address else name,
        "inputtype": "textquery",
        "fields": "place_id",
        "locationbias": f"circle:{RADIUS}@{LAT},{LNG}",
        "language": LANGUAGE,
        "key": GOOGLE_API_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    candidates = data.get("candidates") or []
    return candidates[0]["place_id"] if candidates else None


def get_rating(place_id):
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "rating,user_ratings_total",
        "language": LANGUAGE,
        "key": GOOGLE_API_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    return r.json().get("result", {})


def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        return
    if not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not set")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT id, name, address
        FROM listings
        WHERE city = %s AND google_rating IS NULL
        ORDER BY name
        """,
        (CITY,),
    )
    rows = cur.fetchall()
    print(f"Found {len(rows)} {CITY} listing(s) with no Google rating yet.\n")

    updated = 0
    not_found = 0

    for row in rows:
        name = row["name"]
        address = row["address"] or ""

        place_id = find_place_id(name, address)
        time.sleep(0.15)

        if not place_id:
            print(f"  ? no Google match: {name}")
            not_found += 1
            continue

        details = get_rating(place_id)
        time.sleep(0.15)

        rating = details.get("rating")
        rating_count = details.get("user_ratings_total")

        cur.execute(
            """
            UPDATE listings
            SET place_id = %s,
                google_rating = %s,
                google_rating_count = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (place_id, rating, rating_count, row["id"]),
        )
        conn.commit()

        stars = f"{rating}★ ({rating_count})" if rating is not None else "no rating on Google"
        print(f"  ✓ {name}: {stars}")
        updated += 1

    cur.close()
    conn.close()
    print(f"\n✅ Done! Updated {updated} listing(s), {not_found} not matched on Google.")


if __name__ == "__main__":
    main()
