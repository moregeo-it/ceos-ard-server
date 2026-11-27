import dataclasses
import importlib

from fastapi import HTTPException, status
from pydantic import ValidationError

from app.config import settings
from app.workspace_types import config
from app.workspace_types.base import PathRules, Upstream, WorkspaceType

__all__ = ["PathRules", "Upstream", "WorkspaceType", "all_types", "available_types", "default_type", "get_type", "load_types"]


def create_type(type_config: config.TypeConfig) -> WorkspaceType:
    """
    A type from its module: `create(type_id, settings)` builds it with the module's defaults, `Settings` (optional)
    validates its part of the configuration. The general fields of the configuration replace the module's defaults.
    """
    try:
        module = importlib.import_module(f"app.workspace_types.{type_config.module}")
    except ModuleNotFoundError as e:
        raise ValueError(f"Workspace type {type_config.id}: no module {type_config.module}") from e
    if not callable(getattr(module, "create", None)):
        raise ValueError(f"Workspace type {type_config.id}: the module {type_config.module} has no create()")
    settings_model = getattr(module, "Settings", None)
    if settings_model is None:
        if type_config.settings:
            raise ValueError(f"Workspace type {type_config.id}: the module {type_config.module} has no settings")
        type_settings = None
    else:
        try:
            type_settings = settings_model.model_validate(type_config.settings)
        except ValidationError as e:
            raise ValueError(f"Workspace type {type_config.id}: invalid settings: {e}") from e

    workspace_type = module.create(type_config.id, type_settings)
    changes = {key: getattr(type_config, key) for key in ("title", "description", "color") if getattr(type_config, key) is not None}
    if type_config.upstream:
        default = workspace_type.upstream
        changes["upstream"] = Upstream(
            type_config.upstream.owner,
            type_config.upstream.repo or default.repo,
            type_config.upstream.branch or default.branch,
        )
    return dataclasses.replace(workspace_type, id=type_config.id, **changes)


def load_types(deployment: config.DeploymentConfig) -> tuple[dict[str, WorkspaceType], list[str]]:
    """All configured types by id, and the ids of the offered ones in the configured order."""
    types = {item.id: create_type(item) for item in deployment.types}
    return types, [item.id for item in deployment.types if item.offered]


_TYPES, _OFFERED = load_types(config.load(settings.WORKSPACE_TYPES_CONFIG, required=settings.WORKSPACE_TYPES_CONFIG_REQUIRED))


def available_types() -> list[WorkspaceType]:
    """The types offered for new workspaces, in the configured order."""
    return [_TYPES[type_id] for type_id in _OFFERED]


def all_types() -> list[WorkspaceType]:
    """Every configured type, the offered ones first: existing workspaces may be of a type that isn't offered anymore."""
    available = available_types()
    return available + [workspace_type for workspace_type in _TYPES.values() if workspace_type not in available]


def default_type() -> WorkspaceType:
    return available_types()[0]


def get_type(type_id: str | None) -> WorkspaceType:
    workspace_type = _TYPES.get(type_id) if type_id else default_type()
    if workspace_type is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown workspace type: {type_id}")
    return workspace_type
