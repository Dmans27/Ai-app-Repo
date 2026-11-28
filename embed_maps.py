"""
Map-only pages that another website can show inside an iframe.

The \\\\Mansfield media site has an "Interactive map" block for its stories:
the writer pastes a map link from this app and readers get the live,
draggable map right inside the story. This file provides the pages that
iframe loads:

  /embed/list/<slug>   one public list, as a map
  /embed/u/<user_id>   every place in a user's PUBLIC lists, on one map
  /embed/card/<slug>   one directory listing (a business or hotel) as a card,
                       ?style=photo|wide|full|strip -- for stories about a
                       single place. Its pictures come through
                       /embed/listing-photo/<slug> and its details through
                       /embed/card-detail/<slug>, the same way as for pins.
  /embed/stack?d=...   several of those cards in a swipe row (d is a small
                       base64 JSON list: [{"s": slug, "b": booking link,
                       "l": button label, "v": video link}, ...]).
  /embed/walk?c=lng,lat;lng,lat;...   the walking path through a route's stops
                       (travel "route" stories), from Mapbox walking
                       directions -- see "Walking paths" below.
  /embed/route-map?s=slug,slug,...    a map of just those directory listings,
                       in that order (the media site's AI "Plan a walk").
  /embed/explore?lat=..&lng=..&z=..   a clean map (no pins) to look around before
                       planning a walk, opened at that spot and zoom (the media
                       site's "Plan a walk" page)
  /embed/places-near   directory listings near a point or in a city, with
                       filters -- for the media site's server only (it sends
                       the shared WALK_PLAN_KEY), see "AI walks" below.

A card may also carry a Book / Reserve button (?b=<link>&l=<label>) and a short
video (?v=<link>) that plays when the reader double-taps the picture. Booking
links must be web links; a video must be a plain video file hosted on one of the
sites named in EMBED_ALLOWED_ORIGINS (the media site), never anywhere else.

Both pages are just a map (no header, menu or sign-in) and are marked
noindex so search engines don't list them as pages of their own.

Who may show them? Only the sites named in EMBED_ALLOWED_ORIGINS (space or
comma separated, e.g. "https://the-sod-father.onrender.com"). Every
response from /embed/ carries a Content-Security-Policy "frame-ancestors"
header built from that list, and browsers refuse to show the page inside
any other website. Change the setting in Render if the media site gets a
custom domain.

Pictures on the cards come through /embed/photo/<saved place id>. That route
looks the photo up on the server (so the Google API key stored inside some
older photo links is never put in the embed page), asks Google for the real
image address, and sends the browser there. Answers are cached for a few hours
so a busy article doesn't turn into lots of billable Google photo requests.

The full-size "Show listing" panel inside the map gets its facts from
/embed/place/<saved place id>: your own directory data (phone, description,
rating, on-site reviews) plus live details from Google Places (opening hours,
open-now, what the place serves), fetched on the server with the Google key and
remembered for 24 hours so a busy article doesn't mean many billable requests.

Wired up with two lines near the bottom of cms_app.py:

    from embed_maps import register_embed_routes
    register_embed_routes(app, SavedList, query_one)
"""
import base64
import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from flask import abort, jsonify, redirect, render_template, request, url_for
from sqlalchemy import text as sql_text
from sqlalchemy import (Column, DateTime, Integer, MetaData, PrimaryKeyConstraint,
                        String, Table, Text, delete, func, insert, select)

DEFAULT_ALLOWED_ORIGINS = "https://the-sod-father.onrender.com https://locallaneapp.com https://www.locallaneapp.com"

# The embed page only ever uses one of these Mapbox styles, picked with
# ?style=light|dark|streets|mansfield (anything else falls back to light).
# "mansfield" is Danny's own walking style from Mapbox Studio, used for walking
# routes; MAPBOX_WALK_STYLE in Render can point it at a different published style.
_DEFAULT_WALK_STYLE = "mapbox://styles/dmans27/cmk8opaya000g01s05w2006oc"
_walk_style = (os.environ.get("MAPBOX_WALK_STYLE") or "").strip()
# Studio's Share panel also offers https:// links (".../styles/v1/<user>/<style id>.html?..." or
# ".../styles/v1/<user>/<style id>"): accept those too, as the mapbox://styles/<user>/<style id> they point at.
# (and the Studio editor's own address, studio.mapbox.com/styles/<user>/<style id>/edit).
_m = re.match(r"^https://(?:api\.mapbox\.com/styles/v1|studio\.mapbox\.com/styles)/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", _walk_style)
if _m:
    _walk_style = f"mapbox://styles/{_m.group(1)}/{_m.group(2)}"
if _walk_style and not re.match(r"^mapbox://styles/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$", _walk_style):
    print(f"[maps] MAPBOX_WALK_STYLE isn't a style URL ({_walk_style[:80]!r}); using the default walking style", flush=True)
    _walk_style = ""
print(f"[maps] walking map style: {_walk_style or _DEFAULT_WALK_STYLE}", flush=True)
STYLES = {
    "light": "mapbox://styles/mapbox/light-v11",
    "dark": "mapbox://styles/mapbox/dark-v11",
    "streets": "mapbox://styles/mapbox/streets-v12",
    "mansfield": _walk_style if re.match(r"^mapbox://styles/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$", _walk_style) else _DEFAULT_WALK_STYLE,
}

MAX_PLACES = 300  # plenty for one map, and keeps the page light

# Only plain "https://host" style entries are accepted, so a typo (or a
# stray ";") in the setting can never inject anything into the header.
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d+)?$")


def allowed_origins(app):
    raw = os.environ.get("EMBED_ALLOWED_ORIGINS", "").strip() or DEFAULT_ALLOWED_ORIGINS
    origins = [o.rstrip("/") for o in raw.replace(",", " ").split()]
    origins = [o for o in origins if _ORIGIN.match(o)]
    # While developing on your own computer, a locally running media site
    # may show the maps too (EMBED_ALLOW_LOCALHOST=1, or Flask debug mode).
    if app.debug or os.environ.get("EMBED_ALLOW_LOCALHOST"):
        origins += ["http://localhost:*", "http://127.0.0.1:*"]
    return origins


