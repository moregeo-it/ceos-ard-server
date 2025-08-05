from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from enum import Enum
from datetime import datetime

import time
import asyncio

class BuildStatus(Enum):
    STARTING = "starting"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class LogType(Enum):
    INFO = "info"
    STDOUT = "stdout"
    STDERR = "stderr"
    ERROR = "error"


class BuildLog(BaseModel):
    type: LogType
    text: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class BuildInfo(BaseModel):
    status: BuildStatus
    logs: List[BuildLog] = Field(default_factory=list)
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    error: Optional[str] = None
    process: Optional[asyncio.subprocess.Process] = Field(default=None, exclude=True)
    build_type: str = "all"  # "all" or "specific"
    pfs: Optional[List[str]] = None
    automatic: bool = True

    class Config:
        arbitrary_types_allowed = True

class BuildStatusResponse(BaseModel):
    workspace_id: str
    workspace_status: str
    build_status: Optional[Dict[str, Any]]

class StartBuildRequest(BaseModel):
    pfs: Optional[str] = Field(None, description="Specific PFS to build (Optional)")

class BuildResult(BaseModel):
    workspace_id: str
    build_started: bool
    build_type: str
    pfs: Optional[str]
    status: str