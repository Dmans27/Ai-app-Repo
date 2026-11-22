"""
"Add my place" -- the owner's own listings, from a phone.

    GET  /admin/my-place                 the page (templates/admin_my_place.html)
    GET  /admin/my-place/lookup?q=...    a Google Maps link or a name -> matching places
    POST /admin/my-place                 save: Google's basics + your photos, rating and take

A hybrid listing: Google supplies the facts that are boring to type (name,
address, category, map position, place id); you supply what makes the
directory yours -- your photos (uploaded to Cloudinary), your rating, your
take (a sentence or two in your own words) and your tags. Your photos come
first on every map card and listing page; your rating and take go to the
AI walk planner, which prefers your picks and writes its notes from your words.

If the place is already in the directory (same Google place id) it's updated,
never duplicated. Nothing here touches the reader reviews (listing_comments)
or Google's own rating (google_rating), which stay separate.

Who can use it: a signed-in account whose role is "admin", or whose email is
in ADMIN_EMAILS (Render -> Environment, comma-separated). Everyone else gets 403.

Settings: GOOGLE_MAPS_API_KEY (Places API (New)), CLOUDINARY_* (already set),
ADMIN_EMAILS, and CURATOR_NAME (default "Danny": shown as "Danny's take").
"""
import json
import math
import os
import re
import secrets
from functools import wraps
from urllib.parse import parse_qs, unquote_plus, urlparse

import requests
from flask import abort, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import text as sql_text

MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_TAKE = 600
TAG_IDEAS = ["great for working", "quiet", "outdoor seating", "good for groups", "date spot",
             "open Sundays", "late night", "budget", "worth the wait", "local favourite"]
_MAPS_HOSTS = re.compile(r"^(maps\.app\.goo\.gl|goo\.gl|(www\.)?google\.[a-z.]{2,8}|maps\.google\.[a-z.]{2,8})$")
_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_SEARCH_FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "formattedAddress", "location", "primaryType", "primaryTypeDisplayName",
    "types", "addressComponents", "nationalPhoneNumber", "websiteUri", "rating", "userRatingCount",
    "businessStatus"))


def curator():
    return (os.environ.get("CURATOR_NAME") or "Danny").strip()[:40] or "Danny"


# ── Who may use it ───────────────────────────────────────────────────────

def is_owner():
    if not getattr(current_user, "is_authenticated", False):
        return False
    if (getattr(current_user, "role", "") or "").strip().lower() == "admin":
        return True
    allowed = {e.strip().lower() for e in (os.environ.get("ADMIN_EMAILS") or "").split(",") if e.strip()}
    return (getattr(current_user, "email", "") or "").strip().lower() in allowed


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("login", next=request.path))
        if not is_owner():
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _csrf():
    if not session.get("my_place_csrf"):
        session["my_place_csrf"] = secrets.token_urlsafe(24)
    return session["my_place_csrf"]


def _csrf_ok():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
    want = session.get("my_place_csrf") or ""
    return bool(want) and secrets.compare_digest(sent, want)


# ── Google: a Maps link or a name -> places ──────────────────────────────

def _from_maps_link(text):
    """A Google Maps link -> (name, lat, lng), following a short link once. Only Google's own hosts."""
    try:
        u = urlparse(text)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not _MAPS_HOSTS.match((u.hostname or "").lower()):
        return None
    final = text
    if u.hostname in ("maps.app.goo.gl", "goo.gl"):
        try:
            r = requests.get(text, allow_redirects=True, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            final = r.url
        except requests.RequestException:
            return None
        if not _MAPS_HOSTS.match((urlparse(final).hostname or "").lower()):
            return None
    name = None
    m = re.search(r"/maps/place/([^/@?]+)", final)
    if m:
        name = unquote_plus(m.group(1))
    if not name:
        q = parse_qs(urlparse(final).query)
        name = (q.get("q") or q.get("query") or [None])[0]
    lat = lng = None
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final) or re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", final)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
    if not name and lat is None:
        return None
    return (name or f"{lat},{lng}"), lat, lng


