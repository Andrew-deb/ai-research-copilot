"""Provider settings and identity for the Research MCP runtime."""

import logging
import os

from shared_resource.config import _get_secret

logger = logging.getLogger(__name__)

# --- OpenAlex ---
from shared_resource.brokers.settings import (
    OPENALEX_BASE_URL, OPENALEX_EMAIL, OPENALEX_RATE_LIMIT_DELAY,
)

# --- Semantic Scholar ---
S2_BASE_URL: str = "https://api.semanticscholar.org"
S2_API_KEY: str | None = _get_secret("semantic-scholar", "api-key", "SEMANTIC_SCHOLAR_API_KEY")
# 1.1s = safe delay for authenticated tier (1 req/sec). Use 3.1s without a key.
S2_RATE_LIMIT_DELAY: float = float(os.getenv("S2_RATE_LIMIT_DELAY", "1.1"))

# --- Wikipedia ---
WIKIPEDIA_BASE_URL: str = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_RATE_LIMIT_DELAY: float = float(os.getenv("WIKIPEDIA_RATE_LIMIT_DELAY", "0.1"))

# --- OpenRouter ---
OPENROUTER_API_KEY: str | None = _get_secret("openrouter", "api-key", "OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
# No OPENROUTER_MODEL here on purpose.
#
# This module declared one for a long time and nothing ever read it - the MCP
# server makes no completion calls at all. It sat at
# openai/gpt-oss-120b:free, two retirements stale, and the only thing it ever
# did was make it look as though two services had drifted apart. A second
# default that cannot be used can still be read and believed, which is worse
# than having none.
#
# dashboard/config.py owns the model. Set OPENROUTER_MODEL in the environment
# to change it.

# --- MCP Server ---
MCP_SERVER_NAME: str = "ai-research-copilot"
MCP_SERVER_VERSION: str = "1.0.0"


if not S2_API_KEY:
    logger.warning("S2_API_KEY not set — using unauthenticated tier. Set S2_RATE_LIMIT_DELAY=3.1.")
