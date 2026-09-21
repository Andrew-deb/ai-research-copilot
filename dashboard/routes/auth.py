"""
dashboard/routes/auth.py — Google sign-in, sign-out.

Authorization Code flow with OIDC, via Authlib. Google is the identity provider;
this application issues no token of its own. After the callback verifies the
identity, the only thing that persists is a Flask signed session cookie holding a
`user_id`.

**No JWT.** A signed session is sufficient for one application, and the Databricks
MCP server authenticates the *backend*, not the user — so there is no second service
that would need to verify an application token. Introducing one now would be a
guess about an architecture that has not been built.

Google's access and refresh tokens are deliberately **not stored**. They are used
once, inside the callback, to read the user's profile, and then discarded. Keeping
them would mean holding credentials for someone's Google account to enable nothing
this product does.
"""

import logging

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_DISCOVERY_URL
from middleware.auth import SESSION_ANON_KEY, SESSION_USER_KEY, forget_user
from services import auth_service

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

_oauth = OAuth()


def init_oauth(app) -> None:
    """Register the Google client on the app. Called once from create_app()."""
    _oauth.init_app(app)
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        # Local dev with the bypass on never reaches the OAuth routes, so a missing
        # client is not fatal here. Production refuses to boot without one - see
        # the guard in config.py.
        logger.info("Google OAuth not configured; /auth/google will report it.")
        return

    _oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )


def safe_next(raw: str | None) -> str:
    """
    A redirect target that cannot leave this site.

    Accepts only a path: it must start with a single `/`. `//evil.com` and
    `https://evil.com` are both rejected, the first because browsers read a
    protocol-relative URL as an absolute one. Without this check, `?next=` is an
    open redirect that makes a phishing link look like it came from us.
    """
    if not raw:
        return url_for("home.index")
    if not raw.startswith("/") or raw.startswith("//"):
        return url_for("home.index")
    return raw


def _auth_page(mode: str, **extra):
    """
    Both entry points render the same template with different copy.

    Sign-up and log-in are the same Google flow — we cannot know whether an
    account exists until Google tells us who this is. The split is for the
    *visitor*, who does know, and it is what lets the product greet a newcomer
    differently from someone returning.

    Which onboarding a person sees is decided by whether a row was created, not
    by which button they pressed: a returning user who clicks "Sign up" should
    not be walked through onboarding again.
    """
    copy = {
        "login": ("Log in to Research Copilot",
                  "Your library, notes and reading progress are private to your account."),
        "signup": ("Create your account",
                   "Save papers, keep notes, and get discoveries picked for your field."),
    }[mode]
    return render_template(
        "login.html",
        mode=mode,
        heading=copy[0],
        lead=copy[1],
        next_url=safe_next(request.args.get("next")),
        **extra,
    )


@bp.get("/login")
def login():
    return _auth_page("login")


@bp.get("/signup")
def signup():
    return _auth_page("signup")


@bp.get("/auth/google")
def google():
    """Send the user to Google. Authlib generates and stores state + nonce."""
    if "google" not in _oauth._clients:
        return _auth_page("login",
                          error="Google sign-in is not configured on this deployment."), 503

    # Survives the round trip in the session rather than the URL, so it cannot be
    # tampered with between here and the callback.
    session["post_login_next"] = safe_next(request.args.get("next"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    return _oauth.google.authorize_redirect(redirect_uri)


@bp.get("/auth/google/callback")
def google_callback():
    """
    Google redirects back here. Authlib verifies `state` and the id_token `nonce`
    before we see anything, so a forged or replayed callback never reaches the
    user lookup below.
    """
    try:
        token = _oauth.google.authorize_access_token()
    except Exception as exc:  # noqa: BLE001 - any failure here means "not signed in"
        logger.warning("Google callback rejected: %s: %s", type(exc).__name__, exc)
        return _auth_page("login",
                          error="Sign-in could not be completed. Please try again."), 400

    claims = token.get("userinfo") or {}
    subject, email = claims.get("sub"), claims.get("email")

    if not subject or not email:
        logger.warning("Google returned no subject/email; claims present: %s", sorted(claims))
        return _auth_page("login",
                          error="Google did not share an email address with us."), 400

    if claims.get("email_verified") is False:
        # An unverified Google email is not proof of controlling that address, and
        # accounts are keyed on it for linking.
        return _auth_page("login",
                          error="Please verify your email address with Google first."), 400

    user = auth_service.resolve_or_create_user(
        provider="google",
        subject=subject,
        email=email,
        display_name=claims.get("name"),
        avatar_url=claims.get("picture"),
    )

    # Session fixation defence: a brand-new session id for the authenticated
    # session, so a value an attacker planted before login cannot be reused after.
    destination = session.get("post_login_next") or url_for("home.index")
    session.clear()
    session[SESSION_USER_KEY] = str(user["user_id"])
    session.permanent = True
    forget_user(user["user_id"])

    logger.info("Signed in user %s", user["user_id"])
    return redirect(safe_next(destination))


@bp.post("/logout")
def logout():
    """POST, not GET: a GET logout can be triggered by an <img> tag on any page."""
    user_id = session.get(SESSION_USER_KEY)
    session.clear()
    if user_id:
        forget_user(user_id)
        logger.info("Signed out user %s", user_id)
    return redirect(url_for("home.index"))