def _safe_http(url):
    """Only real web links go on a pin -- never a javascript: address."""
    url = (url or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


_LISTING_COLS = ("slug, COALESCE(NULLIF(photo_url, ''), NULLIF(card_image_url, '')) AS photo_url, "
                 "photo_urls_json, google_rating, google_rating_count")

MAX_PHOTOS = 6  # pictures per place in the pop-up carousel


def _listing_info(place, query_one, cols=None):
    """The directory listing that matches a saved place (same lookup order
    as the public map page: listing id, Google place id, then name).
    Returns the listing's slug, photo and Google rating, or None."""
    cols = cols or _LISTING_COLS
    try:
        row = None
        if getattr(place, "listing_id", None):
            row = query_one(f"SELECT {cols} FROM listings WHERE id = :id LIMIT 1",
                            {"id": place.listing_id})
        if not row and getattr(place, "place_id", None):
            row = query_one(f"SELECT {cols} FROM listings WHERE place_id = :place_id LIMIT 1",
                            {"place_id": place.place_id})
        if not row and place.name:
            row = query_one(f"SELECT {cols} FROM listings WHERE LOWER(name) = LOWER(:name) LIMIT 1",
                            {"name": place.name})
        return row
    except Exception:
        return None  # a missing listing just means no link, photo or rating


def _stored_photos(own_photo, listing):
    """Every stored photo link for a place, main picture first: the place's
    own photo, then the directory listing's photo and gallery. Links only
    (never shown to visitors -- they go through /embed/photo)."""
    found = [own_photo or "", listing["photo_url"] if listing else ""]
    raw = listing.get("photo_urls_json") if listing else None
    if raw:
        try:
            gallery = json.loads(raw) or []
        except (TypeError, ValueError):
            gallery = []
        found += [g for g in gallery if isinstance(g, str)]
    photos = []
    for url in found:
        url = _safe_http(url)
        if url and url not in photos:
            photos.append(url)
    return photos[:MAX_PHOTOS]


def _rating(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1) if number > 0 else None


def _count(value):
    """A review count as a whole number, or None."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _places_json(places, query_one):
    out = []
    for place in places:
        if place.latitude is None or place.longitude is None:
            continue
        listing = _listing_info(place, query_one)
        stored_photos = _stored_photos(place.photo_url, listing)
        out.append({
            "id": place.id,
            "name": place.name or "",
            "lat": float(place.latitude),
            "lng": float(place.longitude),
            "category": place.category or "",
            "address": place.address or "",
            "notes": place.notes or "",
            "website": _safe_http(place.website),
            "listing": listing["slug"] if listing else None,
            "rating": _rating(listing["google_rating"]) if listing else None,
            "ratingCount": _count(listing.get("google_rating_count")) if listing else None,
            # Never the stored links themselves: they can contain an API key.
            "photo": url_for("embed_place_photo", saved_place_id=place.id) if stored_photos else None,
            "photos": [url_for("embed_place_photo", saved_place_id=place.id, i=n)
                       for n in range(len(stored_photos))],
        })
        if len(out) >= MAX_PLACES:
            break
    return out


# ---- Card pictures ----------------------------------------------------------

_PHOTO_FETCH_HOSTS = {"maps.googleapis.com", "places.googleapis.com"}
_PHOTO_IMAGE_SUFFIXES = (".googleusercontent.com", ".ggpht.com")
_PHOTO_TTL = 6 * 3600       # remember a resolved picture for 6 hours
_MISS_TTL = 10 * 60         # remember "no picture" for 10 minutes
_PHOTO_CACHE_MAX = 1000
_photo_cache = {}           # (saved place id, picture number) -> (expires, image url or None)
_CARD_PHOTO_WIDTH = "500"   # cards are small, no need for the full-size photo


def _is_image_host(host):
    host = (host or "").lower()
    return host.endswith(_PHOTO_IMAGE_SUFFIXES)


def _public_photo_url(stored):
    """Turn a stored photo link into an image address that is safe to show
    publicly (no API key). Returns None if that isn't possible."""
    parts = urlparse(stored)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return None
    if _is_image_host(host):
        return stored if "key=" not in parts.query.lower() else None
    if host not in _PHOTO_FETCH_HOSTS:
        return None  # only ever call Google, never an address someone typed in
    # Ask Google where the picture lives; it answers with a redirect to the
    # key-free image address, and we hand that to the browser instead.
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in ("maxwidth", "maxwidthpx"):
            value = _CARD_PHOTO_WIDTH
        query.append((key, value))
    try:
        reply = requests.get(urlunparse(parts._replace(query=urlencode(query))),
                             allow_redirects=False, timeout=6)
    except requests.RequestException:
        return None
    location = reply.headers.get("Location", "")
    target = urlparse(location)
    if (reply.status_code in (301, 302, 303, 307, 308) and target.scheme == "https"
            and _is_image_host(target.hostname) and "key=" not in target.query.lower()):
        return location
    return None


# ---- Full-size "Show listing" panel ----------------------------------------

_DETAIL_COLS = ("id AS listing_row_id, slug, phone, website, address, description, "
                "google_rating, google_rating_count, place_id AS listing_place_id, my_rating, my_take, my_tags")
_GOOGLE_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_GOOGLE_FIELDS = ",".join([
    "formattedAddress", "internationalPhoneNumber", "websiteUri", "googleMapsUri",
    "rating", "userRatingCount", "editorialSummary", "utcOffsetMinutes", "regularOpeningHours",
    "servesBreakfast", "servesBrunch", "servesLunch", "servesDinner", "servesDessert",
    "servesCoffee", "servesCocktails", "servesBeer", "servesWine", "servesVegetarianFood",
    "dineIn", "takeout", "delivery", "outdoorSeating", "reservable",
])
_GOOGLE_TAGS = [
    ("servesCoffee", "Coffee"), ("servesBreakfast", "Breakfast"), ("servesBrunch", "Brunch"),
    ("servesLunch", "Lunch"), ("servesDinner", "Dinner"), ("servesDessert", "Dessert"),
    ("servesCocktails", "Cocktails"), ("servesBeer", "Beer"), ("servesWine", "Wine"),
    ("servesVegetarianFood", "Vegetarian options"), ("dineIn", "Dine-in"), ("takeout", "Takeaway"),
    ("delivery", "Delivery"), ("outdoorSeating", "Outdoor seating"), ("reservable", "Takes reservations"),
]
_DETAIL_TTL = 24 * 3600     # opening hours and tags rarely change
_DETAIL_MISS_TTL = 10 * 60
_DETAIL_CACHE_MAX = 500
_detail_cache = {}          # Google place id -> (expires, Google's answer or None)


def _google_details(place_id):
    """Live details for one place from Google Places (New), cached for a day.
    Returns Google's JSON, or None (no key, no id, or Google said no)."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key or not place_id or not _GOOGLE_PLACE_ID.match(place_id):
        return None
    now = time.time()
    hit = _detail_cache.get(place_id)
    if hit and hit[0] > now:
        return hit[1]
    data = None
    try:
        reply = requests.get(
            f"https://places.googleapis.com/v1/places/{place_id}",
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": _GOOGLE_FIELDS},
            params={"languageCode": "en"}, timeout=6)
        if reply.status_code == 200:
            data = reply.json()
    except (requests.RequestException, ValueError):
        data = None
    if len(_detail_cache) >= _DETAIL_CACHE_MAX:
        _detail_cache.clear()
    _detail_cache[place_id] = (now + (_DETAIL_TTL if data else _DETAIL_MISS_TTL), data)
    return data


def _open_now(periods, offset_minutes, now=None):
    """Is the place open right now? Worked out here from the weekly opening
    periods (so the cached answer never goes stale). None if unknown."""
    if not periods or offset_minutes is None:
        return None
    local = (now or datetime.now(timezone.utc)) + timedelta(minutes=offset_minutes)
    current = ((local.weekday() + 1) % 7) * 1440 + local.hour * 60 + local.minute  # minutes since Sunday 00:00
    for period in periods:
        opens, closes = period.get("open"), period.get("close")
        if not opens:
            continue
        start = opens.get("day", 0) * 1440 + opens.get("hour", 0) * 60 + opens.get("minute", 0)
        if not closes:
            return True  # open around the clock
        end = closes.get("day", 0) * 1440 + closes.get("hour", 0) * 60 + closes.get("minute", 0)
        if (start <= current < end) if start <= end else (current >= start or current < end):
            return True
    return False


def _place_detail(row, query_one, now=None):
    """Everything the full-size panel shows, merged from your directory data
    and live Google details. `row` is the saved place."""
    place = type("P", (), dict(row))()
    listing = _listing_info(place, query_one, _DETAIL_COLS)
    listing = listing or {}
    google = _google_details(row.get("place_id") or listing.get("listing_place_id")) or {}

    hours = (google.get("regularOpeningHours") or {})
    lines = hours.get("weekdayDescriptions") or []
    offset = google.get("utcOffsetMinutes")
    local = (now or datetime.now(timezone.utc)) + timedelta(minutes=offset or 0)
    # Find today's line by weekday name instead of trusting the list order.
    today_index = None
    if offset is not None and lines:
        for index, line in enumerate(lines):
            if line.lower().startswith(local.strftime("%A").lower()):
                today_index = index
                break
    today_line = lines[today_index] if today_index is not None else None

    reviews = []
    if listing.get("listing_row_id"):
        try:
            for r in _site_reviews(query_one, listing["listing_row_id"]):
                reviews.append({"author": r["author_name"], "rating": r["rating"], "body": r["body"]})
        except Exception:
            reviews = []

    rating = google.get("rating") or listing.get("google_rating")
    return {
        "address": listing.get("address") or google.get("formattedAddress") or row.get("address") or "",
        "phone": listing.get("phone") or google.get("internationalPhoneNumber") or "",
        "website": _safe_http(listing.get("website") or google.get("websiteUri") or row.get("website")),
        "mapsUri": _safe_http(google.get("googleMapsUri")),
        "description": (listing.get("description") or (google.get("editorialSummary") or {}).get("text") or "")[:600],
        "rating": _rating(rating),
        "ratingCount": google.get("userRatingCount") or listing.get("google_rating_count") or None,
        "openNow": _open_now(hours.get("periods"), offset, now),
        "todayHours": today_line,
        "hours": lines if len(lines) == 7 else None,
        "todayIndex": today_index,
        "tags": [label for key, label in _GOOGLE_TAGS if google.get(key) is True],
        "reviews": reviews,
        "live": bool(google),
        # The owner's own words (from "Add my place"), shown above everything Google says.
        "curator": (os.environ.get("CURATOR_NAME") or "Danny").strip()[:40],
        "myRating": _rating(listing.get("my_rating")),
        "myTake": (listing.get("my_take") or "").strip()[:600],
        "myTags": [t.strip() for t in (listing.get("my_tags") or "").split(",") if t.strip()][:10],
    }


def _site_reviews(query_one, listing_id):
    """Up to three approved reviews people left on your site for a listing
    (newest first). query_one returns a single row, so ask three times."""
    found = []
    for offset in range(3):
        row = query_one(
            "SELECT author_name, rating, body FROM listing_comments "
            "WHERE listing_id = :id AND is_approved = 1 ORDER BY id DESC LIMIT 1 OFFSET :off",
            {"id": listing_id, "off": offset})
        if not row:
            break
        found.append(row)
    return found


# ---- One listing as a card (/embed/card/<slug>) -----------------------------

CARD_STYLES = ("photo", "wide", "full", "strip")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_CARD_COLS = ("id, slug, name, category, address, website, latitude, longitude, place_id, "
              "COALESCE(NULLIF(photo_url, ''), NULLIF(card_image_url, '')) AS photo_url, "
              "photo_urls_json, google_rating, google_rating_count")


def _card_listing(query_one, slug):
    """A published directory listing by slug, or None. Drafts are never served."""
    if not _SLUG.match(slug or ""):
        return None
    try:
        return query_one(f"SELECT {_CARD_COLS} FROM listings WHERE slug = :slug AND status = 'published' LIMIT 1",
                         {"slug": slug})
    except Exception:
        return None


BOOK_LABEL_MAX = 30
VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v")
MAX_STACK = 8


def _clean_book(url, label):
    """The writer's booking link and button label, or None. Web links only."""
    url = _safe_http(url)
    if not url or len(url) > 600:
        return None
    label = " ".join(str(label or "").split())[:BOOK_LABEL_MAX] or "Book"
    return {"url": url, "label": label}


def _clean_video(app, url):
    """A video link, but only a plain video file on a site that is allowed to
    embed these cards (EMBED_ALLOWED_ORIGINS) -- so a card can never be made to
    load video from somewhere else."""
    url = (url or "").strip()
    if not url or len(url) > 600:
        return None
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username:
        return None
    if not parts.path.lower().endswith(VIDEO_SUFFIXES):
        return None
    origin = f"{parts.scheme}://{parts.netloc.lower()}"
    for allowed in allowed_origins(app):
        if allowed.endswith(":*"):       # "http://localhost:*" (development only)
            if parts.scheme + "://" + (parts.hostname or "") == allowed[:-2]:
                return url
        elif origin == allowed.lower():
            return url
    return None


def _card_payload(listing):
    """What the card page needs about one listing. Same shape as a map pin, with
    an id of "L<listing id>" (so a saved card can never be mistaken for a saved
    pin that happens to have the same number) and its own picture/detail links."""
    photos = _stored_photos("", listing)
    slug = listing["slug"]
    lat, lng = listing.get("latitude"), listing.get("longitude")
    return {
        "id": f"L{listing['id']}",
        "name": listing.get("name") or "",
        "lat": float(lat) if lat is not None else None,
        "lng": float(lng) if lng is not None else None,
        "category": listing.get("category") or "",
        "address": listing.get("address") or "",
        "notes": "",
        "website": _safe_http(listing.get("website")),
        "listing": slug,
        "rating": _rating(listing.get("google_rating")),
        "ratingCount": _count(listing.get("google_rating_count")),
        "photo": url_for("embed_listing_photo", slug=slug) if photos else None,
        "photos": [url_for("embed_listing_photo", slug=slug, i=n) for n in range(len(photos))],
        "detailUrl": url_for("embed_card_detail", slug=slug),
        "book": None,
        "video": None,
    }



# ---- City maps ---------------------------------------------------------------
# Every place already carries a city (its own, or the one on its directory
# listing). A "city map" is simply the public places in one city: the embed
# link takes ?city=Vienna, and the map opens already fitted to those places.

MAX_CITIES = 200
CITY_CARDS_MIN_ZOOM = 7  # a city map shows its cards as soon as it opens


def _clean_city(value):
    return " ".join(str(value or "").split())[:120]


def _city_key(value):
    return _clean_city(value).casefold()


def _city_of(place, query_one):
    """The city of a saved place: its own, else its directory listing's."""
    own = _clean_city(getattr(place, "city", None))
    if own:
        return own
    row = _listing_info(place, query_one, cols="city")
    return _clean_city(row["city"]) if row and row.get("city") else ""


def _pick_label(names):
    """Several spellings of one city ("vienna", "Vienna"): show the most common."""
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts, key=lambda n: (-counts[n], n != n.title(), n))[0]


