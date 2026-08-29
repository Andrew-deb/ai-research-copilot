"""
dashboard/services/__init__.py

Business logic layer for the dashboard app.

Same contract as mcp_server/services/: no Flask imports, no raw SQL. Services
operate on plain Python data, call dashboard/repositories/lakebase.py for
persistence, dashboard/embedding.py for query vectors, and dashboard/llm_client.py
for RAG synthesis. They raise typed domain exceptions; the Flask error handler
turns those into responses.
"""
