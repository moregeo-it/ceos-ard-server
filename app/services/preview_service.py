import os
import re
import pathlib
import logging
from sqlalchemy.orm import Session

from app.schemas.preview import PreviewFile
from app.services.workspace_service import workspace_service
from app.utils.sanitization import sanitize_filename, sanitize_path

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self):
        pass
    
    async def list_preview_files(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ):
        try:
            workspace = workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

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

        except Exception as e:
            logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
            return False, [], f"Failed to get preview list: {str(e)}"

    async def get_preview_file(
        self,
        db: Session,
        file_name: str,
        workspace_id: str,
        user_id: str
    ):
        try:
            workspace = workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            build_dir = os.path.join(workspace_path, "build")

            if not os.path.exists(build_dir):
                return False, None, "Build directory does not exist. Please build the workspace first."
            
            file_name = sanitize_filename(file_name)
            file_path = os.path.join(workspace_path, "build", file_name)

            if not file_path.startswith(os.path.join(workspace_path, "build")):
                return False, None, "Preview file {file_name} is not in the build directory."
            
            if not file_path.endswith(".html"):
                return False, None, "Preview file {file_name} is not an HTML file."
            
            if not os.path.exists(file_path):
                return False, None, "Preview file {file_name} does not exist."
            
            if not os.path.isfile(file_path):
                return False, None, "Preview file {file_name} is not a file."
            
            if not os.access(file_path, os.R_OK):
                return False, None, "Preview file {file_name} is not readable."
            
            preview_file = pathlib.Path(file_path).read_text(encoding='utf-8')

            preview_file = re.sub(
                r'<!--\s*edit:\s*([\w\-\.\~\/\\]+)\s*-->',
                lambda match: f'<a name="{pathlib.Path(match.group(1)).as_posix()}"></a><button class="edit" value="{pathlib.Path(match.group(1)).as_posix()}">Edit</button>',
                preview_file
            )

            return True, preview_file, ""

        except Exception as e:
            logger.error(f"Error getting preview file {file_name} for workspace {workspace_id}: {e}")
            return False, None, f"Failed to get preview file: {str(e)}"

preview_service = PreviewService()



    