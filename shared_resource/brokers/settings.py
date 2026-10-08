"""Provider configuration shared by Render and MCP without runtime secret lookups."""
import os

OPENALEX_BASE_URL = "https://api.openalex.org"
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "user@research-copilot.dev")
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY")
OPENALEX_RATE_LIMIT_DELAY = float(os.getenv("OPENALEX_RATE_LIMIT_DELAY", "0.12"))
