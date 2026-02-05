import logging
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.services.build_service import BuildService
from app.services.workspace_service import WorkspaceService
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self):
        self.build_service = BuildService()
        self.workspace_service = WorkspaceService()

    async def generate_preview(self, db: Session, pfs: list[str] | None, workspace_id: str, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

        build_info = await self.build_service.build(workspace_path=workspace.abs_path, workspace_id=workspace_id, pfs=pfs or workspace.pfs)

        if build_info.get("status") == "success":
            return await self._get_preview_files(workspace.abs_path, file_prefix=build_info.get("output_file"))
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=build_info.get("message"))

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

            build_info = await self.build_service.build(
                workspace_path=workspace.abs_path, workspace_id=workspace_id, pfs=pfs or workspace.pfs, include_format=format
            )

            if build_info.get("status") == "success":
                document_file = Path(build_info.get("output_file") + f".{format}")
                if document_file.exists():
                    return {
                        "path": document_file,
                        "name": document_file.name,
                    }
                else:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requested document file not found")

            else:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=build_info.get("message"))

        except Exception as e:
            logger.error(f"Error downloading preview document for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while downloading the preview document. Please try again later." + str(e),
            ) from e


preview_service = PreviewService()
