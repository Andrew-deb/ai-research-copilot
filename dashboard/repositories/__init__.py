"""
dashboard/repositories/__init__.py

Repository layer package for the dashboard app.

Identical structural contract to mcp_server/repositories/: all SQL and
pgvector queries for the dashboard live here. Each Databricks App is a
separate process, so the dashboard manages its own connection lifecycle.
"""
