"""Owner-scoped durable Wick context references, never cached asset content."""
import json
from repositories import lakebase


def read(owner, conversation_id):
    rows = lakebase.run_query(
        "SELECT wick_context FROM conversations WHERE conversation_id = %s AND user_id = %s;",
        (conversation_id, owner))
    return rows[0].get('wick_context') if rows else None


def save(owner, conversation_id, references):
    return lakebase.run_write(
        "UPDATE conversations SET wick_context = %s::jsonb WHERE conversation_id = %s AND user_id = %s RETURNING conversation_id;",
        (json.dumps(references), conversation_id, owner))
