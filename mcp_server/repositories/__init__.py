"""
mcp_server/repositories/__init__.py

Repository layer package.

All SQL queries, pgvector searches, and database writes for the MCP server
live in this package. No module outside this package may import psycopg2,
open a database connection, or write raw SQL.
"""
