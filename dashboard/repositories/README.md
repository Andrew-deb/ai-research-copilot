# dashboard/repositories/ — Data Access Layer (Dashboard App)

This package contains all database queries and pgvector interactions specifically tailored for the Flask dashboard web application.

## Files

| File | Purpose |
|------|---------|
| `lakebase.py` | Database repository providing data access and specialized UI aggregation queries for the dashboard. |

## Key Design & Implementation Decisions

### 1. Isolated Connection Pool & Context Management
* Operates in an independent process from the MCP Server, maintaining its own database connection lifecycle with automatic commit, rollback, and cleanup.

### 2. UI-Specific Aggregations
* Contains optimized aggregate queries such as `get_dashboard_stats()` to compute real-time counts across active learning goals, curated collections, reading statuses (not started, reading, completed), and notes in minimal database round-trips.

### 3. Paginated Text & Vector Search
* Implements SQL `LIMIT` and `OFFSET` queries alongside pgvector cosine distance operations, powering responsive UI views for paper exploration and Kanban board status transitions.
