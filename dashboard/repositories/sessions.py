"""
dashboard/repositories/sessions.py — the server side of a signed cookie.

The cookie carries a random token; this table decides whether that token still
counts. Everything here works on the SHA-256 of the token, never the token
itself, so a dump of the table hands nobody a working session.

One design note worth keeping: `touch_and_validate` is a single UPDATE that both
checks the session and records the activity, because this database sits across a
network with a round-trip floor of several hundred milliseconds. Asking "is this
live?" and then saying "it was just used" separately would double that on every
check for no gain — the answer to the first question is exactly the set of rows
the second one wants to write.

No Flask state in this layer.
"""

from __future__ import annotations

import hashlib
import secrets

from repositories.lakebase import run_query, run_write

# 32 bytes from `secrets`, url-safe. Long enough that guessing is not a threat
# model, which is why a plain SHA-256 is the right digest here — there is no
# low-entropy input for an attacker to grind against.
TOKEN_BYTES = 32


def new_token() -> str:
    """A fresh session token, for the cookie. Never stored as-is."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def record(user_id: str, token: str, user_agent: str | None,
           ip_address: str | None) -> dict | None:
    """Register a newly signed-in browser."""
    return run_write(
        """
        INSERT INTO user_sessions (user_id, session_hash, user_agent, ip_address)
        VALUES (%s, %s, %s, %s)
        RETURNING session_id, created_at;
        """,
        (user_id, digest(token), (user_agent or "")[:500], ip_address),
        returning=True,
    )


def touch_and_validate(token: str) -> dict | None:
    """
    Is this session live, and if so mark it as used just now.

    One statement for both questions — see the module docstring. Returns None for
    a token that was never issued and for one that has been revoked; the caller
    treats both the same way, because "signed out" is the honest answer to each.
    """
    return run_write(
        """
        UPDATE user_sessions
           SET last_seen_at = now()
         WHERE session_hash = %s
           AND revoked_at IS NULL
        RETURNING session_id, user_id;
        """,
        (digest(token),),
        returning=True,
    )


def list_active(user_id: str) -> list[dict]:
    """Live sessions, most recently used first — what the device list shows."""
    return run_query(
        """
        SELECT session_id, session_hash, user_agent, ip_address,
               created_at, last_seen_at
          FROM user_sessions
         WHERE user_id = %s
           AND revoked_at IS NULL
         ORDER BY last_seen_at DESC;
        """,
        (user_id,),
    )


# ---------------------------------------------------------------------------
# Revoking
#
# Revoked rows are marked, never deleted. "Signed out at 14:02 from an address in
# Lagos" is the answer somebody needs after losing a laptop, and a DELETE throws
# exactly that away.
# ---------------------------------------------------------------------------

def revoke_one(user_id: str, session_id: str) -> int:
    """
    Sign one device out. Scoped to the owner, so a guessed id revokes nothing.
    """
    return run_write(
        """
        UPDATE user_sessions SET revoked_at = now()
         WHERE session_id = %s AND user_id = %s AND revoked_at IS NULL;
        """,
        (session_id, user_id),
    )


def revoke_by_token(token: str) -> int:
    """
    Revoke the session presenting this token — what logout does.

    Clearing the cookie only stops THIS browser presenting it. Revoking the row
    is what kills a copy taken beforehand, which is the difference between
    logging out and merely appearing to.
    """
    return run_write(
        "UPDATE user_sessions SET revoked_at = now() "
        "WHERE session_hash = %s AND revoked_at IS NULL;",
        (digest(token),),
    )


def revoke_others(user_id: str, keep_token: str) -> int:
    """
    Sign out everywhere except here.

    Keeping the current session is the point: somebody worried about a device
    they no longer control should not have to sign in again on the one they are
    holding, and being logged out by your own security action reads as a failure.
    """
    return run_write(
        """
        UPDATE user_sessions SET revoked_at = now()
         WHERE user_id = %s AND session_hash <> %s AND revoked_at IS NULL;
        """,
        (user_id, digest(keep_token)),
    )


def revoke_all(user_id: str) -> int:
    """Every session, including the current one. Used when deleting an account."""
    return run_write(
        "UPDATE user_sessions SET revoked_at = now() "
        "WHERE user_id = %s AND revoked_at IS NULL;",
        (user_id,),
    )
