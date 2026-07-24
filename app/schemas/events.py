from enum import Enum
from typing import Any


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
    SHARE_REVOKED = "share.revoked"
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

    `actor_user_id` is who caused the change (clients echo-suppress their own). `old_path` is the
    pre-change path (file.renamed, and file.reverted when the revert undid a staged rename).
    `target_user_id`, if set, restricts delivery to one subscriber (share.revoked). `seq`/`ts`
    are added on publish.
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
    event.update(extra)
    return event
