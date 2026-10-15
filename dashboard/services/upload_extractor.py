"""Parser subprocess only. No database, storage clients, Flask or model calls."""

import io
import json
import sys

MAX_CHARS = 200000
MAX_PAGES = 200
VERSION = "text-v1"


class ExtractionRejected(Exception):
    pass


def extract(data, media_type):
    pages = []
    remaining = MAX_CHARS
    if media_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ExtractionRejected("encrypted_pdf")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise ExtractionRejected("too_many_pages")
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            remaining -= len(text)
            if remaining < 0:
                raise ExtractionRejected("too_much_text")
            pages.append({"page": number, "text": text})
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ExtractionRejected("invalid_text") from None
        if "\x00" in text or len(text) > MAX_CHARS:
            raise ExtractionRejected("invalid_text")
        pages = [{"page": 1, "text": text}]
    if not any(page["text"].strip() for page in pages):
        raise ExtractionRejected("no_extractable_text")
    return {"version": VERSION, "pages": pages}


def main():
    import resource

    # Applied inside the child, never preexec_fn in a threaded web process.
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    try:
        result = extract(sys.stdin.buffer.read(10 * 1024 * 1024 + 1), sys.argv[1])
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except ExtractionRejected as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
    except Exception:
        print(json.dumps({"ok": False, "error": "invalid_document"}))


if __name__ == "__main__":
    main()
