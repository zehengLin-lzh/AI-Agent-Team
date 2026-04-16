"""Artifact renderer — materializes artifacts to disk or display."""
from __future__ import annotations

from pathlib import Path

from agent_team.artifacts.types import Artifact, ArtifactType, ArtifactStatus


def write_code_artifacts(
    artifacts: list[Artifact],
    execution_path: str | None = None,
) -> list[dict]:
    """Write code file artifacts to disk. Returns file change records.

    Delegates to the existing files/writer.py logic for diff generation.
    """
    from agent_team.files.writer import _resolve_base_dir

    base_dir = _resolve_base_dir(execution_path)
    changes = []

    for artifact in artifacts:
        if artifact.type != ArtifactType.CODE_FILE:
            continue
        if not artifact.file_path or not artifact.content.strip():
            continue

        file_path = base_dir / artifact.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        is_new = not file_path.exists()
        old_content = "" if is_new else file_path.read_text(errors="replace")

        file_path.write_text(artifact.content)
        artifact.status = ArtifactStatus.WRITTEN

        changes.append({
            "path": str(file_path),
            "is_new": is_new,
            "preview": artifact.content[:500],
        })

    return changes


def render_artifact_summary(artifact: Artifact) -> str:
    """Produce a human-readable one-line summary of an artifact."""
    if artifact.type == ArtifactType.CODE_FILE:
        lines = artifact.content.count("\n") + 1
        return f"[{artifact.language or 'code'}] {artifact.file_path} ({lines} lines)"
    elif artifact.type == ArtifactType.DOCUMENT:
        words = len(artifact.content.split())
        return f"[doc] {artifact.title} ({words} words)"
    elif artifact.type == ArtifactType.QUERY:
        return f"[sql] {artifact.title}: {artifact.content[:60]}..."
    elif artifact.type == ArtifactType.ANALYSIS:
        words = len(artifact.content.split())
        return f"[analysis] {artifact.title} ({words} words)"
    else:
        return f"[{artifact.type.value}] {artifact.title or 'untitled'}"
