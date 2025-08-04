from pydantic import BaseModel
from typing import List, Optional


class PreviewFile(BaseModel):
    name: str
    path: str


class PreviewErrorMessage(BaseModel):
    message: str
    code: int