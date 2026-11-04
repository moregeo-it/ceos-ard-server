import logging
import re
from pathlib import Path

from app.config import settings
from app.utils.pfs_utils import PlainStringSafeLoader
from app.utils.yaml_cache import load_yaml_cached
from app.workspace_types.base import DEFAULT_HIDDEN_PATHS, Action, EditorRule, FileMenuRule, PathRules, Upstream, WorkspaceType

logger = logging.getLogger(__name__)

GLOSSARY_SCHEMA = {
    "type": "object",
    "properties": {
        "term": {"type": "string", "minLength": 2},
        "description": {"type": "string", "minLength": 2, "description": "Markdown is supported for formatting."},
    },
}

GLOSSARY_UI_SCHEMA = {
    "type": "VerticalLayout",
    "elements": [
        {"type": "Control", "scope": "#/properties/term", "options": {"clearable": False}},
        # The description can't be multiline for glossary descriptions yet
        {"type": "Control", "scope": "#/properties/description", "options": {"clearable": False}},
    ],
}


class CeosArdType(WorkspaceType):
    def file_usage(self, workspace_path: Path, file_path: str) -> list[dict[str, str]] | None:
        match = re.match(r"^[\\/]?requirements[\\/](.+)\.ya?ml$", file_path)
        if not match:
            # TODO: implement for glossary, sections, references as well
            return None

        requirement_name = match.group(1)

        pfs_dir = workspace_path / "pfs"
        if not pfs_dir.exists() or not pfs_dir.is_dir():
            return None

        usage = []
        for pfs_folder in pfs_dir.iterdir():
            if not pfs_folder.is_dir():
                continue

            document_file = pfs_folder / "document.yaml"
            if not document_file.exists():
                continue

            try:
                # Cached (read-only use): parsed on the loop on every /context request
                document = load_yaml_cached(document_file, PlainStringSafeLoader)
                categories = document.get("requirements", [])
            except Exception as e:
                logger.warning(f"Could not read or parse requirements file for {pfs_folder.name}: {e}")
                continue

            if not isinstance(categories, list):
                continue

            for category in categories:
                if isinstance(category, dict) and "requirements" in category:
                    requirements = category.get("requirements", [])
                    if isinstance(requirements, list) and any(
                        req == requirement_name or (isinstance(req, dict) and req.get("ref") == requirement_name) for req in requirements
                    ):
                        usage.append({"label": pfs_folder.name, "path": f"/pfs/{pfs_folder.name}/document.yaml"})
                        break

        return usage


def create() -> CeosArdType:
    return CeosArdType(
        id="ceos-ard",
        title="CEOS-ARD",
        description="Product Family Specifications for CEOS Analysis Ready Data",
        upstream=Upstream(settings.CEOS_ARD_ORG, settings.CEOS_ARD_REPO, settings.CEOS_ARD_BRANCH),
        paths=PathRules(hidden=DEFAULT_HIDDEN_PATHS | {"build", "templates"}),
        pfs=True,
        editors=[EditorRule(match="glossary/*.yaml", json_schema=GLOSSARY_SCHEMA, ui_schema=GLOSSARY_UI_SCHEMA)],
        # PFS are created with their own dialog; only new ones can be renamed or deleted
        actions=[Action(id="create-pfs", title="Add New PFS", folder="/pfs", dialog="CreatePfsDialog")],
        file_menu=[
            FileMenuRule(match="pfs", kind="folder", ops=[]),
            FileMenuRule(match="pfs/*", kind="folder", ops=[], ops_if_new=["rename", "delete"]),
            FileMenuRule(match="pfs/**", kind="file", ops=["open_source", "download", "diff", "revert"]),
        ],
    )