def _city_maps_for(SavedList, user, query_one):
    """The signed-in person's cities: [{city, public, private}] -- how many
    pinned places each has on public lists (embeddable) and on private ones."""
    lists = SavedList.query.filter_by(user_id=user.id).order_by(SavedList.id).all()
    found = {}
    for lst in lists:
        for place in lst.places:
            if place.latitude is None or place.longitude is None:
                continue
            city = _city_of(place, query_one)
            if not city:
                continue
            entry = found.setdefault(_city_key(city), {"names": [], "public": 0, "private": 0})
            entry["names"].append(city)
            entry["public" if lst.is_public else "private"] += 1
    out = [{"city": _pick_label(e["names"]), "public": e["public"], "private": e["private"]}
           for e in found.values()]
    out.sort(key=lambda e: (-(e["public"] + e["private"]), e["city"].casefold()))
    return out[:MAX_CITIES]



# ---- Custom maps -------------------------------------------------------------
# A custom map is a name plus a hand-picked set of the person's saved places
# ("Vienna coffee weekend"). Two small tables hold them. They are created here,
# the first time the app starts with this code (CREATE TABLE only if missing).

_maps_meta = MetaData()
_custom_maps = Table(
    "custom_maps", _maps_meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("name", String(80), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)
_custom_map_places = Table(
    "custom_map_places", _maps_meta,
    Column("map_id", Integer, nullable=False),
    Column("saved_place_id", Integer, nullable=False),
    PrimaryKeyConstraint("map_id", "saved_place_id"),
)

# Walking paths (see "Walking paths" below): one row per route, so each route
# only ever costs one Mapbox request, also across restarts and deploys.
_walk_cache = Table(
    "walk_route_cache", _maps_meta,
    Column("key", String(64), primary_key=True),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)

MAX_CUSTOM_MAPS = 50
MAP_NAME_MAX = 80
_tables_ready = False


def _engine(app):
    return app.extensions["sqlalchemy"].engine


def _ensure_tables(app):
    """Creates the two tables if they are missing. Never raises: a database
    hiccup at start-up must not stop the whole app from booting."""
    global _tables_ready
    if _tables_ready:
        return True
    try:
        with app.app_context():
            _maps_meta.create_all(_engine(app), checkfirst=True)
        _tables_ready = True
    except Exception as exc:  # noqa: BLE001
        print(f"[custom maps] could not create tables: {exc}", flush=True)
    return _tables_ready


def _clean_map_name(value):
    return " ".join(str(value or "").split())[:MAP_NAME_MAX]


def _my_places(SavedList, user, query_one):
    """{saved place id: (place, list, city)} for every pinned place the person has saved."""
    out = {}
    lists = SavedList.query.filter_by(user_id=user.id).order_by(SavedList.id).all()
    for lst in lists:
        for place in lst.places:
            if place.latitude is None or place.longitude is None:
                continue
            out[place.id] = (place, lst, _city_of(place, query_one))
    return out


def _custom_maps_for(app, SavedList, user):
    """The person's custom maps: [{id, name, public, private}], newest first."""
    if not _ensure_tables(app):
        return []
    with _engine(app).connect() as conn:
        maps = conn.execute(select(_custom_maps).where(_custom_maps.c.user_id == user.id)
                            .order_by(_custom_maps.c.id.desc())).mappings().all()
        links = {}
        if maps:
            rows = conn.execute(select(_custom_map_places).where(
                _custom_map_places.c.map_id.in_([m["id"] for m in maps]))).mappings().all()
            for row in rows:
                links.setdefault(row["map_id"], []).append(row["saved_place_id"])
    visibility = {}
    for lst in SavedList.query.filter_by(user_id=user.id).all():
        for place in lst.places:
            if place.latitude is not None and place.longitude is not None:
                visibility[place.id] = bool(lst.is_public)
    out = []
    for m in maps:
        ids = [i for i in links.get(m["id"], []) if i in visibility]
        out.append({"id": m["id"], "name": m["name"],
                    "public": sum(1 for i in ids if visibility[i]),
                    "private": sum(1 for i in ids if not visibility[i])})
    return out


# ---- Walking paths ------------------------------------------------------------
# The real path along the streets between a route's stops, with distances,
# times and turn instructions, from the Mapbox Directions API ("walking"
# profile: prefers sidewalks and footpaths). Asked for on the server, so the
# token stays here (MAPBOX_SECRET_TOKEN in Render; falls back to MAPBOX_TOKEN).
#
#   GET /embed/walk?c=lng,lat;lng,lat;...          the route's stops, in order
#   GET /embed/walk?c=lng,lat;...&from=me          the first point is the walker
#        (re-routing during "Start walk"): never stored, never logged, and it
#        must be within WALK_FROM_MAX_M of the next stop.
#
# Every stop must be a pinned place in this directory (so the endpoint can't be
# used as a free directions service for anywhere), answers are kept in the
# walk_route_cache table, and new Mapbox requests are rate-limited per visitor.

WALK_URL = "https://api.mapbox.com/directions/v5/mapbox/walking/"
WALK_MAX_STOPS = 25            # Mapbox's limit for one request
WALK_MAX_SPAN_M = 60000        # a walk, not a road trip
WALK_FROM_MAX_M = 5000         # re-routing: the walker must be near the next stop
WALK_CACHE_DAYS = 60           # streets change slowly
WALK_LIMIT = 30                # new Mapbox requests per visitor per window
WALK_WINDOW = 600              # seconds
_walk_hits = {}                # ip -> [timestamps]
_walk_lock = threading.Lock()


def _metres(a, b):
    r, to_r = 6371000.0, math.pi / 180
    d_lat, d_lng = (b[1] - a[1]) * to_r, (b[0] - a[0]) * to_r
    h = math.sin(d_lat / 2) ** 2 + math.cos(a[1] * to_r) * math.cos(b[1] * to_r) * math.sin(d_lng / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _parse_walk_points(raw):
    """'lng,lat;lng,lat;...' -> [(lng, lat), ...] rounded to ~1 m, or None."""
    parts = str(raw or "").split(";")
    if not (2 <= len(parts) <= WALK_MAX_STOPS):
        return None
    points = []
    for part in parts:
        try:
            lng, lat = (float(x) for x in part.split(","))
        except (TypeError, ValueError):
            return None
        if not (-180 <= lng <= 180 and -90 <= lat <= 90) or math.isnan(lng) or math.isnan(lat):
            return None
        points.append((round(lng, 5), round(lat, 5)))
    for a, b in zip(points, points[1:]):
        if _metres(a, b) > WALK_MAX_SPAN_M:
            return None
    return points


def _is_pinned_place(query_one, lng, lat):
    """True if a saved place or a published directory listing sits at this
    point (within ~2 m)."""
    d = 0.00002
    box = {"la": lat - d, "lb": lat + d, "na": lng - d, "nb": lng + d}
    row = query_one(
        "SELECT 1 AS x FROM saved_place WHERE latitude BETWEEN :la AND :lb "
        "AND longitude BETWEEN :na AND :nb LIMIT 1", box)
    if not row:
        row = query_one(
            "SELECT 1 AS x FROM listings WHERE status = 'published' AND latitude BETWEEN :la AND :lb "
            "AND longitude BETWEEN :na AND :nb LIMIT 1", box)
    return bool(row)


def _walk_rate_ok(ip):
    now = time.time()
    with _walk_lock:
        if len(_walk_hits) > 5000:
            _walk_hits.clear()
        hits = [t for t in _walk_hits.get(ip, []) if t > now - WALK_WINDOW]
        if len(hits) >= WALK_LIMIT:
            _walk_hits[ip] = hits
            return False
        hits.append(now)
        _walk_hits[ip] = hits
        return True


def _walk_token():
    return (os.environ.get("MAPBOX_SECRET_TOKEN") or os.environ.get("MAPBOX_TOKEN") or "").strip()


def _shape_walk(data):
    """Keep only what the map needs from a Directions reply."""
    route = (data.get("routes") or [None])[0]
    if not route or not isinstance(route.get("geometry"), dict):
        return None
    coords = [[round(float(x), 6), round(float(y), 6)] for x, y in route["geometry"].get("coordinates", [])[:6000]]
    if len(coords) < 2:
        return None
    legs = []
    for leg in route.get("legs") or []:
        steps = []
        for st in leg.get("steps") or []:
            man = st.get("maneuver") or {}
            loc = man.get("location") or [None, None]
            steps.append({
                "text": str(man.get("instruction") or "")[:200],
                "type": str(man.get("type") or "")[:30],
                "modifier": str(man.get("modifier") or "")[:30],
                "distance": round(float(st.get("distance") or 0), 1),
                "at": [round(float(loc[0]), 6), round(float(loc[1]), 6)] if loc[0] is not None else None,
            })
        legs.append({"distance": round(float(leg.get("distance") or 0), 1),
                     "duration": round(float(leg.get("duration") or 0), 1),
                     "steps": steps})
    return {"geometry": {"type": "LineString", "coordinates": coords},
            "distance": round(float(route.get("distance") or 0), 1),
            "duration": round(float(route.get("duration") or 0), 1),
            "legs": legs}


def _fetch_walk(points):
    token = _walk_token()
    if not token:
        return None, "no_token"
    coords = ";".join(f"{lng},{lat}" for lng, lat in points)
    try:
        r = requests.get(WALK_URL + coords, params={
            "geometries": "geojson", "overview": "full", "steps": "true",
            "access_token": token}, timeout=12)
    except requests.RequestException:
        return None, "unreachable"
    if r.status_code != 200:
        print(f"[walk] mapbox answered {r.status_code}", flush=True)
        return None, "mapbox_error"
    try:
        shaped = _shape_walk(r.json())
    except (ValueError, TypeError, KeyError):
        shaped = None
    return (shaped, None) if shaped else (None, "no_route")


# ---- AI walks ----------------------------------------------------------------
# The media site's "Plan a walk": a reader asks (typed or spoken) for something
# like "3 highly rated coffee shops near me"; the media site's server works out
# what they mean, asks /embed/places-near for matching listings from this
# directory, picks and orders the stops, then shows /embed/route-map?s=... with
# its walking route. Only the media site's server may search (it sends the shared
# secret WALK_PLAN_KEY, set to the same value in both Render services).

# The same groups as the map's filter bar (templates/embed_map.html groupOf).
PLACE_GROUPS = ("food", "coffee", "bars", "wine", "rooftops", "shops", "hotels", "sights", "other")
_HOTEL = re.compile(r"hotel|hostel|lodging|guest ?house|apartment|resort|\binn\b")
_SIGHT = re.compile(r"museum|gallery|sight|attraction|landmark|monument|palace|church|cathedral|castle|park|tourist")


def place_group(category):
    c = (category or "").lower()
    if "coffee" in c or "cafe" in c or "café" in c:
        return "coffee"
    if "bar" in c or "cocktail" in c:
        return "bars"
    if "wine" in c:
        return "wine"
    if "rooftop" in c:
        return "rooftops"
    if _HOTEL.search(c):
        return "hotels"
    if _SIGHT.search(c):
        return "sights"
    if "shop" in c:
        return "shops"
    if "restaurant" in c or "food" in c:
        return "food"
    return "other"


NEAR_MAX_RADIUS = 20000      # metres
EXPLORE_DEFAULT = (16.3713, 48.2082)   # lng, lat: Vienna, Stephansplatz
NEAR_MAX_RESULTS = 60
_geo_cache = {}              # text -> (expires, (lng, lat) or None)


# ── Google fallback: places the directory doesn't have yet ──────────────────
# The walk planner and the route builder on the media site search Google
# (Places API (New), GOOGLE_MAPS_API_KEY) when the directory has too few places
# somewhere -- so walkers can use the app in any city. A place only becomes a
# listing (source 'google', published) when a walker actually picks it for a
# walk or a route (/embed/places-import). GOOGLE_DAILY_CAP (default 800) caps
# the Google calls a day, so a bug or abuse can't run up the bill.
GOOGLE_PLACES_BASE = "https://places.googleapis.com/v1"
_G_BASIC = ("id", "displayName", "formattedAddress", "location", "primaryType", "primaryTypeDisplayName",
            "types", "addressComponents", "businessStatus")
_G_RICH = _G_BASIC + ("rating", "userRatingCount", "nationalPhoneNumber", "websiteUri")
_GOOGLE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
GOOGLE_SEEN_SECONDS = 20 * 60
_google_seen = {}                       # place id -> (time, place): a search result, so adding it costs no second call
_google_calls = {"day": "", "n": 0}
_google_lock = threading.Lock()


def _google_key():
    return (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


def _google_base():
    return (os.environ.get("GOOGLE_PLACES_BASE") or GOOGLE_PLACES_BASE).rstrip("/")


def _google_budget_ok():
    try:
        cap = int(os.environ.get("GOOGLE_DAILY_CAP") or 800)
    except ValueError:
        cap = 800
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with _google_lock:
        if _google_calls["day"] != today:
            _google_calls.update(day=today, n=0)
        if _google_calls["n"] >= cap:
            return False
        _google_calls["n"] += 1
        return True


def _google_shape(p):
    """A Google place -> the directory's place fields (None for closed places or ones without a spot)."""
    from my_place import _address_part, _category
    loc = p.get("location") or {}
    if not p.get("id") or loc.get("latitude") is None or loc.get("longitude") is None:
        return None
    if p.get("businessStatus") not in (None, "OPERATIONAL"):
        return None
    category = _category(p)
    return {
        "placeId": p["id"], "name": ((p.get("displayName") or {}).get("text") or "")[:160],
        "category": category, "group": place_group(category),
        "address": (p.get("formattedAddress") or "")[:300],
        "city": (_address_part(p, "locality") or _address_part(p, "postal_town"))[:80],
        "state": _address_part(p, "administrative_area_level_1", short=True)[:40],
        "lat": float(loc["latitude"]), "lng": float(loc["longitude"]),
        "rating": _rating(p.get("rating")), "ratingCount": _count(p.get("userRatingCount")),
        "phone": (p.get("nationalPhoneNumber") or "")[:40], "website": (p.get("websiteUri") or "")[:400],
    }


def _google_remember(place, rich):
    now = time.time()
    with _google_lock:
        if len(_google_seen) > 3000:
            for k in [k for k, (t, _p, _r) in _google_seen.items() if now - t > GOOGLE_SEEN_SECONDS]:
                _google_seen.pop(k, None)
        _google_seen[place["placeId"]] = (now, place, rich)


def google_text_search(text, lat=None, lng=None, radius=20000, limit=20, rich=False):
    """Places for a text search near a point, or None when Google can't be used (no key, over the cap, an error)."""
    key = _google_key()
    if not key or not text or not _google_budget_ok():
        return None
    body = {"textQuery": text[:200], "pageSize": max(1, min(20, int(limit))), "languageCode": "en"}
    if lat is not None and lng is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lng},
                                           "radius": float(max(100, min(50000, radius)))}}
    fields = ",".join("places." + f for f in (_G_RICH if rich else _G_BASIC))
    try:
        r = requests.post(_google_base() + "/places:searchText", json=body, timeout=10,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields})
    except requests.RequestException as exc:
        print(f"[google] search failed: {exc}", flush=True)
        return None
    if r.status_code != 200:
        print(f"[google] search said {r.status_code}: {r.text[:300]}", flush=True)
        return None
    out = []
    for p in (r.json() or {}).get("places") or []:
        place = _google_shape(p)
        if place:
            _google_remember(place, rich)
            out.append(place)
    return out


