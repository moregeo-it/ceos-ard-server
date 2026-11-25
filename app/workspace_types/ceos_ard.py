import asyncio
import copy
import hashlib
import logging
import re
import shutil
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import yaml
from ceos_ard_cli.schema import PFS_DOCUMENT
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from strictyaml import YAMLValidationError, as_document
from strictyaml.exceptions import YAMLSerializationError

from app.utils.limits import FileTooLarge, check_read_size
from app.utils.pfs_utils import PlainStringSafeLoader
from app.utils.toolchains import Toolchain, ToolchainError, ToolchainSpec, toolchains
from app.utils.yaml_cache import load_yaml_cached
from app.workspace_types.base import (
    DEFAULT_HIDDEN_PATHS,
    Action,
    ActionResult,
    EditorRule,
    FileMenuRule,
    NameRule,
    PathRules,
    Selection,
    SelectionError,
    Upstream,
    WorkspaceType,
)

if TYPE_CHECKING:
    from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

PFS_FOLDER = "pfs"
# PFS ids become folder and file names and arguments of the ceos-ard CLI
# Capital letters and digits only, like every official PFS id: "-" joins ids in build output names
# (output_name()). Widen it if CEOS ever needs other characters.
PFS_ID_PATTERN = r"^[A-Z0-9]{2,10}$"
PFS_ID_MESSAGE = "A PFS id consists of 2 to 10 capital letters or digits, e.g. NRB"
CREATE_PFS = "create-pfs"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pfs_types: list[str] = Field(["SAR", "Optical"], min_length=1, description="The types a new PFS can have")
    # Without -draft, which the CLI adds to documents that aren't stable
    default_version: str = Field("1.0.0", min_length=1, description="The version of new PFS")


class NewPfs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=PFS_ID_PATTERN)
    title: str = Field(min_length=1, max_length=100)
    type: str
    applies_to: str = Field("", max_length=2000)
    base: str | None = Field(None, pattern=PFS_ID_PATTERN)


def create_pfs_schema(pfs_types: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["id", "title", "type"],
        "properties": {
            "id": {
                "type": "string",
                "title": "ID",
                "pattern": PFS_ID_PATTERN,
                "description": "The name of the new PFS folder: 2 to 10 capital letters or digits, e.g. NRB",
            },
            "title": {"type": "string", "title": "Title", "minLength": 1, "maxLength": 100, "description": "e.g. Normalised Radar Backscatter"},
            "type": {"type": "string", "title": "Type", "enum": pfs_types},
            "applies_to": {
                "type": "string",
                "title": "Applies To",
                "maxLength": 2000,
                "description": "e.g. Data collected by Synthetic Aperture Radar sensors",
            },
            "base": {"type": "string", "title": "Base PFS", "description": "Copies this PFS of the workspace as the start of the new one"},
        },
    }


CREATE_PFS_UI_SCHEMA = {
    "type": "VerticalLayout",
    "elements": [
        {"type": "Control", "scope": "#/properties/id"},
        {"type": "Control", "scope": "#/properties/title"},
        {"type": "Control", "scope": "#/properties/type"},
        {"type": "Control", "scope": "#/properties/applies_to", "options": {"multi": True}},
        {"type": "Control", "scope": "#/properties/base"},
    ],
}


def default_pfs_document(*, title: str, version: str, type: str, applies_to: str = "", author: str | None = None) -> dict[str, Any]:
    return {
        "title": title,
        "version": version,
        "type": type,
        "applies_to": applies_to,
        "background": "",
        "glossary": [],
        "references": [],
        "introduction": [],
        "requirements": [],
        "annexes": [],
        "authors": [author] if author else [],
        "changes": [],
    }


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


CLI_PACKAGE = "ceos-ard-cli"
CLI_PYPROJECT_URL = "https://raw.githubusercontent.com/ceos-org/ceos-ard-cli/v{version}/pyproject.toml"
REQUIREMENTS_FILE = "requirements.txt"
# The first version ceos-org/ceos-ard pinned in requirements.txt
MIN_CLI_VERSION = Version("0.4.0")
MAX_REQUIREMENTS_SIZE = 4096


