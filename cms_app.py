import os
import re
import json
import time
import random
import base64
import uuid
import traceback
import sqlite3
from datetime import datetime
from functools import wraps
from flask import redirect
from models import UserSavedList
import base64



import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import markdown

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, abort, session, jsonify
from sqlalchemy.sql.functions import user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from openai import OpenAI
from openai import APIError, RateLimitError, APITimeoutError

import requests
from urllib.parse import urlparse

from concurrent.futures import ThreadPoolExecutor
from json_repair import repair_json


from flask import Flask
from models import db
from auth import auth
from flask_login import LoginManager


from flask import request, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, SavedList, SavedPlace, BusinessClaim, UserSavedList



import cloudinary
import cloudinary.uploader
import cloudinary.api
import os



AI_EXECUTOR = ThreadPoolExecutor(max_workers=2)
AI_JOBS = {}  # job_id -> dict(status, result, error, created_at)
# -----------------------
# App + config
# -----------------------



from sqlalchemy import create_engine, text as sql_text

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "cms.db")

database_url = os.environ.get("DATABASE_URL")

if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or f"sqlite:///{SQLITE_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-change-me-please")

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_size": 5,
    "max_overflow": 10
}



cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)

db.init_app(app)

engine = create_engine(
    app.config["SQLALCHEMY_DATABASE_URI"],
    pool_pre_ping=True,
    pool_recycle=280,
    pool_size=5,
    max_overflow=10,
    future=True
)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


