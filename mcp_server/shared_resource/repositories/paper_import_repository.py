"""Idempotent selected imports; exact identifiers, no fuzzy identity merges."""
import json
import re
from ..exceptions import ValidationError


def import_metadata(db, paper):
    identifier = paper['openalex_id']
    doi = re.sub(r'^https?://doi.org/', '', (paper.get('doi') or '').strip(), flags=re.I).lower() or None
    with db.get_connection() as connection, connection.cursor() as cursor:
        # These locks serialize imports through this contract. Existing ingestion
        # writers retain their own contracts; this is not a global DOI migration.
        for key in sorted({identifier, doi or identifier}):
            cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('paper-import:' + key,))
        cursor.execute('''SELECT paper_id, title FROM papers
            WHERE openalex_id=%s OR (%s IS NOT NULL AND
                lower(regexp_replace(doi, '^https?://doi.org/', '', 'i'))=%s)
            ORDER BY paper_id FOR UPDATE''', (identifier, doi, doi))
        matches = cursor.fetchall()
        if len(matches) > 1:
            raise ValidationError('Multiple existing papers match this identity. Resolve the duplicate before importing.')
        if matches:
            return {'paper_id': str(matches[0][0]), 'title': matches[0][1], 'already_imported': True}
        cursor.execute('''INSERT INTO papers
            (openalex_id, doi, title, abstract, publication_year, venue,
             citation_count, source_api, open_access_url, payload, synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'openalex',%s,%s,now())
            ON CONFLICT (openalex_id) DO NOTHING
            RETURNING paper_id, title''',
            (identifier, doi, paper['title'], paper.get('abstract'), paper.get('publication_year'),
             paper.get('venue'), paper.get('citation_count') or 0, paper.get('open_access_url'),
             json.dumps(paper.get('payload') or {})))
        saved = cursor.fetchone()
        if saved is None:
            cursor.execute("SELECT paper_id, title FROM papers WHERE openalex_id=%s", (identifier,))
            saved = cursor.fetchone()
            return {"paper_id": str(saved[0]), "title": saved[1], "already_imported": True}
        # Author metadata and relationships are committed with the paper. Reuse
        # stable provider IDs; do not create repeated anonymous author identities.
        for author in paper.get('_authors') or []:
            if not author.get('openalex_id') or not author.get('display_name'):
                continue
            cursor.execute('''INSERT INTO authors (openalex_id, display_name, institution)
                VALUES (%s,%s,%s) ON CONFLICT (openalex_id) DO UPDATE SET
                display_name=EXCLUDED.display_name RETURNING author_id''',
                (author['openalex_id'], author['display_name'], author.get('institution')))
            author_id = cursor.fetchone()[0]
            cursor.execute('''INSERT INTO paper_authors (paper_id, author_id, position)
                VALUES (%s,%s,%s) ON CONFLICT (paper_id, author_id) DO NOTHING''',
                (saved[0], author_id, author.get('position', 0)))
        return {'paper_id': str(saved[0]), 'title': saved[1], 'already_imported': False}
