"""
dashboard/services/onboarding_service.py — five questions, none of them required.

What this collects and what it does NOT do are both deliberate.

**It collects.** Nothing reads these answers yet: §8g of the design marks the
"For You" surface documentation-only for Phase 3. Collecting first is the right
order — an empty interest table makes personalisation impossible to build, and a
personalisation surface over no data is impossible to judge.

**Every step is skippable, and a skip still advances.** The question was put to
somebody and they answered it by declining; treating that as "not asked" would
mean asking again, which is how an optional flow becomes a compulsory one.

**Field choices are stored one row per field, never merged.** A 2026-09-20
decision rejected grouped chips for exactly this reason: these rows carry
`confidence = 0.9`, so a chip covering two fields would put a high-confidence
interest in somebody's profile that they never chose. A biochemist who picks
Biochemistry must not also be recorded as researching agriculture.
"""

from __future__ import annotations

import logging

from exceptions import ValidationError
from repositories import lakebase

logger = logging.getLogger(__name__)

TOTAL_STEPS = 5

# Onboarding is the one source a person states in their own words, so it is
# trusted far above anything inferred from behaviour later.
SOURCE = "explicit_onboarding"
CONFIDENCE = 0.9

# --- The 26-field taxonomy ---------------------------------------------------
# Thirteen shown, thirteen behind "search all fields". The split is about what
# is offered first and nothing else: one chip is exactly one field, the set is
# the canonical 26, and neither half groups or expands anything.
#
# Alphabetical, so the ordering implies no hierarchy — a list with Computer
# Science at the top tells a nurse what this product thinks of their subject.
FIELDS_SHOWN = (
    "Agricultural & Biological Sciences",
    "Biochemistry, Genetics & Molecular Biology",
    "Business, Management & Accounting",
    "Chemistry",
    "Computer Science",
    "Economics, Econometrics & Finance",
    "Engineering",
    "Environmental Science",
    "Mathematics",
    "Medicine",
    "Physics & Astronomy",
    "Psychology",
    "Social Sciences",
)

FIELDS_MORE = (
    "Arts & Humanities",
    "Chemical Engineering",
    "Decision Sciences",
    "Dentistry",
    "Earth & Planetary Sciences",
    "Energy",
    "Health Professions",
    "Immunology & Microbiology",
    "Materials Science",
    "Neuroscience",
    "Nursing",
    "Pharmacology, Toxicology & Pharmaceutics",
    "Veterinary",
)

ALL_FIELDS = FIELDS_SHOWN + FIELDS_MORE

PRIMARY_GOALS = (
    "Keep up with new work",
    "Run a literature review",
    "Find methods for a project",
    "Explore a new field",
    "Teaching or supervising",
    "Just curious",
)

RESEARCHER_TYPES = (
    "PhD student",
    "Postdoc",
    "Faculty / PI",
    "Industry researcher",
    "Engineer or data scientist",
    "Master's / undergraduate",
    "Independent researcher",
    "Prefer not to say",
)

HELP_TASKS = (
    "Finding papers I'd otherwise miss",
    "Summarising what a paper found",
    "Comparing methods across papers",
    "Tracking what I've read",
    "Building reading lists",
    "Drafting related-work sections",
)

# Deliberately absent: age, gender, location, employer. None of it improves
# discovery, and asking for it would make a research tool feel like a form.

MAX_TOPICS = 5
TOPIC_MAX_CHARS = 80


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def state(user_id: str) -> dict:
    """Where this person is, and what they have already said."""
    profile = lakebase.get_user_profile(user_id) or {}
    step = int(profile.get("onboarding_step") or 0)
    return {
        "step": step,
        "next_step": min(step + 1, TOTAL_STEPS),
        "total": TOTAL_STEPS,
        "complete": bool(profile.get("onboarding_completed_at")),
        "profile": profile,
    }


