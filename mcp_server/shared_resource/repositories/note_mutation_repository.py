"""Owner-scoped note mutations using the caller's database executor."""


def update_note(run_write, user_id, note_id, note_text, title=None, tags=None):
    return run_write(
        """UPDATE notes SET note_text = %s, title = %s, tags = %s, updated_at = now()
        WHERE note_id = %s AND user_id = %s RETURNING *;""",
        (note_text, title, tags or [], note_id, user_id), returning=True)


def set_note_pinned(run_write, user_id, note_id, pinned):
    return run_write(
        "UPDATE notes SET pinned = %s WHERE note_id = %s AND user_id = %s RETURNING *;",
        (pinned, note_id, user_id), returning=True)


def delete_note(run_write, user_id, note_id):
    row = run_write(
        "DELETE FROM notes WHERE note_id = %s AND user_id = %s RETURNING note_id;",
        (note_id, user_id), returning=True)
    return row is not None