def cli_version(workspace_path: Path, installed: Iterable[str] = ()) -> str:
    """
    The CLI version `requirements.txt` asks for: until 1.0.0 exactly the version given with `~=` or `==`,
    from 1.0.0 the highest installed version that satisfies it, else the version given.
    """
    invalid = ToolchainError(f"{REQUIREMENTS_FILE} may only contain one line like `{CLI_PACKAGE}~=1.2.3`")
    file = workspace_path / REQUIREMENTS_FILE
    if not file.exists() and not file.is_symlink():
        raise ToolchainError(
            f"The workspace has no {REQUIREMENTS_FILE}, which sets the version of the CEOS-ARD CLI. Update the workspace from upstream to get it."
        )
    # Read in the server, so only a regular file of the workspace, and only as much as one line needs
    if file.is_symlink() or not file.is_file():
        raise invalid
    try:
        with file.open("rb") as handle:
            content = handle.read(MAX_REQUIREMENTS_SIZE + 1)
        if len(content) > MAX_REQUIREMENTS_SIZE:
            raise invalid
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise invalid from None
    lines = [line for line in (line.split("#", 1)[0].strip() for line in text.splitlines()) if line]
    if len(lines) != 1:
        raise invalid
    try:
        requirement = Requirement(lines[0])
        specifiers = list(requirement.specifier)
        version = Version(specifiers[0].version) if len(specifiers) == 1 else None
    except (InvalidRequirement, InvalidVersion):
        raise invalid from None
    if (
        canonicalize_name(requirement.name) != CLI_PACKAGE
        or requirement.extras
        or requirement.url
        or requirement.marker
        or version is None
        or specifiers[0].operator not in ("~=", "==")
        or len(version.release) != 3
        or version.epoch
        or version.local
        or version.is_devrelease
    ):
        raise invalid
    if version < MIN_CLI_VERSION:
        raise ToolchainError(f"CEOS-ARD CLI {version} is too old, at least {MIN_CLI_VERSION} is needed. Update the workspace from upstream.")
    if version.major >= 1:
        candidates = [Version(name.removeprefix(f"{CLI_PACKAGE}-")) for name in installed if name.startswith(f"{CLI_PACKAGE}-")]
        candidates = [candidate for candidate in candidates if candidate in requirement.specifier]
        if candidates:
            version = max(candidates)
    return str(version)