def google_place(place_id):
    """One place by its Google id: from a recent search if we have it, else Google's Place Details."""
    with _google_lock:
        seen = _google_seen.get(place_id)
    if seen and time.time() - seen[0] < GOOGLE_SEEN_SECONDS:
        return seen[1]
    key = _google_key()
    if not key or not _google_budget_ok():
        return None
    try:
        r = requests.get(_google_base() + "/places/" + place_id, timeout=10,
                         headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": ",".join(_G_RICH)})
    except requests.RequestException as exc:
        print(f"[google] details failed: {exc}", flush=True)
        return None
    if r.status_code != 200:
        print(f"[google] details said {r.status_code}: {r.text[:300]}", flush=True)
        return None
    place = _google_shape(r.json() or {})
    if place:
        _google_remember(place, True)
    return place


def _google_slug(conn, name, city, address):
    def slugify(v):   # "Café Sperl" -> "cafe-sperl", "Devoción" -> "devocion"
        import unicodedata
        v = unicodedata.normalize("NFKD", (v or "").replace("ß", "ss")).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", v.strip().lower())).strip("-")
    street = slugify((address or "").split(",")[0])[:40]
    for cand in (slugify(name)[:80], slugify(f"{name}-{city}")[:100], slugify(f"{name}-{city}-{street}")[:140]):
        if cand and not conn.execute(sql_text("SELECT 1 FROM listings WHERE slug = :s"), {"s": cand}).first():
            return cand
    return (slugify(name)[:60] or "place") + "-" + hashlib.sha1(os.urandom(8)).hexdigest()[:6]


def _walk_key_ok():
    want = (os.environ.get("WALK_PLAN_KEY") or "").strip()
    got = (request.headers.get("X-Walk-Key") or "").strip()
    return bool(want) and len(got) == len(want) and hashlib.sha256(got.encode()).digest() == hashlib.sha256(want.encode()).digest()


def _geocode(text, near=None):
    """A named place ("Naschmarkt, Vienna") -> (lng, lat), from Mapbox, remembered for a day."""
    text = " ".join(str(text or "").split())[:120]
    token = (os.environ.get("MAPBOX_SECRET_TOKEN") or os.environ.get("MAPBOX_TOKEN") or "").strip()
    if not text or not token:
        return None
    key = (text.lower(), near)
    hit = _geo_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    params = {"limit": 1, "access_token": token}
    if near:
        params["proximity"] = f"{near[0]},{near[1]}"
    found = None
    try:
        r = requests.get("https://api.mapbox.com/geocoding/v5/mapbox.places/"
                         + requests.utils.quote(text, safe="") + ".json", params=params, timeout=8)
        if r.status_code != 200:
            print(f"[geocode] Mapbox said {r.status_code} for {text!r}", flush=True)
        if r.status_code == 200:
            feats = (r.json() or {}).get("features") or []
            if feats and isinstance(feats[0].get("center"), list):
                found = (float(feats[0]["center"][0]), float(feats[0]["center"][1]))
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"[geocode] failed for {text!r}: {exc}", flush=True)
        found = None
    if found is None:
        print(f"[geocode] no match for {text!r}", flush=True)
    if len(_geo_cache) > 2000:
        _geo_cache.clear()
    _geo_cache[key] = (time.time() + 86400, found)
    return found


