"""
setup_secrets.py — Creates Databricks secret scopes for all API keys.

Reads values from .env and prompts for any missing secrets interactively.
Run once before deploying to Databricks. See sql/README.md for scope names.
"""

import os
import sys
from getpass import getpass

from dotenv import load_dotenv

load_dotenv()


# Define all required secrets with their metadata
SECRETS_CONFIG = [
    {
        "scope": "database",
        "key": "lakebase-url",
        "env_var": "DATABASE_URL",
        "label": "Lakebase connection URL",
        "required": True,
        "example": "postgresql://user:pass@host.cloud.databricks.com:5432/dbname"
    },
    {
        "scope": "openalex",
        "key": "email",
        "env_var": "OPENALEX_EMAIL",
        "label": "OpenAlex polite pool email",
        "required": True,
        "example": "your-email@example.com"
    },
    {
        "scope": "semantic-scholar",
        "key": "api-key",
        "env_var": "SEMANTIC_SCHOLAR_API_KEY",
        "label": "Semantic Scholar API key",
        "required": False,
        "example": "your-s2-api-key"
    },
    {
        "scope": "openrouter",
        "key": "api-key",
        "env_var": "OPENROUTER_API_KEY",
        "label": "OpenRouter API key",
        "required": True,
        "example": "sk-or-v1-..."
    }
]


def get_secret_value(config: dict) -> str | None:
    """Get secret value from env or prompt user."""
    value = os.getenv(config["env_var"])
    
    if value:
        return value
    
    # Prompt for missing value
    print(f"\n📝 {config['label']}")
    print(f"   Scope: {config['scope']}/{config['key']}")
    print(f"   Example: {config['example']}")
    
    if not config["required"]:
        response = input(f"   Enter value (or press Enter to skip): ").strip()
        return response if response else None
    
    while True:
        response = getpass(f"   Enter value (required): ").strip()
        if response:
            return response
        print("   ⚠️  This secret is required. Please provide a value.")


def put_secret(scope: str, key: str, value: str | None, label: str) -> bool:
    """Store secret in Databricks secret scope. Returns True on success."""
    if not value:
        print(f"  ⏭️  {label} — skipped")
        return False
    
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        
        # Create scope if it doesn't exist
        try:
            w.secrets.create_scope(scope=scope)
            print(f"  📦 Created scope: {scope}")
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise
        
        # Store the secret
        w.secrets.put_secret(scope=scope, key=key, string_value=value)
        print(f"  ✅ {scope}/{key} — {label}")
        return True
        
    except Exception as e:
        print(f"  ❌ {scope}/{key}: {e}")
        return False


def main():
    print("🔐 Setting up Databricks secret scopes for AI Research Copilot\n")
    print("This script will collect all required API keys and connection strings.")
    print("Values from .env will be used automatically. You'll be prompted for missing values.\n")
    
    results = {"success": [], "skipped": [], "failed": []}
    
    for config in SECRETS_CONFIG:
        print(f"\n{'='*70}")
        
        # Get the secret value
        value = get_secret_value(config)
        
        # Store it in Databricks
        success = put_secret(config["scope"], config["key"], value, config["label"])
        
        # Track results
        secret_id = f"{config['scope']}/{config['key']}"
        if success:
            results["success"].append(secret_id)
        elif value is None:
            results["skipped"].append(secret_id)
        else:
            results["failed"].append(secret_id)
    
    # Print summary
    print(f"\n\n{'='*70}")
    print("\n📊 Summary:")
    print(f"   ✅ Stored: {len(results['success'])} secrets")
    print(f"   ⏭️  Skipped: {len(results['skipped'])} secrets")
    print(f"   ❌ Failed: {len(results['failed'])} secrets")
    
    if results["success"]:
        print(f"\n✅ Successfully stored:")
        for secret_id in results["success"]:
            print(f"   • {secret_id}")
    
    if results["skipped"]:
        print(f"\n⏭️  Skipped (optional):")
        for secret_id in results["skipped"]:
            print(f"   • {secret_id}")
    
    if results["failed"]:
        print(f"\n❌ Failed to store:")
        for secret_id in results["failed"]:
            print(f"   • {secret_id}")
    
    print(f"\n\n🔍 Verify your secrets with: databricks secrets list-scopes")
    print(f"📖 View scope contents with: databricks secrets list --scope <scope-name>\n")
    
    # Exit with error if required secrets failed
    required_failed = any(
        f"{c['scope']}/{c['key']}" in results["failed"]
        for c in SECRETS_CONFIG
        if c["required"]
    )
    if required_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
