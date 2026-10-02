"""Resolve a page hint into a short, authorized context for the agent.

Browser-provided labels are never authority. An item is looked up under the
current user on each turn, so a stale panel cannot act on a deleted or foreign
collection or goal. The context is only a hint; tool permissions still apply.
"""

import json

from repositories import lakebase
from exceptions import ValidationError
from uuid import UUID

PAGES = {
    "dashboard": "Dashboard", "search": "Paper search",
    "collections": "Collections", "progress": "Reading progress",
    "notes": "Notes", "goals": "Learning goals",
}


def resolve(user_id: str | None, kind: str, item_id: str = "") -> dict | None:
    kind = (kind or "").strip().lower()
    item_id = (item_id or "").strip()
    if not kind:
        return None
    if len(item_id) > 100:
        raise ValidationError("Invalid assistant context.")
    if kind in PAGES and not item_id:
        if kind in ("notes", "progress", "goals") and not user_id:
            raise ValidationError("Sign in to use this workspace context.")
        return {"kind": kind, "id": "", "label": PAGES[kind]}
    if not item_id:
        raise ValidationError("Invalid assistant context.")
    try:
        item_id = str(UUID(item_id))
    except (ValueError, AttributeError):
        raise ValidationError("Invalid assistant context.") from None
    if kind == "paper":
        row = lakebase.get_paper(item_id)
        label = row.get("title") if row else None
    elif kind == "collection":
        row = (lakebase.get_collection(item_id, user_id) if user_id else None) or \
              lakebase.get_curated_collection(item_id)
        label = row.get("name") if row else None
    elif kind == "goal" and user_id:
        row = lakebase.get_learning_goal(item_id, user_id)
        label = row.get("title") if row else None
    else:
        raise ValidationError("Invalid assistant context.")
    if not label:
        raise ValidationError("This assistant context is no longer available.")
    return {"kind": kind, "id": item_id, "label": str(label)[:120]}


# Keep the shared read contract authoritative across Render and Wick.
try:
    from shared_resource.services import workspace_service as shared_workspace
    from shared_resource import exceptions as domain_errors
except ModuleNotFoundError:
    from mcp_server.shared_resource.services import workspace_service as shared_workspace
    from mcp_server.shared_resource import exceptions as domain_errors
from repositories import conversation_context

MAX_SELECTIONS = 5
MAX_ASSET_CHARS = 2500
MAX_PAYLOAD_CHARS = 14000


def normalize(references):
    """Finite references only. Labels/content/actors are never taken from clients."""
    if not isinstance(references, list) or len(references) > MAX_SELECTIONS:
        raise ValidationError('Choose at most five context items.')
    result = []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {'kind', 'id'}:
            raise ValidationError('Invalid context reference.')
        kind, item_id = reference['kind'], reference['id']
        if not isinstance(kind, str) or not isinstance(item_id, str):
            raise ValidationError('Invalid context reference.')
        if kind == 'page':
            if item_id not in PAGES:
                raise ValidationError('Invalid page context.')
        elif kind in {'paper', 'note', 'collection', 'goal'}:
            try:
                item_id = str(UUID(item_id))
            except (ValueError, AttributeError):
                raise ValidationError('Invalid context reference.') from None
        else:
            raise ValidationError('Unsupported context kind.')
        cleaned = {'kind': kind, 'id': item_id}
        if cleaned not in result:
            result.append(cleaned)
    return result


def legacy_reference(kind, item_id=''):
    return {'kind': 'page', 'id': kind} if kind in PAGES and not item_id else {'kind': kind, 'id': item_id}


def resolve_references(owner, references, *, strict=False):
    """Fresh owner-checked bounded content, with unavailable references visible."""
    items = []
    for reference in normalize(references):
        kind, item_id = reference['kind'], reference['id']
        try:
            if kind == 'note':
                row = lakebase.get_note(owner, item_id) if owner else None
                if not row:
                    raise ValidationError('This context is unavailable.')
                label = row.get('title') or 'Note'
            else:
                hint = resolve(owner, item_id if kind == 'page' else kind,
                               '' if kind == 'page' else item_id)
                label = hint['label']
            payload = (shared_workspace.get_workspace_paper(lakebase, item_id) if kind == 'paper'
                       else shared_workspace.get_workspace_resource(lakebase, owner, kind, item_id, limit=5))
            text = json.dumps(payload, ensure_ascii=False, default=str)
            items.append({**reference, 'label': str(label)[:120], 'available': True,
                          'content': text[:MAX_ASSET_CHARS],
                          'truncated': len(text) > MAX_ASSET_CHARS or bool(payload.get('truncated')) or bool(payload.get('next_cursor'))})
        except (ValidationError, domain_errors.WorkspaceResourceNotFoundError,
                domain_errors.ValidationError, domain_errors.PaperNotFoundError):
            if strict:
                raise ValidationError('This context is unavailable or not accessible.') from None
            items.append({**reference, 'label': 'Unavailable ' + kind, 'available': False})
    return items


def load(owner, conversation_id):
    return normalize(conversation_context.read(owner, conversation_id) or []) if owner and conversation_id else []


def save(owner, conversation_id, references):
    if not owner or not lakebase.get_conversation(owner, conversation_id):
        raise ValidationError('This conversation is unavailable.')
    references = normalize(references)
    previous = load(owner, conversation_id)
    resolve_references(owner, [ref for ref in references if ref not in previous], strict=True)
    # Retaining unavailable references is allowed so users can remove one at
    # a time. They yield no content and never grant access to a foreign asset.
    conversation_context.save(owner, conversation_id, references)


def bundle(owner, references):
    items = resolve_references(owner, references)
    if not items:
        return None
    first = items[0]
    result = {'kind': first['id'] if first['kind'] == 'page' else first['kind'],
              'id': '' if first['kind'] == 'page' else first['id'], 'label': first['label'],
              'references': normalize(references), 'items': items}
    # Nested JSON can expand escaped text. Bound the serialized payload without
    # cutting JSON or losing any selected references/unavailable indicators.
    while len(json.dumps(result, ensure_ascii=False)) > MAX_PAYLOAD_CHARS:
        longest = max(items, key=lambda item: len(item.get('content', '')))
        longest['content'] = longest.get('content', '')[:len(longest.get('content', '')) // 2]
        longest['truncated'] = True
    return result



def search(owner, query='', kind=None, cursor=None):
    try:
        return shared_workspace.find_workspace_resources(lakebase, owner, query,
            [kind] if kind else None, limit=20, cursor=cursor)
    except (domain_errors.ValidationError, domain_errors.WorkspaceResourceNotFoundError) as exc:
        raise ValidationError(str(exc)) from exc
