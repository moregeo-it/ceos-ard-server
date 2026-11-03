from fastapi import HTTPException, status

from app.config import settings
from app.workspace_types import ceos_ard
from app.workspace_types.base import PathRules, Upstream, WorkspaceType

__all__ = ["PathRules", "Upstream", "WorkspaceType", "available_types", "default_type", "get_type"]

_FACTORIES = {
    "ceos-ard": ceos_ard.create,
}

# Every known type stays resolvable, so existing workspaces keep working when a type is disabled
_TYPES: dict[str, WorkspaceType] = {type_id: factory() for type_id, factory in _FACTORIES.items()}

if not settings.WORKSPACE_TYPES:
    raise ValueError("WORKSPACE_TYPES must list at least one workspace type")
unknown = [type_id for type_id in settings.WORKSPACE_TYPES if type_id not in _TYPES]
if unknown:
    raise ValueError(f"Unknown workspace types in WORKSPACE_TYPES: {', '.join(unknown)}")


def available_types() -> list[WorkspaceType]:
    """The types offered for new workspaces, in the configured order."""
    return [_TYPES[type_id] for type_id in settings.WORKSPACE_TYPES]


def default_type() -> WorkspaceType:
    return available_types()[0]


def get_type(type_id: str | None) -> WorkspaceType:
    workspace_type = _TYPES.get(type_id or "ceos-ard")
    if workspace_type is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown workspace type: {type_id}")
    return workspace_type
