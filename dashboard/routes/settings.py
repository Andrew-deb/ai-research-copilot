"""
dashboard/routes/settings.py — the account's own page.

One GET and one POST per card, rather than one POST that takes everything.
A page that saves all four areas at once cannot tell "I did not change my
topics" from "I have no topics", and would quietly wipe a section somebody
never opened.
"""

from flask import Blueprint, redirect, render_template, request, url_for

from middleware.auth import current_tier, current_user_id
from middleware.capabilities import require_capability
from routes.helpers import action_response
from services import onboarding_service, settings_service

bp = Blueprint("settings", __name__)


@bp.get("/settings")
@require_capability("notes:write")
def page():
    """
    Everything about this account, in four cards.

    Gated on a signed-in capability: an anonymous visitor has no account to
    have settings for, and the decorator's sign-in prompt is a truer answer
    than an empty page.
    """
    user_id = current_user_id()
    return render_template(
        "settings.html",
        data=settings_service.overview(user_id, current_tier()),
        fields_shown=onboarding_service.FIELDS_SHOWN,
        fields_more=onboarding_service.FIELDS_MORE,
        primary_goals=onboarding_service.PRIMARY_GOALS,
        researcher_types=onboarding_service.RESEARCHER_TYPES,
        help_tasks=onboarding_service.HELP_TASKS,
        max_topics=onboarding_service.MAX_TOPICS,
    )


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
        redirect_to=url_for("settings.page"),
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
        redirect_to=url_for("settings.page"),
        flash_message="Research preferences updated.",
    )
