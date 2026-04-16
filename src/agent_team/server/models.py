"""Request/response models for the HTTP API."""
from enum import Enum
from pydantic import BaseModel


class AskMode(str, Enum):
    PLAN_ONLY = "plan_only"
    PLAN_AND_EXECUTE = "plan_and_execute"


class AskRequest(BaseModel):
    plan: str
    mode: AskMode | None = None
    agent_mode: str | None = None  # thinking/coding/brainstorming/architecture/execution
    execution_path: str | None = None


class AskResponse(BaseModel):
    title: str
    timestamp: str
    mode: AskMode
    agent_mode: str
    execution_path: str | None
    plan_file_path: str
    phase_outputs: dict[str, str]
    file_changes: list[dict] | None = None
