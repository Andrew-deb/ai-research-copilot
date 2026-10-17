"""Pure bounded passage selection, independent of storage and agent runtimes."""
import re

MAX_PASSAGES = 4
MAX_PASSAGE_CHARS = 1800


def select_passages(artifact, query='', page=None):
    pages = artifact.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= 200:
        raise ValueError('Invalid extracted document.')
    if page is not None and (type(page) is not int or not 1 <= page <= len(pages)):
        raise ValueError('Choose a valid extracted page.')
    terms = set(re.findall(r'\w+', query.casefold()))
    candidates = []
    for expected, item in enumerate(pages, 1):
        number, text = item.get('page'), item.get('text')
        if type(number) is not int or number != expected or not isinstance(text, str):
            raise ValueError('Invalid extracted page.')
        if page is not None and number != page:
            continue
        # Windows preserve their actual page and offset; no invented sections.
        for offset in range(0, len(text), MAX_PASSAGE_CHARS):
            passage = text[offset:offset + MAX_PASSAGE_CHARS]
            if passage.strip():
                score = len(terms & set(re.findall(r'\w+', passage.casefold())))
                candidates.append((score, number, offset, passage))
    chosen = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))[:MAX_PASSAGES]
    return [{'page': n, 'offset': offset, 'text': text} for _, n, offset, text in chosen]
