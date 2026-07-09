from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Types of real-time workspace events broadcast to subscribers over SSE.

    Phase 1 propagation is one-directional (owner -> readonly viewers). Later phases
    (e.g. comments) add new members here without changing the transport.
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
    target_user_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a workspace event envelope for `EventBroker.publish`.

    `actor_user_id` is the user who caused the change; clients echo-suppress events whose
    actor is themselves. `target_user_id`, when set, restricts delivery to a single subscriber
    (used for `share.revoked`). `seq`/`ts` are stamped by the broker at publish time.
    """
    event: dict[str, Any] = {
        "type": event_type.value,
        "actor_user_id": actor_user_id,
        "path": path,
        "file": file,
    }
    if target_user_id is not None:
        event["target_user_id"] = target_user_id
    event.update(extra)
    return event