def needs_onboarding(user_id: str | None) -> bool:
    """
    Whether to send somebody to onboarding after signing in.

    False for anyone who has finished it AND for anyone who has not — once they
    have started. Only a brand-new account is redirected; after that the flow is
    something they return to, not something that intercepts them. Being sent
    back into a form on every sign-in is how people learn to rush through one.
    """
    if not user_id:
        return False
    profile = lakebase.get_user_profile(user_id)
    return profile is None


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def _clean_topics(raw) -> list[str]:
    """
    Free-text topics, tidied.

    Whitespace collapsed and compared case-insensitively for duplicates, but
    stored as typed: "Causal Inference" is how somebody wrote it and is how it
    should read back to them, even though it is the same interest as "causal
    inference".
    """
    if isinstance(raw, str):
        raw = raw.split(",")

    seen: dict[str, str] = {}
    for item in (raw or []):
        topic = " ".join(str(item).split())
        if not topic:
            continue
        if len(topic) > TOPIC_MAX_CHARS:
            raise ValidationError(
                f"Keep each topic under {TOPIC_MAX_CHARS} characters.")
        seen.setdefault(topic.lower(), topic)
        if len(seen) > MAX_TOPICS:
            raise ValidationError(f"Pick up to {MAX_TOPICS} topics.")
    return list(seen.values())


def _record_interests(user_id: str, kind: str, values: list[str]) -> None:
    """
    Replace this kind's onboarding answers with the ones just given.

    Cleared first, because a step is a whole answer rather than an addition:
    somebody who returns and unticks Chemistry means they are not a chemist,
    and an INSERT-only step would leave them one forever.

    Scoped to this source, so deselecting a field here never deletes the
    evidence that they keep reading that field's papers — a different claim,
    made by a different mechanism, which is the entire reason source is part of
    the key.
    """
    lakebase.clear_interests(user_id, kind, SOURCE)
    for value in values:
        lakebase.record_interest(user_id, kind, value, SOURCE, CONFIDENCE)


def save_step(user_id: str, step: int, answers: dict) -> dict:
    """
    Record one step's answer and advance.

    An unknown option is dropped rather than refused. These arrive from a form
    anybody can edit, and the cost of a bad value is a wrong row in a profile —
    not worth an error page in the middle of somebody's first five minutes. The
    known values are the only ones that can appear, so nothing arbitrary is
    stored.
    """
    if step < 1 or step > TOTAL_STEPS:
        raise ValidationError("That step does not exist.")

    profile: dict = {}

    if step == 1:
        chosen = [f for f in (answers.get("fields") or []) if f in ALL_FIELDS]
        _record_interests(user_id, "field", chosen)

    elif step == 2:
        _record_interests(user_id, "topic", _clean_topics(answers.get("topics")))

    elif step == 3:
        goal = answers.get("primary_goal")
        profile["primary_goal"] = goal if goal in PRIMARY_GOALS else None

    elif step == 4:
        kind = answers.get("researcher_type")
        profile["researcher_type"] = kind if kind in RESEARCHER_TYPES else None

    elif step == 5:
        tasks = [t for t in (answers.get("help_tasks") or []) if t in HELP_TASKS]
        profile["help_tasks"] = tasks

    # Never backwards: somebody who returns to step 2 from the finished flow has
    # not un-answered steps 3 to 5.
    current = int((lakebase.get_user_profile(user_id) or {}).get("onboarding_step") or 0)
    profile["onboarding_step"] = max(current, step)
    profile["complete"] = step >= TOTAL_STEPS

    lakebase.upsert_user_profile(user_id, profile)
    return state(user_id)


def skip_all(user_id: str) -> dict:
    """
    Leave the whole thing, now.

    Marked complete rather than abandoned half-done, because the alternative is
    asking again next time — and a flow that reappears after being dismissed is
    not optional, whatever its buttons say. It stays reachable from settings for
    anybody who changes their mind.
    """
    lakebase.upsert_user_profile(
        user_id, {"onboarding_step": TOTAL_STEPS, "complete": True})
    return state(user_id)


def interests(user_id: str) -> dict[str, list[str]]:
    """What this person has told us, by kind. Read back into the form."""
    rows = lakebase.get_user_interests(user_id, source=SOURCE)
    out: dict[str, list[str]] = {"field": [], "topic": []}
    for row in rows:
        out.setdefault(row["kind"], []).append(row["value"])
    return out
