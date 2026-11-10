"""
Map-only pages that another website can show inside an iframe.

The \\\\Mansfield media site has an "Interactive map" block for its stories:
the writer pastes a map link from this app and readers get the live,
draggable map right inside the story. This file provides the pages that
iframe loads:

  /embed/list/<slug>   one public list, as a map
  /embed/u/<user_id>   every place in a user's PUBLIC lists, on one map

Both pages are just a map (no header, menu or sign-in) and are marked
noindex so search engines don't list them as pages of their own.

Who may show them? Only the sites named in EMBED_ALLOWED_ORIGINS (space or
comma separated, e.g. "https://the-sod-father.onrender.com"). Every
response from /embed/ carries a Content-Security-Policy "frame-ancestors"
header built from that list, and browsers refuse to show the page inside
any other website. Change the setting in Render if the media site gets a
custom domain.

Wired up with two lines near the bottom of cms_app.py:

    from embed_maps import register_embed_routes
    register_embed_routes(app, SavedList, query_one)
"""
import os
import re

from flask import abort, render_template, request, url_for

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


def _listing_slug(place, query_one):
    """The directory listing that matches a saved place (same lookup order
    as the public map page: listing id, Google place id, then name)."""
    try:
        row = None
        if getattr(place, "listing_id", None):
            row = query_one("SELECT slug FROM listings WHERE id = :id LIMIT 1",
                            {"id": place.listing_id})
        if not row and getattr(place, "place_id", None):
            row = query_one("SELECT slug FROM listings WHERE place_id = :place_id LIMIT 1",
                            {"place_id": place.place_id})
        if not row and place.name:
            row = query_one("SELECT slug FROM listings WHERE LOWER(name) = LOWER(:name) LIMIT 1",
                            {"name": place.name})
        return row["slug"] if row else None
    except Exception:
        return None  # a missing listing just means no "Show listing" link


def _places_json(places, query_one):
    out = []
    for place in places:
        if place.latitude is None or place.longitude is None:
            continue
        out.append({
            "name": place.name or "",
            "lat": float(place.latitude),
            "lng": float(place.longitude),
            "category": place.category or "",
            "address": place.address or "",
            "notes": place.notes or "",
            "website": _safe_http(place.website),
            "listing": _listing_slug(place, query_one),
        })
        if len(out) >= MAX_PLACES:
            break
    return out


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
