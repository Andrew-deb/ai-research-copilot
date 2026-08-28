"""
mcp_server/brokers/__init__.py

Broker layer package.

Every module in this package is responsible for communicating with
exactly ONE external API. No broker may touch the database, import Flask,
or call another broker. This strict Single Responsibility boundary means:
  - Swapping an API source requires changing exactly one file
  - Each broker can be tested in isolation with no Lakebase connection
  - API-specific rate limiting and error handling never leaks into services
"""
