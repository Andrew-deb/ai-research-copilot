"""Bounded corpus retrieval shared by the dashboard and Wick."""


def search(db, user_id, query, saved_only, limit, offset):
    # plainto_tsquery creates safe lexemes; OR matches partial topic phrases.
    # No dynamic SQL or embeddings/provider calls are needed.
    return db.run_query("""
        WITH terms AS (
          SELECT regexp_replace(plainto_tsquery('english', %s)::text, ' & ', ' | ', 'g')::tsquery AS q
        ), documents AS (
          SELECT p.paper_id, p.title, p.publication_year, p.venue,
                 left(COALESCE(p.abstract, ''), 600) AS abstract,
                 COALESCE((SELECT string_agg(a.display_name, ', ' ORDER BY pa.position)
                   FROM paper_authors pa JOIN authors a ON a.author_id = pa.author_id
                   WHERE pa.paper_id = p.paper_id), '') AS authors,
                 setweight(to_tsvector('english', COALESCE(p.title, '')), 'A') ||
                 setweight(to_tsvector('english', COALESCE(p.abstract, '')), 'B') AS document
          FROM papers p
          WHERE NOT %s OR EXISTS (SELECT 1 FROM reading_progress rp
                                 WHERE rp.paper_id = p.paper_id AND rp.user_id = %s)
        )
        SELECT paper_id, title, publication_year, venue, abstract, left(authors, 400) AS authors,
               ts_rank_cd(document, terms.q) AS rank
        FROM documents CROSS JOIN terms
        WHERE %s = '' OR document @@ terms.q OR authors ILIKE %s
        ORDER BY rank DESC, publication_year DESC NULLS LAST, title, paper_id
        LIMIT %s OFFSET %s
    """, (query, saved_only, user_id, query,
          '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%',
          limit, offset))
