import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.toolchains import Toolchain

DEFAULT_HIDDEN_PATHS = frozenset({".git", ".github"})

FileOperation = Literal["create", "rename", "delete", "revert", "diff", "download", "open_source"]


class Rule(BaseModel):
    # A typo in a type's configuration fails at startup instead of silently never matching
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class GlobRule(Rule):
    match: str = Field(description="Glob of workspace paths without leading slash: `*` matches within a folder, `**` across folders")

    @field_validator("match")
    @classmethod
    def no_leading_slash(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("Globs match paths without leading slash")
        return value


class FileMenuRule(GlobRule):
    kind: Literal["file", "folder"] | None = Field(None, description="Only files or only folders; both if omitted")
    ops: list[FileOperation] = Field(description="The operations offered")
    ops_if_new: list[FileOperation] = Field([], description="Added for files and folders that aren't committed yet")


class EditorRule(GlobRule):
    codec: Literal["yaml", "glossary-term"] = Field("yaml", description="How the editor turns the file into the form data and back")
    json_schema: dict[str, Any] = Field(alias="schema", description="JSON Schema of the form data")
    ui_schema: dict[str, Any] | None = Field(None, description="JSON Forms UI schema")


class Action(Rule):
    id: str
    title: str
    folder: str = Field(description="The folder whose file menu offers the action")
    json_schema: dict[str, Any] | None = Field(None, alias="schema", description="JSON Schema of the input, asked for in a generic dialog")
    # Only the dialogs the editor runs as actions (FileContextMenu.vue)
    dialog: Literal["CreatePfsDialog"] | None = Field(None, description="An editor dialog that runs the action instead")

    @model_validator(mode="after")
    def schema_or_dialog(self) -> "Action":
        if (self.json_schema is None) == (self.dialog is None):
            raise ValueError("An action has either a schema or a dialog")
        return self


class SearchFilter(Rule):
    id: str
    title: str
    values: list[str]


class Labels(Rule):
    usage: str | None = Field(None, description='Replaces "Used in", the files that use the open file')


# The file menu of every type starts with these rules; the last rule matching a path decides
DEFAULT_FILE_MENU = [
    FileMenuRule(match="**", kind="folder", ops=["create", "rename", "delete"]),
    FileMenuRule(match="**", kind="file", ops=["open_source", "download", "diff", "revert", "rename", "delete"]),
]


def match_glob(path: str, glob: str) -> bool:
    """`*` matches within a folder, `**` across folders; paths without leading slash, like the editor's glob.js."""
    pattern = ""
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            pattern += "(?:.*/)?"
            i += 3
            continue
        if glob.startswith("**", i):
            pattern += ".*"
            i += 2
            continue
        char = glob[i]
        pattern += "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
        i += 1
    return re.fullmatch(pattern, path.strip("/")) is not None


@dataclass(frozen=True)
class PathRules:
    hidden: frozenset[str] = DEFAULT_HIDDEN_PATHS
    # Only these root entries are shown when set, e.g. only the content folder of a site repository
    visible: frozenset[str] | None = None

    def is_hidden(self, root_entry: str) -> bool:
        if root_entry in self.hidden:
            return True
        return self.visible is not None and root_entry not in self.visible


@dataclass(frozen=True)
class Upstream:
    owner: str
    repo: str
    branch: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass
class WorkspaceType:
    id: str
    title: str
    description: str
    upstream: Upstream
    paths: PathRules
    # Distinguishes the workspaces of this type in the editor, e.g. in headers and badges
    color: str = "#3e5265"
    # Whether workspaces select PFS for the document preview, see `preview`
    pfs: bool = False
    # "pfs": a document built from the selected PFS; "file": the file open in the editor
    preview: Literal["pfs", "file"] = "pfs"
    # Folders the file tree shows opened, e.g. when all content is in one folder
    expanded_folders: list[str] = field(default_factory=list)
    searchable_extensions: frozenset[str] = frozenset({".txt", ".md", ".json", ".yaml", ".yml", ".xml"})
    labels: Labels = field(default_factory=Labels)
    # The first rule whose glob matches a file decides its form editor
    editors: list[EditorRule] = field(default_factory=list)
    search_filters: list[SearchFilter] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    # Added to DEFAULT_FILE_MENU; the last rule matching a path decides
    file_menu: list[FileMenuRule] = field(default_factory=list)

    def allowed_operations(self, path: str, kind: str, is_new: bool) -> set[str]:
        rule = None
        for candidate in DEFAULT_FILE_MENU + self.file_menu:
            if candidate.kind in (None, kind) and match_glob(path, candidate.match):
                rule = candidate
        operations = set(rule.ops)
        if is_new:
            operations |= set(rule.ops_if_new)
        return operations

    async def file_usage(self, workspace_path: Path, file_path: str) -> list[dict[str, str]] | None:
        return None

    # Files of the workspace that choose its toolchain: saving them prepares the new toolchain
    toolchain_files: ClassVar[frozenset[str]] = frozenset()

    def toolchain(self, workspace_path: Path) -> Toolchain | None:
        """The toolchain the workspace builds with; raises ToolchainError if the workspace doesn't name a valid one."""
        return None

    def build_command(self, workspace_path: Path, pfs: list[str], output_prefix: str, include_format: str | None) -> list[str]:
        """The build command, run with the programs of the toolchain."""
        raise NotImplementedError(f"Workspaces of type {self.id} can't be built")

    def matches_search_filters(self, workspace_path: Path, file_path: str, filters: dict[str, set[str]]) -> bool:
        return False

    async def run_action(self, workspace_path: Path, action: str, data: dict[str, Any]) -> Path:
        raise NotImplementedError

    async def render_file(self, workspace_path: Path, file_path: str) -> str | None:
        return None

    def to_public(self, available: bool) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "available": available,
            "color": self.color,
            "upstream": {"owner": self.upstream.owner, "repo": self.upstream.repo, "branch": self.upstream.branch, "url": self.upstream.url},
            "pfs": self.pfs,
            "preview": self.preview,
            "expanded_folders": self.expanded_folders,
            "labels": self.labels,
            "editors": self.editors,
            "search_filters": self.search_filters,
            "actions": self.actions,
            "file_menu": DEFAULT_FILE_MENU + self.file_menu,
        }