async def cli_toolchain_spec(version: str) -> ToolchainSpec:
    """The CLI and the conda packages its release pins, e.g. pandoc."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(CLI_PYPROJECT_URL.format(version=version))
        if response.status_code == 404:
            raise ToolchainError(f"CEOS-ARD CLI {version} doesn't exist")
        response.raise_for_status()
        pyproject = tomllib.loads(response.text)
        pypi = {CLI_PACKAGE: f"=={version}"}
        # Pre-releases the CLI pins must be requested explicitly, e.g. bibtexparser==2.0.0b9
        for dependency in pyproject["project"]["dependencies"]:
            requirement = Requirement(dependency)
            if any(specifier.operator == "==" and Version(specifier.version).is_prerelease for specifier in requirement.specifier):
                pypi[requirement.name] = str(requirement.specifier)
        return ToolchainSpec(conda=dict(pyproject["tool"]["pixi"]["dependencies"]), pypi=pypi, chromium=True)
    except ToolchainError:
        raise
    except (httpx.HTTPError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as e:
        raise ToolchainError(f"What CEOS-ARD CLI {version} needs can't be found out right now, try again later") from e


@dataclass
class CeosArdType(WorkspaceType):
    pfs_settings: Settings = field(default_factory=Settings)

    toolchain_files = frozenset({REQUIREMENTS_FILE})
    # The toolchain is installed from the CLI's own pins, so the CLI (from 0.8.0) needn't check it on every build
    build_env = {"CEOS_ARD_SKIP_TOOL_CHECK": "1"}

    async def action_form(self, workspace_path: Path, action: Action) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if action.id != CREATE_PFS:
            return await super().action_form(workspace_path, action)
        schema, ui_schema = copy.deepcopy(action.json_schema), copy.deepcopy(action.ui_schema)
        options = await self.selection_options(workspace_path)
        if options:
            schema["properties"]["base"]["oneOf"] = [{"const": option["id"], "title": f"{option['id']}: {option['title']}"} for option in options]
        else:
            del schema["properties"]["base"]
            ui_schema["elements"] = [element for element in ui_schema["elements"] if element["scope"] != "#/properties/base"]
        return schema, ui_schema

    async def run_action(self, workspace_path: Path, action: str, data: dict[str, Any], author: str) -> ActionResult:
        if action != CREATE_PFS:
            raise ValueError(f"Unknown action: {action}")
        try:
            request = NewPfs.model_validate(data)
        except ValidationError as e:
            error = e.errors()[0]
            raise ValueError(f"Invalid {'.'.join(map(str, error['loc'])) or 'input'}: {error['msg']}") from e
        if request.type not in self.pfs_settings.pfs_types:
            raise ValueError(f"The type must be one of {', '.join(self.pfs_settings.pfs_types)}")
        target = workspace_path / PFS_FOLDER / request.id
        if target.exists():
            raise FileExistsError("PFS already exists")
        try:
            # In a thread: copying a base PFS is heavy filesystem work, safe under the workspace lock
            await asyncio.to_thread(self._create_pfs, workspace_path, target, request, author)
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
            raise
        return ActionResult(created=target, open=target / "document.yaml")

    def _create_pfs(self, workspace_path: Path, target: Path, request: NewPfs, author: str) -> None:
        document_path = target / "document.yaml"
        data = None
        if request.base:
            base_path = workspace_path / PFS_FOLDER / request.base
            if not base_path.is_dir():
                raise ValueError("Base PFS not found")
            shutil.copytree(base_path, target)
            if document_path.exists():
                try:
                    check_read_size(document_path.stat().st_size, document_path.name)
                    # Ideally loaded with strictyaml, but it resolves references, so they couldn't be written back. This
                    # loses e.g. comments in document.yaml, until ceos-ard-cli can load unresolved documents.
                    data = yaml.load(document_path.read_text(encoding="utf-8"), Loader=PlainStringSafeLoader)
                except (yaml.YAMLError, FileTooLarge) as e:
                    raise ValueError(f"Failed to read base PFS document: {e}") from e
                if isinstance(data, dict):
                    data.update(request.model_dump(include={"title", "type", "applies_to"}, exclude_unset=True))
        else:
            target.mkdir(parents=True)

        settings = self.pfs_settings
        if not isinstance(data, dict):
            data = default_pfs_document(
                title=request.title, version=settings.default_version, type=request.type, applies_to=request.applies_to, author=author
            )
        # The CLI refuses a PFS without authors
        defaults = default_pfs_document(title="", version=settings.default_version, type=request.type, author=author)
        for key, value in data.items():
            if (value is None or (key == "authors" and not value)) and key in defaults:
                data[key] = defaults[key]
        try:
            schema = PFS_DOCUMENT(file=document_path.name, base_path=workspace_path)
            document_path.write_text(as_document(data, schema).as_yaml(), encoding="utf-8")
        except (YAMLValidationError, YAMLSerializationError) as e:
            raise ValueError(f"Failed to write PFS document: {e}") from e

    def toolchain(self, workspace_path: Path) -> Toolchain:
        version = cli_version(workspace_path, toolchains.installed())
        return Toolchain(name=f"{CLI_PACKAGE}-{version}", spec=lambda: cli_toolchain_spec(version))

    def build_command(self, workspace_path: Path, selection: list[str], output_prefix: str, include_format: str | None) -> list[str]:
        # The flags disable the formats
        disabled = [f"--{format}" for format in ("pdf", "docx") if format != include_format]
        return ["ceos-ard", "generate", *selection, "-o", output_prefix, "-i", str(workspace_path), *disabled]

    def output_name(self, selection: list[str]) -> str:
        if not selection:
            raise SelectionError("Select at least one PFS for the preview")
        if not all(re.fullmatch(PFS_ID_PATTERN, pfs_id) for pfs_id in selection):
            raise SelectionError("Invalid PFS id")
        name = "-".join(selection)
        if len(name) > 200:  # a file name holds at most 255 bytes
            name = hashlib.sha256(name.encode()).hexdigest()
        return name

    async def selection_options(self, workspace_path: Path) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._pfs, workspace_path)

    @staticmethod
    def _pfs(workspace_path: Path) -> list[dict[str, str]]:
        """The PFS of the workspace: the folders of pfs/ with a document.yaml."""
        folder = workspace_path / PFS_FOLDER
        if not folder.is_dir():
            return []
        options = []
        for pfs_dir in sorted(folder.iterdir()):
            document_path = pfs_dir / "document.yaml"
            if not pfs_dir.is_dir() or not document_path.is_file():
                continue
            try:
                # Cached (read-only use): parsed on every request
                document = load_yaml_cached(document_path, PlainStringSafeLoader)
            except Exception as e:
                logger.error(f"Error reading PFS document {document_path}: {e}")
                continue
            if not isinstance(document, dict):
                logger.error(f"Invalid PFS document format in {document_path}")
                continue
            options.append({"id": pfs_dir.name, "title": document.get("title") or pfs_dir.name, "path": f"/{PFS_FOLDER}/{pfs_dir.name}"})
        return options

    async def upstream_selection_options(self, github: "GitHubService", token: str) -> list[dict[str, str]]:
        upstream = self.upstream
        names = await github.get_folder_names(upstream.owner, upstream.repo, token, PFS_FOLDER, upstream.branch)
        return [{"id": name, "title": name} for name in names]

    async def file_usage(self, workspace_path: Path, file_path: str) -> list[dict[str, str]] | None:
        return await asyncio.to_thread(self._requirement_usage, workspace_path, file_path)

    def _requirement_usage(self, workspace_path: Path, file_path: str) -> list[dict[str, str]] | None:
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


def create(type_id: str, type_settings: Settings) -> CeosArdType:
    return CeosArdType(
        pfs_settings=type_settings,
        id=type_id,
        title="CEOS-ARD",
        description="Product Family Specifications for CEOS Analysis Ready Data",
        upstream=Upstream("ceos-org", "ceos-ard", "main"),
        paths=PathRules(hidden=DEFAULT_HIDDEN_PATHS | {"build", "templates"}),
        selection=Selection(label="PFS", multiple=True, on_create=True),
        downloads=["pdf", "docx"],
        editors=[EditorRule(match="glossary/*.yaml", json_schema=GLOSSARY_SCHEMA, ui_schema=GLOSSARY_UI_SCHEMA)],
        # PFS are created with their action; only new ones can be renamed or deleted
        actions=[
            Action(
                id=CREATE_PFS,
                title="Add New PFS",
                folder=f"/{PFS_FOLDER}",
                json_schema=create_pfs_schema(type_settings.pfs_types),
                ui_schema=CREATE_PFS_UI_SCHEMA,
            )
        ],
        # A PFS folder name is its id, which the preview and the CLI only accept in this form
        names=[NameRule(match=f"{PFS_FOLDER}/*", kind="folder", pattern=PFS_ID_PATTERN, message=PFS_ID_MESSAGE)],
        file_menu=[
            FileMenuRule(match="pfs", kind="folder", ops=[]),
            FileMenuRule(match="pfs/*", kind="folder", ops=[], ops_if_new=["rename", "delete"]),
            FileMenuRule(match="pfs/**", kind="file", ops=["open_source", "download", "diff", "revert"]),
        ],
    )
