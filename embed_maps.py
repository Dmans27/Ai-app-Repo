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
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from flask import abort, jsonify, redirect, render_template, request, url_for

DEFAULT_ALLOWED_ORIGINS = "https://the-sod-father.onrender.com"

# The embed page only ever uses one of these Mapbox styles, picked with
# ?style=light|dark|streets (anything else falls back to light).
STYLES = {
    "light": "mapbox://styles/mapbox/light-v11",
    "dark": "mapbox://styles/mapbox/dark-v11",
    "streets": "mapbox://styles/mapbox/streets-v12",
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
                 "photo_urls_json, google_rating")

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
                "google_rating, google_rating_count, place_id AS listing_place_id")
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
              "photo_urls_json, google_rating")


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
        "photo": url_for("embed_listing_photo", slug=slug) if photos else None,
        "photos": [url_for("embed_listing_photo", slug=slug, i=n) for n in range(len(photos))],
        "detailUrl": url_for("embed_card_detail", slug=slug),
        "book": None,
        "video": None,
    }


def register_embed_routes(app, SavedList, query_one):
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
        return _render(title, _places_json(places, query_one),
                       url_for("user_public_map", user_id=user_id), request)

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

    @app.after_request
    def _allow_embedding(response):
        if request.path.startswith("/embed/"):
            ancestors = " ".join(["'self'"] + allowed_origins(app))
            response.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors}"
            response.headers.pop("X-Frame-Options", None)  # it would override the line above
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


def _render(title, places, open_url, req):
    style = STYLES.get(req.args.get("style", "light"), STYLES["light"])
    return render_template(
        "embed_map.html",
        payload={
            "token": os.environ.get("MAPBOX_TOKEN") or "",
            "style": style,
            "title": title,
            "open": open_url,
            "places": places,
        },
        title=title,
    )
