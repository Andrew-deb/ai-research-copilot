"""Approval policy is separate from resource ownership and tier capabilities."""

import hashlib
import json
from uuid import UUID

import config
from exceptions import CapabilityDeniedError, ResearchCopilotError, ValidationError
from repositories import action_approvals
from services import assistant_context

# Explicit policies for Wick's registered writes; no catch-all mutation tool.
ACTIONS = {
    'reorder_collection_papers': ('Reorder collection papers', 'collection', 'collection_id'),
    'create_collection': ('Create a collection', None, None),
    'create_note': ('Create a note', None, None),
    'edit_note': ('Replace note contents and metadata', 'note', 'note_id'),
    'set_note_pinned': ('Set note pin state', 'note', 'note_id'),
    'delete_note': ('Permanently delete a note', 'note', 'note_id'),
    'create_learning_goal': ('Create a learning goal', None, None),
    'add_paper_to_collection': ('Add a paper to a collection', 'collection', 'collection_id'),
    'remove_paper_from_collection': ('Remove a paper from a collection', 'collection', 'collection_id'),
    'mark_paper_status': ('Change reading status', 'paper', 'paper_id'),
    'update_goal_status': ('Change learning-goal status', 'goal', 'goal_id'),
}
ASSET_ARGUMENTS = {'note_id': 'note', 'paper_id': 'paper', 'collection_id': 'collection', 'goal_id': 'goal'}


class ApprovalRequired(ResearchCopilotError):
    """Internal control flow: preserve the continuation before any tool I/O."""

    def __init__(self, proposal, checkpoint):
        self.proposal = proposal
        self.checkpoint = checkpoint
        super().__init__('This workspace action needs your approval.')


def fingerprint(name, arguments):
    encoded = json.dumps([name, arguments], sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _asset_label(user_id, kind, asset_id):
    resolved = assistant_context.bundle(user_id, [{'kind': kind, 'id': asset_id}])
    item = resolved['items'][0] if resolved and resolved['items'] else None
    return item['label'] if item and item['available'] else None


def proposal(user_id, name, arguments):
    if not user_id or name not in ACTIONS:
        raise CapabilityDeniedError('This action has no supported approval policy.')
    try:
        valid = isinstance(arguments, dict) and len(json.dumps(arguments, allow_nan=False)) <= 20000
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValidationError('Invalid action arguments.')

    label, kind, key = ACTIONS[name]
    target, target_label = 'workspace', 'your workspace'
    if key:
        try:
            target = str(UUID(arguments.get(key, '')))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError('Invalid action target.') from None
        target_label = _asset_label(user_id, kind, target)
        if target_label is None:
            raise ValidationError('The action target is unavailable.')

    endpoint = config.mcp_endpoint('wick')
    scope = hashlib.sha256(json.dumps(['wick-actions-v1', endpoint, name, target]).encode()).hexdigest()
    display = []
    for argument, value in arguments.items():
        shown = value
        asset_kind = ASSET_ARGUMENTS.get(argument)
        if asset_kind and isinstance(value, str) and value:
            shown = _asset_label(user_id, asset_kind, value) or 'Unavailable asset'
        display.append({
            'label': argument.replace('_', ' ').capitalize(),
            'value': shown if isinstance(shown, str) else json.dumps(shown, ensure_ascii=False),
        })
    return {
        'display': display, 'tool': name, 'arguments': arguments,
        'fingerprint': fingerprint(name, arguments), 'scope_key': scope,
        'scope_label': label + ' — ' + target_label,
        'label': label, 'target_label': target_label,
    }


def guard(user_id, name, arguments, checkpoint, permit=None, mode="ask"):
    if mode not in ("ask", "autonomous"):
        raise ValidationError("Invalid approval mode.")
    """Re-resolve the target even for previously approved operations."""
    proposed = proposal(user_id, name, arguments)
    if permit and permit['fingerprint'] == proposed['fingerprint']:
        if permit['scope_key'] != proposed['scope_key']:
            raise ValidationError('The action endpoint or target has changed. Request a new approval.')
        decision = permit['decision']
        if decision not in ('once', 'always', 'deny'):
            raise ValidationError('Invalid approval decision.')
        permit.clear()
        if decision == 'deny':
            raise CapabilityDeniedError(
                'The user declined this action. Do not retry it or substitute another mutation.',
                requires_auth=False)
        return
    if mode == "autonomous":
        return
    if action_approvals.has_grant(user_id, proposed['scope_key']):
        return
    raise ApprovalRequired(proposed, checkpoint)
