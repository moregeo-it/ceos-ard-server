from pydantic import BaseModel


class PreviewErrorMessage(BaseModel):
    message: str
    code: int
