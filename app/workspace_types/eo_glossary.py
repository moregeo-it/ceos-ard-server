import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.sandbox import run_python
from app.utils.workspace_cache import WorkspaceCache
from app.workspace_types import eo_glossary_render
from app.workspace_types.base import (
    DEFAULT_HIDDEN_PATHS,
    Action,
    ActionResult,
    EditorRule,
    FileMenuRule,
    PathRules,
    SearchFilter,
    Upstream,
    WorkspaceType,
)
from app.workspace_types.eo_glossary_render import (
    TERM_CLASSES,
    TERM_GROUPS,
    TERM_STATUSES,
    TERMS_DIR,
    build_term_map,
    is_term,
    read_file,
    slugify,
)
from app.workspace_types.eo_glossary_render import tags as term_tags

logger = logging.getLogger(__name__)

_MARKDOWN = {"type": "string", "contentMediaType": "text/markdown"}
TERM_SCHEMA = {
    "type": "object",
    "required": ["title", "class", "status", "definitions"],
    "properties": {
        "title": {"type": "string", "title": "Term", "minLength": 2},
        "description": {"type": "string", "title": "Summary", "description": "One sentence, used for search engines and link previews."},
        "class": {"type": "string", "title": "Class", "enum": TERM_CLASSES},
        "status": {"type": "string", "title": "Status", "enum": TERM_STATUSES},
        "groups": {"type": "array", "title": "Responsible groups", "uniqueItems": True, "items": {"type": "string", "enum": TERM_GROUPS}},
        "synonyms": {"type": "array", "title": "Synonyms", "items": {"type": "string"}},
        "definitions": {
            "type": "array",
            "title": "Definitions",
            "minItems": 1,
            "items": {
                "type": "object",
                "title": "Definition",
                "properties": {
                    "definition": {**_MARKDOWN, "title": "Definition", "description": "Must be substitutable for the term in a sentence."},
                    "notes": {**_MARKDOWN, "title": "Notes"},
                    "examples": {**_MARKDOWN, "title": "Examples"},
                    "sources": {**_MARKDOWN, "title": "Sources"},
                },
            },
        },
        "other_tags": {"type": "array", "title": "Other tags", "items": {"type": "string"}},
    },
}

RENDERER = Path(eo_glossary_render.__file__)

# The preview and the usage of a file come from one run, whichever of them is requested first
_rendered = WorkspaceCache()


class RenderError(Exception):
    pass


async def _render(workspace_path: Path, file_path: str) -> dict[str, Any]:
    """The preview (`html`) and usage of a file. Rendering parses every term, all of them workspace content: isolated like a build."""

    async def run() -> dict[str, Any]:
        result = await run_python(RENDERER, str(workspace_path), file_path, workspace=workspace_path, timeout=settings.BUILD_TIMEOUT)
        if result.returncode != 0:
            logger.error(f"Rendering {file_path} failed: {result.stderr.decode(errors='replace')[-2000:]}")
            raise RenderError(f"{file_path} could not be rendered")
        return json.loads(result.stdout)

    return await _rendered.get(workspace_path.name, file_path, run)


_MULTI = {"multi": True}
TERM_UI_SCHEMA = {
    "type": "VerticalLayout",
    "elements": [
        {"type": "Control", "scope": "#/properties/title"},
        {"type": "Control", "scope": "#/properties/description", "options": _MULTI},
        {"type": "Control", "scope": "#/properties/status", "options": {"chips": True}},
        {"type": "Control", "scope": "#/properties/class", "options": {"chips": True}},
        {"type": "Control", "scope": "#/properties/groups"},
        {"type": "Control", "scope": "#/properties/synonyms"},
        {
            "type": "Control",
            "scope": "#/properties/definitions",
            "options": {
                "elementLabelProp": "definition",
                "detail": {
                    "type": "VerticalLayout",
                    "elements": [
                        {"type": "Control", "scope": "#/properties/definition", "options": _MULTI},
                        {"type": "Control", "scope": "#/properties/notes", "options": _MULTI},
                        {"type": "Control", "scope": "#/properties/examples", "options": _MULTI},
                        {"type": "Control", "scope": "#/properties/sources", "options": _MULTI},
                    ],
                },
            },
        },
        {"type": "Control", "scope": "#/properties/other_tags"},
    ],
}

