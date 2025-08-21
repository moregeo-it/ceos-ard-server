import logging
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.services.build_service import build_service
from app.services.workspace_service import workspace_service

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self):
        self.build_service = build_service

    async def generate_preview(self, db: Session, pfs: list[str] | None, workspace_id: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = workspace_service.get_workspace_by_id(db, workspace_id, user_id)

            build_info = await self.build_service.start_build(
                workspace_path=str(workspace.workspace_path), workspace_id=workspace_id, pfs=pfs or workspace.pfs
            )

            if build_info.get("status") == "success":
                workspace.last_build_at = datetime.now()
                db.commit()

                workspace_path = Path(workspace.workspace_path)
                return await self._get_preview_files(workspace_path, pfs)

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

        return html_content


preview_service = PreviewService()
