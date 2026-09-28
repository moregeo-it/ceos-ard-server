import logging
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.workspace import GitWorkspace
from app.schemas.events import EventType
from app.services.build_service import BuildService
from app.services.events_service import EventBroker, event_broker
from app.services.workspace_service import WorkspaceService
from app.utils.locks import build_locks
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(
        self, build_service: BuildService | None = None, workspace_service: WorkspaceService | None = None, broker: EventBroker | None = None
    ):
        self.build_service = build_service or BuildService()
        self.workspace_service = workspace_service or WorkspaceService()
        self.broker = broker or event_broker

    async def _build(self, workspace: GitWorkspace, pfs: list[str] | None, include_format: str | None = None) -> str:
        """Run the build (the caller holds `build_locks`) and return the output prefix."""
        build_info = await self.build_service.build(
            workspace_path=workspace.abs_path, workspace_id=workspace.id, pfs=pfs, include_format=include_format
        )
        if build_info.get("status") != "success":
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=build_info.get("message"))
        return build_info["output_file"]

    async def generate_preview(self, db: Session, pfs: list[str] | None, workspace_id: str, user_id: str):
        # Owner only: a build writes into the workspace, and this build is what everyone else sees
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, min_role="owner")
        pfs_selection = pfs or workspace.pfs

        # One lock per workspace: every build writes into the same build/ directory
        async with build_locks(workspace_id):
            prefix = await self._build(workspace, pfs_selection)
            html = await self._get_preview_files(workspace.abs_path, file_prefix=prefix)

        self.broker.emit(workspace_id, EventType.PREVIEW_GENERATED, actor_user_id=user_id, pfs=list(pfs_selection or []))
        return html

    async def get_current_preview(self, db: Session, workspace_id: str, user_id: str):
        """The owner's last build for the workspace's PFS list, without building."""
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        prefix = self.build_service.output_prefix(workspace.abs_path, workspace.pfs)

        async with build_locks(workspace_id):
            if not workspace.pfs or not Path(prefix + ".html").exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preview has been generated yet")
            return await self._get_preview_files(workspace.abs_path, file_prefix=prefix)

    async def _get_preview_files(self, workspace_path: Path, file_prefix: str | None = None):
        build_dir = workspace_path / "build"

        if not build_dir.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build directory not found")

        filepath = Path(file_prefix + ".html")
        html_content = filepath.read_text(encoding="utf-8") if filepath.exists() else ""

        def replace_edit_tags(match):
            path = Path(match.group(1))
            if not path.is_absolute():
                path = workspace_path / path
            file_path = normalize_workspace_path(path, workspace_path)
            return f'<a name="{file_path}"></a><button class="edit" value="{file_path}">Edit</button>'

        # \ and : are needed for Windows compatibility
        html_content = re.sub(r"<!--\s*edit:\s*([\w\-.~/\\:]+)\s*-->", replace_edit_tags, html_content)

        return html_content

    async def get_preview_static_file(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            return validate_workspace_path(("build/" + file_path), workspace.abs_path, exists=True, type="file", is_preview=True)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting preview static file {file_path} for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while retrieving the preview static file. Please try again later." + str(e),
            ) from e

    async def download_preview_document(self, db: Session, pfs: list[str] | None, format: str, workspace_id: str, user_id: str) -> dict[str, Any]:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

            if workspace.viewer_role == "owner":
                async with build_locks(workspace_id):
                    prefix = await self._build(workspace, pfs or workspace.pfs, include_format=format)
            else:
                # Everyone else gets the owner's last build (both formats), for the owner's PFS selection only
                if pfs and set(pfs) != set(workspace.pfs or []):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can change the PFS selection of the preview"
                    )
                prefix = self.build_service.output_prefix(workspace.abs_path, workspace.pfs)

            document_file = Path(f"{prefix}.{format}")
            if not document_file.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requested document file not found")

            return {
                "path": document_file,
                "name": document_file.name,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error downloading preview document for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while downloading the preview document. Please try again later." + str(e),
            ) from e
