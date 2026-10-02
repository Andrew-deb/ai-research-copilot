"""Validate complete membership and reorder atomically under the collection lock."""
from ..exceptions import ValidationError


def reorder(db, user_id, collection_id, paper_ids):
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute('''SELECT collection_id FROM collections
            WHERE collection_id=%s AND user_id=%s AND NOT is_curated FOR UPDATE''',
            (collection_id, user_id))
        if not cursor.fetchone():
            raise ValidationError('That collection is unavailable or cannot be edited.')
        cursor.execute('SELECT paper_id FROM collection_papers WHERE collection_id=%s', (collection_id,))
        current = {str(row[0]) for row in cursor.fetchall()}
        if set(paper_ids) != current:
            raise ValidationError('Supply every current collection paper exactly once. Refresh the collection and retry.')
        cursor.execute('''UPDATE collection_papers cp SET sequence_order=v.position
            FROM unnest(%s::uuid[]) WITH ORDINALITY AS v(paper_id, position)
            WHERE cp.collection_id=%s AND cp.paper_id=v.paper_id''', (paper_ids, collection_id))
        if cursor.rowcount != len(paper_ids):
            raise ValidationError('Collection membership changed. Refresh and retry.')
        return cursor.rowcount