def create_core_tables():
    with engine.begin() as conn:

        # ---------------------------------------------------
        # Pages
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS pages (
                id SERIAL PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                template TEXT NOT NULL DEFAULT 'landing_default',
                status TEXT NOT NULL DEFAULT 'draft',
                tag_title TEXT,
                hero_title TEXT,
                card_image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Sections
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS sections (
                id SERIAL PRIMARY KEY,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                section_type TEXT NOT NULL DEFAULT 'paragraph',
                heading TEXT,
                body TEXT,
                media_path TEXT,
                media_alt TEXT,
                media_caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Listings
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS listings (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE,
                category TEXT,
                city TEXT,
                state TEXT,
                address TEXT,
                phone TEXT,
                website TEXT,
                description TEXT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                photo_url TEXT,
                photo_urls_json TEXT,
                card_image_url TEXT,
                featured INTEGER DEFAULT 0,
                status TEXT DEFAULT 'published',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Ads
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS ads (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                placement TEXT NOT NULL,
                image_url TEXT,
                headline TEXT,
                body TEXT,
                button_text TEXT,
                target_url TEXT,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Facts
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS facts (
                id SERIAL PRIMARY KEY,
                topic TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                snippet TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Conversations
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                session_id TEXT,
                state_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Conversation Messages
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Listing Comments
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS listing_comments (
                id SERIAL PRIMARY KEY,
                listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                author_name TEXT NOT NULL,
                author_email TEXT,
                body TEXT NOT NULL,
                rating INTEGER NOT NULL DEFAULT 5,
                is_approved INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Directory Meta
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS directory_page_meta (
                id SERIAL PRIMARY KEY,
                page_id INTEGER UNIQUE NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                category TEXT NOT NULL,
                intro_text TEXT
            );
        """))

        # ---------------------------------------------------
        # Feed Posts
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS feed_posts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                post_type TEXT NOT NULL DEFAULT 'text',
                title TEXT,
                body TEXT,
                image_url TEXT,
                listing_id INTEGER,
                saved_list_id INTEGER,
                city TEXT,
                is_public INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # ---------------------------------------------------
        # Feed Likes
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS feed_post_likes (
                id SERIAL PRIMARY KEY,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, user_id)
            );
        """))

        # ---------------------------------------------------
        # Feed Comments
        # ---------------------------------------------------
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS feed_post_comments (
                id SERIAL PRIMARY KEY,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        
        
        
        conn.execute(sql_text("""
            ALTER TABLE listings
            ADD COLUMN IF NOT EXISTS place_id TEXT;
        """))

        conn.execute(sql_text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_place_id
            ON listings(place_id)
            WHERE place_id IS NOT NULL;
        """))
        
        
        
        
        conn.execute(sql_text("""
    CREATE TABLE IF NOT EXISTS user_friends (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        friend_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, friend_id)
    );
"""))
        
        
        
        conn.execute(sql_text("""
    CREATE TABLE IF NOT EXISTS user_saved_lists (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        saved_list_id INTEGER NOT NULL REFERENCES saved_list(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_user_saved_list UNIQUE (user_id, saved_list_id)
    );
"""))



def ensure_user_profile_columns():
    with engine.begin() as conn:

        conn.execute(sql_text("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS favorite_categories TEXT;
        """))

        conn.execute(sql_text("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS home_city VARCHAR(120);
        """))

        conn.execute(sql_text("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS budget_style VARCHAR(50);
        """))

        conn.execute(sql_text("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS intent_type VARCHAR(120);
        """))

        conn.execute(sql_text("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE;
        """))
        
        conn.execute(sql_text("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS profile_image_url TEXT;
        """))

    print("user profile columns checked", flush=True)


def init_db():
    with app.app_context():
        db.create_all()
        create_core_tables()
        ensure_user_profile_columns()
        print("[SQLALCHEMY DB URL]", db.engine.url, flush=True)


def bootstrap_app():
    init_db()
    print("init_db() finished", flush=True)


bootstrap_app()











login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


app.register_blueprint(auth)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

load_dotenv()
load_dotenv(os.path.join(BASE_DIR, "keys.env"))

print("CWD:", os.getcwd())
print("SERPER_API_KEY exists:", bool(os.environ.get("SERPER_API_KEY")))
print("OPENAI_API_KEY exists:", bool(os.environ.get("OPENAI_API_KEY")))

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    timeout=240,
    max_retries=0
)      
        
        
        
        
        
 


            
            
    
        
    
    


# -----------------------
# Helpers
# -----------------------






def geocode_location_name(location_name):
    if not GOOGLE_MAPS_API_KEY or not location_name:
        return None

    cleaned = location_name.strip()

    # remove business/category words so "bars in los angeles" doesn't geocode as a bar
    category_words = [
        "bars", "bar", "restaurants", "restaurant", "coffee", "coffee shops",
        "tacos", "pizza", "brunch", "dinner", "lunch", "breakfast",
        "near me", "nearby", "tonight", "best", "good"
    ]

    lowered = cleaned.lower()
    for word in category_words:
        lowered = lowered.replace(word, "")

    cleaned = lowered.strip(" ,")

    if not cleaned:
        return None

    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": cleaned,
            "key": GOOGLE_MAPS_API_KEY
        }

        res = requests.get(url, params=params, timeout=10).json()

        if res.get("status") != "OK" or not res.get("results"):
            return None

        result = res["results"][0]
        loc = result["geometry"]["location"]

        return {
            "city": result.get("formatted_address", cleaned),
            "lat": loc["lat"],
            "lng": loc["lng"]
        }

    except Exception as e:
        print("[GEOCODE_LOCATION_ERROR]", str(e), flush=True)
        return None








def safe_query_all(sql, params=(), fallback=None, label="SAFE_QUERY_ALL"):
    try:
        return query_all(sql, params)
    except sqlite3.OperationalError as e:
        print(f"[{label}]", str(e), flush=True)
        return [] if fallback is None else fallback
    except Exception as e:
        print(f"[{label}_UNEXPECTED]", str(e), flush=True)
        return [] if fallback is None else fallback


def safe_query_one(sql, params=(), fallback=None, label="SAFE_QUERY_ONE"):
    try:
        return query_one(sql, params)
    except sqlite3.OperationalError as e:
        print(f"[{label}]", str(e), flush=True)
        return fallback
    except Exception as e:
        print(f"[{label}_UNEXPECTED]", str(e), flush=True)
        return fallback




def is_broad_query(message: str) -> bool:
    text = (message or "").strip().lower()

    broad_phrases = [
        "food near me",
        "restaurants near me",
        "bars near me",
        "coffee near me",
        "find food",
        "find bars",
        "find coffee",
        "where should i go",
        "something to eat",
    ]
    return any(p in text for p in broad_phrases)


def detect_category_from_message(message: str):
    text = (message or "").strip().lower()

    category_keywords = {
        "coffee": ["coffee", "cafe", "espresso", "latte"],
        "bars": ["bar", "bars", "cocktail", "cocktails", "pub", "drinks"],
        "restaurants": ["restaurant", "restaurants", "food", "eat", "dinner", "lunch"],
        "burgers": ["burger", "burgers"],
        "pizza": ["pizza"],
        "sushi": ["sushi"],
        "mexican": ["mexican", "tacos", "taqueria"],
        "shopping": ["shopping", "shops", "boutiques", "stores"],
        "gym": ["gym", "fitness", "workout"],
    }

    for category, keywords in category_keywords.items():
        if any(word in text for word in keywords):
            return category
    return None


def maybe_reset_state_for_new_topic(state: dict, message: str) -> dict:
    state = dict(state or {})
    detected_category = detect_category_from_message(message)
    old_category = state.get("category")

    if detected_category and old_category and detected_category != old_category:
        return {
            "category": detected_category,
            "city": state.get("city"),
            "area": state.get("area"),
            "lat": state.get("lat"),
            "lng": state.get("lng"),
            "vibe": None,
            "purpose": None,
            "last_followup": None,
        }

    return state


def update_state_from_message(state: dict, message: str, lat=None, lng=None) -> dict:
    state = dict(state or {})
    text = (message or "").strip().lower()

    state.setdefault("category", None)
    state.setdefault("city", None)
    state.setdefault("area", None)
    state.setdefault("vibe", None)
    state.setdefault("purpose", None)
    state.setdefault("lat", None)
    state.setdefault("lng", None)
    state.setdefault("last_followup", None)

    if lat is not None:
        state["lat"] = lat
    if lng is not None:
        state["lng"] = lng

    detected_category = detect_category_from_message(message)
    if detected_category:
        state["category"] = detected_category

    city_override = extract_city(message)

    if city_override:
        state["city"] = city_override
        state["lat"] = None
        state["lng"] = None

    if "near path" in text:
        state["area"] = "near PATH"
    elif "uptown" in text:
        state["area"] = "uptown"
    elif "downtown" in text:
        state["area"] = "downtown"
    elif "waterfront" in text:
        state["area"] = "waterfront"

    if any(word in text for word in ["quiet", "quieter", "calm"]):
        state["vibe"] = "quiet"
    elif any(word in text for word in ["lively", "fun", "busy", "energetic"]):
        state["vibe"] = "lively"
    elif any(word in text for word in ["casual", "relaxed", "easygoing"]):
        state["vibe"] = "casual"
    elif any(word in text for word in ["romantic", "date night", "date-night"]):
        state["vibe"] = "date-night"

    if any(word in text for word in ["work", "study", "laptop-friendly"]):
        state["purpose"] = "work"
    elif any(word in text for word in ["quick", "fast", "grab and go", "grab-and-go"]):
        state["purpose"] = "quick"
    elif any(word in text for word in ["date", "date night", "date-night"]):
        state["purpose"] = "date"

    return state


def should_ask_followup(state):
    category = state.get("category")
    city = state.get("city")
    lat = state.get("lat")
    lng = state.get("lng")
    vibe = state.get("vibe")
    purpose = state.get("purpose")

    if not category:
        return True, "What kind of place are you looking for?", "category"

    if not city and not (lat and lng):
        return True, "What city or area are you in?", "location"

    if category == "coffee" and not vibe and not purpose:
        return True, "Do you want the best coffee, a quiet work spot, or something quick?", "coffee_vibe"

    if category in ["restaurants", "bars"] and not vibe and not purpose:
        return True, "What kind of vibe are you looking for — casual, lively, quick, or date-night?", "food_vibe"

    return False, None, None


def build_query_from_state(state: dict) -> str:
    parts = [
        state.get("vibe"),
        state.get("purpose"),
        state.get("category"),
        state.get("area"),
        state.get("city"),
    ]
    return " ".join([p for p in parts if p]).strip()


def resolve_search_location(state: dict, lat=None, lng=None):
    city = (state.get("city") or "").strip() or None
    user_lat = state.get("lat")
    user_lng = state.get("lng")
    location_source = "conversation"

    if city:
        city_lat, city_lng = geocode_city(city, None)
        if city_lat is not None and city_lng is not None:
            return city_lat, city_lng, "city"

    if user_lat is None or user_lng is None:
        try:
            if lat is not None and lng is not None:
                return float(lat), float(lng), "browser"
        except (TypeError, ValueError):
            pass

    return user_lat, user_lng, location_source


def bucket_results(internal_results, external_results, query, searched_city):
    internal_results = rows_to_dicts(internal_results)
    external_results = rows_to_dicts(external_results)

    for r in internal_results:
        r["source"] = "internal"

    for r in external_results:
        r["source"] = "external"

    featured_internal = [
        r for r in internal_results
        if int(r.get("featured", 0)) == 1
        and is_relevant_featured_result(
            r,
            query=query,
            searched_city=searched_city,
            max_featured_distance=10
        )
    ]

    regular_internal = [r for r in internal_results if r not in featured_internal]
    combined = featured_internal + regular_internal + external_results

    return featured_internal, regular_internal, external_results, combined


def build_recommendation_reply(state: dict, results: list) -> str:
    if not results:
        return (
            "I couldn't find any strong nearby matches yet. "
            "Try a different category or give me a more specific area."
        )

    category = state.get("category") or "places"
    vibe = state.get("vibe")
    purpose = state.get("purpose")
    city = state.get("city")

    lead = "Here are some good"
    if vibe:
        lead += f" {vibe}"
    if purpose:
        lead += f" {purpose}"
    lead += f" {category}"

    if city:
        lead += f" in {city}"

    top_names = [r.get("name") for r in results[:4] if r.get("name")]
    if top_names:
        return f"{lead.title()}. I’d start with {', '.join(top_names)}."
    return f"{lead.title()}."



import json
import uuid
from flask import session

from sqlalchemy import create_engine, text as sql_text
import os

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def get_or_create_session_id():
    if "chat_session_id" not in session:
        session["chat_session_id"] = str(uuid.uuid4())
    return session["chat_session_id"]

def load_or_create_conversation(user_id=None):
    session_id = get_or_create_session_id()

    if user_id:
        conversation = query_one(
            """
            SELECT *
            FROM conversations
            WHERE user_id = :user_id
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            {"user_id": user_id}
        )
    else:
        conversation = query_one(
            """
            SELECT *
            FROM conversations
            WHERE session_id = :session_id
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            {"session_id": session_id}
        )

    if conversation:
        return conversation

    with engine.begin() as conn:
        row = conn.execute(
            sql_text("""
                INSERT INTO conversations (
                    user_id,
                    session_id,
                    state_json
                )
                VALUES (
                    :user_id,
                    :session_id,
                    :state_json
                )
                RETURNING *
            """),
            {
                "user_id": user_id,
                "session_id": session_id,
                "state_json": json.dumps({})
            }
        ).first()

    return dict(row._mapping)

def save_message(conversation_id, role, content):
    execute(
        """
        INSERT INTO conversation_messages (
            conversation_id,
            role,
            content
        )
        VALUES (
            :conversation_id,
            :role,
            :content
        )
        """,
        {
            "conversation_id": conversation_id,
            "role": role,
            "content": content
        }
    )

    execute(
        """
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = :conversation_id
        """,
        {
            "conversation_id": conversation_id
        }
    )

def load_messages(conversation_id, limit=12):
    return query_all(
        """
        SELECT role, content
        FROM conversation_messages
        WHERE conversation_id = :conversation_id
        ORDER BY id ASC
        LIMIT :limit
        """,
        {
            "conversation_id": conversation_id,
            "limit": limit
        }
    )

def load_state(conversation):
    raw = conversation.get("state_json") or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {}

def save_state(conversation_id, state):
    execute(
        """
        UPDATE conversations
        SET state_json = :state_json,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :conversation_id
        """,
        {
            "state_json": json.dumps(state),
            "conversation_id": conversation_id
        }
    )




KNOWN_CITY_COORDS = {
    "nashville": (36.1627, -86.7816),
    "miami": (25.7617, -80.1918),
    "austin": (30.2672, -97.7431),
    "chicago": (41.8781, -87.6298),
    "naperville": (41.7508, -88.1535),
    "new york": (40.7128, -74.0060),
    "denver": (39.7392, -104.9903)
}




def update_state_from_message(state, message, lat=None, lng=None):
    text = (message or "").strip().lower()

    state.setdefault("category", None)
    state.setdefault("city", None)
    state.setdefault("area", None)
    state.setdefault("vibe", None)
    state.setdefault("purpose", None)
    state.setdefault("lat", lat)
    state.setdefault("lng", lng)

    if lat is not None:
        state["lat"] = lat
    if lng is not None:
        state["lng"] = lng

    category_keywords = {
        "coffee": ["coffee", "cafe", "espresso", "latte"],
        "burgers": ["burger", "burgers"],
        "pizza": ["pizza"],
        "bars": ["bar", "bars", "cocktails", "drinks", "pub"],
        "restaurants": ["restaurant", "restaurants", "food", "eat", "dinner", "lunch"],
        "sushi": ["sushi"],
        "mexican": ["mexican", "tacos", "taqueria"],
    }
    
    
    
    location_override = detect_location_from_query(message)

    if location_override:
        state["city"] = location_override["city"]
        state["lat"] = location_override["lat"]
        state["lng"] = location_override["lng"]

    for category, keywords in category_keywords.items():
        if any(k in text for k in keywords):
            state["category"] = category
            break

    city_override = extract_city(message)

    if city_override:
        state["city"] = city_override
        state["lat"] = None
        state["lng"] = None

    if "near path" in text:
        state["area"] = "near PATH"
    elif "uptown" in text:
        state["area"] = "uptown"
    elif "downtown" in text:
        state["area"] = "downtown"
    elif "waterfront" in text:
        state["area"] = "waterfront"

    # --- vibe handling ---
    if any(word in text for word in ["quiet", "quieter", "calm"]):
        state["vibe"] = "quiet"
    elif any(word in text for word in ["lively", "fun", "busy", "energetic"]):
        state["vibe"] = "lively"
    elif any(word in text for word in ["casual", "relaxed", "easygoing"]):
        state["vibe"] = "casual"
    elif any(word in text for word in ["romantic", "date night", "date-night", "date"]):
        state["vibe"] = "date-night"

    # --- purpose handling ---
    if any(word in text for word in ["work", "study", "laptop-friendly"]):
        state["purpose"] = "work"
    elif any(word in text for word in ["quick", "fast", "grab and go", "grab-and-go"]):
        state["purpose"] = "quick"
    elif any(word in text for word in ["date", "date night", "date-night"]):
        state["purpose"] = "date"

    return state









def run_conversational_search(state):
    category = state.get("category")
    city = state.get("city")
    lat = state.get("lat")
    lng = state.get("lng")
    vibe = state.get("vibe")
    purpose = state.get("purpose")
    area = state.get("area")

    query_parts = []
    if category:
        query_parts.append(category)
    if vibe:
        query_parts.append(vibe)
    if purpose:
        query_parts.append(purpose)
    if area:
        query_parts.append(area)
    if city:
        query_parts.append(city)

    query_text = " ".join(query_parts).strip()

    # Search internal listings first
    internal_results = search_internal_listings(
        q=query_text,
        lat=lat,
        lng=lng,
        limit=10
    )

    # Then search Google Places
    external_results = google_places_text_search(
        query=query_text,
        lat=lat,
        lng=lng,
        limit=10
    ) if query_text else []

    # Combine results
    results = internal_results + external_results

    return results




def build_recommendation_reply(state, results):
    category = state.get("category") or "places"
    vibe = state.get("vibe")
    purpose = state.get("purpose")
    city = state.get("city")

    intro_parts = ["Here are some good"]
    if vibe:
        intro_parts.append(vibe)
    if purpose:
        intro_parts.append(purpose)
    intro_parts.append(category)

    if city:
        intro_parts.append(f"in {city}")

    intro = " ".join(intro_parts).replace("  ", " ").strip()

    top_names = [r.get("name") for r in results[:4] if r.get("name")]
    if top_names:
        return f"{intro.title()}. I’d start with {', '.join(top_names)}."
    return f"{intro.title()}. I found a few options worth checking out."




def build_google_photo_url(photo_name, max_width=1200):
    if not photo_name:
        return None

    return (
        f"https://places.googleapis.com/v1/{photo_name}/media"
        f"?maxWidthPx={max_width}&key={GOOGLE_MAPS_API_KEY}"
    )
    
    

def attach_google_photo_gallery(place):
    photos = place.get("photos") or []
    gallery = []

    for photo in photos[:5]:
        photo_name = photo.get("name")
        if not photo_name:
            continue

        url = build_google_photo_url(photo_name, max_width=1200)
        if not url:
            continue

        gallery.append({
            "url": url,
            "attributions": photo.get("authorAttributions", [])
        })

    place["photo_gallery"] = gallery
    return place




def build_google_photo_url(photo_name, max_width=800):
    if not photo_name:
        return None
    return (
        f"https://places.googleapis.com/v1/{photo_name}/media"
        f"?key={GOOGLE_MAPS_API_KEY}&maxWidthPx={max_width}"
    )
    
    
    



def geocode_city(city: str, state: str = None):
    if city:
        fallback = KNOWN_CITY_COORDS.get(city.strip().lower())
        if fallback:
            return fallback

    if not GOOGLE_MAPS_API_KEY or not city:
        return None, None

    query = city.strip()
    if state:
        query = f"{city}, {state}"

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": query,
        "key": GOOGLE_MAPS_API_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        print("[GEOCODE_CITY_QUERY]", query, flush=True)
        print("[GEOCODE_CITY_RESPONSE]", data, flush=True)

        status = data.get("status")
        if status != "OK":
            return None, None

        results = data.get("results", []) or []
        if not results:
            return None, None

        location = results[0].get("geometry", {}).get("location", {})
        return location.get("lat"), location.get("lng")

    except Exception as e:
        print("[GEOCODE_CITY_ERROR]", str(e), flush=True)
        return None, None






def rows_to_dicts(rows):
    output = []

    for row in rows or []:
        if isinstance(row, dict):
            output.append(row)
        else:
            output.append(dict(row))

    return output




def build_map_results(internal_results, external_results):
    map_results = []

    for l in internal_results:
        if l.get("latitude") is not None and l.get("longitude") is not None:
            map_results.append({
                "name": l.get("name"),
                "lat": l.get("latitude"),
                "lng": l.get("longitude"),
                "category": l.get("category"),
                "slug": l.get("slug"),
                "source": "internal",
                "website": l.get("website"),
            })

    for l in external_results:
        if l.get("latitude") is not None and l.get("longitude") is not None:
            map_results.append({
                "name": l.get("name"),
                "lat": l.get("latitude"),
                "lng": l.get("longitude"),
                "category": l.get("category"),
                "slug": None,
                "source": "google",
                "website": l.get("website"),
            })

    return map_results



def summarize_ai_results(user_message, internal_results, external_results):
    combined = internal_results[:3] + external_results[:3]

    if not combined:
        return "I couldn’t find a strong match for that search yet. Try a broader phrase like coffee, dentist, or gym."

    names = [r.get("name") for r in combined if r.get("name")]
    top_names = ", ".join(names[:3])

    return f'I found a few strong options for "{user_message}". A good place to start is {top_names}.'


def get_active_ads(placement: str, limit: int = 10):
    return query_all(
        """
        SELECT *
        FROM ads
        WHERE is_active = 1
          AND placement = :placement
        ORDER BY sort_order ASC, id DESC
        LIMIT :limit
        """,
        {
            "placement": placement,
            "limit": limit
        }
    )


def map_context(map_lat, map_lng, map_results):
    return {
        "center": {
            "lat": map_lat,
            "lng": map_lng
        },
        "results": map_results or []
    }
    
    
 


def category_icon(category: str) -> str:
    if not category:
        return "📍"

    category = category.lower().strip()

    icons = {
        "coffee_shop": "☕",
        "coffee": "☕",
        "restaurant": "🍔",
        "bar": "🍸",
        "bar_and_grill": "🍸",
        "gym": "💪",
        "dentist": "🦷",
        "doctor": "🩺",
        "pharmacy": "💊",
        "plumber": "🔧",
        "electrician": "⚡",
        "hair_salon": "💇",
        "beauty_salon": "💅",
        "spa": "🧖",
        "bakery": "🥐",
        "cafe": "☕",
        "hotel": "🏨",
        "real_estate_agency": "🏠",
        "car_repair": "🚗",
        "school": "🏫",
    }

    return icons.get(category, "📍")

   
app.jinja_env.globals.update(category_icon=category_icon)
app.jinja_env.globals.update(
    category_icon=category_icon,
    map_context=map_context
)


def categories_match(query: str, category: str) -> bool:
    q = (query or "").lower()
    c = (category or "").lower()

    category_map = {
        "coffee": ["coffee", "coffee shop", "cafe", "cafes"],
        "coffee shop": ["coffee", "coffee shop", "cafe", "cafes"],
        "restaurant": ["restaurant", "restaurants", "pizza", "burger", "food"],
        "barber": ["barber", "barbershop", "hair salon"],
        "gym": ["gym", "fitness", "workout"],
    }

    for canonical, keywords in category_map.items():
        if q == canonical or q in keywords:
            return c in keywords or c == canonical

    return q in c





def google_place_photo_uri(photo_name: str, max_width: int = 600):
    """
    Convert a Google Places photo resource name into a displayable photo URL.
    Example photo_name:
    places/ChIJ.../photos/AeeoHc...
    """
    if not GOOGLE_MAPS_API_KEY or not photo_name:
        return None

    url = f"https://places.googleapis.com/v1/{photo_name}/media"

    headers = {
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY
    }

    params = {
        "maxWidthPx": max_width,
        "skipHttpRedirect": "true"
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)

        if not r.ok:
            print("[GOOGLE_PHOTO_STATUS]", r.status_code, flush=True)
            print("[GOOGLE_PHOTO_BODY]", r.text, flush=True)
            r.raise_for_status()

        data = r.json()
        return data.get("photoUri")

    except Exception as e:
        print("[GOOGLE_PHOTO_ERROR]", str(e), flush=True)
        return None



def google_place_photo_uri(photo_name: str, max_width: int = 600):
    """
    photo_name looks like:
    places/PLACE_ID/photos/PHOTO_REFERENCE
    """
    if not GOOGLE_MAPS_API_KEY or not photo_name:
        return None

    url = f"https://places.googleapis.com/v1/{photo_name}/media"

    headers = {
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY
    }

    params = {
        "maxWidthPx": max_width,
        "skipHttpRedirect": "true"
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)

        if not r.ok:
            print("[GOOGLE_PHOTO_STATUS]", r.status_code, flush=True)
            print("[GOOGLE_PHOTO_BODY]", r.text, flush=True)
            r.raise_for_status()

        data = r.json()
        return data.get("photoUri")

    except Exception as e:
        print("[GOOGLE_PHOTO_ERROR]", str(e), flush=True)
        return None






def seed_directory_pages():
    cities = [
        ("Naperville", "IL"),
        ("Wellington", "FL"),
        ("West Palm Beach", "FL"),
        ("Aurora", "IL"),
        ("Plainfield", "IL"),
        ("Boca Raton", "FL"),
        ("Joliet", "IL"),
        ("Orlando", "FL"),
        ("Tampa", "FL"),
        ("Miami", "FL"),
        ("Chicago", "IL"),
        ("Downers Grove", "IL"),
        ("Elmhurst", "IL"),
        ("St. Charles", "IL"),
        ("Bolingbrook", "IL"),
        ("Lisle", "IL"),
        ("Palm Beach Gardens", "FL"),
        ("Lake Worth", "FL"),
        ("Delray Beach", "FL"),
        ("Fort Lauderdale", "FL"),
    ]

    categories = [
        ("restaurant", "Restaurants"),
        ("coffee", "Coffee Shops"),
        ("dentist", "Dentists"),
        ("gym", "Gyms"),
        ("plumber", "Plumbers"),
    ]

    with get_conn() as conn:
        for city, state in cities:
            for category_slug, category_label in categories:
                slug = slugify(f"{category_label} in {city} {state}")
                title = f"Best {category_label} in {city}, {state}"
                description = f"Find the best {category_label.lower()} in {city}, {state}. Browse local listings and discover nearby businesses."
                hero_title = f"Best {category_label} in {city}, {state}"
                tag_title = f"{city}, {state}"

                execute(
    """
    INSERT INTO pages (
        slug,
        title,
        description,
        template,
        status,
        tag_title,
        hero_title,
        created_at,
        updated_at
    )
    VALUES (
        :slug,
        :title,
        :description,
        'landing_default',
        'published',
        :tag_title,
        :hero_title,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    )
    ON CONFLICT (slug) DO NOTHING
    """,
    {
        "slug": slug,
        "title": title,
        "description": description,
        "tag_title": tag_title,
        "hero_title": hero_title
    }
)

                page = conn.execute("SELECT id FROM pages WHERE slug = ?", (slug,)).fetchone()
                if not page:
                    continue

                intro_text = f"Explore top {category_label.lower()} in {city}, {state}. Compare local options and find a business near you."

                conn.execute("""
                    INSERT OR IGNORE INTO directory_page_meta
                    (page_id, city, state, category, intro_text)
                    VALUES (?, ?, ?, ?, ?)
                """, (page["id"], city, state, category_slug, intro_text))

        conn.commit()







def merge_search_results(internal_results, google_results, max_internal=10, max_google=10):
    return {
        "internal": internal_results[:max_internal],
        "external": google_results[:max_google],
    }



def response_text(resp) -> str:
    if resp is None:
        return ""

    t = getattr(resp, "output_text", None)
    if isinstance(t, str) and t.strip():
        return t.strip()

    out = getattr(resp, "output", None) or []
    chunks = []

    for item in out:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        content = content or []

        for part in content:
            ptype = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            ptext = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)

            # Handle nested text shapes like {"text": {"value": "..."}} if they occur
            if isinstance(ptext, dict):
                ptext = ptext.get("value") or ptext.get("text") or ""

            if ptype in ("output_text", "text") and isinstance(ptext, str):
                chunks.append(ptext)

    return "".join(chunks).strip()



def generate_ai_payload_and_save(page_id: int, topic: str, audience: str, tone: str) -> dict:
    start = time.time()

    facts = retrieve_facts(topic)
    sources_block = "\n".join(
        f"[source:{f['id']}] {f['title']} ({f.get('url','')})\n"
        f"{(f['snippet'][:280] + '…') if len(f['snippet']) > 280 else f['snippet']}"
        for f in facts
    ) or "NO_SOURCES_AVAILABLE"

    prompt = f"""
Topic: {topic}
Audience: {audience}
Tone: {tone}

SOURCES (use these only for factual claims):
{sources_block}

Return JSON ONLY matching the provided schema.
No markdown. No extra text.
""".strip()

    MAX_SECTION_IMAGES = 0
    HERO_IMAGE_SIZE = "1024x1024"
    SECTION_IMAGE_SIZE = "512x512"

    last_err = None
    resp = None
    raw = ""
    data = None

    # ----------------------------
    # 1) Generate + parse BLOG PLAN (plan only)
    # ----------------------------
    plan = None
    last_err = None
    raw = ""

    max_out = 3000  # will bump if we hit max_output_tokens

    for i in range(3):
        try:
            resp = client.responses.create(
                model="gpt-5-mini",
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert SEO content strategist.\n"
                            "Return VALID JSON only matching the provided schema.\n"
                            "No markdown. No explanations.\n"
                            "Do not invent facts.\n"
                            "Use SOURCES only for factual claims.\n"
                            "If SOURCES are insufficient, still return valid JSON and note the claim cannot be verified.\n"
                            "For every outline item, ALWAYS include subheadings (use [] if none).\n"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=max_out,
                reasoning={"effort": "low"},
                text={"format": BLOG_PLAN_FORMAT},
                store=False,
            )

            status = getattr(resp, "status", None)
            incomplete = getattr(resp, "incomplete_details", None)
            reason = getattr(incomplete, "reason", None) if incomplete else None

            if status == "incomplete" and reason == "max_output_tokens":
                last_err = f"Response incomplete due to max_output_tokens (max_out={max_out})"
                max_out = min(max_out + 1500, 8000)  # bump for next attempt
                _retry_sleep(i)
                continue

            raw = response_text(resp)
            if not raw:
                last_err = "Empty model output"
                print("[AI_EMPTY_OUTPUT] resp.output=", getattr(resp, "output", None), flush=True)
                print("[AI_EMPTY_OUTPUT] resp=", resp, flush=True)
                _retry_sleep(i)
                continue

            try:
                plan = safe_json_loads(raw)
                break
            except Exception as e:
                last_err = f"Plan JSON parse failed: {e}"
                if looks_truncated_json(raw):
                    _retry_sleep(i)
                    continue
                break

        except (RateLimitError, APITimeoutError, APIError) as e:
            last_err = str(e)
            _retry_sleep(i)

    if plan is None:
        raise RuntimeError(
            f"Model did not return valid JSON. detail={last_err} raw_head={raw[:200]!r}"
        )
    
    
    
    # ----------------------------
    # 2) Generate sections from plan (section-by-section)
    # ----------------------------
    sections_out = []

    for idx, item in enumerate(plan.get("outline", []), start=1):
        heading = (item.get("heading") or "").strip()
        goal = (item.get("goal") or "").strip()
        subheads = item.get("subheadings") or []
        subheads_txt = "\n".join(f"- {h}" for h in subheads) if subheads else "None"

        section_prompt = f"""
Write ONE blog section in JSON.

Topic: {topic}
Audience: {audience}
Tone: {tone}

SOURCES (use these only for factual claims):
{sources_block}

Section heading: {heading}
Goal: {goal}
Subheadings:
{subheads_txt}

Rules:
- Output VALID JSON only matching the schema.
- Body should be 300–400 words.
- Use Markdown in the body.
- Use bullet lists where helpful.
- Do not repeat content from other sections.
- If sources are insufficient, still write valid JSON and state that a claim cannot be verified.
""".strip()

        sec_data = None
        sec_raw = ""
        sec_err = None

        for attempt in range(3):
            try:
                sec_resp = client.responses.create(
                    model="gpt-5-mini",
                    input=[
                        {"role": "system", "content": "You write a single blog section as strict JSON."},
                        {"role": "user", "content": section_prompt},
                    ],
                    max_output_tokens=2000,
                    text={"format": BLOG_SECTION_FORMAT},
                    store=False,
                )

                sec_raw = response_text(sec_resp)
                if not sec_raw:
                    sec_err = "Empty model output"
                    _retry_sleep(attempt)
                    continue

                sec_data = safe_json_loads(sec_raw)
                sec_data["body"] = normalize_bullets(sec_data.get("body", ""))

                sections_out.append(sec_data)
                break

            except Exception as e:
                sec_err = e
                _retry_sleep(attempt)

        if sec_data is None:
            raise RuntimeError(
                "Section generation failed. "
                f"detail={sec_err} "
                f"raw_head={sec_raw[:300]!r} "
                f"raw_tail={sec_raw[-300:]!r} "
                f"failed_section_heading={heading!r}"
            )

    # Add FAQ as a final section
    faq_items = plan.get("faq") or []
    if faq_items:
        faq_md = "\n".join([
            f"**{q.get('question','').strip()}**\n\n{q.get('answer','').strip()}\n"
            for q in faq_items
        ])

        sections_out.append({
            "section_type": "heading_paragraph",
            "heading": "FAQ",
            "body": normalize_bullets(faq_md.strip())
        })

    data = {
        "title": plan["title"],
        "description": plan["meta_description"],
        "tag_title": plan["title"],
        "hero_title": plan["hero_title"],
        "sections": sections_out,
    }

    # 2) Moderation
    mod = client.moderations.create(
        model="omni-moderation-latest",
        input=(
            (data.get("title") or "") + "\n" +
            (data.get("description") or "") + "\n" +
            "\n".join((s.get("body") or "") for s in data.get("sections", []))
        )
    )
    if mod.results[0].flagged:
        raise RuntimeError("AI output flagged by moderation.")

    # 3) Hero image
    hero_img = client.images.generate(
        model="gpt-image-1",
        prompt=(
            f"Hero image for a website landing page about: {data['title']}.\n"
            f"Audience: {audience}\n"
            f"Tone: {tone}\n"
            "Style: clean, modern, professional, high quality.\n"
            "No text on the image."
        ),
        size=HERO_IMAGE_SIZE,
    )
    hero_url = save_b64_png_to_uploads(hero_img.data[0].b64_json, prefix="hero")
    
    

    # 4) Save to DB (your existing logic)
    sections_in = data.get("sections", []) or []
    with get_conn() as conn:
        conn.execute("""
    UPDATE pages
    SET slug=?, title=?, description=?, tag_title=?, hero_title=?, updated_at=?
    WHERE id=?
""", (
    plan["slug"],              # <- add this
    data["title"],
    data["description"],
    data.get("tag_title", ""),
    data.get("hero_title", ""),
    datetime.utcnow().isoformat(),
    page_id,
))

        conn.execute("DELETE FROM sections WHERE page_id=?", (page_id,))
        sort_order = 0

        # hero image section
        conn.execute("""
            INSERT INTO sections (
                page_id, sort_order, section_type, heading, body,
                media_path, media_alt, media_caption, updated_at
            ) VALUES (?, ?, 'image', ?, ?, ?, ?, ?, ?)
        """, (
            page_id, sort_order,
            "", "",
            hero_url, data["title"], "AI generated hero image",
            datetime.utcnow().isoformat()
        ))
        sort_order += 1

        for s in sections_in:
            conn.execute("""
                INSERT INTO sections (page_id, sort_order, section_type, heading, body, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                page_id, sort_order,
                (s.get("section_type") or "paragraph"),
                (s.get("heading") or ""),
                normalize_bullets(s.get("body", "")),
                datetime.utcnow().isoformat()
            ))
            sort_order += 1

        conn.commit()

    return {
        "ok": True,
        "page_id": page_id,
        "seconds": round(time.time() - start, 1),
        "hero_image_url": hero_url,
    }
    
    
    



def _run_ai_job(job_id: str, page_id: int, topic: str, audience: str, tone: str):
    AI_JOBS[job_id]["status"] = "running"
    try:
        # Call your existing logic here.
        # Easiest approach: move your existing admin_ai_generate body
        # (the heavy part) into a function that RETURNS the JSON payload.
        result_payload = generate_ai_payload_and_save(page_id, topic, audience, tone)

        AI_JOBS[job_id]["status"] = "done"
        AI_JOBS[job_id]["result"] = result_payload
    except Exception as e:
        AI_JOBS[job_id]["status"] = "error"
        AI_JOBS[job_id]["error"] = str(e)
        AI_JOBS[job_id]["traceback"] = traceback.format_exc()

        # Print to terminal reliably
        print("[AI_JOB_ERROR]", str(e), flush=True)
        print(AI_JOBS[job_id]["traceback"], flush=True)




def response_text(resp) -> str:
    """
    Extract text from an OpenAI Responses API response across SDK shapes.
    """
    if resp is None:
        return ""

    t = getattr(resp, "output_text", None)
    if isinstance(t, str) and t.strip():
        return t.strip()

    out = getattr(resp, "output", None) or []
    chunks = []

    for item in out:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        content = content or []

        for part in content:
            ptype = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            ptext = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)

            if ptype in ("output_text", "text") and isinstance(ptext, str):
                chunks.append(ptext)

    return "".join(chunks).strip()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS





def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def normalize_params(params):
    if params is None:
        return {}

    if isinstance(params, dict):
        return params

    raise ValueError(
        "Tuple/list SQL params are no longer supported. "
        "Use named params like :id with {'id': value}."
    )


def query_all(sql, params=None):
    params = normalize_params(params)

    with engine.connect() as conn:
        result = conn.execute(sql_text(sql), params)
        return [dict(row._mapping) for row in result.fetchall()]


def query_one(sql, params=None):
    params = normalize_params(params)

    with engine.connect() as conn:
        row = conn.execute(sql_text(sql), params).first()
        return dict(row._mapping) if row else None


def execute(sql, params=None):
    params = normalize_params(params)

    with engine.begin() as conn:
        result = conn.execute(sql_text(sql), params)

        try:
            return result.scalar_one_or_none()
        except Exception:
            return None


def get_conn():
    return engine.begin()


def next_sort_order(page_id: int) -> int:
    row = query_one("SELECT COALESCE(MAX(sort_order), -1) AS m FROM sections WHERE page_id=?", (page_id,))
    return int(row["m"]) + 1


def retrieve_facts(topic: str, limit: int = 8):
    rows = query_all("""
        SELECT id, title, url, snippet
        FROM facts
        WHERE topic LIKE ? OR title LIKE ?
        ORDER BY id DESC
        LIMIT ?
    """, (f"%{topic}%", f"%{topic}%", limit))
    return [dict(r) for r in rows]


def create_admin_if_missing(email: str, password: str):
    email = email.strip().lower()
    existing = query_one("SELECT id FROM users WHERE email=?", (email,))
    if existing:
        return

    pw_hash = generate_password_hash(password)
    execute(
        "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
        (email, pw_hash)
    )


def save_b64_png_to_uploads(b64_png: str, prefix: str = "ai") -> str:
    filename = f"{prefix}_{uuid.uuid4().hex}.png"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(b64_png))
    return f"/static/uploads/{filename}"


def normalize_bullets(md: str) -> str:
    if not md:
        return md
    md = re.sub(r"\s*-\s+", "\n- ", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


@app.template_filter("md")
def md_filter(text):
    return markdown.markdown(text or "", extensions=["extra"])


def _retry_sleep(i: int):
    time.sleep((2 ** i) + random.random())


def looks_truncated_json(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return True

    # Must start like JSON
    if not s.startswith("{"):
        return False

    # Unbalanced braces/brackets => truncated
    if s.count("{") > s.count("}"):
        return True
    if s.count("[") > s.count("]"):
        return True

    # Unterminated string => truncated
    if s.count('"') % 2 == 1:
        return True

    # BIG ONE for your logs: missing the final "]}"
    if not s.endswith("}"):
        return True
    if '"sections"' in s and not s.rstrip().endswith("]}"):
        # Many valid JSON objects end with "}" not "]}", so we only enforce this
        # when sections exists (like your schema).
        if not s.rstrip().endswith("]}"):
            # Could be "... ]\n}" too; allow whitespace between
            if not re.search(r"\]\s*\}\s*$", s):
                return True

    return False




def safe_json_loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        # attempt repair
        raw = raw.replace("'", '"')
        return json.loads(raw)








BLOG_PLAN_FORMAT = {
    "type": "json_schema",
    "name": "blog_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "meta_description", "slug", "hero_title", "outline", "faq"],
        "properties": {
            "title": {"type": "string"},
            "meta_description": {"type": "string"},
            "slug": {"type": "string"},
            "hero_title": {"type": "string"},
                "outline": {
                    "type": "array",
                    "minItems": 5,
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "heading", "goal", "subheadings"],
                        "properties": {
                            "id": {"type": "string"},
                            "heading": {"type": "string"},
                            "goal": {"type": "string"},
                            "subheadings": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
            "faq": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question", "answer"],
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"}
                    }
                }
            }
        }
    }
}

BLOG_SECTION_FORMAT = {
    "type": "json_schema",
    "name": "blog_section",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["section_type", "heading", "body"],
        "properties": {
            "section_type": {"type": "string", "enum": ["heading_paragraph"]},
            "heading": {"type": "string"},
            "body": {"type": "string"}
        }
    }
}


import requests
from flask import jsonify, request, abort

SERPER_API_KEY = os.environ.get("SERPER_API_KEY")


def serper_search(query: str, num: int = 5):
    api_key = os.environ.get("SERPER_API_KEY")  # read at call-time
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is not set")

    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": num}

    r = requests.post(url, headers=headers, json=payload, timeout=30)

    if not r.ok:
        # show Serper’s actual message
        raise RuntimeError(f"Serper HTTP {r.status_code}: {r.text}")

    return r.json()



GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

print("GOOGLE_MAPS_API_KEY loaded:", bool(GOOGLE_MAPS_API_KEY), flush=True)
print("GOOGLE_MAPS_API_KEY first 8:", (GOOGLE_MAPS_API_KEY or "")[:8], flush=True)


from math import radians, sin, cos, sqrt, atan2

def haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)

    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return r * c


