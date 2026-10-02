"""
dashboard/routes/settings.py — the account's own page.

One GET and one POST per card, rather than one POST that takes everything.
A page that saves all four areas at once cannot tell "I did not change my
topics" from "I have no topics", and would quietly wipe a section somebody
never opened.
"""

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)

from middleware.auth import SESSION_TOKEN_KEY, current_tier, current_user_id
from middleware.capabilities import require_capability
from routes.helpers import action_response
from services import (account_service, onboarding_service, settings_service,
                      usage_service)

bp = Blueprint("settings", __name__)

_SECTIONS = ("preferences", "account", "usage", "security", "appearance",
             "keyboard")


@bp.get("/settings")
@require_capability("notes:write")
def page():
    """
    Everything about this account, in four sections.

    Gated on a signed-in capability: an anonymous visitor has no account to
    have settings for, and the decorator's sign-in prompt is a truer answer
    than an empty page.
    """
    user_id = current_user_id()
    section = request.args.get("section")

    return render_template(
        "settings.html",
        section=section if section in _SECTIONS else "preferences",
        data=settings_service.overview(
            user_id, current_tier(), session.get(SESSION_TOKEN_KEY)),
        # Composed here rather than inside settings_service: usage_service
        # already imports settings_service for its labels, and having them
        # import each other is a cycle waiting for an unlucky import order.
        today=usage_service.today(user_id, current_tier()),
        fields_shown=onboarding_service.FIELDS_SHOWN,
        fields_more=onboarding_service.FIELDS_MORE,
        primary_goals=onboarding_service.PRIMARY_GOALS,
        researcher_types=onboarding_service.RESEARCHER_TYPES,
        help_tasks=onboarding_service.HELP_TASKS,
        max_topics=onboarding_service.MAX_TOPICS,
    )


@bp.get("/settings/usage")
@require_capability("notes:write")
def usage_analytics():
    """
    The Analytics tab's data, fetched when the tab is opened rather than with
    the page.

    Two aggregate queries against a database with a round-trip floor of several
    hundred milliseconds, for a tab most visits never open. Rendering them with
    every /settings load would put that cost on somebody changing their display
    name.
    """
    days = request.args.get("days", usage_service.DEFAULT_WINDOW, type=int)
    return jsonify(usage_service.analytics(
        current_user_id(), days, request.args.get("feature")))


@bp.post("/settings/profile")
@require_capability("notes:write")
def save_profile():
    """
    The display name, and only the display name.

    Email and provider are the verified identity this application
    authenticates against. They are shown because they are useful to see, and
    are not accepted here at all — not validated and rejected, simply never
    read from the form, so no amount of editing the page can offer one.
    """
    settings_service.update_display_name(
        current_user_id(), request.form.get("display_name"))

    return action_response(
        {"ok": True},
        redirect_to=url_for("settings.page", section="account"),
        flash_message="Name updated.",
    )


@bp.post("/settings/research")
@require_capability("notes:write")
def save_research():
    """
    One card's worth of research preferences.

    `section` says which card posted, so only that card's answers are touched.
    Without it a form that omits topics would read as "I have none".
    """
    section = (request.form.get("section") or "").strip()
    answers: dict = {}

    if section == "fields":
        answers["fields"] = request.form.getlist("fields")
    elif section == "topics":
        answers["topics"] = request.form.getlist("topics")
    elif section == "context":
        answers["primary_goal"] = request.form.get("primary_goal")
        answers["researcher_type"] = request.form.get("researcher_type")
        answers["help_tasks"] = request.form.getlist("help_tasks")

    if answers:
        settings_service.update_research(current_user_id(), answers)

    return action_response(
        {"ok": True},
        redirect_to=url_for("settings.page", section="preferences"),
        flash_message="Research preferences updated.",
    )


@bp.post("/settings/incognito")
@require_capability("notes:write")
def save_incognito():
    """
    Whether searches are written to history.

    Stored on `users`, never on `user_profiles` — a preference written there
    would create the row `needs_onboarding()` tests for, and hide the welcome
    flow from somebody who had never seen it.
    """
    enabled = request.form.get("incognito") == "on"
    account_service.set_incognito(current_user_id(), enabled)

    return action_response(
        {"ok": True, "incognito": enabled},
        redirect_to=url_for("settings.page", section="preferences"),
        flash_message=("Incognito mode is on — searches will not be saved."
                       if enabled else "Incognito mode is off."),
    )
