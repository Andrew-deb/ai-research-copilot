"""
setup_secrets.py — Creates Databricks secret scopes for all API keys.

Reads values from .env and stores them in the appropriate secret scope.
Run once before deploying to Databricks. See sql/README.md for scope names.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def put_secret(scope: str, key: str, value: str | None, label: str) -> None:
    if not value:
        print(f"  ⚠️  {label} not set — skipping")
        return
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        try:
            w.secrets.create_scope(scope=scope)
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise
        w.secrets.put_secret(scope=scope, key=key, string_value=value)
        print(f"  ✅ {scope}/{key} — {label}")
    except Exception as e:
        print(f"  ❌ {scope}/{key}: {e}")


def main():
    print("🔐 Setting up Databricks secret scopes...\n")
    put_secret("database", "lakebase-url", os.getenv("DATABASE_URL"), "Lakebase connection URL")
    put_secret("openalex", "email", os.getenv("OPENALEX_EMAIL"), "OpenAlex polite pool email")
    put_secret("semantic-scholar", "api-key", os.getenv("SEMANTIC_SCHOLAR_API_KEY"), "Semantic Scholar API key")
    put_secret("openrouter", "api-key", os.getenv("OPENROUTER_API_KEY"), "OpenRouter API key")
    print("\n✅ Done. Verify with: databricks secrets list-scopes")


if __name__ == "__main__":
    main()
