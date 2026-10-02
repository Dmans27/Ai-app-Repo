import re
import time

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    email               = db.Column(db.String(120), unique=True, nullable=False)
    name                = db.Column(db.String(120))
    username            = db.Column(db.String(30), unique=True, nullable=True)
    password_hash       = db.Column(db.String(255), nullable=False)
    role                = db.Column(db.String(50), default="user", nullable=False)
    favorite_categories = db.Column(db.Text, nullable=True)  # legacy restaurant-discovery field, no longer collected
    home_city           = db.Column(db.String(120), nullable=True)
    budget_style        = db.Column(db.String(50), nullable=True)  # legacy restaurant-discovery field, no longer collected
    intent_type         = db.Column(db.String(120), nullable=True)  # legacy restaurant-discovery field, no longer collected
    causes              = db.Column(db.Text, nullable=True)  # JSON array of charity cause categories the user supports
    age_range           = db.Column(db.String(20), nullable=True)
    sex                 = db.Column(db.String(30), nullable=True)
    profile_image_url   = db.Column(db.Text, nullable=True)
    onboarding_complete = db.Column(db.Boolean, default=False)
    has_seen_welcome    = db.Column(db.Boolean, default=False)  # first-open welcome popup, dismissed once and never shown again
    is_organization     = db.Column(db.Boolean, default=False)  # organization account vs. individual donor account
    org_mission         = db.Column(db.Text, nullable=True)  # organization's mission/description, mirrored onto their public listing
    stripe_account_id   = db.Column(db.String(255), nullable=True)  # Stripe Connect (Standard) account id, set once an organization completes "Connect with Stripe"
    stripe_connected_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class BusinessClaim(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    place_id      = db.Column(db.String(255))
    business_name = db.Column(db.String(255), nullable=False)
    address       = db.Column(db.String(255))
    website       = db.Column(db.String(255))
    category      = db.Column(db.String(100))
    status        = db.Column(db.String(50), default="pending")
    created_at    = db.Column(db.DateTime, default=db.func.now())


class SavedList(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title      = db.Column(db.String(255), nullable=False)
    description= db.Column(db.Text)
    slug       = db.Column(db.String(255), unique=True, nullable=False)
    is_public  = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship("User", backref="saved_lists")


class SavedPlace(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    saved_list_id = db.Column(db.Integer, db.ForeignKey("saved_list.id"), nullable=False)
    listing_id    = db.Column(db.Integer, nullable=True)
    place_id      = db.Column(db.String(255), nullable=True)
    name          = db.Column(db.String(255), nullable=False)
    address       = db.Column(db.String(255))
    website       = db.Column(db.String(255))
    category      = db.Column(db.String(100))
    photo_url     = db.Column(db.Text)
    city          = db.Column(db.String(120), nullable=True)
    state         = db.Column(db.String(120), nullable=True)
    cuisine       = db.Column(db.String(120), nullable=True)
    notes         = db.Column(db.Text)
    latitude      = db.Column(db.Float, nullable=True)
    longitude     = db.Column(db.Float, nullable=True)
    created_at    = db.Column(db.DateTime, default=db.func.now())

    saved_list = db.relationship("SavedList", backref="places")


class UserSavedList(db.Model):
    __tablename__ = "user_saved_lists"

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    saved_list_id = db.Column(db.Integer, db.ForeignKey("saved_list.id"), nullable=False)
    created_at    = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("user_id", "saved_list_id", name="uq_user_saved_list"),
    )


class Donation(db.Model):
    """One donation payment -- either a one-time gift or a single billing
    cycle of a recurring monthly donation. Created as 'pending' the moment a
    Stripe Checkout Session is started, then flipped to 'succeeded'/'failed'
    by the Stripe webhook once the payment actually clears. A monthly
    subscription gets a fresh row here for every successful renewal, so this
    table doubles as the donation history/ledger, not just a one-row-per-
    subscription pointer."""
    id                           = db.Column(db.Integer, primary_key=True)
    donor_user_id                = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    org_user_id                  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    listing_id                   = db.Column(db.Integer, nullable=True)  # the listings row this donation was made from, for display
    amount_cents                 = db.Column(db.Integer, nullable=False)  # total charged to the donor, in cents
    platform_fee_cents           = db.Column(db.Integer, nullable=False)  # Dony's 5% cut of amount_cents, in cents
    currency                     = db.Column(db.String(10), default="usd", nullable=False)
    is_recurring                 = db.Column(db.Boolean, default=False)
    status                       = db.Column(db.String(30), default="pending")  # pending, succeeded, failed, refunded, canceled
    stripe_checkout_session_id   = db.Column(db.String(255), nullable=True)
    stripe_payment_intent_id     = db.Column(db.String(255), nullable=True)
    stripe_subscription_id       = db.Column(db.String(255), nullable=True)  # set on every row belonging to a recurring subscription
    created_at                   = db.Column(db.DateTime, default=db.func.now())

    donor        = db.relationship("User", foreign_keys=[donor_user_id])
    organization = db.relationship("User", foreign_keys=[org_user_id])