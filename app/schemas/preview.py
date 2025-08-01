from pydantic import BaseModel
from typing import List, Optional


class PreviewFile(BaseModel):
    name: str
    path: str


class PreviewListResponse(BaseModel):
    success: bool
    preview_files: List[PreviewFile] = []
    message: Optional[str] = None


class PreviewErrorMessage(BaseModel):
    success: bool = False
    message: str
    error: Optional[str] = None

class PreviewFileResponse(BaseModel):
    success: bool
    preview_file: PreviewFile
    message: Optional[str]