_city_cache = {}


def _mapbox_cities(path, params, types="place"):
    """Cities from Mapbox's geocoder (types=place): [{name, label, country, lat, lng, kind}], remembered for a day.
    types="locality,neighborhood,place" with a point gives the district there first (Vienna's Bezirke are
    localities), then the neighbourhood, then the city -- each with the centre Mapbox gives it."""
    token = (os.environ.get("MAPBOX_SECRET_TOKEN") or os.environ.get("MAPBOX_TOKEN") or "").strip()
    if not token:
        return None
    key = (path, types, tuple(sorted(params.items())))
    hit = _city_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    base = (os.environ.get("MAPBOX_GEOCODE_BASE") or "https://api.mapbox.com/geocoding/v5/mapbox.places").rstrip("/")
    try:
        r = requests.get(base + "/" + requests.utils.quote(path, safe=",.-") + ".json",
                         params=dict(params, access_token=token, types=types, language="en"), timeout=8)
        feats = ((r.json() or {}).get("features") or []) if r.status_code == 200 else None
    except (requests.RequestException, ValueError) as exc:
        print(f"[cities] failed for {path!r}: {exc}", flush=True)
        return None
    if feats is None:
        print(f"[cities] Mapbox said {r.status_code} for {path!r}", flush=True)
        return None
    out = []
    for f in feats:
        center = f.get("center")
        if not isinstance(center, list) or len(center) != 2:
            continue
        country = ""
        for c in f.get("context") or []:
            if str(c.get("id", "")).startswith("country."):
                country = c.get("text") or ""
        out.append({"name": (f.get("text") or "")[:80], "label": (f.get("place_name") or f.get("text") or "")[:160],
                    "country": country[:60], "lng": round(float(center[0]), 5), "lat": round(float(center[1]), 5),
                    "kind": str(f.get("id") or "").split(".")[0] or "place"})
    if len(_city_cache) > 3000:
        _city_cache.clear()
    _city_cache[key] = (time.time() + 86400, out)
    return out


def _float_arg(name, lo, hi):
    try:
        v = float(request.args.get(name))
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi and not math.isnan(v) else None


