"""The workspace types of a deployment, read from a YAML file, see workspace-types.example.yaml."""

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


class _Config(BaseModel):
    # A typo fails at startup instead of being ignored
    model_config = ConfigDict(extra="forbid", frozen=True)


class UpstreamConfig(_Config):
    owner: str = Field(min_length=1, max_length=100)
    repo: str | None = Field(None, min_length=1, max_length=100)
    branch: str | None = Field(None, min_length=1, max_length=255)


class TypeConfig(_Config):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,49}$", description="Stored with every workspace, so it never changes")
    module: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="The module of app/workspace_types that implements the type")
    offered: bool = Field(True, description="Whether new workspaces can be created; existing ones keep working either way")
    title: str | None = None
    description: str | None = None
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    upstream: UpstreamConfig | None = Field(None, description="Replaces the module's default upstream; repo and branch keep the default if omitted")
    settings: dict[str, Any] = Field(default_factory=dict, description="Validated by the module's Settings model")


class DeploymentConfig(_Config):
    types: list[TypeConfig] = Field(min_length=1, description="The first offered type is the default")

    @model_validator(mode="after")
    def check_types(self) -> "DeploymentConfig":
        ids = [item.id for item in self.types]
        if duplicates := sorted({item for item in ids if ids.count(item) > 1}):
            raise ValueError(f"Workspace types configured more than once: {', '.join(duplicates)}")
        if not any(item.offered for item in self.types):
            raise ValueError("At least one workspace type must be offered")
        return self


# Without a configuration file: CEOS-ARD with its defaults
DEFAULT = DeploymentConfig(types=[TypeConfig(id="ceos-ard", module="ceos_ard")])


def load(path: Path, required: bool) -> DeploymentConfig:
    """The configuration in `path`; the default if it doesn't exist and isn't `required`."""
    if not path.exists():
        if required:
            raise ValueError(f"The workspace types configuration {path} doesn't exist")
        logger.info(f"No workspace types configuration at {path}, offering CEOS-ARD")
        return DEFAULT
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return DeploymentConfig.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ValueError(f"Invalid workspace types configuration {path}: {e}") from e
