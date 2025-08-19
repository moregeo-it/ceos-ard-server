from pydantic import BaseModel


class AuthError(BaseModel):
    code: int
    message: str