NEW_TERM_SCHEMA = {
    "type": "object",
    "required": ["title"],
    "properties": {"title": {"type": "string", "title": "Term", "minLength": 3, "maxLength": 100}},
}


class EoGlossaryType(WorkspaceType):
    # The links between terms come from the renderer, which runs in the build sandbox
    usage_in_sandbox = True

    def matches_search_filters(self, workspace_path: Path, file_path: str, filters: dict[str, set[str]]) -> bool:
        if not is_term(file_path):
            return False
        tags = term_tags(workspace_path, file_path)
        # OR within a filter, AND across filters
        return all(tags & values for values in filters.values())

    def tracks_usage(self, file_path: str) -> bool:
        return is_term(file_path)

    async def file_usage(self, workspace_path: Path, file_path: str) -> list[dict[str, str]]:
        usage = (await _render(workspace_path, file_path))["usage"] or []
        return [{"label": info["title"], "path": info["path"]} for info in usage]

    async def render_file(self, workspace_path: Path, file_path: str) -> str | None:
        return (await _render(workspace_path, file_path))["html"]

    async def run_action(self, workspace_path: Path, action: str, data: dict[str, Any], author: str) -> ActionResult:
        if action != "new-term":
            raise ValueError(f"Unknown action: {action}")
        title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
        if not 3 <= len(title) <= 100:
            raise ValueError("The term needs 3 to 100 characters")
        slug = slugify(title)
        if not re.match(r"^\w[\w.-]*\w$", slug):
            raise ValueError("The term may only contain letters, digits, spaces, dots and hyphens")

        terms_dir = workspace_path / TERMS_DIR
        if not terms_dir.resolve().is_relative_to(workspace_path.resolve()):
            raise ValueError(f"{TERMS_DIR} is not a folder of the workspace")
        target = terms_dir / f"{slug}.md"
        existing = build_term_map(workspace_path).get(title.lower())
        if target.exists() or existing:
            raise FileExistsError(f'The term "{title}" exists already')

        try:
            template = read_file(workspace_path, workspace_path / "docs" / "_template.md").replace("Term Name", title)
        except OSError:
            template = None
        if template is not None:
            # The template shows how to add a second definition; a new term starts with one
            template = re.split(r"^_{3,}\s*$", template, maxsplit=1, flags=re.MULTILINE)[0].rstrip() + "\n"
        else:
            front_matter = f'---\ntitle: {title}\ndescription: ""\ntags:\n  - core\n  - to be defined\n---\n\n'
            template = front_matter + f"# {title}\n\n## 1 Definition\n\n### Notes\n\n### Examples\n\n### Sources\n"
        terms_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(template, encoding="utf-8", newline="\n")
        return ActionResult(created=target, open=target)


def create(type_id: str, type_settings: None) -> EoGlossaryType:
    return EoGlossaryType(
        id=type_id,
        title="CEOS EO Glossary",
        description="Terms and definitions of the CEOS Earth Observation Glossary",
        upstream=Upstream("ceos-org", "eo-glossary", "main"),
        paths=PathRules(hidden=DEFAULT_HIDDEN_PATHS, visible=frozenset({"docs"})),
        color="#00796b",
        preview="file",
        searchable_extensions=frozenset({".md", ".mdx"}),
        expanded_folders=["/docs", f"/{TERMS_DIR}"],
        labels={"usage": "Linked from"},
        editors=[EditorRule(match=f"{TERMS_DIR}/*.md", codec="glossary-term", json_schema=TERM_SCHEMA, ui_schema=TERM_UI_SCHEMA)],
        search_filters=[
            SearchFilter(id="status", title="Status", values=TERM_STATUSES),
            SearchFilter(id="class", title="Class", values=TERM_CLASSES),
            SearchFilter(id="group", title="Group", values=TERM_GROUPS),
        ],
        actions=[Action(id="new-term", title="New term", folder=f"/{TERMS_DIR}", json_schema=NEW_TERM_SCHEMA)],
        # Terms are created with "New term", and renamed by changing their title, which keeps file name and title in sync
        file_menu=[
            FileMenuRule(match="docs", kind="folder", ops=["create"]),
            FileMenuRule(match=TERMS_DIR, kind="folder", ops=[]),
            FileMenuRule(match=f"{TERMS_DIR}/*", kind="file", ops=["open_source", "download", "diff", "revert", "delete"]),
        ],
    )
