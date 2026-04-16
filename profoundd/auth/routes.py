"""Public user authentication routes: magic link, password, API key management."""
import secrets
from datetime import datetime, timedelta, timezone

from flask import (
    render_template, request, redirect, url_for, session, flash, current_app
)

from profoundd.auth import auth_bp
from profoundd.utils.models import db, PublicUser, SavedAlert
from profoundd.auth.email import send_magic_link


def _current_user():
    """Get current logged-in public user, or None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(PublicUser, user_id)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()

    if not email:
        flash("Please enter your email address.", "error")
        return render_template("auth/login.html")

    user = PublicUser.query.filter_by(email=email).first()

    # Password login (if user has a password set)
    if password and user and user.check_password(password):
        session["user_id"] = user.id
        session["user_email"] = user.email
        session["user_logged_in"] = True
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()
        return redirect(request.args.get("next", "/"))

    if password and user and user.password_hash:
        flash("Incorrect password.", "error")
        return render_template("auth/login.html")

    # Magic link flow
    if not user:
        # Auto-create account on first magic link request
        user = PublicUser(email=email)
        db.session.add(user)

    # Generate magic token
    token = secrets.token_urlsafe(48)
    user.magic_token = token
    user.magic_token_expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    db.session.commit()

    # Send email
    magic_url = url_for("auth.magic_login", token=token, _external=True)
    sent = send_magic_link(email, magic_url)

    if sent:
        flash("Check your email for a login link! It expires in 15 minutes.", "success")
    else:
        # Fallback: show the link directly (for dev/testing when SendGrid isn't configured)
        flash(f"Email delivery not configured. Dev login link: {magic_url}", "info")

    return render_template("auth/login.html")


@auth_bp.route("/magic/<token>")
def magic_login(token):
    """Verify magic link token and log user in."""
    user = PublicUser.query.filter_by(magic_token=token).first()

    if not user:
        flash("Invalid or expired login link.", "error")
        return redirect(url_for("auth.login"))

    if user.magic_token_expires and user.magic_token_expires < datetime.now(timezone.utc):
        flash("Login link has expired. Please request a new one.", "error")
        user.magic_token = None
        db.session.commit()
        return redirect(url_for("auth.login"))

    # Log in
    session["user_id"] = user.id
    session["user_email"] = user.email
    session["user_logged_in"] = True
    user.magic_token = None
    user.magic_token_expires = None
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    # If no terms agreement yet, redirect to terms
    if not user.agreed_to_terms:
        return redirect(url_for("auth.terms"))

    flash("You're logged in!", "success")
    return redirect("/")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    agree = request.form.get("agree_terms")

    if not email:
        flash("Email is required.", "error")
        return render_template("auth/register.html")

    if not agree:
        flash("You must agree to the terms of use.", "error")
        return render_template("auth/register.html")

    existing = PublicUser.query.filter_by(email=email).first()
    if existing:
        flash("An account with this email already exists. Try logging in.", "error")
        return redirect(url_for("auth.login"))

    user = PublicUser(email=email, agreed_to_terms=True, agreed_at=datetime.now(timezone.utc))
    if password:
        user.set_password(password)
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    session["user_email"] = user.email
    session["user_logged_in"] = True

    flash("Account created! You can now use AI analysis.", "success")
    return redirect(url_for("auth.settings"))


@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("user_email", None)
    session.pop("user_logged_in", None)
    flash("You've been logged out.", "info")
    return redirect("/")


@auth_bp.route("/settings", methods=["GET", "POST"])
def settings():
    user = _current_user()
    if not user:
        return redirect(url_for("auth.login", next=url_for("auth.settings")))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "api_key":
            provider = request.form.get("provider", "").strip()
            api_key = request.form.get("api_key", "").strip()

            if provider not in ("anthropic", "openai", "xai"):
                flash("Invalid provider.", "error")
            elif not api_key:
                # Clear key
                user.set_api_key("", "")
                db.session.commit()
                flash("API key removed.", "info")
            else:
                user.set_api_key(api_key, provider)
                db.session.commit()
                flash(f"API key saved for {provider}.", "success")

        elif action == "password":
            new_pass = request.form.get("new_password", "").strip()
            if len(new_pass) < 8:
                flash("Password must be at least 8 characters.", "error")
            else:
                user.set_password(new_pass)
                db.session.commit()
                flash("Password updated.", "success")

        return redirect(url_for("auth.settings"))

    return render_template("auth/settings.html", user=user)


@auth_bp.route("/terms")
def terms():
    return render_template("auth/terms.html")


@auth_bp.route("/agree-terms", methods=["POST"])
def agree_terms():
    user = _current_user()
    if not user:
        return redirect(url_for("auth.login"))
    user.agreed_to_terms = True
    user.agreed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash("Terms accepted. Welcome to Profoundd!", "success")
    return redirect(url_for("auth.settings"))


# --- Saved search alerts ---

@auth_bp.route("/alerts")
def alerts_list():
    """List the current user's saved alerts."""
    user = _current_user()
    if not user:
        return redirect(url_for("auth.login", next=url_for("auth.alerts_list")))
    alerts = db.session.query(SavedAlert).filter_by(user_id=user.id).order_by(SavedAlert.created_at.desc()).all()
    return render_template("auth/alerts.html", user=user, alerts=alerts)


@auth_bp.route("/alerts/create", methods=["POST"])
def alerts_create():
    """Create a new saved alert."""
    user = _current_user()
    if not user:
        return redirect(url_for("auth.login"))
    query = request.form.get("query", "").strip()
    if not query or len(query) < 2:
        flash("Query is required.", "error")
        return redirect(url_for("auth.alerts_list"))
    category = (request.form.get("category", "all") or "all").strip()
    frequency = request.form.get("frequency", "daily")
    if frequency not in ("immediate", "daily", "weekly"):
        frequency = "daily"
    alert = SavedAlert(
        user_id=user.id,
        query=query[:500],
        category=category[:50],
        frequency=frequency,
        unsub_token=secrets.token_urlsafe(32),
    )
    db.session.add(alert)
    db.session.commit()
    flash(f'Alert saved: "{query}" ({frequency})', "success")
    return redirect(url_for("auth.alerts_list"))


@auth_bp.route("/alerts/<int:alert_id>/toggle", methods=["POST"])
def alerts_toggle(alert_id):
    user = _current_user()
    if not user:
        return redirect(url_for("auth.login"))
    alert = db.session.query(SavedAlert).filter_by(id=alert_id, user_id=user.id).first()
    if alert:
        alert.enabled = not alert.enabled
        db.session.commit()
        flash(f"Alert {'enabled' if alert.enabled else 'paused'}.", "success")
    return redirect(url_for("auth.alerts_list"))


@auth_bp.route("/alerts/<int:alert_id>/delete", methods=["POST"])
def alerts_delete(alert_id):
    user = _current_user()
    if not user:
        return redirect(url_for("auth.login"))
    alert = db.session.query(SavedAlert).filter_by(id=alert_id, user_id=user.id).first()
    if alert:
        db.session.delete(alert)
        db.session.commit()
        flash("Alert deleted.", "success")
    return redirect(url_for("auth.alerts_list"))


@auth_bp.route("/alerts/unsubscribe/<token>")
def alerts_unsubscribe(token):
    """One-click unsubscribe from an alert (no login required — uses token)."""
    alert = db.session.query(SavedAlert).filter_by(unsub_token=token).first()
    if not alert:
        return render_template("auth/unsubscribed.html", found=False)
    alert.enabled = False
    db.session.commit()
    return render_template("auth/unsubscribed.html", found=True, alert=alert)