import math


def haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def search_internal_listings(
    q: str = "",
    lat: float = None,
    lng: float = None,
    limit: int = 20,
    max_distance_miles: float = 50
):
    sql = """
        SELECT
            id,
            name,
            slug,
            category,
            city,
            state,
            address,
            phone,
            website,
            description,
            featured,
            latitude,
            longitude,
            COALESCE(NULLIF(photo_url, ''), NULLIF(card_image_url, '')) AS photo_url,
            photo_urls_json,
            card_image_url
        FROM listings
        WHERE status = 'published'
    """

    params = {}

    q_lower = (q or "").lower().strip()

    if q_lower:
        sql += """
            AND (
                LOWER(name) LIKE :term
                OR LOWER(category) LIKE :term
                OR LOWER(description) LIKE :term
                OR LOWER(city) LIKE :term
            )
        """
        params["term"] = f"%{q_lower}%"

    sql += """
        ORDER BY featured DESC, name ASC
        LIMIT :limit
    """
    params["limit"] = limit * 4

    results = query_all(sql, params)
    results = rows_to_dicts(results)

    filtered_results = []

    for item in results:
        item["distance_miles"] = None

        item_lat = item.get("latitude")
        item_lng = item.get("longitude")

        if (
            lat is not None
            and lng is not None
            and item_lat is not None
            and item_lng is not None
        ):
            try:
                item["distance_miles"] = round(
                    haversine_miles(
                        float(lat),
                        float(lng),
                        float(item_lat),
                        float(item_lng)
                    ),
                    1
                )
            except Exception:
                item["distance_miles"] = None

        score = 0

        if q_lower:
            name = (item.get("name") or "").lower()
            category = (item.get("category") or "").lower()
            description = (item.get("description") or "").lower()
            city_text = (item.get("city") or "").lower()

            if q_lower in category:
                score += 5
            if q_lower in name:
                score += 4
            if q_lower in description:
                score += 2
            if q_lower in city_text:
                score += 1

        item["relevance_score"] = score

        if lat is not None and lng is not None:
            if (
                item["distance_miles"] is not None
                and item["distance_miles"] <= max_distance_miles
            ):
                filtered_results.append(item)
        else:
            filtered_results.append(item)

    if lat is not None and lng is not None:
        filtered_results.sort(
            key=lambda x: (
                -(x.get("relevance_score", 0)),
                -int(x.get("featured", 0)),
                x["distance_miles"] if x["distance_miles"] is not None else 999999,
                (x.get("name") or "").lower()
            )
        )
    else:
        filtered_results.sort(
            key=lambda x: (
                -(x.get("relevance_score", 0)),
                -int(x.get("featured", 0)),
                (x.get("name") or "").lower()
            )
        )

    return filtered_results[:limit]


def is_relevant_featured_result(item, query: str, searched_city: str = None, max_featured_distance: float = 10):
    item_category = (item.get("category") or "").lower()
    item_name = (item.get("name") or "").lower()
    item_description = (item.get("description") or "").lower()
    item_city = (item.get("city") or "").lower()
    q = (query or "").lower().strip()

    text_blob = f"{item_name} {item_category} {item_description}"

    query_matches = q in text_blob if q else True
    city_matches = True

    if searched_city:
        city_matches = item_city == searched_city.lower()

    distance_matches = True
    if item.get("distance_miles") is not None:
        distance_matches = float(item["distance_miles"]) <= max_featured_distance

    return query_matches and city_matches and distance_matches




@app.post("/friends/<int:user_id>/add")
@login_required
def add_friend(user_id):
    if user_id == current_user.id:
        flash("You cannot add yourself.")
        return redirect(request.referrer or url_for("account"))

    existing = query_one(
        """
        SELECT id
        FROM user_friends
        WHERE user_id = :user_id
          AND friend_id = :friend_id
        """,
        {
            "user_id": current_user.id,
            "friend_id": user_id
        }
    )

    if not existing:
        execute(
            """
            INSERT INTO user_friends (
                user_id,
                friend_id,
                status,
                created_at
            )
            VALUES (
                :user_id,
                :friend_id,
                'pending',
                CURRENT_TIMESTAMP
            )
            """,
            {
                "user_id": current_user.id,
                "friend_id": user_id
            }
        )

    flash("Friend request sent.")
    return redirect(request.referrer or url_for("account"))


