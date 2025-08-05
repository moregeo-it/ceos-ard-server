from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

import os
import shutil
import logging
from app.utils.sanitization import sanitize_path
from app.services.git_service import git_service
from app.schemas.workspace import FileOperationRequest
from app.services.workspace_service import workspace_service

logger = logging.getLogger(__name__)

class FileService:
    def __init__(self):
        self.git_service = git_service
        self.workspace_service = workspace_service

    async def get_workspace_files(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) :
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            file_and_folder = []

            for root, dirs, files in os.walk(workspace_path):
                if root == workspace_path:
                    dirs[:] = [dir for dir in dirs if dir not in ['template', 'build', 'LICENSE', 'README.md']]
                else:
                    dirs[:] = [dir for dir in dirs if not dir.startswith('.')]
                files[:] = [file for file in files if not file.startswith('.') and not file.endswith('.pdf')]

                for dir in dirs:
                    file_and_folder.append(os.path.join(root, dir))

                for file in files:
                    file_and_folder.append(os.path.join(root, file))

            return file_and_folder
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get workspace files: {str(e)}"
            )
        
    async def create_file_or_folder(
        self,
        db: Session,
        workspace_id: str,
        request_data: dict,
        user_id: str
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        if not request_data.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name is required"
            )
        
        if not request_data.type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type is required"
            )
        
        if not request_data.path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is required"
            )
        
        if request_data.type not in ["file", "folder"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type must be file or folder"
            )

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )
            
            return self._create_file_or_folder(
                workspace_path=workspace_path,
                name=request_data.name,
                type=request_data.type,
                path=request_data.path
            )

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create file or folder: {str(e)}"
            )
    def _create_file_or_folder(
        self,
        workspace_path: str,
        name: str,
        type: str,
        path: str
    ):
        target_path = os.path.join(workspace_path, path, name)

        if os.path.exists(target_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{type.capitalize()} already exists"
            )

        if type == "file":
            open(target_path, "w").close()
            message = "File created successfully"
        elif type == "folder":
            os.makedirs(target_path, exist_ok=True)
            message = "Folder created successfully"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid type"
            )

        return {
            "name": name,
            "message": message,
            "path": target_path,
            "directory": type == "folder"
        }

    async def read_file_content(
        self,
        db: Session,
        workspace_id: str,
        file_path: str,
        user_id: str
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        if not file_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is required"
            )
        
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            file_path = sanitize_path(file_path)
            file_path = os.path.join(workspace_path, file_path)

            if not file_path.startswith(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File path is not in the workspace"
                )

            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found"
                )

            if not os.path.isfile(file_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File path is not a file"
                )
            
            with open(file_path, "r") as f:
                content = f.read()

            return content

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read file: {str(e)}"
            )
        
    async def store_file_content(
        self,
        db: Session,
        workspace_id: str,
        file_path: str,
        content: bytes,
        user_id: str
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        if not file_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is required"
            )
        
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content is required"
            )
        
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            file_path = sanitize_path(file_path)
            file_path = os.path.join(workspace_path, file_path)

            if not file_path.startswith(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File path is not in the workspace"
                )

            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found"
                )

            if not os.path.isfile(file_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path does not point to a file"
                )
            
            if not os.access(file_path, os.W_OK):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Permission denied"
                )

            with open(file_path, "wb") as f:
                f.write(content)

            return {"message": "File content stored successfully"}

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to store file content: {str(e)}"
            )
    
    async def delete_file_or_folder(
        self,
        db: Session,
        workspace_id: str,
        file_path: str,
        user_id: str
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        if not file_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is required"
            )
        
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            return await self._delete_file_or_folder(workspace_path, file_path)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete file or folder: {str(e)}"
            )

    async def _delete_file_or_folder(self, workspace_path, file_path):
        target_path = os.path.join(workspace_path, sanitize_path(file_path))

        if not os.path.exists(target_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File or folder not found"
            )

        if os.path.isfile(target_path):
            os.remove(target_path)
            return {"message": "File deleted successfully"}
        elif os.path.isdir(target_path):
            shutil.rmtree(target_path)
            return {"message": "Folder deleted successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid type"
            )

    async def update_file(
        self,
        db: Session,
        workspace_id: str,
        file_path: str,
        operation_request: FileOperationRequest,
        user_id: str
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        if not file_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is required"
            )
        
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            if not operation_request.operation in ["rename", "revert"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid operation"
                )
            
            if operation_request.operation == "rename" and not operation_request.new_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="New name is required"
                )

            return await self._update_file(workspace_path, file_path, operation_request)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update file: {str(e)}"
            )

    async def _update_file(self, workspace_path, file_path, operation_request):
        target_path = os.path.join(workspace_path, file_path)

        if not os.path.isfile(target_path) or not target_path.startswith(workspace_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        if not os.access(target_path, os.W_OK):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied"
            )

        if operation_request.operation == "rename":
            new_file_path = os.path.join(os.path.dirname(target_path), operation_request.new_name)
            if os.path.exists(new_file_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File already exists"
                )
            os.replace(target_path, new_file_path)
        elif operation_request.operation == "revert":
            await git_service.revert_file_changes(workspace_path=workspace_path, file_path=file_path)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid operation"
            )

        return {"message": "File updated successfully"}
    
    async def search_files(self, db: Session, workspace_id: str, search_query: str, user_id: str):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )
        
        if not search_query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query is required"
            )
        
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        workspace_path = str(workspace.workspace_path)

        if not os.path.exists(workspace_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found"
            )

        return await self._search_files(workspace_path, search_query)
    
    async def _search_files(self, workspace_path, search_query):
        try:
            search_results = []
            for root, _, files in os.walk(workspace_path):
                for file in files:
                    file_path = os.path.join(root, file)

                    if not file_path.startswith(workspace_path):
                        continue

                    if search_query in file:
                        search_results.append({
                            "name": file,
                            "type": "filename",
                            "path": os.path.relpath(file_path, workspace_path)
                        })
                    
                    try:
                        with open(file_path, "r") as f:
                            content = f.read()
                            if search_query.lower() in content.lower():
                                lines = content.splitlines()
                                for i, line in enumerate(lines):
                                    if search_query.lower() in line.lower():
                                        column = line.lower().index(search_query.lower())
                                        search_results.append({
                                            "name": file,
                                            "type": "file",
                                            "path": os.path.relpath(file_path, workspace_path),
                                            "line": i + 1,
                                            "column": column + 1,
                                            "excerpt": line.strip()
                                        })
                                        break
                    except Exception as e:
                        logger.error(f"Error reading file: {e}")

            return JSONResponse(content=search_results, status_code=status.HTTP_200_OK)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to search files: {str(e)}"
            )
    async def get_changed_files(self, db: Session, workspace_id: str, user_id: str):
        try:
            if not workspace_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Workspace ID is required"
                )
            
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            git_status = await git_service.get_git_status(workspace_path)

            changed_files = []

            for file in git_status['modified_files']:
                    changed_files.append({
                        "path": file.path,
                        "status": file.status
                    })

            for file in git_status['untracked_files']:
                    changed_files.append({
                        "path": file,
                        "status": "untracked"
                    })

            return changed_files
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get changed files: {str(e)}"
            )
        
    async def get_file_diff(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            stdout, stderr, returncode = await git_service._run_git_command(
                ["git", "diff", file_path],
                cwd=workspace_path
            )

            if returncode != 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to get file diff: {stderr}"
                )

            return stdout
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get file diff: {str(e)}"
            )

file_service = FileService()