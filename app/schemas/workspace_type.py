from pydantic import BaseModel, Field

from app.workspace_types.base import Action, EditorRule, FileMenuRule, Labels


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
    labels: Labels
    editors: list[EditorRule]
    actions: list[Action]
    file_menu: list[FileMenuRule]