@app.get("/messages/<int:user_id>")
@login_required
def messages_with_user(user_id):
    other_user = User.query.get_or_404(user_id)

    return render_template(
        "messages.html",
        other_user=other_user
    )





from flask import request, jsonify
from flask_login import current_user


@app.post("/ai-chat")
def ai_chat():
    try:
        data = request.get_json(silent=True) or {}
        message = (data.get("message") or "").strip()
        lat = data.get("lat")
        lng = data.get("lng")

        if not message:
            return jsonify({"error": "Message is required."}), 400

        user_id = current_user.id if current_user.is_authenticated else None

 

        conversation = load_or_create_conversation(user_id=user_id)
        conversation_id = conversation["id"]

        save_message(conversation_id, "user", message)

        state = load_state(conversation)
        state = maybe_reset_state_for_new_topic(state, message)

        if is_broad_query(message):
            state["category"] = detect_category_from_message(message) or state.get("category")
            state["vibe"] = None
            state["purpose"] = None
            state["last_followup"] = None

        state = update_state_from_message(state, message, lat=lat, lng=lng)

        ask_followup, followup_question, followup_key = should_ask_followup(state)

        if ask_followup:
            if state.get("last_followup") == followup_key:
                if followup_key == "food_vibe":
                    followup_question = "Would you prefer something casual, quick, lively, or more date-night?"
                elif followup_key == "coffee_vibe":
                    followup_question = "Do you want the best coffee, somewhere quiet to work, or something quick?"
                elif followup_key == "location":
                    followup_question = "What area are you in so I can narrow it down?"
                elif followup_key == "category":
                    followup_question = "What type of place are you in the mood for?"

            state["last_followup"] = followup_key
            save_state(conversation_id, state)
            save_message(conversation_id, "assistant", followup_question)

            return jsonify({
                "conversation_id": conversation_id,
                "reply": followup_question,
                "summary": followup_question,
                "needs_clarification": True,
                "results": [],
                "featured_internal": [],
                "regular_internal": [],
                "external_results": [],
                "state": state
            })

        state["last_followup"] = None
        save_state(conversation_id, state)

        searched_city = (state.get("city") or "").strip() or None
        user_lat, user_lng, location_source = resolve_search_location(state, lat=lat, lng=lng)

        if user_lat is None or user_lng is None:
            no_location_reply = "What city or area are you in? I need a location to narrow it down."
            save_message(conversation_id, "assistant", no_location_reply)
            return jsonify({
                "conversation_id": conversation_id,
                "reply": no_location_reply,
                "summary": no_location_reply,
                "needs_clarification": True,
                "results": [],
                "featured_internal": [],
                "regular_internal": [],
                "external_results": [],
                "state": state
            })

        # Simpler queries for better matches
        category = (state.get("category") or "").strip().lower()
        vibe = (state.get("vibe") or "").strip().lower()
        purpose = (state.get("purpose") or "").strip().lower()

        internal_query = category
        google_query = category

        if category == "coffee":
            internal_query = "coffee"
            if purpose == "work" or vibe == "quiet":
                google_query = "quiet coffee shop"
            elif purpose == "quick":
                google_query = "quick coffee shop"
            elif purpose == "best":
                google_query = "best coffee shop"
            else:
                google_query = "coffee shop"

        elif category in ["restaurants", "restaurant"]:
            internal_query = "restaurant"
            if vibe == "date-night":
                google_query = "date night restaurant"
            elif purpose == "quick":
                google_query = "quick restaurant"
            else:
                google_query = "restaurant"

        elif category == "bars":
            internal_query = "bar"
            if vibe == "lively":
                google_query = "lively bar"
            else:
                google_query = "bar"

        elif category == "shopping":
            internal_query = "shopping"
            google_query = "shopping"

        elif not category:
            internal_query = build_query_from_state(state)
            google_query = internal_query

        internal_results = []
        external_results = []

        try:
            internal_results = search_internal_listings(
                q=internal_query,
                lat=user_lat,
                lng=user_lng,
                limit=20
            )
        except Exception as e:
            print("[AI_CHAT_INTERNAL_ERROR]", str(e), flush=True)
            internal_results = []

        try:
            if google_query:
                external_results = google_places_text_search(
                    query=google_query,
                    lat=user_lat,
                    lng=user_lng,
                    limit=20
                )
        except Exception as e:
            print("[AI_CHAT_EXTERNAL_ERROR]", str(e), flush=True)
            external_results = []

        print("[AI_CHAT_CATEGORY]", category, flush=True)
        print("[AI_CHAT_INTERNAL_QUERY]", internal_query, flush=True)
        print("[AI_CHAT_GOOGLE_QUERY]", google_query, flush=True)
        print("[AI_CHAT_INTERNAL_COUNT]", len(internal_results), flush=True)
        print("[AI_CHAT_EXTERNAL_COUNT]", len(external_results), flush=True)

        featured_internal, regular_internal, external_results, results = bucket_results(
            internal_results=internal_results,
            external_results=external_results,
            query=internal_query or google_query,
            searched_city=searched_city
        )

        reply = build_recommendation_reply(state, results)
        save_message(conversation_id, "assistant", reply)

        return jsonify({
            "conversation_id": conversation_id,
            "reply": reply,
            "summary": reply,
            "needs_clarification": False,
            "results": results,
            "featured_internal": featured_internal,
            "regular_internal": regular_internal,
            "external_results": external_results,
            "map_center": {
                "lat": user_lat,
                "lng": user_lng
            },
            "location_source": location_source,
            "searched_city": searched_city,
            "searched_state": None,
            "state": state
        })

    
    except Exception as e:
        print("[AI_CHAT_ERROR]", str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"error": str(e)}), 500
    
    
@app.post("/lists/<int:list_id>/save")
@login_required
def save_public_list(list_id):
    saved_list = SavedList.query.filter_by(
        id=list_id,
        is_public=True
    ).first_or_404()

    existing = UserSavedList.query.filter_by(
        user_id=current_user.id,
        saved_list_id=saved_list.id
    ).first()

    if not existing:
        db.session.add(
            UserSavedList(
                user_id=current_user.id,
                saved_list_id=saved_list.id
            )
        )
        db.session.commit()

    flash("List saved.")
    return redirect(request.referrer or url_for("account"))


@app.post("/lists/<int:list_id>/unsave")
@login_required
def unsave_public_list(list_id):
    existing = UserSavedList.query.filter_by(
        user_id=current_user.id,
        saved_list_id=list_id
    ).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()

    flash("List removed.")
    return redirect(request.referrer or url_for("account"))   
    

    
    
    
from flask import url_for
import os
import uuid
from werkzeug.utils import secure_filename
from io import BytesIO




@app.post("/feed/create")
@login_required
def create_feed_post():
    body = (request.form.get("body") or "").strip()
    city = (request.form.get("city") or "").strip()
    photo = request.files.get("photo")
    image_url = None

    if photo and photo.filename:
        photo_bytes = photo.read()

        print("[FEED_PHOTO_NAME]", photo.filename, flush=True)
        print("[FEED_PHOTO_CONTENT_TYPE]", photo.content_type, flush=True)
        print("[FEED_PHOTO_BYTES]", len(photo_bytes), flush=True)

        if len(photo_bytes) == 0:
            flash("The uploaded image was empty. Please choose another photo.")
            return redirect(url_for("feed"))

        content_type = photo.content_type or "image/jpeg"
        encoded_photo = base64.b64encode(photo_bytes).decode("utf-8")
        data_uri = f"data:{content_type};base64,{encoded_photo}"

        upload_result = cloudinary.uploader.upload(
            data_uri,
            folder="localai/feed",
            resource_type="image"
        )

        image_url = upload_result["secure_url"]

    if not body and not image_url:
        flash("Write something or upload a photo.")
        return redirect(url_for("feed"))

    execute(
        """
        INSERT INTO feed_posts (
            user_id,
            post_type,
            body,
            image_url,
            city,
            is_public
        )
        VALUES (
            :user_id,
            :post_type,
            :body,
            :image_url,
            :city,
            1
        )
        """,
        {
            "user_id": current_user.id,
            "post_type": "photo" if image_url else "text",
            "body": body or None,
            "image_url": image_url,
            "city": city or None
        }
    )

    return redirect(url_for("feed"))





@app.post("/account/profile-photo")
@login_required
def update_profile_photo():
    profile_photo = request.files.get("profile_photo")

    if not profile_photo or not profile_photo.filename:
        flash("Please choose a photo.")
        return redirect(url_for("account"))

    profile_photo.stream.seek(0)

    upload_result = cloudinary.uploader.upload(
        profile_photo.stream,
        folder="localai/profiles",
        resource_type="image"
    )

    current_user.profile_image_url = upload_result["secure_url"]

    db.session.commit()

    flash("Profile photo updated.")
    return redirect(url_for("account"))
    
    
    
@app.post("/feed/<int:post_id>/comment")
@login_required
def comment_feed_post(post_id):
    body = (request.form.get("body") or "").strip()

    if not body:
        return redirect(url_for("feed"))

    execute(
        """
        INSERT INTO feed_post_comments (
            post_id,
            user_id,
            body
        )
        VALUES (
            :post_id,
            :user_id,
            :body
        )
        """,
        {
            "post_id": post_id,
            "user_id": current_user.id,
            "body": body
        }
    )

    return redirect(url_for("feed"))
    
    
    
    
    
@app.post("/ai-chat/reset")
def reset_ai_chat():
    user_id = current_user.id if current_user.is_authenticated else None
    session_id = get_or_create_session_id()

    with engine.begin() as conn:
        if user_id:
            conn.execute(
                sql_text("""
                    DELETE FROM conversation_messages
                    WHERE conversation_id IN (
                        SELECT id
                        FROM conversations
                        WHERE user_id = :user_id
                    )
                """),
                {"user_id": user_id}
            )

            conn.execute(
                sql_text("""
                    DELETE FROM conversations
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id}
            )
        else:
            conn.execute(
                sql_text("""
                    DELETE FROM conversation_messages
                    WHERE conversation_id IN (
                        SELECT id
                        FROM conversations
                        WHERE session_id = :session_id
                    )
                """),
                {"session_id": session_id}
            )

            conn.execute(
                sql_text("""
                    DELETE FROM conversations
                    WHERE session_id = :session_id
                """),
                {"session_id": session_id}
            )

    session.pop("chat_session_id", None)

    return jsonify({"ok": True})







@app.post("/ai-search")
def ai_search():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({"error": "Message is required."}), 400

        intent = call_llm_for_search_intent(user_message)

        query = (intent.get("query") or "").strip()
        use_current_location = bool(intent.get("use_current_location", True))
        city = (intent.get("city") or "").strip() or None
        state = (intent.get("state") or "").strip() or None

        lat = data.get("lat")
        lng = data.get("lng")

        user_lat = None
        user_lng = None
        location_source = "fallback"

        # 1. If user named a city, always use that first
        if city:
            city_lat, city_lng = geocode_city(city, state)

            if city_lat is not None and city_lng is not None:
                user_lat = city_lat
                user_lng = city_lng
                location_source = "city"
            else:
                return jsonify({
                    "error": f'Could not resolve location for "{city}".'
                }), 400

        # 2. Only use browser location when NO city was named
        elif use_current_location:
            try:
                if lat is not None and lng is not None:
                    user_lat = float(lat)
                    user_lng = float(lng)
                    location_source = "browser"
            except (TypeError, ValueError):
                user_lat = None
                user_lng = None

        # 3. Final fallback
        if user_lat is None or user_lng is None:
            return jsonify({
                "error": "Could not get your current location. Please allow location access or type a city."
            }), 400

        print("[AI_SEARCH_MESSAGE]", user_message, flush=True)
        print("[AI_SEARCH_INTENT]", intent, flush=True)
        print("[AI_SEARCH_FINAL_LOCATION]", {
            "lat": user_lat,
            "lng": user_lng,
            "location_source": location_source,
            "city": city,
            "state": state
        }, flush=True)

        internal_results = search_internal_listings(
            q=query,
            lat=user_lat,
            lng=user_lng,
            limit=20
        )

        print("[AI_SEARCH_INTERNAL_COUNT]", len(internal_results), flush=True)
        for r in internal_results[:5]:
            print("[AI_SEARCH_INTERNAL_ITEM]", {
                "name": r.get("name"),
                "city": r.get("city"),
                "state": r.get("state"),
                "distance_miles": r.get("distance_miles")
            }, flush=True)

        external_results = []
        if query:
            external_results = google_places_text_search(
                query=query,
                lat=user_lat,
                lng=user_lng,
                limit=20
            )

        print("[AI_SEARCH_EXTERNAL_COUNT]", len(external_results), flush=True)
        for r in external_results[:5]:
            print("[AI_SEARCH_EXTERNAL_ITEM]", {
                "name": r.get("name"),
                "address": r.get("address"),
                "distance_miles": r.get("distance_miles")
            }, flush=True)

        internal_results = rows_to_dicts(internal_results)
        external_results = rows_to_dicts(external_results)

        featured_internal = [
            r for r in internal_results
            if int(r.get("featured", 0)) == 1
            and is_relevant_featured_result(
                r,
                query=query,
                searched_city=city,
                max_featured_distance=10
            )
        ]

        regular_internal = [
            r for r in internal_results
            if r not in featured_internal
        ]

        # Build AI summary here
        featured_names = [
            r["name"] for r in featured_internal[:4]
            if r.get("name")
        ]

        regular_names = [
            r["name"] for r in regular_internal[:4]
            if r.get("name")
        ]

        external_names = [
            r["name"] for r in external_results[:4]
            if r.get("name")
        ]

        if featured_names:
            ai_summary = (
                f"If you're looking for good options nearby, I'd start with "
                f"{', '.join(featured_names)}. "
                f"These are some of the strongest featured spots in the area."
            )

            if regular_names:
                ai_summary += (
                    f" I also included nearby places like "
                    f"{', '.join(regular_names)} so you have more options."
                )

        elif regular_names:
            ai_summary = (
                f"A good place to start is {', '.join(regular_names)}. "
                f"I also included more nearby options below so you can compare what looks best."
            )

        elif external_names:
            ai_summary = (
                f"I found a few nearby places that I highly recommend checking out, including "
                f"{', '.join(external_names)}."
            )

        else:
            ai_summary = (
                "I couldn't find any strong nearby matches yet. "
                "Try a different category or a more specific city."
            )
            
            
        if "coffee" in query.lower():
            prefix = "If you're looking for coffee nearby, I'd start with "
        elif "restaurant" in query.lower():
            prefix = "If you're deciding where to eat, I'd start with "
        else:
            prefix = "If you're looking for good options nearby, I'd start with "

        map_results = []

        return jsonify({
            "message": user_message,
            "intent": intent,
            "summary": ai_summary,
            "featured_internal": featured_internal,
            "regular_internal": regular_internal,
            "external_results": external_results,
            "map_center": {
                "lat": user_lat,
                "lng": user_lng
            },
            "location_source": location_source,
            "searched_city": city,
            "searched_state": state,
            "map_results": map_results
        })

    except Exception as e:
        print("[AI_SEARCH_ERROR]", str(e), flush=True)
        return jsonify({"error": str(e)}), 500
    
    



    


@app.post("/admin/pages/<int:page_id>/refresh-sources")
def admin_refresh_sources(page_id):
    # Ensure page exists
    page = query_one(
    "SELECT * FROM pages WHERE id = :page_id",
    {"page_id": page_id}
)
    if not page:
        abort(404)

    topic = (request.form.get("topic") or page["title"] or "").strip()
    if not topic:
        return jsonify({"error": "Topic is required to fetch sources."}), 400

    try:
        results = serper_search(topic, num=8)
        organic = results.get("organic", []) or []

        inserted = 0
        facts = []

        with get_conn() as conn:
            for item in organic[:8]:
                title = (item.get("title") or "").strip()
                url = (item.get("link") or "").strip()
                snippet = (item.get("snippet") or "").strip()

                if not title or not snippet:
                    continue

                conn.execute(
                    """
                    INSERT INTO facts (topic, title, url, snippet, created_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (topic, title, url, snippet),
                )
                inserted += 1

                facts.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet
                })

            conn.commit()

        return jsonify({"ok": True, "inserted": inserted, "facts": facts}), 200

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except requests.HTTPError as e:
        return jsonify({"error": f"Serper HTTP error: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Source fetch failed: {type(e).__name__}: {e}"}), 500
    
    
    
    
    
    
    
