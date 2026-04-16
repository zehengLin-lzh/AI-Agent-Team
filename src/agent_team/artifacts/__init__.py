"""Artifact system — typed output tracking for all domains."""
from agent_team.artifacts.types import Artifact, ArtifactType
from agent_team.artifacts.store import ArtifactStore

__all__ = ["Artifact", "ArtifactType", "ArtifactStore"]
