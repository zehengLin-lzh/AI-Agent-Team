"""File extraction and writing from agent outputs."""
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from agent_team.config import REPO_ROOT

_MAX_PREVIEW_LINES = 30
_MAX_DIFF_LINES = 200


def _normalize_file_blocks(text: str) -> str:
    """Normalize common LLM output variations to standard --- FILE --- format.

    Handles:
    - Markdown code blocks inside --- FILE --- delimiters
    - **path** or `path` followed by ```lang blocks
    - ```lang with # file: path comment inside
    """
    # 1. Strip markdown code fences INSIDE existing --- FILE --- blocks
    text = re.sub(
        r"(---\s*FILE:\s*.+?\s*---\n)\s*```\w*\n",
        r"\1",
        text,
    )
    text = re.sub(
        r"\n```\s*\n(---\s*END FILE\s*---)",
        r"\n\1",
        text,
    )

    # 2. **path/to/file.py** or `path/to/file.py` followed by ```lang\n...\n```
    text = re.sub(
        r"(?:\*\*|`)([a-zA-Z0-9_./-]+\.\w+)(?:\*\*|`)\s*\n```\w*\n(.*?)```",
        r"--- FILE: \1 ---\n\2--- END FILE ---",
        text,
        flags=re.DOTALL,
    )

    # 3. ```lang\n# file: path/to/file.py\n...\n```
    text = re.sub(
        r"```\w*\n#\s*(?:file(?:name)?|path)\s*:\s*(.+?)\n(.*?)```",
        r"--- FILE: \1 ---\n\2--- END FILE ---",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    return text


def _guess_filename_from_plan(planner_output: str) -> str:
    """Try to extract a filename from PLANNER output for fallback use."""
    # Look for common patterns: "Create file X", "Write X", file extensions
    m = re.search(
        r"(?:create|write|build|generate|make)\s+(?:a\s+)?(?:file\s+)?(?:called\s+|named\s+)?[`\"']?([a-zA-Z0-9_./-]+\.\w{1,4})[`\"']?",
        planner_output,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Look for any filename with common extensions
    m = re.search(r"\b([a-zA-Z0-9_/-]+\.(?:py|js|ts|sh|rb|go|rs|java|cpp|c|h))\b", planner_output)
    if m:
        return m.group(1)
    return "output.py"


def _extract_single_file_fallback(
    text: str, planner_output: str = ""
) -> list[tuple[str, str]]:
    """Last resort: extract the largest markdown code block if no FILE blocks found."""
    code_blocks = re.findall(r"```\w*\n(.*?)```", text, re.DOTALL)
    if not code_blocks:
        return []
    code = max(code_blocks, key=len).strip()
    if len(code) < 20:  # Too short to be meaningful
        return []
    filename = _guess_filename_from_plan(planner_output)
    return [(filename, code)]


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
    planner_output: str = "",
) -> list[FileChangeInfo]:
    """Extract --- FILE: ... --- blocks and write them to disk.

    Returns a list of FileChangeInfo with diff/preview data for display.
    """
    base_dir = _resolve_base_dir(execution_path)
    changes: list[FileChangeInfo] = []

    # Normalize common LLM format deviations before extraction
    normalized = _normalize_file_blocks(executor_output)

    pattern = re.compile(
        r"---\s*FILE:\s*(.+?)\s*---\n(.*?)---\s*END FILE\s*---",
        re.DOTALL,
    )
    matches = list(pattern.finditer(normalized))

    # Fallback: if no FILE blocks found, try to extract from code blocks
    if not matches:
        fallback_files = _extract_single_file_fallback(normalized, planner_output)
        for fname, code in fallback_files:
            target = base_dir / fname
            is_new = not target.exists()
            preview_text = None
            diff_text = None
            if is_new:
                lines = code.splitlines()
                preview_text = "\n".join(lines[:_MAX_PREVIEW_LINES])
                if len(lines) > _MAX_PREVIEW_LINES:
                    preview_text += f"\n... +{len(lines) - _MAX_PREVIEW_LINES} more lines"
            else:
                try:
                    old = target.read_text()
                    rel = str(target.relative_to(base_dir)) if target.is_relative_to(base_dir) else str(target)
                    diff_text = _compute_diff(old, code, rel) or None
                except Exception:
                    pass
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code)
            changes.append(FileChangeInfo(path=target, is_new=is_new, diff=diff_text, preview=preview_text))
        return changes

    for match in matches:
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