# -----------------------
# Auth decorators
# -----------------------



def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if int(session.get("is_admin", 0)) != 1:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# -----------------------
# Routes
# -----------------------


import io
import qrcode
from flask import send_file



@app.get("/lists/<slug>/qr.png")
def public_list_qr_png(slug):
    saved_list = SavedList.query.filter_by(
        slug=slug,
        is_public=True
    ).first_or_404()

    share_url = url_for(
        "view_public_list",
        slug=saved_list.slug,
        _external=True
    )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )

    qr.add_data(share_url)
    qr.make(fit=True)

    img = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/png",
        download_name=f"{saved_list.slug}-qr.png"
    )





@app.get("/admin/seed-directory-pages")
def admin_seed_directory_pages():
    seed_directory_pages()
    return "Seeded directory pages."


@app.get("/d/<slug>")
def directory_page(slug):
    page = safe_query_one("""
        SELECT p.*, d.city, d.state, d.category, d.intro_text
        FROM pages p
        JOIN directory_page_meta d ON d.page_id = p.id
        WHERE p.slug = ? AND p.status = 'published'
    """, (slug,), label="DIRECTORY_PAGE_ERROR")

    if not page:
        abort(404)

    listings = safe_query_all("""
        SELECT *
        FROM listings
        WHERE status = 'published'
          AND LOWER(category) LIKE LOWER(?)
          AND LOWER(city) = LOWER(?)
          AND LOWER(state) = LOWER(?)
        ORDER BY featured DESC, name ASC
        LIMIT 50
    """, (
        f"%{page['category']}%",
        page["city"],
        page["state"]
    ), label="DIRECTORY_PAGE_LISTINGS_ERROR")

    return render_template(
        "directory_city_category.html",
        page=page,
        listings=listings
    )
    
    
    
    
    
@app.get("/feed")
def feed():
    posts = query_all("""
    SELECT
        p.*,
        COALESCE(NULLIF(u.name, ''), u.email, 'User') AS user_name,
        l.name AS listing_name,
        (
            SELECT COUNT(*)
            FROM feed_post_likes
            WHERE post_id = p.id
        ) AS like_count,
        (
            SELECT COUNT(*)
            FROM feed_post_comments
            WHERE post_id = p.id
        ) AS comment_count
    FROM feed_posts p
    LEFT JOIN "user" u
        ON u.id = p.user_id
    LEFT JOIN listings l
        ON l.id = p.listing_id
    WHERE p.is_public = 1
    ORDER BY p.id DESC
    LIMIT 50
""")

    for post in posts:
        post["comments"] = query_all(
            """
            SELECT
                c.*,
                COALESCE(NULLIF(u.name, ''), u.email, 'User') AS user_name
            FROM feed_post_comments c
            LEFT JOIN "user" u
                ON u.id = c.user_id
            WHERE c.post_id = :post_id
            ORDER BY c.id ASC
            """,
            {
                "post_id": post["id"]
            }
        )

    return render_template("feed.html", posts=posts)



@app.get("/admin/import-google-place")
def import_google_place():
    place_id = (request.args.get("place_id") or "").strip()

    if not place_id:
        return "Missing place_id", 400

    existing_place = query_one(
        """
        SELECT slug
        FROM listings
        WHERE place_id = :place_id
        """,
        {"place_id": place_id}
    )

    if existing_place:
        return redirect(f"/listing/{existing_place['slug']}")

    url = f"https://places.googleapis.com/v1/places/{place_id}"

    headers = {
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": ",".join([
            "id",
            "displayName",
            "formattedAddress",
            "location",
            "primaryType",
            "websiteUri",
            "photos"
        ])
    }

    try:
        r = requests.get(url, headers=headers, timeout=20)

        if not r.ok:
            print("[IMPORT_GOOGLE_PLACE_STATUS]", r.status_code, flush=True)
            print("[IMPORT_GOOGLE_PLACE_BODY]", r.text, flush=True)
            return "Failed to fetch place from Google", 502

        data = r.json()

        name = (data.get("displayName") or {}).get("text") or "Untitled Place"
        address = data.get("formattedAddress") or ""
        category = data.get("primaryType") or ""
        website = data.get("websiteUri") or ""

        location = data.get("location") or {}
        latitude = location.get("latitude")
        longitude = location.get("longitude")

        photos = data.get("photos") or []
        photo_url = None
        photo_urls = []

        print("[IMPORT_GOOGLE_PLACE_PHOTO_COUNT]", len(photos), flush=True)

        for photo in photos[:8]:
            photo_name = photo.get("name")

            if not photo_name:
                continue

            print("[IMPORT_GOOGLE_PLACE_PHOTO_NAME]", photo_name, flush=True)

            resolved_url = google_place_photo_uri(photo_name, max_width=1200)

            if resolved_url:
                photo_urls.append(resolved_url)

        if photo_urls:
            photo_url = photo_urls[0]

        print("[IMPORT_GOOGLE_PLACE_PHOTO_URLS]", photo_urls, flush=True)

        base_slug = slugify(name)
        slug = base_slug

        existing_slug = query_one(
            """
            SELECT slug
            FROM listings
            WHERE slug = :slug
            """,
            {"slug": slug}
        )

        if existing_slug:
            slug = f"{base_slug}-{place_id[:8]}"

        existing_final_slug = query_one(
            """
            SELECT slug
            FROM listings
            WHERE slug = :slug
            """,
            {"slug": slug}
        )

        if existing_final_slug:
            return redirect(f"/listing/{existing_final_slug['slug']}")

        execute(
            """
            INSERT INTO listings (
                place_id,
                name,
                slug,
                category,
                address,
                website,
                latitude,
                longitude,
                photo_url,
                photo_urls_json,
                status,
                featured,
                created_at,
                updated_at
            )
            VALUES (
                :place_id,
                :name,
                :slug,
                :category,
                :address,
                :website,
                :latitude,
                :longitude,
                :photo_url,
                :photo_urls_json,
                'published',
                0,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            """,
            {
                "place_id": place_id,
                "name": name,
                "slug": slug,
                "category": category,
                "address": address,
                "website": website,
                "latitude": latitude,
                "longitude": longitude,
                "photo_url": photo_url,
                "photo_urls_json": json.dumps(photo_urls)
            }
        )

        return redirect(f"/listing/{slug}")

    except Exception as e:
        print("[IMPORT_GOOGLE_PLACE_ERROR]", str(e), flush=True)
        return "Import failed", 500








@app.get("/admin/ai-jobs/<job_id>")
def ai_job_status(job_id):
    job = AI_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    # (optional) cleanup old jobs
    # if time.time() - job["created_at"] > 3600: del AI_JOBS[job_id]

    return jsonify({
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    })
    
    
    
@app.get("/")
def home():
    articles = query_all("""
        SELECT id, slug, title, description, updated_at, card_image_url
        FROM pages
        WHERE status = 'published'
        ORDER BY updated_at DESC, id DESC
        LIMIT 8
""")

    homepage_top_ads = get_active_ads("homepage_top", limit=1)
    homepage_inline_ads = get_active_ads("homepage_inline", limit=3)

    user_lists = []
    default_saved_list_id = None

    if current_user.is_authenticated:
        user_lists = SavedList.query.filter_by(
            user_id=current_user.id
        ).order_by(SavedList.title.asc()).all()

        first_list = user_lists[0] if user_lists else None
        default_saved_list_id = first_list.id if first_list else None

    return render_template(
        "directory_home.html",
        articles=articles,
        article_style="spotlight",
        homepage_top_ads=homepage_top_ads,
        homepage_inline_ads=homepage_inline_ads,
        default_saved_list_id=default_saved_list_id,
        user_lists=user_lists
    )



from math import radians, sin, cos, sqrt, atan2

def haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8  # Earth radius in miles

    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)

    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return r * c


def google_place_photo_uri(photo_name: str, max_width: int = 600):
    if not GOOGLE_MAPS_API_KEY or not photo_name:
        return None

    return (
        f"https://places.googleapis.com/v1/{photo_name}/media"
        f"?maxWidthPx={max_width}&key={GOOGLE_MAPS_API_KEY}"
    )

def google_places_text_search(query: str, lat: float = None, lng: float = None, limit: int = 10):
    if not GOOGLE_MAPS_API_KEY or not query:
        return []

    url = "https://places.googleapis.com/v1/places:searchText"

    has_location = lat is not None and lng is not None

    body = {
        "textQuery": query,
        "maxResultCount": max(1, min(int(limit or 10), 20))
    }

    if has_location:
        body["locationBias"] = {
            "circle": {
                "center": {
                    "latitude": float(lat),
                    "longitude": float(lng)
                },
                "radius": 12000.0
            }
        }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": ",".join([
            "places.id",
            "places.displayName",
            "places.formattedAddress",
            "places.location",
            "places.primaryType",
            "places.rating",
            "places.userRatingCount",
            "places.googleMapsUri",
            "places.websiteUri",
            "places.photos"
        ])
    }

    print("[GOOGLE_PLACES_QUERY]", query, flush=True)
    print("[GOOGLE_PLACES_LOCATION]", lat, lng, flush=True)
    print("[GOOGLE_PLACES_BODY]", body, flush=True)
    print("[GOOGLE_API_REQUEST_BODY]", body, flush=True)

    try:
        r = requests.post(url, headers=headers, json=body, timeout=20)

        if not r.ok:
            print("[GOOGLE_PLACES_STATUS]", r.status_code, flush=True)
            print("[GOOGLE_PLACES_RESPONSE]", r.text, flush=True)
            return []

        data = r.json()

    except Exception as e:
        print("[GOOGLE_PLACES_ERROR]", str(e), flush=True)
        return []

    places = data.get("places") or []
    results = []

    for p in places:
        location = p.get("location") or {}
        display_name = p.get("displayName") or {}
        photos = p.get("photos") or []

        place_lat = location.get("latitude")
        place_lng = location.get("longitude")

        photo_name = None
        photo_url = None
        photo_urls = []
        photo_gallery = []

        for photo in photos[:5]:
            name = photo.get("name")
            if not name:
                continue

            photo_uri = google_place_photo_uri(name, max_width=1200)
            if not photo_uri:
                continue

            if not photo_name:
                photo_name = name

            if not photo_url:
                photo_url = google_place_photo_uri(name, max_width=600) or photo_uri

            photo_urls.append(photo_uri)
            photo_gallery.append({"url": photo_uri})

        item = {
            "source": "google",
            "badge": "Nearby result",
            "place_id": p.get("id"),
            "slug": None,
            "name": display_name.get("text") or "Unnamed place",
            "category": p.get("primaryType") or "",
            "city": "",
            "state": "",
            "address": p.get("formattedAddress") or "",
            "phone": None,
            "website": p.get("websiteUri") or p.get("googleMapsUri") or "",
            "google_maps_uri": p.get("googleMapsUri") or "",
            "description": None,
            "featured": 0,
            "latitude": place_lat,
            "longitude": place_lng,
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"),
            "photo_name": photo_name,
            "photo_url": photo_url,
            "photo_urls": photo_urls,
            "photo_gallery": photo_gallery,
            "distance_miles": None,
        }

        if has_location and place_lat is not None and place_lng is not None:
            try:
                item["distance_miles"] = round(
                    haversine_miles(
                        float(lat),
                        float(lng),
                        float(place_lat),
                        float(place_lng)
                    ),
                    1
                )
            except Exception as e:
                print("[GOOGLE_PLACES_DISTANCE_ERROR]", str(e), flush=True)
                item["distance_miles"] = None

        results.append(item)

    if has_location:
        results = [
            r for r in results
            if r.get("distance_miles") is not None
            and r["distance_miles"] <= LOCAL_RADIUS_MILES
        ]

        results.sort(
            key=lambda x: (
                x["distance_miles"],
                x.get("name") or ""
            )
        )
    else:
        results.sort(key=lambda x: x.get("name") or "")

    print("[GOOGLE_PLACES_RESULTS_COUNT]", len(results), flush=True)

    return results



import hashlib
import json

def normalize_search_query(q: str) -> str:
    if not q:
        return ""

    q = q.lower().strip()

    replacements = {
        "near me": "",
        "nearby": "",
        "in my area": "",
        "around me": ""
    }

    for old, new in replacements.items():
        q = q.replace(old, new)

    return q.strip()




def detect_intent(q):
    q = (q or "").lower()

    if "coffee" in q or "cafe" in q:
        return "coffee_shop"
    if "bar" in q or "drink" in q:
        return "bar"
    if "restaurant" in q or "food" in q:
        return "restaurant"
    if "things to do" in q or "explore" in q:
        return "discover"

    return "general"



def build_ai_prompt(user_query, city, featured_results, nearby_results):
    return f"""
You are a helpful local guide.

The user asked: "{user_query}"
User city/location: {city or "unknown"}

Write a short, friendly recommendation in 3 to 5 sentences.
Keep it conversational and specific.
Mention 1 to 3 of the featured places if available.
Then mention that more nearby options are listed below.
Do not make up businesses that are not provided.

Featured places:
{featured_results}

Nearby places:
{nearby_results}
"""




def make_google_cache_key(query: str, lat: float = None, lng: float = None) -> str:
    raw = f"{query}|{lat}|{lng}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_google_cached_results(cache_key: str):
    row = query_one("""
        SELECT response_json
        FROM google_places_cache
        WHERE cache_key = ?
        LIMIT 1
    """, (cache_key,))

    if not row:
        return None

    try:
        return json.loads(row["response_json"])
    except Exception:
        return None
    
    
    



def save_google_cached_results(cache_key: str, results: list):
    execute("""
        INSERT INTO google_places_cache (cache_key, response_json, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cache_key) DO UPDATE SET
            response_json = excluded.response_json,
            created_at = CURRENT_TIMESTAMP
    """, (cache_key, json.dumps(results)))


import math

LOCAL_RADIUS_MILES = 25


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3959.0  # Earth radius in miles

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return r * c


def filter_nearby_results(results, user_lat, user_lng, radius_miles=25):
    if user_lat is None or user_lng is None:
        return results

    nearby = []

    for item in results:
        lat = item.get("latitude")
        lng = item.get("longitude")

        if lat is None or lng is None:
            continue

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            continue

        distance = haversine_miles(user_lat, user_lng, lat, lng)

        if distance <= radius_miles:
            item["distance_miles"] = round(distance, 1)
            nearby.append(item)

    nearby.sort(key=lambda x: x.get("distance_miles", 999999))
    return nearby


import re

def extract_city(query):
    patterns = [
        r"in ([a-zA-Z\s]+)",
        r"near ([a-zA-Z\s]+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, query.lower())
        if match:
            return match.group(1).strip()

    return None



import requests

def geocode_city(city_name):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": city_name,
            "key": GOOGLE_MAPS_API_KEY
        }

        res = requests.get(url, params=params).json()

        if res["results"]:
            loc = res["results"][0]["geometry"]["location"]
            return {"lat": loc["lat"], "lng": loc["lng"]}

    except Exception as e:
        print("[GEOCODE_ERROR]", str(e), flush=True)

    return None


