"""File extraction and writing from agent outputs."""
import re
from pathlib import Path
from agent_team.config import REPO_ROOT

def _resolve_base_dir(execution_path: str | None) -> Path:
    if execution_path:
        p = Path(execution_path).expanduser().resolve()
        return p if p.is_dir() else p.parent
    return REPO_ROOT

def extract_and_write_files(
    executor_output: str,
    execution_path: str | None = None,
    skip_existing: bool = False,
) -> list[Path]:
    base_dir = _resolve_base_dir(execution_path)
    written: list[Path] = []
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(target)
    return written

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
