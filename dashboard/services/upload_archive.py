"""Bounded ZIP exports of a conversation's owned temporary originals."""
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from zipfile import ZipFile, ZIP_STORED
from exceptions import ValidationError
from services import upload_service
import upload_config


def build(owner, conversation_id):
    upload_service.require_enabled()
    items = [item for item in upload_service.list_files(owner, conversation_id)
             if item['status'] in ('ready', 'failed', 'queued', 'processing')]
    if not items:
        raise ValidationError('No downloadable files remain in this conversation.')
    if len(items) > 50:
        raise ValidationError('This export exceeds the supported conversation file limit.')
    output = SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode='w+b')
    try:
        used = set()
        total_bytes = 0
        with ZipFile(output, 'w', compression=ZIP_STORED) as archive:
            for item in items:
                row, content = upload_service.download(owner, item['document_id'])
                if str(row.get("conversation_id")) != str(
                    conversation_id
                ) and not row.get("retained_at"):
                    raise ValidationError('A file is no longer part of this conversation.')
                if len(content) > upload_config.LIMITS.max_file_bytes:
                    raise ValidationError('A file exceeds the export size limit.')
                total_bytes += len(content)
                if total_bytes > 30 * 1024 * 1024:
                    raise ValidationError(
                        "This export exceeds 30 MiB. Download files individually."
                    )
                name = PurePosixPath(row['filename'].replace('\\', '/')).name
                if not name or name in ('.', '..'):
                    name = 'document'
                original = name
                counter = 1
                while name.casefold() in used:
                    counter += 1
                    path = PurePosixPath(original)
                    name = f'{path.stem} ({counter}){path.suffix}'
                used.add(name.casefold())
                archive.writestr(name, content)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise
