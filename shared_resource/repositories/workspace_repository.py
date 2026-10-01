"""Owner-scoped workspace queries with no configuration or database-driver import."""


def get_note(db, user_id: str, note_id: str) -> dict | None:
    rows = db.run_query(
        "SELECT note_id, paper_id, note_text, title, tags, pinned, created_at, updated_at "
        "FROM notes WHERE note_id = %s AND user_id = %s;", (note_id, user_id))
    return rows[0] if rows else None


def get_collection(db, user_id: str | None, collection_id: str) -> dict | None:
    rows = db.run_query(
        "SELECT collection_id, name, description, is_curated, created_at "
        "FROM collections WHERE collection_id = %s AND (user_id = %s OR is_curated);",
        (collection_id, user_id))
    return rows[0] if rows else None


def collection_papers(db, collection_id: str, limit: int, offset: int) -> list[dict]:
    return db.run_query(
        "SELECT p.paper_id, p.title, p.publication_year, cp.sequence_order "
        "FROM collection_papers cp JOIN papers p ON p.paper_id = cp.paper_id "
        "WHERE cp.collection_id = %s ORDER BY cp.sequence_order, p.paper_id LIMIT %s OFFSET %s;",
        (collection_id, limit, offset))


def paper_authors(db, paper_id: str) -> list[dict]:
    return db.run_query(
        "SELECT a.author_id, left(a.display_name, 200) AS display_name, pa.position "
        "FROM authors a JOIN paper_authors pa ON pa.author_id = a.author_id "
        "WHERE pa.paper_id = %s ORDER BY pa.position, a.author_id LIMIT 51;", (paper_id,))


def reading_progress(db, user_id: str, status: str | None, limit: int, offset: int) -> list[dict]:
    return db.run_query(
        "SELECT rp.paper_id, p.title, rp.status, rp.updated_at "
        "FROM reading_progress rp JOIN papers p ON p.paper_id = rp.paper_id "
        "WHERE rp.user_id = %s AND (%s IS NULL OR rp.status = %s) "
        "ORDER BY rp.updated_at DESC, rp.paper_id LIMIT %s OFFSET %s;",
        (user_id, status, status, limit, offset))


def find_resources(db, user_id: str | None, kinds: list[str], query: str,
                   limit: int, offset: int) -> list[dict]:
    """One bounded query across finite resource types; private branches require an actor."""
    branches, params = [], []
    if "paper" in kinds:
        branches.append("SELECT 'paper' AS kind, paper_id::text AS id, title AS label, "
                        "COALESCE(abstract, '') AS detail FROM papers")
    if "collection" in kinds:
        branches.append("SELECT 'collection' AS kind, collection_id::text AS id, name AS label, "
                        "COALESCE(description, '') AS detail FROM collections "
                        "WHERE user_id = %s OR is_curated")
        params.append(user_id)
    if user_id and "note" in kinds:
        branches.append("SELECT 'note' AS kind, note_id::text AS id, "
                        "COALESCE(title, split_part(note_text, E'\\n', 1)) AS label, "
                        "note_text AS detail FROM notes WHERE user_id = %s")
        params.append(user_id)
    if user_id and "goal" in kinds:
        branches.append("SELECT 'goal' AS kind, goal_id::text AS id, title AS label, "
                        "COALESCE(description, '') AS detail FROM learning_goals WHERE user_id = %s")
        params.append(user_id)
    if not branches:
        return []
    # Literal substring search, not wildcard/embedding expansion.
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = ("SELECT kind, id, left(label, 200) AS label, left(detail, 200) AS snippet FROM ("
           + " UNION ALL ".join(branches)
           + ") resources WHERE label ILIKE %s OR detail ILIKE %s "
           "ORDER BY kind, lower(label), id LIMIT %s OFFSET %s;")
    return db.run_query(sql, tuple(params + [pattern, pattern, limit, offset]))
