from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User

auth = Blueprint("auth", __name__)


@auth.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        if not email or not password:
            flash("Email and password required")
            return redirect(url_for("auth.signup"))

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("Email already exists")
            return redirect(url_for("auth.signup"))

        user = User(email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        return redirect(url_for("onboarding"))

    return render_template("signup.html")