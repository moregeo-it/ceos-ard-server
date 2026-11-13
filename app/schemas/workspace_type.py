from typing import Literal

from pydantic import BaseModel, Field

from app.workspace_types.base import Action, EditorRule, FileMenuRule, Labels, SearchFilter


class UpstreamResponse(BaseModel):
    owner: str
    repo: str
    branch: str
    url: str


class WorkspaceTypeResponse(BaseModel):
    id: str
    title: str
    description: str
    available: bool = Field(description="Whether new workspaces of this type can be created")
    color: str
    upstream: UpstreamResponse
    pfs: bool
    preview: Literal["pfs", "file"]
    expanded_folders: list[str]
    labels: Labels
    editors: list[EditorRule]
    search_filters: list[SearchFilter]
    actions: list[Action]
    file_menu: list[FileMenuRule]
