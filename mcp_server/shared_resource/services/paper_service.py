"""Local corpus lookup; external discovery is a separate Research operation."""

from ..exceptions import PaperNotFoundError, ValidationError


def require_paper(repository, paper_id: str) -> dict:
    if not paper_id:
        raise ValidationError("Paper ID is required.")
    paper = repository.get_paper(paper_id)
    if not paper:
        raise PaperNotFoundError(f"Paper '{paper_id}' not found.")
    return paper
