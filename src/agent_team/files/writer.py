"""File extraction and writing from agent outputs."""
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from agent_team.config import REPO_ROOT

_MAX_PREVIEW_LINES = 30
_MAX_DIFF_LINES = 200


@dataclass
class FileChangeInfo:
    """Describes a single file change produced by the EXECUTOR."""
    path: Path
    is_new: bool
    diff: str | None = None       # unified diff for modified files
    preview: str | None = None    # first N lines for new files


def _resolve_base_dir(execution_path: str | None) -> Path:
    if execution_path:
        p = Path(execution_path).expanduser().resolve()
        return p if p.is_dir() else p.parent
    return REPO_ROOT


def _compute_diff(old_content: str, new_content: str, file_path: str) -> str:
    """Compute a unified diff between old and new content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    return "".join(diff)


def extract_and_write_files(
    executor_output: str,
    execution_path: str | None = None,
    skip_existing: bool = False,
) -> list[FileChangeInfo]:
    """Extract --- FILE: ... --- blocks and write them to disk.

    Returns a list of FileChangeInfo with diff/preview data for display.
    """
    base_dir = _resolve_base_dir(execution_path)
    changes: list[FileChangeInfo] = []
    pattern = re.compile(
        r"---\s*FILE:\s*(.+?)\s*---\n(.*?)---\s*END FILE\s*---",
        re.DOTALL,
    )
    for match in pattern.finditer(executor_output):
        raw_path = match.group(1).strip()
        content = match.group(2)
        # If path is absolute, resolve symlinks and check if under base_dir
        if raw_path.startswith("/"):
            abs_path = Path(raw_path).resolve()
            try:
                abs_path.relative_to(base_dir)
                target = abs_path
            except ValueError:
                target = base_dir / raw_path.lstrip("/")
        else:
            target = base_dir / raw_path
        if skip_existing and target.exists():
            continue

        # Compute diff or preview before writing
        is_new = not target.exists()
        diff_text = None
        preview_text = None

        if is_new:
            # Capture first N lines as preview for new files
            lines = content.splitlines()
            preview_text = "\n".join(lines[:_MAX_PREVIEW_LINES])
            if len(lines) > _MAX_PREVIEW_LINES:
                preview_text += f"\n... +{len(lines) - _MAX_PREVIEW_LINES} more lines"
        else:
            # Read existing content and compute diff
            try:
                old_content = target.read_text()
                rel_path = str(target.relative_to(base_dir)) if target.is_relative_to(base_dir) else str(target)
                diff_text = _compute_diff(old_content, content, rel_path)
                if not diff_text:
                    diff_text = None  # No changes
            except Exception:
                diff_text = None  # Can't read original, treat as new-ish

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        changes.append(FileChangeInfo(
            path=target,
            is_new=is_new,
            diff=diff_text,
            preview=preview_text,
        ))
    return changes


def extract_run_commands(executor_output: str) -> list[tuple[str, str]]:
    """Extract RUN command blocks from executor output.
    Returns list of (description, command) tuples."""
    commands = []
    pattern = re.compile(
        r"---\s*RUN:\s*(.+?)\s*---\n(.*?)---\s*END RUN\s*---",
        re.DOTALL,
    )
    for match in pattern.finditer(executor_output):
        desc = match.group(1).strip()
        cmd = match.group(2).strip()
        commands.append((desc, cmd))
    return commands
