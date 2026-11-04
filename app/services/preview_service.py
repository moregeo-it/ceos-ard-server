import asyncio
import contextlib
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.schemas.events import EventType
from app.services.build_service import BuildService
from app.services.events_service import EventBroker, event_broker
from app.services.workspace_service import WorkspaceService
from app.utils.limits import FileTooLarge
from app.utils.locks import build_locks, user_build_locks
from app.utils.sandbox import BuildOutOfMemory
from app.utils.toolchains import ToolchainError, toolchains
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)

DOCUMENT_FORMATS = ("pdf", "docx")


class PreviewService:
    def __init__(
        self, build_service: BuildService | None = None, workspace_service: WorkspaceService | None = None, broker: EventBroker | None = None
    ):
        self.build_service = build_service or BuildService()
        self.workspace_service = workspace_service or WorkspaceService()
        self.broker = broker or event_broker

    async def _build(
        self, workspace: GitWorkspace, user_id: str, pfs: list[str] | None, include_format: str | None = None, build_dir: Path | None = None
    ) -> str:
        """Run the build and return the output prefix; into the workspace's `build/` only under `build_locks`."""
        async with user_build_locks(user_id):
            build_info = await self.build_service.build(
                workspace_path=workspace.abs_path,
                workspace_id=workspace.id,
                workspace_type=workspace.workspace_type,
                pfs=pfs,
                include_format=include_format,
                build_dir=build_dir,
            )
        if build_info.get("status") != "success":
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=build_info.get("message"))
        return build_info["output_file"]

    async def _prepare(self, workspace: GitWorkspace) -> None:
        """Install the workspace's toolchain before a build takes any lock, so a long install doesn't block e.g. GET .../previews/current."""
        toolchain = BuildService.missing_toolchain(workspace.workspace_type, workspace.abs_path)
        if toolchain is None:
            return
        # Without an actor, so the waiting client gets it too: the gateway withholds events from the client that caused them
        self.broker.emit(workspace.id, EventType.PREVIEW_PREPARING, toolchain=toolchain.name)
        # The build reports a failure
        with contextlib.suppress(ToolchainError):
            await toolchains.ensure(toolchain)

    @staticmethod
    def _require_pfs(workspace: GitWorkspace) -> None:
        if not workspace.workspace_type.pfs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="This workspace type previews single files, see GET .../render/{path}"
            )

    async def generate_preview(self, db: Session, workspace_id: str, user_id: str):
        # Owner only: a build writes into the workspace, and this build is what everyone else sees.
        # Always the saved selection, which is what GET .../previews/current reads back.
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, min_role="owner")
        self._require_pfs(workspace)

        await self._prepare(workspace)
        # One lock per workspace: every build writes into the same build/ directory
        async with build_locks(workspace_id):
            prefix = await self._build(workspace, user_id, workspace.pfs)
            html = await self._get_preview_files(workspace, file_prefix=prefix)

        self.broker.emit(workspace_id, EventType.PREVIEW_GENERATED, actor_user_id=user_id, pfs=list(workspace.pfs))
        return html

    async def get_current_preview(self, db: Session, workspace_id: str, user_id: str):
        """The owner's last build for the workspace's PFS list, without building."""
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        self._require_pfs(workspace)
        if not workspace.pfs:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preview has been generated yet")
        prefix = self.build_service.output_prefix(workspace.abs_path, workspace.pfs)

        async with build_locks(workspace_id):
            if not BuildService.artifact(Path(prefix + ".html"), workspace.abs_path / "build"):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preview has been generated yet")
            return await self._get_preview_files(workspace, file_prefix=prefix)

    async def _get_preview_files(self, workspace: GitWorkspace, file_prefix: str):
        workspace_path = workspace.abs_path
        build_dir = workspace_path / "build"

        if not build_dir.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build directory not found")

        filepath = BuildService.artifact(Path(file_prefix + ".html"), build_dir)
        if not filepath:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The build produced no preview")
        try:
            html_content = BuildService.read_preview(filepath)
        except FileTooLarge as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The build produced a preview that is too large") from e
        # Only the owner of an active workspace can edit; everyone else opens the file read-only
        label = "Edit" if workspace.viewer_role == "owner" and workspace.status != WorkspaceStatus.ARCHIVED else "View"

        def replace_edit_tags(match):
            path = Path(match.group(1))
            if not path.is_absolute():
                path = workspace_path / path
            file_path = normalize_workspace_path(path, workspace_path)
            pointer = match.group(2) or ""
            return f'<a name="{file_path}"></a><button class="edit" value="{file_path}{pointer}">{label}</button>'

        # \ and : are needed for Windows compatibility; the optional #/... is a JSON pointer to a field of the file
        html_content = re.sub(r"<!--\s*edit:\s*([\w\-.~/\\:]+)(#[\w\-.~/]*)?\s*-->", replace_edit_tags, html_content)

        return html_content

    async def render_file(self, db: Session, file_path: str, workspace_id: str, user_id: str) -> str:
        """Render one file, without writing anything, so every role can do it."""
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        target = validate_workspace_path(file_path, workspace.abs_path, exists=True, type="file", rules=workspace.workspace_type.paths)
        rel_path = normalize_workspace_path(target, workspace.abs_path)
        try:
            async with user_build_locks(user_id):
                html = await workspace.workspace_type.render_file(workspace.abs_path, rel_path)
        except (TimeoutError, BuildOutOfMemory) as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Rendering the file took too long or needed too much memory"
            ) from e
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The file could not be rendered") from e
        if html is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="There is no preview for this file")
        return html

    async def open_preview_static_file(self, db: Session, file_path: str, workspace_id: str, user_id: str) -> tuple[BinaryIO, Path]:
        """
        Open a file of the preview; the caller closes it.

        Checked and opened while no build runs: a build can swap a checked folder for a symlink to anything before
        the file is opened. Once open, the file stays the one checked.
        """
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            # Not holding the read transaction while a build runs
            db.commit()
            async with build_locks(workspace_id):
                path = validate_workspace_path(("build/" + file_path), workspace.abs_path, exists=True, type="file", is_preview=True)
                return path.open("rb"), path
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting preview static file {file_path} for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while retrieving the preview static file. Please try again later." + str(e),
            ) from e

    async def download_preview_document(self, db: Session, pfs: list[str], format: str, workspace_id: str, user_id: str) -> Path:
        """Build the document for anyone with access and return its path; the caller removes its folder once sent.

        Each download builds into its own folder, so it needs no `build_locks`, and no other build can rewrite it while it is sent.
        """
        # Part of the document's file name, which mustn't lead out of its folder
        if format not in DOCUMENT_FORMATS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"The format must be one of {', '.join(DOCUMENT_FORMATS)}")
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        self._require_pfs(workspace)
        await self._prepare(workspace)
        build_dir = Path(tempfile.mkdtemp(prefix="ceos-ard-download-"))
        try:
            prefix = await self._build(workspace, user_id, pfs, include_format=format, build_dir=build_dir)
            document = BuildService.artifact(Path(f"{prefix}.{format}"), build_dir)
            if not document:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The build produced no document")
            return document
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, build_dir, ignore_errors=True)
            raise