@app.get("/search")
def search():
    q_raw = (request.args.get("q") or "").strip()
    q = normalize_search_query(q_raw)

    # ✅ ADD THIS (Step 1: detect city)
    city_override = extract_city(q_raw)


    lat_raw = (request.args.get("lat") or "").strip()
    lng_raw = (request.args.get("lng") or "").strip()

    user_lat = None
    user_lng = None

    try:
        if lat_raw and lng_raw:
            user_lat = float(lat_raw)
            user_lng = float(lng_raw)
    except ValueError:
        user_lat = None
        user_lng = None

    has_user_location = user_lat is not None and user_lng is not None

    # ✅ ADD THIS (Step 2: override location BEFORE searches)
    if city_override:
        print("[CITY_OVERRIDE]", city_override, flush=True)

        geo = geocode_city(city_override)
        if geo:
            user_lat = geo["lat"]
            user_lng = geo["lng"]
            has_user_location = True

    print("[SEARCH_QUERY]", q_raw, flush=True)
    print("[SEARCH_NORMALIZED]", q, flush=True)
    print("[SEARCH_LAT_LNG]", user_lat, user_lng, flush=True)
    print("[FINAL_USER_LOCATION]", user_lat, user_lng, flush=True)

    internal_results = search_internal_listings(
        q=q,
        lat=user_lat if has_user_location else None,
        lng=user_lng if has_user_location else None,
        limit=30
    ) or []

    if has_user_location:
        internal_results = filter_nearby_results(
            internal_results,
            user_lat,
            user_lng,
            radius_miles=LOCAL_RADIUS_MILES
        )

    external_results = []

    if q:
        external_results = google_places_text_search(
            query=q,
            lat=user_lat if has_user_location else None,
            lng=user_lng if has_user_location else None,
            limit=25
        ) or []

    if has_user_location:
        external_results = filter_nearby_results(
            external_results,
            user_lat,
            user_lng,
            radius_miles=LOCAL_RADIUS_MILES
        )

    featured_results = [r for r in internal_results if r.get("featured")]
    nearby_results = [r for r in internal_results if not r.get("featured")]

    featured_results = featured_results[:3]
    nearby_results = nearby_results[:10]

    ai_answer = generate_local_answer(
        user_query=q_raw,
        featured_results=featured_results,
        nearby_results=nearby_results
    )

    map_results = []

    for l in internal_results:
        lat = l.get("latitude")
        lng = l.get("longitude")

        if lat is not None and lng is not None:
            map_results.append({
                "name": l.get("name"),
                "lat": lat,
                "lng": lng,
                "category": l.get("category"),
                "slug": l.get("slug"),
                "source": "internal",
                "distance_miles": l.get("distance_miles"),
            })

    for l in external_results:
        lat = l.get("latitude")
        lng = l.get("longitude")

        if lat is not None and lng is not None:
            map_results.append({
                "name": l.get("name"),
                "lat": lat,
                "lng": lng,
                "category": l.get("category"),
                "slug": None,
                "source": "google",
                "website": l.get("website"),
                "distance_miles": l.get("distance_miles"),
            })

    try:
        search_ads = get_active_ads("search_inline", limit=3)
    except Exception as e:
        print("[SEARCH_ADS_ERROR]", str(e), flush=True)
        search_ads = []

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "internal_results": internal_results,
            "external_results": external_results,
            "map_results": map_results,
            "user_lat": user_lat,
            "user_lng": user_lng,
        })

    return render_template(
        "search_results.html",
        query=q_raw,
        ai_answer=ai_answer,
        featured_results=featured_results,
        nearby_results=nearby_results,
        user_lat=user_lat,
        user_lng=user_lng,
        internal_results=internal_results,
        external_results=external_results,
        map_lat=user_lat,
        map_lng=user_lng,
        map_results=map_results,
        google_maps_api_key=GOOGLE_MAPS_API_KEY,
        search_ads=search_ads,
        local_radius_miles=LOCAL_RADIUS_MILES,
    )
    
    
def generate_local_answer(user_query, featured_results, nearby_results):
    if featured_results:
        names = [x.get("name") for x in featured_results[:2] if x.get("name")]
        if len(names) == 1:
            featured_text = names[0]
        else:
            featured_text = " and ".join(names)
        return (
            f"If you're looking for a good option, I’d start with {featured_text}. "
            f"These are strong featured picks based on your search. "
            f"I also included more nearby places below in case you want a few extra options."
        )

    if nearby_results:
        first_name = nearby_results[0].get("name", "a nearby place")
        return (
            f"A good place to start is {first_name}. "
            f"I also found a few more nearby options below so you can compare what’s closest and best for you."
        )
        
        

    return "I couldn’t find a strong local match yet, but try refining the city or category and I’ll suggest better nearby options."
    
    
import math

def haversine(lat1, lon1, lat2, lon2):
    R = 3959  # miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c




@app.get("/listing-data/<slug>")
def listing_data(slug):
    listing = query_one(
        """
        SELECT *
        FROM listings
        WHERE slug = :slug
        LIMIT 1
        """,
        {"slug": slug}
    )

    if not listing:
        return {"error": "Not found"}, 404

    photos = []

    try:
        if listing.get("photo_urls_json"):
            photos = json.loads(listing["photo_urls_json"]) or []
    except Exception as e:
        print("[LISTING_DATA_PHOTO_JSON_ERROR]", str(e), flush=True)

    if listing.get("photo_url") and listing["photo_url"] not in photos:
        photos.insert(0, listing["photo_url"])

    print("[LISTING_DATA_SLUG]", slug, flush=True)
    print("[LISTING_DATA_PHOTOS_COUNT]", len(photos), flush=True)

    return {
        "name": listing.get("name"),
        "category": listing.get("category"),
        "address": listing.get("address"),
        "website": listing.get("website"),
        "photo_url": listing.get("photo_url"),
        "photos": photos,
        "description": listing.get("description") or "",
        "latitude": listing.get("latitude"),
        "longitude": listing.get("longitude"),
        "slug": listing.get("slug"),
    }
    

    
@app.get("/listing/<slug>")
def listing_page(slug):
    listing = query_one(
        """
        SELECT *
        FROM listings
        WHERE slug = :slug
          AND status = 'published'
        """,
        {"slug": slug}
    )

    if not listing:
        abort(404)

    related = query_all(
        """
        SELECT *
        FROM listings
        WHERE id != :id
          AND status = 'published'
          AND (
            category = :category
            OR city = :city
          )
        ORDER BY featured DESC, id DESC
        LIMIT 6
        """,
        {
            "id": listing.get("id"),
            "category": listing.get("category") or "",
            "city": listing.get("city") or ""
        }
    ) or []

    comments = query_all(
        """
        SELECT *
        FROM listing_comments
        WHERE listing_id = :listing_id
        ORDER BY created_at DESC
        """,
        {"listing_id": listing.get("id")}
    ) or []

    rating_summary = {
        "comment_count": 0,
        "avg_rating": 0
    }

    try:
        rating_summary = query_one(
            """
            SELECT
                COUNT(*) AS comment_count,
                COALESCE(ROUND(AVG(NULLIF(rating, 0))::numeric, 1), 0) AS avg_rating
            FROM listing_comments
            WHERE listing_id = :listing_id
            """,
            {"listing_id": listing.get("id")}
        ) or rating_summary
    except Exception as e:
        print("[LISTING_RATING_SUMMARY_ERROR]", str(e), flush=True)

    listing_photos = []

    try:
        if listing.get("photo_urls_json"):
            listing_photos = json.loads(listing["photo_urls_json"]) or []
    except Exception as e:
        print("[LISTING_PHOTO_JSON_ERROR]", str(e), flush=True)

    if not listing_photos and listing.get("photo_url"):
        listing_photos = [listing["photo_url"]]

    return render_template(
        "listing_page.html",
        listing=listing,
        related=related,
        comments=comments,
        listing_photos=listing_photos,
        rating_summary=rating_summary
    )
    
    
    
@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")

    
    
    
    
@app.route("/discover")
def discover_page():
    q = (request.args.get("q") or "").strip().lower()
    category = (request.args.get("category") or "").strip().lower()

    default_list_id = None

    if current_user.is_authenticated:
        default_list = SavedList.query.filter_by(user_id=current_user.id) \
            .order_by(SavedList.created_at.asc()) \
            .first()

        if not default_list:
            default_list = SavedList(
                user_id=current_user.id,
                title="My Places",
                description="Places I saved from Local AI",
                is_public=False
            )
            db.session.add(default_list)
            db.session.commit()

        default_list_id = default_list.id

    sql = """
        SELECT
            id,
            name,
            slug,
            category,
            address,
            city,
            state,
            website,
            latitude,
            longitude,
            place_id,
            COALESCE(NULLIF(photo_url, ''), NULLIF(card_image_url, '')) AS photo_url,
            photo_urls_json
        FROM listings
        WHERE status = 'published'
    """

    params = {}

    if q:
        sql += """
          AND (
            LOWER(COALESCE(name, '')) LIKE :q
            OR LOWER(COALESCE(category, '')) LIKE :q
            OR LOWER(COALESCE(address, '')) LIKE :q
            OR LOWER(COALESCE(city, '')) LIKE :q
            OR LOWER(COALESCE(state, '')) LIKE :q
          )
        """
        params["q"] = f"%{q}%"

    if category:
        sql += """
          AND LOWER(COALESCE(category, '')) LIKE :category
        """
        params["category"] = f"%{category}%"

    sql += """
        ORDER BY name ASC
        LIMIT 100
    """

    listings = query_all(sql, params)

    for listing in listings:
        if not listing.get("photo_url") and listing.get("photo_urls_json"):
            try:
                photos = json.loads(listing["photo_urls_json"]) or []
                if photos:
                    listing["photo_url"] = photos[0]
            except Exception as e:
                print("[DISCOVER_PHOTO_JSON_ERROR]", str(e), flush=True)

    print("[DISCOVER_LISTINGS_COUNT]", len(listings), flush=True)
    print("[DISCOVER_DEFAULT_LIST_ID]", default_list_id, flush=True)

    return render_template(
        "discover.html",
        page_title="Discover",
        listings=listings,
        q=q,
        active_category=category,
        default_list_id=default_list_id
    )





@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        current_user.name = request.form.get("name", "").strip()
        current_user.home_city = request.form.get("home_city", "").strip()

        db.session.commit()

        flash("Settings updated.")
        return redirect(url_for("settings"))

    lists = SavedList.query.filter_by(
        user_id=current_user.id
    ).order_by(SavedList.created_at.desc()).all()

    return render_template(
        "settings.html",
        user=current_user,
        lists=lists
    )


@app.post("/settings/lists/<int:list_id>/delete")
@login_required
def delete_user_list(list_id):
    saved_list = SavedList.query.filter_by(
        id=list_id,
        user_id=current_user.id
    ).first_or_404()

    SavedPlace.query.filter_by(
        saved_list_id=saved_list.id
    ).delete(synchronize_session=False)

    UserSavedList.query.filter_by(
        saved_list_id=saved_list.id
    ).delete(synchronize_session=False)

    db.session.delete(saved_list)
    db.session.commit()

    flash("List deleted.")
    return redirect(url_for("settings"))


@app.post("/settings/contact-support")
@login_required
def contact_support():
    message = request.form.get("message", "").strip()

    print("[SUPPORT_REQUEST]", {
        "user_id": current_user.id,
        "email": current_user.email,
        "message": message
    }, flush=True)

    flash("Support request sent.")
    return redirect(url_for("settings"))


@app.post("/settings/delete-account")
@login_required
def delete_account():
    user = current_user

    logout_user()

    db.session.delete(user)
    db.session.commit()

    flash("Your account has been deleted.")
    return redirect(url_for("home"))



    
    
    
@app.get("/admin/listings")
def admin_listings():
    listings = query_all("""
        SELECT id, name, slug, category, city, state, featured, status, updated_at
        FROM listings
        ORDER BY updated_at DESC, id DESC
    """)
    return render_template("admin_listings.html", listings=listings)





import json
from flask import request, jsonify

def build_map_results(internal_results, external_results):
    map_results = []

    for l in internal_results:
        if l.get("latitude") is not None and l.get("longitude") is not None:
            map_results.append({
                "name": l.get("name"),
                "lat": l.get("latitude"),
                "lng": l.get("longitude"),
                "category": l.get("category"),
                "slug": l.get("slug"),
                "source": "internal",
                "website": l.get("website"),
            })

    for l in external_results:
        if l.get("latitude") is not None and l.get("longitude") is not None:
            map_results.append({
                "name": l.get("name"),
                "lat": l.get("latitude"),
                "lng": l.get("longitude"),
                "category": l.get("category"),
                "slug": None,
                "source": "google",
                "website": l.get("website"),
            })

    return map_results



import re

KNOWN_CITIES = {
    "naperville", "chicago", "boston", "miami", "new york", "nyc",
    "los angeles", "la", "san francisco", "austin", "nashville",
    "denver", "seattle", "orlando", "dallas", "houston", "phoenix",
    "atlanta", "tampa", "fort lauderdale", "west palm beach"
}

CITY_ALIASES = {
    "nyc": "New York",
    "la": "Los Angeles",
}

