import asyncio
import logging
import os
import re
import time
from pathlib import Path, PurePosixPath

import httpx

from app.utils.toolchains import Toolchain, ToolchainError, ToolchainSpec, toolchains
from app.workspace_types import ogc_sources
from app.workspace_types.base import DEFAULT_HIDDEN_PATHS, PathRules, Selection, SelectionError, Upstream, WorkspaceType

logger = logging.getLogger(__name__)

CLI_GEM = "metanorma-cli"
LATEST_URL = f"https://rubygems.org/api/v1/versions/{CLI_GEM}/latest.json"
# How long the latest version is used before asking RubyGems again
LATEST_FOR = 6 * 3600
VERSION_PATTERN = r"\d+\.\d+\.\d+"
# Ruby and what gems with native extensions need to build
CONDA_PACKAGES = {"ruby": "*", "c-compiler": "*", "cxx-compiler": "*", "make": "*"}
# Metanorma documents set their flavour in the header, e.g. `:mn-document-class: ogc`
DOCUMENT_CLASS = re.compile(r"^:mn-document-class:", re.MULTILINE)
TITLE = re.compile(r"^=\s+(.+?)\s*$", re.MULTILINE)
HEADER_SIZE = 4096
# Bounds the scan of large repositories
MAX_SCANNED_FILES = 5000
SKIPPED_FOLDERS = DEFAULT_HIDDEN_PATHS | {"build", "node_modules"}
DOCUMENT_PATTERN = r"[\w.\-]+(/[\w.\-]+)*\.adoc"
# Metanorma's message when it can't parse a document, e.g. `asciidoctor: FAILED: document.adoc: Failed to load AsciiDoc document - ...`
FAILED = re.compile(r"asciidoctor: FAILED: (.+)")
INCLUDE = re.compile(r"^(?:include|image)::([^\[\s]+)\[", re.MULTILINE)


class _Latest:
    version: str | None = None
    checked_at = 0.0
    lock: asyncio.Lock | None = None


async def refresh_latest() -> None:
    if time.monotonic() - _Latest.checked_at < LATEST_FOR and _Latest.version:
        return
    _Latest.lock = _Latest.lock or asyncio.Lock()
    async with _Latest.lock:
        if time.monotonic() - _Latest.checked_at < LATEST_FOR and _Latest.version:
            return
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(LATEST_URL)
            response.raise_for_status()
            version = response.json()["version"]
            if not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError(f"Unexpected version {version}")
            _Latest.version = version
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            # The installed versions are used until RubyGems answers
            logger.warning(f"Can't find out the latest version of {CLI_GEM}: {e}")
        _Latest.checked_at = time.monotonic()


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def metanorma_version() -> str:
    """The latest version, else the newest installed one."""
    if _Latest.version:
        return _Latest.version
    installed = [name.removeprefix(f"{CLI_GEM}-") for name in toolchains.installed() if name.startswith(f"{CLI_GEM}-")]
    installed = [version for version in installed if re.fullmatch(VERSION_PATTERN, version)]
    if not installed:
        raise ToolchainError("The latest version of Metanorma can't be found out right now, try again later")
    return max(installed, key=_version_key)


def check_document(document: str) -> str:
    # Also an argument of metanorma, so no leading "-", and no "..", which would lead out of the workspace
    if len(document) > 500 or not re.fullmatch(DOCUMENT_PATTERN, document) or ".." in PurePosixPath(document).parts:
        raise SelectionError("Invalid document")
    if any(part.startswith((".", "-")) for part in PurePosixPath(document).parts):
        raise SelectionError("Invalid document")
    return document


def includes_outside(folder: Path) -> bool:
    """Whether AsciiDoc files of the folder include files outside of it, relative to the including file."""
    for path in folder.rglob("*.adoc"):
        try:
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        base = os.path.relpath(path.parent, folder)
        for target in INCLUDE.findall(text):
            # Absolute paths are relative to the include jail, attributes and URLs can't be followed
            if target.startswith(("/", "{")) or "://" in target:
                continue
            resolved = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
            if resolved == ".." or resolved.startswith("../"):
                return True
    return False


