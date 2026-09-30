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
import time

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_DISCOVERY_URL
from middleware.auth import (
    REAUTH_KEY,
    SESSION_ANON_KEY,
    SESSION_TOKEN_KEY,
    SESSION_USER_KEY,
    forget_session,
    forget_user,
)
from repositories import lakebase, sessions
from services import auth_service, onboarding_service

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
    A redirect target that cannot leave this site, or "" when there isn't one.

    Accepts only a path: it must start with a single `/`. `//evil.com` and
    `https://evil.com` are both rejected, the first because browsers read a
    protocol-relative URL as an absolute one. Without this check, `?next=` is an
    open redirect that makes a phishing link look like it came from us.

    Empty means "nobody asked to go anywhere", and callers supply their own
    default. It used to return the home page instead, which erased exactly that
    distinction — and with it the only signal telling a brand-new account apart
    from someone returning. Every new user was sent to the dashboard rather than
    onboarding, because `if not asked_for` could never be true once `asked_for`
    was always a URL.
    """
    if not raw:
        return ""
    if not raw.startswith("/") or raw.startswith("//"):
        return ""
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


@bp.get("/auth/reauth")
def reauth():
    """
    Prove this account again, for something a session alone should not authorise.

    `prompt=login` is the whole point: without it Google recognises the existing
    session and returns instantly, which proves the browser still has a cookie —
    exactly the thing already in doubt. Forcing the credential is what makes this
    a check rather than a redirect.
    """
    if "google" not in _oauth._clients:
        return _auth_page("login",
                          error="Google sign-in is not configured on this deployment."), 503

    session["reauth_next"] = safe_next(request.args.get("next"))
    return _oauth.google.authorize_redirect(
        url_for("auth.google_callback", _external=True), prompt="login")


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

    # Re-authentication returns through this same callback, and must not be
    # allowed to become a sign-in. The account coming back has to be the account
    # already in the session: without that check, proving *a* Google account
    # would authorise deleting *this* one, which is the opposite of a check.
    reauth_next = session.pop("reauth_next", None)
    if reauth_next is not None:
        signed_in = session.get(SESSION_USER_KEY)
        existing = lakebase.get_user_by_provider("google", subject)

        if not signed_in or not existing or str(existing["user_id"]) != str(signed_in):
            logger.warning("Re-auth returned a different account; refusing.")
            session.pop(REAUTH_KEY, None)
            return redirect(url_for("home.index"))

        session[REAUTH_KEY] = time.time()
        return redirect(safe_next(reauth_next) or url_for("home.index"))

    user = auth_service.resolve_or_create_user(
        provider="google",
        subject=subject,
        email=email,
        display_name=claims.get("name"),
        avatar_url=claims.get("picture"),
    )

    # Session fixation defence: a brand-new session id for the authenticated
    # session, so a value an attacker planted before login cannot be reused after.
    # Read BEFORE the clear below, and kept: the onboarding check needs to know
    # whether they asked for somewhere specific, and after session.clear() there
    # is nothing left to ask.
    asked_for = session.get("post_login_next")
    session.clear()
    session[SESSION_USER_KEY] = str(user["user_id"])
    session[SESSION_TOKEN_KEY] = start_session(user["user_id"])
    session.permanent = True
    forget_user(user["user_id"])

    logger.info("Signed in user %s", user["user_id"])

    # A brand-new account goes to onboarding; anyone who has answered anything
    # at all goes where they were headed. Only the FIRST sign-in is intercepted
    # — being dropped into a form on every return is how people learn to click
    # through one without reading it.
    #
    # An explicit `next` wins regardless: somebody who followed a link to a
    # paper asked for that paper, and onboarding can wait for the dashboard.
    if not asked_for and onboarding_service.needs_onboarding(user["user_id"]):
        return redirect(url_for("onboarding.start"))

    # Re-sanitised rather than trusted: the value was checked on the way in, but
    # a session minted by an older build is not something to redirect on faith.
    return redirect(safe_next(asked_for) or url_for("home.index"))


def start_session(user_id: str) -> str:
    """
    Mint a session token and record the browser presenting it.

    `request.remote_addr` is the real client address because ProxyFix is applied
    to the whole app — without it every device row would read as Render's proxy,
    which is the same value for everybody and tells nobody anything.

    A failure to record is logged and swallowed: the token still works, so a
    database blip costs the device list an entry rather than costing somebody
    their sign-in.
    """
    token = sessions.new_token()
    try:
        sessions.record(
            user_id,
            token,
            user_agent=request.headers.get("User-Agent"),
            ip_address=request.remote_addr,
        )
    except Exception as exc:  # noqa: BLE001 - never fail a sign-in over telemetry
        logger.warning("Could not record session for %s: %s", user_id, exc)
    return token


@bp.post("/logout")
def logout():
    """POST, not GET: a GET logout can be triggered by an <img> tag on any page."""
    user_id = session.get(SESSION_USER_KEY)
    token = session.get(SESSION_TOKEN_KEY)
    session.clear()

    if token:
        # Revoked, not just forgotten. Clearing the cookie stops this browser
        # presenting it, but the row is what makes the token dead everywhere —
        # including in a copy of the cookie taken before logout.
        try:
            sessions.revoke_by_token(token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not revoke session on logout: %s", exc)
        forget_session(token)

    if user_id:
        forget_user(user_id)
        logger.info("Signed out user %s", user_id)
    return redirect(url_for("home.index"))