def detect_location_from_query(query):
    text = (query or "").strip()
    if not text:
        return None

    patterns = [
        r"\bin\s+(.+)$",
        r"\bnear\s+(.+)$",
        r"\baround\s+(.+)$",
        r"\bby\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw_location = match.group(1).strip()
            return geocode_location_name(raw_location)

    return geocode_location_name(text)









def fallback_intent_from_message(message: str):
    text = (message or "").strip()
    lowered = text.lower()

    query = text
    city = None
    state = None
    use_current_location = True

    keyword_map = {
        "coffee": "coffee shop",
        "coffee shop": "coffee shop",
        "coffee shops": "coffee shop",
        "cafe": "coffee shop",
        "cafes": "coffee shop",
        "bar": "bar",
        "bars": "bar",
        "food": "restaurant",
        "restaurant": "restaurant",
        "restaurants": "restaurant",
        "burger": "burger restaurant",
        "burgers": "burger restaurant",
        "pizza": "pizza restaurant",
        "gym": "gym",
        "shopping": "shopping mall",
        "mall": "shopping mall",
        "dentist": "dentist",
        "plumber": "plumber"
    }

    for keyword, mapped in keyword_map.items():
        if keyword in lowered:
            query = mapped
            break

    if "near me" in lowered or "nearby" in lowered or "around me" in lowered:
        return {
            "query": query,
            "use_current_location": True,
            "city": None,
            "state": None,
            "summary_style": "helpful"
        }

    explicit_patterns = [
        r"\bin\s+([A-Za-z\s]+?)(?:,\s*([A-Za-z]{2}))?$",
        r"\bto\s+([A-Za-z\s]+?)(?:,\s*([A-Za-z]{2}))?$",
        r"\btraveling to\s+([A-Za-z\s]+?)(?:,\s*([A-Za-z]{2}))?$",
        r"\bvisiting\s+([A-Za-z\s]+?)(?:,\s*([A-Za-z]{2}))?$"
    ]

    for pattern in explicit_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            city = (match.group(1) or "").strip().title()
            state = (match.group(2) or "").strip().upper() if match.group(2) else None

            if city:
                return {
                    "query": query,
                    "use_current_location": False,
                    "city": city,
                    "state": state,
                    "summary_style": "helpful"
                }

    tokens = lowered.split()
    if tokens:
        first_two = " ".join(tokens[:2]) if len(tokens) >= 2 else None
        first_one = tokens[0]

        if first_two and first_two in KNOWN_CITIES:
            return {
                "query": query,
                "use_current_location": False,
                "city": first_two.title(),
                "state": None,
                "summary_style": "helpful"
            }

        if first_one in KNOWN_CITIES:
            return {
                "query": query,
                "use_current_location": False,
                "city": first_one.title(),
                "state": None,
                "summary_style": "helpful"
            }

    return {
        "query": query,
        "use_current_location": True,
        "city": None,
        "state": None,
        "summary_style": "helpful"
    }


def call_llm_for_search_intent(user_message: str):
    return fallback_intent_from_message(user_message)


def geocode_city(city: str, state: str = None):
    if not GOOGLE_MAPS_API_KEY or not city:
        return None, None

    query = city.strip()
    if state:
        query = f"{city}, {state}"

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": query,
        "key": GOOGLE_MAPS_API_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        print("[GEOCODE_CITY_QUERY]", query, flush=True)
        print("[GEOCODE_CITY_RESPONSE]", data, flush=True)

        status = data.get("status")
        if status != "OK":
            print("[GEOCODE_CITY_STATUS]", status, flush=True)
            return None, None

        results = data.get("results", []) or []
        if not results:
            return None, None

        location = results[0].get("geometry", {}).get("location", {})
        return location.get("lat"), location.get("lng")

    except Exception as e:
        print("[GEOCODE_CITY_ERROR]", str(e), flush=True)
        return None, None


def ai_prompt_for_local_search(user_message: str):
    return f"""
You are a local business search assistant.

Convert the user's request into JSON only.

Return exactly this schema:
{{
  "query": "short business search phrase",
  "use_current_location": true,
  "city": null,
  "state": null,
  "summary_style": "helpful"
}}

Rules:
- Return JSON only.
- "query" should be a short practical business type like:
  "coffee shop", "bar", "restaurant", "shopping mall", "gym", "dentist"
- If the user says "near me", "close to me" or "nearby", set use_current_location to true.
- Use bulletin points in the summary when you name nearby places.
- If the user clearly mentions another city, set use_current_location to false and fill city/state.
- Prefer city-based search when a city is explicitly mentioned.
- Do not include explanation outside the JSON.
- ALWAYS prioritize the city explicitly mentioned by the user.
- If the user says a different city (e.g. "tacos in Boston"), IGNORE their current location.
- Only use the user's current location if NO city is mentioned.
- Extract the city from the query and base all recommendations on that city.

Examples:
- "tacos in Boston" → use Boston
- "coffee near me" → use user's location
- "best bars in Chicago" → use Chicago

User message:
{user_message}
""".strip()


def call_llm_for_search_intent(user_message: str):
    """
    Starter version:
    - uses the fallback parser now
    - easy to replace later with a real LLM call
    """
    return fallback_intent_from_message(user_message)


def summarize_ai_results(user_message, featured_internal, regular_internal, external_results):
    parts = []

    if featured_internal:
        featured_names = ", ".join([r.get("name") for r in featured_internal[:3] if r.get("name")])
        parts.append(f'Featured from our directory: {featured_names}.')

    if not featured_internal and regular_internal:
        regular_names = ", ".join([r.get("name") for r in regular_internal[:3] if r.get("name")])
        parts.append(f'I found some strong matches in your directory: {regular_names}.')

    if external_results:
        external_names = ", ".join([r.get("name") for r in external_results[:3] if r.get("name")])
        parts.append(f'Additional nearby places from Google include {external_names}.')

    if not parts:
        return f'I couldn’t find strong results for "{user_message}" yet. Try a broader phrase like coffee, bars, restaurants, or shopping.'

    return " ".join(parts)



import os
from flask import request, jsonify
from werkzeug.utils import secure_filename

@app.route("/admin/upload-image", methods=["POST"])
def upload_image():

    file = request.files.get("image")

    if not file:
        return jsonify({"error": "No file"}), 400

    filename = secure_filename(file.filename)

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    file.save(filepath)

    image_url = f"/static/uploads/{filename}"

    return jsonify({
        "success": True,
        "url": image_url
    })

@app.route("/admin/listings/new", methods=["GET", "POST"])
def admin_new_listing():
    error = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        slug = slugify(request.form.get("slug") or name)
        category = (request.form.get("category") or "").strip()
        city = (request.form.get("city") or "").strip()
        state = (request.form.get("state") or "").strip()
        address = (request.form.get("address") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        website = (request.form.get("website") or "").strip()
        description = (request.form.get("description") or "").strip()
        featured = 1 if request.form.get("featured") == "1" else 0
        status = (request.form.get("status") or "draft").strip()

        if not name or not slug:
            error = "Name and slug are required."
            return render_template("admin_listing_form.html", error=error, listing=None)

        existing = listing = query_one(
    """
    SELECT *
    FROM listings
    WHERE slug = :slug
    """,
    {
        "slug": slug
    }
)
        if existing:
            error = "That slug is already taken."
            return render_template("admin_listing_form.html", error=error, listing=None)

        execute("""
    INSERT INTO listings (
        name, slug, category, city, state, address, phone, website,
        description, latitude, longitude, featured, status, created_at, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
""", (
    name, slug, category, city, state, address, phone, website,
    description, latitude, longitude, featured, status
))
        
        
        latitude = request.form.get("latitude") or None
        longitude = request.form.get("longitude") or None

        if latitude == "":
            latitude = None
        if longitude == "":
            longitude = None

        return redirect(url_for("admin_listings"))

    return render_template("admin_listing_form.html", error=error, listing=None)




@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        

        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for("signup"))

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("That email is already registered.")
            return redirect(url_for("signup"))
        
        
        if request.form.get("privacy_agree") != "on":
            flash("You must agree to the Privacy Policy to create an account.")
            return redirect(url_for("signup"))

        user = User(name=name, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Account created successfully.")
        return redirect(url_for("onboarding"))

    return render_template("signup.html")




@app.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    if request.method == "POST":
        favorite_categories = request.form.getlist("favorite_categories")
        home_city = request.form.get("home_city", "").strip()
        budget_style = request.form.get("budget_style", "").strip()
        intent_type = request.form.get("intent_type", "").strip()
        profile_photo = request.files.get("profile_photo")

        if profile_photo and profile_photo.filename:
            filename = secure_filename(profile_photo.filename)
            ext = os.path.splitext(filename)[1].lower() or ".jpg"
            new_filename = f"profile_{current_user.id}_{uuid.uuid4().hex}{ext}"

            profile_upload_folder = os.path.join(
                app.config["UPLOAD_FOLDER"],
                "profiles"
            )
            os.makedirs(profile_upload_folder, exist_ok=True)

            save_path = os.path.join(profile_upload_folder, new_filename)
            

            current_user.profile_image_url = url_for(
                "static",
                filename=f"uploads/profiles/{new_filename}"
            )

        current_user.favorite_categories = json.dumps(favorite_categories)
        current_user.home_city = home_city
        current_user.budget_style = budget_style
        current_user.intent_type = intent_type
        current_user.onboarding_complete = True

        db.session.commit()

        flash("Your profile is ready.")
        return redirect(url_for("account"))

    return render_template("onboarding.html")



@app.route("/debug-users")
def debug_users():
    users = User.query.all()
    return {
        "users": [
            {"id": u.id, "name": u.name, "email": u.email}
            for u in users
        ]
    }



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.")
            return redirect(url_for("login"))

        login_user(user)
        flash("Welcome back.")
        return redirect(url_for("account"))

    return render_template("login.html")



@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))




@app.get("/users/search")
@login_required
def search_users():
    q = request.args.get("q", "").strip()

    users = User.query.filter(
        User.email.ilike(f"%{q}%")
    ).limit(10).all() if q else []

    return render_template("user_search.html", users=users, q=q)




@app.get("/admin")
def admin_home():
    counts = safe_query_one("""
        SELECT
          SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published_count,
          SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) AS draft_count,
          COUNT(*) AS total_count
        FROM pages
    """, fallback={
        "published_count": 0,
        "draft_count": 0,
        "total_count": 0
    }, label="ADMIN_HOME_COUNTS_ERROR")

    pages = safe_query_all("""
        SELECT id, slug, title, status, template, updated_at
        FROM pages
        ORDER BY updated_at DESC, id DESC
    """, label="ADMIN_HOME_PAGES_ERROR")

    return render_template("admin_home.html", counts=counts, pages=pages)





@app.get("/admin/ads")
def admin_ads():
    ads = safe_query_all("""
        SELECT *
        FROM ads
        ORDER BY placement ASC, sort_order ASC, id DESC
    """, label="ADMIN_ADS_ERROR")

    return render_template("admin_ads.html", ads=ads)




    






@app.route("/admin/ads/new", methods=["GET", "POST"])
def admin_new_ad():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        placement = (request.form.get("placement") or "").strip()
        image_url = (request.form.get("image_url") or "").strip()
        headline = (request.form.get("headline") or "").strip()
        body = (request.form.get("body") or "").strip()
        button_text = (request.form.get("button_text") or "").strip()
        target_url = (request.form.get("target_url") or "").strip()
        is_active = 1 if request.form.get("is_active") == "1" else 0

        try:
            sort_order = int(request.form.get("sort_order") or 0)
        except ValueError:
            sort_order = 0

        if not name or not placement:
            return render_template(
                "admin_ad_form.html",
                ad=None,
                error="Name and placement are required."
            )

        execute("""
            INSERT INTO ads (
                name, placement, image_url, headline, body,
                button_text, target_url, is_active, sort_order, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name, placement, image_url, headline, body,
            button_text, target_url, is_active, sort_order,
            datetime.utcnow().isoformat()
        ))

        return redirect(url_for("admin_ads"))

    return render_template("admin_ad_form.html", ad=None)


@app.route("/admin/ads/<int:ad_id>/edit", methods=["GET", "POST"])
def admin_edit_ad(ad_id):
    ad = query_one("SELECT * FROM ads WHERE id=?", (ad_id,))
    if not ad:
        abort(404)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        placement = (request.form.get("placement") or "").strip()
        image_url = (request.form.get("image_url") or "").strip()
        headline = (request.form.get("headline") or "").strip()
        body = (request.form.get("body") or "").strip()
        button_text = (request.form.get("button_text") or "").strip()
        target_url = (request.form.get("target_url") or "").strip()
        is_active = 1 if request.form.get("is_active") == "1" else 0

        try:
            sort_order = int(request.form.get("sort_order") or 0)
        except ValueError:
            sort_order = 0

        if not name or not placement:
            return render_template(
                "admin_ad_form.html",
                ad=ad,
                error="Name and placement are required."
            )

        execute("""
            UPDATE ads
            SET
                name=?,
                placement=?,
                image_url=?,
                headline=?,
                body=?,
                button_text=?,
                target_url=?,
                is_active=?,
                sort_order=?,
                updated_at=?
            WHERE id=?
        """, (
            name, placement, image_url, headline, body,
            button_text, target_url, is_active, sort_order,
            datetime.utcnow().isoformat(), ad_id
        ))

        return redirect(url_for("admin_ads"))

    return render_template("admin_ad_form.html", ad=ad)



@app.route("/account")
@login_required
def account():
    lists = SavedList.query.filter_by(user_id=current_user.id) \
        .order_by(SavedList.created_at.desc()) \
        .all()

    account_lists = []
    map_places = []

    for saved_list in lists:
        list_places = []

        for place in saved_list.places:
            listing = None

            if getattr(place, "place_id", None):
                listing = query_one(
                    """
                    SELECT id, slug, photo_url, photo_urls_json
                    FROM listings
                    WHERE place_id = :place_id
                    LIMIT 1
                    """,
                    {"place_id": place.place_id}
                )

            if not listing and place.name:
                listing = query_one(
                    """
                    SELECT id, slug, photo_url, photo_urls_json
                    FROM listings
                    WHERE LOWER(name) = LOWER(:name)
                    LIMIT 1
                    """,
                    {"name": place.name}
                )

            place_dict = {
                "id": place.id,
                "name": place.name or "",
                "address": place.address or "",
                "website": place.website or "",
                "category": place.category or "",
                "photo_url": place.photo_url or "",
                "notes": place.notes or "",
                "latitude": place.latitude,
                "longitude": place.longitude,
                "city": (getattr(place, "city", None) or "").strip(),
                "cuisine": (getattr(place, "cuisine", None) or place.category or "").strip(),
                "place_id": getattr(place, "place_id", None) or "",
                "listing_id": listing["id"] if listing else "",
                "slug": listing["slug"] if listing else "",
            }

            list_places.append(place_dict)

            if place.latitude is not None and place.longitude is not None:
                try:
                    lat = float(place.latitude)
                    lng = float(place.longitude)

                    map_places.append({
                        "id": place.id,
                        "name": place.name or "",
                        "lat": lat,
                        "lng": lng,
                        "address": place.address or "",
                        "website": place.website or "",
                        "category": place.category or "",
                        "photo_url": place.photo_url or "",
                        "city": place_dict["city"],
                        "cuisine": place_dict["cuisine"],
                        "slug": place_dict["slug"],
                    })
                except (TypeError, ValueError):
                    pass

        account_lists.append({
            "id": saved_list.id,
            "title": saved_list.title,
            "description": saved_list.description,
            "slug": saved_list.slug,
            "is_public": saved_list.is_public,
            "created_at": saved_list.created_at,
            "places": list_places,
        })

    print("[ACCOUNT_MAPBOX_TOKEN]", bool(os.getenv("MAPBOX_TOKEN")), flush=True)
    print("[ACCOUNT_MAPBOX_STYLE_URL]", os.getenv("MAPBOX_STYLE_URL", ""), flush=True)
    print("[ACCOUNT_MAP_PLACES_COUNT]", len(map_places), flush=True)

    return render_template(
        "account.html",
        user=current_user,
        lists=account_lists,
        map_places=map_places,
        mapbox_token=os.getenv("MAPBOX_TOKEN", ""),
        mapbox_style_url=os.getenv("MAPBOX_STYLE_URL", "")
    )



@app.route("/claim-business", methods=["POST"])
@login_required
def claim_business():
    place_id = request.form.get("place_id", "").strip()
    business_name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    website = request.form.get("website", "").strip()
    category = request.form.get("category", "").strip()

    if not place_id or not business_name:
        flash("Missing business information.")
        return redirect(request.referrer or url_for("home"))

    existing = BusinessClaim.query.filter_by(
        user_id=current_user.id,
        place_id=place_id
    ).first()

    if existing:
        flash("You already submitted a claim for this business.")
        return redirect(request.referrer or url_for("account"))

    claim = BusinessClaim(
        user_id=current_user.id,
        place_id=place_id,
        business_name=business_name,
        address=address,
        website=website,
        category=category,
        status="pending"
    )

    db.session.add(claim)
    db.session.commit()

    flash("Your claim was submitted.")
    return redirect(url_for("account"))



@app.route("/lists/new", methods=["GET", "POST"])
@login_required
def create_list():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        is_public = request.form.get("is_public") == "1"

        if not title:
            flash("Title is required.")
            return redirect(url_for("create_list"))

        base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        slug = f"{base_slug}-{current_user.id}"

        existing = SavedList.query.filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{int(time.time())}"

        saved_list = SavedList(
            user_id=current_user.id,
            title=title,
            description=description,
            slug=slug,
            is_public=is_public
        )

        db.session.add(saved_list)
        db.session.commit()

        flash("List created successfully.")
        return redirect(url_for("account"))

    return render_template("create_list.html")


@app.route("/lists/<slug>")
def view_public_list(slug):
    saved_list = SavedList.query.filter_by(
        slug=slug,
        is_public=True
    ).first_or_404()

    map_places = []

    for place in saved_list.places:
        if place.latitude and place.longitude:
            map_places.append({
                "name": place.name,
                "lat": float(place.latitude),
                "lng": float(place.longitude),
                "category": place.category or "",
                "address": place.address or ""
            })

    is_saved = False

    if current_user.is_authenticated:
        is_saved = UserSavedList.query.filter_by(
            user_id=current_user.id,
            saved_list_id=saved_list.id
        ).first() is not None

    return render_template(
        "public_list.html",
        saved_list=saved_list,
        owner=saved_list.user,
        map_places=map_places,
        mapbox_token=os.environ.get("MAPBOX_TOKEN"),
        mapbox_style_url=os.environ.get(
            "MAPBOX_STYLE_URL",
            "mapbox://styles/mapbox/dark-v11"
        ),
        is_saved=is_saved
    )
    


@app.post("/lists/invite")
@login_required
def send_list_invite():
    list_id = request.form.get("list_id")
    recipient = request.form.get("recipient", "").strip()

    saved_list = SavedList.query.filter_by(
        id=list_id,
        user_id=current_user.id
    ).first_or_404()

    share_url = url_for("view_public_list", slug=saved_list.slug, _external=True)

    print("[LIST_INVITE]", recipient, share_url, flush=True)

    flash("Invite ready to send.")
    return redirect(url_for("account"))


@app.post("/listing/<slug>/comments")
def add_listing_comment(slug):
    listing = query_one("""
        SELECT *
        FROM listings
        WHERE slug=? AND status='published'
    """, (slug,))

    if not listing:
        abort(404)

    author_name = (request.form.get("author_name") or "").strip()
    author_email = (request.form.get("author_email") or "").strip()
    body = (request.form.get("body") or "").strip()

    try:
        rating = int(request.form.get("rating") or 5)
    except ValueError:
        rating = 5

    if rating < 1:
        rating = 1
    if rating > 5:
        rating = 5

    if not author_name or not body:
        return redirect(url_for("listing_page", slug=slug))

    execute("""
        INSERT INTO listing_comments (
            listing_id, author_name, author_email, body, rating, is_approved, created_at
        )
        VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
    """, (
        listing["id"],
        author_name,
        author_email,
        body,
        rating
    ))

    return redirect(url_for("listing_page", slug=slug))



@app.post("/admin/ads/<int:ad_id>/delete")
def admin_delete_ad(ad_id):
    execute("DELETE FROM ads WHERE id=?", (ad_id,))
    return redirect(url_for("admin_ads"))


from models import db, User, SavedList, SavedPlace, BusinessClaim


@app.route("/lists/<int:list_id>/save-place", methods=["POST"])
@login_required
def save_place_to_list(list_id):
    saved_list = SavedList.query.filter_by(
        id=list_id,
        user_id=current_user.id
    ).first_or_404()

    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    website = request.form.get("website", "").strip()
    category = request.form.get("category", "").strip()
    photo_url = request.form.get("photo_url", "").strip()
    notes = request.form.get("notes", "").strip()
    city = request.form.get("city", "").strip()
    cuisine = request.form.get("cuisine", "").strip()

    listing_id = request.form.get("listing_id")
    place_id = request.form.get("place_id")

    latitude = request.form.get("latitude")
    longitude = request.form.get("longitude")

    print("[SAVE_PLACE_FORM]", {
        "name": name,
        "address": address,
        "listing_id": listing_id,
        "place_id": place_id,
        "latitude": latitude,
        "longitude": longitude,
    }, flush=True)

    if not name:
        flash("Place name is required.")
        return redirect(url_for("account"))

    saved_place = SavedPlace(
        saved_list_id=saved_list.id,
        listing_id=int(listing_id) if listing_id else None,
        place_id=place_id or None,
        name=name,
        address=address,
        website=website,
        category=category,
        photo_url=photo_url,
        notes=notes,
        city=city or None,
        cuisine=cuisine or category or None,
        latitude=float(latitude) if latitude else None,
        longitude=float(longitude) if longitude else None,
    )

    db.session.add(saved_place)
    db.session.commit()

    print("[SAVED_PLACE_ROW]", {
        "id": saved_place.id,
        "name": saved_place.name,
        "latitude": saved_place.latitude,
        "longitude": saved_place.longitude,
    }, flush=True)

    flash("Place saved to your list.")
    return redirect(url_for("account"))





@app.route("/admin/pages/new", methods=["GET", "POST"])
def admin_new_page():
    if request.method == "POST":
        slug = (request.form.get("slug") or "").strip()
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        card_image_url = (request.form.get("card_image_url") or "").strip()
        template = (request.form.get("template") or "landing_default").strip()
        status = (request.form.get("status") or "draft").strip()
        tag_title = (request.form.get("tag_title") or "").strip()
        hero_title = (request.form.get("hero_title") or "").strip()

        if not slug or not title:
            return render_template("admin_page_form.html", error="Slug and Title are required.", page=None, sections=[])

        try:
            execute("""
                INSERT INTO pages (
                slug, title, description, card_image_url, template, status, tag_title, hero_title, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                slug, title, description, card_image_url, template, status,
                tag_title, hero_title, datetime.utcnow().isoformat()
            ))
        except sqlite3.IntegrityError:
            return render_template("admin_page_form.html", error="That slug is already taken.", page=None, sections=[])

        return redirect(url_for("admin_home"))

    return render_template("admin_page_form.html", page=None, sections=[])