def find_documents(workspace_path: Path) -> list[dict[str, str]]:
    """The Metanorma documents of the workspace: AsciiDoc files with `:mn-document-class:` in their header."""
    documents = []
    scanned = 0
    for root, folders, files in os.walk(workspace_path):
        root_path = Path(root)
        folders[:] = sorted(
            folder for folder in folders if not folder.startswith(".") and folder not in SKIPPED_FOLDERS and not (root_path / folder).is_symlink()
        )
        for name in sorted(files):
            if not name.endswith(".adoc"):
                continue
            scanned += 1
            if scanned > MAX_SCANNED_FILES:
                return documents
            path = root_path / name
            if path.is_symlink():
                continue
            try:
                with path.open("rb") as handle:
                    header = handle.read(HEADER_SIZE).decode("utf-8", errors="replace")
            except OSError:
                continue
            if not DOCUMENT_CLASS.search(header):
                continue
            relative = path.relative_to(workspace_path).as_posix()
            try:
                check_document(relative)
            except SelectionError:
                continue
            title = TITLE.search(header)
            documents.append({"id": relative, "title": title.group(1) if title else relative})
    return documents


class OgcType(WorkspaceType):
    build_network = True
    # Metanorma writes its bibliography cache next to the document
    writable_sources = True

    async def refresh_toolchain(self) -> None:
        await refresh_latest()

    def toolchain(self, workspace_path: Path) -> Toolchain:
        version = metanorma_version()

        async def spec() -> ToolchainSpec:
            return ToolchainSpec(conda=CONDA_PACKAGES, gems={CLI_GEM: version})

        return Toolchain(name=f"{CLI_GEM}-{version}", spec=spec)

    def output_name(self, selection: list[str]) -> str:
        if len(selection) != 1:
            raise SelectionError("Select a document for the preview")
        document = PurePosixPath(check_document(selection[0]))
        # One folder per document: Metanorma names the output after the document, e.g. every extension's document.adoc
        folder = "-".join(document.with_suffix("").parts)
        return f"{folder}/{document.stem}"

    def build_command(self, workspace_path: Path, selection: list[str], output_prefix: str, include_format: str | None) -> list[str]:
        if include_format:
            raise ToolchainError("Only the HTML preview is available for OGC documents")
        document = PurePosixPath(check_document(selection[0]))
        command = ["metanorma", "compile", document.name, "-x", "html", "--no-install-fonts", "-o", str(Path(output_prefix).parent)]
        # Asciidoctor only includes files of the document's folder, which some documents rely on for includes with a leading "/";
        # others include files of folders above, e.g. shared examples
        if includes_outside(workspace_path / document.parent):
            command += ["--base-dir", str(workspace_path)]
        return command

    def build_folder(self, selection: list[str]) -> str:
        # Documents include files relative to their folder, some with a leading "/"
        return str(PurePosixPath(check_document(selection[0])).parent)

    def prepare_sources(self, sources: Path) -> dict[str, str]:
        return ogc_sources.prepare(sources)

    def finish_build(self, sources: Path, output_prefix: str) -> None:
        ogc_sources.finish(sources, output_prefix)

    def build_error(self, output: str) -> str | None:
        failed = FAILED.search(output)
        if failed:
            return f"Metanorma couldn't read the document: {failed.group(1).strip()}"
        return "Metanorma couldn't generate the document, e.g. because of an error in the document."

    async def selection_options(self, workspace_path: Path) -> list[dict[str, str]]:
        documents = await asyncio.to_thread(find_documents, workspace_path)
        return [{**document, "path": f"/{document['id']}"} for document in documents]


def create(type_id: str, type_settings: None) -> OgcType:
    return OgcType(
        id=type_id,
        title="OGC",
        description="Standards and other documents of the Open Geospatial Consortium, written with Metanorma",
        # Workspaces choose one of the organization's repositories
        upstream=Upstream("opengeospatial", None, None),
        paths=PathRules(hidden=DEFAULT_HIDDEN_PATHS | {"build"}),
        color="#00599c",
        selection=Selection(label="document"),
        choose_repository=True,
        searchable_extensions=frozenset({".adoc", ".txt", ".md", ".json", ".yaml", ".yml", ".xml"}),
        # Metanorma needs more than the CEOS-ARD CLI: OGC API - Records - Part 1 takes 30 s and 1.1 GB
        build_timeout=180,
        build_memory_max="1536M",
    )
