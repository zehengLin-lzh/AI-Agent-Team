"""Artifact type definitions."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


class ArtifactType(str, Enum):
    CODE_FILE = "code_file"
    DOCUMENT = "document"
    QUERY = "query"
    ANALYSIS = "analysis"
    COMMAND = "command"
    DATA = "data"
    GENERIC = "generic"


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    WRITTEN = "written"
    FAILED = "failed"


@dataclass
class Artifact:
    """A single output artifact produced by the agent pipeline."""
    type: ArtifactType
    content: str
    metadata: dict = field(default_factory=dict)
    # Optional fields
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    producer: str = ""          # which agent/node produced this
    status: ArtifactStatus = ArtifactStatus.DRAFT
    # For code files
    file_path: str = ""         # relative path (e.g. "src/main.py")
    language: str = ""          # programming language
    # For documents
    title: str = ""
    format: str = ""            # "markdown", "plain", "html"