def _category(place):
    """Google's place type -> the directory's words (so the map colours and the AI groups work)."""
    t = (place.get("primaryType") or "").lower()
    types = [x.lower() for x in place.get("types") or []]
    if t in ("cafe", "coffee_shop", "coffee_roastery") or "coffee_shop" in types or "cafe" in types:
        return "coffee shop"
    if t in ("wine_bar",):
        return "wine bar"
    if t in ("bar", "pub", "cocktail_bar", "night_club", "bar_and_grill"):
        return "bar"
    if t == "bakery":
        return "bakery"
    if t.endswith("_restaurant") or t in ("restaurant", "meal_takeaway", "food", "brunch_restaurant"):
        return "restaurant"
    if t in ("hotel", "lodging", "hostel", "bed_and_breakfast", "guest_house"):
        return "hotel"
    if t in ("museum", "art_gallery", "tourist_attraction", "park", "church", "historical_landmark"):
        return t.replace("_", " ")
    label = ((place.get("primaryTypeDisplayName") or {}).get("text") or t.replace("_", " ")).strip().lower()
    return label or "places"


def _address_part(place, kind, short=False):
    for c in place.get("addressComponents") or []:
        if kind in (c.get("types") or []):
            return c.get("shortText" if short else "longText") or ""
    return ""


