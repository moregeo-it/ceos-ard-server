import asyncio
import logging
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from app.config import settings
from app.utils.pfs_utils import PlainStringSafeLoader
from app.utils.toolchains import Toolchain, ToolchainError, ToolchainSpec, toolchains
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


class CeosArdType(WorkspaceType):
    toolchain_files = frozenset({REQUIREMENTS_FILE})
    # The toolchain is installed from the CLI's own pins, so the CLI (from 0.8.0) needn't check it on every build
    build_env = {"CEOS_ARD_SKIP_TOOL_CHECK": "1"}

    def toolchain(self, workspace_path: Path) -> Toolchain:
        version = cli_version(workspace_path, toolchains.installed())
        return Toolchain(name=f"{CLI_PACKAGE}-{version}", spec=lambda: cli_toolchain_spec(version))

    def build_command(self, workspace_path: Path, pfs: list[str], output_prefix: str, include_format: str | None) -> list[str]:
        # The flags disable the formats
        disabled = [f"--{format}" for format in ("pdf", "docx") if format != include_format]
        return ["ceos-ard", "generate", *pfs, "-o", output_prefix, "-i", str(workspace_path), *disabled]

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
