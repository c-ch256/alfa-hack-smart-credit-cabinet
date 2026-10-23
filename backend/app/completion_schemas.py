from datetime import datetime

from pydantic import BaseModel, Field

from app.application_schemas import StrictInput


class DemoCodeResponse(BaseModel):
    code: str
    expires_at: datetime


class DemoSignInput(StrictInput):
    code: str = Field(pattern=r'^[0-9]{6}$')
