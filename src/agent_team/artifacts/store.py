"""In-memory artifact store for a single session."""
from __future__ import annotations

from agent_team.artifacts.types import Artifact, ArtifactType, ArtifactStatus


class ArtifactStore:
    """Tracks all artifacts produced during a pipeline run.

    In-memory only — lives for the duration of one AgentTeam.run() call.
    """

    def __init__(self):
        self._artifacts: list[Artifact] = []

    def add(self, artifact: Artifact) -> str:
        """Add an artifact. Returns its ID."""
        self._artifacts.append(artifact)
        return artifact.id

    def get(self, artifact_id: str) -> Artifact | None:
        for a in self._artifacts:
            if a.id == artifact_id:
                return a
        return None

    def by_type(self, artifact_type: ArtifactType) -> list[Artifact]:
        return [a for a in self._artifacts if a.type == artifact_type]

    def by_producer(self, producer: str) -> list[Artifact]:
        return [a for a in self._artifacts if a.producer == producer]

    def all(self) -> list[Artifact]:
        return list(self._artifacts)

    def mark_validated(self, artifact_id: str) -> None:
        a = self.get(artifact_id)
        if a:
            a.status = ArtifactStatus.VALIDATED

    def mark_written(self, artifact_id: str) -> None:
        a = self.get(artifact_id)
        if a:
            a.status = ArtifactStatus.WRITTEN

    @property
    def count(self) -> int:
        return len(self._artifacts)

    def summary(self) -> dict:
        """Summary for event emission."""
        by_type = {}
        for a in self._artifacts:
            by_type.setdefault(a.type.value, []).append({
                "id": a.id,
                "status": a.status.value,
                "title": a.title or a.file_path or a.type.value,
            })
        return {"total": len(self._artifacts), "by_type": by_type}