def google_search(text, lat=None, lng=None):
    key = (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not key:
        raise LookupError("The Google key (GOOGLE_MAPS_API_KEY) isn't set on this server.")
    body = {"textQuery": text[:200], "maxResultCount": 5, "languageCode": "en"}
    if lat is not None and lng is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": 300.0}}
    try:
        r = requests.post("https://places.googleapis.com/v1/places:searchText", json=body, timeout=10,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": _SEARCH_FIELDS})
    except requests.RequestException:
        raise LookupError("Google couldn't be reached. Please try again.")
    if r.status_code != 200:
        print("[MY_PLACE] Google search said", r.status_code, r.text[:300], flush=True)
        raise LookupError("Google didn't answer that search. Try the place's name and street.")
    out = []
    for p in (r.json() or {}).get("places") or []:
        loc = p.get("location") or {}
        if not p.get("id") or loc.get("latitude") is None:
            continue
        out.append({
            "place_id": p["id"],
            "name": (p.get("displayName") or {}).get("text") or "",
            "address": p.get("formattedAddress") or "",
            "city": _address_part(p, "locality") or _address_part(p, "postal_town"),
            "state": _address_part(p, "administrative_area_level_1", short=True),
            "lat": loc["latitude"], "lng": loc["longitude"],
            "category": _category(p),
            "phone": p.get("nationalPhoneNumber") or "",
            "website": p.get("websiteUri") or "",
            "google_rating": p.get("rating"), "google_count": p.get("userRatingCount"),
            "closed": p.get("businessStatus") not in (None, "OPERATIONAL"),
        })
    return out


# ── Saving ───────────────────────────────────────────────────────────────

def _slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower())
    return re.sub(r"-{2,}", "-", s).strip("-")


def _float(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi and not math.isnan(f) else None


def _clean(v, n):
    return " ".join(str(v or "").split())[:n]


def _gallery(row):
    try:
        g = json.loads(row.get("photo_urls_json") or "[]") or []
    except (TypeError, ValueError):
        g = []
    g = [x for x in g if isinstance(x, str)]
    first = row.get("photo_url")
    if first and first not in g:
        g.insert(0, first)
    return g


def register_my_place(app, engine, cloudinary_uploader):

    def one(sql, params):
        with engine.connect() as conn:
            row = conn.execute(sql_text(sql), params).first()
            return dict(row._mapping) if row else None

    def rows(sql, params):
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sql_text(sql), params)]

    def run(sql, params):
        with engine.begin() as conn:
            return conn.execute(sql_text(sql), params)

    # The new columns (Postgres adds them once; on SQLite, used in tests, each is tried on its own).
    for col, kind in (("my_rating", "DOUBLE PRECISION"), ("my_take", "TEXT"), ("my_tags", "TEXT"),
                      ("source", "TEXT"), ("my_updated_at", "TIMESTAMP")):
        try:
            with engine.begin() as conn:
                if engine.dialect.name == "postgresql":
                    conn.execute(sql_text(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {col} {kind}"))
                else:
                    have = [r[1] for r in conn.execute(sql_text("PRAGMA table_info(listings)"))]
                    if col not in have:
                        conn.execute(sql_text(f"ALTER TABLE listings ADD COLUMN {col} {kind}"))
        except Exception as exc:  # noqa: BLE001 -- the page explains what's missing if this fails
            print(f"[MY_PLACE] couldn't add listings.{col}: {exc}", flush=True)

    def existing_for(place_id):
        if not place_id:
            return None
        return one("SELECT id, slug, name, my_rating, my_take, my_tags, photo_url, photo_urls_json, status "
                   "FROM listings WHERE place_id = :p LIMIT 1", {"p": place_id})

    def free_slug(name, city, address):
        street = _slug((address or "").split(",")[0])[:40]
        for cand in (_slug(name), _slug(f"{name}-{city}"), _slug(f"{name}-{city}-{street}")):
            if cand and not one("SELECT 1 AS x FROM listings WHERE slug = :s", {"s": cand}):
                return cand
        return _slug(f"{name}-{secrets.token_hex(3)}")

    @app.get("/admin/my-place")
    @owner_required
    def admin_my_place():
        edit = None
        if request.args.get("edit", "").isdigit():
            edit = one("SELECT id, slug, name, category, address, city, state, phone, website, latitude, longitude, "
                       "place_id, my_rating, my_take, my_tags, status, photo_url, photo_urls_json "
                       "FROM listings WHERE id = :id", {"id": int(request.args["edit"])})
            if edit:
                edit["photos"] = _gallery(edit)[:12]
        mine = rows("SELECT id, slug, name, category, city, my_rating, status FROM listings "
                    "WHERE source = 'mine' ORDER BY my_updated_at DESC NULLS LAST, id DESC LIMIT 30", {}) \
            if engine.dialect.name == "postgresql" else \
            rows("SELECT id, slug, name, category, city, my_rating, status FROM listings "
                 "WHERE source = 'mine' ORDER BY my_updated_at DESC, id DESC LIMIT 30", {})
        return render_template("admin_my_place.html", csrf=_csrf(), curator=curator(), tag_ideas=TAG_IDEAS,
                               edit=edit, mine=mine, saved=request.args.get("saved", ""),
                               max_photos=MAX_PHOTOS, max_take=MAX_TAKE)

    @app.get("/admin/my-place/lookup")
    @owner_required
    def admin_my_place_lookup():
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify(ok=False, error="Paste a Google Maps link, or type the place's name.")
        lat = lng = None
        link = _from_maps_link(q) if q.lower().startswith("http") else None
        if q.lower().startswith("http") and not link:
            return jsonify(ok=False, error="That link isn't a Google Maps link I can read. Try the place's name instead.")
        text = link[0] if link else q
        if link:
            lat, lng = link[1], link[2]
        try:
            found = google_search(text, lat, lng)
        except LookupError as exc:
            return jsonify(ok=False, error=str(exc))
        for f in found:
            ex = existing_for(f["place_id"])
            f["existing"] = None if not ex else {
                "id": ex["id"], "slug": ex["slug"], "my_rating": ex.get("my_rating"),
                "my_take": ex.get("my_take") or "", "my_tags": ex.get("my_tags") or "",
                "photos": len(_gallery(ex)), "status": ex.get("status")}
        if not found:
            return jsonify(ok=False, error="Google found nothing for that. Try adding the street or city.")
        return jsonify(ok=True, results=found)

    @app.post("/admin/my-place")
    @owner_required
    def admin_my_place_save():
        if not _csrf_ok():
            return jsonify(ok=False, error="This page expired. Reload it and try again."), 400
        f = request.form
        name = _clean(f.get("name"), 160)
        place_id = (f.get("place_id") or "").strip()
        lat, lng = _float(f.get("lat"), -90, 90), _float(f.get("lng"), -180, 180)
        edit_id = int(f["listing_id"]) if (f.get("listing_id") or "").isdigit() else None
        if not name:
            return jsonify(ok=False, error="The place needs a name."), 400
        if place_id and not _PLACE_ID.match(place_id):
            return jsonify(ok=False, error="That Google place id doesn't look right."), 400
        if not edit_id and (lat is None or lng is None):
            return jsonify(ok=False, error="Look the place up first, so it has a spot on the map."), 400
        rating = _float(f.get("my_rating"), 0, 5)
        rating = round(rating * 2) / 2 if rating else None          # whole and half stars
        take = str(f.get("my_take") or "").strip()[:MAX_TAKE]
        tags = ", ".join(t for t in (_clean(x, 40) for x in (f.get("my_tags") or "").split(",")) if t)[:400]
        status = "draft" if f.get("status") == "draft" else "published"

        files = [p for p in request.files.getlist("photos") if p and p.filename][:MAX_PHOTOS]
        uploaded = []
        for p in files:
            data = p.read(MAX_PHOTO_BYTES + 1)
            if len(data) > MAX_PHOTO_BYTES:
                return jsonify(ok=False, error=f"{p.filename} is too big (15 MB max)."), 400
            if not (p.mimetype or "").startswith("image/"):
                return jsonify(ok=False, error=f"{p.filename} isn't a picture."), 400
            try:
                res = cloudinary_uploader.upload(data, folder="localai/my_places", resource_type="image",
                                                 transformation=[{"width": 1800, "crop": "limit",
                                                                  "quality": "auto", "fetch_format": "auto"}])
                uploaded.append(res["secure_url"])
            except Exception as exc:  # noqa: BLE001
                print("[MY_PLACE_UPLOAD_ERROR]", exc, flush=True)
                return jsonify(ok=False, error="A photo didn't upload. Check your connection and try again."), 502

        row = None
        if edit_id:
            row = one("SELECT id, slug, photo_url, photo_urls_json, place_id FROM listings WHERE id = :id", {"id": edit_id})
            if not row:
                return jsonify(ok=False, error="That listing doesn't exist any more."), 404
        elif place_id:
            row = existing_for(place_id)

        fields = {
            "name": name, "category": _clean(f.get("category"), 60) or "places",
            "address": _clean(f.get("address"), 300), "city": _clean(f.get("city"), 80),
            "state": _clean(f.get("state"), 40), "phone": _clean(f.get("phone"), 40),
            "website": _clean(f.get("website"), 400),
            "my_rating": rating, "my_take": take, "my_tags": tags, "status": status,
        }
        if row:
            photos = _gallery(row)
            if uploaded:
                photos = uploaded + ([] if f.get("replace_photos") == "1" else [x for x in photos if x not in uploaded])
            photos = photos[:12]
            sets = ", ".join(f"{k} = :{k}" for k in fields)
            params = dict(fields, id=row["id"])
            if lat is not None and lng is not None:
                sets += ", latitude = :lat, longitude = :lng"
                params.update(lat=lat, lng=lng)
            if place_id and not row.get("place_id"):
                sets += ", place_id = :place_id"
                params["place_id"] = place_id
            if uploaded:
                sets += ", photo_url = :photo, card_image_url = :photo, photo_urls_json = :gallery"
                params.update(photo=photos[0], gallery=json.dumps(photos))
            run(f"UPDATE listings SET {sets}, source = 'mine', my_updated_at = CURRENT_TIMESTAMP, "
                f"updated_at = CURRENT_TIMESTAMP WHERE id = :id", params)
            listing_id, slug = row["id"], row["slug"]
        else:
            slug = free_slug(name, fields["city"], fields["address"])
            params = dict(fields, slug=slug, lat=lat, lng=lng, place_id=place_id or None,
                          photo=uploaded[0] if uploaded else None,
                          gallery=json.dumps(uploaded) if uploaded else None,
                          g_rating=_float(f.get("google_rating"), 0, 5),
                          g_count=int(f["google_count"]) if (f.get("google_count") or "").isdigit() else None)
            run("INSERT INTO listings (name, slug, category, address, city, state, phone, website, latitude, longitude, "
                "place_id, photo_url, card_image_url, photo_urls_json, google_rating, google_rating_count, "
                "my_rating, my_take, my_tags, status, featured, source, my_updated_at, created_at, updated_at) "
                "VALUES (:name, :slug, :category, :address, :city, :state, :phone, :website, :lat, :lng, "
                ":place_id, :photo, :photo, :gallery, :g_rating, :g_count, "
                ":my_rating, :my_take, :my_tags, :status, 0, 'mine', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                params)
            listing_id = (one("SELECT id FROM listings WHERE slug = :s", {"s": slug}) or {}).get("id")
        return jsonify(ok=True, id=listing_id, slug=slug, photos=len(uploaded), status=status,
                       updated=bool(row))
