"""
dashboard/middleware/__init__.py

Middleware package for the dashboard app.

Mirrors mcp_server/middleware/: cross-cutting concerns (user identity
resolution, domain-exception-to-HTTP mapping) kept out of individual routes
so route functions stay thin.
"""
