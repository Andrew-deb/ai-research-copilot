"""
dashboard/routes/onboarding.py — five questions, one at a time.

One route per direction rather than one per step: the step is a path segment,
so a refresh reloads the step somebody was on and the browser's back button
walks the questions backwards, both for free.

Every step can be skipped and the whole thing can be left. Both advance the
counter — see onboarding_service for why a declined question counts as
answered.
"""

from flask import Blueprint, redirect, render_template, request, url_for

from middleware.auth import current_user_id
from middleware.capabilities import require_capability
from services import onboarding_service

bp = Blueprint("onboarding", __name__)


@bp.get("/welcome")
@require_capability("notes:write")
def start():
    """
    Resume where they stopped.

    Gated on a signed-in capability rather than a page of its own for anonymous
    visitors: there is nowhere to store the answers of somebody without an
    account, so the honest thing is the sign-in prompt the decorator gives.
    """
    state = onboarding_service.state(current_user_id())
    return redirect(url_for("onboarding.step", number=state["next_step"]))


@bp.get("/welcome/<int:number>")
@require_capability("notes:write")
def step(number: int):
    user_id = current_user_id()
    state = onboarding_service.state(user_id)

    # Past the end, or past where they have reached: a URL anybody can edit
    # should not show step 5 to somebody who has answered none of them.
    if number < 1 or number > onboarding_service.TOTAL_STEPS:
        return redirect(url_for("onboarding.step", number=state["next_step"]))
    if number > state["next_step"]:
        return redirect(url_for("onboarding.step", number=state["next_step"]))

    return render_template(
        "onboarding.html",
        number=number,
        state=state,
        chosen=onboarding_service.interests(user_id),
        fields_shown=onboarding_service.FIELDS_SHOWN,
        fields_more=onboarding_service.FIELDS_MORE,
        primary_goals=onboarding_service.PRIMARY_GOALS,
        researcher_types=onboarding_service.RESEARCHER_TYPES,
        help_tasks=onboarding_service.HELP_TASKS,
        max_topics=onboarding_service.MAX_TOPICS,
    )


@bp.post("/welcome/<int:number>")
@require_capability("notes:write")
def answer(number: int):
    """
    Record a step and move on.

    Skipping posts the same form with nothing in it, so there is one path
    through here rather than two that can drift — the difference between
    answering and skipping is what the form carries, not which endpoint it
    reaches.
    """
    user_id = current_user_id()
    answers = {
        "fields": request.form.getlist("fields"),
        "topics": request.form.getlist("topics") or request.form.get("topics"),
        "primary_goal": request.form.get("primary_goal"),
        "researcher_type": request.form.get("researcher_type"),
        "help_tasks": request.form.getlist("help_tasks"),
    }

    state = onboarding_service.save_step(user_id, number, answers)

    if state["complete"]:
        return redirect(url_for("home.dashboard"))
    return redirect(url_for("onboarding.step", number=state["next_step"]))


@bp.post("/welcome/skip")
@require_capability("notes:write")
def skip():
    """Leave the whole thing. It stays reachable at /welcome afterwards."""
    onboarding_service.skip_all(current_user_id())
    return redirect(url_for("home.dashboard"))
