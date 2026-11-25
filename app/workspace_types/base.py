import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.toolchains import Toolchain

if TYPE_CHECKING:
    from app.services.github_service import GitHubService

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
    # The editor's codecs (src/codecs); it opens files with a codec it doesn't know in the source editor
    codec: str = Field("yaml", pattern=r"^[a-z][a-z0-9-]*$", description="How the editor turns the file into the form data and back")
    json_schema: dict[str, Any] = Field(alias="schema", description="JSON Schema of the form data")
    ui_schema: dict[str, Any] | None = Field(None, description="JSON Forms UI schema")


class Action(Rule):
    id: str
    title: str
    folder: str = Field(description="The folder whose file menu offers the action")
    json_schema: dict[str, Any] = Field(alias="schema", description="JSON Schema of the input, asked for in a generic dialog, see action_form()")
    ui_schema: dict[str, Any] | None = Field(None, description="JSON Forms UI schema")


class NameRule(GlobRule):
    kind: Literal["file", "folder"] | None = Field(None, description="Only files or only folders; both if omitted")
    pattern: str = Field(description="Regular expression the whole name must match")
    message: str = Field(description="Why a name is refused")

    @field_validator("pattern")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"Invalid regular expression: {e}") from e
        return value


@dataclass(frozen=True)
class ActionResult:
    # The new file or folder, which the file tree shows; everything in it is staged
    created: Path
    # The file the editor opens, if any
    open: Path | None = None


class SearchFilter(Rule):
    id: str
    title: str
    values: list[str]


class Labels(Rule):
    usage: str | None = Field(None, description='Replaces "Used in", the files that use the open file')


class Selection(Rule):
    label: str = Field(description='What is selected, e.g. "PFS" or "Document"')
    multiple: bool = Field(False, description="Whether several can be selected")
    on_create: bool = Field(False, description="Whether new workspaces select from the upstream's options, see upstream_selection_options()")


class SelectionError(ValueError):
    """An invalid selection, with a message for the user."""


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
    # "build": a document built from the workspace's selection, see `selection`; "file": the file open in the editor
    preview: Literal["build", "file"] = "build"
    # What workspaces select for the "build" preview, see selection_options()
    selection: Selection | None = None
    # The formats the built document can be downloaded in, see build_command()
    downloads: list[Literal["pdf", "docx"]] = field(default_factory=list)
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
    # Names new and renamed files and folders must have, e.g. PFS folders named by their id
    names: list[NameRule] = field(default_factory=list)

    def name_error(self, path: str, kind: str) -> str | None:
        """Why `path` (without leading slash) can't be the name of a new or renamed file or folder, if it can't."""
        name = path.rstrip("/").rsplit("/", 1)[-1]
        for rule in self.names:
            if rule.kind in (None, kind) and match_glob(path, rule.match) and not re.fullmatch(rule.pattern, name):
                return rule.message
        return None

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

    def build_command(self, workspace_path: Path, selection: list[str], output_prefix: str, include_format: str | None) -> list[str]:
        """The build command, run with the programs of the toolchain."""
        raise NotImplementedError(f"Workspaces of type {self.id} can't be built")

    # Environment variables for the build command
    build_env: ClassVar[dict[str, str]] = {}

    def output_name(self, selection: list[str]) -> str:
        """The outputs of a selection relative to the build folder, without extension; raises SelectionError for invalid selections.

        Every build and read goes through here, so the selection is checked here too, stored ones included:
        `../` would reach another workspace, a leading `-` would be an option of the build command.
        """
        raise SelectionError(f"Workspaces of type {self.id} can't be built")

    async def selection_options(self, workspace_path: Path) -> list[dict[str, str]]:
        """What the workspace can select, as `{"id", "title"}`, and the `path` of the file or folder that defines it, if any."""
        return []

    async def upstream_selection_options(self, github: "GitHubService", token: str) -> list[dict[str, str]]:
        """What new workspaces can select, from the upstream repository, for a `selection` with `on_create`."""
        return []

    def matches_search_filters(self, workspace_path: Path, file_path: str, filters: dict[str, set[str]]) -> bool:
        return False

    async def action_form(self, workspace_path: Path, action: Action) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The JSON Schema and UI schema the editor asks for the input with, e.g. with options from the workspace."""
        return action.json_schema, action.ui_schema

    async def run_action(self, workspace_path: Path, action: str, data: dict[str, Any], author: str) -> ActionResult:
        """Run an action, removing what it created if it fails; raises ValueError for invalid input, FileExistsError for existing files."""
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
            "preview": self.preview,
            "selection": self.selection,
            "downloads": self.downloads,
            "expanded_folders": self.expanded_folders,
            "labels": self.labels,
            "editors": self.editors,
            "search_filters": self.search_filters,
            "actions": self.actions,
            "file_menu": DEFAULT_FILE_MENU + self.file_menu,
        }