def register_embed_routes(app, SavedList, query_one):
    _ensure_tables(app)

    @app.get("/embed/list/<slug>")
    def embed_list_map(slug):
        saved_list = SavedList.query.filter_by(slug=slug, is_public=True).first_or_404()
        return _render(
            saved_list.title,
            _places_json(saved_list.places, query_one),
            url_for("view_public_list", slug=saved_list.slug),
            request,
        )

    @app.get("/embed/u/<int:user_id>")
    def embed_profile_map(user_id):
        # Only places from this person's PUBLIC lists -- private lists never
        # appear in an embed, whoever has the link.
        lists = (SavedList.query.filter_by(user_id=user_id, is_public=True)
                 .order_by(SavedList.id).all())
        if not lists:
            abort(404)
        places = [p for lst in lists for p in lst.places]
        owner = getattr(lists[0], "user", None)
        name = (getattr(owner, "name", None) or "").strip()
        title = f"{name}’s saved places" if name else "Saved places"
        cards_min_zoom = None

        # ?city=Vienna -> a city map: just that city's places, so the map
        # opens fitted to the city (never the whole world) with cards showing.
        wanted = _city_key(request.args.get("city"))
        if wanted:
            in_city = [(p, _city_of(p, query_one)) for p in places]
            in_city = [(p, c) for p, c in in_city if c and _city_key(c) == wanted]
            if not in_city:
                abort(404)
            places = [p for p, _ in in_city]
            label = _pick_label([c for _, c in in_city])
            title = f"{name}’s {label} places" if name else f"{label} places"
            cards_min_zoom = CITY_CARDS_MIN_ZOOM

        return _render(title, _places_json(places, query_one),
                       url_for("user_public_map", user_id=user_id), request,
                       cards_min_zoom=cards_min_zoom)

    @app.get("/account/city-maps.json")
    def account_city_maps():
        # The "My maps" list on the account page: one map per city, built
        # from the cities on the places this person has saved.
        from flask_login import current_user
        if not current_user.is_authenticated:
            return jsonify({"error": "sign in"}), 401
        maps = _city_maps_for(SavedList, current_user, query_one)
        for entry in maps:
            if entry["public"]:
                entry["url"] = url_for("embed_profile_map", user_id=current_user.id,
                                       city=entry["city"], _external=True)
        custom = _custom_maps_for(app, SavedList, current_user)
        for entry in custom:
            if entry["public"]:
                entry["url"] = url_for("embed_custom_map", map_id=entry["id"], _external=True)
        reply = jsonify({"maps": maps, "custom": custom})
        reply.headers["Cache-Control"] = "no-store"
        return reply

    @app.get("/account/my-places.json")
    def account_my_places():
        # What the "new map" picker offers: every pinned place the person has saved.
        from flask_login import current_user
        if not current_user.is_authenticated:
            return jsonify({"error": "sign in"}), 401
        mine = _my_places(SavedList, current_user, query_one)
        spellings = {}
        for _place, _lst, city in mine.values():
            if city:
                spellings.setdefault(_city_key(city), []).append(city)
        labels = {key: _pick_label(names) for key, names in spellings.items()}   # "vienna" + "Vienna" = one group
        places = [{"id": pid, "name": place.name or "", "city": labels.get(_city_key(city), ""),
                   "category": place.category or "", "list": lst.title or "",
                   "public": bool(lst.is_public)}
                  for pid, (place, lst, city) in mine.items()]
        places.sort(key=lambda p: (p["city"] == "", p["city"].casefold(), p["name"].casefold()))
        reply = jsonify({"places": places[:1000]})
        reply.headers["Cache-Control"] = "no-store"
        return reply

    @app.post("/account/custom-maps")
    def account_custom_map_create():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return jsonify({"error": "Please sign in again."}), 401
        if not request.is_json:
            return jsonify({"error": "Bad request."}), 400
        if not _ensure_tables(app):
            return jsonify({"error": "Maps are not available right now."}), 503
        data = request.get_json(silent=True) or {}
        name = _clean_map_name(data.get("name"))
        if not name:
            return jsonify({"error": "Give the map a name."}), 400
        raw_ids = data.get("place_ids")
        if not isinstance(raw_ids, list):
            return jsonify({"error": "Pick at least one place."}), 400
        wanted = []
        for item in raw_ids[:MAX_PLACES * 2]:
            if isinstance(item, int) and not isinstance(item, bool) and item not in wanted:
                wanted.append(item)
        mine = _my_places(SavedList, current_user, query_one)
        ids = [i for i in wanted if i in mine][:MAX_PLACES]   # only the person's own places
        if not ids:
            return jsonify({"error": "Pick at least one place."}), 400
        with _engine(app).begin() as conn:
            count = conn.execute(select(func.count()).select_from(_custom_maps)
                                 .where(_custom_maps.c.user_id == current_user.id)).scalar()
            if count >= MAX_CUSTOM_MAPS:
                return jsonify({"error": f"You can have up to {MAX_CUSTOM_MAPS} custom maps. Delete one first."}), 400
            result = conn.execute(insert(_custom_maps).values(user_id=current_user.id, name=name))
            map_id = result.inserted_primary_key[0]
            conn.execute(insert(_custom_map_places),
                         [{"map_id": map_id, "saved_place_id": i} for i in ids])
        return jsonify({"ok": True, "id": map_id}), 201

    @app.delete("/account/custom-maps/<int:map_id>")
    def account_custom_map_delete(map_id):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return jsonify({"error": "Please sign in again."}), 401
        if not _ensure_tables(app):
            return jsonify({"error": "Maps are not available right now."}), 503
        with _engine(app).begin() as conn:
            owned = conn.execute(select(_custom_maps.c.id).where(
                _custom_maps.c.id == map_id, _custom_maps.c.user_id == current_user.id)).first()
            if not owned:
                return jsonify({"error": "Map not found."}), 404
            conn.execute(delete(_custom_map_places).where(_custom_map_places.c.map_id == map_id))
            conn.execute(delete(_custom_maps).where(_custom_maps.c.id == map_id))
        return jsonify({"ok": True})

    @app.get("/embed/m/<int:map_id>")
    def embed_custom_map(map_id):
        # A custom map: the hand-picked places, and only those on PUBLIC lists.
        if not _ensure_tables(app):
            abort(404)
        with _engine(app).connect() as conn:
            row = conn.execute(select(_custom_maps).where(_custom_maps.c.id == map_id)).mappings().first()
            if not row:
                abort(404)
            picked = [r[0] for r in conn.execute(
                select(_custom_map_places.c.saved_place_id)
                .where(_custom_map_places.c.map_id == map_id)
                .order_by(_custom_map_places.c.saved_place_id))]
        wanted = set(picked)
        lists = (SavedList.query.filter_by(user_id=row["user_id"], is_public=True)
                 .order_by(SavedList.id).all())
        places = [p for lst in lists for p in lst.places if p.id in wanted]
        if not places:
            abort(404)
        return _render(row["name"], _places_json(places, query_one),
                       url_for("user_public_map", user_id=row["user_id"]), request,
                       cards_min_zoom=CITY_CARDS_MIN_ZOOM)

    @app.get("/embed/photo/<int:saved_place_id>")
    def embed_place_photo(saved_place_id):
        number = request.args.get("i", 0, type=int)  # which of the place's pictures
        if number < 0 or number >= MAX_PHOTOS:
            abort(404)
        now = time.time()
        cache_key = (saved_place_id, number)
        hit = _photo_cache.get(cache_key)
        if hit and hit[0] > now:
            target = hit[1]
        else:
            # Only places that sit in a PUBLIC list are ever served.
            row = query_one(
                "SELECT p.photo_url AS photo_url, p.listing_id AS listing_id, "
                "p.place_id AS place_id, p.name AS name "
                "FROM saved_place p JOIN saved_list l ON l.id = p.saved_list_id "
                "WHERE p.id = :id AND l.is_public LIMIT 1",
                {"id": saved_place_id})
            if not row:
                abort(404)
            listing = _listing_info(type("P", (), dict(row))(), query_one)
            photos = _stored_photos(row["photo_url"], listing)
            target = _public_photo_url(photos[number]) if number < len(photos) else None
            if len(_photo_cache) >= _PHOTO_CACHE_MAX:
                _photo_cache.clear()
            _photo_cache[cache_key] = (now + (_PHOTO_TTL if target else _MISS_TTL), target)
        if not target:
            abort(404)
        reply = redirect(target, code=302)
        reply.headers["Cache-Control"] = "public, max-age=21600"
        return reply

    @app.get("/embed/place/<int:saved_place_id>")
    def embed_place_detail(saved_place_id):
        # Only places that sit in a PUBLIC list are ever served.
        row = query_one(
            "SELECT p.name AS name, p.address AS address, p.website AS website, "
            "p.listing_id AS listing_id, p.place_id AS place_id "
            "FROM saved_place p JOIN saved_list l ON l.id = p.saved_list_id "
            "WHERE p.id = :id AND l.is_public LIMIT 1",
            {"id": saved_place_id})
        if not row:
            abort(404)
        reply = jsonify(_place_detail(dict(row), query_one))
        reply.headers["Cache-Control"] = "public, max-age=60"
        return reply

    @app.get("/embed/card/<slug>")
    def embed_card(slug):
        listing = _card_listing(query_one, slug)
        if not listing or listing.get("latitude") is None or listing.get("longitude") is None:
            abort(404)
        style = request.args.get("style", "photo")
        style = style if style in CARD_STYLES else "photo"
        place = _card_payload(listing)
        place["book"] = _clean_book(request.args.get("b"), request.args.get("l"))
        place["video"] = _clean_video(app, request.args.get("v"))
        return render_template("embed_card.html", payload={"style": style, "place": place},
                               style=style, title=place["name"])

    @app.get("/embed/stack")
    def embed_stack():
        # d = url-safe base64 of a JSON list, one entry per place.
        raw = request.args.get("d", "")
        try:
            items = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            abort(404)
        if not isinstance(items, list):
            abort(404)
        places = []
        for item in items[:MAX_STACK]:
            if not isinstance(item, dict):
                continue
            listing = _card_listing(query_one, str(item.get("s") or ""))
            if not listing or listing.get("latitude") is None or listing.get("longitude") is None:
                continue
            place = _card_payload(listing)
            place["book"] = _clean_book(item.get("b"), item.get("l"))
            place["video"] = _clean_video(app, item.get("v"))
            places.append(place)
        if not places:
            abort(404)
        return render_template("embed_card.html", payload={"style": "stack", "places": places},
                               style="stack", title="Places")

    @app.get("/embed/listing-photo/<slug>")
    def embed_listing_photo(slug):
        number = request.args.get("i", 0, type=int)
        if number < 0 or number >= MAX_PHOTOS:
            abort(404)
        now = time.time()
        cache_key = (f"listing:{slug}", number)
        hit = _photo_cache.get(cache_key)
        if hit and hit[0] > now:
            target = hit[1]
        else:
            listing = _card_listing(query_one, slug)
            if not listing:
                abort(404)
            photos = _stored_photos("", listing)
            target = _public_photo_url(photos[number]) if number < len(photos) else None
            if len(_photo_cache) >= _PHOTO_CACHE_MAX:
                _photo_cache.clear()
            _photo_cache[cache_key] = (now + (_PHOTO_TTL if target else _MISS_TTL), target)
        if not target:
            abort(404)
        reply = redirect(target, code=302)
        reply.headers["Cache-Control"] = "public, max-age=21600"
        return reply

    @app.get("/embed/card-detail/<slug>")
    def embed_card_detail(slug):
        listing = _card_listing(query_one, slug)
        if not listing:
            abort(404)
        row = {"name": listing.get("name"), "address": listing.get("address"),
               "website": listing.get("website"), "listing_id": listing["id"],
               "place_id": listing.get("place_id")}
        reply = jsonify(_place_detail(row, query_one))
        reply.headers["Cache-Control"] = "public, max-age=60"
        return reply

    @app.get("/embed/walk")
    def embed_walk():
        points = _parse_walk_points(request.args.get("c"))
        if not points:
            return jsonify(error="bad_points"), 400
        from_me = request.args.get("from") == "me"
        stops = points[1:] if from_me else points
        if from_me and _metres(points[0], points[1]) > WALK_FROM_MAX_M:
            return jsonify(error="too_far"), 400
        for lng, lat in stops:
            if not _is_pinned_place(query_one, lng, lat):
                return jsonify(error="unknown_stop"), 400

        key = hashlib.sha256(";".join(f"{a},{b}" for a, b in points).encode()).hexdigest()
        if not from_me and _ensure_tables(app):
            try:
                with _engine(app).connect() as conn:
                    row = conn.execute(select(_walk_cache).where(_walk_cache.c.key == key)).mappings().first()
                if row and row["created_at"] and row["created_at"] > datetime.utcnow() - timedelta(days=WALK_CACHE_DAYS):
                    reply = app.response_class(row["body"], mimetype="application/json")
                    reply.headers["Cache-Control"] = "public, max-age=86400"
                    return reply
            except Exception as exc:  # noqa: BLE001
                print(f"[walk] cache read failed: {exc}", flush=True)

        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        if not _walk_rate_ok(ip):
            return jsonify(error="busy"), 429
        shaped, err = _fetch_walk(points)
        if not shaped:
            return jsonify(error=err), 502
        body = json.dumps(shaped, separators=(",", ":"))
        if not from_me and _ensure_tables(app):
            try:
                with _engine(app).begin() as conn:
                    conn.execute(delete(_walk_cache).where(_walk_cache.c.key == key))
                    conn.execute(insert(_walk_cache).values(key=key, body=body, created_at=datetime.utcnow()))
            except Exception as exc:  # noqa: BLE001
                print(f"[walk] cache write failed: {exc}", flush=True)
        reply = app.response_class(body, mimetype="application/json")
        reply.headers["Cache-Control"] = "no-store" if from_me else "public, max-age=86400"
        return reply

    @app.get("/embed/places-near")
    def embed_places_near():
        """Directory listings for an AI walk. ?lat&lng (or ?near=<place name>),
        ?city, ?groups=coffee,food, ?q=words, ?min_rating, ?radius (metres), ?limit."""
        if not _walk_key_ok():
            return jsonify(error="forbidden"), 403
        lat, lng = _float_arg("lat", -90, 90), _float_arg("lng", -180, 180)
        city = " ".join(str(request.args.get("city") or "").split())[:80]
        center = (lng, lat) if lat is not None and lng is not None else None
        near_text = str(request.args.get("near") or "").strip()
        if near_text or city:
            # A named place or city always wins over the reader's own location:
            # someone in Vienna asking for "the Loop, Chicago" wants Chicago. Their
            # location only helps the geocoder pick between same-named places.
            bias, spot = center, None
            if near_text:
                spot = _geocode(near_text + (", " + city if city and city.lower() not in near_text.lower() else ""), bias)
            if not spot and city:
                spot = _geocode(city, bias)
            if spot:
                center = spot
            elif city:
                center = None          # couldn't place it on the map: search the whole city instead
            else:
                return jsonify(error="where", places=[]), 400
        radius = _float_arg("radius", 100, NEAR_MAX_RADIUS) or 2500
        groups = {g for g in str(request.args.get("groups") or "").lower().split(",") if g in PLACE_GROUPS}
        words = [w for w in re.split(r"[^\w']+", str(request.args.get("q") or "").lower()) if len(w) > 2][:8]
        min_rating = _float_arg("min_rating", 0, 5) or 0
        limit = int(_float_arg("limit", 1, NEAR_MAX_RESULTS) or 30)

        sql = ("SELECT id, slug, name, category, city, address, description, latitude, longitude, "
               "google_rating, google_rating_count, my_rating, my_take, my_tags FROM listings WHERE status = 'published' "
               "AND latitude IS NOT NULL AND longitude IS NOT NULL AND slug IS NOT NULL")
        params = {}
        if center:
            dlat = radius / 111320.0
            dlng = radius / (111320.0 * max(0.2, math.cos(math.radians(center[1]))))
            sql += " AND latitude BETWEEN :la AND :lb AND longitude BETWEEN :na AND :nb"
            params.update(la=center[1] - dlat, lb=center[1] + dlat, na=center[0] - dlng, nb=center[0] + dlng)
        elif city:
            sql += " AND LOWER(city) = LOWER(:city)"
            params["city"] = city
        else:
            return jsonify(error="where", places=[]), 400
        sql += " LIMIT 2000"
        try:
            with _engine(app).connect() as conn:
                rows = [dict(r._mapping) for r in conn.execute(sql_text(sql), params)]
        except Exception as exc:  # noqa: BLE001
            print(f"[places-near] query failed: {exc}", flush=True)
            return jsonify(error="unavailable", places=[]), 503

        out = []
        for r in rows:
            group = place_group(r.get("category"))
            if groups and group not in groups:
                continue
            rating = _rating(r.get("google_rating"))
            if min_rating and not (rating and rating >= min_rating):
                continue
            hay = " ".join(str(r.get(k) or "") for k in ("name", "category", "description", "my_take", "my_tags")).lower()
            matched = sum(1 for w in words if w in hay)
            dist = _metres(center, (float(r["longitude"]), float(r["latitude"]))) if center else None
            if dist is not None and dist > radius:
                continue
            out.append({
                "slug": r["slug"], "name": r.get("name") or "", "category": r.get("category") or "",
                "group": group, "lat": float(r["latitude"]), "lng": float(r["longitude"]),
                "rating": rating, "ratingCount": _count(r.get("google_rating_count")),
                "address": r.get("address") or "", "city": r.get("city") or "",
                "about": " ".join(str(r.get("description") or "").split())[:200],
                "myRating": _rating(r.get("my_rating")),
                "myTake": " ".join(str(r.get("my_take") or "").split())[:300],
                "myTags": " ".join(str(r.get("my_tags") or "").split())[:200],
                "matched": matched, "distance": round(dist) if dist is not None else None,
            })
        out.sort(key=lambda p: (-p["matched"], p["distance"] if p["distance"] is not None else 0,
                                -(p["rating"] or 0)))
        reply = jsonify(places=out[:limit], center=[center[0], center[1]] if center else None, total=len(out))
        reply.headers["Cache-Control"] = "no-store"
        return reply

    @app.get("/embed/places-by-slug")
    def embed_places_by_slug():
        """Where these listings are, in this order: ?s=slug,slug (the media site checks a walk a
        reader posts to the community feed, or saves a walk's stop to their places). Walk key only; unknown or
        unpublished slugs are left out. `id` is the listing id (the media site saves it as "<host>:L<id>", like a map pin)."""
        if not _walk_key_ok():
            return jsonify(error="forbidden"), 403
        slugs = [s for s in str(request.args.get("s") or "").split(",") if _SLUG.match(s)][:WALK_MAX_STOPS]
        out, seen = [], set()
        for slug in slugs:
            if slug in seen:
                continue
            seen.add(slug)
            try:
                r = query_one("SELECT id, slug, name, category, address, city, latitude, longitude FROM listings "
                              "WHERE slug = :slug AND status = 'published' LIMIT 1", {"slug": slug})
            except Exception:
                r = None
            if r and r.get("latitude") is not None and r.get("longitude") is not None:
                out.append({"id": r.get("id"), "slug": r["slug"], "name": r.get("name") or "", "category": r.get("category") or "",
                            "address": r.get("address") or "", "city": r.get("city") or "",
                            "lat": float(r["latitude"]), "lng": float(r["longitude"])})
        reply = jsonify(places=out)
        reply.headers["Cache-Control"] = "no-store"
        return reply

    @app.get("/embed/cities")
    def embed_cities():
        """City search for the media site's city feed: ?q=text (up to 5 cities, as you type), or
        ?lat&lng (the city you're in). Walk key only. {cities: [{name, label, country, lat, lng}]}
        ?lat&lng&level=district: the district there instead (Neubau, Wieden...), falling back to the
        neighbourhood or the city; its centre is where the media site puts video pulses."""
        if not _walk_key_ok():
            return jsonify(error="forbidden"), 403
        lat, lng = _float_arg("lat", -90, 90), _float_arg("lng", -180, 180)
        q = " ".join(str(request.args.get("q") or "").split())[:80]
        if lat is not None and lng is not None and request.args.get("level") == "district":
            # A reverse lookup with several types gives one feature per type (no limit allowed): most local first.
            found = _mapbox_cities(f"{lng:.4f},{lat:.4f}", {}, types="locality,neighborhood,place")
            if found:
                rank = {"locality": 0, "neighborhood": 1, "place": 2}
                found = sorted(found, key=lambda c: rank.get(c.get("kind"), 3))[:1]
        elif lat is not None and lng is not None:
            found = _mapbox_cities(f"{lng:.4f},{lat:.4f}", {"limit": 1})
        elif len(q) >= 2:
            found = _mapbox_cities(q, {"limit": 5, "autocomplete": "true"})
        else:
            found = []
        if found is None:
            return jsonify(cities=[], available=False)
        return jsonify(cities=found, available=True)

    @app.get("/embed/google-places")
    def embed_google_places():
        """Google's places for a text search: ?q&lat&lng&radius&limit&rich=1&groups&min_rating&within=1.
        Walk key only. Each place says if it's already a listing (slug). {places, available}."""
        if not _walk_key_ok():
            return jsonify(error="forbidden"), 403
        q = " ".join(str(request.args.get("q") or "").split())[:200]
        if len(q) < 2:
            return jsonify(places=[], available=True)
        lat, lng = _float_arg("lat", -90, 90), _float_arg("lng", -180, 180)
        radius = _float_arg("radius", 100, 50000) or 20000
        limit = int(_float_arg("limit", 1, 20) or 20)
        groups = {g for g in str(request.args.get("groups") or "").lower().split(",") if g in PLACE_GROUPS}
        min_rating = _float_arg("min_rating", 0, 5) or 0
        found = google_text_search(q, lat, lng, radius, limit, rich=request.args.get("rich") == "1")
        if found is None:
            return jsonify(places=[], available=False)
        out = []
        for p in found:
            if groups and p["group"] not in groups:
                continue
            if min_rating and not (p["rating"] and p["rating"] >= min_rating):
                continue
            if lat is not None and lng is not None:
                p = dict(p, distance=round(_metres((lng, lat), (p["lng"], p["lat"]))))
                if request.args.get("within") == "1" and p["distance"] > radius * 1.15:
                    continue
            try:
                row = query_one("SELECT slug, status FROM listings WHERE place_id = :p LIMIT 1", {"p": p["placeId"]})
            except Exception:
                row = None
            if row and row.get("status") != "published":
                continue          # the owner keeps this one off the map
            out.append(dict(p, slug=(row or {}).get("slug") or ""))
        reply = jsonify(places=out, available=True)
        reply.headers["Cache-Control"] = "no-store"
        return reply

    @app.post("/embed/places-import")
    def embed_places_import():
        """Make listings for Google places a walker picked: {placeIds: [...]} -> {places: {placeId: {slug, ...}}}.
        Walk key only. A place that's already a listing just gets its slug back."""
        if not _walk_key_ok():
            return jsonify(error="forbidden"), 403
        ids = []
        for pid in ((request.get_json(silent=True) or {}).get("placeIds") or [])[:15]:
            pid = str(pid or "")
            if _GOOGLE_ID.match(pid) and pid not in ids:
                ids.append(pid)
        out = {}
        for pid in ids:
            try:
                row = query_one("SELECT slug, name, category, address, latitude, longitude, status FROM listings "
                                "WHERE place_id = :p LIMIT 1", {"p": pid})
            except Exception:
                row = None
            if row:
                if row.get("status") == "published":
                    out[pid] = {"slug": row["slug"], "name": row.get("name") or "", "category": row.get("category") or "",
                                "address": row.get("address") or "", "lat": row.get("latitude"), "lng": row.get("longitude")}
                continue
            place = google_place(pid)
            if not place or not place["name"]:
                continue
            try:
                with _engine(app).begin() as conn:
                    slug = _google_slug(conn, place["name"], place["city"], place["address"])
                    conn.execute(sql_text(
                        "INSERT INTO listings (name, slug, category, address, city, state, phone, website, latitude, longitude, "
                        "place_id, google_rating, google_rating_count, status, featured, source, created_at, updated_at) "
                        "VALUES (:name, :slug, :category, :address, :city, :state, :phone, :website, :lat, :lng, "
                        ":pid, :rating, :count, 'published', 0, 'google', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                        {"name": place["name"], "slug": slug, "category": place["category"], "address": place["address"],
                         "city": place["city"], "state": place["state"], "phone": place["phone"], "website": place["website"],
                         "lat": place["lat"], "lng": place["lng"], "pid": pid, "rating": place["rating"],
                         "count": place["ratingCount"]})
            except Exception as exc:  # noqa: BLE001 -- e.g. two walkers adding the same place at once
                print(f"[google] couldn't add {pid}: {exc}", flush=True)
                row = query_one("SELECT slug FROM listings WHERE place_id = :p LIMIT 1", {"p": pid})
                if not row:
                    continue
                slug = row["slug"]
            out[pid] = {"slug": slug, "name": place["name"], "category": place["category"],
                        "address": place["address"], "lat": place["lat"], "lng": place["lng"]}
        return jsonify(places=out)

    @app.get("/embed/route-map")
    def embed_route_map():
        """A map of just these listings, in this order (an AI-planned walk)."""
        slugs = [s for s in str(request.args.get("s") or "").split(",") if s][:WALK_MAX_STOPS]
        places, seen = [], set()
        for slug in slugs:
            if slug in seen:
                continue
            seen.add(slug)
            listing = _card_listing(query_one, slug)
            if listing and listing.get("latitude") is not None and listing.get("longitude") is not None:
                places.append(_card_payload(listing))
        if not places:
            abort(404)
        title = " ".join(str(request.args.get("t") or "").split())[:120] or "Your walk"
        return _render(title, places, None, request, cards_min_zoom=CITY_CARDS_MIN_ZOOM)

    @app.get("/embed/explore")
    def embed_explore():
        """The "Plan a walk" map before there's a walk: a clean map with no pins,
        opened at a point, so a reader can move to the area they want ("food in
        this area") or draw a circle to search in. Pins only appear once the
        walk planner has built a route (/embed/route-map)."""
        lat, lng = _float_arg("lat", -90, 90), _float_arg("lng", -180, 180)
        if lat is None or lng is None:
            lat, lng = EXPLORE_DEFAULT[1], EXPLORE_DEFAULT[0]
        zoom = _float_arg("z", 3, 19) or 13.5
        title = " ".join(str(request.args.get("t") or "").split())[:120] or "Plan a walk"
        return _render(title, [], None, request, extra={"explore": True, "center": [lng, lat], "zoom": zoom})

    @app.after_request
    def _allow_embedding(response):
        if request.path.startswith("/embed/"):
            ancestors = " ".join(["'self'"] + allowed_origins(app))
            response.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors}"
            response.headers.pop("X-Frame-Options", None)  # it would override the line above
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# Snow on the walking map: it falls (over the whole screen, like Mapbox's own snow) only while the
# map is looking at one of these areas, fading in when you get there and out when you leave, and
# heavier the closer you zoom. Each area is a middle point [lng, lat] and how far it reaches in
# metres. MAPBOX_SNOW_ZONES in Render can replace the list with JSON in the same shape, or be "off".
SNOW_ZONES = [
    {"name": "Innere Stadt", "center": [16.3713, 48.2083], "radius": 1300},
]


