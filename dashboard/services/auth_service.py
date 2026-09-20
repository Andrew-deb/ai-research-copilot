"""
dashboard/services/auth_service.py — resolving an external identity to an account.

One job: turn "Google says this is subject X with email Y" into a `users` row,
without ever letting a change at the provider hand someone else's account away.
"""

import logging

from repositories import lakebase

logger = logging.getLogger(__name__)


def resolve_or_create_user(provider: str, subject: str, email: str,
                           display_name: str | None = None,
                           avatar_url: str | None = None) -> dict:
    """
    Find or create the account behind a verified provider identity.

    Matching order, and the order is the security-relevant part:

      1. (provider, subject) — Google's `sub` is immutable for the life of the
         account. This is the real key.

      2. email — only to *link* an account that predates OAuth, which is how the
         existing dev/demo row keeps its collections and notes instead of being
         orphaned by a second row with the same address.

    Email is never used to match an account that already has a different subject.
    Google allows an account's address to change, so "whoever presents this email"
    is not an identity: if a previous owner's address were reassigned, matching on
    email alone would hand them the original account. The check below refuses that
    and creates a separate account instead.
    """
    existing = lakebase.get_user_by_provider(provider, subject)
    if existing:
        lakebase.touch_user_login(
            existing["user_id"], display_name=display_name, avatar_url=avatar_url
        )
        return existing

    by_email = lakebase.get_user_by_email(email)
    if by_email:
        claimed_by = by_email.get("provider_subject")
        if claimed_by and claimed_by != subject:
            logger.warning(
                "Email %s already belongs to provider subject %s; not linking to %s.",
                email, claimed_by, subject,
            )
            raise PermissionError(
                "That email address is already linked to a different account."
            )

        logger.info("Linking existing account %s to %s:%s", by_email["user_id"], provider, subject)
        return lakebase.link_user_provider(
            user_id=by_email["user_id"],
            provider=provider,
            subject=subject,
            display_name=display_name,
            avatar_url=avatar_url,
        )

    logger.info("Creating account for %s:%s", provider, subject)
    return lakebase.create_oauth_user(
        provider=provider,
        subject=subject,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
    )
