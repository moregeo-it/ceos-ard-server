import logging
import re
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.services.build_service import BuildService
from app.services.workspace_service import WorkspaceService
from app.utils.validation import validate_workspace_path, normalize_workspace_path

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self):
        self.build_service = BuildService()
        self.workspace_service = WorkspaceService()

    async def generate_preview(self, db: Session, pfs: list[str] | None, workspace_id: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

            build_info = await self.build_service.start_build(workspace_path=workspace.abs_path, workspace_id=workspace_id, pfs=pfs or workspace.pfs)

            if build_info.get("status") == "success":
                return await self._get_preview_files(workspace.abs_path, pfs=pfs or workspace.pfs)

            else:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Build failed with status")

        except Exception as e:
            logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while generating the preview files. Please try again later." + str(e),
            ) from e

    async def _get_preview_files(self, workspace_path: Path, pfs: list[str] | None = None):
        build_dir = workspace_path / "build"

        if not build_dir.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build directory not found")

        preview_list = build_dir.iterdir()

        html_content = ""
        for file in preview_list:
            if file.is_file() and file.suffix == ".html" and any(p in file.name for p in (pfs or [])):
                with open(file, encoding="utf-8") as f:
                    html_content += f.read() + "\n"

        def replace_edit_tags(match):
            path = Path(match.group(1))
            if not path.is_absolute():
                path = workspace_path / path
            file_path = normalize_workspace_path(path, workspace_path)
            return f'<a name="{file_path}"></a><button class="edit" value="{file_path}">Edit</button>'

        html_content = re.sub(r"<!--\s*edit:\s*([\w\-.~/\\]+)\s*-->", replace_edit_tags, html_content)

        return html_content

    async def get_preview_static_file(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            preview_file_path = validate_workspace_path("build/" + file_path, workspace.abs_path, type="file")

            return preview_file_path
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting preview static file {file_path} for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while retrieving the preview static file. Please try again later." + str(e),
            ) from e

preview_service = PreviewService()
