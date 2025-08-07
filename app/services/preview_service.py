import os
import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from datetime import datetime
from app.schemas.build import BuildStatus

from app.services.build_service import build_service
from app.services.workspace_service import workspace_service
from app.utils.sanitization import sanitize_filename, sanitize_path

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self):
        self.build_service = build_service
    
    async def generate_preview(
        self,
        db: Session,
        pfs: List[str] = None,
        workspace_id: str = None,
        user_id: str = None 
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )

        try:
            workspace = workspace_service.get_workspace_by_id(db, workspace_id, user_id)

            build_info = await self.build_service.start_build(
                workspace_path=str(workspace.workspace_path),
                workspace_id=workspace_id,
                pfs=pfs or workspace.pfs
            )

            if build_info.status == BuildStatus.COMPLETED:
                workspace.last_build_at = datetime.utcnow()
                workspace.updated_at = datetime.utcnow()
                db.commit()

                workspace_path = str(workspace.workspace_path)
                return True, await self._get_preview_files(workspace_path, pfs), ""

            else:
                return False, [], "Build failed. Please check the logs for more details."

        except Exception as e:
            logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
            return False, [], f"Failed to get preview list: {str(e)}"


    async def _get_preview_files(self, workspace_path: str, pfs: Optional[List[str]]):
        build_dir = os.path.join(str(workspace_path), "build")

        if not os.path.exists(build_dir):
            return False, [], "Build directory does not exist. Please build the workspace first."

        preview_list = os.listdir(build_dir)

        html_content = ""
        for file in preview_list:
            if file.endswith(".html") and any(p in file for p in pfs):
                with open(os.path.join(build_dir, file), "r") as f:
                    html_content = f.read()

        return html_content
        
preview_service = PreviewService()