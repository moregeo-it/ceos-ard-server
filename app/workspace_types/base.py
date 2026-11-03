import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_HIDDEN_PATHS = frozenset({".git", ".github"})

# The operations of the file menu. The last rule matching a path decides which are offered there;
# "ops_if_new" are added for files and folders that are new in the workspace.
DEFAULT_FILE_MENU = [
    {"match": "**", "kind": "folder", "ops": ["create", "rename", "delete"]},
    {"match": "**", "kind": "file", "ops": ["open_source", "download", "diff", "revert", "rename", "delete"]},
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

    def is_hidden(self, root_entry: str) -> bool:
        return root_entry in self.hidden


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
    # Whether workspaces select PFS for the document preview
    pfs: bool = False
    searchable_extensions: frozenset[str] = frozenset({".txt", ".md", ".json", ".yaml", ".yml", ".xml"})
    labels: dict[str, str] = field(default_factory=dict)
    editors: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    file_menu: list[dict[str, Any]] = field(default_factory=list)

    def allowed_operations(self, path: str, kind: str, is_new: bool) -> set[str]:
        rule = None
        for candidate in DEFAULT_FILE_MENU + self.file_menu:
            if candidate.get("kind", kind) == kind and match_glob(path, candidate["match"]):
                rule = candidate
        operations = set(rule["ops"])
        if is_new:
            operations |= set(rule.get("ops_if_new", []))
        return operations

    def file_usage(self, workspace_path: Path, file_path: str) -> list[dict[str, str]] | None:
        return None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "color": self.color,
            "upstream": {"owner": self.upstream.owner, "repo": self.upstream.repo, "branch": self.upstream.branch, "url": self.upstream.url},
            "pfs": self.pfs,
            "labels": self.labels,
            "editors": self.editors,
            "actions": self.actions,
            "file_menu": DEFAULT_FILE_MENU + self.file_menu,
        }