@app.route("/admin/pages/<int:page_id>/edit", methods=["GET", "POST"])
def admin_edit_page(page_id):
    slug = slugify(request.form.get("slug"))
    page = query_one("SELECT * FROM pages WHERE id=?", (page_id,))
    if not page:
        abort(404)

    if request.method == "POST":
        slug = (request.form.get("slug") or "").strip()
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        template = (request.form.get("template") or "landing_default").strip()
        card_image_url = (request.form.get("card_image_url") or "").strip()
        status = (request.form.get("status") or "draft").strip()
        tag_title = (request.form.get("tag_title") or "").strip()
        hero_title = (request.form.get("hero_title") or "").strip()

        if not slug or not title:
            sections = query_all("""
                SELECT id, sort_order, section_type, heading, body, media_path, media_alt, media_caption
                FROM sections
                WHERE page_id=?
                ORDER BY sort_order ASC, id ASC
            """, (page_id,))
            return render_template("admin_page_form.html", error="Slug and Title are required.", page=page, sections=sections)

        try:
            execute("""
                UPDATE pages
                SET slug=?, title=?, description=?, card_image_url=?, template=?, status=?,
                    tag_title=?, hero_title=?, updated_at=?
                WHERE id=?
            """, (
                slug, title, description, card_image_url, template, status,
                tag_title, hero_title, datetime.utcnow().isoformat(), page_id
            ))
        except sqlite3.IntegrityError:
            sections = query_all("""
                SELECT id, sort_order, section_type, heading, body, media_path, media_alt, media_caption
                FROM sections
                WHERE page_id=?
                ORDER BY sort_order ASC, id ASC
            """, (page_id,))
            return render_template("admin_page_form.html", error="That slug is already taken.", page=page, sections=sections)

        return redirect(url_for("admin_home"))

    sections = query_all("""
        SELECT id, sort_order, section_type, heading, body, media_path, media_alt, media_caption
        FROM sections
        WHERE page_id=?
        ORDER BY sort_order ASC, id ASC
    """, (page_id,))

    return render_template("admin_page_form.html", page=page, sections=sections)


@app.route('/admin/listings/<int:id>/delete', methods=['POST'])
def delete_listing(id):
    try:
        execute("DELETE FROM listings WHERE id = ?", (id,))
        return redirect('/admin/listings')
    except Exception as e:
        print("[DELETE_LISTING_ERROR]", str(e), flush=True)
        return "Error deleting listing", 500


@app.route('/admin/listings/<int:id>/make-featured', methods=['POST'])
def make_featured(id):
    try:
        execute("UPDATE listings SET featured = 1 WHERE id = ?", (id,))
        return redirect('/admin/listings')
    except Exception as e:
        print("[MAKE_FEATURED_ERROR]", str(e), flush=True)
        return "Error making listing featured", 500


@app.route('/admin/listings/<int:id>/remove-featured', methods=['POST'])
def remove_featured(id):
    try:
        execute("UPDATE listings SET featured = 0 WHERE id = ?", (id,))
        return redirect('/admin/listings')
    except Exception as e:
        print("[REMOVE_FEATURED_ERROR]", str(e), flush=True)
        return "Error removing featured status", 500
    
    
    
    
    












@app.route("/my-lists/<int:list_id>")
@login_required
def my_list_detail(list_id):
    saved_list = SavedList.query.filter_by(
        id=list_id,
        user_id=current_user.id
    ).first_or_404()

    map_places = []

    for place in saved_list.places:
        if place.latitude is None or place.longitude is None:
            continue

        try:
            lat = float(place.latitude)
            lng = float(place.longitude)
        except (TypeError, ValueError):
            continue

        map_places.append({
            "name": place.name or "",
            "lat": lat,
            "lng": lng,
            "address": place.address or "",
            "website": place.website or "",
            "category": place.category or "",
            "photo_url": place.photo_url or ""
        })

    return render_template(
        "my_list_detail.html",
        saved_list=saved_list,
        map_places=map_places,
        mapbox_token=os.getenv("MAPBOX_TOKEN", ""),
        mapbox_style_url=os.getenv("MAPBOX_STYLE_URL", "")
    )


@app.post("/admin/pages/<int:page_id>/hero/update")
def admin_update_hero(page_id):
    page = query_one("SELECT id FROM pages WHERE id=?", (page_id,))
    if not page:
        abort(404)

    tag_title = (request.form.get("tag_title") or "").strip()
    hero_title = (request.form.get("hero_title") or "").strip()

    execute("""
        UPDATE pages
        SET tag_title = ?, hero_title = ?, updated_at = ?
        WHERE id = ?
    """, (tag_title, hero_title, datetime.utcnow().isoformat(), page_id))

    return redirect(url_for("admin_edit_page", page_id=page_id))



    
    



@app.post("/admin/pages/<int:page_id>/sections/add")
def admin_add_section(page_id):
    page = query_one("SELECT id FROM pages WHERE id=?", (page_id,))
    if not page:
        abort(404)

    section_type = (request.form.get("section_type") or "paragraph").strip()
    heading = (request.form.get("heading") or "").strip()
    body = (request.form.get("body") or "").strip()
    custom_html = (request.form.get("custom_html") or "").strip()
    submit_kind = (request.form.get("submit_kind") or "section").strip()

    media_path = None
    media_alt = (request.form.get("media_alt") or "").strip()
    media_caption = (request.form.get("media_caption") or "").strip()
    file = request.files.get("media_file")

    if submit_kind == "image_only":
        section_type = "image"

    if section_type == "html":
        body = custom_html

    if file and file.filename:
        if not allowed_file(file.filename):
            return redirect(url_for("admin_edit_page", page_id=page_id))

        filename = secure_filename(file.filename)
        name, ext = os.path.splitext(filename)
        final_name = f"{name}_{int(datetime.utcnow().timestamp())}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, final_name)
        file.save(save_path)
        media_path = f"/static/uploads/{final_name}"

    if section_type in ("paragraph", "heading_paragraph") and not body:
        return redirect(url_for("admin_edit_page", page_id=page_id))
    if section_type == "html" and not body:
        return redirect(url_for("admin_edit_page", page_id=page_id))
    if section_type == "image" and not media_path:
        return redirect(url_for("admin_edit_page", page_id=page_id))

    sort_order = next_sort_order(page_id)

    execute("""
        INSERT INTO sections (
            page_id, sort_order, section_type, heading, body,
            media_path, media_alt, media_caption, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        page_id, sort_order, section_type,
        heading, body,
        media_path, media_alt, media_caption,
        datetime.utcnow().isoformat()
    ))

    return redirect(url_for("admin_edit_page", page_id=page_id))


@app.post("/admin/pages/<int:page_id>/sections/<int:section_id>/update")
def admin_update_section(page_id, section_id):
    heading = (request.form.get("heading") or "").strip()
    body = (request.form.get("body") or "").strip()
    section_type = (request.form.get("section_type") or "paragraph").strip()

    if not body:
        return redirect(url_for("admin_edit_page", page_id=page_id))

    execute("""
        UPDATE sections
        SET section_type=?, heading=?, body=?, updated_at=?
        WHERE id=? AND page_id=?
    """, (section_type, heading, body, datetime.utcnow().isoformat(), section_id, page_id))

    return redirect(url_for("admin_edit_page", page_id=page_id))


@app.post("/admin/pages/<int:page_id>/sections/<int:section_id>/delete")
def admin_delete_section(page_id, section_id):
    execute("DELETE FROM sections WHERE id=? AND page_id=?", (section_id, page_id))
    return redirect(url_for("admin_edit_page", page_id=page_id))


@app.post("/admin/pages/<int:page_id>/sections/<int:section_id>/move")
def admin_move_section(page_id, section_id):
    direction = request.form.get("direction")  # "up" or "down"

    current = query_one("SELECT id, sort_order FROM sections WHERE id=? AND page_id=?", (section_id, page_id))
    if not current:
        abort(404)

    if direction == "up":
        neighbor = query_one("""
            SELECT id, sort_order FROM sections
            WHERE page_id=? AND sort_order < ?
            ORDER BY sort_order DESC
            LIMIT 1
        """, (page_id, current["sort_order"]))
    else:
        neighbor = query_one("""
            SELECT id, sort_order FROM sections
            WHERE page_id=? AND sort_order > ?
            ORDER BY sort_order ASC
            LIMIT 1
        """, (page_id, current["sort_order"]))

    if not neighbor:
        return redirect(url_for("admin_edit_page", page_id=page_id))

    execute("UPDATE sections SET sort_order=?, updated_at=? WHERE id=?",
            (neighbor["sort_order"], datetime.utcnow().isoformat(), current["id"]))
    execute("UPDATE sections SET sort_order=?, updated_at=? WHERE id=?",
            (current["sort_order"], datetime.utcnow().isoformat(), neighbor["id"]))

    return redirect(url_for("admin_edit_page", page_id=page_id))


@app.post("/admin/pages/<int:page_id>/delete")
def admin_delete_page(page_id):
    execute("DELETE FROM pages WHERE id=?", (page_id,))
    return redirect(url_for("admin_home"))



import urllib.parse

@app.get("/p/<slug>")
def public_page(slug):
    page = safe_query_one("""
        SELECT *
        FROM pages
        WHERE slug = ? AND status = 'published'
    """, (slug,), label="PUBLIC_PAGE_ERROR")

    if not page:
        abort(404)

    sections = safe_query_all("""
        SELECT *
        FROM sections
        WHERE page_id = ?
        ORDER BY sort_order ASC, id ASC
    """, (page["id"],), label="PUBLIC_PAGE_SECTIONS_ERROR")

    directory_meta = safe_query_one("""
        SELECT *
        FROM directory_page_meta
        WHERE page_id = ?
    """, (page["id"],), label="PUBLIC_PAGE_META_ERROR")

    directory_listings = []
    directory_intro = None

    if directory_meta:
        directory_intro = directory_meta["intro_text"]

        directory_listings = safe_query_all("""
            SELECT *
            FROM listings
            WHERE status = 'published'
              AND LOWER(city) = LOWER(?)
              AND LOWER(state) = LOWER(?)
              AND LOWER(category) LIKE LOWER(?)
            ORDER BY featured DESC, name ASC
            LIMIT 50
        """, (
            directory_meta["city"],
            directory_meta["state"],
            f"%{directory_meta['category']}%"
        ), label="PUBLIC_PAGE_LISTINGS_ERROR")

    template_name = page["template"] or "landing_default"
    if not template_name.endswith(".html"):
        template_name = f"{template_name}.html"

    return render_template(
        template_name,
        page=page,
        sections=sections,
        directory_meta=directory_meta,
        directory_intro=directory_intro,
        directory_listings=directory_listings
    )
    
    
    
    



# -----------------------
# World map API
# -----------------------
@app.get("/api/world-map")
def api_world_map():
    data = {
        "City": ["New York", "London", "Tokyo", "Paris", "Sydney", "Cairo", "Moscow", "Rio de Janeiro", "Beijing", "Los Angeles", "Vienna"],
        "Country": ["USA", "UK", "Japan", "France", "Australia", "Egypt", "Russia", "Brazil", "China", "USA", "Austria"],
        "Latitude": [40.7128, 51.5074, 35.6762, 48.8566, -33.8688, 30.0444, 55.7558, -22.9068, 39.9042, 34.0522, 48.2081],
        "Longitude": [-74.0060, -0.1278, 139.6503, 2.3522, 151.2093, 31.2357, 37.6173, -43.1729, 116.4074, -118.2437, 16.3713]
    }
    df = pd.DataFrame(data)

    fig = px.scatter_mapbox(
        df,
        lat="Latitude",
        lon="Longitude",
        hover_name="City",
        hover_data=["Country"],
        zoom=1,
        height=600
    )

    routes = [
        ["New York", "Moscow"],
        ["Sydney", "Los Angeles"],
        ["Sydney", "Moscow"],
        ["New York", "Los Angeles"],
    ]
    colors = ['blue', 'green', 'purple', 'orange', 'yellow']

    for i, route in enumerate(routes):
        line_df = df[df["City"].isin(route)].copy()
        line_df["order"] = line_df["City"].apply(lambda x: route.index(x))
        line_df = line_df.sort_values("order")

        fig.add_trace(go.Scattermapbox(
            lat=line_df["Latitude"],
            lon=line_df["Longitude"],
            mode="lines+markers",
            marker=go.scattermapbox.Marker(size=10, color="red"),
            line=go.scattermapbox.Line(width=2, color=colors[i]),
            name=f"Route {i+1}"
        ))

    fig.update_layout(showlegend=False, mapbox_style="carto-darkmatter")
    fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})

    return jsonify(fig.to_dict())


# -----------------------
# AI Generate Route (drop-in, fixed control flow)
# -----------------------



@app.post("/admin/pages/<int:page_id>/ai-generate")
def admin_ai_generate(page_id):
    page = query_one("SELECT * FROM pages WHERE id=?", (page_id,))
    if not page:
        abort(404)

    topic = (request.form.get("topic") or page["title"] or "").strip()
    audience = (request.form.get("audience") or "general readers").strip()
    tone = (request.form.get("tone") or "clear, practical, confident").strip()

    if not topic:
        return jsonify({"error": "Please provide a topic."}), 400

    job_id = str(uuid.uuid4())
    AI_JOBS[job_id] = {
        "status": "queued",
        "result": None,
        "error": None,
        "created_at": time.time(),
    }

    AI_EXECUTOR.submit(_run_ai_job, job_id, page_id, topic, audience, tone)

    # include poll_url so the frontend doesn't have to guess
    return jsonify({"job_id": job_id, "poll_url": f"/admin/ai-jobs/{job_id}"}), 202



# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    init_db()
    create_admin_if_missing("you@example.com", "ChangeMe123!")
    app.run(debug=True, use_reloader=False, port=8525)