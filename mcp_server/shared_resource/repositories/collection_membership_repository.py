"""Serialize appends on a collection row; duplicates retain their saved position."""


def append_paper(db, collection_id, paper_id):
    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT collection_id FROM collections WHERE collection_id = %s FOR UPDATE', (collection_id,))
            cursor.execute('''INSERT INTO collection_papers (collection_id, paper_id, sequence_order)
                SELECT %s, %s, COALESCE(MAX(sequence_order), 0) + 1 FROM collection_papers WHERE collection_id = %s
                ON CONFLICT (collection_id, paper_id) DO UPDATE SET sequence_order = collection_papers.sequence_order
                RETURNING sequence_order''', (collection_id, paper_id, collection_id))
            return int(cursor.fetchone()[0])
