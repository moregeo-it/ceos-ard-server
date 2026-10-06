from enum import Enum
from typing import Any

from app.utils.request_context import get_client_id


class EventType(str, Enum):
    """Real-time workspace event types broadcast to subscribers over the WebSocket gateway.

    Contract note: the values and payloads are mirrored by `WorkspaceEventType`/`WorkspaceEvent*`
    in `openapi.yaml` and by `src/services/events.js` in ceos-ard-editor - update them together
    (guarded by `scripts/check_event_contract.py`).
    """

    FILE_SAVED = "file.saved"
    FILE_CREATED = "file.created"
    FILE_DELETED = "file.deleted"
    FILE_RENAMED = "file.renamed"
    FILE_REVERTED = "file.reverted"
    FILE_COMMITTED = "file.committed"
    # The owner synced the workspace with GitHub (POST .../sync, or the merge on commit) and files
    # changed on disk at arbitrary depth: subscribers must reload the tree and their open files.
    WORKSPACE_SYNCED = "workspace.synced"
    # Title, description, PFS list or status changed via PATCH (archiving has its own event).
    WORKSPACE_UPDATED = "workspace.updated"
    # The owner generated the preview; every other client fetches GET .../previews/current.
    PREVIEW_GENERATED = "preview.generated"
    COLLABORATOR_REVOKED = "collaborator.revoked"
    # The owner changed this user's mode; sent only to that user, who refetches the workspace
    COLLABORATOR_UPDATED = "collaborator.updated"
    WORKSPACE_ARCHIVED = "workspace.archived"
    WORKSPACE_DELETED = "workspace.deleted"


def build_event(
    event_type: EventType,
    *,
    actor_user_id: str | None = None,
    path: str | None = None,
    file: Any | None = None,
    old_path: str | None = None,
    target_user_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a workspace event envelope for `EventBroker.publish`.

    `actor_user_id` is who caused the change. `old_path` is the pre-change path (file.renamed, and
    file.reverted when the revert undid a staged rename). `target_user_id`, if set, restricts
    delivery to one subscriber (collaborator.revoked). `seq`/`ts` are added on publish.

    `actor_client_id` is the client that made the change (from the request context); the gateway
    withholds the event from that client's socket and strips the field before sending.
    """
    event: dict[str, Any] = {
        "type": event_type.value,
        "actor_user_id": actor_user_id,
        "path": path,
        "file": file,
    }
    if old_path is not None:
        event["old_path"] = old_path
    if target_user_id is not None:
        event["target_user_id"] = target_user_id
    client_id = get_client_id()
    if actor_user_id is not None and client_id is not None:
        event["actor_client_id"] = client_id
    event.update(extra)
    return event
