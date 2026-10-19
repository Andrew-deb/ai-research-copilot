"""Deterministic, page-preserving bounded chunks for private document indexes."""

CHUNK_VERSION = "pages-1200-overlap-200-v1"
MAX_CHUNKS = 400


def split(artifact):
    pages = artifact.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 200:
        raise ValueError("Invalid pages")
    chunks = []
    for expected, page in enumerate(pages, 1):
        if page.get("page") != expected or not isinstance(page.get("text"), str):
            raise ValueError("Invalid page")
        text = page["text"]
        for offset in range(0, len(text), 1000):
            passage = text[offset : offset + 1200]
            if passage.strip():
                chunks.append({"page": expected, "offset": offset, "text": passage})
            if len(chunks) > MAX_CHUNKS:
                raise ValueError("Document exceeds index chunk limit")
    if not chunks:
        raise ValueError("No text to index")
    return chunks