def _snow_zones():
    raw = (os.environ.get("MAPBOX_SNOW_ZONES") or "").strip()
    if raw.lower() in ("off", "none", "0", "false"):
        return []
    zones = SNOW_ZONES
    if raw:
        try:
            zones = json.loads(raw)
        except ValueError:
            print("[maps] MAPBOX_SNOW_ZONES isn't valid JSON; using the built-in snow areas", flush=True)
            zones = SNOW_ZONES
    out = []
    for z in zones if isinstance(zones, list) else []:
        try:
            lng, lat = float(z["center"][0]), float(z["center"][1])
            r = float(z.get("radius", 1000))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if -180 <= lng <= 180 and -90 <= lat <= 90 and 50 <= r <= 50000:
            out.append({"name": str(z.get("name") or "")[:60], "center": [lng, lat], "radius": r})
    return out[:20]


def _render(title, places, open_url, req, cards_min_zoom=None, extra=None):
    style_key = req.args.get("style", "light")
    style = STYLES.get(style_key, STYLES["light"])
    payload = {
        "token": os.environ.get("MAPBOX_TOKEN") or "",
        "style": style,
        "title": title,
        "open": open_url,
        "places": places,
        "cardsMinZoom": cards_min_zoom,
        "snow": _snow_zones() if style_key == "mansfield" else [],
    }
    payload.update(extra or {})
    return render_template("embed_map.html", payload=payload, title=title)
