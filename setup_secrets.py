"""
setup_secrets.py — Databricks Secret Scope Setup

PURPOSE:
    Creates Databricks secret scopes and stores API keys for all services
    used by the AI Research & Learning Copilot. This is a one-time setup
    task that should be run from a machine with Databricks CLI configured
    or from a Databricks notebook.

    After running this, all application components (MCP server, dashboard,
    Spark notebook) can retrieve secrets via:
        WorkspaceClient().secrets.get_secret(scope=SCOPE, key=KEY)

DESIGN NOTES:
    Secrets are stored in Databricks-managed secret scopes rather than
    in .env files or hardcoded values. This provides:
    - Encryption at rest and in transit
    - Access control via ACLs
    - Audit logging of secret access
    - Safe for multi-user workspaces (each user can have their own scope)

    The .env file is used ONLY for local development. In production
    (Databricks Apps), all secrets come from the secret scope.

USAGE:
    1. Set your values in the .env file first
    2. Run: python setup_secrets.py
    3. Verify: databricks secrets list-secrets --scope database
"""

import base64
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def create_scope_and_secret(scope: str, key: str, value: str, description: str) -> None:
    """Create a secret scope (if needed) and store a secret in it."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.workspace import AclPermission

    w = WorkspaceClient()

    # Create scope (idempotent — errors if scope already exists, which is fine)
    try:
        w.secrets.create_scope(scope=scope)
        print(f"  ✅ Created scope: '{scope}'")
    except Exception as e:
        if "RESOURCE_ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
            print(f"  ℹ️  Scope '{scope}' already exists (reusing)")
        else:
            print(f"  ❌ Failed to create scope '{scope}': {e}")
            return

    # Store the secret (base64-encoded, same convention as Day 2/3 labs)
    try:
        encoded = base64.b64encode(value.encode("utf-8")).decode("utf-8")
        w.secrets.put_secret(scope=scope, key=key, string_value=value)
        print(f"  ✅ Stored secret: '{scope}/{key}' — {description}")
    except Exception as e:
        print(f"  ❌ Failed to store secret '{scope}/{key}': {e}")


def main():
    print("=" * 60)
    print("🔐 Setting up Databricks Secret Scopes")
    print("=" * 60)

    # --- Lakebase connection URL ---
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        print("\n📦 Lakebase (Database)")
        create_scope_and_secret(
            scope="database",
            key="lakebase-url",
            value=db_url,
            description="Lakebase Postgres connection URL"
        )
    else:
        print("\n⚠️  DATABASE_URL not set in .env — skipping Lakebase secret")

    # --- OpenAlex (uses email in header, not a secret key) ---
    oa_email = os.getenv("OPENALEX_EMAIL")
    if oa_email:
        print("\n📦 OpenAlex")
        create_scope_and_secret(
            scope="openalex",
            key="email",
            value=oa_email,
            description="OpenAlex polite pool email"
        )
    else:
        print("\n⚠️  OPENALEX_EMAIL not set — skipping (will use anonymous pool)")

    # --- Semantic Scholar API key ---
    s2_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    if s2_key:
        print("\n📦 Semantic Scholar")
        create_scope_and_secret(
            scope="semantic-scholar",
            key="api-key",
            value=s2_key,
            description="Semantic Scholar API key"
        )
    else:
        print("\n⚠️  SEMANTIC_SCHOLAR_API_KEY not set — skipping (will use unauthenticated)")

    # --- OpenRouter (RAG summaries) ---
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        print("\n📦 OpenRouter")
        create_scope_and_secret(
            scope="openrouter",
            key="api-key",
            value=or_key,
            description="OpenRouter API key for RAG summaries"
        )
    else:
        print("\n⚠️  OPENROUTER_API_KEY not set — skipping")

    print("\n" + "=" * 60)
    print("🎉 Secret scope setup complete!")
    print("=" * 60)
    print("\n  To verify, run:")
    print("    databricks secrets list-scopes")
    print("    databricks secrets list-secrets --scope database")


if __name__ == "__main__":
    main()
