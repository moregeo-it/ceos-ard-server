import os
import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from datetime import datetime
from app.schemas.build import BuildStatus
from app.schemas.preview import PreviewFile
from app.schemas.workspace import WorkspaceStatus

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
        pfs: Optional[List[str]] = None,
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
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            if not os.path.exists(os.path.join(workspace_path, "build")):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Build directory not found"
                )
            
            if workspace.status != WorkspaceStatus.ACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Workspace is not active"
                )
            
            if not pfs and workspace.pfs:
                pfs = workspace.pfs

            build_info = await self.build_service.start_build(
                workspace_path=workspace_path,
                workspace_id=workspace_id,
                pfs=pfs
            )

            if build_info.status == BuildStatus.COMPLETED:
                workspace.last_build_at = datetime.utcnow()
                workspace.updated_at = datetime.utcnow()
                db.commit()

                build_dir = os.path.join(workspace_path, "build")

                if not os.path.exists(build_dir):
                    return False, [], "Build directory does not exist. Please build the workspace first."
                preview_list = os.listdir(build_dir)

                html_files = []
                for file in preview_list:
                    if file.endswith(".html"):
                        preview_files = PreviewFile(
                            name=sanitize_filename(file),
                            path=sanitize_path(os.path.join(build_dir, file))
                        )
                        html_files.append(preview_files)

                return True, html_files, ""

            else:
                return False, [], "Build failed. Please check the logs for more details."

        except Exception as e:
            logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
            return False, [], f"Failed to get preview list: {str(e)}"
        
preview_service = PreviewService()