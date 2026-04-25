import re
import time

from flask import Flask, render_template, request, redirect, url_for, flash, session, Blueprint
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)



import os

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///cms.db"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="user", nullable=False)
    favorite_categories = db.Column(db.Text, nullable=True)
    home_city = db.Column(db.String(120), nullable=True)
    budget_style = db.Column(db.String(50), nullable=True)
    intent_type = db.Column(db.String(120), nullable=True)
    profile_image_url = db.Column(db.Text, nullable=True)
    onboarding_complete = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    
    
class BusinessClaim(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    place_id = db.Column(db.String(255))

    business_name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(255))
    website = db.Column(db.String(255))
    category = db.Column(db.String(100))

    status = db.Column(db.String(50), default="pending")  # pending / approved / rejected

    created_at = db.Column(db.DateTime, default=db.func.now())
    

    
    

class SavedList(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    is_public = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship("User", backref="saved_lists")
    
    
    
    
class SavedPlace(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    saved_list_id = db.Column(db.Integer, db.ForeignKey("saved_list.id"), nullable=False)

    listing_id = db.Column(db.Integer, nullable=True)
    place_id = db.Column(db.String(255), nullable=True)

    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(255))
    website = db.Column(db.String(255))
    category = db.Column(db.String(100))
    photo_url = db.Column(db.String(500))
    city = db.Column(db.String(120), nullable=True)
    state = db.Column(db.String(120), nullable=True)
    cuisine = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text)

    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, default=db.func.now())

    saved_list = db.relationship("SavedList", backref="places")
    

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


@login_manager.user_loader
def load_user(user_id):
    print("[USER_LOADER]", user_id, flush=True)
    user = User.query.get(int(user_id))
    print("[USER_LOADER_RESULT]", user.email if user else None, flush=True)
    return user


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

        user = User(name=name, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        print("[SESSION_AFTER_SIGNUP]", dict(session), flush=True)
        flash("Account created successfully.")
        return redirect(url_for("account"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    print("[LOGIN_ROUTE]", request.method, flush=True)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        print("[LOGIN_ATTEMPT]", email, flush=True)

        user = User.query.filter_by(email=email).first()
        print("[LOGIN_FOUND_USER]", user.id if user else None, flush=True)

        if not user or not user.check_password(password):
            print("[LOGIN_FAILED]", flush=True)
            flash("Invalid email or password.")
            return redirect(url_for("login"))

        login_user(user)
        print("[LOGIN_SUCCESS]", user.id, user.email, flush=True)
        print("[SESSION_AFTER_LOGIN]", dict(session), flush=True)

        flash("Welcome back.")
        return redirect(url_for("account"))

    return render_template("login.html")

import re

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
    saved_list = SavedList.query.filter_by(slug=slug, is_public=True).first_or_404()
    return render_template("public_list.html", saved_list=saved_list)














@app.route("/account")
@login_required
def account():
    lists = SavedList.query.filter_by(user_id=current_user.id).order_by(SavedList.created_at.desc()).all()
    return render_template("account.html", user=current_user, lists=lists)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.")
    return redirect(url_for("login"))