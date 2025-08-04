from fastapi import HTTPException, status
from sqlalchemy.orm import Session

import os
import shutil
from app.utils.sanitization import sanitize_path
from app.services.git_service import git_service
from app.schemas.workspace import FileOperationRequest
from app.services.workspace_service import workspace_service

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
        
        if not request_data.get("name"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name is required"
            )
        
        if not request_data.get("type"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type is required"
            )
        
        if not request_data.get("path"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is required"
            )
        
        if request_data.get("type") not in ["file", "folder"]:
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
                name=request_data.get("name"),
                type=request_data.get("type"),
                path=request_data.get("path")
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

        return {"message": message}

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

            return {"content": content}

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
        content: str,
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
                    detail="File path is not a file"
                )
            
            if not os.access(file_path, os.W_OK):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Permission denied"
                )

            with open(file_path, "w") as f:
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
        type: str,
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
        
        if type not in ["file", "folder"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid type"
            )
        
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            return await self._delete_file_or_folder(workspace_path, type, file_path)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete file or folder: {str(e)}"
            )

    async def _delete_file_or_folder(self, workspace_path, type, file_path):
        target_path = os.path.join(workspace_path, file_path)

        if not os.path.exists(target_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File or folder not found"
            )

        if type == "file":
            os.remove(target_path)
            message = "File deleted successfully"
        elif type == "folder":
            shutil.rmtree(target_path)
            message = "Folder deleted successfully"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid type"
            )

        return {"message": message}

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

        if not os.path.exists(target_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        if not os.access(target_path, os.W_OK):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied"
            )

        if not target_path.startswith(workspace_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is not in the workspace"
            )

        if not os.path.isfile(target_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is not a file"
            )

        if operation_request.operation == "rename":
            new_file_path = os.path.join(os.path.dirname(target_path), operation_request.new_name)

            if os.path.exists(new_file_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File already exists"
                )
            
            os.rename(target_path, new_file_path)
            message = "File renamed successfully"
        elif operation_request.operation == "revert":
            await git_service.revert_file_changes(workspace_path=workspace_path, file_path=target_path)
            message = "File reverted successfully"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid operation"
            )

        return {"message": message}

file_service = FileService()